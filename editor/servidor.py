"""Sala de edición: revisar videos, portadas y copies; corregir subtítulos y marcas; subir assets.

Abrir:  python servidor.py   →   http://localhost:8765   (o doble clic en abrir_sala.bat)
"""
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import actualizador
import asistente
import cuenta
import drive
import motor
import antigravity
import clips_largos
import guion_word
import importar_marca
import marca
import panel
import proyecto
import respaldos

PUERTO = int(sys.argv[sys.argv.index("--puerto") + 1]) if "--puerto" in sys.argv else int(os.environ.get("SALA_PUERTO") or 8765)  # --puerto para probar sin chocar con la sala abierta
# Desde otra PC o la tablet (por WiFi) solo se puede abrir el teleprompter, y con PIN.
ABIERTO_A_LA_RED = {"/teleprompter", "/api/teleprompter", "/entrar"}


def ip_local():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


RED_EN_MARCHA = {"si": None}  # si la sala arrancó escuchando la red (el cambio vale recién al volver a abrirla)


def red():
    r = motor.CONFIG.get("red_local", {})
    return {"activa": bool(r.get("activa")), "pin": r.get("pin", ""), "en_marcha": RED_EN_MARCHA["si"],
            "url": f"http://{ip_local()}:{PUERTO}/teleprompter" if r.get("activa") else None}
LATIDO = {"ultimo": 0.0, "adios": 0.0}  # la ventana avisa que está abierta; al cerrarla, la sala se apaga (solo con el .exe)


def al_cerrar_ventana():
    """La ventana dijo adiós: si en unos segundos no vuelve (no era una recarga), se apaga la sala.
    Si hay un trabajo en curso (armar, exportar, subir), primero lo termina."""
    import os
    if not os.environ.get("SALA_VENTANA"):
        return
    def esperar():
        marca = LATIDO["adios"]
        time.sleep(6)
        if LATIDO["ultimo"] > marca:
            return
        while TRABAJO["activo"]:
            time.sleep(2)
            if LATIDO["ultimo"] > marca:
                return
        os._exit(0)
    threading.Thread(target=esperar, daemon=True).start()


def vigilar_ventana():
    """Por si la ventana se cierra sin despedirse (se colgó, se apagó la PC a medias): sin latido por 3 minutos
    y sin trabajo en curso, la sala se apaga. Minimizada, la página igual late una vez por minuto."""
    import os
    if not os.environ.get("SALA_VENTANA"):
        return
    def revisar():
        while True:
            time.sleep(30)
            if LATIDO["ultimo"] and time.time() - LATIDO["ultimo"] > 180 and not TRABAJO["activo"]:
                os._exit(0)
    threading.Thread(target=revisar, daemon=True).start()


TRABAJO = {"activo": False, "accion": None, "mensaje": "", "error": None, "fin": None, "log": [], "historial": [], "inicio": None}
HISTORIAL = motor.DATOS / "historial_trabajos.json"  # cada trabajo: qué, de qué sesión, cuánto duró y cuántos videos


def leer_historial():
    try:
        return json.loads(HISTORIAL.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


TRABAJO["historial"] = leer_historial()[:15]
candado = threading.Lock()


def url_media(ruta):
    """Ruta local -> URL servible. Lo de la semana (Drive) va por /grab, lo del editor por /media."""
    ruta = Path(ruta)
    try:
        return "/grab/" + quote(ruta.relative_to(motor.S.grab).as_posix()) + f"?v={int(ruta.stat().st_mtime)}"
    except (ValueError, FileNotFoundError):
        pass
    try:
        return "/media/" + quote(ruta.relative_to(motor.DATOS).as_posix()) + (
            f"?v={int(ruta.stat().st_mtime)}" if ruta.exists() else "")
    except ValueError:
        return None


def estado():
    S = motor.S
    prod = motor.produccion()
    ps = []
    ajp, combos = motor.ajustes_piezas(), list(motor.combinaciones())
    for p in motor.piezas():
        d = motor.leer_subs(p["nombre"])
        proc = S.proc / f"{p['nombre']}.mp4"
        ps.append({"nombre": p["nombre"], "tipo": p["tipo"], "angulo": p.get("angulo") or "", "procesada": proc.exists(),
                   "video": url_media(proc if proc.exists() else p["original"]),
                   "lineas": d["lineas"], "marcas": motor.marcas_de(p["nombre"]),
                   "marcas_editadas": "marcas" in d, "info": motor.info_pieza(p["nombre"]),
                   # lo quitado de la pieza (desde el texto o desde «Editar video») y cuántos videos la usan
                   "quitados": motor.ajustes_de_pieza(p["nombre"], "A", ajp).get("quitar_rangos") or [],
                   "n_videos": sum(1 for c in combos if p["nombre"] in (c["hook"], c["cuerpo"], c["cta"])),
                   # sus tomas (para «Cambiar la toma» en el editor): el clip y con qué nombre entra cada una
                   "tomas": [{"rel": r, "nombre": n, "video": url_media(ruta)} for r, n, ruta in zip(p["rels"], p.get("nombres_partes") or [p["nombre"]] * len(p["rels"]), p["partes"])]})
    salidas = []
    for c in motor.combinaciones():
        f = S.salida / f"{c['archivo']}.mp4"
        por = S.portadas / f"{c['archivo']}.jpg"
        r = S.recetas / f"{c['codigo']}.json"
        info_hook = motor.info_pieza(c["hook"])
        v = c.get("version") or {}
        etiqueta = v.get("nombre") or f"Hook {motor.sin_angulo(c['hook'])[0].split('_')[-1].lstrip('H')}" + (f" · {info_hook['tipo']}" if info_hook.get("tipo") not in (None, "", "hook") else "")
        ap = motor.ajustes_portada(c["codigo"])
        av = motor.ajustes_video(c["codigo"])
        # desactualizado: se cambió algo de sus piezas (subtítulos, recortes, zoom) o sus ajustes después de armarlo
        viejo = f.exists() and motor.editado_en(c) > f.stat().st_mtime + 1
        ang = motor.sin_angulo(c["cuerpo"])[1]
        salidas.append({**c, "edicion": motor.letra_edicion(c), "angulo": ang, "etiqueta": etiqueta, "es_version": bool(v) and not v.get("propio"), "propio": bool(v.get("propio")),
                        "existe": f.exists(), "desactualizado": viejo,
                        "estado": av.get("estado") or "pendiente",
                        "ajustes_portada": {**ap, "posicion": ap.get("posicion", "centro"), "logo": ap.get("logo", True)},
                        "ajustes_video": av, "video": url_media(f) if f.exists() else None,
                        "portada": url_media(por) if por.exists() else None,
                        "titulo_portada": motor.texto_portada(c) if (S.proc / f"{c['hook']}.mp4").exists() or prod else "",
                        "receta": json.loads(r.read_text(encoding="utf-8")) if r.exists() else None,
                        "copy": motor.copy_de(c["codigo"])})
    assets = []
    if S.assets.exists():
        assets = [{"id": f.stem, "archivo": f.name, "url": url_media(f),
                   "es_video": f.suffix.lower() in motor.EXT_VID}
                  for f in sorted(S.assets.iterdir()) if f.suffix.lower() in motor.EXT_IMG | motor.EXT_VID]
    notas = motor.DATOS / "correcciones.md"
    sfx = sorted(set(motor.CONFIG.get("sfx_mapa", {})) | {d.name for d in (motor.BIBLIO_BASE / "sfx").iterdir() if d.is_dir()})
    checklist = motor.checklist()
    checklist["descartados"] = [{**c, "video": url_media(motor.S.grab / c["rel"])} for c in motor.clips_descartados()]
    for c in checklist["sin_asignar"]:
        c["video"] = url_media(motor.S.grab / c["rel"])
    for m in checklist["musica"]:
        f = motor.buscar_musica(m["id"])
        m["url"] = url_media(f) if f else None
    musica_ids = [m["id"] for m in checklist["musica"]]
    import fuentes
    fondo = S.proc / "_fondo.jpg"  # un cuadro limpio (sin portada) para previsualizar plantillas
    if not fondo.exists():
        base = next((S.proc / f"{p['nombre']}.mp4" for p in motor.piezas() if (S.proc / f"{p['nombre']}.mp4").exists()), None)
        if base:
            try:
                motor.run([motor.FFMPEG, "-hide_banner", "-y", "-ss", "1", "-i", str(base), "-frames:v", "1",
                           "-vf", "scale=540:-2", "-q:v", "4", str(fondo)])
            except Exception:
                pass
    dr = drive.estado()
    ajustes_p = motor.ajustes_piezas()
    for p in ps:
        p["editada"] = bool({k for k in ajustes_p.get(p["nombre"], {}) if k != "editado"})
    return {"version": motor.VERSION, "red": red(), "drive": dr, "drive_pendientes": drive.pendientes(), "marca": marca.resumen(),
            "antigravity": bool(antigravity.exe()), "ia": motor.CONFIG.get("ia") or "",
            "sin_carpeta": bool(cuenta._leer(cuenta.INFO).get("sin_carpeta")), "bajando": TRABAJO["activo"] and TRABAJO["accion"] == "bajar",
            "sesion_bajada": (not dr["activo"]) or all(drive.bajada(S.en_drive(m)) for m in S.miembros),
            "grupo": {"sesiones": S.miembros, "guion": S.drive} if S.es_grupo else None, "grupos": motor.grupos(), "fondo": url_media(fondo) if fondo.exists() else None, "plantillas": {k: ({**v, "escala_activa": 108} if float(v.get("escala_activa") or 108) < 50 else v) for k, v in motor.plantillas().items()}, "plantilla": motor.id_plantilla(),  # las dañadas hasta la 1.47.8 (escala 3) se ven bien
            # una predeterminada que ya se borró no cuenta: vuelve la de por defecto
            "plantilla_predeterminada": next((p for p in (motor.CONFIG.get("plantilla_activa"), motor.CONFIG["subtitulos"].get("plantilla"))
                                              if p and p in motor.plantillas()), motor.id_plantilla()),
            "plantilla_del_guion": prod.get("plantilla_sub"), "fuentes": fuentes.disponibles(),
            "portada_estilo": {"fuente": motor.CONFIG["portada"].get("fuente") or "", "mayusculas": motor.CONFIG["portada"].get("mayusculas", True)},
            "propias": list(motor.CONFIG.get("plantillas_propias", {})), "fabrica": list(motor.CONFIG.get("plantillas_sub", {})),
            "elementos": elementos(), "luts": motor.luts(),
            "claves": [motor.norm(x) for x in motor.CONFIG["palabras_clave"]],
            "semana": S.semana, "prefijo": S.prefijo, "semanas": motor.semanas_disponibles(),
            "formato": motor.formato(), "lienzo": motor.lienzo(), "ventana_defecto": motor.VENTANA_DEFECTO[motor.formato()],
            "posiciones_titulo": motor.POSICIONES_TITULO[motor.formato()], "piezas_otro_formato": motor.piezas_en_otro_formato(),
            "archivadas": motor.archivadas(), "siguiente_produccion": motor.siguiente_produccion(),
            "carpeta": str(S.grab), "hay_produccion": bool(prod),
            "en_drive": [S.en_drive(m) for m in S.miembros],  # carpetas de Drive de donde «Analizar clips» trae los clips
            "piezas": ps, "n_clips": len(motor.clips()), "salidas": salidas, "assets": assets, "checklist": checklist, "musica_ids": musica_ids, "piezas_guion": {n: i.get("texto", "") for n, i in prod.get("piezas", {}).items()},
            "calendario": motor.calendario(), "auto_descargas": bool(motor.CONFIG.get("auto_descargas", True)), "exportar": motor.ajustes_exportar(), "silencios": motor.ajustes_silencios(), "cortes_voz": {**motor.ajustes_cortes_voz(), "acelerar": motor.ajustes_ritmo()["acelerar"]},
            "ediciones": motor.ediciones(), "plan": motor.plan_sesion(), "pistas_musica": [f.name for f in motor.pistas_musica()],
            "angulos": {a: motor.nombre_angulo(a) for a in sorted({p.get("angulo") or "" for p in ps} | {""})}, "estilo_edicion": motor.estilo_edicion(), "sonidos_nivel": {"normalizar": True, "objetivo_lufs": -20, **motor.CONFIG.get("sonidos_nivel", {})}, "sfx": sfx, "estilos": motor.ESTILOS_SUB,
            "listos": str(S.listos), "panel": bool(motor.CONFIG.get("panel", {}).get("url")),
            "notas": notas.read_text(encoding="utf-8") if notas.exists() else "", "trabajo": TRABAJO}


INICIO = time.time()


def _sin_falla(f):
    try:
        f()
    except Exception as e:  # noqa: BLE001 — sin internet: se revisa la próxima vez
        print("No se pudo revisar la conexión con Drive:", e, flush=True)


def _sin_error(f):
    try:
        f(avance=lambda m: None)
    except Exception as e:  # sin internet o sin panel: se sube la próxima vez
        print("No se pudo subir:", e)


def carpeta_descargas():
    """La carpeta de Descargas de esta PC (la de verdad, aunque Windows la haya movido a otro disco u OneDrive).
    Se puede fijar otra en config.local.json → carpeta_descargas."""
    import ctypes
    c = motor.CONFIG.get("carpeta_descargas")
    if c and Path(c).is_dir():
        return Path(c)
    if os.name == "nt":
        try:
            class GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]
            descargas = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(descargas), 0, None, ctypes.byref(p)) == 0:
                ruta = Path(p.value)
                ctypes.windll.ole32.CoTaskMemFree(p)
                if ruta.is_dir():
                    return ruta
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Downloads"


def importar_descarga(nombre, aid, destino):
    """Un archivo de Descargas pasa a la sesión con el nombre que pide el guion (Musica o Flow). Devuelve (archivo, carpeta)."""
    base = carpeta_descargas().resolve()
    f = (base / nombre).resolve()
    if f.parent != base or not f.is_file() or not NOMBRE_OK.match(aid) or destino not in ("musica", "assets"):
        raise ValueError("Archivo o nombre no válido")
    ext = f.suffix.lower()
    if ext not in (motor.EXT_AUDIO if destino == "musica" else motor.EXT_IMG | motor.EXT_VID):
        raise ValueError("Ese archivo no es " + ("de audio" if destino == "musica" else "una imagen ni un clip"))
    carpeta = motor.S.musica if destino == "musica" else motor.S.assets
    carpeta.mkdir(parents=True, exist_ok=True)
    tmp = carpeta / f".{aid}{ext}.part"
    shutil.copy2(f, tmp)
    apartar_viejos(carpeta, aid)
    os.replace(tmp, carpeta / f"{aid}{ext}")
    return f"{aid}{ext}", carpeta


def apartar_viejos(carpeta, nombre):
    """La versión anterior de un archivo (aunque tenga otra extensión) pasa a _REEMPLAZADOS/ en vez de borrarse,
    en esta PC y en Drive: si no, la bajada siguiente traía de vuelta la vieja y a veces salía esa en el video."""
    viejos = [v for v in carpeta.glob(f"{nombre}.*") if v.is_file()]
    if not viejos:
        return []
    guardar = carpeta / "_REEMPLAZADOS"
    guardar.mkdir(exist_ok=True)
    sello = time.strftime("%Y%m%d-%H%M%S")
    for v in viejos:
        rel = None
        try:
            rel = v.relative_to(motor.S.principal).as_posix()
        except ValueError:
            pass
        try:
            os.replace(v, guardar / f"{sello}_{v.name}")
        except OSError:
            v.unlink(missing_ok=True)
        if rel:
            drive.quitar(motor.S.drive, rel)
    return [v.name for v in viejos]


FLOW_MUSIC_AUDIO = re.compile(r"https://storage\.googleapis\.com/producer-app-public/clips/[\w-]+\.(m4a|mp3|wav)")


def bajar_pista(aid, url):
    """El audio de una canción de Flow Music es público: se baja a Descargas con el id como nombre. Solo de ese dominio."""
    m = FLOW_MUSIC_AUDIO.fullmatch(url)
    if not m or not NOMBRE_OK.match(aid):
        raise ValueError("no es una URL de audio de Flow Music")
    import urllib.request
    destino = carpeta_descargas() / f"{aid}.{m.group(1)}"
    n = 2
    while destino.exists():  # no pisa un archivo que ya estaba en Descargas con ese nombre
        destino = carpeta_descargas() / f"{aid} ({n}).{m.group(1)}"
        n += 1
    with urllib.request.urlopen(url, timeout=120) as r, open(destino, "wb") as f:
        shutil.copyfileobj(r, f)
    if destino.stat().st_size < 20_000:
        destino.unlink()
        raise ValueError("el audio llegó vacío")
    return destino.name


