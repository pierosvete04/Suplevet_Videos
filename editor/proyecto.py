"""«Actualizar proyecto»: la edición de una sesión se comparte entre las PCs del equipo por Drive.

Lo que se edita en la sala (subtítulos corregidos, marcas, cortes, zoom, imágenes, versiones, portadas, copies, plan y
ediciones) vive en la PC, en datos/subtitulos/<sesión>/. Para que otra PC lo vea, cada vez que algo cambia la sala
sube un paquete (un .zip chiquito) a la carpeta de la sesión en Drive, en _SALA_PROYECTO/, con quién lo hizo, cuándo
y qué cambió. Las otras PCs lo ven y eligen:
  · Actualizar: traen esos cambios (antes se guarda una copia de seguridad de lo suyo).
  · Ahora no: siguen con lo suyo; el aviso vuelve a salir más tarde.
  · Quedarme con lo mío: lo suyo pasa a ser la versión del proyecto (lo del otro queda en el historial).
Si las dos PCs cambiaron la misma pieza, se avisa antes de actualizar. Cada versión queda en el historial y se puede
volver a cualquiera.

En Drive: <sesión>/_SALA_PROYECTO/proyecto.json (la última versión y el historial) y vNNNN.zip (cada versión).
En la PC: datos/subtitulos/<sesión>/_proyecto_local.json (de qué versión parte lo de esta PC).
"""
import hashlib
import io
import json
import os
import threading
import time
import zipfile
from pathlib import Path

import cuenta
import drive
import motor

CARPETA = "_SALA_PROYECTO"
LOCAL = "_proyecto_local.json"
CADA = 60  # segundos entre revisiones
MAX_HISTORIAL = 80
_NO_VAN = (LOCAL, "_calidad.json")  # lo de esta PC que no se comparte
_cerrojo = threading.RLock()
ESTADO = {}  # sesión -> lo último que se sabe del proyecto (lo lee la pantalla)

# Lo que no vive en datos/subtitulos/<sesión> pero cambia cómo sale el video: las plantillas propias, la plantilla
# activa y el estilo de edición (en config.local.json) y los clips asignados a mano (datos/asignaciones). Viajan en el
# paquete como dos archivos más; al traerlos se guardan en su lugar. Sin esto, el editor bajaba la sesión y sus videos
# salían con otra letra, otro zoom y otros clips.
ESTILO = "_estilo_equipo.json"
A_MANO = "_a_mano.json"
_DEL_ESTILO = ("plantillas_propias", "plantilla_activa", "estilo_edicion", "sonidos_nivel")
_CAMBIO = {"plantillas_propias": "plantilla_activa_cambio", "plantilla_activa": "plantilla_activa_cambio",
           "estilo_edicion": "estilo_cambio", "sonidos_nivel": "estilo_cambio"}


