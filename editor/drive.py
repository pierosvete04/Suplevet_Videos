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
import os
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
CONF = cuenta.CONF  # la llave de siempre; la que se usa es cuenta.conf()
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
    cmd = [str(rclone()), *map(str, args), "--config", str(cuenta.conf())]
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


DE_QUE_CARPETA = ".sala/carpeta.txt"  # dentro de la copia local: de qué carpeta de Drive es (viaja con ella al apartarla)


def _carpeta_de_la_copia():
    try:
        return (CACHE / DE_QUE_CARPETA).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _anotar_carpeta_de_la_copia(cid):
    try:
        (CACHE / ".sala").mkdir(parents=True, exist_ok=True)
        (CACHE / DE_QUE_CARPETA).write_text(cid, encoding="utf-8")
    except OSError:
        pass


def revisar_copia_local():
    """La llave y la copia local (datos/drive) tienen que ser de la carpeta que eligió la cuenta. Si la copia es de
    otra (otra cuenta, otro proyecto), se aparta en datos/drive_<id> y deja de verse: sus sesiones no se listan ni se
    suben a la carpeta nueva. Devuelve True si apartó algo."""
    c = cuenta.estado()
    if not c["conectado"] or not c["carpeta_id"]:
        return False
    # una copia sin anotar (de antes de la 1.71) es de la carpeta con la que trabajaba rclone, no de la elegida
    cuenta.reparar_llave()  # si la llave apuntaba a otra carpeta, queda anotada en drive.json → llave_apuntaba
    de_la_copia = (_carpeta_de_la_copia()
                   or (cuenta._leer(cuenta.INFO).get("llave_apuntaba") if CACHE.exists() else "") or c["carpeta_id"])
    if de_la_copia == c["carpeta_id"]:
        _anotar_carpeta_de_la_copia(c["carpeta_id"])
        return False
    cambiar_copia_local(de_la_copia, c["carpeta_id"])
    _anotar_carpeta_de_la_copia(c["carpeta_id"])
    motor.guardar_config({"drive": {**motor.CONFIG.get("drive", {}), "activo": True, "carpeta_id": c["carpeta_id"], "link": c["link"]}})
    if not (CACHE / motor.S.semana).is_dir():  # la sesión abierta era de la otra carpeta: no se abre en esta
        motor.guardar_config({"semana": ""})
        motor.usar_semana("")
    print(f"Drive: la copia local era de otra carpeta ({de_la_copia}): quedó aparte en drive_{de_la_copia}", flush=True)
    return True


def asegurar_config(forzar=False):
    """Con la sesión común abierta (se entró desde cualquiera de las salas), la sala trabaja contra ese proyecto."""
    apartada = revisar_copia_local()
    c = cuenta.estado()
    cfg = motor.CONFIG.get("drive", {})
    if not c["conectado"] or (not forzar and not apartada and cfg.get("activo") and cfg.get("carpeta_id") == c["carpeta_id"]):
        return False
    cambiar_copia_local(cfg.get("carpeta_id"), c["carpeta_id"])
    _anotar_carpeta_de_la_copia(c["carpeta_id"])
    motor.guardar_config({"drive": {"activo": True, "carpeta_id": c["carpeta_id"], "link": c["link"],
                                    "credenciales_propias": bool(cuenta._leer(cuenta.INFO).get("cliente"))},
                          "carpeta_trabajo": str(CACHE), "biblioteca": str(CACHE / "SOUND FX")})
    if Path(motor.S.base) != CACHE:  # la sesión abierta todavía apunta a la carpeta de antes de conectar Drive
        motor.usar_semana(motor.S.semana)
    try:
        actualizar_lista()
    except Exception as e:  # sin internet: la lista se trae después
        print("Drive:", e, flush=True)
    return True


# Lo que la sala guarda de la carpeta del proyecto, además de las sesiones: la lista de Drive, las producciones, los
# grupos y las archivadas. Viajan con la copia local al apartarla (en .sala/, que nunca se sube).
_LISTAS = (LISTA, motor.PRODUCCIONES_PATH, motor.GRUPOS_PATH, motor.ARCHIVADAS_PATH)