def abrir_chrome(avance):
    """Claude in Chrome necesita Chrome abierto: si está cerrado, se abre (con el último perfil usado) y se le da tiempo
    a la extensión para conectarse."""
    if os.name != "nt":
        return
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"], capture_output=True, text=True, errors="replace")
    if "chrome.exe" in r.stdout:
        return
    rutas = [Path(os.environ.get(v, "")) / "Google" / "Chrome" / "Application" / "chrome.exe"
             for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    chrome = next((p for p in rutas if p.is_file()), None)
    if not chrome:
        raise RuntimeError("No encuentro Google Chrome en esta PC: hace falta Chrome con la extensión de Claude")
    avance("Abriendo Chrome…")
    subprocess.Popen([str(chrome)])
    time.sleep(20)


def generar_en_flow(avance):
    """«Generar en Flow»: si el guion no trae música, Claude escribe sus prompts; después genera en Flow y Flow Music
    (en el Chrome de esta PC) lo que falta, lo guarda en Flow/ y Musica/ con su id y lo sube a Drive."""
    if not motor.produccion().get("musica"):
        avance("El guion no tiene música: Claude escribe sus prompts…")
        asistente.generar_prompts_musica(avance=avance)
        motor._prod_cache = None
    ch = motor.checklist()
    imagenes = [{"id": a["id"], "tipo": a.get("tipo", "imagen"), **{k: a.get("flow", {}).get(k, "") for k in ("prompt", "plan_b", "formato", "que_muestra")}}
                for a in ch["assets"] if not a["listo"]]
    musica = [{"id": m["id"], "prompt": (m.get("prompt") or {}).get("prompt", ""), "evitar": (m.get("prompt") or {}).get("evitar", "")}
              for m in ch["musica"] if not m["listo"]]
    if not imagenes and not musica:
        return {"hechos": [], "pendientes": [], "mensaje": "No falta nada: todas las imágenes y la música ya están."}
    avance(f"Falta generar {len(imagenes)} imágenes o clips y {len(musica)} pistas")
    abrir_chrome(avance)
    inicio = time.time() - 5
    r = asistente.generar_en_flow(imagenes, musica, carpeta_descargas(), avance=avance)
    destinos = {**{x["id"]: "assets" for x in imagenes}, **{x["id"]: "musica" for x in musica}}
    hechos, pendientes = [], [p for p in (r.get("pendientes") or []) if p.get("id") not in (r.get("urls") or {})]
    archivos = dict(r.get("archivos") or {})
    for aid, url in (r.get("urls") or {}).items():  # la música se baja desde aquí: Chrome frena varias descargas seguidas
        if destinos.get(aid) != "musica":
            continue
        try:
            archivos[aid] = bajar_pista(aid, str(url))
        except (ValueError, OSError) as e:
            pendientes.append({"id": aid, "motivo": f"no pude bajar su audio ({e})"})
    for aid, nombre in archivos.items():
        if aid not in destinos:
            continue
        f = carpeta_descargas() / str(nombre)
        if not f.is_file() or f.stat().st_mtime < inicio:  # solo lo que se bajó ahora, nunca un archivo viejo
            pendientes.append({"id": aid, "motivo": f"no encontré «{nombre}» recién bajado en Descargas"})
            continue
        try:
            importar_descarga(f.name, aid, destinos[aid])
            apartar_descarga(f.name)  # ya está en la sesión (y sube a Drive): no queda suelto en Descargas
            hechos.append(aid)
        except ValueError as e:
            pendientes.append({"id": aid, "motivo": str(e)})
    if hechos and drive.estado()["activo"]:
        drive.subir(motor.S.drive, sorted({motor.S.musica.name if destinos[a] == "musica" else motor.S.assets.name for a in hechos}), avance=avance)
    return {"hechos": hechos, "pendientes": pendientes}


USADOS = "Usados por la sala"  # subcarpeta de Descargas donde quedan los archivos que ya pasaron a una sesión


def id_de_archivo(nombre, ids):
    """El código del guion que lleva el nombre de un archivo bajado («1110_C1_IMG2 (1).png» → 1110_C1_IMG2), o None.
    Gana el código más largo que aparece entero (así C1_IMG1 no se confunde con C1_IMG10)."""
    base = re.sub(r"\s*\(\d+\)$", "", Path(nombre).stem).strip().lower()
    mejor = None
    for i in ids:
        il = str(i).lower()
        if base == il:
            return i
        if re.search(rf"(^|[^a-z0-9]){re.escape(il)}($|[^a-z0-9])", base) and (not mejor or len(i) > len(mejor)):
            mejor = i
    return mejor


def _tipo_descarga(f):
    ext = f.suffix.lower()
    return "musica" if ext in motor.EXT_AUDIO else "imagen" if ext in motor.EXT_IMG else "video" if ext in motor.EXT_VID else None


def codigos_armados(cumple=lambda c: True):
    """Los videos que ya tienen su .mp4 armado (y cumplen la condición): solo esos quedan «por rearmar» al cambiar
    un estilo o una plantilla. Antes se devolvían todas las combinaciones aunque no hubiera ninguno armado."""
    return [c["codigo"] for c in motor.combinaciones() if cumple(c) and (motor.S.salida / f"{c['archivo']}.mp4").exists()]


# Faltantes › «¿Cuántas imágenes?»: los mismos topes que el formulario (por cuerpo 12, clips 4, por hook 4)
TOPES_APOYO = (("por_cuerpo", 12), ("clips", 4), ("por_hook", 4))


def descargas_recientes(dias=7, todos=False):
    """Música, imágenes y clips bajados en los últimos días (lo de Flow, Flow Music o Suno), lo más nuevo primero.
    Solo los que se llaman como algo del guion de la sesión; los demás se cuentan (y se ven con todos=True)."""
    base = carpeta_descargas()
    ck = motor.checklist()
    ids = {"musica": [m["id"] for m in ck.get("musica", [])], "assets": [a["id"] for a in ck.get("assets", [])]}
    puestos = {x["id"] for x in ck.get("musica", []) + ck.get("assets", []) if x.get("listo")}  # ya están en la sesión
    archivos, otros = [], 0
    if base.is_dir():
        limite = time.time() - dias * 86400
        for f in base.iterdir():
            tipo = _tipo_descarga(f)
            if not tipo or not f.is_file():
                continue
            st = f.stat()
            if st.st_mtime < limite or st.st_size > 600 * 1024 * 1024:
                continue
            aid = id_de_archivo(f.name, ids["musica" if tipo == "musica" else "assets"])
            if aid in puestos:  # ese ya está puesto en la sesión: no se vuelve a ofrecer
                continue
            if not aid:
                otros += 1
                if not todos:
                    continue
            archivos.append({"nombre": f.name, "tipo": tipo, "mb": round(st.st_size / 1048576, 1), "fecha": st.st_mtime, "id": aid,
                             "url": "/descargas/" + quote(f.name) + f"?v={int(st.st_mtime)}"})
    archivos.sort(key=lambda x: (not x["id"], -x["fecha"]))
    # se muestran hasta 60 (los que se llaman como algo del guion primero): «total» dice cuántos había, para que cuadre
    return {"carpeta": str(base), "dias": dias, "archivos": archivos[:60], "total": len(archivos), "otros": otros,
            "auto": bool(motor.CONFIG.get("auto_descargas", True))}


def apartar_descarga(nombre):
    """El archivo de Descargas que ya pasó a la sesión se guarda en «Descargas/Usados por la sala» (no se borra):
    así Descargas queda limpio y no se vuelve a ofrecer."""
    base = carpeta_descargas()
    f = base / nombre
    if not f.is_file():
        return
    d = base / USADOS
    d.mkdir(exist_ok=True)
    destino, n = d / f.name, 2
    while destino.exists():
        destino = d / f"{f.stem} ({n}){f.suffix}"
        n += 1
    try:
        shutil.move(str(f), str(destino))
    except OSError:  # abierto en otro programa: se queda donde está
        pass


AUTO_DESC = []  # lo que llegó solo de Descargas: [{t, archivo, id, codigos}]
CHOCAN_CON_DESCARGAS = {"bajar", "reiniciar", "terminar", "renombrar", "ordenar_todo", "conectar"}


def traer_descargas(dias=3):
    """Pasa a la sesión los archivos de Descargas que se llaman como una imagen, clip o pista que FALTA (nunca reemplaza
    lo que ya está). Devuelve lo que trajo."""
    base = carpeta_descargas()
    if not base.is_dir():
        return []
    ck = motor.checklist()
    faltan = {"musica": [m["id"] for m in ck.get("musica", []) if not m.get("listo")],
              "assets": [a["id"] for a in ck.get("assets", []) if not a.get("listo")]}
    if not faltan["musica"] and not faltan["assets"]:
        return []
    traidos, limite = [], time.time() - dias * 86400
    for f in sorted(base.iterdir(), key=lambda x: -x.stat().st_mtime if x.is_file() else 0):
        tipo = _tipo_descarga(f)
        if not tipo or not f.is_file() or f.name.startswith(".") or f.suffix.lower() in (".crdownload", ".part", ".tmp"):
            continue
        st = f.stat()
        if st.st_mtime < limite or time.time() - st.st_mtime < 3:  # recién llegando: se espera a que termine de bajar
            continue
        destino = "musica" if tipo == "musica" else "assets"
        aid = id_de_archivo(f.name, faltan[destino])
        if not aid:
            continue
        try:
            archivo, carpeta = importar_descarga(f.name, aid, destino)
        except (ValueError, OSError):
            continue
        apartar_descarga(f.name)
        faltan[destino].remove(aid)
        drive.subir_en_segundo_plano(motor.S.drive, [carpeta.relative_to(motor.S.principal).as_posix()])
        traidos.append({"t": time.time(), "archivo": archivo, "id": aid, "codigos": motor.videos_que_usan(aid, destino)})
    return traidos


def traer_de_antigravity():
    """Pasa a la sesión la música y los prompts que escribió Gemini en Antigravity, apenas estén listos."""
    traidos = []
    m = antigravity.traer_musica()
    if m:
        traidos.append({"t": time.time(), "archivo": "la música del guion (" + ", ".join(m["pistas"]) + ")", "id": "", "codigos": []})
    try:
        p = antigravity.traer_prompts()
        traidos.append({"t": time.time(), "archivo": f"{p['nuevos']} prompts de Flow para el guion", "id": "", "codigos": []})
    except ValueError:
        pass
    if traidos and drive.estado()["activo"]:
        drive.subir_en_segundo_plano(motor.S.drive, ["Guion"])
    return traidos


def vigilar_descargas():
    """Mientras la sala está abierta, lo que se baja de Flow, Flow Music o Suno con el nombre del guion pasa solo a la
    sesión (cada 20 s). Se apaga en Faltantes («Traer solos de Descargas»). Solo en la sala de verdad, no en una de prueba."""
    def revisar():
        while True:
            time.sleep(20)
            # mientras analiza, prepara o arma, las imágenes y la música igual entran (no chocan con los clips); solo se
            # espera en lo que mueve o borra la carpeta de la sesión
            if not motor.CONFIG.get("auto_descargas", True) or (TRABAJO["activo"] and TRABAJO.get("accion") in CHOCAN_CON_DESCARGAS):
                continue
            r = []
            try:  # lo que Gemini dejó en Antigravity: la música del guion y los prompts de Flow pasan solos
                r += traer_de_antigravity()
            except Exception:  # noqa: BLE001
                pass
            try:
                r += traer_descargas()
            except Exception:  # noqa: BLE001 — nunca debe tumbar la sala
                pass
            if r:
                AUTO_DESC.extend(r)
                del AUTO_DESC[:-50]
    threading.Thread(target=revisar, daemon=True).start()


def subir_asignaciones():
    for m in motor.S.miembros:
        drive.subir_en_segundo_plano(motor.S.en_drive(m), ["asignaciones.json"])


def elementos():
    """Imágenes, logos y GIF para las plantillas: los que subió el equipo y los de la marca."""
    out = []
    for base, propio in ((motor.ELEMENTOS, True), (motor.BIBLIO_BASE / "marca", False)):
        if base.is_dir():
            for f in sorted(base.iterdir()):
                if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    out.append({"archivo": f.name, "propio": propio, "url": url_media(f) if propio else "/marca/" + quote(f.name)})
    return out


def leer_guion_de_carpeta():
    """Lee el guion nuevo de la carpeta Guion/Guiones y, si lo leyó, sube a Drive y al panel el guion de la sala.
    Devuelve {archivo, leido: resumen} o {archivo, error} o None."""
    r = motor.importar_guion_de_carpeta()
    if r and r.get("resumen"):
        drive.subir_en_segundo_plano(motor.S.drive, ["Guion"])
        if motor.CONFIG.get("panel", {}).get("token"):
            threading.Thread(target=lambda: _sin_error(panel.subir_guion), daemon=True).start()
        return {"archivo": r["archivo"], "leido": r["resumen"]}
    return r


def exportar_a(extra, hacer, avance):
    """Exporta en una carpeta «<fecha> <sesión>» dentro de la carpeta que eligió el usuario (o de V.FINALES si no eligió)
    y, si dijo que sí al Drive, la copia también a V.FINALES y la sube. hacer(raiz) exporta y devuelve los hechos."""
    elegida = (extra.get("carpeta") or "").strip()
    ok = revisar_destino(elegida)
    if not ok["ok"]:
        raise RuntimeError(ok["motivo"])
    base = Path(elegida) if elegida else motor.S.listos
    # «Crear otra carpeta»: un segundo exporte del mismo día no se mezcla con el primero
    nombre = nombre_libre(base, ok["nombre"]) if extra.get("nueva") else ok["nombre"]
    raiz = base / nombre
    if not extra.get("nueva") and extra.get("reemplazar"):  # «Reemplazar lo de hoy»: sin restos del exporte anterior
        vaciar_exporte(raiz, avance)
        if extra.get("drive", not elegida) and raiz != motor.S.listos / nombre:
            vaciar_exporte(motor.S.listos / nombre, avance)
    hechos = hacer(raiz)
    if extra.get("drive", not elegida):
        en_drive = motor.S.listos / nombre
        if raiz != en_drive and raiz.exists():
            avance("Copiando el exporte a V.FINALES para el Drive")
            shutil.copytree(raiz, en_drive, dirs_exist_ok=True)
        if drive.estado()["activo"]:
            try:
                drive.subir(motor.S.drive, [f"{motor.S.listos.name}/{nombre}"], avance=avance)
            except Exception as e:  # noqa: BLE001 — los videos ya están: que se vea qué faltó y se pueda reintentar
                drive._anotar_pendiente(motor.S.drive, [f"{motor.S.listos.name}/{nombre}"], e)
                avance(f"Los videos quedaron en {raiz}, pero no se pudieron subir al Drive. Toca «Reintentar» en el aviso de Drive.")
        else:
            avance("La sala no está conectada a Drive: los videos quedaron en V.FINALES de la carpeta de trabajo.")
    if elegida and hechos and sys.platform == "win32":
        os.startfile(str(raiz))  # abre la carpeta del exporte en el explorador
    return hechos, raiz


def elegir_carpeta(inicial=""):
    """Abre el cuadro de Windows para elegir una carpeta. Devuelve la ruta, o "" si se canceló."""
    if sys.platform != "win32":
        import tkinter
        from tkinter import filedialog
        tk = tkinter.Tk()
        tk.withdraw()
        tk.attributes("-topmost", True)
        r = filedialog.askdirectory(initialdir=inicial or None, title="¿Dónde exportas los videos?")
        tk.destroy()
        return r or ""
    # El servidor corre aparte de la ventana de la sala: un cuadro sin dueño visible se abría detrás de ella.
    # Se le da un dueño visible (una ventanita invisible en el centro, siempre encima) y se la trae adelante.
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true; $f.ShowInTaskbar = $true; $f.Text = 'Estudio Suplevet';"
          "$f.StartPosition = 'CenterScreen'; $f.Width = 1; $f.Height = 1; $f.Opacity = 0; $f.FormBorderStyle = 'None';"
          "$f.Show(); $f.Activate(); $f.BringToFront();"
          "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
          "$d.Description = 'Elige la carpeta donde se exportan los videos (dentro se crea una carpeta con la fecha y la sesión)';"
          "$d.ShowNewFolderButton = $true; if ($env:SALA_CARPETA -and (Test-Path $env:SALA_CARPETA)) { $d.SelectedPath = $env:SALA_CARPETA };"
          "$r = $d.ShowDialog($f); $f.Close();"
          "if ($r -eq 'OK') { [Console]::OutputEncoding = [Text.Encoding]::UTF8; Write-Output $d.SelectedPath }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, timeout=300,
                           env={**os.environ, "SALA_CARPETA": inicial or ""}, creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        raise RuntimeError("El cuadro para elegir la carpeta no respondió en 5 minutos. Pega la ruta de la carpeta en el campo de abajo.")
    return r.stdout.decode("utf-8", "replace").strip()


def revisar_destino(carpeta):
    """¿Se puede exportar en esa carpeta? Devuelve {ok, motivo, existe_hoy, nombre}: existe_hoy dice si ya hay un
    exporte de hoy de esta sesión ahí (para preguntar si se reemplaza o se crea otra carpeta)."""
    nombre = motor.carpeta_de_exporte()
    if not carpeta:
        base = motor.S.listos
    else:
        base = Path(carpeta)
        if not base.is_dir():
            return {"ok": False, "motivo": "Esa carpeta ya no está (¿un USB que se desconectó?). Elige otra.", "nombre": nombre}
        try:  # se prueba escribir de verdad: una carpeta del sistema existe pero no deja guardar
            prueba = base / f".sala_prueba_{os.getpid()}"
            prueba.write_bytes(b"")
            prueba.unlink()
        except OSError:
            return {"ok": False, "motivo": "En esa carpeta no se puede guardar (no hay permiso). Elige otra, por ejemplo una en Documentos o en el Escritorio.", "nombre": nombre}
    return {"ok": True, "existe_hoy": (base / nombre).exists(), "nombre": nombre}


EXT_EXPORTE = {".mp4", ".jpg", ".txt", ".wav", ".srt", ".png", ".webp", ".jpeg", ".gif"}


def vaciar_exporte(carpeta, avance=print):
    """Al elegir «Reemplazar lo de hoy»: la carpeta del exporte anterior se vacía para que no queden videos viejos
    mezclados. Si tiene algo que no puso la sala (otros archivos), no se borra: se le cambia el nombre a «(anterior)»."""
    if not carpeta.is_dir():
        return
    archivos = [f for f in carpeta.rglob("*") if f.is_file()]
    if all(f.suffix.lower() in EXT_EXPORTE or f.name.startswith(".") for f in archivos):
        shutil.rmtree(carpeta)
    else:
        aparte = carpeta.with_name(f"{carpeta.name} (anterior {time.strftime('%H.%M')})")
        carpeta.rename(aparte)
        avance(f"La carpeta de hoy tenía otros archivos: la dejé como «{aparte.name}» y exporté en una nueva")


def nombre_libre(base, nombre):
    """«23-10-2026 P05 …», o «… (2)», «… (3)» si ya existe."""
    if not (base / nombre).exists():
        return nombre
    n = 2
    while (base / f"{nombre} ({n})").exists():
        n += 1
    return f"{nombre} ({n})"


# Lo que se guardó mientras la sala trabajaba y hay que rearmar después: se corre solo cuando termina el trabajo
# actual (antes daba «Ya hay un trabajo en curso» aunque los cambios sí se habían guardado, y al reintentar se
# duplicaban las versiones).
COLA = []


def poner_en_cola(accion, pieza=None, extra=None):
    for x in COLA:  # dos rearmados seguidos se juntan en uno
        if x[0] == accion == "armar_sel":
            x[2]["codigos"] = sorted(set(x[2]["codigos"]) | set((extra or {}).get("codigos") or []))
            return
        if x[0] == accion and x[1] == pieza and accion in ("uno", "procesar"):
            return
    COLA.append((accion, pieza, dict(extra or {})))


def abrir_sesion(sem):
    """Abre (y si no existe, crea) una sesión: la de la lista de arriba, una nueva o la de un link."""
    sem = " ".join(str(sem).split()).replace("/", "／")  # Drive permite "11/10 SESIÓN"; en disco se guarda con la barra ancha
    if TRABAJO["activo"] and sem != motor.S.semana:
        # el trabajo seguiría con las carpetas de la otra sesión: los videos quedaban en la sesión equivocada
        raise RuntimeError("La sala está trabajando (☰). Espera a que termine para cambiar de sesión.")
    if not motor.nombre_sesion_valido(sem):
        raise ValueError('Nombre de sesión no válido (sin / \\ : * ? " < > |)')
    nueva = motor.S.base / sem
    es_grupo = sem in motor.grupos()
    try:  # lo último de la sesión que se deja queda guardado
        respaldos.hacer(auto=True)
    except Exception as e:
        print("No se pudo guardar la copia de seguridad:", e)
    recien = False
    if not es_grupo and drive.estado()["activo"] and sem not in drive.sesiones_en_drive():
        try:  # la lista guardada puede estar vieja: antes de crear una sesión nueva en Drive se mira de nuevo
            drive.actualizar_lista()
        except Exception as e:  # noqa: BLE001
            print("No se pudo actualizar la lista de Drive:", e)
    if not es_grupo and drive.estado()["activo"] and sem not in drive.sesiones_en_drive():
        drive.crear_sesion(sem)
        recien = True
    if not es_grupo and not nueva.exists() and (recien or not (drive.estado()["activo"] and sem in drive.sesiones_en_drive())):
        for sub in ("Guion", "Clips/Hooks", "Clips/Cuerpo", "Clips/CTA", "Flow", "V.FINALES"):
            (nueva / sub).mkdir(parents=True, exist_ok=True)
    if recien:  # recién creada está vacía: no hace falta bajarla (eso trababa «Escribir guion con Claude»)
        (drive.CACHE / sem).mkdir(parents=True, exist_ok=True)
        (drive.CACHE / sem / ".bajada").write_text(str(time.time()), encoding="utf-8")
    motor.usar_semana(sem)
    motor.guardar_config({"semana": sem})
    if drive.estado()["activo"] and not all(drive.bajada_completa(motor.S.en_drive(m)) for m in motor.S.miembros) and not TRABAJO["activo"]:
        if sem in motor.archivadas():  # una sesión terminada que se vuelve a abrir: sale del archivo y se baja de nuevo
            motor.guardar_archivadas([x for x in motor.archivadas() if x != sem])
            threading.Thread(target=lambda: _sin_falla(drive.subir_archivadas), daemon=True).start()
        lanzar("bajar")  # se baja sola la primera vez que se abre


def sesion_de_link(link):
    """La sesión de un link de carpeta de Drive (de EDICIÓN DE VIDEOS) o de una carpeta de esta PC."""
    link = link.strip().strip('"').strip()
    if not link:
        raise ValueError("Pega el link de la carpeta (en Drive: clic derecho → Compartir → Copiar enlace)")
    if re.match(r"^[A-Za-z]:[\\/]|^\\\\", link):
        return sesion_de_carpeta_local(Path(link))
    if not drive.estado()["activo"]:
        raise RuntimeError("Para abrir una carpeta de Drive con su link, conecta primero la sala con Google Drive (Configuración → Google Drive).")
    fid = drive.carpeta_id(link)
    if fid == drive.estado().get("carpeta_id"):
        raise ValueError("Ese es el link de la carpeta del equipo (EDICIÓN DE VIDEOS). Pega el link de una sesión de adentro, o elígela en la lista de arriba.")
    carpetas = json.loads(drive._rc("lsjson", f"{drive.REMOTO}:", "--dirs-only", "--max-depth", "2", timeout=120) or "[]")
    d = next((x for x in carpetas if x.get("ID") == fid), None)
    if not d:
        raise ValueError("Esa carpeta no está dentro de EDICIÓN DE VIDEOS (o tu cuenta no la ve). Muévela dentro de la carpeta del equipo en Drive y vuelve a intentar.")
    partes = d["Path"].split("/")
    drive.actualizar_lista()
    # un día de grabación dentro de una producción («P02 …/11／10 SESIÓN») se trabaja desde la producción
    return partes[0]


def sesion_de_carpeta_local(p):
    """Una carpeta de esta PC: si está en la carpeta de trabajo es esa sesión; si está en otro lado se enlaza ahí
    (un acceso de Windows: los archivos no se mueven ni se copian)."""
    if not p.is_dir():
        raise ValueError(f"No encuentro la carpeta {p}")
    if drive.estado()["activo"]:
        raise RuntimeError("La sala está conectada a Drive: abre la carpeta con su link de Drive, o cópiala dentro de EDICIÓN DE VIDEOS.")
    base = motor.S.base
    try:
        return p.resolve().relative_to(base.resolve()).parts[0]
    except ValueError:
        pass
    nombre = p.name
    if not motor.nombre_sesion_valido(nombre):
        raise ValueError("El nombre de esa carpeta no sirve como sesión: cámbiale el nombre (sin / \\ : * ? \" < > |)")
    enlace = base / nombre
    if enlace.exists():
        if enlace.resolve() == p.resolve():
            return nombre
        raise ValueError(f"Ya hay una sesión «{nombre}» en la carpeta de trabajo: cámbiale el nombre a la carpeta y vuelve a intentar.")
    base.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(enlace), str(p)], capture_output=True, text=True, creationflags=0x08000000)
    if r.returncode != 0:
        raise RuntimeError("Windows no dejó enlazar la carpeta: " + (r.stderr or r.stdout).strip()[:200])
    return nombre


