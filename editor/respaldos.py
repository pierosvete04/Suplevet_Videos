"""Copias de seguridad de los cambios de la sala (no de los videos ni de los clips, que pesan mucho y se pueden rearmar).

Cada copia es un .zip chiquito (decenas de KB) con todo lo que el equipo edita en una sesión: subtítulos corregidos,
marcas, cortes, zooms, versiones, portadas, copies, plan, ediciones, asignaciones y el guion; y además los ajustes
generales de esta PC (estilo de edición, plantillas propias, sonidos editados…) sin llaves ni contraseñas.

· Automáticas: cada pocos minutos, solo si algo cambió desde la última. Se guardan las últimas MAX_AUTO por sesión.
· Con nombre: «Guardar copia ahora»; no se borran solas.
· Antes de restaurar una copia se guarda otra de cómo estaba todo, por si hay que volver.
· Se guardan en datos/respaldos/<sesión>/ y, con Drive conectado, también en Drive (_SALA/respaldos/<sesión>/)."""
import hashlib
import io
import json
import re
import threading
import time
import zipfile
from pathlib import Path

import motor

CARPETA = motor.DATOS / "respaldos"
MAX_AUTO = 60
CADA = 180  # segundos entre revisiones automáticas
_SECRETO = re.compile(r"(^|_)(token|secret|secreto|clave|password|contrase\w*|client_id|client_secret|cid|pin|llave)(_|$)", re.I)
_PROPIO_PC = ("semana", "carpeta", "drive", "panel", "red", "cuenta", "biblioteca")  # ajustes de cada PC que no se restauran
_cerrojo = threading.Lock()


def _sin_secretos(v):
    """Quita a cualquier profundidad las llaves que parecen secretos (token, clave, contraseña…)."""
    if isinstance(v, dict):
        return {k: _sin_secretos(x) for k, x in v.items() if not _SECRETO.search(str(k))}
    if isinstance(v, list):
        return [_sin_secretos(x) for x in v]
    return v


def _carpeta(sesion=None):
    return CARPETA / (sesion or motor.S.semana)


def _contenido():
    """{ruta dentro del zip: bytes} con lo editable de la sesión abierta y los ajustes generales de esta PC."""
    S, d = motor.S, {}
    if S.subs.exists():
        for f in sorted(S.subs.rglob("*.json")):
            rel = f.relative_to(S.subs)
            if not any(p.startswith("_respaldo") for p in rel.parts[:-1]):  # carpetas de respaldo viejas
                d[f"sesion/{rel.as_posix()}"] = f.read_bytes()
    for m in S.miembros:  # asignaciones de clips (la de esta PC manda) de cada sesión del grupo
        drive_f, local_f = motor._asig_archivos(m)
        for f in (local_f, drive_f):
            if f.exists():
                d[f"asignaciones/{m}.json"] = f.read_bytes()
                break
    guion = S.principal / "produccion.json"
    if guion.exists():
        d["guion/produccion.json"] = guion.read_bytes()
    if motor.LOCAL_PATH.exists():  # ajustes generales, sin llaves ni contraseñas
        try:
            local = json.loads(motor.LOCAL_PATH.read_text(encoding="utf-8"))
            d["ajustes/config.local.json"] = json.dumps(_sin_secretos({k: v for k, v in local.items() if not k.startswith(_PROPIO_PC)}),
                                                        ensure_ascii=False, indent=1).encode("utf-8")
        except ValueError:
            pass
    if motor.GRUPOS_PATH.exists():
        d["ajustes/grupos.json"] = motor.GRUPOS_PATH.read_bytes()
    return d


def _firma(contenido):
    h = hashlib.sha1()
    for k in sorted(contenido):
        h.update(k.encode())
        h.update(contenido[k])
    return h.hexdigest()


def _leer_manifiesto(zf):
    try:
        with zipfile.ZipFile(zf) as z:
            return json.loads(z.read("respaldo.json"))
    except (KeyError, ValueError, zipfile.BadZipFile, OSError):
        return {}


def listar(sesion=None):
    """Copias de la sesión, de la más nueva a la más vieja."""
    c = _carpeta(sesion)
    out = []
    for f in sorted(c.glob("*.zip"), reverse=True) if c.exists() else []:
        m = _leer_manifiesto(f)
        out.append({"archivo": f.name, "fecha": m.get("fecha") or f.stem[:16], "nombre": m.get("nombre") or "",
                    "auto": m.get("auto", True), "archivos": m.get("archivos", 0), "kb": round(f.stat().st_size / 1024, 1),
                    "version": m.get("version", ""), "usuario": m.get("usuario", ""), "pc": m.get("pc", ""),
                    "sesion": m.get("sesion", "")})
    return out


def _quien():
    """Quién hizo la copia y en qué PC (la cuenta de Google con la que se entró y el nombre de la PC)."""
    try:
        import cuenta
        c = cuenta.estado()
        return {"usuario": c.get("email") or "", "nombre_usuario": c.get("nombre") or "", "pc": c.get("dispositivo") or ""}
    except Exception:  # noqa: BLE001
        import socket
        return {"usuario": "", "nombre_usuario": "", "pc": socket.gethostname()}