def cambiar_copia_local(antes, ahora):
    """Cada carpeta del proyecto tiene su copia en esta PC. Al cambiar de carpeta (otra cuenta, otro proyecto) la de
    antes se aparta en datos/drive_<id> y vuelve la de la nueva si ya se había usado: así no se mezclan las sesiones
    ni la marca de dos proyectos (pasó: con la cuenta de Mr Peanut se veían P05 y la marca de Suplevet)."""
    if not antes or not ahora or antes == ahora:
        return
    try:
        aparte = motor.DATOS / f"drive_{antes}"
        if CACHE.exists():
            if aparte.exists():  # una copia vieja de esa carpeta que quedó de antes: se guarda con otro nombre
                aparte.rename(motor.DATOS / f"drive_{antes}_{int(time.time())}")
            CACHE.rename(aparte)
        if aparte.exists():
            (aparte / ".sala").mkdir(exist_ok=True)
            for f in _LISTAS:
                if f.exists():
                    f.replace(aparte / ".sala" / f.name)
        vuelve = motor.DATOS / f"drive_{ahora}"
        if vuelve.exists() and not CACHE.exists():
            vuelve.rename(CACHE)
            for f in _LISTAS:
                g = CACHE / ".sala" / f.name
                if g.exists():
                    g.replace(f)
    except OSError as e:  # un archivo abierto (un video que se está armando): no se sigue, se reintenta al volver a abrir
        raise RuntimeError("No pude apartar los archivos de la carpeta anterior: hay alguno abierto. Espera a que termine "
                           "lo que se está armando, cierra la sala y vuelve a abrirla.") from e


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
        if not motor.clips_solo_aqui():  # con «Clips solo en esta PC», los clips que no están en Drive son de aquí: se quedan
            quitar_borrados_en_drive(nombre, avance)
        (CACHE / nombre / ".bajada").write_text(str(time.time()), encoding="utf-8")
        avance(f"«{nombre}» quedó lista en esta PC")


_NO_SE_LIMPIA = ("v.finales/", "videos finales/", "listos/", "flow/", "assets/", "musica/", "música/", "_sala_proyecto/", "_reemplazados/")