def maximizar_ventana():
    """La sala se abre en pantalla completa (maximizada). La ventana la crea el .exe a 1440×920; se maximiza desde aquí
    con Windows para no tener que bajar el instalador completo. Lo pide la página cada vez que se abre una ventana
    (antes era solo la primera vez que arrancaba la sala: si ya estaba corriendo, la ventana nueva quedaba chica).
    Solo ventanas de esta PC cuyo título empieza con «Sala de edición» (la propia o la de Edge en modo aplicación)."""
    if os.name != "nt" or motor.CONFIG.get("ventana_maximizada") is False:
        return

    def tarea():
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        encontradas = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cada(hwnd, _):
            n = user32.GetWindowTextLengthW(hwnd)
            if n and user32.IsWindowVisible(hwnd):
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, b, n + 1)
                if b.value.startswith("Sala de edición"):
                    encontradas.append(hwnd)
            return True
        for _ in range(10):  # la ventana puede tardar un poco en aparecer
            encontradas.clear()
            user32.EnumWindows(cada, 0)
            if encontradas:
                for h in encontradas:
                    if not user32.IsZoomed(h) and not user32.IsIconic(h):
                        user32.ShowWindow(h, 3)  # SW_MAXIMIZE
                return
            time.sleep(0.5)
    threading.Thread(target=lambda: _sin_falla(tarea), daemon=True).start()


def sesion_en_pc():
    """Lo que la sesión abierta ocupa en esta PC: (carpetas de Drive que la forman, carpetas locales pesadas, bytes).
    Pesado = la copia de Drive (clips, Flow, música, guion, V.FINALES), las piezas preparadas, los videos armados y
    las transcripciones. Los subtítulos, las recetas y las copias de seguridad (livianos) se quedan."""
    S = motor.S
    raices = [S.carpeta_prod] if S.carpeta_prod else list(dict.fromkeys([S.drive, *[S.en_drive(m) for m in S.miembros]]))
    raices = [r for r in raices if r and not any(o != r and r.startswith(o + "/") for o in raices)]  # sin repetir lo de adentro
    locales = [drive.CACHE / r for r in raices] + [S.proc, S.salida, S.transcripciones]
    peso = sum(f.stat().st_size for d in locales if d.exists() for f in d.rglob("*") if f.is_file())
    return raices, locales, peso


def sin_exportar():
    """Los videos armados que no están en V.FINALES (si se borra la sesión de la PC, habría que volver a armarlos)."""
    S = motor.S
    hechos = {f.name for f in S.listos.rglob("*.mp4")} if S.listos.exists() else set()
    return sorted(f.stem for f in S.salida.glob("*.mp4") if f.name not in hechos) if S.salida.exists() else []


