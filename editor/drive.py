"""Conexión con Google Drive por enlace (sin Google Drive para escritorio), usando rclone.

Cómo funciona:
  1. En la sala se pega el link de la carpeta del equipo (EDICIÓN DE VIDEOS) y se toca «Conectar».
  2. rclone abre Google en el navegador; se entra una sola vez con la cuenta que tiene acceso.
  3. La sala baja a esta PC solo la sesión que se va a editar (Clips, Guion, Flow, Musica) y los sonidos
     que usa, y sube a Drive lo que produce: V.FINALES, asignaciones, imágenes de Flow, música y guion.

Todo vive en datos/: rclone.exe (se baja solo la primera vez), rclone.conf (la llave de acceso; no se
comparte) y drive/<sesión>/ (copia local de trabajo).

Uso desde consola (lo usa Claude para subir guiones):
  python drive.py subir "14 10 SESIÓN" Guion
  python drive.py sesiones
"""
import io
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import cuenta
import motor

REMOTO = cuenta.REMOTO  # la sesión de Google es la común de las tres salas (cuenta.py)
CONF = cuenta.CONF
CACHE = motor.DATOS / "drive"
LISTA = motor.DATOS / "drive_sesiones.json"
# Bloopers: tomas que el equipo apartó (la sala no las usa; bajarlas solo gasta tiempo y disco)
# (los .docx sí se bajan: el guion en Word que el equipo deja en Guion/ se lee solo; y los .png también:
#  una imagen de Flow puede venir en .png)
EXCLUIR_AL_BAJAR = ["V.FINALES/**", "VIDEOS FINALES/**", "listos/**", "desktop.ini", "_SALA_PROYECTO/**", "_REEMPLAZADOS/**",
                    "{Bloopers,BLOOPERS,bloopers,Blooper,BLOOPER}/**"]
SUBIBLES = ["V.FINALES", "Flow", "Musica", "Guion", "asignaciones.json", "Clips"]
_candado = threading.Lock()


def rclone():
    """Ruta de rclone.exe; si no está, lo baja de la página oficial (una sola vez, ~30 MB)."""
    for c in (motor.DATOS / "herramientas" / "rclone.exe", motor.RAIZ.parent / "rclone" / "rclone.exe"):
        if c.exists():
            return c
    destino = motor.DATOS / "herramientas" / "rclone.exe"
    destino.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request("https://downloads.rclone.org/rclone-current-windows-amd64.zip",
                                 headers={"User-Agent": "Mozilla/5.0 SalaSuplevet"})
    with urllib.request.urlopen(req, timeout=300) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    nombre = next(n for n in z.namelist() if n.endswith("rclone.exe"))
    destino.write_bytes(z.read(nombre))
    return destino