def _virtuales():
    d = {}
    estilo = {k: motor.CONFIG[k] for k in _DEL_ESTILO if motor.CONFIG.get(k) and k in _config_local()}
    if estilo:
        d[ESTILO] = json.dumps(estilo, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
    f = motor._asig_mano_archivo(motor.S.semana)
    if f.exists():
        try:
            d[A_MANO] = json.dumps(sorted(json.loads(f.read_text(encoding="utf-8"))), ensure_ascii=False, indent=1).encode("utf-8")
        except (ValueError, TypeError):
            pass
    return d


def _config_local():
    """Solo lo que esta PC eligió (config.local.json), no lo que trae la sala de fábrica."""
    try:
        return json.loads(motor.LOCAL_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _guardar_virtual(rel, datos):
    """Pone en su lugar lo que llegó del proyecto en los archivos de _virtuales(). Si no llegó nada (una versión
    vieja, de antes de que viajaran), lo de esta PC queda como está."""
    if not datos:
        return
    if rel == ESTILO:
        nuevo = json.loads(datos)
        if "plantillas_propias" in nuevo:  # las plantillas que solo tiene esta PC no se pierden
            nuevo["plantillas_propias"] = {**(motor.CONFIG.get("plantillas_propias") or {}), **nuevo["plantillas_propias"]}
        cambio = {k: v for k, v in nuevo.items() if k in _DEL_ESTILO and motor.CONFIG.get(k) != v}
        for marca in {_CAMBIO[k] for k in cambio}:
            cambio[marca] = time.time()  # los videos armados con lo de antes quedan por rearmar
        if cambio:
            motor.guardar_config(cambio)
    elif rel == A_MANO:
        f = motor._asig_mano_archivo(motor.S.semana)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(json.loads(datos), ensure_ascii=False), encoding="utf-8")


# ---------- lo de esta PC ----------

def _archivos():
    """{ruta relativa: bytes} de lo que se edita en la sesión abierta."""
    S, d = motor.S, {}
    if not S.subs.exists():
        return _virtuales()
    for f in sorted(S.subs.rglob("*.json")):
        rel = f.relative_to(S.subs)
        if rel.name in _NO_VAN or rel.as_posix() in (ESTILO, A_MANO) \
                or any(p.startswith(("_respaldo", "_reemplazados", "_copias")) for p in rel.parts[:-1]):
            continue
        d[rel.as_posix()] = f.read_bytes()
    d.update(_virtuales())
    return d


def _propios(archivos):
    """Lo editado de la sesión, sin las plantillas ni el estilo (que toda PC tiene aunque nunca haya abierto la sesión)."""
    return {k: v for k, v in archivos.items() if k not in (ESTILO, A_MANO)}


def _firma(archivos):
    return {k: hashlib.sha1(v).hexdigest()[:16] for k, v in archivos.items()}


def _local_path():
    return motor.S.subs / LOCAL


def _leer_local():
    try:
        return json.loads(_local_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _guardar_local(d, archivos=None):
    """Anota de qué versión parte esta PC y guarda una copia de esa versión (para saber después qué cambió aquí)."""
    motor.S.subs.mkdir(parents=True, exist_ok=True)
    _local_path().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    if archivos is not None:
        (motor.S.subs / "_proyecto_base.zip").write_bytes(_zip(archivos))


def _leer_base():
    try:
        with zipfile.ZipFile(motor.S.subs / "_proyecto_base.zip") as zf:
            return {n: zf.read(n) for n in zf.namelist()}
    except (FileNotFoundError, zipfile.BadZipFile):
        return {}


def quien():
    c = cuenta.estado()
    return {"autor": c.get("nombre") or c.get("email") or os.environ.get("USERNAME", "Alguien"),
            "email": c.get("email", ""), "pc": c.get("dispositivo") or os.environ.get("COMPUTERNAME", "")}


# ---------- qué cambió (en palabras) ----------

QUE = {ESTILO: "plantillas y estilo de edición", A_MANO: "clips asignados a mano", "_ajustes_piezas.json": "cortes, zoom e imágenes", "_ajustes_videos.json": "ajustes de videos (plantilla, altura, silencios)",
       "_versiones.json": "versiones y «solo este video»", "_portadas.json": "portadas", "_copies_editados.json": "copies",
       "_ediciones.json": "ediciones y plan de la sesión", "_plan.json": "plan del director", "_estados.json": "estados de los videos"}


def _piezas_cambiadas(viejos, nuevos):
    """{pieza o tema: qué} entre dos juegos de archivos (bytes)."""
    out = {}
    for rel in sorted(set(viejos) | set(nuevos)):
        a, b = viejos.get(rel), nuevos.get(rel)
        if a == b:
            continue
        nombre = Path(rel).name
        if nombre == "_ajustes_piezas.json":
            try:
                ja, jb = json.loads(a or b"{}"), json.loads(b or b"{}")
            except ValueError:
                ja, jb = {}, {}
            for k in sorted(set(ja) | set(jb)):
                limpio = lambda d: {x: y for x, y in (d or {}).items() if x != "editado"}
                if limpio(ja.get(k)) != limpio(jb.get(k)):
                    out.setdefault(k.split("@")[0], set()).add("cortes y zoom" + (f" (edición {k.split('@')[1]})" if "@" in k else ""))
        elif nombre.startswith("_"):
            out.setdefault(QUE.get(nombre, nombre[1:-5].replace("_", " ")), set()).add("")
        else:
            out.setdefault(Path(rel).stem, set()).add("subtítulos y marcas")
    return {k: sorted(x for x in v if x) for k, v in out.items()}


def _texto_cambios(cambios):
    partes = [f"{k} ({', '.join(v)})" if v else k for k, v in cambios.items()]
    return partes[:12] + ([f"y {len(partes) - 12} más"] if len(partes) > 12 else [])


# ---------- Drive ----------

def _remoto(rel=""):
    return f"{drive.REMOTO}:{motor.S.drive}/{CARPETA}" + (f"/{rel}" if rel else "")


def _leer_remoto():
    try:
        return json.loads(drive._rc("cat", _remoto("proyecto.json"), timeout=60) or "{}")
    except RuntimeError as e:
        if "not found" in str(e).lower() or "directory not found" in str(e).lower():
            return {}
        raise


def _bajar_zip(version):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "v.zip"
        drive._rc("copyto", _remoto(f"v{int(version):04d}.zip"), destino, timeout=120)
        with zipfile.ZipFile(destino) as zf:
            return {n: zf.read(n) for n in zf.namelist() if n.endswith(".json")}


def _zip(archivos):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, datos in archivos.items():
            zf.writestr(rel, datos)
    return b.getvalue()


def _subir(nombre, datos):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / nombre
        f.write_bytes(datos)
        drive._rc("copyto", f, _remoto(nombre), timeout=120)


# ---------- revisar, publicar, traer ----------

def disponible():
    return drive.estado()["activo"] and drive.bajada(motor.S.drive)


def revisar():
    """Compara lo de esta PC con el proyecto en Drive. Devuelve (y guarda en ESTADO) qué hay y qué se puede hacer."""
    with _cerrojo:
        sem = motor.S.semana
        if not disponible():
            ESTADO[sem] = {"activo": False}
            return ESTADO[sem]
        rem, loc = _leer_remoto(), _leer_local()
        archivos = _archivos()
        firma = _firma(archivos)
        base = loc.get("firma") or {}
        cambie = firma != base if loc.get("version") is not None else bool(firma)
        vr = int(rem.get("version") or 0)
        vl = loc.get("version")
        e = {"activo": True, "sesion": sem, "version_remota": vr, "version_local": vl, "cambie": cambie,
             "ultima": (rem.get("historial") or [None])[-1], "historial": list(reversed(rem.get("historial") or []))[:30],
             "revisado": time.time()}
        if vr and firma == (rem.get("firma") or {}):
            # ya está igual que el proyecto (por ejemplo la primera vez en esta PC): se anota sin preguntar
            if vl != vr:
                _guardar_local({"version": vr, "firma": firma, "cuando": time.time()}, archivos)
                e.update(version_local=vr, cambie=False)
            e["pendiente"] = False
        else:
            e["pendiente"] = bool(vr) and vr != vl
        if e["pendiente"] and cambie:
            # las dos PCs cambiaron algo: ¿tocaron las mismas piezas?
            # lo que cambiaron en las versiones que esta PC todavía no trajo, y lo que cambió aquí desde la suya
            suyas = {x for h in rem.get("historial") or [] if int(h.get("version") or 0) > int(vl or 0) for x in h.get("piezas") or []}
            mias = set(_piezas_cambiadas(_leer_base(), archivos))
            e["choque"] = sorted(mias & suyas)
            e["mias"] = sorted(mias)
        else:
            e["choque"], e["mias"] = [], []
        if e.get("pendiente") and vl is None and not _propios(archivos):
            # primera vez de esta sesión en esta PC (p. ej. el editor abre lo que analizó otra PC): no hay nada que
            # pueda chocar, así que lo del equipo se trae solo, sin esperar a que alguien toque «Actualizar»
            try:
                r = traer()
                e.update(pendiente=False, version_local=r.get("version"), traido_solo=True)
            except Exception as err:  # noqa: BLE001 — queda el aviso de siempre para traerlo a mano
                print("Proyecto: no se pudo traer solo:", err)
        ESTADO[sem] = e
        return e


def publicar(motivo=""):
    """Sube lo de esta PC como la versión nueva del proyecto. Devuelve el número de versión."""
    with _cerrojo:
        rem, loc = _leer_remoto(), _leer_local()
        archivos = _archivos()
        if not archivos:
            return None
        anterior = {}
        vr = int(rem.get("version") or 0)
        if vr:
            try:
                anterior = _bajar_zip(vr)
            except Exception:  # noqa: BLE001 — sin la anterior, se describe sin comparar
                anterior = {}
        cambios = _piezas_cambiadas(anterior, archivos)
        if vr and not cambios:
            _guardar_local({"version": vr, "firma": _firma(archivos), "cuando": time.time()}, archivos)
            return vr
        nueva = vr + 1
        q = quien()
        _subir(f"v{nueva:04d}.zip", _zip(archivos))
        entrada = {"version": nueva, **q, "fecha": time.time(), "piezas": sorted(cambios), "cambios": _texto_cambios(cambios),
                   **({"motivo": motivo} if motivo else {})}
        historial = (rem.get("historial") or []) + [entrada]
        _subir("proyecto.json", json.dumps({"version": nueva, "firma": _firma(archivos), "historial": historial[-MAX_HISTORIAL:]},
                                           ensure_ascii=False, indent=1).encode("utf-8"))
        _guardar_local({"version": nueva, "firma": _firma(archivos), "cuando": time.time()}, archivos)
        ESTADO.pop(motor.S.semana, None)
        return nueva


def _mezclar(base, mios, suyos):
    """Junta lo de esta PC con lo que trae el proyecto: lo que el otro no tocó se queda como está aquí; lo que aquí no
    se tocó se trae; si los dos cambiaron lo mismo, gana lo que se trae (lo de aquí queda en la copia de seguridad).
    Los archivos con una entrada por pieza o por video (cortes, ajustes, versiones, portadas, copies) se juntan
    entrada por entrada. Devuelve {ruta: bytes o None (quitar)}."""
    out = {}
    for rel in set(base) | set(mios) | set(suyos):
        b, m, s_ = base.get(rel), mios.get(rel), suyos.get(rel)
        if s_ == b:
            continue  # el otro no lo cambió: queda lo de aquí
        if m == b:
            out[rel] = s_  # aquí no se cambió: se trae (o se quita, si allá se quitó)
            continue
        if s_ is None:
            continue  # allá se quitó pero aquí se cambió: se queda lo de aquí
        try:
            jb, jm, js = (json.loads(x) if x else {} for x in (b, m, s_))
        except ValueError:
            out[rel] = s_
            continue
        if not (Path(rel).name.startswith("_") and all(isinstance(x, dict) for x in (jb, jm, js))):
            out[rel] = s_  # la misma pieza cambiada en las dos PCs: gana la que se trae
            continue
        junto = dict(jm)
        for k in set(jb) | set(jm) | set(js):
            if js.get(k) == jb.get(k):
                continue
            if k in js:
                junto[k] = js[k]
            else:
                junto.pop(k, None)
        out[rel] = json.dumps(junto, ensure_ascii=False, indent=1).encode("utf-8")
    return out


def traer(version=None):
    """Trae una versión del proyecto (la última si no se dice) a esta PC. Con la última, se junta con lo de aquí
    (ver _mezclar); con una versión vieja del historial («Volver a esta»), queda exactamente como era. Antes se
    guarda una copia de seguridad de lo de esta PC. Devuelve {version, cambios, autor, sigue_cambiado}."""
    import respaldos
    with _cerrojo:
        rem = _leer_remoto()
        ultima = int(rem.get("version") or 0)
        vr = int(version or ultima)
        if not vr:
            raise ValueError("Todavía no hay un proyecto en Drive para esta sesión")
        nuevos = _bajar_zip(vr)
        try:
            respaldos.hacer(nombre=f"Antes de traer la versión {vr} del proyecto", subir=False)
        except Exception as e:  # noqa: BLE001
            print("No se pudo guardar la copia antes de actualizar:", e)
        actuales = _archivos()
        S = motor.S
        if version and vr != ultima:  # volver a una versión vieja: igual que era
            cambios = {rel: nuevos.get(rel) for rel in set(actuales) | set(nuevos) if actuales.get(rel) != nuevos.get(rel)}
        else:
            cambios = _mezclar(_leer_base(), actuales, nuevos)
        S.subs.mkdir(parents=True, exist_ok=True)
        for rel, datos in cambios.items():
            if ".." in Path(rel).parts:
                continue
            if rel in (ESTILO, A_MANO):
                _guardar_virtual(rel, datos)
                continue
            f = S.subs / rel
            if datos is None:
                f.unlink(missing_ok=True)
                continue
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f".{f.name}.part")
            tmp.write_bytes(datos)
            os.replace(tmp, f)  # con fecha nueva: los videos que la usan quedan «⟳ rearmar»
        motor._prod_cache = None
        ahora = _archivos()
        # la base es la versión traída: si quedó algo propio de aquí, se sube solo como versión nueva
        _guardar_local({"version": vr, "firma": _firma(nuevos), "cuando": time.time()}, nuevos)
        ESTADO.pop(S.semana, None)
        entrada = next((h for h in rem.get("historial") or [] if h.get("version") == vr), {})
        return {"version": vr, "cambios": entrada.get("cambios") or [], "autor": entrada.get("autor"),
                "sigue_cambiado": _firma(ahora) != _firma(nuevos)}


def _cambie_aqui():
    """¿Hay algo editado en esta PC que todavía no está en ninguna versión del proyecto?"""
    loc = _leer_local()
    firma = _firma(_archivos())
    return firma != (loc.get("firma") or {}) if loc.get("version") is not None else bool(firma)


def volver(version):
    """«Volver a esta»: la sesión queda como la versión elegida y eso se sube como una versión NUEVA del proyecto
    (v5 = copia de la v2), así las otras PCs reciben el aviso y nada se pierde: lo de antes sigue en el historial.
    Si en esta PC había cambios sin subir, primero se suben, para que también queden en el historial."""
    with _cerrojo:
        antes = publicar("lo que había antes de volver") if _cambie_aqui() and not revisar().get("pendiente") else None
        r = traer(version)
        r["nueva"] = publicar(f"volvió a la versión {int(version)}")
        r["antes"] = antes
        return r


def antes_de_restaurar():
    """Antes de restaurar una copia: lo de esta PC que no se había subido se sube, para que quede en el historial."""
    if not disponible():
        return None
    with _cerrojo:
        return publicar("lo que había antes de restaurar") if _cambie_aqui() and not revisar().get("pendiente") else None


def tras_restaurar(fecha):
    """Después de restaurar una copia de seguridad, eso pasa a ser la versión del proyecto (para todo el equipo)."""
    if not disponible():
        return None
    return publicar(f"volvió a la copia de seguridad del {fecha}" if fecha else "volvió a una copia de seguridad")


def quedarme():
    """Lo de esta PC pasa a ser la versión del proyecto (lo que había queda en el historial)."""
    return publicar("se quedó con su versión")


# ---------- en segundo plano ----------

def vigilar(ocupado=lambda: False):
    """Cada minuto: si en esta PC cambió algo y nadie subió otra versión, se sube sola; si otra PC subió una, se avisa."""
    def bucle():
        while True:
            time.sleep(CADA)
            try:
                if ocupado():
                    continue
                e = revisar()
                if e.get("activo") and e.get("cambie") and not e.get("pendiente"):
                    publicar()
                    revisar()
            except Exception as ex:  # noqa: BLE001 — sin internet: se intenta en la próxima vuelta
                print("Proyecto:", str(ex)[:200], flush=True)
    threading.Thread(target=bucle, daemon=True).start()
