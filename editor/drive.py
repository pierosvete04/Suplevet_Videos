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

import motor

REMOTO = "suplevet"
CONF = motor.DATOS / "rclone.conf"
CACHE = motor.DATOS / "drive"
LISTA = motor.DATOS / "drive_sesiones.json"
EXCLUIR_AL_BAJAR = ["V.FINALES/**", "VIDEOS FINALES/**", "listos/**", "*.docx", "*.png", "desktop.ini"]
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
    conectado = CONF.exists() and f"[{REMOTO}]" in CONF.read_text(encoding="utf-8", errors="ignore") and "token" in CONF.read_text(encoding="utf-8", errors="ignore")
    return {"activo": bool(cfg.get("activo")) and conectado, "conectado": conectado, "link": cfg.get("link", ""),
            "carpeta_id": cfg.get("carpeta_id", ""), "credenciales_propias": bool(cfg.get("credenciales_propias")),
            "sesiones": sesiones_en_drive()}


def conectar(link, client_id="", client_secret="", avance=print):
    """Abre Google en el navegador para dar acceso (una sola vez) y deja la sala trabajando contra esa carpeta.
    client_id/client_secret: las credenciales OAuth del proyecto de Google Cloud del equipo (tipo «App de escritorio»).
    Sin ellas usa las de rclone, que comparten cuota con todos sus usuarios: sirve para probar."""
    cid = carpeta_id(link)
    avance("Se abrió Google en el navegador: entra con la cuenta que tiene acceso a la carpeta y acepta.")
    if CONF.exists():
        _rc("config", "delete", REMOTO)
    extra = [f"client_id={client_id.strip()}", f"client_secret={client_secret.strip()}"] if client_id.strip() else []
    _rc("config", "create", REMOTO, "drive", "scope=drive", f"root_folder_id={cid}", *extra, timeout=600)
    avance("Google dio el acceso. Comprobando que se ve la carpeta…")
    _rc("lsjson", f"{REMOTO}:", "--max-depth", "1", timeout=60)  # comprueba que la carpeta se ve
    avance("Leyendo las sesiones de Drive…")
    motor.guardar_config({"drive": {"activo": True, "carpeta_id": cid, "link": link, "credenciales_propias": bool(extra)},
                          "carpeta_trabajo": str(CACHE), "biblioteca": str(CACHE / "SOUND FX")})
    actualizar_lista()
    avance("Conectado a Google Drive.")


def desconectar():
    motor.guardar_config({"drive": {**motor.CONFIG.get("drive", {}), "activo": False}})


def actualizar_lista():
    """Lista de sesiones que hay en Drive (se guarda para mostrarla aunque no haya internet)."""
    datos = json.loads(_rc("lsjson", f"{REMOTO}:", "--dirs-only", timeout=60) or "[]")
    nombres = sorted(d["Name"] for d in datos if "SESI" in d["Name"].upper() or d["Name"].upper().startswith("SEMANA"))
    LISTA.write_text(json.dumps(nombres, ensure_ascii=False), encoding="utf-8")
    bajar_grupos()
    return nombres


def bajar_grupos():
    """Los grupos de sesiones (varias grabaciones = una producción) se comparten por Drive: _SALA/grupos.json."""
    try:
        _rc("copyto", f"{REMOTO}:_SALA/grupos.json", motor.GRUPOS_PATH, timeout=60)
    except Exception:
        pass  # todavía no hay grupos en Drive


def subir_grupos():
    if estado()["activo"] and motor.GRUPOS_PATH.exists():
        threading.Thread(target=lambda: _rc("copyto", motor.GRUPOS_PATH, f"{REMOTO}:_SALA/grupos.json", timeout=60), daemon=True).start()


def sesiones_en_drive():
    try:
        return json.loads(LISTA.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


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
            local = CACHE / nombre / parte
            if not local.exists():
                continue
            avance(f"Subiendo a Drive: {nombre}/{parte}")
            if local.is_dir():
                _rc("copy", local, f"{REMOTO}:{nombre}/{parte}", "--exclude", "desktop.ini", avance=avance)
            else:
                _rc("copyto", local, f"{REMOTO}:{nombre}/{parte}")


def subir_en_segundo_plano(nombre, partes):
    if not estado()["activo"]:
        return
    def tarea():
        try:
            subir(nombre, partes, avance=lambda m: None)
        except Exception as e:  # sin internet: se reintenta en la próxima subida
            print("No se pudo subir a Drive:", e)
    threading.Thread(target=tarea, daemon=True).start()


def crear_sesion(nombre):
    for sub in ("Guion", "Clips/Hooks", "Clips/Cuerpo", "Clips/CTA", "Flow", "Musica", "V.FINALES"):
        _rc("mkdir", f"{REMOTO}:{nombre}/{sub}")
    actualizar_lista()


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