def quitar_borrados_en_drive(nombre, avance=print):
    """Los clips grabados que se borraron (o se movieron) en Drive también salen de esta PC: bajar solo agrega, y la
    sala seguía contando clips que ya no existen (P05: una carpeta borrada en Drive dejaba 24 clips de más). No se
    borran: pasan a datos/papelera/<sesión>/. Solo clips de video; nunca Flow, música ni videos finales."""
    local = CACHE / nombre
    if not local.is_dir():
        return []
    try:
        remotos = {x["Path"] for x in json.loads(_rc("lsjson", f"{REMOTO}:{nombre}", "-R", "--files-only", "--fast-list", timeout=600))}
    except Exception:  # noqa: BLE001 — sin la lista de Drive no se toca nada
        return []
    if not remotos:  # Drive no devolvió nada: mejor no tocar
        return []
    quitados = []
    for f in local.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in motor.EXT_VID:
            continue
        rel = f.relative_to(local).as_posix()
        if rel in remotos or rel.lower().startswith(_NO_SE_LIMPIA) or time.time() - f.stat().st_mtime < 3600:
            continue  # está en Drive, no es un clip grabado, o recién llegó (puede estar subiéndose)
        destino = motor.DATOS / "papelera" / nombre / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(f, destino)
            quitados.append(rel)
        except OSError:
            pass
    for d in sorted((x for x in local.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
        try:
            d.rmdir()  # las carpetas que quedaron vacías (solo si están vacías)
        except OSError:
            pass
    if quitados:
        n = len(quitados)
        avance(f"{n} clip{'s' if n != 1 else ''} que ya no {'están' if n != 1 else 'está'} en Drive "
               f"{'salieron' if n != 1 else 'salió'} de esta PC (en la papelera de la sala, por si acaso)")
    return quitados


def clips_sin_subir(nombre):
    """Los clips grabados de esta PC que no están en Drive (o están con otro tamaño): [(ruta, bytes)]."""
    local = CACHE / nombre
    if not local.is_dir():
        return []
    try:
        remotos = {x["Path"]: x["Size"] for x in
                   json.loads(_rc("lsjson", f"{REMOTO}:{nombre}", "-R", "--files-only", "--fast-list", timeout=600) or "[]")}
    except RuntimeError as e:
        if "not found" not in str(e).lower():  # sin internet: no se sabe (no se da por subido)
            raise
        remotos = {}  # la sesión todavía no está en Drive: todos sus clips son de aquí
    out = []
    for f in local.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in motor.EXT_VID:
            continue
        rel = f.relative_to(local).as_posix()
        if not rel.lower().startswith(_NO_SE_LIMPIA) and remotos.get(rel) != f.stat().st_size:
            out.append((rel, f.stat().st_size))
    return out


def subir_clips(nombre, avance=print):
    """Sube los clips grabados de esta PC que faltan en Drive (los de «Clips solo en esta PC»). Devuelve cuántos."""
    faltan = [rel for rel, _ in clips_sin_subir(nombre)]
    if not faltan:
        return 0
    lista = motor.DATOS / "_trabajo" / "clips_a_subir.txt"
    lista.parent.mkdir(parents=True, exist_ok=True)
    lista.write_text("\n".join(faltan), encoding="utf-8")
    with _candado:
        avance(f"Subiendo a Drive {len(faltan)} clip{'s' if len(faltan) != 1 else ''} de «{nombre}»…")
        _rc("copy", CACHE / nombre, f"{REMOTO}:{nombre}", "--files-from-raw", lista, *RAPIDO, avance=avance, prefijo="Clips: ")
    return len(faltan)


def compartir_transcripciones(nombre, local, avance=print):
    """Lo que la sala escuchó de cada clip (datos/transcripciones/<sesión>) se comparte por Drive, en
    <sesión>/_SALA_PROYECTO/transcripciones: sube lo de esta PC y baja lo de las otras. Así otra PC (el editor) no
    vuelve a escuchar los clips que ya escuchó alguien (unos 10 s por clip). Son archivos chicos."""
    remoto = f"{REMOTO}:{nombre}/_SALA_PROYECTO/transcripciones"
    local = Path(local)
    local.mkdir(parents=True, exist_ok=True)
    try:
        if any(local.glob("*.json")):
            _rc("copy", local, remoto, "--update", "--include", "*.json", *RAPIDO, timeout=900)
        _rc("copy", remoto, local, "--update", "--include", "*.json", *RAPIDO, timeout=900)
        n = sum(1 for _ in local.glob("*.json"))
        if n:
            avance(f"Lo escuchado de {n} clips está al día con Drive")
    except Exception:  # noqa: BLE001 — la primera vez no existe la carpeta en Drive, o no hay internet: no es grave
        pass


def subir_transcripciones(nombre, local):
    """Solo sube lo escuchado de esta PC que todavía no está en Drive (la subida automática de cada 5 minutos)."""
    local = Path(local)
    if not local.is_dir() or not any(local.glob("*.json")):
        return
    try:
        _rc("copy", local, f"{REMOTO}:{nombre}/_SALA_PROYECTO/transcripciones", "--update", "--include", "*.json", *RAPIDO, timeout=900)
    except Exception:  # noqa: BLE001 — sin internet: va en la próxima vuelta
        pass


_PIEZAS = ["--max-depth", "1", "--exclude", "_*", "--include", "*.mp4", "--include", "*.partes.json", "--include", "*.onda.json",
           "--exclude", "*"]


def subir_piezas(nombre, local, avance=print):
    """Las piezas preparadas (datos/procesado/<sesión>: el .mp4 de cada pieza, sus partes y su onda) se comparten por
    Drive en <sesión>/_SALA_PROYECTO/piezas. Así otra PC (el editor) las baja y no tiene que prepararlas otra vez
    (en P05: 3,6 GB). Solo sube lo nuevo o lo que cambió."""
    local = Path(local)
    if not local.is_dir() or not any(local.glob("*.mp4")):
        return
    with _candado:
        avance(f"Subiendo a Drive las piezas preparadas de «{nombre}» (la primera vez tarda: son videos)…")
        _rc("copy", local, f"{REMOTO}:{nombre}/_SALA_PROYECTO/piezas", *_PIEZAS, *RAPIDO, avance=avance,
            prefijo="Piezas preparadas: ", timeout=None)


def bajar_piezas(nombre, local, avance=print):
    """Trae las piezas preparadas que subió otra PC (si las hay). Lo de esta PC que sea más nuevo se queda."""
    local = Path(local)
    local.mkdir(parents=True, exist_ok=True)
    try:
        hay = json.loads(_rc("lsjson", f"{REMOTO}:{nombre}/_SALA_PROYECTO/piezas", "--files-only", "--include", "*.mp4", timeout=300) or "[]")
    except Exception:  # noqa: BLE001 — nadie las subió todavía, o sin internet: se preparan aquí
        return 0
    if not hay:
        return 0
    with _candado:
        avance(f"Bajando las {len(hay)} piezas ya preparadas de «{nombre}» (así no hay que prepararlas en esta PC)…")
        _rc("copy", f"{REMOTO}:{nombre}/_SALA_PROYECTO/piezas", local, "--update", *_PIEZAS, *RAPIDO, avance=avance,
            prefijo="Piezas preparadas: ", timeout=None)
    return len(hay)


def bajada_completa(nombre):
    return (CACHE / nombre / ".bajada").exists()


def bajar_biblioteca(avance=print):
    """Solo las carpetas y archivos de sonidos que usa la sala (sfx_mapa), no toda la librería.
    Si en Drive falta alguno (otra marca, una carpeta renombrada), se avisa y se sigue: la sala usa los que vienen con
    ella. Antes un sonido faltante cortaba la bajada de la sesión y «Analizar los clips» fallaba siempre."""
    faltan = []
    for rels in motor.CONFIG.get("sfx_mapa", {}).values():
        for rel in rels:
            destino = CACHE / "SOUND FX" / rel
            if destino.exists():
                continue
            avance(f"Bajando sonidos: {rel.split('/')[-1]}")
            fuente = f"{REMOTO}:SOUND FX/{rel}"
            try:
                if Path(rel).suffix:
                    _rc("copyto", fuente, destino)
                else:
                    _rc("copy", fuente, destino, "--max-depth", "1", "--exclude", "*.asd")
            except Exception:  # noqa: BLE001
                faltan.append(rel.split("/")[-1])
    if faltan:
        avance(f"Ojo: no encontré en Drive {len(faltan)} carpeta(s) de sonidos (SOUND FX: {', '.join(faltan[:4])}"
               f"{'…' if len(faltan) > 4 else ''}). Se usan los sonidos que trae la sala.")


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


# «Terminar sesión»: todo lo de la sesión sube a Drive, se comprueba archivo por archivo y recién ahí se puede borrar
# la copia de esta PC. Nada se borra en Drive.
EXCLUIR_TODO = ["--exclude", "desktop.ini", "--exclude", ".bajada", "--exclude", "*.part", "--exclude", ".*.part",
                "--exclude", "_REEMPLAZADOS/**"]


def subir_todo(nombre, avance=print):
    """Sube la carpeta entera de la sesión (clips, Flow, música, guion, V.FINALES…). Solo agrega o actualiza."""
    local = CACHE / nombre
    if not local.is_dir():
        return
    with _candado:
        avance(f"Subiendo a Drive todo «{nombre}»…")
        _rc("copy", local, f"{REMOTO}:{nombre}", *EXCLUIR_TODO, *RAPIDO, avance=avance, prefijo="Subiendo: ")


def faltan_en_drive(nombre):
    """Los archivos de la copia local de la sesión que no están en Drive, o que están con otro tamaño."""
    local = CACHE / nombre
    if not local.is_dir():
        return []
    d = motor.DATOS / "_trabajo"
    d.mkdir(parents=True, exist_ok=True)
    falta, distinto = d / "faltan_en_drive.txt", d / "distintos_en_drive.txt"
    for f in (falta, distinto):
        f.write_text("", encoding="utf-8")
    r = subprocess.run([str(rclone()), "check", str(local), f"{REMOTO}:{nombre}", "--one-way", "--size-only", *EXCLUIR_TODO,
                        "--missing-on-dst", str(falta), "--differ", str(distinto), "--config", str(cuenta.conf())],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    lista = [x.strip() for f in (falta, distinto) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    if r.returncode != 0 and not lista:  # no pudo comparar (sin internet…): no se da por bueno
        raise RuntimeError("No pude comparar con Drive: " + (r.stderr.strip().splitlines() or ["error desconocido"])[-1])
    return lista


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


def borrar_sesion(carpetas, avance=print):
    """Manda a la papelera de Drive las carpetas de una sesión: desde ahí se recuperan por unos 30 días."""
    for c in carpetas:
        avance(f"Mandando «{c}» a la papelera de Drive…")
        try:
            _rc("purge", f"{REMOTO}:{c}", "--drive-use-trash=true", timeout=900)
        except RuntimeError as e:
            if "not found" not in str(e).lower():  # si ya no estaba en Drive, no pasa nada
                raise
    actualizar_lista()


def crear_sesion(nombre):
    # Guiones: el guion · Clips: solo lo grabado (C1/, C2/…, CTA/ o sueltos) · Flow: imágenes y clips de apoyo
    for sub in ("Guiones", "Clips", "Flow", "Musica", "V.FINALES"):
        _rc("mkdir", f"{REMOTO}:{nombre}/{sub}")
    actualizar_lista()


# ---------- Después de elegir la carpeta del proyecto ----------
# Entrar y elegir (o crear) la carpeta es común a las tres apps (cuenta.primera, desde la pantalla de cuenta.js).
# Aquí va solo lo de esta sala: trabajar contra esa carpeta y, si es nueva, dejarle la marca neutra y _SALA.

def tras_elegir_carpeta(r):
    import marca
    asegurar_config(forzar=True)
    if r.get("nueva"):
        marca.raiz().mkdir(parents=True, exist_ok=True)
        if not (marca.raiz() / "marca.json").exists():
            marca.guardar({**marca.NEUTRA, "nombre": r.get("marca", "")})
        try:
            cuenta.rc("mkdir", f"{REMOTO}:_SALA", timeout=60)
        except RuntimeError:
            pass
    else:
        threading.Thread(target=marca.bajar, daemon=True).start()


# ---------- Equipo: quién entra a la carpeta (las cuentas de Google con permiso) ----------
# Se usa la misma llave de Google de la sala (la de rclone) para hablar con Drive: ver, invitar y quitar personas.
ROLES = {"owner": "Dueño", "organizer": "Dueño", "fileOrganizer": "Editor", "writer": "Editor", "commenter": "Comentador", "reader": "Lector"}


def _token():
    import urllib.request  # noqa: F401
    _rc("lsd", f"{REMOTO}:", "--max-depth", "1", timeout=60)  # si la llave venció, rclone la renueva y la guarda
    texto = cuenta.conf().read_text(encoding="utf-8")
    m = re.search(rf"^\[{REMOTO}\]\n(.*?)(?=^\[|\Z)", texto, re.S | re.M)
    t = m and re.search(r"^token\s*=\s*(.*)$", m.group(1), re.M)
    if not t:
        raise RuntimeError("Primero conecta la sala con Google Drive")
    return json.loads(t.group(1))["access_token"]


def _api(metodo, ruta, cuerpo=None):
    import urllib.error
    import urllib.request
    url = f"https://www.googleapis.com/drive/v3/files/{ruta}"
    req = urllib.request.Request(url, method=metodo, data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
                                 headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        if e.code == 403:
            raise RuntimeError("Tu cuenta no puede cambiar quién entra a esta carpeta: solo el dueño o un editor con permiso para compartir")
        raise RuntimeError(f"Drive no aceptó el cambio ({e.code}): {msg}"[:300])


def equipo():
    """Las personas con acceso a la carpeta del equipo (y si está abierta a cualquiera con el link)."""
    cid = estado().get("carpeta_id") or cuenta._leer(cuenta.INFO).get("carpeta_id")
    if not cid:
        raise RuntimeError("La sala no tiene carpeta del equipo todavía")
    d = _api("GET", f"{cid}/permissions?fields=permissions(id,type,role,emailAddress,displayName,photoLink,domain)&supportsAllDrives=true")
    yo = (cuenta.estado().get("email") or "").lower()
    out = []
    for p in d.get("permissions", []):
        out.append({"id": p["id"], "tipo": p.get("type"), "rol": p.get("role"), "rol_txt": ROLES.get(p.get("role"), p.get("role")),
                    "email": p.get("emailAddress", ""), "nombre": p.get("displayName", ""), "foto": p.get("photoLink", ""),
                    "dominio": p.get("domain", ""), "yo": (p.get("emailAddress") or "").lower() == yo})
    orden = {"owner": 0, "organizer": 0, "writer": 1, "fileOrganizer": 1, "commenter": 2, "reader": 3}
    out.sort(key=lambda p: (p["tipo"] == "anyone", orden.get(p["rol"], 9), p["email"]))
    return {"personas": out, "link": f"https://drive.google.com/drive/folders/{cid}", "carpeta_id": cid}


def invitar(email, rol="writer"):
    email = str(email or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Escribe un correo de Google válido")
    if rol not in ("writer", "reader", "commenter"):
        raise ValueError("Rol no válido")
    cid = equipo()["carpeta_id"]
    _api("POST", f"{cid}/permissions?sendNotificationEmail=true&supportsAllDrives=true", {"type": "user", "role": rol, "emailAddress": email})
    return equipo()


def quitar_acceso(pid):
    if not re.fullmatch(r"[\w-]+", str(pid or "")):
        raise ValueError("Permiso no válido")
    datos = equipo()
    p = next((x for x in datos["personas"] if x["id"] == pid), None)
    if not p:
        raise ValueError("Esa persona ya no tiene acceso")
    if p["rol"] in ("owner", "organizer"):
        raise ValueError("Al dueño de la carpeta no se le puede quitar")
    if p["yo"]:
        raise ValueError("No puedes quitarte a ti mismo desde la sala")
    _api("DELETE", f"{datos['carpeta_id']}/permissions/{pid}?supportsAllDrives=true")
    return equipo()


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