def hacer(nombre="", auto=False, subir=True):
    """Guarda una copia. La automática no se guarda si nada cambió desde la última. Devuelve su info o None."""
    with _cerrojo:
        contenido = _contenido()
        if not contenido:
            return None
        firma = _firma(contenido)
        c = _carpeta()
        c.mkdir(parents=True, exist_ok=True)
        previas = sorted(c.glob("*.zip"))
        if auto and previas and _leer_manifiesto(previas[-1]).get("firma") == firma:
            return None
        ahora = time.localtime()
        limpio = re.sub(r"[^\w\- ]", "", nombre).strip()[:40]
        archivo = c / (time.strftime("%Y-%m-%d_%H-%M-%S", ahora) + (f"_{limpio.replace(' ', '-')}" if limpio else "") + ".zip")
        manifiesto = {"fecha": time.strftime("%Y-%m-%d %H:%M", ahora), "sesion": motor.S.semana, "nombre": nombre.strip()[:60],
                      "auto": auto, "firma": firma, "archivos": len(contenido), "version": motor.VERSION, **_quien()}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.writestr("respaldo.json", json.dumps(manifiesto, ensure_ascii=False, indent=1))
            for k, v in contenido.items():
                z.writestr(k, v)
        archivo.write_bytes(buf.getvalue())
        # las automáticas viejas se van; las que tienen nombre se quedan
        autos = [f for f in sorted(c.glob("*.zip")) if _leer_manifiesto(f).get("auto", True)]
        for f in autos[:-MAX_AUTO]:
            f.unlink(missing_ok=True)
    if subir:
        _subir(archivo)
    return {"archivo": archivo.name, **{k: manifiesto[k] for k in ("fecha", "nombre", "auto", "archivos")},
            "kb": round(archivo.stat().st_size / 1024, 1)}


def _subir(archivo):
    """Copia en Drive, en segundo plano (si Drive no está conectado o falla, la copia igual queda en esta PC)."""
    def subir():
        try:
            import drive
            if drive.estado()["activo"]:
                drive._rc("copyto", archivo, f"{drive.REMOTO}:_SALA/respaldos/{archivo.parent.name}/{archivo.name}", timeout=120)
        except Exception:
            pass
    threading.Thread(target=subir, daemon=True).start()


def restaurar(nombre_archivo):
    """Deja la sesión abierta como estaba en esa copia. Antes guarda una copia de cómo está ahora."""
    f = _carpeta() / Path(nombre_archivo).name
    if not f.exists():
        raise ValueError("No encuentro esa copia")
    m = _leer_manifiesto(f)
    if m.get("sesion") and m["sesion"] != motor.S.semana:
        raise ValueError(f"Esa copia es de la sesión «{m['sesion']}»: ábrela primero")
    import proyecto
    antes = hacer(nombre=f"Antes de restaurar {m.get('fecha', '')}", auto=False)
    S = motor.S
    # con el cerrojo del proyecto: que no se suba a Drive una sesión a medio restaurar.
    # De qué versión del proyecto parte esta PC no se restaura: así lo restaurado cuenta como un cambio de aquí y se
    # sube como versión nueva (si volviera también ese dato, la sala creería estar atrasada y ofrecería «Actualizar»).
    with proyecto._cerrojo, _cerrojo, zipfile.ZipFile(f) as z:
        nombres = [n for n in z.namelist() if n != "respaldo.json" and ".." not in n and Path(n).name != proyecto.LOCAL]
        sesion = {n[len("sesion/"):] for n in nombres if n.startswith("sesion/")}
        # lo que no estaba en la copia (una versión o un ajuste creado después) se quita: la sesión queda igual que entonces
        if S.subs.exists():
            for g in S.subs.rglob("*.json"):
                rel = g.relative_to(S.subs)
                if rel.as_posix() not in sesion and rel.name != proyecto.LOCAL and not any(p.startswith("_respaldo") for p in rel.parts[:-1]):
                    g.unlink()
        for n in nombres:
            datos = z.read(n)
            if n.startswith("sesion/"):
                destino = S.subs / n[len("sesion/"):]
            elif n.startswith("asignaciones/"):
                destino = motor._asig_archivos(Path(n).stem)[1]
            elif n == "guion/produccion.json":
                destino = S.principal / "produccion.json"
            elif n == "ajustes/grupos.json":
                destino = motor.GRUPOS_PATH
            elif n == "ajustes/config.local.json":  # se mezcla: las llaves, Drive, el panel y la sesión abierta de esta PC no se tocan
                motor.guardar_config({k: v for k, v in json.loads(datos).items() if not k.startswith(_PROPIO_PC) and not _SECRETO.search(k)})
                continue
            else:
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(datos)
    # todo lo que usa la sesión cambió: los videos quedan por rearmar
    motor.guardar_config({"estilo_cambio": time.time()})
    return {"restaurada": f.name, "fecha": m.get("fecha", ""), "antes": antes["archivo"] if antes else None}


def vigilar():
    """Hilo de fondo: cada CADA segundos guarda una copia si algo cambió."""
    def bucle():
        while True:
            time.sleep(CADA)
            try:
                hacer(auto=True)
            except Exception:
                pass
    threading.Thread(target=bucle, daemon=True).start()
