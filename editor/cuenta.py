"""La cuenta: entrar con Google, quedarse con la sesión abierta y ver en qué dispositivos se abrió.

Es el mismo archivo en las tres apps (Sala de edición, Sala de diseño y Publicador): si se cambia en una, se copia
igual a las otras dos. Todo lo de la cuenta vive en la PC en %LOCALAPPDATA%/Salas (una sola sesión para las tres):
  rclone.conf      la llave de Google (no sale nunca de la PC)
  drive.json       el proyecto (link de la carpeta de Drive) y quién entró (correo, nombre, foto)
  dispositivo.json el id de esta PC

El candado de verdad es Drive: solo entra quien tenga compartida la carpeta del proyecto. Lo que se ve en
«Dispositivos y seguridad» vive en Drive, en _SISTEMA/dispositivos/<correo>/<id de la PC>.json, y cada app lo
actualiza al abrir y cada 10 minutos. «Cerrar sesión en esa PC» deja una marca que esa PC lee en su siguiente
latido y entonces cierra la sesión sola.
"""
import io
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

SUITE = Path(os.environ.get("SALAS_SUITE") or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Salas")
SUITE.mkdir(parents=True, exist_ok=True)
REMOTO = "proyecto"
CONF = SUITE / "rclone.conf"
INFO = SUITE / "drive.json"
DISPOSITIVO = SUITE / "dispositivo.json"
CARPETA = "_SISTEMA/dispositivos"
DIAS_NUEVO = 7
_app = {"nombre": "", "version": ""}
_candado = threading.Lock()
_ultimo_latido = {"t": 0}
PROCESOS = set()


def iniciar(app, version):
    """Cada app se presenta al arrancar: así el registro de dispositivos dice qué apps usa cada PC."""
    _app.update(nombre=app, version=version)
    traer_sesion_vieja()


# ---------- rclone ----------
def rclone():
    destino = SUITE / "herramientas" / "rclone.exe"
    otras = [Path(os.environ.get("LOCALAPPDATA", "")) / "SalaSuplevet" / "datos" / "herramientas" / "rclone.exe"]
    for c in [destino, *otras]:
        if c.exists():
            return c
    destino.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request("https://downloads.rclone.org/rclone-current-windows-amd64.zip", headers={"User-Agent": "Mozilla/5.0 Salas"})
    with urllib.request.urlopen(req, timeout=300) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    destino.write_bytes(z.read(next(n for n in z.namelist() if n.endswith("rclone.exe"))))
    return destino


def rc(*args, timeout=None, entrada=None):
    cmd = [str(rclone()), *map(str, args), "--config", str(CONF)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE if entrada is not None else None,
                         text=True, encoding="utf-8", errors="replace", creationflags=0x08000000 if os.name == "nt" else 0)
    PROCESOS.add(p)
    try:
        out, err = p.communicate(entrada, timeout=timeout)
    finally:
        PROCESOS.discard(p)
    if p.returncode != 0:
        raise RuntimeError("Drive: " + ((err or "").strip().splitlines() or ["error desconocido"])[-1])
    return out


# ---------- Estado de la sesión ----------
def _leer(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _guardar(p, datos):
    Path(p).write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def _llave():
    try:
        texto = CONF.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return ""
    m = re.search(rf"^\[{REMOTO}\]\n(.*?)(?=^\[|\Z)", texto, re.S | re.M)
    return m.group(1) if m else ""


def estado():
    i = _leer(INFO)
    con_llave = "token" in _llave()
    return {"conectado": bool(i.get("activo")) and con_llave, "email": i.get("email", ""), "nombre": i.get("nombre", ""),
            "foto": i.get("foto", ""), "link": i.get("link", ""), "carpeta_id": i.get("carpeta_id", ""),
            "dispositivo": dispositivo()["nombre"], "cerrada_desde_otra_pc": bool(i.get("cerrada_desde_otra_pc"))}


def carpeta_id(link):
    m = re.search(r"/folders/([\w-]{10,})", link) or re.search(r"[?&]id=([\w-]{10,})", link) or re.fullmatch(r"([\w-]{20,})", link.strip())
    if not m:
        raise ValueError("No reconozco el link. Copia el enlace de la carpeta del proyecto desde Drive (Compartir → Copiar enlace).")
    return m.group(1)


def traer_sesion_vieja():
    """Si esta PC ya estaba conectada con la Sala de edición (antes de la cuenta común), se reusa esa llave.
    Solo la primera vez: si alguien cerró sesión, no se vuelve a entrar solo."""
    if "token" in _llave() or INFO.exists():
        return
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "SalaSuplevet" / "datos"
    try:
        vieja = (base / "rclone.conf").read_text(encoding="utf-8")
        cfg = json.loads((base / "config.local.json").read_text(encoding="utf-8")).get("drive", {})
    except (FileNotFoundError, ValueError):
        return
    if "token" not in vieja or not cfg.get("carpeta_id") or not cfg.get("activo"):
        return
    seccion = re.search(r"^\[[^\]]+\]\n.*?(?=^\[|\Z)", vieja, re.S | re.M)
    if not seccion:
        return
    CONF.write_text(re.sub(r"^\[[^\]]+\]", f"[{REMOTO}]", seccion.group(0).strip(), count=1) + "\n", encoding="utf-8")
    info = {"activo": True, "link": cfg.get("link", ""), "carpeta_id": cfg["carpeta_id"], "entro": time.strftime("%Y-%m-%d %H:%M:%S")}
    cid = re.search(r"^client_id\s*=\s*(\S+)", seccion.group(0), re.M)
    secreto = re.search(r"^client_secret\s*=\s*(\S+)", seccion.group(0), re.M)
    if cid and secreto:  # el cliente de Google del equipo: se reusa para las próximas entradas
        info["cliente"] = {"id": cid.group(1), "secreto": secreto.group(1)}
    _guardar(INFO, info)
    try:
        _quien()
    except Exception:  # sin internet: se averigua después
        pass


def _quien():
    """Correo, nombre y foto de la cuenta de Google con la que se entró (API de Drive, con la misma llave)."""
    rc("lsjson", f"{REMOTO}:", "--max-depth", "1", "--dirs-only", timeout=60)  # renueva la llave si venció
    m = re.search(r"^token\s*=\s*(\{.*\})\s*$", _llave(), re.M)
    if not m:
        raise RuntimeError("Google no devolvió la llave")
    token = json.loads(m.group(1))["access_token"]
    req = urllib.request.Request("https://www.googleapis.com/drive/v3/about?fields=user", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        u = json.loads(r.read())["user"]
    i = _leer(INFO)
    i.update(email=u.get("emailAddress", ""), nombre=u.get("displayName", ""), foto=u.get("photoLink", ""))
    i.pop("cerrada_desde_otra_pc", None)
    _guardar(INFO, i)
    return i


def entrar(link):
    """Abre Google en el navegador para entrar (una sola vez por PC) y deja la sesión abierta en las tres apps."""
    cid = carpeta_id(link)
    with _candado:
        if CONF.exists():
            try:
                rc("config", "delete", REMOTO)
            except RuntimeError:
                pass
        cliente = _leer(INFO).get("cliente") or {}
        extra = [f"client_id={cliente['id']}", f"client_secret={cliente['secreto']}"] if cliente.get("id") else []
        rc("config", "create", REMOTO, "drive", "scope=drive", f"root_folder_id={cid}", *extra, timeout=600)
        try:
            rc("lsjson", f"{REMOTO}:", "--max-depth", "1", timeout=60)
        except RuntimeError:
            raise RuntimeError("Esa cuenta no tiene acceso a la carpeta del proyecto. Pídele al dueño que la comparta con tu correo.")
        _guardar(INFO, {**_leer(INFO), "activo": True, "link": link, "carpeta_id": cid, "entro": time.strftime("%Y-%m-%d %H:%M:%S")})
        _quien()
    latido(forzar=True)
    return estado()


def cerrar_sesion(marcar_en_drive=True):
    """Borra la llave de esta PC: las tres apps vuelven a la pantalla de entrar."""
    if marcar_en_drive:  # en Drive queda «sesión cerrada» para todas las apps de esta PC
        try:
            i, did = _leer(INFO), dispositivo()["id"]
            carpeta = _carpeta_email(i["email"])
            for a in json.loads(rc("lsjson", f"{REMOTO}:{carpeta}", "--files-only", "--include", f"{did}__*.json", timeout=60) or "[]"):
                if a["Name"].endswith("__cerrar.json"):
                    continue
                reg = _leer_remoto(f"{carpeta}/{a['Name']}")
                if reg:
                    reg["estado"] = "cerrada"
                    _escribir_remoto(f"{carpeta}/{a['Name']}", reg)
        except Exception:
            pass
    with _candado:
        try:
            rc("config", "delete", REMOTO)
        except Exception:
            pass
        i = _leer(INFO)
        _guardar(INFO, {k: i[k] for k in ("link", "carpeta_id", "cliente", "cerrada_desde_otra_pc") if k in i})


# ---------- Dispositivos ----------
def dispositivo():
    d = _leer(DISPOSITIVO)
    if not d.get("id"):
        d = {"id": uuid.uuid4().hex[:12], "creado": time.strftime("%Y-%m-%d %H:%M")}
        _guardar(DISPOSITIVO, d)
    d["nombre"] = socket.gethostname()
    return d


def _slug(texto):
    return re.sub(r"[^a-z0-9]+", "-", texto.lower().replace("ñ", "n").replace("é", "e").replace("ó", "o").replace("í", "i")).strip("-") or "app"


def _carpeta_email(email):
    return f"{CARPETA}/{email}"


def _ruta(email, did, app=None):
    """Cada app escribe solo su archivo: <PC>__<app>.json. El pedido de cierre es <PC>__cerrar.json.
    Así nunca dos programas escriben el mismo archivo (Drive permitiría dos archivos con el mismo nombre)."""
    return f"{_carpeta_email(email)}/{did}__{_slug(app or _app['nombre'])}.json"


def _leer_remoto(ruta):
    try:
        return json.loads(rc("cat", f"{REMOTO}:{ruta}", timeout=30) or "{}")
    except (RuntimeError, ValueError):
        return {}


def _escribir_remoto(ruta, datos):
    rc("rcat", f"{REMOTO}:{ruta}", timeout=60, entrada=json.dumps(datos, ensure_ascii=False, indent=1))


def _anotar(cerrada=False):
    """Anota esta PC con esta app. Devuelve True si otra PC pidió cerrar la sesión de esta."""
    i, d = _leer(INFO), dispositivo()
    if not i.get("email"):
        return False
    ruta = _ruta(i["email"], d["id"])
    previo = _leer_remoto(ruta)
    ahora = time.strftime("%Y-%m-%d %H:%M")
    _escribir_remoto(ruta, {"id": d["id"], "equipo": d["nombre"], "usuario_windows": os.environ.get("USERNAME", ""),
                            "sistema": f"{platform.system()} {platform.release()}", "app": _app["nombre"], "version": _app["version"],
                            "primer_acceso": previo.get("primer_acceso", ahora), "ultimo_acceso": ahora,
                            "estado": "cerrada" if cerrada else "abierta"})
    pedido = _leer_remoto(_ruta(i["email"], d["id"], "cerrar"))
    return bool(pedido) and pedido.get("pedido", "") > i.get("entro", "")  # un pedido viejo no cierra una entrada nueva


def latido(forzar=False):
    """Al abrir y cada 10 minutos: se anota esta PC y se mira si otra PC pidió cerrar esta sesión."""
    if not estado()["conectado"] or (not forzar and time.time() - _ultimo_latido["t"] < 600):
        return
    _ultimo_latido["t"] = time.time()

    def tarea():
        try:
            if not _leer(INFO).get("email"):
                _quien()
            if _anotar():
                cerrar_sesion(marcar_en_drive=True)
                i = _leer(INFO)
                i["cerrada_desde_otra_pc"] = True
                _guardar(INFO, i)
        except Exception as e:  # sin internet: se intenta en el próximo latido
            print("Cuenta:", e, flush=True)
    threading.Thread(target=tarea, daemon=True).start()


def dispositivos():
    """Las PCs donde se abrió esta cuenta (con las apps de cada una), de la más reciente a la más vieja."""
    i, yo = _leer(INFO), dispositivo()["id"]
    if not i.get("email"):
        return []
    carpeta = _carpeta_email(i["email"])
    try:
        rc("dedupe", "--dedupe-mode", "newest", f"{REMOTO}:{carpeta}", timeout=60)  # por si dos apps escribieron a la vez
    except RuntimeError:
        pass
    try:
        archivos = json.loads(rc("lsjson", f"{REMOTO}:{carpeta}", "--files-only", timeout=60) or "[]")
    except RuntimeError:
        return []
    pcs, cierres = {}, {}
    for a in archivos:
        reg = _leer_remoto(f"{carpeta}/{a['Name']}")
        did = a["Name"].split("__")[0]
        if not reg:
            continue
        if a["Name"].endswith("__cerrar.json"):
            cierres[did] = reg
            continue
        pc = pcs.setdefault(did, {"id": did, "equipo": reg.get("equipo"), "usuario_windows": reg.get("usuario_windows"),
                                  "sistema": reg.get("sistema"), "apps": {}, "primer_acceso": reg.get("primer_acceso", ""),
                                  "ultimo_acceso": "", "estado": "cerrada"})
        pc["apps"][reg.get("app") or "?"] = {"version": reg.get("version", ""), "ultimo": reg.get("ultimo_acceso", ""), "estado": reg.get("estado")}
        pc["primer_acceso"] = min(pc["primer_acceso"] or "9", reg.get("primer_acceso", "9"))
        pc["ultimo_acceso"] = max(pc["ultimo_acceso"], reg.get("ultimo_acceso", ""))
        if reg.get("estado") == "abierta":
            pc["estado"] = "abierta"
    limite = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - DIAS_NUEVO * 86400))
    for did, pc in pcs.items():
        pc["esta_pc"] = did == yo
        pc["nuevo"] = pc["primer_acceso"] >= limite
        pc["cerrar"] = did in cierres and pc["estado"] == "abierta"
    return sorted(pcs.values(), key=lambda r: r.get("ultimo_acceso", ""), reverse=True)


def cerrar_en(did):
    """Pide cerrar la sesión de otra PC: esa PC la cierra sola en su siguiente latido (máximo 10 minutos)."""
    i = _leer(INFO)
    if not re.fullmatch(r"[0-9a-f]{6,32}", did or ""):
        raise ValueError("Ese dispositivo no existe")
    _escribir_remoto(_ruta(i["email"], did, "cerrar"), {"pedido": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                        "por": dispositivo()["nombre"]})


def cerrar_en_todas_menos_esta():
    yo = dispositivo()["id"]
    n = 0
    for d in dispositivos():
        if d.get("id") != yo and d.get("estado") != "cerrada":
            cerrar_en(d["id"])
            n += 1
    return n