def lanzar(accion, pieza=None, extra=None, en_cola=False):
    """Corre procesar/armar/exportar en segundo plano para que la app no se congele. Con en_cola, si ya hay un
    trabajo corriendo, queda en la cola y se corre solo al terminar. Devuelve True si quedó en la cola."""
    if TRABAJO["activo"]:
        if en_cola:
            poner_en_cola(accion, pieza, extra)
            TRABAJO["cola"] = [x[0] for x in COLA]
            return True
        raise RuntimeError("Ya hay un trabajo en curso. Espera a que termine.")

    def avance(msg):
        motor.revisar_cancelado()
        TRABAJO["mensaje"] = msg
        TRABAJO["log"] = (TRABAJO["log"] + [msg])[-200:]
        TRABAJO["log_t"] = (TRABAJO.get("log_t", []) + [time.time()])[-200:]
        # «(136/186)»: cuántos van de cuántos, y desde cuándo, para calcular cuánto falta
        if m := re.search(r"\((\d+)/(\d+)\)", msg):
            n, total = int(m[1]), int(m[2])
            av = TRABAJO.get("avance") or {}
            if av.get("total") != total or n < av.get("n", 0):
                av = {"n0": n, "t0": time.time(), "total": total}
            av.update({"n": n, "t": time.time()})
            TRABAJO["avance"] = av

    def renombrar_todo():
        plan = motor.plan_renombrar()["cambios"]
        visto = (extra or {}).get("plan")
        if visto:  # se renombra solo lo que la persona vio en la lista (y que sigue siendo válido ahora)
            ok = {(c.get("viejo"), c.get("nuevo")) for c in visto if isinstance(c, dict)}
            plan = [c for c in plan if (c["viejo"], c["nuevo"]) in ok]
        hechos = 0
        for n, cb in enumerate(plan, 1):
            motor.revisar_cancelado()
            avance(f"Renombrando {Path(cb['viejo']).name} → {cb['nuevo']} ({n}/{len(plan)})")
            if drive.estado()["activo"]:
                try:
                    drive.mover(motor.S.en_drive(cb["sesion"]), cb["viejo"], cb["nuevo"])  # si Drive no deja, no se toca nada local
                except RuntimeError as e:
                    avance(f"Se saltó {Path(cb['viejo']).name}: {e}")
                    continue
            motor.aplicar_renombre(cb)
            hechos += 1
        if drive.estado()["activo"]:
            for m in {cb["sesion"] for cb in plan}:
                drive.subir(motor.S.en_drive(m), ["asignaciones.json"], avance=lambda x: None)
        return hechos

    def guion_de_carpeta():
        """El guion que el equipo dejó en Guion/Guiones: se lee antes de comparar los clips con el guion."""
        r = leer_guion_de_carpeta()
        if r and r.get("leido"):
            s = r["leido"]
            avance(f"Leí el guion «{r['archivo']}»: {s['cuerpos']} cuerpos, {s['hooks']} hooks y {s['ctas']} CTA")
            if accion not in ("ordenar_todo", "producir", "escuchar"):  # esas reasignan después de escuchar
                reasignar_y_preparar(preparar=False)
        elif r and r.get("error"):
            avance(f"Ojo: no pude leer el guion «{r['archivo']}»: {r['error']}")

    def reasignar_y_preparar(preparar=True):
        """Vuelve a comparar todos los clips con el guion de hoy (motor.reasignar_por_guion) y prepara lo que cambió."""
        avance("Comparando todos los clips con el guion (lo asignado a mano no se toca)…")
        r = motor.reasignar_por_guion()
        if r["cambiados"]:
            subir_asignaciones()
        avance(f"Clips revisados con el guion: {r['asignados']} con pieza, {r['cambiados']} cambiaron"
               + (f", {r['sin_pieza']} sin pieza (otras tomas o frases que no están en el guion)" if r["sin_pieza"] else ""))
        TRABAJO["resultado"] = {k: v for k, v in r.items() if k != "nuevo"}
        if preparar and r["cambiados"]:
            motor.procesar(avance=avance)
            avance("Listo: las piezas que cambiaron ya están preparadas. Toca «Armar todo» para rearmar los videos.")

    def guion_si_falta():
        """Sin guion: lo arma con lo que se dijo en cada pieza (y lo sube a Drive y al panel)."""
        n = motor.guion_desde_grabado()
        if n:
            avance(f"Guion sacado de lo grabado: {n} piezas al día con lo que se dijo (puedes subir el de verdad cuando quieras)")
            drive.subir_en_segundo_plano(motor.S.drive, ["Guion"])
            if motor.CONFIG.get("panel", {}).get("token"):
                threading.Thread(target=lambda: _sin_error(panel.subir_guion), daemon=True).start()

    def correr():
        motor.HILO_TRABAJO["id"] = threading.get_ident()
        try:
            with candado:
                if accion in ("todo", "procesar", "guion_grabado", "ordenar_todo", "producir", "escuchar"):
                    guion_de_carpeta()
                if accion in ("todo", "procesar", "guion_grabado"):
                    motor.procesar(avance=avance)
                    guion_si_falta()
                if accion == "guion_grabado" and not motor.guion_desde_grabado() and motor._guion_propio().get("origen") != "grabado":
                    avance("Esta sesión ya tiene guion: no lo cambié")
                if accion in ("todo", "armar"):
                    hechos_a = motor.armar(pieza, avance=avance)
                    avance(f"Listo: {len(hechos_a)} video{'s' if len(hechos_a) != 1 else ''} armado{'s' if len(hechos_a) != 1 else ''}")
                if accion == "armar_sel":
                    hechos_a = motor.armar(avance=avance, codigos=extra["codigos"])
                    avance(f"Listo: {len(hechos_a)} video{'s' if len(hechos_a) != 1 else ''} armado{'s' if len(hechos_a) != 1 else ''}")
                if accion == "reasignar":  # el guion cambió (o se pidió a mano): todos los clips de nuevo con el guion
                    reasignar_y_preparar()
                if accion == "ordenar_todo":  # analizar, asignar lo seguro y renombrar, todo de una vez
                    motor.escuchar_clips(avance=avance)
                    n = motor.aceptar_sugerencias(0.5)
                    avance(f"{n} clips identificados solos")
                    subir_asignaciones()
                if accion in ("renombrar", "ordenar_todo"):
                    hechos = renombrar_todo()
                    avance(f"Listo: {hechos} clips con el nombre de su pieza")
                if accion == "producir":  # «Producir todo»: de los clips subidos a los videos exportados, en un solo paso
                    op, t0, res = extra or {}, time.time(), {}
                    avance("1/6 · Escuchando los clips nuevos y comparándolos con el guion")
                    motor.escuchar_clips(avance=avance)
                    res["identificados"] = motor.aceptar_sugerencias(0.5)
                    subir_asignaciones()
                    if op.get("renombrar", True):
                        avance("2/6 · Renombrando los clips con el código de su pieza")
                        res["renombrados"] = renombrar_todo()
                    avance("3/6 · Preparando los clips (recortar, volumen, transcribir)")
                    motor.procesar(avance=avance)
                    guion_si_falta()
                    avance("4/6 · Armando los videos")
                    res["armados"] = len(motor.armar(avance=avance))
                    avance("5/6 · Revisando la calidad")
                    calidad = motor.revisar_calidad(avance=avance)
                    res["con_problemas"] = sorted(k for k, v in calidad.items() if any(p["nivel"] == "err" for p in v["problemas"]))
                    res["con_avisos"] = sorted(k for k, v in calidad.items() if any(p["nivel"] == "warn" for p in v["problemas"]))
                    if op.get("exportar", True):
                        avance("6/6 · Exportando")
                        listos = [c for c in calidad if c not in res["con_problemas"]] if op.get("saltar_errores", True) else None
                        res["exportados"] = 0 if listos == [] else len(motor.exportar(avance=avance, codigos=listos, ajustes={"por_dia": True}))
                        if drive.estado()["activo"]:
                            drive.subir(motor.S.drive, [motor.S.listos.name], avance=avance)
                    if op.get("panel") and motor.CONFIG.get("panel", {}).get("token"):
                        avance("Subiendo al panel")
                        res["panel"] = panel.subir_semana(avance=avance)
                    inf = motor.informe_clips()
                    res["sin_identificar"] = [d["clip"] for d in inf["dudas"]]
                    res["faltan_grabar"] = inf["faltan"]
                    res["faltan_flow"] = [a["id"] for a in motor.checklist()["assets"] if not a["listo"]]
                    res["faltan_musica"] = [m["id"] for m in motor.checklist()["musica"] if not m["listo"]]
                    res["duracion"] = round(time.time() - t0)
                    TRABAJO["resultado"] = res
                    avance(f"Producción lista: {res['armados']} videos armados"
                           + (f", {res.get('exportados', 0)} exportados" if op.get("exportar", True) else "")
                           + (f", {len(res['con_problemas'])} con problemas" if res["con_problemas"] else "")
                           + (f", {len(res['sin_identificar'])} clips sin identificar" if res["sin_identificar"] else ""))
                if accion == "partes_sel":
                    hechos, raiz = exportar_a(extra, lambda r: motor.exportar_partes(extra["codigos"], avance=avance, raiz=r), avance)
                    avance(f"Exportados por partes {len(hechos)} videos a {raiz}{os.sep}POR_PARTES")
                if accion == "exportar_sel":
                    ok = revisar_destino((extra.get("carpeta") or "").strip())  # antes de armar: no esperar minutos para el error
                    if not ok["ok"]:
                        raise RuntimeError(ok["motivo"])
                    def viejo(c):  # tiene cambios guardados después de armarlo
                        f = motor.S.salida / f"{c['archivo']}.mp4"
                        return f.exists() and motor.editado_en(c) > f.stat().st_mtime + 1
                    faltan = [c for c in motor.combinaciones() if c["codigo"] in extra["codigos"]
                              and (not (motor.S.salida / f"{c['archivo']}.mp4").exists() or (extra.get("rearmar") and viejo(c)))]
                    if faltan:
                        avance(f"Antes de exportar: armando {len(faltan)} video{'s' if len(faltan) != 1 else ''} (sin armar o con cambios)")
                    if faltan:
                        motor.armar(avance=avance, codigos=[c["codigo"] for c in faltan])
                    hechos, raiz = exportar_a(extra, lambda r: motor.exportar(avance=avance, codigos=extra["codigos"],
                                                                              ajustes=extra.get("ajustes"), raiz=r), avance)
                    avance(f"Exportados {len(hechos)} video{'s' if len(hechos) != 1 else ''} a {raiz}")
                if accion == "clips_preparar":  # video largo o suelto: pasarlo a texto y proponer clips
                    clips_largos.preparar(extra["id"], avance=avance)
                if accion == "clips_armar":
                    hechos, salida = clips_largos.armar(extra["id"], extra["clips"], extra.get("opciones"), avance=avance)
                    if drive.estado()["activo"]:
                        avance("Subiendo los clips a Drive")
                        drive.subir(motor.S.drive, [motor.S.listos.name], avance=avance)
                    # sin «Listo:» (el aviso de la sala ya lo pone), en plural bien y con la ruta corta (sesión/carpeta)
                    n = len(extra["clips"])  # «hechos» trae también los de antes
                    corta = "/".join(Path(str(salida)).parts[-2:])
                    avance(f"{n} clip{'s' if n != 1 else ''} armado{'s' if n != 1 else ''} en {corta}")
                if accion == "conectar":
                    drive.conectar(extra["link"], extra.get("client_id", ""), extra.get("client_secret", ""), avance=avance)
                if accion == "bajar":
                    for m in motor.S.miembros:  # en un grupo, cada una de sus sesiones
                        drive.bajar_sesion(motor.S.en_drive(m), avance=avance)
                    drive.compartir_transcripciones(motor.S.drive, motor.S.transcripciones, avance=avance)
                if accion == "subir_drive":  # «⬆ Subir a Drive»: todo lo de esta PC, ahora
                    for m in motor.S.miembros:
                        drive.subir(motor.S.en_drive(m), avance=avance)
                    avance("Subiendo lo que se escuchó de cada clip…")
                    drive.subir_transcripciones(motor.S.drive, motor.S.transcripciones)
                    if proyecto.disponible() and proyecto._cambie_aqui() and not proyecto.revisar().get("pendiente"):
                        avance("Subiendo lo editado (subtítulos, cortes, imágenes)…")
                        proyecto.publicar("subido a mano")
                    _sin_falla(lambda: respaldos.hacer(auto=True))
                    avance(f"Listo: lo de «{motor.S.semana}» en esta PC está en Drive")
                if accion == "lista_drive":
                    avance(f"{len(drive.actualizar_lista())} sesiones en Drive")
                if accion == "escuchar":
                    motor.escuchar_clips(avance=avance)
                if accion in ("analizar", "todo_por_mi"):
                    # Paso 1: traer de Drive todos los clips, pasar su audio a texto (en esta PC, gratis), compararlo con
                    # el guion, identificar solos los clips que se parecen claramente a una pieza y procesarlos.
                    if drive.estado()["activo"]:
                        for m in motor.S.miembros:
                            drive.bajar_sesion(motor.S.en_drive(m), avance=avance)
                        drive.compartir_transcripciones(motor.S.drive, motor.S.transcripciones, avance=avance)  # lo que ya escuchó otra PC
                    guion_de_carpeta()
                    motor.escuchar_clips(avance=avance)
                    if drive.estado()["activo"]:
                        drive.compartir_transcripciones(motor.S.drive, motor.S.transcripciones, avance=avance)  # y lo de aquí, para las otras
                    n = motor.aceptar_sugerencias()
                    if n and drive.estado()["activo"]:
                        for m in motor.S.miembros:
                            drive.subir(motor.S.en_drive(m), ["asignaciones.json"], avance=avance)
                    motor.procesar(avance=avance)
                    guion_si_falta()
                    falta = len(motor.clips_sin_asignar())
                    avance("Clips analizados y comparados con el guion"
                           + (f": {n} identificados por lo que se dice" if n else "")
                           + (f", {falta} por revisar a mano" if falta else ": todos tienen su pieza") + ".")
                if accion == "todo_por_mi":
                    # «Lo analizo y ordeno todo por ti»: sin guion, la sala ya armó uno con lo que se dijo en cada clip;
                    # ahora renombra los clips en Drive con el código de su pieza y Claude propone imágenes y música
                    avance("Renombrando los clips con el código de su pieza (también en Drive)")
                    hechos = renombrar_todo()
                    claude = asistente.estado()
                    if claude.get("instalado") and claude.get("sesion"):
                        if motor.faltan_prompts_flow():
                            avance("Claude elige las imágenes y los clips de apoyo…")
                            asistente.generar_prompts_flow(avance=avance)
                        if not motor.produccion().get("musica"):
                            avance("Claude propone la música…")
                            asistente.generar_prompts_musica(avance=avance)
                        motor._prod_cache = None
                        if drive.estado()["activo"]:
                            drive.subir(motor.S.drive, ["Guion"], avance=avance)
                    falta = sum(1 for a in motor.checklist()["assets"] if not a["listo"])
                    avance(f"Listo: guion armado con lo grabado, {hechos} clips renombrados"
                           + (f", {falta} imágenes o clips por generar en Flow (Faltantes)" if falta else "") + ".")
                if accion == "guion":
                    sesion = extra["sesion"]
                    texto = asistente.generar_guion(sesion, extra.get("pedido", ""), avance=avance)
                    motor._prod_cache = None
                    if sesion == motor.S.semana:
                        motor.guardar_documentos_guion()
                    if drive.estado()["activo"]:
                        drive.subir(sesion, ["Guion"], avance=avance)
                    TRABAJO["resultado"] = texto
                    avance("Guion listo: " + (texto.strip().splitlines() or [""])[0][:120])
                if accion == "mas_apoyo":  # más imágenes y clips por cuerpo, escritos por Claude
                    op = extra or {}
                    texto = asistente.mas_apoyo_visual(int(op.get("por_cuerpo", 3)), int(op.get("clips", 1)), int(op.get("por_hook", 0)), avance=avance)
                    motor._prod_cache = None
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, ["Guion"], avance=avance)
                    TRABAJO["resultado"] = texto
                    falta = sum(1 for a in motor.checklist()["assets"] if not a["listo"])
                    avance(f"Listo: Claude agregó apoyo visual. Faltan {falta} imágenes o clips por generar en Flow (Faltantes → «Generar lo que falta»).")
                if accion == "prompts_musica":  # preparar la sesión: Claude propone la música del guion
                    asistente.generar_prompts_musica(avance=avance)
                    motor._prod_cache = None
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, ["Guion"], avance=avance)
                    n = len(motor.checklist().get("musica") or [])
                    avance(f"Listo: Claude propuso {n} pista{'s' if n != 1 else ''} de música. Genéralas en Faltantes → «Generar lo que falta».")
                if accion == "prompts_flow":
                    texto = asistente.generar_prompts_flow(avance=avance)
                    motor._prod_cache = None
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, ["Guion"], avance=avance)
                    TRABAJO["resultado"] = texto
                    avance("Prompts de Flow listos: ahora se generan las imágenes (pestaña Faltantes)")
                if accion == "generar_flow":  # la música (y sus prompts si faltan) y las imágenes, en Flow con Chrome
                    r = generar_en_flow(avance)
                    motor._prod_cache = None
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, ["Guion"], avance=avance)
                    TRABAJO["resultado"] = r
                    falta = "; ".join(f"{p['id']}: {p.get('motivo', '')}" for p in r["pendientes"])
                    avance(r.get("mensaje") or (f"Generados en Flow y guardados: {', '.join(r['hechos']) or 'ninguno'}."
                                                + (f" Faltan: {falta}" if falta else " Ahora rearma los videos.")))
                if accion == "rehacer":  # la nota del equipo sobre una imagen -> su prompt de Flow nuevo
                    r = asistente.rehacer_imagen(extra["id"], extra["nota"], avance=avance)
                    motor._prod_cache = None
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, ["Guion"], avance=avance)
                    TRABAJO["resultado"] = r
                    avance(f"Prompt nuevo de {extra['id']}: {(r.get('cambio') or 'listo').rstrip('. ')}. Cópialo y genera la imagen en Flow.")
                if accion == "rehacer_musica":  # «⟳ Regenerar prompt» de una pista: Claude lo reescribe con adrenalina
                    r = asistente.rehacer_musica(extra["id"], extra.get("nota", ""), avance=avance)
                    motor._prod_cache = None
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, ["Guion"], avance=avance)
                    TRABAJO["resultado"] = r
                    avance(f"Prompt nuevo de {extra['id']}: {(r.get('cambio') or 'listo').rstrip('. ')}. Cópialo y genera la pista en Flow Music.")
                if accion == "temas":
                    TRABAJO["resultado"] = asistente.proponer_temas(extra["cuerpos"], extra["publico"], extra.get("idea", ""), avance=avance)
                    avance("Temas propuestos: elige uno por cuerpo.")
                if accion == "revisar":
                    r = asistente.revisar_grabacion(motor.S.semana, avance=avance)
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, ["Guion"], avance=avance)
                    TRABAJO["resultado"] = r["texto"]
                    if r["aplicado"]:
                        avance("Ojo: estas palabras del plan no se dijeron: " + ", ".join(r["aplicado"][:5]))
                    motor.armar(avance=avance)
                    avance("Revisión de Claude aplicada y videos rearmados")
                if accion == "uno":
                    combo = next(c for c in motor.combinaciones() if c["codigo"] == pieza)
                    avance(f"Armando {combo['archivo']}")
                    motor.armar_uno(combo)
                if accion == "portadas":
                    motor.rehacer_portadas(avance=avance)
                if accion == "exportar":  # «Armar todos los videos» (todo) solo arma: exportar es aparte, cuando se decide publicar
                    hechos = motor.exportar(avance=avance)
                    avance(f"Exportados {len(hechos)} video{'s' if len(hechos) != 1 else ''} a {motor.S.listos}")
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, [motor.S.listos.name], avance=avance)
                        avance(f"Exportados y subidos a Drive ({motor.S.listos.name}): {len(hechos)} videos")
                if accion == "reiniciar":  # «Empezar de cero»: clips y análisis de nuevo; el guion y lo editado se quedan
                    if not drive.estado()["activo"]:
                        raise RuntimeError("Drive no está conectado: sin Drive no se pueden volver a bajar los clips.")
                    sem = motor.S.semana
                    avance("Guardando una copia de seguridad de cómo estaba…")
                    _sin_falla(lambda: respaldos.hacer(nombre="Antes de empezar de cero", auto=False))
                    for m in motor.S.miembros:  # sin clips identificados, también en Drive (si no, volverían al bajar)
                        motor._escribir_asig(m, {})
                        motor._reemp_archivo(m).unlink(missing_ok=True)
                        drive.subir(motor.S.en_drive(m), ["asignaciones.json"], avance=avance)
                    _, locales, peso = sesion_en_pc()
                    avance(f"Borrando de esta PC los clips y lo analizado ({peso / 1e9:.1f} GB)…")
                    for d in locales:
                        shutil.rmtree(d, ignore_errors=True)
                    for m in motor.S.miembros:
                        drive.bajar_sesion(motor.S.en_drive(m), avance=avance)
                    avance(f"Listo: «{sem}» empezó de cero con los clips de Drive. Ahora: Analizar los clips.")
                if accion == "terminar":  # «Terminar sesión»: todo a Drive, comprobado; y si se pidió, se borra de esta PC
                    if not drive.estado()["activo"]:
                        raise RuntimeError("Drive no está conectado: sin Drive no se puede terminar la sesión.")
                    sem = motor.S.semana
                    if proyecto.disponible():  # lo editado (cortes, subtítulos, imágenes) también queda en Drive
                        if proyecto.revisar().get("pendiente") and proyecto._cambie_aqui():
                            raise RuntimeError("Otra PC subió cambios de esta sesión que todavía no trajiste: resuélvelo en ☁ Proyecto y vuelve a terminar.")
                        if proyecto._cambie_aqui():
                            avance("Guardando lo editado en el proyecto…")
                            proyecto.publicar("al terminar la sesión")
                    raices, locales, peso = sesion_en_pc()
                    for r in raices:
                        drive.subir_todo(r, avance=avance)
                    avance("Comprobando en Drive, archivo por archivo…")
                    faltan = [f"{r}/{x}" for r in raices for x in drive.faltan_en_drive(r)]
                    if faltan:
                        raise RuntimeError(f"Drive todavía no tiene {len(faltan)} archivo{'s' if len(faltan) != 1 else ''} "
                                           f"({', '.join(faltan[:3])}{'…' if len(faltan) > 3 else ''}). No borré nada: vuelve a tocar «Terminar sesión».")
                    if not (extra or {}).get("borrar"):
                        avance(f"Listo: todo «{sem}» está en Drive (comprobado archivo por archivo).")
                    else:
                        for d in locales:
                            shutil.rmtree(d, ignore_errors=True)
                        motor.guardar_archivadas([x for x in motor.archivadas() if x != sem] + [sem])
                        threading.Thread(target=lambda: _sin_falla(drive.subir_archivadas), daemon=True).start()
                        avance(f"Listo: «{sem}» está entera en Drive y se borró de esta PC ({peso / 1e9:.1f} GB libres). "
                               "Quedó archivada: si la vuelves a abrir, se baja de Drive.")
                if accion == "importar":
                    n = panel.importar_finales(avance=avance)
                    avance(f"Importados {n} videos finales al panel")
                if accion == "panel":
                    n = panel.subir_semana(avance=avance)
                    avance(f"Subidos {n} videos al panel")
            TRABAJO["mensaje"] = "Listo"
        except motor.Cancelado:
            TRABAJO["error"] = None
            TRABAJO["mensaje"] = "Cancelado"
            TRABAJO["log"] = (TRABAJO["log"] + ["Cancelado por ti."])[-200:]
            TRABAJO["cancelado"] = True
        except Exception as e:
            TRABAJO["error"] = str(e)
            avance("Error: " + str(e).splitlines()[0])
            traceback.print_exc()
        finally:
            TRABAJO["activo"] = False
            motor.CANCELAR.clear()
            TRABAJO["fin"] = time.time()
            # cuántos videos (o clips) pasaron por el trabajo, para saber cuánto tarda cada uno
            pasos = [l for l in TRABAJO["log"] if l.startswith(("Armando ", "Exportando ", "Renombrando ", "Procesando ", "Transcribiendo "))]
            if accion == "producir":
                pasos = [l for l in TRABAJO["log"] if l.startswith("Armando ")]
            h = {"accion": accion, "sesion": motor.S.semana, "inicio": TRABAJO["inicio"], "fin": TRABAJO["fin"],
                 "duracion": round(TRABAJO["fin"] - TRABAJO["inicio"], 1), "cantidad": len(pasos),
                 "resultado": "cancelado" if TRABAJO.get("cancelado") else "error" if TRABAJO["error"] else "listo",
                 "mensaje": (TRABAJO["error"] or (TRABAJO["log"] or [""])[-1])[:200]}
            todo = [h] + leer_historial()
            try:
                HISTORIAL.write_text(json.dumps(todo[:300], ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
            TRABAJO["historial"] = todo[:15]
            if TRABAJO.get("cancelado") and COLA:  # cancelar también cancela lo que esperaba en la cola
                COLA.clear()
            if COLA:
                siguiente = COLA.pop(0)
                TRABAJO["cola"] = [x[0] for x in COLA]
                try:
                    lanzar(*siguiente)
                except Exception as e:  # noqa: BLE001
                    print("No se pudo correr lo que estaba en la cola:", e)
            else:
                TRABAJO["cola"] = []

    motor.CANCELAR.clear()
    motor.PAUSA.clear()
    TRABAJO.update({"activo": True, "accion": accion, "mensaje": "Empezando…", "error": None, "log": [], "log_t": [], "resultado": None,
                    "cancelado": False, "inicio": time.time(), "pausado": None, "avance": None})
    threading.Thread(target=correr, daemon=True).start()


NOMBRE_OK = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return  # la ventana se cerró o se recargó a mitad de una respuesta: no es un error
        super().handle_error(request, client_address)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _pin_ok(self):
        from http.cookies import SimpleCookie
        c = SimpleCookie(self.headers.get("Cookie") or "")
        pin = red()["pin"]
        return bool(pin) and "sala_pin" in c and c["sala_pin"].value == pin

    def _permitido(self, ruta):
        if self._local():
            return True
        if ruta not in ABIERTO_A_LA_RED:
            self.send_error(403)
            return False
        if ruta == "/api/teleprompter" and not self._pin_ok():
            self._json({"error": "PIN"}, 401)
            return False
        return True

    def do_GET(self):
        with motor.memoria():  # los clips y las piezas se miran una sola vez por pedido
            return self._do_GET()

    def _do_GET(self):
        u = urlparse(self.path)
        if not self._permitido(u.path):
            return
        if u.path == "/teleprompter":
            b = (motor.RAIZ / "teleprompter.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/teleprompter":
            q = parse_qs(u.query)
            sesion = (q.get("sesion") or [motor.S.semana])[0]
            if sesion not in motor.semanas_disponibles():
                sesion = motor.S.semana
            self._json({"sesion": sesion, "sesiones": motor.semanas_disponibles(), "piezas": motor.guion_para_grabar(sesion)})
        elif u.path == "/":
            b = (motor.RAIZ / "app.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/estado":
            self._json(estado())
        elif u.path == "/api/pedidos_antigravity":  # lo que se le pidió a Gemini y sigue sin terminar, con su mensaje
            self._json({"pedidos": antigravity.pedidos()})
        elif u.path == "/api/terminar_info":  # antes de «Terminar sesión»: cuánto ocupa y qué videos no se exportaron
            raices, _, peso = sesion_en_pc()
            self._json({"ok": True, "sesion": motor.S.semana, "peso": peso, "carpetas": raices, "sin_exportar": sin_exportar(),
                        "drive": bool(drive.estado()["activo"])})
        elif u.path == "/api/trabajo":
            self._json(TRABAJO)
        elif u.path == "/api/marca":
            self._json(marca.datos())
        elif u.path.startswith("/marca/"):  # logos, productos y tipografías de la marca del equipo (_MARCA)
            rel = unquote(u.path[len("/marca/"):])
            if rel.split("/")[0] not in marca.TIPOS:  # los logos que trae la sala (biblioteca/marca), los de siempre
                return self._archivo(motor.BIBLIO_BASE / "marca", rel)
            try:
                f = marca.ruta_archivo(rel)
            except ValueError:
                return self.send_error(404)
            if not f.is_file():
                return self.send_error(404)
            self._archivo(f.parent, f.name)
        elif u.path == "/api/equipo":
            try:
                self._json({"ok": True, **drive.equipo()})
            except (RuntimeError, ValueError) as e:
                self._json({"ok": False, "error": str(e)})
        elif u.path == "/api/clips":
            self._json({"ok": True, "videos": clips_largos.lista()})
        elif u.path in ("/api/clips/uno", "/api/clips/fuente", "/api/clips/salida"):
            q = parse_qs(u.query)
            try:
                i = q.get("id", [""])[0]
                if u.path == "/api/clips/uno":
                    return self._json({"ok": True, **clips_largos.uno(i)})
                f = clips_largos.fuente(i) if u.path == "/api/clips/fuente" else clips_largos.carpeta_salida(i) / Path(q.get("archivo", [""])[0]).name
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)})
            if not f or not f.is_file():
                return self.send_error(404)
            self._archivo(f.parent, f.name)
        elif u.path == "/api/marca_importar/ver":  # lo subido para importar, antes de guardarlo (para verlo en la revisión)
            q = parse_qs(u.query)
            try:
                f = importar_marca.ruta_staging(q.get("id", [""])[0], q.get("ruta", [""])[0])
            except ValueError:
                return self.send_error(404)
            self._archivo(f.parent, f.name)
        elif u.path == "/api/cuenta/carpetas":  # elegir la carpeta del proyecto (cuenta.js): las carpetas de la cuenta
            self._json({"ok": True, **cuenta.carpetas_para_elegir()})
        elif u.path == "/api/sesiones":  # la pantalla de entrada: qué sesiones hay y cómo va cada una
            self._json({"sesiones": motor.resumen_sesiones(), "actual": motor.S.semana, "ultima": motor.CONFIG.get("semana")})
        elif u.path == "/api/proyecto":  # ¿hay una versión nueva del proyecto hecha en otra PC?
            e = proyecto.ESTADO.get(motor.S.semana)
            if e is None or "forzar" in u.query:
                try:
                    e = proyecto.revisar()
                except Exception as ex:  # noqa: BLE001 — sin internet
                    e = {"activo": False, "error": str(ex)[:200]}
            self._json({**e, "yo": proyecto.quien()})
        elif u.path == "/api/cuadro":
            q2 = parse_qs(u.query)
            c = next((x for x in motor.combinaciones() if x["codigo"] == q2["codigo"][0]), None)
            if not c:
                return self.send_error(404)
            destino = motor.carpeta_trabajo() / "cuadro.jpg"
            motor.cuadro(c, float(q2.get("t", ["0.5"])[0]), destino)
            b = destino.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/portada_vista":  # la portada con los ajustes que se están eligiendo, sin guardarla
            q2 = parse_qs(u.query)
            c = next((x for x in motor.combinaciones() if x["codigo"] == q2["codigo"][0]), None)
            if not c:
                return self.send_error(404)
            destino = motor.carpeta_trabajo() / "portada_vista.jpg"
            d = json.loads(q2.get("d", ["{}"])[0])
            motor.hacer_portada(c, prueba={k: v for k, v in d.items() if k in motor.CAMPOS_PORTADA}, destino=destino)
            b = destino.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/guion_drive":  # ¿cambió el guion en Drive? si sí, se trae solo
            cambio = False
            try:
                g = motor.S.guion
                if drive.estado()["activo"] and g.is_relative_to(drive.CACHE) and drive.bajada(motor.S.drive):
                    cambio = drive.traer_guion(g)
                    if cambio:
                        motor._prod_cache = None
            except Exception as e:  # sin internet: se reintenta en la próxima revisión
                print("No se pudo revisar el guion en Drive:", e)
            res = {}
            if not TRABAJO["activo"]:  # el guion que el equipo dejó en Guion/Guiones se lee solo (no a mitad de un trabajo)
                try:
                    res = leer_guion_de_carpeta() or {}
                except Exception as e:
                    print("No se pudo leer el guion de la carpeta:", e)
            self._json({"cambio": cambio or bool(res.get("leido")), **res})
        elif u.path == "/api/guion":
            self._json(motor.revision_guion())
        elif u.path == "/api/estructura":
            try:
                q = parse_qs(u.query)
                e = motor.estructura(q["codigo"][0], **{k: q[k][0] for k in ("hook", "cuerpo", "cta") if k in q})
                for k in ("hook", "cuerpo", "cta"):
                    e[k]["url"] = url_media(e[k].pop("archivo")) if e[k].get("archivo") else None
                self._json(e)
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif u.path == "/api/version":
            self._json(actualizador.revisar(forzar="forzar" in u.query))
        elif u.path == "/api/claude":
            self._json(asistente.estado(forzar="forzar" in u.query))
        elif u.path == "/api/respaldos":  # copias de seguridad de los cambios de la sesión abierta
            self._json({"respaldos": respaldos.listar(), "sesion": motor.S.semana, "max_auto": respaldos.MAX_AUTO})
        elif u.path == "/api/latido":
            if "ping" in u.query:
                LATIDO["ultimo"] = time.time()
                cuenta.latido()
            self._json({"viva": time.time() - LATIDO["ultimo"] < 20})
        elif u.path == "/cuenta.js":
            b = (motor.RAIZ / "cuenta.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/cuenta":
            self._json(cuenta.estado())
        elif u.path == "/api/dispositivos":
            self._json({"dispositivos": cuenta.dispositivos(), "dias_nuevo": cuenta.DIAS_NUEVO})
        elif u.path == "/api/sonidos":
            self._json(motor.biblioteca_sonidos())
        elif u.path == "/api/ganancia":  # cuánto se sube o se baja un sonido en el video (para escucharlo igual de fuerte)
            f = motor.ruta_sonido(parse_qs(u.query).get("id", [""])[0])
            self._json({"g": round(motor.ganancia_normalizada(f), 3) if f else 1.0})
        elif u.path == "/sfx":  # el sonido con su edición de la página Sonidos (&original=1: tal cual el archivo)
            q = parse_qs(u.query)
            f = motor.ruta_sonido(q.get("id", [""])[0])
            if not f:
                return self.send_error(404)
            if "original" not in q:
                try:  # &aj=<json>: escuchar una edición sin guardarla
                    f = motor.sonido_editado(f, json.loads(q["aj"][0]) if "aj" in q else None)
                except Exception:  # noqa: BLE001
                    pass
            self._archivo(f.parent, f.name)
        elif u.path == "/musica":  # una pista por su nombre, para escucharla en la galería de Piezas
            f = motor.buscar_musica(parse_qs(u.query).get("id", [""])[0])
            if not f:
                return self.send_error(404)
            self._archivo(f.parent, f.name)
        elif u.path == "/api/muestra_estilo":  # muestra de una opción de Estilo de edición, hecha con ffmpeg como el armado
            q = parse_qs(u.query)
            try:
                cambios = json.loads(q.get("cambios", ["{}"])[0] or "{}")
                self._json({"ok": True, **motor.muestra_estilo(q.get("que", [""])[0], cambios if isinstance(cambios, dict) else {})})
            except (ValueError, RuntimeError) as e:
                self._json({"ok": False, "error": str(e).splitlines()[0][:300]})
        elif u.path.startswith("/muestra/"):
            self._archivo(motor.MUESTRAS, u.path.rsplit("/", 1)[1])
        elif u.path == "/pista":  # la música de un video, para escucharla en la vista previa del editor
            q = parse_qs(u.query)
            try:
                r = json.loads((motor.S.recetas / f"{q['codigo'][0]}.json").read_text(encoding="utf-8"))
                f = Path(r["pistas"][int(q["k"][0])]["archivo"])
            except (KeyError, ValueError, IndexError, FileNotFoundError):
                return self.send_error(404)
            if not f.is_file():
                return self.send_error(404)
            self._archivo(f.parent, f.name)
        elif u.path == "/api/informe_clips":
            self._json(motor.informe_clips())
        elif u.path == "/api/calidad":
            q2 = parse_qs(u.query)
            self._json(motor.revisar_calidad(q2["codigos"][0].split(",") if q2.get("codigos") else None, avance=lambda m: None))
        elif u.path == "/api/resultados":
            self._json({"metricas": motor.metricas(), "ranking": motor.ranking()})
        elif u.path == "/api/historial":
            self._json(leer_historial())
        elif u.path == "/api/renombrar":
            self._json(motor.plan_renombrar())
        elif u.path == "/api/espectro_sonido":  # frecuencias de un sonido, para el fondo del ecualizador
            f = motor.ruta_sonido(parse_qs(u.query).get("id", [""])[0])
            self._json(motor.espectro_archivo(f) if f else {"f": [], "db": []})
        elif u.path == "/api/onda_sonido":  # forma de onda de un sonido de la biblioteca (pista «Sonidos» del editor)
            q = parse_qs(u.query)
            f = motor.ruta_sonido(q.get("id", [""])[0])
            if f and "original" not in q:
                try:
                    f = motor.sonido_editado(f)
                except Exception:  # noqa: BLE001
                    pass
            self._json(motor.onda_archivo(f) if f else {"por_seg": 50, "picos": [], "dur": 0})
        elif u.path == "/api/partes":  # los clips (o frases) de una pieza, para la línea de tiempo de Piezas
            n = parse_qs(u.query)["pieza"][0]
            firma = motor.S.proc / f"{n}.partes.json"
            try:
                clips = len(json.loads(firma.read_text(encoding="utf-8")).get("duraciones") or []) > 1
            except (FileNotFoundError, ValueError, AttributeError):
                clips = False
            self._json({"partes": motor.partes_de(n), "clips": clips})
        elif u.path == "/api/onda":
            self._json(motor.onda(parse_qs(u.query)["pieza"][0]))
        elif u.path == "/api/apoyo_visual":
            self._json(motor.apoyo_visual())
        elif u.path == "/api/guion_hojas":
            self._json(motor.hojas_guion())
        elif u.path == "/api/guion_doc":
            version = (parse_qs(u.query).get("version") or ["host"])[0]
            vista = (parse_qs(u.query).get("vista") or ["combinacion"])[0]
            texto = motor.documento_guion("produccion" if version == "produccion" else "host", vista)
            if texto and "guardar" in u.query:  # quedan también en Drive (carpeta Guion), para abrirlas desde cualquier lado
                if motor.guardar_documentos_guion():  # solo si cambiaron (antes, cada cambio de pestaña subía a Drive)
                    drive.subir_en_segundo_plano(motor.S.drive, ["Guion"])
            self._json({"texto": texto})
        elif u.path == "/api/informe":
            f = motor.S.guion / "director" / "informe.md"
            self._json({"texto": f.read_text(encoding="utf-8") if f.exists() else ""})
        elif u.path.startswith("/media/"):
            self._archivo(motor.DATOS, unquote(u.path[len("/media/"):]))
        elif u.path.startswith("/fuentes/"):
            import fuentes
            self._archivo(fuentes.ruta(unquote(u.path[len("/fuentes/"):])).parent, unquote(u.path[len("/fuentes/"):]))
        elif u.path.startswith("/marca/"):
            self._archivo(motor.BIBLIO_BASE / "marca", unquote(u.path[len("/marca/"):]))
        elif u.path.startswith("/grab/"):
            self._archivo(motor.S.grab, unquote(u.path[len("/grab/"):]))
        elif u.path == "/api/descargas":
            self._json(descargas_recientes(todos=bool(parse_qs(u.query).get("todos"))))
        elif u.path == "/api/auto_descargas":  # lo que llegó solo de Descargas desde «desde»
            desde = float(parse_qs(u.query).get("desde", ["0"])[0] or 0)
            self._json({"ok": True, "auto": bool(motor.CONFIG.get("auto_descargas", True)), "t": time.time(),
                        "traidos": [x for x in AUTO_DESC if x["t"] > desde]})
        elif u.path.startswith("/descargas/"):  # vista previa de lo que se bajó (solo desde esta PC)
            self._archivo(carpeta_descargas(), unquote(u.path[len("/descargas/"):]))
        else:
            self.send_error(404)

    def _archivo(self, base, rel):
        base = base.resolve()
        ruta = (base / rel).resolve()
        if base not in ruta.parents or not ruta.is_file():
            return self.send_error(404)
        tam = ruta.stat().st_size
        ini, fin = 0, tam - 1
        rango = self.headers.get("Range")
        if rango and rango.startswith("bytes="):
            a, _, b = rango[6:].partition("-")
            ini = int(a) if a else 0
            fin = min(int(b), tam - 1) if b else tam - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {ini}-{fin}/{tam}")
        else:
            self.send_response(200)
        tipo = mimetypes.guess_type(ruta.name)[0]
        if ruta.suffix.lower() not in motor.EXT_VID | motor.NO_VIDEO and motor.es_video(ruta):
            tipo = "video/mp4"  # clips sin extensión («C1.1» no es un .1, «C1-H1»): Drive a veces se la quita
        self.send_header("Content-Type", tipo or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fin - ini + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(ruta, "rb") as f:
            f.seek(ini)
            restante = fin - ini + 1
            try:
                while restante > 0:
                    trozo = f.read(min(1 << 16, restante))
                    if not trozo:
                        break
                    self.wfile.write(trozo)
                    restante -= len(trozo)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass

    def _subir(self, q):
        """Guarda el archivo arrastrado con el nombre correcto (assets de Flow o piezas grabadas). Se escribe en un
        temporal y solo si llegó entero reemplaza al anterior (que pasa a _REEMPLAZADOS/): antes se borraba primero
        y una subida cortada dejaba un archivo a medias con el nombre bueno, que además se subía a Drive."""
        destino = q["destino"][0]
        nombre = q["nombre"][0]
        ext = "." + q["ext"][0].lower().lstrip(".")
        carpetas = {"assets": motor.S.assets, "musica": motor.S.musica, "hooks": motor.S.carpeta("hook"),
                    "cuerpos": motor.S.carpeta("cuerpo"), "ctas": motor.S.carpeta("cta")}
        # un hook, un cuerpo o un CTA es siempre un video (antes entraba una foto como Clips/Hooks/C1_H2.jpg)
        permitidas = motor.EXT_AUDIO if destino == "musica" else motor.EXT_VID if destino in ("hooks", "cuerpos", "ctas") \
            else motor.EXT_IMG | motor.EXT_VID
        if destino not in carpetas or not NOMBRE_OK.match(nombre) or ext not in permitidas:
            raise ValueError("Ese archivo no va aquí: " + ("sube un audio" if destino == "musica" else "sube un video"
                             if destino in ("hooks", "cuerpos", "ctas") else "sube una imagen o un clip"))
        if destino in ("hooks", "cuerpos", "ctas") and TRABAJO["activo"]:
            raise RuntimeError("La sala está trabajando con los clips (☰). Espera a que termine para cambiar una toma.")
        carpeta = carpetas[destino]
        carpeta.mkdir(parents=True, exist_ok=True)
        largo = int(self.headers.get("Content-Length") or 0)
        if largo <= 0:
            raise ValueError("El archivo llegó vacío")
        tmp = carpeta / f".{nombre}{ext}.{os.getpid()}.part"
        restante = largo
        try:
            with open(tmp, "wb") as f:
                while restante > 0:
                    trozo = self.rfile.read(min(1 << 20, restante))
                    if not trozo:
                        break
                    f.write(trozo)
                    restante -= len(trozo)
            if restante > 0:
                raise RuntimeError("La subida se cortó antes de terminar: no se cambió nada. Vuelve a intentarlo.")
            apartar_viejos(carpeta, nombre)
            ruta = carpeta / f"{nombre}{ext}"
            os.replace(tmp, ruta)
        finally:
            tmp.unlink(missing_ok=True)
        return ruta.name

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._permitido(u.path):
            return
        # Una página de internet abierta en el navegador podía mandar órdenes a la sala (abrirla a la red, exportar…):
        # el navegador siempre dice de qué página viene el pedido (Origin) y tiene que ser la sala misma.
        origen = self.headers.get("Origin")
        if origen and origen != "null" and urlparse(origen).netloc.lower() != (self.headers.get("Host") or "").lower():
            self.send_error(403)
            return
        try:
            if u.path == "/entrar":
                pin = str(self._cuerpo().get("pin", ""))
                if not red()["pin"] or pin != red()["pin"]:
                    time.sleep(1)  # frena intentos a ciegas
                    return self._json({"ok": False}, 403)
                b = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"sala_pin={pin}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            if u.path == "/api/cancelar":
                if TRABAJO["activo"]:
                    motor.cancelar()
                    TRABAJO["pausado"] = None
                return self._json({"ok": True})
            if u.path == "/api/pausar":  # {pausa: true} pausa el trabajo en curso; {pausa: false} lo reanuda
                if not TRABAJO["activo"]:
                    return self._json({"ok": False, "error": "No hay ningún proceso corriendo"}, 400)
                if self._cuerpo().get("pausa"):
                    motor.PAUSA.set()
                    TRABAJO["pausado"] = TRABAJO.get("pausado") or time.time()
                else:
                    motor.PAUSA.clear()
                    if TRABAJO.get("pausado"):  # lo pausado no cuenta para calcular cuánto falta
                        quieto = time.time() - TRABAJO["pausado"]
                        if TRABAJO.get("avance"):
                            TRABAJO["avance"]["t0"] += quieto
                        TRABAJO["inicio_pausas"] = TRABAJO.get("inicio_pausas", 0) + quieto
                    TRABAJO["pausado"] = None
                return self._json({"ok": True, "pausado": TRABAJO["pausado"]})
            if u.path == "/api/entrar":  # entrar con Google: sesión común de las tres salas
                e = cuenta.entrar(str(self._cuerpo().get("link", "")))
                drive.asegurar_config(forzar=True)
                return self._json({"ok": True, "cuenta": e})
            if u.path == "/api/salir":
                cuenta.cerrar_sesion()
                motor.guardar_config({"drive": {**motor.CONFIG.get("drive", {}), "activo": False}})
                return self._json({"ok": True})
            if u.path == "/api/cerrar_dispositivo":
                cuenta.cerrar_en(str(self._cuerpo()["id"]))
                return self._json({"ok": True})
            if u.path == "/api/cerrar_otros":
                return self._json({"ok": True, "cerrados": cuenta.cerrar_en_todas_menos_esta()})
            if u.path == "/api/adios":
                LATIDO["adios"] = time.time()
                al_cerrar_ventana()
                return self._json({"ok": True})
            if u.path == "/api/red":
                activa = bool(self._cuerpo().get("activa"))
                import secrets
                actual = motor.CONFIG.get("red_local", {})
                motor.guardar_config({"red_local": {"activa": activa, "pin": actual.get("pin") or f"{secrets.randbelow(9000) + 1000}"}})
                return self._json({"ok": True, **red(), "reiniciar": True})
            if u.path == "/api/subir":
                archivo = self._subir(q)
                carpeta = {"assets": motor.S.assets, "musica": motor.S.musica, "hooks": motor.S.carpeta("hook"),
                           "cuerpos": motor.S.carpeta("cuerpo"), "ctas": motor.S.carpeta("cta")}[q["destino"][0]]
                drive.subir_en_segundo_plano(motor.S.drive, [carpeta.relative_to(motor.S.principal).as_posix()])
                self._json({"ok": True, "archivo": archivo})
            elif u.path == "/api/subir_guion":  # el guion de la sesión: produccion.json de Claude, PDF, Word, .md o .txt
                n = int(self.headers.get("Content-Length") or 0)
                if n > 20 * 1024 * 1024:
                    raise ValueError("El archivo es muy grande para ser un guion")
                if q.get("probar"):  # solo leerlo: qué trae y qué piezas se quitarían (para preguntar antes)
                    return self._json({"ok": True, **motor.probar_guion(q["nombre"][0], self.rfile.read(n))})
                r = motor.subir_guion(q["nombre"][0], self.rfile.read(n))
                drive.subir_en_segundo_plano(motor.S.drive, ["Guion"])
                if motor.clips():  # con clips ya grabados: se vuelven a comparar con el guion nuevo
                    r["reasignar_en_cola"] = bool(lanzar("reasignar", en_cola=True))
                if motor.CONFIG.get("panel", {}).get("token"):
                    threading.Thread(target=lambda: _sin_error(panel.subir_guion), daemon=True).start()
                self._json({"ok": True, **r})
            elif u.path == "/api/formato":
                if TRABAJO["activo"]:
                    raise RuntimeError("Hay un trabajo en curso: espera a que termine para cambiar el formato.")
                motor.guardar_formato(self._cuerpo()["formato"])
                self._json({"ok": True, "formato": motor.formato()})
            elif u.path == "/api/semana":
                abrir_sesion(self._cuerpo()["semana"])
                self._json({"ok": True})
            elif u.path == "/api/proyecto":
                b = self._cuerpo()
                acc = b.get("accion")
                if acc in ("actualizar", "volver") and TRABAJO["activo"]:
                    raise RuntimeError("La sala está trabajando (☰). Espera a que termine para actualizar el proyecto.")
                if acc == "actualizar":
                    r = proyecto.traer()
                elif acc == "volver":
                    r = proyecto.volver(int(b.get("version") or 0))
                elif acc in ("quedarme", "publicar"):
                    r = {"version": proyecto.quedarme() if acc == "quedarme" else proyecto.publicar()}
                else:
                    raise ValueError("Acción no válida")
                try:
                    proyecto.revisar()
                except Exception:  # noqa: BLE001
                    pass
                self._json({"ok": True, **r})
            elif u.path == "/api/subir_subtitulos":  # revisión de calidad: el subtítulo donde no lo tapan los botones
                codigos = motor.subir_subtitulos()
                cola = bool(codigos and self._cuerpo().get("armar") and lanzar("armar_sel", extra={"codigos": codigos}, en_cola=True))
                self._json({"ok": True, "codigos": codigos, "en_cola": cola})
            elif u.path == "/api/marca":
                self._json({"ok": True, "marca": marca.guardar(self._cuerpo().get("marca") or {})})
            elif u.path == "/api/marca_archivo":
                n = int(self.headers.get("Content-Length") or 0)
                if n > 30 * 1024 * 1024:
                    raise ValueError("El archivo es muy grande (máximo 30 MB)")
                nombre = marca.guardar_archivo(q["tipo"][0], q["nombre"][0], self.rfile.read(n))
                self._json({"ok": True, "archivo": nombre, "marca": marca.datos()})
            elif u.path == "/api/equipo":
                b = self._cuerpo()
                if b.get("accion") == "invitar":
                    self._json({"ok": True, **drive.invitar(b.get("email"), b.get("rol") or "writer")})
                elif b.get("accion") == "quitar":
                    self._json({"ok": True, **drive.quitar_acceso(b.get("id"))})
                else:
                    raise ValueError("Acción no válida")
            elif u.path == "/api/guion_word":
                b = self._cuerpo()
                version = "produccion" if b.get("version") == "produccion" else "host"
                texto = motor.documento_guion(version, b.get("vista") or "combinacion")
                if not texto:
                    raise ValueError("Esta sesión todavía no tiene guion")
                f = guion_word.guardar(texto, motor.S.semana, version, motor.S.guion)
                drive.subir_en_segundo_plano(motor.S.drive, ["Guion"])
                self._json({"ok": True, "ruta": str(f)})
            elif u.path == "/api/clips/subir":
                n = int(self.headers.get("Content-Length") or 0)
                i = clips_largos.recibir(q.get("nombre", ["video.mp4"])[0], self.rfile.read, n)
                cola = lanzar("clips_preparar", extra={"id": i}, en_cola=True)
                self._json({"ok": True, "id": i, "en_cola": bool(cola)})
            elif u.path == "/api/clips/armar":
                b = self._cuerpo()
                clips = [{"ini": float(c["ini"]), "fin": float(c["fin"]), "titulo": str(c.get("titulo") or "")[:120],
                          "encuadre": c.get("encuadre") if c.get("encuadre") in ("cara", "centro", "difuminado") else None,
                          "x": None if c.get("x") in (None, "") else max(0.0, min(1.0, float(c["x"])))} for c in b.get("clips") or []]
                if not clips:
                    raise ValueError("Elige al menos un clip")
                clips_largos.uno(str(b.get("id") or ""))  # que exista
                cola = lanzar("clips_armar", extra={"id": b["id"], "clips": clips, "opciones": b.get("opciones") or {}}, en_cola=True)
                self._json({"ok": True, "en_cola": bool(cola)})
            elif u.path == "/api/clips/borrar":
                clips_largos.borrar(str(self._cuerpo().get("id") or ""))
                self._json({"ok": True})
            elif u.path == "/api/clips/abrir":
                d = clips_largos.carpeta_salida(str(self._cuerpo().get("id") or ""))
                d.mkdir(parents=True, exist_ok=True)
                os.startfile(str(d))
                self._json({"ok": True})
            elif u.path == "/api/abrir_navegador":  # el teleprompter va en el navegador (en la ventana de la sala no había cómo volver)
                ruta = str(self._cuerpo().get("ruta") or "/teleprompter")
                if not ruta.startswith("/teleprompter"):
                    raise ValueError("Solo se abre el teleprompter")
                import webbrowser
                webbrowser.open(f"http://localhost:{self.server.server_address[1]}{ruta}")
                self._json({"ok": True})
            elif u.path == "/api/maximizar":  # la página recién abierta pide su ventana maximizada
                maximizar_ventana()
                self._json({"ok": True})
            elif u.path == "/api/paquete_antigravity":
                b = self._cuerpo()
                self._json({"ok": True, **antigravity.armar(bool(b.get("todas")), bool(b.get("abrir")))})
            elif u.path == "/api/chrome_flow":  # abre el Chrome aparte de Flow (para entrar la primera vez con la cuenta)
                porque = antigravity.abrir_chrome_flow()
                self._json({"ok": not porque, "porque": porque})
            elif u.path == "/api/prompts_antigravity":  # «Agregar los prompts» con Gemini, sin Claude
                b = self._cuerpo()
                self._json({"ok": True, **antigravity.armar_prompts(*(max(0, min(tope, int(float(b.get(k) or 0)))) for k, tope in TOPES_APOYO), abrir=True)})
            elif u.path == "/api/ia":  # con qué IA trabaja esta PC: «claude» o «antigravity» (todo con una)
                ia = str(self._cuerpo().get("ia") or "")
                motor.guardar_config({"ia": ia if ia in ("claude", "antigravity") else ""})
                self._json({"ok": True, "ia": motor.CONFIG["ia"]})
            elif u.path == "/api/abrir_antigravity":  # «Abrir Antigravity» en la ventana de los pasos
                self._json({"ok": True, "abierto": antigravity.abrir_pedido(str(self._cuerpo().get("tipo") or ""))})
            elif u.path == "/api/musica_antigravity":  # la música con Gemini: la escribe (si falta) y la genera en Flow Music
                self._json({"ok": True, **antigravity.armar_musica(abrir=True)})
            elif u.path == "/api/traer_prompts_antigravity":
                self._json({"ok": True, **antigravity.traer_prompts()})
            elif u.path == "/api/marca_plantilla":
                self._json({"ok": True, "ruta": str(importar_marca.guardar_plantilla())})
            elif u.path == "/api/marca_importar":
                acc = q.get("accion", [""])[0]
                if acc == "nuevo":
                    self._json({"ok": True, "id": importar_marca.nuevo()})
                elif acc == "subir":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 400 * 1024 * 1024:
                        raise ValueError("El archivo es muy grande (máximo 400 MB)")
                    cuantos = importar_marca.recibir(q["id"][0], q["ruta"][0], self.rfile.read, n)
                    self._json({"ok": True, "archivos": cuantos})
                elif acc == "analizar":
                    self._json({"ok": True, **importar_marca.analizar(self._cuerpo().get("id"))})
                elif acc == "aplicar":
                    b = self._cuerpo()
                    self._json({"ok": True, **importar_marca.aplicar(b.get("id"), b.get("propuesta") or {})})
                else:
                    raise ValueError("Acción no válida")
            elif u.path == "/api/marca_borrar":
                b = self._cuerpo()
                marca.borrar(str(b.get("tipo") or ""), str(b.get("archivo") or ""))
                self._json({"ok": True, "marca": marca.datos()})
            elif u.path == "/api/cuenta/primera":  # entrar y elegir (o crear) la carpeta del proyecto (cuenta.js)
                r = cuenta.primera(self._cuerpo())
                if "editor" in r:  # ya eligió carpeta
                    drive.tras_elegir_carpeta(r)
                self._json({"ok": True, **r})
            elif u.path == "/api/abrir_link":  # trabajar en una carpeta pegando su link de Drive (o su ruta en esta PC)
                sem = sesion_de_link(str(self._cuerpo().get("link") or ""))
                abrir_sesion(sem)
                self._json({"ok": True, "semana": motor.S.semana})
            elif u.path == "/api/respaldos":  # guardar una copia ahora o restaurar una
                b = self._cuerpo()
                if b.get("accion") == "restaurar":
                    if TRABAJO["activo"]:
                        raise ValueError("Espera a que termine lo que está haciendo la sala (☰) para restaurar")
                    try:  # lo de ahora que no se había subido queda en el historial de ☁ Proyecto
                        proyecto.antes_de_restaurar()
                    except Exception as e:  # noqa: BLE001 — igual queda la copia «Antes de restaurar»
                        print("Proyecto antes de restaurar:", e)
                    r = respaldos.restaurar(str(b.get("archivo", "")))
                    try:  # lo restaurado pasa a ser la versión del proyecto: las otras PCs reciben el aviso
                        r["version_proyecto"] = proyecto.tras_restaurar(r.get("fecha", ""))
                    except Exception as e:  # noqa: BLE001 — sin Drive se sube solo en la próxima revisión
                        print("Proyecto tras restaurar:", e)
                    if drive.estado()["activo"]:
                        for m in motor.S.miembros:
                            drive.subir_en_segundo_plano(motor.S.en_drive(m), ["asignaciones.json"])
                        drive.subir_en_segundo_plano(motor.S.drive, ["Guion"])
                        threading.Thread(target=drive.subir_grupos, daemon=True).start()
                    self._json({"ok": True, **r})
                else:
                    self._json({"ok": True, "respaldo": respaldos.hacer(nombre=str(b.get("nombre") or "Guardada a mano"))})
            elif u.path == "/api/auto_descargas":  # prender o apagar «Traer solos de Descargas», o traer ahora
                b = self._cuerpo()
                if "activo" in b:
                    motor.guardar_config({"auto_descargas": bool(b["activo"])})
                traidos = (traer_de_antigravity() + traer_descargas()) if b.get("ahora") else []
                AUTO_DESC.extend(traidos)
                self._json({"ok": True, "auto": bool(motor.CONFIG.get("auto_descargas", True)), "traidos": traidos})
            elif u.path == "/api/importar_descarga":
                # un archivo de Descargas pasa a la sesión con el nombre que pide el guion, se sube a Drive
                # y se dice qué videos lo usan (para re-armar solo esos)
                b = self._cuerpo()
                aid, destino = str(b.get("id", "")), b.get("destino")
                archivo, carpeta = importar_descarga(str(b.get("nombre", "")), aid, destino)
                apartar_descarga(str(b.get("nombre", "")))
                drive.subir_en_segundo_plano(motor.S.drive, [carpeta.relative_to(motor.S.principal).as_posix()])
                self._json({"ok": True, "archivo": archivo, "codigos": motor.videos_que_usan(aid, destino)})
            elif u.path == "/api/archivar":  # una sesión terminada deja de salir en las listas (se encuentra buscando)
                b = self._cuerpo()
                sesion = b.get("sesion")
                if sesion not in motor.semanas_disponibles():
                    raise ValueError("Esa sesión no existe")
                lista = [x for x in motor.archivadas() if x != sesion] + ([sesion] if b.get("archivar") else [])
                motor.guardar_archivadas(lista)
                drive.subir_archivadas()
                self._json({"ok": True})
            elif u.path == "/api/grupo":
                b = self._cuerpo()
                nombre = " ".join(str(b.get("nombre", "")).split()).upper()[:60]
                gs = motor.grupos()
                if (gs.get(nombre) or {}).get("carpeta"):
                    raise ValueError("Esta producción es una carpeta de Drive con sus días dentro: se cambia en Drive")
                if b.get("borrar"):
                    gs.pop(nombre, None)
                else:
                    sesiones = [x for x in b.get("sesiones", []) if x in motor.semanas_disponibles() and x not in gs]
                    if not motor.nombre_sesion_valido(nombre) or len(nombre) < 2 or len(sesiones) < 2:
                        raise ValueError("Ponle un nombre al grupo y elige al menos dos sesiones")
                    if nombre in sesiones:
                        raise ValueError("El grupo no puede llamarse igual que una sesión")
                    guion = b.get("guion") if b.get("guion") in sesiones else sesiones[0]
                    gs[nombre] = {"sesiones": sesiones, "guion": guion}
                motor.guardar_grupos(gs)
                drive.subir_grupos()
                if not b.get("borrar"):
                    motor.usar_semana(nombre)
                    motor.guardar_config({"semana": nombre})
                elif motor.S.semana == nombre:
                    otra = (motor.semanas_disponibles() or [""])[-1]
                    motor.usar_semana(otra)
                    motor.guardar_config({"semana": otra})
                self._json({"ok": True})
            elif u.path == "/api/subtitulos":
                motor.guardar_lineas_editadas(q["pieza"][0], self._cuerpo()["lineas"])
                self._json({"ok": True})
            elif u.path == "/api/plantilla":
                b = self._cuerpo()
                pid = b["id"]
                if not re.fullmatch(r"[a-z0-9_]{1,40}", pid):
                    raise ValueError("Id de plantilla no válido")
                # los videos que la usan se cuentan antes de borrarla (después ya usaban otra y la lista salía vacía)
                usaban = codigos_armados(lambda c: motor.id_plantilla_de(c) == pid)
                if b.get("borrar"):
                    propias = {k: v for k, v in motor.CONFIG.get("plantillas_propias", {}).items() if k != pid}
                    cambio = {"plantillas_propias": propias}
                    if motor.CONFIG.get("plantilla_activa") == pid:  # era la predeterminada: sus videos quedan por rearmar
                        cambio["plantilla_activa_cambio"] = time.time()
                        if pid not in motor.CONFIG.get("plantillas_sub", {}):  # era propia: se vuelve a la de por defecto
                            cambio["plantilla_activa"] = motor.CONFIG["subtitulos"].get("plantilla")
                    motor.guardar_config(cambio)
                    motor.guardar_config({"plantillas_borradas_cambio": {**(motor.CONFIG.get("plantillas_borradas_cambio") or {}), pid: time.time()}})
                    return self._json({"ok": True, "codigos": usaban})
                if b.get("datos"):  # una de fábrica editada se guarda encima (con «Restaurar» vuelve a la original)
                    if not str(b["datos"].get("nombre") or "").strip():
                        raise ValueError("Ponle un nombre a la plantilla")
                    nueva = motor.limpiar_plantilla(b["datos"])
                    sin_fecha = lambda d: {k: v for k, v in (d or {}).items() if k != "editado"}
                    if sin_fecha(nueva) != sin_fecha(motor.plantillas().get(pid)):  # igual que antes: no se toca (ni pide rearmar)
                        datos = {**nueva, "editado": time.time()}  # así los videos que la usan quedan «por re-armar»
                        motor.guardar_config({"plantillas_propias": {**motor.CONFIG.get("plantillas_propias", {}), pid: datos}})
                    elif not b.get("predeterminada"):
                        return self._json({"ok": True, "codigos": [], "sin_cambios": True})
                if b.get("predeterminada"):
                    motor.guardar_config({"plantilla_activa": pid, "plantilla_activa_cambio": time.time()})
                    motor.fijar_plantilla_sesion(pid)
                # los videos que usan esta plantilla (para ofrecer re-armarlos)
                usan = codigos_armados(lambda c: motor.id_plantilla_de(c) == pid)
                self._json({"ok": True, "codigos": usan})
            elif u.path == "/api/elemento":
                nombre = re.sub(r"[^\w.-]", "_", q["nombre"][0])[-80:]
                if not nombre.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    raise ValueError("Sube una imagen PNG, JPG, WEBP o un GIF")
                datos = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                # que de verdad sea una imagen (antes se aceptaba cualquier archivo con la extensión cambiada)
                if not (datos[:8] == b"\x89PNG\r\n\x1a\n" or datos[:3] == b"\xff\xd8\xff" or datos[:6] in (b"GIF87a", b"GIF89a")
                        or (datos[:4] == b"RIFF" and datos[8:12] == b"WEBP")):
                    raise ValueError("Ese archivo no es una imagen PNG, JPG, WEBP ni GIF (aunque se llame así)")
                if (motor.ELEMENTOS / nombre).exists() and not q.get("reemplazar"):  # se pregunta antes de pisar uno igual
                    return self._json({"ok": False, "existe": True, "archivo": nombre, "error": f"Ya hay un elemento «{nombre}»"})
                motor.ELEMENTOS.mkdir(parents=True, exist_ok=True)
                (motor.ELEMENTOS / nombre).write_bytes(datos)
                self._json({"ok": True, "archivo": nombre})
            elif u.path == "/api/lut_borrar":  # quitar un LUT subido (si no está en uso)
                nombre = Path(str(self._cuerpo().get("nombre") or "")).name
                f = motor.ruta_lut(nombre)
                if not f:
                    raise ValueError("Ese LUT ya no está")
                if motor.estilo_edicion().get("lut") == nombre or any((e.get("estilo") or {}).get("lut") == nombre for e in motor.ediciones().values()):
                    raise ValueError("Ese LUT está en uso: primero apaga «Color (LUT)» o elige otro")
                f.unlink()
                self._json({"ok": True, "luts": motor.luts()})
            elif u.path == "/api/lut":  # LUT de color para Estilo de edición
                nombre = motor.guardar_lut(q["nombre"][0][-80:], self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                self._json({"ok": True, "archivo": nombre, "luts": motor.luts()})
            elif u.path == "/api/fuente":
                import fuentes
                nombre = re.sub(r"[^\w.-]", "_", q["nombre"][0])
                if not nombre.lower().endswith((".ttf", ".otf")):
                    raise ValueError("Sube un archivo .ttf u .otf")
                fuentes.PROPIAS.mkdir(parents=True, exist_ok=True)
                destino = fuentes.PROPIAS / nombre
                if destino.exists() and not q.get("reemplazar"):
                    return self._json({"ok": False, "existe": True, "error": f"Ya hay una fuente «{nombre}»"})
                tmp = fuentes.PROPIAS / f".{nombre}.part"
                tmp.write_bytes(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                try:
                    fuentes.Fuente(tmp)
                except Exception:
                    tmp.unlink(missing_ok=True)
                    raise ValueError("Ese archivo no es una fuente que la sala pueda leer (la que tenías no se tocó)")
                os.replace(tmp, destino)
                fuentes.cargar.cache_clear()
                self._json({"ok": True, "archivo": nombre})
            elif u.path == "/api/portada":
                b = self._cuerpo()
                c = next((x for x in motor.combinaciones() if x["codigo"] == b.get("codigo")), None)
                if not c:
                    raise ValueError("Ese video ya no está en la sesión")
                for k, lo, hi in (("tamano", 50, 220), ("borde", 0, 30)):  # lo mismo que deja el formulario
                    if b.get(k) not in (None, ""):
                        try:
                            b[k] = max(lo, min(hi, float(b[k])))
                        except (TypeError, ValueError):
                            raise ValueError(f"El {'tamaño' if k == 'tamano' else 'borde'} de la portada tiene que ser un número")
                if isinstance(b.get("titulo"), str):
                    b["titulo"] = b["titulo"].strip()[:120]
                if not b.get("titulo") and not b.get("solo_imagen"):
                    raise ValueError("Escribe el título de la portada (o marca «Solo imagen»)")
                motor.guardar_ajustes_portada(b["codigo"], b)
                motor.hacer_portada(c)  # un segundo: no hace falta re-armar el video
                self._json({"ok": True, "portada": url_media(motor.S.portadas / f"{c['archivo']}.jpg")})
            elif u.path == "/api/ajustes_video":
                b = self._cuerpo()
                if "silenciar" in b:  # pistas silenciadas de un video (voz, sonidos, música): solo eso
                    mudo = {k: True for k in ("voz", "sonidos", "musica") if (b.get("silenciar") or {}).get(k)}
                    motor.guardar_ajustes_video(b["codigo"], {"silenciar": mudo or None})
                    if b.get("armar"):
                        lanzar("armar_sel", extra={"codigos": [b["codigo"]]}, en_cola=True)
                    return self._json({"ok": True, "silenciar": mudo})
                if b.get("codigos"):  # varios videos elegidos: solo cambia su plantilla (la altura de cada uno se queda)
                    for cod in b["codigos"]:
                        motor.guardar_ajustes_video(cod, {"plantilla": b.get("plantilla") or None})
                    if b.get("armar"):
                        cola = lanzar("armar_sel", extra={"codigos": b["codigos"]}, en_cola=True)
                        return self._json({"ok": True, "codigos": b["codigos"], "en_cola": bool(cola)})
                    return self._json({"ok": True, "codigos": b["codigos"]})
                codigos = [b["codigo"]]
                if b.get("todo_el_cuerpo"):  # la misma plantilla y altura para todos los videos de ese cuerpo
                    cuerpo = next(c["cuerpo"] for c in motor.combinaciones() if c["codigo"] == b["codigo"])
                    codigos = [c["codigo"] for c in motor.combinaciones() if c["cuerpo"] == cuerpo]
                for cod in codigos:
                    motor.guardar_ajustes_video(cod, {"plantilla": b.get("plantilla") or None,
                                                      "ajustes_sub": b.get("ajustes_sub") or None})
                cola = bool(b.get("armar") and lanzar("armar_sel", extra={"codigos": codigos}, en_cola=True))
                self._json({"ok": True, "codigos": codigos, "en_cola": cola})
            elif u.path == "/api/estado_video":
                b = self._cuerpo()
                if b.get("estado") not in ("pendiente", "editando", "listo"):
                    raise ValueError("Estado no válido")
                for cod in b.get("codigos") or [b["codigo"]]:
                    motor.guardar_ajustes_video(cod, {"estado": b["estado"]})
                self._json({"ok": True})
            elif u.path == "/api/descartar":
                b = self._cuerpo()
                hechos = motor.descartar_clips(b.get("rels") or [], bool(b.get("recuperar")))
                if drive.estado()["activo"] and hechos:
                    def mover_en_drive(lista=hechos):
                        for sesion, viejo, nuevo in lista:
                            try:
                                drive.mover(motor.S.en_drive(sesion), viejo, nuevo)
                            except Exception as e:
                                print("No se pudo mover en Drive:", e)
                        for sesion in {h[0] for h in lista}:
                            drive.subir(motor.S.en_drive(sesion), ["asignaciones.json"], avance=lambda m: None)
                    threading.Thread(target=mover_en_drive, daemon=True).start()
                self._json({"ok": True, "movidos": len(hechos)})
            elif u.path == "/api/renombrar_grupo":
                if TRABAJO["activo"]:
                    raise RuntimeError("Espera a que termine el trabajo en curso")
                b = self._cuerpo()
                nuevo = motor.renombrar_grupo(str(b["viejo"]), str(b["nuevo"]))
                drive.subir_grupos()
                drive.subir_archivadas()
                self._json({"ok": True, "nuevo": nuevo})
            elif u.path == "/api/producir":
                b = self._cuerpo()
                lanzar("producir", extra={k: bool(b.get(k)) for k in ("renombrar", "exportar", "saltar_errores", "panel")})
                self._json({"ok": True})
            elif u.path == "/api/metricas":  # vistas y retención de cada video (para saber qué hook funciona)
                m = motor.guardar_metricas(self._cuerpo().get("datos") or {})
                drive.subir_en_segundo_plano(motor.S.drive, ["metricas.json", "Guion"])
                self._json({"ok": True, "metricas": m, "ranking": motor.ranking()})
            elif u.path == "/api/guion_texto":  # editar lo que dice una pieza: Drive, panel y teleprompter
                b = self._cuerpo()
                pz = motor.editar_texto_pieza(str(b["pieza"]), b.get("texto", ""), b.get("partes"))
                drive.subir_en_segundo_plano(motor.S.drive, ["Guion"])
                if motor.CONFIG.get("panel", {}).get("token"):
                    threading.Thread(target=lambda: _sin_error(panel.subir_guion), daemon=True).start()
                self._json({"ok": True, "pieza": pz})
            elif u.path == "/api/sonidos_nivel":  # normalizar el volumen de los efectos (página Sonidos)
                b = self._cuerpo()
                aj = {"normalizar": bool(b.get("normalizar", True)), "objetivo_lufs": max(-30.0, min(-10.0, float(b.get("objetivo_lufs", -20))))}
                motor.guardar_config({"sonidos_nivel": aj, "estilo_cambio": time.time()})
                self._json({"ok": True, **aj, "codigos": codigos_armados()})
            elif u.path == "/api/estilo_edicion":  # zoom alternado, zoom en el hook y fundido (Plantillas → Estilo de edición)
                b = self._cuerpo()
                d = motor.estilo_edicion()  # lo que no venga se queda como estaba
                num = lambda k, lo, hi: max(lo, min(hi, float(b.get(k, d[k]))))
                aj = {"zoom_alterno": bool(b.get("zoom_alterno", d["zoom_alterno"])),
                      "zoom_tipo": b.get("zoom_tipo", d["zoom_tipo"]) if b.get("zoom_tipo", d["zoom_tipo"]) in ("punch", "in", "out", "mezcla") else "punch",
                      "zoom_fuerza": num("zoom_fuerza", 0.04, 0.35), "zoom_cada": 1 if int(b.get("zoom_cada", d["zoom_cada"])) == 1 else 2,
                      "zoom_hook": bool(b.get("zoom_hook", d["zoom_hook"])), "zoom_hook_fuerza": num("zoom_hook_fuerza", 0.04, 0.3),
                      "fundido": num("fundido", 0.05, 1.0),
                      "limpieza_voz": b.get("limpieza_voz", d["limpieza_voz"]) if b.get("limpieza_voz", d["limpieza_voz"]) in ("apagada", *motor.LIMPIEZAS_VOZ) else "suave",
                      "seguir_cara": bool(b.get("seguir_cara", d["seguir_cara"])),
                      "lut": b.get("lut", d["lut"]) if motor.ruta_lut(b.get("lut", d["lut"])) else "",
                      "lut_fuerza": num("lut_fuerza", 0.1, 1.0),
                      "efecto": b.get("efecto", d["efecto"]) if b.get("efecto", d["efecto"]) in ("ninguno", *motor.EFECTOS_VIDEO) else "ninguno",
                      "transicion": b.get("transicion", d["transicion"]) if b.get("transicion", d["transicion"]) in motor.TRANSICIONES else "flash",
                      "transicion_donde": b.get("transicion_donde", d["transicion_donde"]) if b.get("transicion_donde", d["transicion_donde"]) in motor.DONDE_TRANSICION else "cta"}
                motor.guardar_config({"estilo_edicion": aj, "estilo_cambio": time.time()})
                self._json({"ok": True, **aj, "codigos": codigos_armados()})
            elif u.path == "/api/silencios":  # cortar solo el silencio del inicio y del final de cada clip
                b = self._cuerpo()
                aj = {"auto": bool(b.get("auto", True)), "margen": max(0.0, min(0.5, float(b.get("margen", 0.08))))}
                motor.guardar_config({"silencios": aj})
                self._json({"ok": True, **aj})
            elif u.path == "/api/cortes_voz":  # cortes por lo que se dijo: aplicar solos, largo de pausa y respiro
                b = self._cuerpo()
                antes = motor.ajustes_cortes_voz()
                aj = {"auto": bool(b.get("auto", antes["auto"])),
                      "pausa": max(0.25, min(3.0, float(b.get("pausa", antes["pausa"])))),
                      "respiro": max(0.0, min(0.4, float(b.get("respiro", antes["respiro"]))))}
                ritmo = {**motor.CONFIG.get("ritmo", {}), "acelerar": bool(b.get("acelerar", motor.ajustes_ritmo()["acelerar"]))}
                motor.guardar_config({"cortes_voz": aj, "ritmo": ritmo, "cortes_voz_cambio": time.time()})
                self._json({"ok": True, **aj, "acelerar": ritmo["acelerar"]})
            elif u.path == "/api/ediciones":  # ediciones A, B, C…: crear, cambiar su estilo/música/plantilla o borrar
                b = self._cuerpo()
                codigos = []
                if b.get("accion") == "crear":
                    letra = motor.crear_edicion(b.get("nombre"), b.get("copiar_de") or "A")
                elif b.get("accion") == "borrar":
                    letra = b["letra"]
                    motor.borrar_edicion(letra)
                else:
                    letra = b["letra"]
                    codigos = motor.guardar_edicion(letra, {k: b[k] for k in ("nombre", "estilo", "musica", "plantilla", "ritmo") if k in b})
                self._json({"ok": True, "letra": letra, "ediciones": motor.ediciones(), "plan": motor.plan_sesion(), "codigos": codigos})
            elif u.path == "/api/version_cuerpo":
                # el motor de cuerpo: otra versión de un cuerpo con los mismos hooks y CTA
                #  · modo «edicion»: el mismo material, editado aparte (C1-H1-CTA1-B…)
                #  · modo «toma»: otra toma completa del cuerpo (C1~A2), con los hooks y el CTA de siempre
                b = self._cuerpo()
                cuerpo = str(b.get("cuerpo") or "")
                if b.get("modo") == "toma":
                    pieza = motor.siguiente_toma_de_cuerpo(cuerpo)
                    motor.guardar_asignacion(str(b.get("rel") or ""), pieza)
                    motor.marcar_a_mano(str(b.get("rel") or ""))
                    subir_asignaciones()
                    lanzar("procesar", en_cola=True)
                    cods = [c["codigo"] for c in motor.combinaciones() if c["cuerpo"] == pieza]
                    if cods:
                        lanzar("armar_sel", extra={"codigos": cods}, en_cola=True)
                    self._json({"ok": True, "pieza": pieza, "codigos": cods})
                else:
                    letra, cods = motor.version_de_cuerpo(cuerpo, b.get("edicion") or "A")
                    if cods:
                        lanzar("armar_sel", extra={"codigos": cods}, en_cola=True)
                    self._json({"ok": True, "edicion": letra, "codigos": cods})
            elif u.path == "/api/plan":  # «Plan de la sesión»: qué ediciones se arman de cada cuerpo
                b = self._cuerpo()
                plan = motor.guardar_plan(b.get("plan") or {})
                todos = list(motor.combinaciones())
                faltan = [c["codigo"] for c in todos if not (motor.S.salida / f"{c['archivo']}.mp4").exists()]
                if b.get("armar") and faltan:
                    lanzar("armar_sel", extra={"codigos": faltan})
                self._json({"ok": True, "plan": plan, "faltan": faltan, "total": len(todos)})
            elif u.path == "/api/sonido":
                b = self._cuerpo()
                if b.get("accion") not in ("agregar", "quitar", "cambiar", "mover", "ajustar"):
                    raise ValueError("Acción no válida")
                pieza = motor.clave_pieza(b["pieza"], b.get("edicion") if b.get("edicion") in motor.ediciones() else "A")
                codigos = motor.editar_sonido(pieza, b["accion"], b["t"], b["cat"], b.get("id"), b.get("nuevaCat"), b.get("t_nuevo"),
                                              b.get("desde"), b.get("largo"), b.get("vol"), b.get("vel"))
                self._json({"ok": True, "codigos": codigos})
            elif u.path == "/api/elegir_carpeta":  # cuadro de Windows para elegir dónde exportar
                carpeta = elegir_carpeta(str(self._cuerpo().get("inicial") or ""))
                self._json({"ok": True, "carpeta": carpeta, **(revisar_destino(carpeta) if carpeta else {})})
            elif u.path == "/api/destino_exporte":  # ¿existe, se puede guardar, ya hay un exporte de hoy?
                self._json({"ok": True, **{("destino_ok" if k == "ok" else k): v for k, v in revisar_destino(str(self._cuerpo().get("carpeta") or "").strip()).items()}})
            elif u.path == "/api/drive_reintentar":
                if not drive.estado()["activo"]:
                    raise RuntimeError("La sala no está conectada a Drive")
                threading.Thread(target=lambda: _sin_error(drive.reintentar_pendientes), daemon=True).start()
                self._json({"ok": True})
            elif u.path == "/api/abrir_listos":  # abre en el explorador la carpeta de los videos exportados (V.FINALES)
                pedida = Path(str(self._cuerpo().get("carpeta") or ""))  # la última carpeta donde se exportó, si existe
                d = pedida if str(pedida) not in ("", ".") and pedida.is_dir() else motor.S.listos
                d.mkdir(parents=True, exist_ok=True)
                if sys.platform == "win32":
                    os.startfile(str(d))
                else:
                    import subprocess
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(d)])
                self._json({"ok": True})
            elif u.path == "/api/ajustes_sonido":  # editar un sonido de la biblioteca: queda así en todos los videos
                b = self._cuerpo()
                aj = motor.guardar_ajustes_sonido(str(b.get("id", "")), b.get("ajustes") or {})
                self._json({"ok": True, "ajustes": aj, "codigos": motor.videos_con_sonido(str(b.get("id", "")))})
            elif u.path == "/api/edicion":
                b = self._cuerpo()
                codigos = motor.guardar_edicion_piezas(b)
                # más los videos con sonidos cambiados en el editor (ya guardados, falta re-armarlos)
                codigos = sorted(set(codigos) | set(b.get("extra_codigos") or []))
                # solo se rearman los videos que ya estaban armados (los que nunca se armaron se arman desde Videos)
                armados = {c["codigo"] for c in motor.combinaciones() if (motor.S.salida / f"{c['archivo']}.mp4").exists()}
                codigos = [c for c in codigos if c in armados]
                # un video aprobado que cambió por dentro (comparte el cuerpo o el CTA) vuelve a «Editando» para revisarlo
                for cod in codigos:
                    if motor.ajustes_video(cod).get("estado") == "listo":
                        motor.guardar_ajustes_video(cod, {"estado": "editando"})
                cola = bool(b.get("armar") and codigos and lanzar("armar_sel", extra={"codigos": codigos}, en_cola=True))
                self._json({"ok": True, "codigos": codigos, "en_cola": cola})
            elif u.path == "/api/claude":
                b = self._cuerpo()
                if b.get("accion") == "login":
                    asistente.iniciar_sesion()
                elif b.get("accion") == "guion":
                    sesion = b.get("sesion") or motor.S.semana
                    if sesion not in motor.semanas_disponibles():
                        raise ValueError("Primero crea la sesión")
                    lanzar("guion", extra={"sesion": sesion, "pedido": str(b.get("pedido", ""))[:4000]})
                elif b.get("accion") == "revisar":
                    lanzar("revisar")
                elif b.get("accion") == "rehacer":
                    nota = str(b.get("nota", "")).strip()[:1000]
                    if len(nota) < 3:
                        raise ValueError("Escribe qué hay que cambiar en la imagen")
                    lanzar("rehacer", extra={"id": str(b.get("id", "")), "nota": nota})
                elif b.get("accion") == "rehacer_musica":
                    lanzar("rehacer_musica", extra={"id": str(b.get("id", "")), "nota": str(b.get("nota", "")).strip()[:1000]})
                elif b.get("accion") == "mas_apoyo":
                    lanzar("mas_apoyo", extra={k: max(0, min(tope, int(float(b.get(k) or 0)))) for k, tope in TOPES_APOYO})
                elif b.get("accion") == "temas":
                    lanzar("temas", extra={"cuerpos": max(1, min(10, int(b.get("cuerpos") or 5))),
                                           "publico": "dueños de mascotas" if b.get("publico") == "duenos" else "médicos veterinarios",
                                           "idea": str(b.get("idea", ""))[:1000]})
                else:
                    raise ValueError("Acción no válida")
                self._json({"ok": True})
            elif u.path == "/api/version":
                b = self._cuerpo()
                if b.get("borrar"):
                    cola = False
                    if motor.borrar_version(b["borrar"]):  # eran ajustes propios: vuelve a lo de sus piezas
                        cola = lanzar("uno", b["borrar"], en_cola=True)
                    return self._json({"ok": True, "en_cola": bool(cola)})
                vid = motor.guardar_version(b, b.get("id") or None)
                cola = bool(b.get("armar") and lanzar("uno", vid, en_cola=True))
                self._json({"ok": True, "codigo": vid, "en_cola": cola})
            elif u.path == "/api/ajustes_exportar":  # se guardan apenas se cambian en el panel de Exportar
                aj = {k: v for k, v in (self._cuerpo().get("ajustes") or {}).items() if k in motor.AJUSTES_EXPORTAR}
                motor.guardar_config({"exportar": aj})
                self._json({"ok": True})
            elif u.path == "/api/trabajo_sel":
                b = self._cuerpo()
                if b.get("ajustes"):  # los ajustes de exportación quedan guardados para la próxima vez
                    aj = {k: v for k, v in b["ajustes"].items() if k in motor.AJUSTES_EXPORTAR}
                    motor.guardar_config({"exportar": aj})
                    b["ajustes"] = aj
                extra = {"codigos": b["codigos"], "ajustes": b.get("ajustes")}
                if "drive" in b or b.get("carpeta"):  # lo que se respondió en «¿Al Drive? ¿A qué carpeta?»
                    extra.update(drive=bool(b.get("drive")), carpeta=str(b.get("carpeta") or ""), nueva=bool(b.get("nueva")),
                                 reemplazar=bool(b.get("reemplazar")), rearmar=bool(b.get("rearmar", True)))
                lanzar(b["accion"], extra=extra)
                self._json({"ok": True})
            elif u.path == "/api/asignar":
                b = self._cuerpo()
                motor.guardar_asignacion(b["rel"], b.get("pieza") or "")
                motor.marcar_a_mano(b["rel"])  # lo decidió una persona: el análisis ya no lo cambia
                subir_asignaciones()
                # la pieza con su clip nuevo se prepara y se vuelve a escuchar (lo que se dijo se actualiza);
                # con otro trabajo corriendo, queda en la cola
                procesando = bool(b.get("pieza"))
                cola = procesando and lanzar("procesar", en_cola=True)
                # con armar: después de preparar la toma nueva se rearman los videos que usan esa pieza (con sus mismos hooks)
                if procesando and b.get("armar"):
                    pz = motor.pieza_de_toma(str(b["pieza"]).upper())
                    cods = [c["codigo"] for c in motor.combinaciones() if pz in (c["hook"], c["cuerpo"], c["cta"])]
                    if cods:
                        lanzar("armar_sel", extra={"codigos": cods}, en_cola=True)
                self._json({"ok": True, "procesando": procesando, "en_cola": bool(cola)})
            elif u.path == "/api/drive":
                b = self._cuerpo()
                if b.get("accion") == "conectar":
                    drive.carpeta_id(b.get("link", ""))  # valida el link antes de abrir Google
                    lanzar("conectar", extra=b)
                elif b.get("accion") == "desconectar":
                    drive.desconectar()
                else:
                    lanzar({"bajar": "bajar", "subir": "subir_drive", "lista": "lista_drive"}[b["accion"]])
                self._json({"ok": True})
            elif u.path == "/api/numerar_hooks":
                n = motor.numerar_hooks(self._cuerpo().get("cuerpo"))
                subir_asignaciones()
                self._json({"ok": True, "asignadas": n})
            elif u.path == "/api/aceptar_sugerencias":
                # «Emparejar lo que falta» del Guion usa un mínimo más bajo; Faltantes, el de siempre (50 %)
                minimo = min(0.9, max(0.3, float(self._cuerpo().get("minimo") or 0.5)))
                r = motor.reasignar_por_guion(minimo)
                subir_asignaciones()
                self._json({"ok": True, "asignadas": r["asignados"], "cambiados": r["cambiados"], "sin_pieza": r["sin_pieza"]})
            elif u.path == "/api/quitar_texto":
                self._json({"ok": True, "codigos": motor.guardar_quitados(q["pieza"][0], self._cuerpo().get("rangos") or [])})
            elif u.path == "/api/marcas":
                motor.guardar_marcas(q["pieza"][0], self._cuerpo()["marcas"])
                self._json({"ok": True})
            elif u.path == "/api/copy":
                motor.guardar_copy(q["codigo"][0], self._cuerpo())
                self._json({"ok": True})
            elif u.path == "/api/reiniciar_sesion":
                lanzar("reiniciar")
                self._json({"ok": True})
            elif u.path == "/api/terminar":
                lanzar("terminar", extra={"borrar": bool(self._cuerpo().get("borrar"))})
                self._json({"ok": True})
            elif u.path == "/api/trabajo":
                b = self._cuerpo()
                lanzar(b["accion"], b.get("pieza"), extra={"plan": b["plan"]} if isinstance(b.get("plan"), list) else None)
                self._json({"ok": True})
            elif u.path == "/api/actualizar":
                if not actualizador.LANZADOR:
                    raise RuntimeError("En desarrollo se actualiza con git pull, no desde la app")
                if TRABAJO["activo"]:
                    raise RuntimeError("Espera a que termine el trabajo en curso")
                version = actualizador.aplicar()
                self._json({"ok": True, "version": version})
                actualizador.reiniciar_luego()
            elif u.path == "/api/notas":
                (motor.DATOS / "correcciones.md").write_text(self._cuerpo()["texto"], encoding="utf-8")
                self._json({"ok": True})
            else:
                self.send_error(404)
        except ValueError as e:  # un error normal del usuario (archivo o dato que no sirve): no es una falla de la sala
            self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    host = "0.0.0.0" if red()["activa"] else "127.0.0.1"
    RED_EN_MARCHA["si"] = host == "0.0.0.0"
    print(f"Sala de edición en http://localhost:{PUERTO}  (sesión {motor.S.semana})", flush=True)
    vigilar_ventana()
    cuenta.iniciar("Sala de edición", motor.VERSION)
    # (asegurar_config no recibe «avance»: con _sin_error fallaba siempre y no se revisaba la conexión al abrir)
    threading.Thread(target=lambda: _sin_falla(drive.asegurar_config), daemon=True).start()
    threading.Thread(target=lambda: _sin_falla(marca.bajar), daemon=True).start()  # la marca del equipo (_MARCA en Drive)
    respaldos.vigilar()  # copia de los cambios cada pocos minutos, solo si algo cambió
    if PUERTO == 8765 or os.environ.get("SALA_PROYECTO"):  # una sala de prueba no se lleva los archivos de Descargas
        vigilar_descargas()
    if PUERTO == 8765 or os.environ.get("SALA_PROYECTO"):  # una sala de prueba en otro puerto no sube nada al proyecto del equipo
        proyecto.vigilar(lambda: TRABAJO["activo"])  # la edición se comparte con las otras PCs por Drive (Actualizar proyecto)
    def subir_cada_5_min():  # lo escuchado de cada clip también se sube solo (lo editado ya sube cada minuto: proyecto)
        while True:
            time.sleep(300)
            if drive.estado()["activo"] and not TRABAJO["activo"]:
                _sin_falla(lambda: drive.subir_transcripciones(motor.S.drive, motor.S.transcripciones))
    if PUERTO == 8765 or os.environ.get("SALA_PROYECTO"):
        threading.Thread(target=subir_cada_5_min, daemon=True).start()

    def bajar_si_falta():  # la sesión abierta sin sus clips (recién instalada, o se borraron): se baja sola
        time.sleep(8)
        if (drive.estado()["activo"] and not TRABAJO["activo"] and motor.S.semana not in motor.archivadas()
                and (motor.S.es_grupo or motor.S.semana in drive.sesiones_en_drive())
                and not all(drive.bajada_completa(motor.S.en_drive(m)) for m in motor.S.miembros)):
            _sin_falla(lambda: lanzar("bajar"))
    if PUERTO == 8765 or os.environ.get("SALA_PROYECTO"):
        threading.Thread(target=bajar_si_falta, daemon=True).start()
    cuenta.latido(forzar=True)
    if red()["activa"]:
        print(f"Teleprompter para la tablet: {red()['url']}  ·  PIN {red()['pin']}")
    print(f"Lista para usar en {time.time() - INICIO:.1f} s", flush=True)
    Servidor((host, PUERTO), H).serve_forever()