def _rc(*args, avance=None, timeout=None, prefijo="Drive: "):
    cmd = [str(rclone()), *map(str, args), "--config", str(CONF)]
    if not avance:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError("Drive: " + (r.stderr.strip().splitlines() or ["error desconocido"])[-1])
        return r.stdout
    p = subprocess.Popen(cmd + ["--stats", "2s", "--stats-one-line", "-v"], stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace")
    motor.PROCESOS.add(p)
    ultima = ""
    for linea in p.stderr:
        linea = linea.strip()
        ultima = linea or ultima
        # con --stats-one-line: «… INFO  :   1.2 GiB / 3.4 GiB, 35%, 12.1 MiB/s, ETA 3m2s»
        m = re.search(r"([\d.]+\s*\w?i?B\s*/\s*[\d.]+\s*\w?i?B),\s*(\d+%)(?:,\s*([\d.]+\s*\w?i?B/s))?(?:,\s*ETA\s*(\S+))?", linea)
        if m and avance:
            vel = f" · {m.group(3)}" if m.group(3) else ""
            eta = f" · faltan {m.group(4)}" if m.group(4) and m.group(4) != "-" else ""
            avance(f"{prefijo}{m.group(2)} ({m.group(1)}{vel}{eta})")
    codigo = p.wait()
    motor.PROCESOS.discard(p)
    motor.revisar_cancelado()
    if codigo != 0:
        raise RuntimeError("Drive: " + ultima[-300:])


def carpeta_id(link):
    m = re.search(r"/folders/([\w-]{10,})", link) or re.search(r"[?&]id=([\w-]{10,})", link) or re.fullmatch(r"([\w-]{20,})", link.strip())
    if not m:
        raise ValueError("No reconozco el link. Copia el enlace de la carpeta desde Drive (Compartir → Copiar enlace).")
    return m.group(1)


def estado():
    cfg = motor.CONFIG.get("drive", {})
    conectado = cuenta.estado()["conectado"]
    return {"activo": bool(cfg.get("activo")) and conectado, "conectado": conectado, "link": cfg.get("link", ""),
            "carpeta_id": cfg.get("carpeta_id", ""), "credenciales_propias": bool(cfg.get("credenciales_propias")),
            "sesiones": sesiones_en_drive()}


def conectar(link, client_id="", client_secret="", avance=print):
    """Abre Google en el navegador para dar acceso (una sola vez) y deja la sala trabajando contra esa carpeta.
    client_id/client_secret: las credenciales OAuth del proyecto de Google Cloud del equipo (tipo «App de escritorio»).
    Sin ellas usa las de rclone, que comparten cuota con todos sus usuarios: sirve para probar."""
    carpeta_id(link)  # avisa si el link no sirve
    if client_id.strip():  # el cliente de Google del equipo: queda para las próximas entradas
        info = cuenta._leer(cuenta.INFO)
        info["cliente"] = {"id": client_id.strip(), "secreto": client_secret.strip()}
        cuenta._guardar(cuenta.INFO, info)
    avance("Se abrió Google en el navegador: entra con la cuenta que tiene acceso a la carpeta y acepta.")
    cuenta.entrar(link)
    avance("Leyendo las sesiones de Drive…")
    asegurar_config(forzar=True)
    avance("Conectado a Google Drive.")


def asegurar_config(forzar=False):
    """Con la sesión común abierta (se entró desde cualquiera de las salas), la sala trabaja contra ese proyecto."""
    c = cuenta.estado()
    cfg = motor.CONFIG.get("drive", {})
    if not c["conectado"] or (not forzar and cfg.get("activo") and cfg.get("carpeta_id") == c["carpeta_id"]):
        return False
    motor.guardar_config({"drive": {"activo": True, "carpeta_id": c["carpeta_id"], "link": c["link"],
                                    "credenciales_propias": bool(cuenta._leer(cuenta.INFO).get("cliente"))},
                          "carpeta_trabajo": str(CACHE), "biblioteca": str(CACHE / "SOUND FX")})
    try:
        actualizar_lista()
    except Exception as e:  # sin internet: la lista se trae después
        print("Drive:", e, flush=True)
    return True


def desconectar():
    cuenta.cerrar_sesion()
    motor.guardar_config({"drive": {**motor.CONFIG.get("drive", {}), "activo": False}})


def actualizar_lista():
    """Lista de sesiones que hay en Drive (se guarda para mostrarla aunque no haya internet)."""
    datos = json.loads(_rc("lsjson", f"{REMOTO}:", "--dirs-only", timeout=60) or "[]")
    nombres = sorted(d["Name"] for d in datos if motor.es_nombre_sesion(d["Name"]))
    LISTA.write_text(json.dumps(nombres, ensure_ascii=False), encoding="utf-8")
    actualizar_producciones([n for n in nombres if motor.PRODUCCION.match(n)], nombres)
    bajar_grupos()
    bajar_archivadas()
    return nombres


def actualizar_producciones(prods, sueltas):
    """Las producciones «P02 TEMA» que tienen sus días de grabación dentro («P02 …/11／10 SESIÓN/Clips/…»):
    cuáles son sus días y dónde está el guion. Si un día estaba suelto y ahora está dentro, su copia de esta PC
    se muda con él (no se vuelve a bajar ni a procesar)."""
    out = {}
    for prod in prods:
        try:
            dentro = json.loads(_rc("lsjson", f"{REMOTO}:{prod}", "--dirs-only", "--max-depth", "2", timeout=60) or "[]")
        except Exception:
            continue
        rutas = {d["Path"] for d in dentro}
        dias = sorted(d["Name"] for d in dentro if "/" not in d["Path"] and (
            motor.es_dia_de_grabacion(d["Name"]) or (d["Name"].lower() not in motor.SUBCARPETAS and f"{d['Path']}/Clips" in rutas)))
        if not dias:
            continue
        if any(x in rutas for x in motor.CARPETAS_GUION):
            guion = ""
        else:
            guion = next((d for d in dias if any(f"{d}/{x}" in rutas for x in motor.CARPETAS_GUION)), dias[0])
        out[prod] = {"sesiones": dias, "guion": guion}
        for dia in dias:
            if dia not in sueltas:
                motor.mudar_a_produccion(prod, dia, CACHE)
    motor.PRODUCCIONES_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def bajar_grupos():
    """Los grupos de sesiones (varias grabaciones = una producción) se comparten por Drive: _SALA/grupos.json."""
    try:
        _rc("copyto", f"{REMOTO}:_SALA/grupos.json", motor.GRUPOS_PATH, timeout=60)
    except Exception:
        pass  # todavía no hay grupos en Drive


def bajar_archivadas():
    """Las sesiones archivadas (terminadas) también se comparten: _SALA/archivadas.json."""
    try:
        _rc("copyto", f"{REMOTO}:_SALA/archivadas.json", motor.ARCHIVADAS_PATH, timeout=60)
    except Exception:
        pass  # todavía nadie archivó nada


def subir_archivadas():
    if estado()["activo"] and motor.ARCHIVADAS_PATH.exists():
        threading.Thread(target=lambda: _rc("copyto", motor.ARCHIVADAS_PATH, f"{REMOTO}:_SALA/archivadas.json", timeout=60), daemon=True).start()


def subir_grupos():
    if estado()["activo"] and motor.GRUPOS_PATH.exists():
        threading.Thread(target=lambda: _rc("copyto", motor.GRUPOS_PATH, f"{REMOTO}:_SALA/grupos.json", timeout=60), daemon=True).start()


def sesiones_en_drive():
    try:
        return json.loads(LISTA.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


def traer_guion(local):
    """Trae de Drive el guion de la sesión si allá está más nuevo (lo cambió Claude, el panel u otra PC).
    Solo reemplaza archivos más viejos que los de Drive: lo editado en esta sala y aún no subido no se pisa.
    Devuelve True si cambió algo."""
    # todas las carpetas del guion que haya (Guion, Guiones, 1_guion): el equipo puede crear «Guiones» en Drive
    # aunque la sala ya tenga «Guion»
    base = Path(local).parent
    rel = base.relative_to(CACHE).as_posix()
    # Una sola llamada a Drive para las tres carpetas posibles (antes eran tres seguidas y cada una volvía a buscar toda
    # la ruta de carpetas: tardaba unos 14 s y corría al abrir, cada 90 s y al volver a la ventana)
    carpetas = [base / x for x in motor.CARPETAS_GUION]
    habian = {d: d.is_dir() for d in carpetas}

    def huella():
        return {(d.name, f.name): (f.stat().st_size, int(f.stat().st_mtime)) for d in carpetas if d.is_dir() for f in d.glob("*") if f.is_file()}
    antes = huella()
    filtro = [a for x in motor.CARPETAS_GUION for a in ("--include", f"/{x}/*")]
    try:
        _rc("copy", f"{REMOTO}:{rel}", base, "--update", "--max-depth", "2", *filtro, "--exclude", "desktop.ini", timeout=60)
    except Exception:
        return False  # sin internet: se reintenta en la próxima revisión
    for d in carpetas:
        if not habian[d] and d.is_dir() and not any(d.iterdir()):
            d.rmdir()  # vacía: no se deja, para que no tape a la que sí tiene el guion
    return huella() != antes


def bajada(nombre):
    return (CACHE / nombre).is_dir()


RAPIDO = ["--transfers", "8", "--checkers", "16", "--fast-list", "--multi-thread-streams", "4"]


def bajar_sesion(nombre, avance=print):
    """Copia a esta PC lo necesario para editar la sesión (sin los videos finales).
    Primero lo liviano (guion, asignaciones, música, Flow) para que la sala ya muestre el guion;
    después los clips grabados, varios a la vez."""
    with _candado:
        excluir = [a for e in EXCLUIR_AL_BAJAR for a in ("--exclude", e)]
        avance(f"Bajando el guion de «{nombre}»…")
        _rc("copy", f"{REMOTO}:{nombre}", CACHE / nombre, *excluir, "--exclude", "Clips/**", "--exclude", "*.{mov,MOV,mp4,MP4}",
            *RAPIDO, avance=avance, prefijo="Guion y archivos: ")
        avance(f"Bajando los clips de «{nombre}»…")
        _rc("copy", f"{REMOTO}:{nombre}", CACHE / nombre, *excluir, *RAPIDO, avance=avance, prefijo="Clips: ")
        bajar_biblioteca(avance)
        (CACHE / nombre / ".bajada").write_text(str(time.time()), encoding="utf-8")
        avance(f"«{nombre}» quedó lista en esta PC")


def bajada_completa(nombre):
    return (CACHE / nombre / ".bajada").exists()


def bajar_biblioteca(avance=print):
    """Solo las carpetas y archivos de sonidos que usa la sala (sfx_mapa), no toda la librería."""
    for rels in motor.CONFIG.get("sfx_mapa", {}).values():
        for rel in rels:
            destino = CACHE / "SOUND FX" / rel
            if destino.exists():
                continue
            avance(f"Bajando sonidos: {rel.split('/')[-1]}")
            fuente = f"{REMOTO}:SOUND FX/{rel}"
            if Path(rel).suffix:
                _rc("copyto", fuente, destino)
            else:
                _rc("copy", fuente, destino, "--max-depth", "1", "--exclude", "*.asd")


def subir(nombre, partes=None, avance=print):
    """Sube a Drive lo que produce la sala en esa sesión. No borra nada en Drive."""
    with _candado:
        for parte in partes or SUBIBLES:
            if parte == "Guion":  # la carpeta del guion puede llamarse Guion, Guiones o 1_guion
                parte = next((x for x in motor.CARPETAS_GUION if (CACHE / nombre / x).exists()), parte)
            local = CACHE / nombre / parte
            if not local.exists():
                continue
            avance(f"Subiendo a Drive: {nombre}/{parte}")
            if local.is_dir():
                _rc("copy", local, f"{REMOTO}:{nombre}/{parte}", "--exclude", "desktop.ini", avance=avance)
            else:
                _rc("copyto", local, f"{REMOTO}:{nombre}/{parte}")


# Lo que no se pudo subir a Drive (sin internet, Drive no deja…): la sala lo muestra con «Reintentar».
# Antes solo quedaba en el log y la otra PC nunca recibía el archivo.
PENDIENTES = []
_cand_pend = threading.Lock()


def _anotar_pendiente(nombre, partes, error):
    with _cand_pend:
        for x in PENDIENTES:
            if x["sesion"] == nombre and x["partes"] == list(partes or []):
                x.update(error=str(error)[:200], cuando=time.time())
                return
        PENDIENTES.append({"sesion": nombre, "partes": list(partes or []), "error": str(error)[:200], "cuando": time.time()})


def pendientes():
    with _cand_pend:
        return [dict(x) for x in PENDIENTES]


def reintentar_pendientes(avance=print):
    """Vuelve a subir lo que quedó pendiente. Devuelve cuántas subidas siguen fallando."""
    with _cand_pend:
        lista = list(PENDIENTES)
        PENDIENTES.clear()
    for x in lista:
        try:
            subir(x["sesion"], x["partes"] or None, avance=avance)
        except Exception as e:  # noqa: BLE001
            _anotar_pendiente(x["sesion"], x["partes"], e)
    return len(PENDIENTES)


def subir_en_segundo_plano(nombre, partes):
    if not estado()["activo"]:
        return
    def tarea():
        try:
            subir(nombre, partes, avance=lambda m: None)
        except Exception as e:  # sin internet: queda pendiente y la sala avisa con «Reintentar»
            print("No se pudo subir a Drive:", e)
            _anotar_pendiente(nombre, partes, e)
    threading.Thread(target=tarea, daemon=True).start()


def quitar(sesion, rel):
    """Un archivo reemplazado en la sala se aparta también en Drive (a _REEMPLAZADOS/), para que la bajada siguiente
    no lo traiga de vuelta (rclone copy no borra nada). Si en Drive no estaba, no pasa nada."""
    if not estado()["activo"]:
        return
    def tarea():
        try:
            _rc("moveto", f"{REMOTO}:{sesion}/{rel}", f"{REMOTO}:{sesion}/_REEMPLAZADOS/{time.strftime('%Y%m%d-%H%M%S')}_{Path(rel).name}", timeout=300)
        except Exception as e:  # noqa: BLE001
            if "not found" not in str(e).lower() and "directory not found" not in str(e).lower():
                print("No se pudo apartar en Drive:", rel, e)
    threading.Thread(target=tarea, daemon=True).start()


def existe(sesion, rel):
    """¿Ese archivo ya está en Drive? (para no pisar al renombrar). None si no se pudo preguntar."""
    try:
        return bool((_rc("lsf", f"{REMOTO}:{sesion}/{rel}", timeout=60) or "").strip())
    except Exception as e:  # noqa: BLE001
        return False if "not found" in str(e).lower() else None


def mover(sesion, viejo, nuevo):
    """Mueve un archivo dentro de la sesión en Drive (para descartar o recuperar clips). Si el destino ya existe
    en Drive (otra PC subió una toma con ese nombre), no lo pisa."""
    if existe(sesion, nuevo):
        raise RuntimeError(f"En Drive ya hay un archivo «{nuevo}»: no se renombró para no pisarlo")
    _rc("moveto", f"{REMOTO}:{sesion}/{viejo}", f"{REMOTO}:{sesion}/{nuevo}", timeout=300)


def crear_sesion(nombre):
    # Guiones: el guion · Clips: solo lo grabado (C1/, C2/…, CTA/ o sueltos) · Flow: imágenes y clips de apoyo
    for sub in ("Guiones", "Clips", "Flow", "Musica", "V.FINALES"):
        _rc("mkdir", f"{REMOTO}:{nombre}/{sub}")
    actualizar_lista()


# ---------- Primera vez: entrar con Google y elegir (o crear) la carpeta del equipo ----------
# Para quien instala la sala sin tener todavía la carpeta: entra con su cuenta, ve sus carpetas y las compartidas con
# ella, y elige una o crea la suya. Mientras no elige, las apps no usan la cuenta (sin carpeta no hay dónde trabajar).
# Las otras dos salas comparten esta sesión de Google (cuenta.py), y con la carpeta elegida quedan conectadas también.

def entrar_sin_carpeta(client_id="", client_secret=""):
    """Abre Google en el navegador para entrar. client_id/secret: las del proyecto de Google Cloud del equipo (opcional)."""
    with cuenta._candado:
        info = cuenta._leer(cuenta.INFO)
        if client_id.strip():
            info["cliente"] = {"id": client_id.strip(), "secreto": client_secret.strip()}
        if cuenta.CONF.exists():
            try:
                cuenta.rc("config", "delete", REMOTO)
            except RuntimeError:
                pass
        cliente = info.get("cliente") or {}
        extra = [f"client_id={cliente['id']}", f"client_secret={cliente['secreto']}"] if cliente.get("id") else []
        cuenta.rc("config", "create", REMOTO, "drive", "scope=drive", *extra, timeout=600)
        info.update(activo=False, sin_carpeta=True, link="", carpeta_id="", entro=time.strftime("%Y-%m-%d %H:%M:%S"))
        cuenta._guardar(cuenta.INFO, info)
    cuenta._quien()
    return cuenta._leer(cuenta.INFO)


_SIN_RAIZ = f"{REMOTO},root_folder_id='':"  # «Mi unidad» entera, aunque la conexión ya apunte a una carpeta


def carpetas_para_elegir():
    """{mias: [...], compartidas: [...]}: las carpetas de «Mi unidad» y las compartidas con esta cuenta (nombre e id)."""
    def lista(*extra):
        try:
            return [{"nombre": x["Name"], "id": x.get("ID", "")} for x in json.loads(cuenta.rc("lsjson", _SIN_RAIZ, "--dirs-only", "--max-depth", "1", *extra, timeout=120) or "[]")
                    if x.get("ID") and not x["Name"].startswith(".")]
        except RuntimeError:
            return []
    return {"mias": sorted(lista(), key=lambda x: x["nombre"].lower()), "compartidas": sorted(lista("--drive-shared-with-me"), key=lambda x: x["nombre"].lower())}


def crear_carpeta_equipo(nombre="EDICIÓN DE VIDEOS"):
    """Crea la carpeta del equipo en «Mi unidad» (si ya existe una con ese nombre, la usa). Devuelve su id."""
    nombre = " ".join(str(nombre or "").split())[:80] or "EDICIÓN DE VIDEOS"
    if any(c in nombre for c in '\\/:*?"<>|'):
        raise ValueError('El nombre de la carpeta no puede llevar / \\ : * ? " < > |')
    cuenta.rc("mkdir", f"{_SIN_RAIZ}{nombre}", timeout=120)
    d = next((x for x in carpetas_para_elegir()["mias"] if x["nombre"] == nombre), None)
    if not d:
        raise RuntimeError("Se creó la carpeta pero Drive no la muestra todavía: vuelve a intentar en un momento")
    return d["id"], nombre


def usar_carpeta(cid, nombre=""):
    """Deja la conexión trabajando dentro de esa carpeta (la de las sesiones) y revisa que se pueda escribir en ella.
    Devuelve {editor: bool, link}."""
    if not re.fullmatch(r"[\w-]{10,}", cid or ""):
        raise ValueError("Esa carpeta no es válida")
    with cuenta._candado:
        # se cambia solo la línea root_folder_id de la conexión (sin librerías que el .exe podría no traer)
        texto = cuenta.CONF.read_text(encoding="utf-8")
        m = re.search(rf"^\[{REMOTO}\]\n(.*?)(?=^\[|\Z)", texto, re.S | re.M)
        if not m:
            raise RuntimeError("Primero entra con tu cuenta de Google")
        seccion = m.group(1)
        if re.search(r"^root_folder_id\s*=.*$", seccion, re.M):
            seccion = re.sub(r"^root_folder_id\s*=.*$", f"root_folder_id = {cid}", seccion, flags=re.M)
        else:
            seccion = f"root_folder_id = {cid}\n" + seccion
        cuenta.CONF.write_text(texto[:m.start(1)] + seccion + texto[m.end(1):], encoding="utf-8")
    # ¿se puede escribir? (con permiso de solo lectura, Drive borra en silencio lo que la sala sube)
    editor = True
    prueba = f"_SALA/.prueba_{int(time.time())}.txt"
    try:
        cuenta.rc("rcat", f"{REMOTO}:{prueba}", timeout=60, entrada="prueba de la sala")
        cuenta.rc("deletefile", f"{REMOTO}:{prueba}", timeout=60)
    except RuntimeError:
        editor = False
    link = f"https://drive.google.com/drive/folders/{cid}"
    info = cuenta._leer(cuenta.INFO)
    info.update(activo=True, link=link, carpeta_id=cid, carpeta_nombre=nombre)
    info.pop("sin_carpeta", None)
    cuenta._guardar(cuenta.INFO, info)
    motor.guardar_config({"drive": {**motor.CONFIG.get("drive", {}), "activo": True, "link": link, "carpeta_id": cid}})
    asegurar_config(forzar=True)
    try:
        actualizar_lista()
    except Exception:  # noqa: BLE001
        pass
    return {"editor": editor, "link": link, "nombre": nombre}


def preparar_carpeta_nueva(nombre_marca=""):
    """En una carpeta recién creada deja lo de base: _MARCA (con la marca neutra) y _SALA."""
    import marca
    marca.raiz().mkdir(parents=True, exist_ok=True)
    if not (marca.raiz() / "marca.json").exists():
        marca.guardar({**marca.NEUTRA, "nombre": nombre_marca})
    try:
        cuenta.rc("mkdir", f"{REMOTO}:_SALA", timeout=60)
    except RuntimeError:
        pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) >= 2 and sys.argv[1] == "sesiones":
        print("\n".join(actualizar_lista()))
    elif len(sys.argv) >= 3 and sys.argv[1] == "subir":
        subir(sys.argv[2], sys.argv[3:] or None)
        print("Listo.")
    elif len(sys.argv) >= 3 and sys.argv[1] == "bajar":
        bajar_sesion(sys.argv[2])
        print("Listo.")
    else:
        print(__doc__)
