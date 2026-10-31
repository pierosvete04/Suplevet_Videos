"""Motor de edición de la sala: piezas grabadas -> videos terminados con portada y copy.

Flujo:
  1. procesar:  recorta silencios, iguala volumen y formato (1080x1920, o 1920x1080 si la sesión es 16:9)
                y transcribe cada pieza.
                Los subtítulos se guardan en subtitulos/<SEMANA>/<pieza>.json y NO se sobrescriben
                si ya existen (así se conservan las correcciones hechas a mano).
  2. armar:     une hook + cuerpo + CTA; inserta imágenes y clips en la palabra que marca el guion,
                cambia el estilo del subtítulo por palabra, pone efectos de sonido por categoría,
                música con ducking y genera la portada (JPG) de cada video.
  3. exportar:  copia cada video + portada + copy a listos/DIA_X según el calendario.

Carpeta de una semana (en Drive o en editor/grabaciones):
  SEMANA_02/
    produccion.json      guion con marcas, assets de Flow, copies y calendario (lo escribe Claude)
    hooks/C1_H1.mp4   cuerpos/C1.mp4   ctas/CTA-A.mp4   (C1_CTA1.mp4 = CTA solo de ese cuerpo)
    assets/S02_C1_IMG1.png  S02_C1_CLIP1.mp4           (imágenes y clips de Google Flow)
    listos/DIA_1/...                                    (lo que sale para publicar)

Uso:
  python motor.py procesar            procesa piezas nuevas
  python motor.py armar               arma todas las combinaciones (y sus portadas)
  python motor.py armar --pieza C1    re-arma solo los videos que usan esa pieza
  python motor.py exportar            copia a listos/DIA_X con portada y copy
  python motor.py todo                procesar + armar + exportar
  --semana SEMANA_02                  trabaja otra semana (si no, la de config.json)
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent         # código (se reemplaza al actualizar)
# Datos de esta PC (procesado, salidas, config.local…). El .exe portable usa la carpeta "datos"
# junto al ejecutable; en desarrollo, la misma carpeta del código.
DATOS = Path(os.environ.get("SALA_DATOS") or RAIZ)
DATOS.mkdir(parents=True, exist_ok=True)
VERSION = (RAIZ / "VERSION").read_text(encoding="utf-8").strip() if (RAIZ / "VERSION").exists() else "0.0.0"
CONFIG_PATH = RAIZ / "config.json"
LOCAL_PATH = DATOS / "config.local.json"  # lo propio de cada PC: carpeta de Drive, semana activa, token del panel
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
if LOCAL_PATH.exists():
    CONFIG.update(json.loads(LOCAL_PATH.read_text(encoding="utf-8")))
EXT_IMG = {".png", ".jpg", ".jpeg", ".webp"}
EXT_VID = {".mp4", ".mov", ".webm", ".m4v"}
EXT_AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"}
NULL = "NUL" if sys.platform == "win32" else "/dev/null"
NO_VIDEO = EXT_IMG | EXT_AUDIO | {".json", ".txt", ".md", ".docx", ".pdf", ".ini", ".srt", ".ass", ".zip", ".gif"}


def es_video(f):
    """Por la extensión o, si no tiene (Drive a veces la quita: «C1-H1», «C1.1»), por lo que dice el archivo."""
    suf = f.suffix.lower()
    if suf in EXT_VID:
        return True
    # «IMG_1742.MOV.4e9dfb4c.partial»: un clip que Drive todavía está bajando (trae cabecera de video, pero está a medias)
    if suf in (".partial", ".crdownload", ".tmp", ".part") or ".partial" in f.name.lower():
        return False
    if suf in NO_VIDEO or f.name.startswith(".") or not f.is_file():
        return False
    try:
        with open(f, "rb") as x:
            cab = x.read(12)
    except OSError:
        return False
    return cab[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free") or cab[:4] == b"\x1a\x45\xdf\xa3"


def nombre_clip(f):
    """El nombre sin extensión: «C1-H1.MOV» → «C1-H1», pero «C1.1» (sin extensión) sigue siendo «C1.1»."""
    return f.stem if f.suffix.lower() in EXT_VID else f.name


def _buscar_ffmpeg():
    en_path = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if all(en_path):
        return en_path
    patron = str(Path.home() / "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe")
    for f in sorted(glob.glob(patron), reverse=True):
        b = Path(f).parent
        return str(b / "ffmpeg.exe"), str(b / "ffprobe.exe")
    raise SystemExit("No encuentro ffmpeg. Corre instalar.bat o: winget install Gyan.FFmpeg")


FFMPEG, FFPROBE = _buscar_ffmpeg()
# Sonidos y música: en Drive (_BIBLIOTECA) si está configurado, si no la carpeta local.
# Sonidos: la librería del equipo (SOUND FX en Drive) si está configurada; si falta, los provisionales del código.
BIBLIO = Path(CONFIG.get("biblioteca") or (DATOS / "biblioteca" if (DATOS / "biblioteca").exists() else RAIZ / "biblioteca"))
BIBLIO_BASE = RAIZ / "biblioteca"


# ---------- Rutas de la semana activa ----------

# Carpetas que se aceptan en una sesión. La primera de cada lista es la que se crea si falta:
# es la convención del equipo (DD MM SESIÓN/Clips/Hooks, Cuerpo, CTA y V.FINALES).
CARPETAS = {
    "hook": ["Clips/Hooks", "Clips/Hook", "hooks"],
    "cuerpo": ["Clips/Cuerpo", "Clips/Cuerpos", "cuerpos"],
    "cta": ["Clips/CTA", "Clips/CTAs", "ctas"],
}
CARPETA_SUELTOS = "Clips"  # clips sin clasificar (IMG_1053.MOV…)


def _primera(base, opciones):
    for o in opciones:
        if (base / o).is_dir():
            return base / o
    return base / opciones[0]


# ---------- Grupos: varias sesiones de grabación que son una sola producción (un guion) ----------
# datos/grupos.json (copia de Drive _SALA/grupos.json):
#   {"B2B VETERINARIOS": {"sesiones": ["11／10 SESIÓN", "13／10 SESIÓN"], "guion": "11／10 SESIÓN"}}
GRUPOS_PATH = DATOS / "grupos.json"


def grupos_manuales():
    try:
        return json.loads(GRUPOS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def grupos():
    """Los grupos hechos a mano + las producciones con sus días de grabación dentro («P02 TEMA/11／10 SESIÓN»).
    Una carpeta de producción manda sobre un grupo a mano con el mismo nombre."""
    return {**grupos_manuales(), **producciones_con_dias()}


def guardar_grupos(g):
    g = {k: v for k, v in g.items() if not v.get("carpeta")}  # las de carpeta salen solas de Drive: no se guardan
    GRUPOS_PATH.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- Producciones con sus días dentro: «P02 B2B VETERINARIOS/11／10 SESIÓN», «…/13／10 SESIÓN» ----------
# Un guion puede servir para varios días de grabación: la carpeta de la producción tiene una carpeta por día
# (cada una con su Clips/). El guion, Flow, la música y V.FINALES van en la producción o en uno de los días.
# datos/drive_producciones.json (lo arma drive.actualizar_lista): {"P02 …": {"sesiones": [días], "guion": "día" o ""}}
PRODUCCIONES_PATH = DATOS / "drive_producciones.json"
SUBCARPETAS = {"guion", "guiones", "1_guion", "clips", "flow", "assets", "musica", "música", "v.finales", "videos finales", "listos"}
CARPETAS_GUION = ("Guion", "Guiones", "1_guion")  # ahí se deja el guion (PDF, Word, .md, .txt o produccion.json) y la sala lo lee


def es_dia_de_grabacion(nombre):
    n = nombre.upper()
    return (nombre.lower() not in SUBCARPETAS and not n.startswith(("_", "."))
            and (bool(re.match(r"^\d{1,2}\D+\d{1,2}", n)) or "SESI" in n or n.startswith(("SEMANA", "DIA", "DÍA", "GRAB"))))


def dias_de_produccion(carpeta):
    """En disco: los días de grabación dentro de una carpeta de producción y dónde está el guion."""
    dias = sorted(d.name for d in carpeta.iterdir() if d.is_dir()
                  and (es_dia_de_grabacion(d.name) or (d.name.lower() not in SUBCARPETAS and (d / "Clips").is_dir())))
    if not dias:
        return None
    if any((carpeta / x).is_dir() for x in CARPETAS_GUION):
        guion = ""
    else:
        guion = next((d for d in dias if any((carpeta / d / x).is_dir() for x in CARPETAS_GUION)), dias[0])
    return {"sesiones": dias, "guion": guion}


def producciones_con_dias():
    out = {}
    if CONFIG.get("drive", {}).get("activo"):
        try:
            out = json.loads(PRODUCCIONES_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            pass
    base = Path(CONFIG.get("carpeta_trabajo") or DATOS / "grabaciones")
    if base.is_dir():  # también las que ya están en esta PC (o sin Drive, las de la carpeta de trabajo)
        for d in base.iterdir():
            if d.is_dir() and PRODUCCION.match(d.name):
                local = dias_de_produccion(d)
                if local:
                    previo = out.get(d.name) or {}
                    out[d.name] = {"sesiones": sorted({*previo.get("sesiones", []), *local["sesiones"]}),
                                   "guion": previo["guion"] if "guion" in previo else local["guion"]}
    return {k: {**v, "carpeta": k} for k, v in out.items() if v.get("sesiones")}


def mudar_a_produccion(prod, dia, cache):
    """Un día que estaba suelto en Drive y ahora está dentro de su producción: la copia de esta PC se mueve
    (no se vuelve a bajar) y se actualiza lo que recordaba la ruta vieja, para no volver a procesar nada."""
    viejo, nuevo = cache / dia, cache / prod / dia
    if not viejo.is_dir() or nuevo.exists():
        return False
    nuevo.parent.mkdir(parents=True, exist_ok=True)
    viejo.rename(nuevo)
    for ext in (".json", ".reemplazos.json"):  # lo identificado a mano, con el nombre nuevo
        a, b = DATOS / "asignaciones" / f"{dia}{ext}", DATOS / "asignaciones" / prod / f"{dia}{ext}"
        if a.exists() and not b.exists():
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(a, b)
    a_txt, b_txt = str(viejo), str(nuevo)

    def cambiar(x):
        if isinstance(x, str):
            return b_txt + x[len(a_txt):] if x == a_txt or x.startswith(a_txt + os.sep) else x
        if isinstance(x, list):
            return [cambiar(y) for y in x]
        if isinstance(x, dict):
            return {k: cambiar(v) for k, v in x.items()}
        return x
    for sub in ("procesado", "recetas", "subtitulos"):
        for f in (DATOS / sub / prod).glob("*.json") if (DATOS / sub / prod).is_dir() else []:
            try:
                texto = f.read_text(encoding="utf-8")
                if dia not in texto:
                    continue
                d = json.loads(texto)
            except (ValueError, OSError):
                continue
            d2 = cambiar(d)
            if d2 != d:
                f.write_text(json.dumps(d2, ensure_ascii=False, indent=None if sub == "procesado" else 1), encoding="utf-8")
    return True


def nombre_sesion_valido(nombre):
    """El mismo criterio para crear, abrir y renombrar sesiones y grupos: cualquier letra, número, espacio y signos
    comunes, sin los que Windows no deja en un nombre de carpeta. Antes un grupo «P03 PIEL + PELAJE» se creaba pero
    después no se podía abrir."""
    nombre = str(nombre or "")
    return 1 <= len(nombre) <= 60 and not nombre.startswith(".") and not re.search(r'[\\/:*?"<>|\x00-\x1f]', nombre)


def renombrar_grupo(viejo, nuevo):
    """Cambia el nombre de un grupo (la producción). Las carpetas de Drive de sus días no cambian; en esta PC se
    mueven sus ediciones, procesados y videos armados a carpetas con el nombre nuevo. Los archivos de video
    conservan su prefijo (el del día que tiene el guion)."""
    nuevo = " ".join(str(nuevo).split()).upper()
    g = grupos()
    if viejo not in g:
        raise ValueError("Ese grupo no existe")
    if g[viejo].get("carpeta"):
        raise ValueError("Esta producción es una carpeta de Drive: cámbiale el nombre allá")
    if not nombre_sesion_valido(nuevo):
        raise ValueError('Nombre no válido (sin / \\ : * ? " < > |)')
    if nuevo in g or nuevo in semanas_disponibles():
        raise ValueError("Ya hay una sesión o un grupo con ese nombre")
    for sub in ("procesado", "subtitulos", "salidas", "recetas", "transcripciones"):
        a, b = DATOS / sub / viejo, DATOS / sub / nuevo
        if a.exists() and not b.exists():
            a.rename(b)
    g[nuevo] = g.pop(viejo)
    guardar_grupos(g)
    arch = archivadas()
    if viejo in arch:
        guardar_archivadas([nuevo if x == viejo else x for x in arch])
    if CONFIG.get("semana") == viejo:
        guardar_config({"semana": nuevo})
    if S.semana == viejo:
        usar_semana(nuevo)
    return nuevo


# ---------- Sesiones archivadas: ya terminadas, no salen en las listas (se encuentran buscando) ----------
# datos/archivadas.json (copia de Drive _SALA/archivadas.json): ["5／10 SESIÓN", …]
ARCHIVADAS_PATH = DATOS / "archivadas.json"


def archivadas():
    try:
        return list(json.loads(ARCHIVADAS_PATH.read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError):
        return []


def guardar_archivadas(lista):
    ARCHIVADAS_PATH.write_text(json.dumps(sorted(set(lista)), ensure_ascii=False, indent=1), encoding="utf-8")


# «Clips solo en esta PC» (de cada PC, en config.local): la sala no sube los clips grabados de esa sesión ni los saca
# de la PC aunque no estén en Drive. Lo editado sí se comparte. Los clips suben cuando se pide («Subir mis clips»).
def clips_solo_aqui(sesion=None):
    return (sesion or S.semana) in (CONFIG.get("clips_solo_aqui") or [])


def poner_clips_solo_aqui(activo, sesion=None):
    s = sesion or S.semana
    guardar_config({"clips_solo_aqui": [x for x in CONFIG.get("clips_solo_aqui") or [] if x != s] + ([s] if activo else [])})


# Producciones nuevas: «P07 ARTICULACIONES» (número + tema). Se nombran al escribir el guion,
# cuando todavía no se sabe qué día se graba; los clips de todos los días de grabación van dentro.
PRODUCCION = re.compile(r"^P(\d{1,3})\b", re.I)


def es_nombre_sesion(nombre):
    n = nombre.upper()
    return "SESI" in n or n.startswith("SEMANA") or bool(PRODUCCION.match(n))


def siguiente_produccion():
    nums = [int(m.group(1)) for m in (PRODUCCION.match(x) for x in semanas_disponibles() + archivadas()) if m]
    return f"P{(max(nums) + 1) if nums else 1:02d}"


class Rutas:
    def __init__(self, semana):
        self.semana = semana
        base = Path(CONFIG.get("carpeta_trabajo") or DATOS / "grabaciones")
        self.base = base
        g = grupos().get(semana)
        self.es_grupo = bool(g)
        self.miembros = list(g["sesiones"]) if g else [semana]
        # producción con sus días dentro: los días se buscan en su carpeta («P02 …/11／10 SESIÓN»)
        self.carpeta_prod = g.get("carpeta") if g else None
        self.raiz = base / self.carpeta_prod if self.carpeta_prod else base
        # sesión principal: ahí viven el guion, Flow, la música y V.FINALES (en un grupo, la que tiene el guion;
        # en una producción con días, la producción misma o el día que tiene el guion). Ruta dentro de la carpeta de trabajo.
        if self.carpeta_prod:
            self.drive = f"{self.carpeta_prod}/{g['guion']}" if g.get("guion") else self.carpeta_prod
        else:
            self.drive = (g.get("guion") or g["sesiones"][0]) if g else semana
        self.principal = base / self.drive
        # en un grupo, los clips de todas sus sesiones se ven desde la carpeta de arriba («11／10 SESIÓN/Clips/…»)
        self.grab = self.raiz if g else base / semana
        self.assets = _primera(self.principal, ["Flow", "assets"])
        self.listos = _primera(self.principal, ["V.FINALES", "VIDEOS FINALES", "listos"])
        self.musica = _primera(self.principal, ["Musica", "Música", "musica"])
        self.asignaciones = self.principal / "asignaciones.json"
        self.proc = DATOS / "procesado" / semana      # local: pesado, no se sincroniza
        self.subs = DATOS / "subtitulos" / semana
        self.salida = DATOS / "salidas" / semana
        self.portadas = DATOS / "salidas" / semana / "portadas"
        self.recetas = DATOS / "recetas" / semana
        self.transcripciones = DATOS / "transcripciones" / semana
        self.copies_editados = DATOS / "subtitulos" / semana / "_copies_editados.json"
        con_guion = Path(self.drive).name  # el prefijo sale de la carpeta que tiene el guion
        fecha = re.match(r"^(\d{1,2})\D+(\d{1,2})", con_guion)   # "13 10 SESIÓN" -> 1310
        prod = PRODUCCION.match(con_guion)                         # "P07 ARTICULACIONES" -> P07
        n = re.search(r"(\d+)", con_guion)
        self.prefijo = (f"P{int(prod.group(1)):02d}" if prod
                        else f"{int(fecha.group(1)):02d}{int(fecha.group(2)):02d}" if fecha
                        else f"S{int(n.group(1)):02d}" if n else "S00")

    @property
    def guion(self):  # se busca cada vez: la carpeta Guiones puede aparecer después (la crea el equipo en Drive)
        return _primera(self.principal, list(CARPETAS_GUION))

    def carpeta(self, tipo):
        return _primera(self.principal, CARPETAS[tipo])

    def carpetas_sesiones(self):
        return [self.raiz / m for m in self.miembros]

    def en_drive(self, sesion):
        """Ruta en Drive (y en la carpeta de trabajo) de una sesión del grupo: «P02 …/11／10 SESIÓN» o «11／10 SESIÓN»."""
        return f"{self.carpeta_prod}/{sesion}" if self.carpeta_prod else sesion


S = Rutas(CONFIG["semana"])


def usar_semana(semana):
    global S, _prod_cache
    S = Rutas(semana)
    _prod_cache = None
    if S.es_grupo and not S.carpeta_prod:  # una producción con días no hereda de sesiones sueltas con el mismo nombre
        heredar_de_sesiones()


def heredar_de_sesiones():
    """Un grupo aprovecha lo ya hecho en sus sesiones (piezas procesadas, subtítulos corregidos, lo escuchado),
    para no volver a transcribir. Solo copia lo que el grupo todavía no tiene."""
    for m in S.miembros:
        for origen, destino in ((DATOS / "procesado" / m, S.proc), (DATOS / "subtitulos" / m, S.subs)):
            if not origen.is_dir():
                continue
            destino.mkdir(parents=True, exist_ok=True)
            for f in origen.iterdir():
                if f.is_file() and not f.name.startswith("_") and not (destino / f.name).exists():
                    shutil.copy2(f, destino / f.name)
        t = DATOS / "transcripciones" / m
        if t.is_dir():
            S.transcripciones.mkdir(parents=True, exist_ok=True)
            pre = re.sub(r"[^\w.-]", "_", m + "/")
            for f in t.iterdir():
                if f.is_file() and not (S.transcripciones / (pre + f.name)).exists():
                    shutil.copy2(f, S.transcripciones / (pre + f.name))


def resumen_sesiones():
    """Para la pantalla de entrada: cada sesión con lo que ya tiene en esta PC (sin abrirla ni bajar nada):
    si tiene guion, cuántos videos hay armados, cuándo se trabajó por última vez aquí y si ya está en esta PC."""
    arch, grs = set(archivadas()), grupos()
    out = []
    for n in semanas_disponibles():
        subs, sal = DATOS / "subtitulos" / n, DATOS / "salidas" / n
        tiempos = [f.stat().st_mtime for d in (subs, sal) if d.is_dir() for f in d.iterdir() if f.is_file()]
        carpetas = [S.base / n] + ([S.base / g["carpeta"]] if (g := grs.get(n)) and g.get("carpeta") else [])
        if grs.get(n):  # los días de grabación de un grupo (sueltos, o dentro de la carpeta de la producción)
            raiz = S.base / grs[n]["carpeta"] if grs[n].get("carpeta") else S.base
            carpetas += [raiz / x for x in grs[n].get("sesiones", [])]
        en_pc = any(c.is_dir() for c in carpetas)
        guion = any((c / x / "produccion.json").exists() or ((c / x).is_dir() and any(f.suffix.lower() in (".docx", ".pdf") for f in (c / x).iterdir()))
                    for c in carpetas for x in CARPETAS_GUION)
        m_ = re.match(r"^P\d+\s+(ADS|ORG)\b", n, re.I)
        # cuánto ocupa en esta PC: la carpeta de la sesión (clips, guion, imágenes) + lo preparado y lo armado aquí
        peso = sum(_peso_carpeta(c) for c in carpetas) + _peso_carpeta(DATOS / "procesado" / n) + _peso_carpeta(sal)
        out.append({"nombre": n, "archivada": n in arch, "grupo": bool(grs.get(n)), "en_pc": en_pc, "guion": guion, "peso": peso,
                    "miniatura": bool(sal.is_dir() and next(sal.glob("*.mp4"), None)),
                    "tipo": ("ads" if m_.group(1).upper() == "ADS" else "organico") if m_ else None,
                    "videos": len(list(sal.glob("*.mp4"))) if sal.is_dir() else 0,
                    "ultima": max(tiempos) if tiempos else None})
    return out


def _peso_carpeta(d):
    """Bytes de todo lo que hay dentro (0 si no existe)."""
    total = 0
    try:
        for raiz, _, archivos in os.walk(d):
            for a in archivos:
                try:
                    total += os.path.getsize(os.path.join(raiz, a))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def miniatura_sesion(nombre):
    """Una imagen de un video armado de la sesión, para su cuadro en la pantalla de sesiones (en caché)."""
    sal = DATOS / "salidas" / nombre
    videos = sorted(sal.glob("*.mp4"), key=lambda f: f.stat().st_mtime) if sal.is_dir() else []
    if not videos:
        return None
    destino = DATOS / "miniaturas" / f"{re.sub(r'[^\w.-]+', '_', nombre)}.jpg"
    if destino.exists() and destino.stat().st_mtime >= videos[-1].stat().st_mtime:
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    run([FFMPEG, "-hide_banner", "-y", "-ss", "1.5", "-i", str(videos[-1]), "-frames:v", "1",
         "-vf", "scale=360:-2", "-q:v", "5", str(destino)])
    return destino


def vista_previa_clip(rel):
    """Una imagen del clip (al segundo 1) para que su cuadro no se vea negro antes de darle play (en caché)."""
    f = S.grab / rel
    if not f.is_file():
        return None
    st = f.stat()
    destino = DATOS / "miniaturas" / "clips" / S.semana / f"{hashlib.md5(f'{rel}-{st.st_size}-{int(st.st_mtime)}'.encode()).hexdigest()}.jpg"
    if destino.exists():
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp.jpg")
    for seg in ("1", "0"):  # un clip de menos de 1 s: el primer cuadro
        try:
            run([FFMPEG, "-hide_banner", "-y", "-ss", seg, "-i", str(f), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "6", str(tmp)])
        except RuntimeError:
            continue
        if tmp.exists() and tmp.stat().st_size:
            os.replace(tmp, destino)
            return destino
    return None


TIRA_CUADROS = 30  # cuántos cuadros lleva la tira de una pieza (la pantalla los reparte a lo largo de la pieza)


def tira_pieza(nombre):
    """Los cuadros de la pieza preparada, uno al lado del otro en una sola imagen, para la pista «Video» de la línea de
    tiempo de Piezas: así se ve el clip (también el de un hook o un CTA de un solo clip). En caché."""
    f = S.proc / f"{nombre}.mp4"
    if not f.is_file():
        return None
    st = f.stat()
    clave = hashlib.md5(f"{nombre}-{st.st_size}-{int(st.st_mtime)}-{TIRA_CUADROS}".encode()).hexdigest()
    destino = DATOS / "miniaturas" / "tiras" / S.semana / f"{clave}.jpg"
    if destino.exists():
        return destino
    dur = duracion(f) or 0
    if dur <= 0:
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp.jpg")
    # un cuadro al medio de cada tramo (fps = cuadros / duración), recortado al centro (1:2, sirve en vertical y en
    # horizontal: en la pista cada cuadro es angosto) y todos en una fila
    run([FFMPEG, "-hide_banner", "-y", "-i", str(f), "-vf",
         f"fps={TIRA_CUADROS / dur:.6f}:start_time={dur / TIRA_CUADROS / 2:.3f},crop=w=min(iw\\,ih/2):h=ih,scale=48:96,"
         f"tile={TIRA_CUADROS}x1",
         "-frames:v", "1", "-q:v", "6", str(tmp)])
    if not tmp.exists() or not tmp.stat().st_size:
        return None
    os.replace(tmp, destino)
    return destino


def borrar_sesion_local(nombre, carpetas_drive):
    """Borra de esta PC todo lo de una sesión: su copia de Drive (clips, Flow, música, guion), las piezas preparadas,
    los videos armados, lo escuchado y lo editado. Las copias de seguridad (datos/respaldos) se quedan. Devuelve bytes."""
    carpetas_drive = [c for c in carpetas_drive if c and not c.startswith((".", "/")) and ".." not in Path(c).parts]
    if not nombre_sesion_valido(nombre) or not carpetas_drive:  # con un nombre vacío se borraría la carpeta entera
        raise ValueError("Nombre de sesión no válido")
    base = Path(CONFIG.get("carpeta_trabajo") or DATOS / "grabaciones")
    asig = DATOS / "asignaciones"
    carpetas = [base / c for c in carpetas_drive] + [asig / c for c in carpetas_drive]
    carpetas += [DATOS / d / nombre for d in ("procesado", "subtitulos", "salidas", "recetas", "transcripciones", "ondas")]
    carpetas += [DATOS / "miniaturas" / "clips" / nombre, DATOS / "miniaturas" / "tiras" / nombre]
    archivos = [DATOS / "miniaturas" / f"{re.sub(r'[^\w.-]+', '_', nombre)}.jpg"]
    archivos += [asig / f"{c}{x}" for c in carpetas_drive for x in (".json", ".reemplazos.json")]

    def dentro(x):  # nunca algo fuera de los datos de la sala o de la carpeta de trabajo, ni esas carpetas enteras
        x = x.resolve()
        return any(x != r.resolve() and r.resolve() in x.parents for r in (DATOS, base))

    peso = 0
    for d in carpetas:
        if d.is_dir() and dentro(d):
            peso += sum(x.stat().st_size for x in d.rglob("*") if x.is_file())
            shutil.rmtree(d, ignore_errors=True)
    for f in archivos:
        if f.is_file() and dentro(f):
            peso += f.stat().st_size
            f.unlink(missing_ok=True)
    return peso


def semanas_disponibles():
    def es_sesion(d):
        return d.is_dir() and (es_nombre_sesion(d.name) or (d / "Clips").is_dir())

    hoy = time.localtime()

    def orden(nombre):  # de la más vieja a la más nueva: otras, "13 10 SESIÓN" -> (año, 10, 13), luego producciones «P07 …»
        m = re.match(r"^(\d{1,2})\D+(\d{1,2})", nombre)
        p = PRODUCCION.match(nombre)
        if m:  # el nombre no trae año: un mes más de un mes adelante del actual es del año pasado (diciembre visto en enero)
            mes = int(m.group(2))
            return (1, hoy.tm_year - (mes > hoy.tm_mon + 1), mes, int(m.group(1)), nombre)
        return (2, int(p.group(1)), 0, 0, nombre) if p else (0, 0, 0, 0, nombre)  # «P07 …» son las nuevas: al final (arriba en la lista)

    nombres = {d.name for d in S.base.iterdir() if es_sesion(d) and not d.name.startswith("_")} if S.base.exists() else set()
    if CONFIG.get("drive", {}).get("activo"):  # también las que están en Drive y aún no se bajaron
        try:
            nombres |= set(json.loads((DATOS / "drive_sesiones.json").read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError):
            pass
    return sorted(nombres - set(grupos()), key=orden) + sorted(grupos())


def guardar_config(cambios):
    """Guarda en config.local.json (no se comparte entre PCs)."""
    CONFIG.update(cambios)
    local = json.loads(LOCAL_PATH.read_text(encoding="utf-8")) if LOCAL_PATH.exists() else {}
    local.update(cambios)
    LOCAL_PATH.write_text(json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Utilidades ----------

# ---------- Cancelar el trabajo en curso ----------
import threading as _hilos
CANCELAR = _hilos.Event()
PAUSA = _hilos.Event()  # «Pausar» en Procesos: el trabajo se queda quieto al terminar el paso que está haciendo
PROCESOS = set()  # ffmpeg, rclone o Claude corriendo ahora: se cortan al cancelar


class Cancelado(Exception):
    pass


HILO_TRABAJO = {"id": None, "ayudantes": set()}  # solo el trabajo en segundo plano se cancela; lo demás (ver un video, una portada) no
_LOCAL = _hilos.local()  # .slot: carpeta de trabajo propia de cada hilo que arma a la vez


def revisar_cancelado():
    yo = _hilos.get_ident()
    del_trabajo = yo == HILO_TRABAJO["id"] or yo in HILO_TRABAJO["ayudantes"]
    while del_trabajo and PAUSA.is_set() and not CANCELAR.is_set():  # en pausa: espera aquí hasta «Reanudar»
        time.sleep(0.3)
    if CANCELAR.is_set() and del_trabajo:
        raise Cancelado("Cancelado")


def cancelar():
    PAUSA.clear()
    CANCELAR.set()
    for p in list(PROCESOS):
        try:
            p.kill()
        except OSError:
            pass


def _prioridad_baja():
    """Lo que corre el trabajo de fondo (armar, preparar) va con prioridad baja: usa el procesador cuando está libre y
    cede apenas el editor lo necesita. Antes, armando de a dos, ffmpeg se quedaba con casi todo el procesador y el
    editor iba muy lento hasta que terminaba."""
    if os.name != "nt":
        return 0
    yo = _hilos.get_ident()
    return 0x00004000 if yo == HILO_TRABAJO.get("id") or yo in HILO_TRABAJO.get("ayudantes", ()) else 0  # BELOW_NORMAL


def run(cmd, cwd=None):
    revisar_cancelado()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", cwd=cwd,
                         creationflags=_prioridad_baja())
    PROCESOS.add(p)
    try:
        out, err = p.communicate()
    finally:
        PROCESOS.discard(p)
    revisar_cancelado()
    if p.returncode != 0:
        raise RuntimeError(f"Falló: {' '.join(map(str, cmd))[:300]}\n{err[-1500:]}")
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


_DURACIONES = {}


def duracion(p):
    """Duración de un archivo (ffprobe), recordada mientras el archivo no cambie: el armado preguntaba lo mismo
    decenas de veces por video."""
    try:
        st = os.stat(p)
        clave = (str(p), st.st_size, st.st_mtime_ns)
    except OSError:
        clave = None
    if clave and clave in _DURACIONES:
        return _DURACIONES[clave]
    d = float(run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]).stdout.strip())
    if clave:
        _DURACIONES[clave] = d
    return d


_FPS = {}


def fps_de(p):
    """Cuadros por segundo de un video (ffprobe, el «r_frame_rate»), recordados mientras el archivo no cambie.
    El iPhone graba a 59,94 o 29,97 con cuadros que varían un poco: lo que importa es si es de 30 o de 60."""
    try:
        st = os.stat(p)
        clave = (str(p), st.st_size, st.st_mtime_ns)
    except OSError:
        return 30.0
    if clave not in _FPS:
        try:
            r = run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate,avg_frame_rate",
                     "-of", "csv=p=0", str(p)]).stdout.strip().splitlines()
            fr = [x for x in (r[0].split(",") if r else []) if "/" in x]
            vals = [int(a) / int(b) for a, b in (x.split("/") for x in fr) if int(b)]
            _FPS[clave] = max(vals) if vals else 30.0
        except (RuntimeError, ValueError, IndexError):
            _FPS[clave] = 30.0
    return _FPS[clave]


def fps_redondo(x):
    """30 o 60: la sala trabaja con esas dos (60 si el video se grabó a 50 o más)."""
    return 60 if (x or 0) >= 47 else 30


def fps_para_piezas(partes):
    """A cuántos cuadros se prepara una pieza: los de sus clips (60 si alguno es de 60) o lo que diga
    config «fps_piezas» (30 o 60; «auto» = según los clips, lo de siempre)."""
    forzado = str(CONFIG.get("fps_piezas") or "auto")
    if forzado in ("30", "60"):
        return int(forzado)
    return max([fps_redondo(fps_de(x)) for x in partes] or [30])


def norm(w):
    w = unicodedata.normalize("NFD", w.lower())
    return re.sub(r"[^a-z0-9ñ]", "", "".join(c for c in w if unicodedata.category(c) != "Mn"))


_prod_cache = None


def produccion():
    """produccion.json de la semana (en la carpeta de la semana o en semanas/SEMANA_XX)."""
    global _prod_cache
    for f in (S.guion / "produccion.json", S.grab / "produccion.json",
              RAIZ.parent / "semanas" / S.drive / "produccion.json"):
        if f.exists():
            m = f.stat().st_mtime
            if not _prod_cache or _prod_cache[0] != (f, m):
                _prod_cache = ((f, m), _arreglar_letras(json.loads(f.read_text(encoding="utf-8"))))
            return _sumar_plan(_prod_cache[1])
    return _sumar_plan({})


def _arreglar_letras(x):
    """Textos con tildes dañadas al pasar por otro programa («cicatrizaciÃ³n» → «cicatrización»)."""
    if isinstance(x, dict):
        return {_arreglar_letras(k): _arreglar_letras(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_arreglar_letras(v) for v in x]
    if isinstance(x, str) and ("Ã" in x or "Â" in x):
        for cod in ("cp1252", "latin-1"):
            try:
                return x.encode(cod).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
    return x


def _sumar_plan(base):
    """El plan del director (subtitulos/<sesión>/_plan.json) manda sobre el guion: música, videos y piezas."""
    plan = plan_director()
    if not plan:
        return base
    out = {**base}
    for k in ("musica", "musica_por_defecto", "plantilla_sub", "calendario", "formato", "formato_cambio"):
        if k in plan:
            out[k] = plan[k]
    for k in ("videos", "piezas"):
        if k in plan:
            out[k] = {**base.get(k, {}), **{n: {**base.get(k, {}).get(n, {}), **v} for n, v in plan[k].items()}}
    return out


def _plan_path():
    return S.subs / "_plan.json"


def plan_director():
    try:
        return json.loads(_plan_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


# ---------- Formato: vertical 9:16 (Reels, TikTok, Shorts) u horizontal 16:9 (web, YouTube, correos) ----------
# Es de la sesión: lo puede pedir el guion («formato»: "horizontal") o elegirse en la sala (se anota en el plan).
# Las plantillas de subtítulos se diseñan sobre el vertical y se adaptan solas al 16:9 (ver _al_lienzo).
FORMATOS = {"vertical": (1080, 1920), "horizontal": (1920, 1080)}
# ventana de imagen por defecto (x, y, ancho): en el vertical arriba de la cara; en el 16:9 a la derecha, porque
# el que habla se para en el tercio izquierdo (como en YouTube) y deja libre ese lado
VENTANA_DEFECTO = {"vertical": (110, 330, 860), "horizontal": (1120, 180, 720)}
POSICIONES_TITULO = {"vertical": {"arriba": 560, "centro": 960, "abajo": 1380},  # dentro del recorte 4:5 del perfil de Instagram
                     "horizontal": {"arriba": 250, "centro": 540, "abajo": 830}}


def formato():
    f = produccion().get("formato")
    return f if f in FORMATOS else "vertical"


def lienzo():
    """(ancho, alto) en píxeles del video de la sesión."""
    return FORMATOS[formato()]


def guardar_formato(f):
    """Cambia el formato de la sesión. Las piezas se vuelven a preparar con el tamaño nuevo y los videos quedan
    «por re-armar» (los subtítulos corregidos se conservan: la voz es la misma)."""
    global _prod_cache
    if f not in FORMATOS:
        raise ValueError("Formato no válido")
    if f == formato():
        return
    S.subs.mkdir(parents=True, exist_ok=True)
    _plan_path().write_text(json.dumps({**plan_director(), "formato": f, "formato_cambio": time.time()},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
    _prod_cache = None
    (S.proc / "_fondo.jpg").unlink(missing_ok=True)  # el cuadro de muestra de las plantillas se rehace con el tamaño nuevo


def _al_lienzo(pl):
    """La plantilla (medidas en px del video vertical 1080×1920) llevada al lienzo de la sesión. En 16:9 se mantiene
    el lugar a lo ancho, la letra baja un 20 % y el subtítulo va más cerca del borde de abajo (no hay botones de
    TikTok que esquivar)."""
    W, H = lienzo()
    if (W, H) == FORMATOS["vertical"]:
        return pl
    st = CONFIG["subtitulos"]
    kx, ky, kt = W / 1080, H / 1920, 0.8
    out = {**pl}
    for c in ("tamano", "borde", "sombra", "desenfoque"):
        if out.get(c) not in (None, ""):
            out[c] = float(out[c]) * kt
    out["x_centro"] = float(pl.get("x_centro", 540)) * kx
    out["ancho_max"] = min(W - 160, float(pl.get("ancho_max", 960)) * kx)
    out["altura_desde_abajo"] = float(pl.get("altura_desde_abajo", st["altura_desde_abajo"])) * ky * 0.6
    for c in ("altura_hook", "altura_cta"):
        if pl.get(c) not in (None, ""):
            out[c] = float(pl[c]) * ky * 0.6
    out["altura_desde_arriba"] = float(pl.get("altura_desde_arriba", st.get("altura_desde_arriba", 300))) * ky
    out["elementos"] = [{**e, "x": float(e.get("x", 0)) * kx, "y": float(e.get("y", 0)) * ky,
                         "ancho": float(e.get("ancho", 200)) * kt} for e in pl.get("elementos") or []]
    return out


def info_pieza(nombre):
    """Lo que dice el guion de una pieza (una de otro ángulo, C1~IZQ, dice lo mismo que la de frente)."""
    pz = produccion().get("piezas", {})
    return pz.get(nombre) or pz.get(sin_angulo(nombre)[0], {})


# C1_H2 (hook), C1 (cuerpo), C1_P3 (parte 3 del cuerpo C1), CTA / CTA-A / C1_CTA1
# C1_H2 (hook), C1_H2_P1 (parte 1 del hook 2), C1 (cuerpo), C1_P3 (parte 3 del cuerpo), CTA / CTA-A / C1_CTA1
# CTA-1_P2 (parte 2 del CTA-1): un CTA también se puede grabar en partes
# «C22_P3B»: la frase 3 dicha en dos clips seguidos (el segundo completa al primero); va justo después de C22_P3
CODIGO_PIEZA = re.compile(r"^((?:C|P)\d+_H\d+(?:_P\d+)?|(?:C|P)\d+(?:_P\d+B?)?|(?:(?:C|P)\d+_)?CTA[-_]?[A-Z0-9]*(?:_P\d+)?)$", re.I)
PARTE = re.compile(r"^((?:C|P)\d+(?:_H\d+)?(?:~[A-Z0-9]+)?|(?:(?:C|P)\d+_)?CTA(?:-?[A-Z0-9]+?)?(?:~[A-Z0-9]+)?)_P(\d+)(B?)$", re.I)

# Como nombra el equipo los archivos a mano (en carpetas C1/, C2/…, CTA/):
#   C1-H1 · C1 H1 → C1_H1      C1-H1.2 → C1_H1_P2 (hook en partes)
#   C1.1 · C1-1 → C1_P1        C3.1 HASTA C3.4 → C3_P1 (una toma con las frases 1 a 4)
#   CTA 1.2 · CTA-1.2 → CTA1_P2 (como en el guion: CTA1)      CTA-A, C1_CTA1 → igual
_SEP = r"[\s._-]*"
_N_HOOK = re.compile(rf"^([CP])(\d+){_SEP}H(\d+)(?:[\s._-]+P?(\d+))?$", re.I)
_N_RANGO = re.compile(rf"^([CP])(\d+)[\s._-]+P?(\d+)\s*(?:HASTA|AL|A|-)\s*(?:[CP]\d+[\s._-]+)?P?(\d+)$", re.I)
_N_PARTE = re.compile(r"^([CP])(\d+)[\s._-]+P?(\d+)$", re.I)
_N_CUERPO = re.compile(r"^([CP])(\d+)$", re.I)
_N_CTA = re.compile(r"^(?:([CP]\d+)[\s_-]*)?CTA(?:[\s_-]*([A-Z0-9]+?))?(?:[\s._-]+P?(\d+))?$", re.I)


def leer_nombre(texto):
    """(código de pieza, (desde, hasta) si la toma trae varias frases) o (None, None) si el nombre no dice nada."""
    t = " ".join(str(texto).split()).upper()
    if (m := _N_CTA.match(t)) and m[2] and m[2].isdigit():  # «CTA 1.2», «CTA-1», «CTA1»: como en el guion (CTA1)
        return (f"{m[1]}_" if m[1] else "") + f"CTA{int(m[2])}" + (f"_P{int(m[3])}" if m[3] else ""), None
    if CODIGO_PIEZA.match(t):  # ya viene como lo escribe la sala
        return t, None
    if m := _N_HOOK.match(t):
        return f"{m[1]}{int(m[2])}_H{int(m[3])}" + (f"_P{int(m[4])}" if m[4] else ""), None
    if m := _N_RANGO.match(t):
        a, b = int(m[3]), int(m[4])
        return (f"{m[1]}{int(m[2])}_P{a}", (a, b)) if b > a else (None, None)
    if m := _N_PARTE.match(t):
        return f"{m[1]}{int(m[2])}_P{int(m[3])}", None
    if m := _N_CUERPO.match(t):
        return f"{m[1]}{int(m[2])}", None
    if m := _N_CTA.match(t):
        return (f"{m[1]}_" if m[1] else "") + "CTA" + (f"-{m[2]}" if m[2] else "") + (f"_P{int(m[3])}" if m[3] else ""), None
    return None, None


def tipo_de(nombre):
    n = nombre.upper()
    return "cta" if "CTA" in n else "hook" if re.search(r"_H\d+", n) else "cuerpo"


def _asig_archivos(sesion):
    """La copia de Drive (compartida con el equipo) y la local de esta PC; la local manda.
    La local existe porque Drive borra en silencio lo que escribe una cuenta con permiso de solo lectura."""
    return S.raiz / sesion / "asignaciones.json", DATOS / "asignaciones" / f"{S.en_drive(sesion)}.json"


def _leer_asig(sesion):
    d = {}
    for f in _asig_archivos(sesion):
        try:
            d.update(json.loads(f.read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError, OSError):
            pass
    return d


def leer_asignaciones():
    """{rel del clip: pieza}. En un grupo, cada sesión guarda las suyas y aquí se ven con «sesión/» delante."""
    if not S.es_grupo:
        return _leer_asig(S.semana)
    return {f"{m}/{rel}": p for m in S.miembros for rel, p in _leer_asig(m).items()}


def _escribir_asig(sesion, d):
    texto = json.dumps(d, ensure_ascii=False, indent=1)
    drive_f, local_f = _asig_archivos(sesion)
    local_f.parent.mkdir(parents=True, exist_ok=True)
    local_f.write_text(texto, encoding="utf-8")
    try:
        drive_f.write_text(texto, encoding="utf-8")
    except OSError:
        pass


def _reemp_archivo(sesion):
    f = DATOS / "asignaciones" / f"{S.en_drive(sesion)}.reemplazos.json"
    f.parent.mkdir(parents=True, exist_ok=True)  # en una producción con días: asignaciones/P02 …/11／10 SESIÓN…
    return f


def _leer_reemp(sesion):
    try:
        return json.loads(_reemp_archivo(sesion).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def reemplazos():
    """{rel del clip: {pieza, por, fecha}}: clips que eran de una pieza y se cambiaron por otro (vuelven a
    «Clips por identificar»). Con la misma forma de rel que leer_asignaciones."""
    if not S.es_grupo:
        return _leer_reemp(S.semana)
    return {f"{m}/{rel}": v for m in S.miembros for rel, v in _leer_reemp(m).items()}


def guardar_asignacion(rel, pieza):
    """Asigna un clip a una pieza. Si esa pieza ya tenía otro clip, lo reemplaza: el anterior vuelve a
    «Clips por identificar». Devuelve la sesión donde quedó guardada (para subir su asignaciones.json a Drive).
    Quitar se guarda como "" (no se borra la línea) para que no vuelva desde la copia de Drive."""
    sesion = S.semana
    if S.es_grupo:
        sesion, rel = rel.split("/", 1)
        if sesion not in S.miembros:
            raise ValueError("Ese clip no es de este grupo")
    if pieza:
        if not CODIGO_PIEZA.match(sin_angulo_codigo(pieza)[0]):
            raise ValueError(f"«{pieza}» no es un código de pieza (C1_H2, C3, C1_P2, CTA-A…)")
        pieza = pieza.upper()
        for m in S.miembros:  # el clip que tenía esa pieza queda libre (en un grupo, en cualquiera de sus sesiones)
            dm = _leer_asig(m)
            viejos = [r for r, p in dm.items() if p == pieza and not (m == sesion and r == rel)]
            if viejos:
                dm.update({r: "" for r in viejos})
                _escribir_asig(m, dm)
                rp = _leer_reemp(m)  # se recuerda de qué pieza era y por cuál se cambió
                rp.update({r: {"pieza": pieza, "por": Path(rel).name, "fecha": time.time()} for r in viejos})
                _reemp_archivo(m).write_text(json.dumps(rp, ensure_ascii=False, indent=1), encoding="utf-8")
        # (si la pieza la tenía un clip por su nombre de archivo, H3.MOV…, gana el asignado a mano: ver piezas())
    d = _leer_asig(sesion)
    d[rel] = pieza or ""
    _escribir_asig(sesion, d)
    rp = _leer_reemp(sesion)
    if pieza and rp.pop(rel, None):  # volvió a tener pieza: ya no es un clip reemplazado
        _reemp_archivo(sesion).write_text(json.dumps(rp, ensure_ascii=False, indent=1), encoding="utf-8")
    return sesion


# Memoria corta: mientras se arma una respuesta para la pantalla (por ejemplo el estado de la sesión), los clips,
# las piezas y los videos posibles se calculan una sola vez. Antes el estado los recalculaba unas 30 veces seguidas
# (una por cada video y su copy) mirando la carpeta de clips cada vez: con 27 clips tardaba casi 2 segundos.
_memo_hilo = threading.local()


class memoria:
    """with motor.memoria(): ... — dentro, clips(), piezas() y combinaciones() se calculan una vez (por hilo)."""
    def __enter__(self):
        self.prev = getattr(_memo_hilo, "d", None)
        if self.prev is None:
            _memo_hilo.d = {}
        return self

    def __exit__(self, *a):
        if self.prev is None:
            _memo_hilo.d = None


def _con_memoria(clave, calcular):
    import copy
    d = getattr(_memo_hilo, "d", None)
    if d is None:
        return calcular()
    if clave not in d:
        d[clave] = calcular()
    return copy.deepcopy(d[clave])  # copia: quien la use puede cambiarla sin tocar la guardada


def clips():
    return _con_memoria(("clips", S.semana), _clips_sin_memoria)


def _clips_sin_memoria():
    """Todos los videos de la sesión: [{rel, ruta, tipo_carpeta}]. tipo_carpeta = None si están sueltos en Clips/."""
    vistos, out = set(), []
    for sesion in S.carpetas_sesiones():
      # también la forma ordenada por video: Clips/C2/Cuerpo/…, Clips/C2/Hooks/…
      por_cuerpo = [d for d in sorted((sesion / "Clips").glob("*")) if d.is_dir() and re.fullmatch(r"(?:C|P)\d+", d.name, re.I)] \
          if (sesion / "Clips").is_dir() else []
      for carpeta, nombres in [(sesion, CARPETAS)] + [(d, {t: [o.split("/")[-1] for o in ops] for t, ops in CARPETAS.items()}) for d in por_cuerpo]:
        for tipo, opciones in nombres.items():
            for o in opciones:
                d = carpeta / o
                if d.is_dir():
                    for f in sorted(d.rglob("*"), key=lambda x: x.as_posix().lower()):
                        if f not in vistos and not _apartado(f, d) and es_video(f):
                            vistos.add(f)
                            # en Clips/C2/…, Clips/CTA/…: ya está en su carpeta (al ordenar no se mueve)
                            out.append({"rel": f.relative_to(S.grab).as_posix(), "ruta": f, "tipo_carpeta": tipo,
                                        "a_mano": carpeta in por_cuerpo or tipo == "cta"})
      for sueltos in [sesion / CARPETA_SUELTOS] + por_cuerpo:
        if sueltos.is_dir():
            for f in sorted(sueltos.iterdir()):
                if f not in vistos and es_video(f):
                    vistos.add(f)
                    out.append({"rel": f.relative_to(S.grab).as_posix(), "ruta": f, "tipo_carpeta": None,
                                "a_mano": sueltos in por_cuerpo})  # sueltos en Clips/: la sala los ordena
      # en subcarpetas de Clips/ con cualquier nombre («Clips/Clips iphone de mi papa/…», «Clips/C1/Ángulo 2/…»):
      # también son sueltos (salvo _DESCARTADOS, Bloopers…)
      if (sesion / CARPETA_SUELTOS).is_dir():
          raiz_sueltos = sesion / CARPETA_SUELTOS
          for f in sorted(raiz_sueltos.rglob("*"), key=lambda x: x.as_posix().lower()):
              if f not in vistos and not _apartado(f, raiz_sueltos) and es_video(f):
                  vistos.add(f)
                  out.append({"rel": f.relative_to(S.grab).as_posix(), "ruta": f, "tipo_carpeta": None, "a_mano": False})
      # ordenado a mano sin Clips/: «P04 …/C1/C1-H1», «…/C1/C1.1», «…/CTA/CTA 1.2» (Bloopers y lo demás no se mira)
      for d in carpetas_de_piezas(sesion):
        tipo = "cta" if d.name.upper().startswith("CTA") else None
        for f in sorted(d.rglob("*"), key=lambda x: x.as_posix().lower()):
            if f in vistos or _apartado(f, d):
                continue
            if es_video(f):
                vistos.add(f)
                out.append({"rel": f.relative_to(S.grab).as_posix(), "ruta": f, "tipo_carpeta": tipo, "a_mano": True})
    return out


def _apartado(f, carpeta):
    """Dentro de una subcarpeta Bloopers, _DESCARTADOS, descartes…: el equipo lo apartó y no se usa."""
    return any(p.startswith("_") or "BLOOPER" in p.upper() or "DESCART" in p.upper() for p in f.relative_to(carpeta).parts[:-1])


# ---------- Ángulos: el mismo guion grabado desde otro lado ----------
# Lo de frente va como siempre (C1/C1.1). Otro ángulo va en una carpeta con su nombre: C1/IZQUIERDA/C1.1 o
# IZQUIERDA/C1/C1.1. Esa toma es otra pieza (C1~IZQ) con el mismo guion, y arma sus propios videos (C1-H1-CTA1-IZQ).
_ANGULOS = [(r"(DE )?FRENTE|FRONTAL|CENTRO|PRINCIPAL", ""),
            (r"IZQUIERDA|IZQUIERDO|IZQ|(LADO|ANGULO|CAMARA) IZQUIERD[OA]", "IZQ"),
            (r"DERECHA|DERECHO|DER|(LADO|ANGULO|CAMARA) DERECH[OA]", "DER"),
            (r"LADO|LATERAL|PERFIL", "LAD"), (r"ARRIBA|CENITAL|PICADO", "ARR"), (r"ABAJO|CONTRAPICADO", "ABA"),
            (r"CERCA|PRIMER PLANO|PRIMERPLANO|CLOSE UP|CLOSEUP", "CER")]
NOMBRES_ANGULO = {"": "Frente", "IZQ": "Izquierda", "DER": "Derecha", "LAD": "Lado", "ARR": "Arriba", "ABA": "Abajo",
                  "CER": "Primer plano"}


def codigo_angulo(carpeta):
    """«IZQUIERDA» → «IZQ», «CAM 2» → «A2», «FRENTE» → «» (la de siempre); None si la carpeta no es de un ángulo."""
    t = " ".join(re.sub(r"[_\-.]+", " ", unicodedata.normalize("NFD", str(carpeta)).encode("ascii", "ignore").decode()).upper().split())
    for patron, cod in _ANGULOS:
        if re.fullmatch(patron, t):
            return cod
    m = re.fullmatch(r"(?:ANGULO|CAM|CAMARA|TOMA|VISTA)\s*(\d+)", t)
    return f"A{int(m.group(1))}" if m else None


def nombre_angulo(cod):
    return NOMBRES_ANGULO.get(cod) or (f"Ángulo {cod[1:]}" if cod.startswith("A") else cod.title())


def codigo_angulo_en(c):
    """¿El clip está bajo una carpeta de ángulo (cualquiera menos «frente»)?"""
    return any(codigo_angulo(p) for p in Path(c["rel"]).parts[:-1])


def angulo_de(c):
    """Ángulo de un clip según sus carpetas («C1/IZQUIERDA/C1.1» → «IZQ»); «» si es de frente."""
    for parte in Path(c["rel"]).parts[:-1]:
        cod = codigo_angulo(parte)
        if cod is not None:
            return cod
    return ""


def con_angulo(nombre, ang):
    """«C1_P2» + «IZQ» → «C1~IZQ_P2»; «C1_H1» → «C1_H1~IZQ». Sin ángulo, queda igual."""
    if not nombre or not ang or "~" in nombre:
        return nombre
    m = PARTE.match(nombre)
    return f"{m.group(1)}~{ang}_P{m.group(2)}{m.group(3)}" if m else f"{nombre}~{ang}"


def sin_angulo(nombre):
    """(nombre de frente, ángulo): «C1_H1~IZQ» → («C1_H1», «IZQ»)."""
    base, _, ang = str(nombre).partition("~")
    return base, ang


def sin_angulo_codigo(codigo):
    """Como sin_angulo, pero también para una parte: «C1~A2_P3» → («C1_P3», «A2»)."""
    m = re.search(r"~([A-Z0-9]+)", str(codigo), re.I)
    return (str(codigo)[:m.start()] + str(codigo)[m.end():], m.group(1).upper()) if m else (str(codigo), "")


def carpetas_de_piezas(sesion):
    """Carpetas C1/, C2/…, CTA/ sueltas en la sesión (sin Clips/): así ordena el equipo lo que grabó."""
    if not sesion.is_dir():
        return []
    propias = [d for d in sorted(sesion.iterdir(), key=lambda x: x.name.lower())
               if d.is_dir() and re.fullmatch(r"(?:[CP]\d+|CTAS?(?:[\s_-]*[A-Z0-9]+)?)", d.name.strip(), re.I)
               and d.name.lower() not in ("ctas",)]  # «ctas» ya la mira CARPETAS
    # otro ángulo con sus carpetas adentro: IZQUIERDA/C1/…, IZQUIERDA/CTA/…
    for d in sorted(sesion.iterdir(), key=lambda x: x.name.lower()):
        if d.is_dir() and codigo_angulo(d.name):
            propias += carpetas_de_piezas(d)
    return propias


def nombre_automatico(c):
    """Nombre de pieza que se deduce sin asignar a mano:
    C1_H2.mov · Cuerpo/3.MOV -> C1_P3 · Cuerpo/C2/1.MOV -> C2_P1 · CTA/CTA.MOV -> CTA.
    También como los nombra el equipo a mano: C1-H1, C1.1, CTA 1.2 (ver leer_nombre)."""
    stem, tipo = nombre_clip(c["ruta"]), c["tipo_carpeta"]
    # las carpetas del clip dentro de la sesión, de la más cercana a la más lejana. Una carpeta de ángulo puede ir en
    # cualquier lugar («Clips/C6/Ángulo 2/Hooks/H1», «Clips/Ángulo 2/C6/Cuerpo/1»): se salta al buscar el cuerpo y el tipo
    carpetas = [p for p in reversed(Path(c["rel"]).parts[:-1]) if codigo_angulo(p) is None]
    if tipo is None and codigo_angulo_en(c):  # sin tipo por estar bajo una carpeta de ángulo: lo dice su carpeta
        tipo = next(({"hook": "hook", "hooks": "hook", "cuerpo": "cuerpo", "cuerpos": "cuerpo", "cta": "cta", "ctas": "cta"}
                     .get(p.lower()) for p in carpetas if p.lower() in ("hook", "hooks", "cuerpo", "cuerpos", "cta", "ctas")), None)

    def cuerpo_de(ruta):  # Cuerpo/C2/1.MOV, C2/Cuerpo/1.MOV o C2/Ángulo 2/Hooks/H1.MOV → C2; si no dice, C1
        for d in carpetas[:4]:
            if re.fullmatch(r"(?:C|P)\d+", d, re.I):
                return d.upper()
        return "C1"
    codigo, _ = leer_nombre(stem)
    if codigo:
        return codigo
    if tipo == "cuerpo" and stem.isdigit():
        return f"{cuerpo_de(c['ruta'])}_P{int(stem)}"
    if tipo == "cuerpo" and stem.lower() in ("cuerpo", "c"):
        return cuerpo_de(c["ruta"])
    if tipo == "cta" and stem.upper().startswith("CTA"):
        return stem.upper()
    if tipo == "hook":  # Hooks/1-2.MOV (hook 1, parte 2) o Hooks/H3.MOV; Hooks/C2/… si hay varios cuerpos
        cuerpo = cuerpo_de(c["ruta"])
        m = re.fullmatch(r"H?(\d+)\s*[-_.]\s*(\d+)", stem, re.I)
        if m:
            return f"{cuerpo}_H{int(m.group(1))}_P{int(m.group(2))}"
        m = re.fullmatch(r"H(\d+)", stem, re.I)
        if m:
            return f"{cuerpo}_H{int(m.group(1))}"
    if tipo is None and stem.lower() == "cuerpo":  # Clips/C3/Cuerpo.MOV (cuerpo de una sola toma)
        return cuerpo_de(c["ruta"])
    return None


def _toma_dice(larga, suelta):
    """¿La toma larga («C3.1 HASTA C3.4») dice lo mismo que la toma suelta (C3.1)? Se mira en lo transcrito;
    si alguno no se escuchó todavía, se cree al nombre (sí)."""
    import difflib
    tl, ts = transcripcion_clip(larga, crear=False), transcripcion_clip(suelta, crear=False)
    if not tl or not ts:
        return True
    a, b = [norm(w) for w in ts.split() if norm(w)], [norm(w) for w in tl.split() if norm(w)]
    if not a:
        return True
    iguales = sum(blq.size for blq in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return iguales >= 0.6 * len(a)


_LLAMADO = {"whatsapp", "escribenos", "escribeme", "escribe", "comenta", "comentanos", "comentame", "pidelo", "pide",
            "pidenos", "compralo", "compra", "link", "enlace", "mensaje", "dm", "llamanos", "inbox", "perfil"}


def es_llamado(texto):
    """¿Esta frase es un CTA completo por sí sola? (pide hacer algo: escribir, comentar, pedir…)"""
    k = [norm(w) for w in str(texto).split() if norm(w)]
    return len(k) >= 4 and any(w in _LLAMADO for w in k)


def escuchar_tomas_largas(avance=print):
    """Transcribe las tomas largas («C3.1 HASTA C3.4») y las sueltas de esas mismas frases, para saber si de verdad
    las dicen (ver piezas). Queda en caché: solo tarda la primera vez."""
    asig = leer_asignaciones()
    todos = [c for c in clips() if not asig.get(c["rel"])]
    rangos = {}
    for c in todos:
        codigo, rango = leer_nombre(nombre_clip(c["ruta"]))
        if rango:
            rangos.setdefault(PARTE.match(codigo).group(1).upper(), []).append((rango, c))
    oir = []
    for c in todos:
        m = PARTE.match(nombre_automatico(c) or "")
        for rango, larga in rangos.get(m.group(1).upper(), []) if m else []:
            if larga is not c and rango[0] <= int(m.group(2)) <= rango[1]:
                oir += [x for x in (larga, c) if x not in oir]
    # las partes de un CTA: para saber si son un CTA largo o varios CTA distintos
    por_cta = {}
    for c in todos:
        m = PARTE.match(nombre_automatico(c) or "")
        if m and "CTA" in m.group(1).upper():
            por_cta.setdefault(m.group(1).upper(), []).append(c)
    oir += [c for lista in por_cta.values() if len(lista) > 1 for c in lista if c not in oir]
    for c in oir:
        if transcripcion_clip(c, crear=False) is None:
            avance(f"Escuchando {c['rel']}")
            transcripcion_clip(c)


def piezas():
    return _con_memoria(("piezas", S.semana), _piezas_sin_memoria)


def _piezas_sin_memoria():
    """Piezas listas para editar: [{nombre, tipo, partes: [rutas], original, rel}].
    Un cuerpo grabado en partes (C1_P1, C1_P2…) es una sola pieza C1 con sus partes en orden.
    Si dos clips dicen ser la misma pieza, gana el asignado a mano."""
    asig = leer_asignaciones()
    todos = clips()
    ang = {c["rel"]: angulo_de(c) for c in todos}  # de qué ángulo es cada toma (carpeta IZQUIERDA, CAM 2…)
    # una toma con varias frases («C3.1 HASTA C3.4») cubre esas partes: las tomas sueltas de esas frases sobran
    cubre = {}
    for c in todos:
        if not asig.get(c["rel"]):
            codigo, rango = leer_nombre(nombre_clip(c["ruta"]))
            if rango:
                base = PARTE.match(con_angulo(codigo, ang[c["rel"]])).group(1).upper()
                cubre.update({(base, k): c["rel"] for k in range(rango[0], rango[1] + 1)})
    # …salvo que la toma larga no diga esa frase (se ve en lo transcrito): «C3.1 HASTA C3.4» que empieza en la
    # frase 2 deja entrar a la toma suelta C3.1, y la larga pasa a ser la parte 2
    por_rel = {c["rel"]: c for c in todos}
    for c in todos:
        m = PARTE.match(con_angulo(nombre_automatico(c), ang[c["rel"]]) or "") if not asig.get(c["rel"]) else None
        clave = (m.group(1).upper(), int(m.group(2))) if m else None
        if clave and cubre.get(clave, c["rel"]) != c["rel"] and not _toma_dice(por_rel[cubre[clave]], c):
            del cubre[clave]
    inicio_real = {}
    for (base, k), rel in cubre.items():
        inicio_real[rel] = min(k, inicio_real.get(rel, k))
    por_nombre, origen_manual = {}, set()
    for c in todos:
        manual = asig.get(c["rel"])
        nombre = con_angulo(manual or nombre_automatico(c), ang[c["rel"]])  # la de otro ángulo no pisa a la de frente
        if not manual and c["rel"] in inicio_real and PARTE.match(nombre or ""):
            nombre = f"{PARTE.match(nombre).group(1)}_P{inicio_real[c['rel']]}"
        m = PARTE.match(nombre or "")
        if not manual and m and cubre.get((m.group(1).upper(), int(m.group(2))), c["rel"]) != c["rel"]:
            continue
        if not nombre or (nombre in por_nombre and nombre in origen_manual and not manual):
            continue
        por_nombre[nombre] = c
        if manual:
            origen_manual.add(nombre)
    agrupadas = {}
    nombre_de_rel = {c["rel"]: nombre for nombre, c in por_nombre.items()}  # con qué nombre entra cada toma (C1_P3…)
    for nombre, c in por_nombre.items():
        m = PARTE.match(nombre)
        base, orden = (m.group(1).upper(), int(m.group(2))) if m else (nombre, 0)
        agrupadas.setdefault(base, []).append((orden, c, bool(m and m.group(3))))
    out = []
    for nombre, lista in agrupadas.items():
        lista.sort(key=lambda x: (x[0], x[2]))  # C22_P3 y enseguida C22_P3B (la frase en dos clips)
        lista = [(orden, c) for orden, c, _ in lista]
        partes = [c["ruta"] for _, c in lista]
        # qué frases cubren las partes (C2.1, C2.2, C2.3, C2.5 → falta la 4)
        cubre_rel = {c["rel"]: list(range(orden, max(orden, (leer_nombre(nombre_clip(c["ruta"]))[1] or (0, orden))[1]) + 1))
                     for orden, c in lista if orden}
        numeros = sorted({k for ks in cubre_rel.values() for k in ks})
        out.append({"nombre": nombre, "tipo": tipo_de(nombre), "partes": partes, "original": partes[0],
                    "rel": lista[0][1]["rel"], "rels": [c["rel"] for _, c in lista],
                    # el nombre de pieza de cada toma: para cambiar una toma por otra, la nueva se asigna con ese nombre
                    "nombres_partes": [nombre_de_rel[c["rel"]] for _, c in lista],
                    "frases": cubre_rel,  # {rel: [frases que dice]} en una pieza grabada en partes
                    "faltan_partes": [k for k in range(1, max(numeros) + 1) if k not in numeros] if numeros else []})
    # Un CTA en partes donde cada parte ya es un llamado completo («escríbenos por WhatsApp…» y «comenta Suplevet…»)
    # no es un CTA largo: son CTA distintos. Se separan (CTA1, CTA1B…) y cada video lleva uno solo (ver combinaciones).
    separados = []
    for p in out:
        if p["tipo"] != "cta" or len(p["partes"]) < 2 or p["nombre"] in origen_manual:
            continue
        textos = [transcripcion_clip(por_rel[r], crear=False) for r in p["rels"]]
        if all(t and es_llamado(t) for t in textos):
            for k, (ruta, rel) in enumerate(zip(p["partes"], p["rels"])):
                nb, na = sin_angulo(p["nombre"])  # CTA1~IZQ → CTA1B~IZQ: la letra va antes del ángulo
                separados.append({**p, "nombre": con_angulo(nb + ("" if k == 0 else chr(ord("A") + k)), na), "partes": [ruta],
                                  "original": ruta, "rel": rel, "rels": [rel], "frases": {}, "faltan_partes": [],
                                  "nombres_partes": [p["nombres_partes"][k]],
                                  "alterna": p["nombre"]})
            p["separado"] = True
    out = [p for p in out if not p.get("separado")] + separados
    for p in out:
        p["angulo"] = sin_angulo(p["nombre"])[1]
    orden = {"hook": 0, "cuerpo": 1, "cta": 2}
    return sorted(out, key=lambda p: (orden[p["tipo"]], p["nombre"]))


DESCARTADOS = "_DESCARTADOS"  # dentro de Clips/: lo que el equipo descartó (no se borra; se puede recuperar)


# ---------- Ordenar la sesión: cada clip con el nombre de su pieza ----------

def _carpeta_de_codigo(codigo):
    if re.search(r"_H\d", codigo):
        return "Clips/Hooks"
    if "CTA" in codigo.upper():
        return "Clips/CTA"
    return "Clips/Cuerpo"


def _carpeta_destino(sesion, codigo):
    """A dónde va un clip suelto ya identificado. Una carpeta por video (Clips/C1/ con sus hooks y sus frases,
    Clips/CTA/), salvo que la sesión ya use la forma vieja (Clips/Hooks, Clips/Cuerpo con videos dentro) o tenga
    sus carpetas C1/, CTA/ fuera de Clips/ (entonces van ahí, junto a las demás)."""
    vieja = any(any(es_video(f) for f in (sesion / d).rglob("*")) for d in ("Clips/Hooks", "Clips/Cuerpo") if (sesion / d).is_dir())
    if vieja:
        return _carpeta_de_codigo(codigo)
    fuera = bool(carpetas_de_piezas(sesion))
    dentro = (sesion / "Clips").is_dir() and any(d.is_dir() and re.fullmatch(r"[CP]\d+|CTA", d.name, re.I)
                                                 for d in (sesion / "Clips").iterdir())
    base = "" if fuera and not dentro else "Clips/"
    return base + ("CTA" if "CTA" in codigo.upper() else codigo.split("_")[0])


def plan_renombrar():
    """Lo que haría «Ordenar y renombrar»: cada clip ya identificado pasa a Clips/Hooks|Cuerpo|CTA con el
    código de su pieza (IMG_1058.MOV → Clips/Hooks/C2_H3.MOV). [{sesion, viejo, nuevo, pieza}] + los que no se pueden."""
    asig = leer_asignaciones()
    cambios, problemas, destinos = [], [], set()
    usados = {r for p in piezas() for r in p["rels"]}
    for c in clips():
        if c["rel"] not in usados:
            continue
        codigo = (asig.get(c["rel"]) or nombre_automatico(c) or "").upper()
        if not codigo:
            continue
        sesion, rel = c["rel"].split("/", 1) if S.es_grupo else (S.semana, c["rel"])
        ext = c["ruta"].suffix.upper() if c["ruta"].suffix.lower() in EXT_VID else ""
        # el ángulo lo dice la carpeta («Clips/C1/Ángulo 2/C1_P3.MOV»), no el nombre del archivo
        base, ang = sin_angulo_codigo(codigo)
        if c.get("a_mano"):
            # ordenado a mano en C1/, CTA/…: no se saca de su carpeta, y si su nombre ya dice la pieza, ni se toca
            if leer_nombre(nombre_clip(c["ruta"]))[0] in (codigo, base):
                continue
            carpeta, quiere = Path(rel).parent, ("CTA" if "CTA" in base else base.split("_")[0])
            if carpeta.name.upper() != quiere and re.fullmatch(r"[CP]\d+|CTA", carpeta.name, re.I):
                carpeta = carpeta.parent / quiere  # estaba en C2/ pero es de C3: va a la carpeta de al lado
            nuevo = f"{carpeta.as_posix()}/{base}{ext}"
        else:
            nuevo = (f"{_carpeta_destino(S.raiz / sesion, base)}/" + (f"{nombre_angulo(ang)}/" if ang else "")
                     + f"{base}{ext}")
        if rel == nuevo:
            continue
        if (sesion, nuevo.lower()) in destinos or (S.raiz / sesion / nuevo).exists():
            problemas.append(f"{c['ruta'].name}: ya hay un archivo {nuevo}")
            continue
        destinos.add((sesion, nuevo.lower()))
        cambios.append({"sesion": sesion, "viejo": rel, "nuevo": nuevo, "pieza": codigo, "rel": c["rel"]})
    return {"cambios": cambios, "problemas": problemas}


def aplicar_renombre(cambio):
    """Ya movido en Drive: mueve la copia local y actualiza todo lo que recordaba el nombre viejo (asignación,
    procesado y transcripción), para que la sala no lo tome como un clip nuevo ni vuelva a procesarlo."""
    sesion, viejo, nuevo = cambio["sesion"], cambio["viejo"], cambio["nuevo"]
    f_viejo, f_nuevo = S.raiz / sesion / viejo, S.raiz / sesion / nuevo
    f_nuevo.parent.mkdir(parents=True, exist_ok=True)
    if f_viejo.exists():
        f_viejo.replace(f_nuevo)
    d = _leer_asig(sesion)
    d.pop(viejo, None)
    d[nuevo] = cambio["pieza"]  # el nombre ya lo dice, pero se deja asignado a mano por si acaso
    _escribir_asig(sesion, d)
    # la firma del procesado guarda la ruta de cada parte: se cambia la ruta, no se reprocesa
    for firma in S.proc.glob("*.partes.json"):
        try:
            datos = json.loads(firma.read_text(encoding="utf-8"))
        except ValueError:
            continue
        partes = datos.get("partes") if isinstance(datos, dict) else datos
        if partes and str(f_viejo) in partes:
            partes = [str(f_nuevo) if x == str(f_viejo) else x for x in partes]
            datos = {**datos, "partes": partes} if isinstance(datos, dict) else partes
            firma.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    rel_nuevo = f"{sesion}/{nuevo}" if S.es_grupo else nuevo
    marcar_auto(rel_nuevo)  # lo movió la sala, no una persona: el análisis lo puede corregir si estaba mal
    t_viejo = _archivo_transcripcion({"rel": cambio["rel"]})
    if t_viejo.exists():
        t_viejo.replace(_archivo_transcripcion({"rel": rel_nuevo}))


GUION_CANDADO = threading.RLock()  # leer, cambiar y escribir el guion de a uno (sala, importar, armar con lo grabado)


def omitir_pieza(nombre, omitir=True):
    """«No se grabó»: la pieza sigue en el guion, pero ya no se pide en «Piezas por grabar» (un hook o CTA que se
    decidió no usar). Se guarda en el guion (produccion.json), así lo ven todas las PCs; se puede volver a pedir."""
    with GUION_CANDADO:
        f = next((x for x in (S.guion / "produccion.json", S.grab / "produccion.json") if x.exists()), None)
        if not f:
            raise ValueError("Esta sesión no tiene guion (produccion.json)")
        prod = json.loads(f.read_text(encoding="utf-8"))
        pz = prod.get("piezas", {}).get(nombre)
        if pz is None:
            raise ValueError(f"{nombre} no está en el guion")
        if omitir:
            pz["omitida"] = time.strftime("%Y-%m-%d %H:%M")
        else:
            pz.pop("omitida", None)
        if f == S.guion / "produccion.json":
            _guardar_produccion(prod)
        else:
            f.write_text(json.dumps(prod, ensure_ascii=False, indent=1), encoding="utf-8")
            global _prod_cache
            _prod_cache = None


def editar_texto_pieza(nombre, texto, partes=None):
    """Cambia lo que dice una pieza en el guion (produccion.json): se ve en Guion, en el teleprompter y en el panel."""
    with GUION_CANDADO:
        f = next((x for x in (S.guion / "produccion.json", S.grab / "produccion.json") if x.exists()), None)
        if not f:
            raise ValueError("Esta sesión no tiene guion (produccion.json)")
        prod = json.loads(f.read_text(encoding="utf-8"))
        pz = prod.get("piezas", {}).get(nombre)
        if pz is None:
            raise ValueError(f"{nombre} no está en el guion")
        partes = [" ".join(str(p).split()) for p in (partes or []) if str(p).strip()]
        if len(partes) > 1:
            pz["partes"] = partes
            pz["texto"] = " ".join(partes)
        else:
            # una sola frase (o ninguna en la lista): sin «partes», si no el teleprompter seguía con las frases viejas
            pz.pop("partes", None)
            pz["texto"] = partes[0] if partes else " ".join(str(texto).split())
        pz["editado_en_sala"] = time.strftime("%Y-%m-%d %H:%M")
        pz["editado_en_sala_t"] = time.time()
        if f == S.guion / "produccion.json":
            _guardar_produccion(prod)
        else:
            f.write_text(json.dumps(prod, ensure_ascii=False, indent=1), encoding="utf-8")
            global _prod_cache
            _prod_cache = None
        return pz


def descartar_clips(rels, recuperar=False):
    """Mueve clips a Clips/_DESCARTADOS (o los devuelve a Clips/). Devuelve [(sesión, rel_viejo, rel_nuevo)] para hacer
    lo mismo en Drive. rel como en clips(): en un grupo empieza con «sesión/»."""
    hechos = []
    for rel in rels:
        sesion, sub = (rel.split("/", 1) if S.es_grupo else (S.semana, rel))
        if sesion not in S.miembros or ".." in sub:
            continue
        origen = S.raiz / sesion / sub
        if not origen.is_file():
            continue
        nuevo = f"Clips/{origen.name}" if recuperar else f"Clips/{DESCARTADOS}/{origen.name}"
        destino = S.raiz / sesion / nuevo
        destino.parent.mkdir(parents=True, exist_ok=True)
        origen.replace(destino)
        guardar_asignacion(rel, "")  # ya no es de ninguna pieza
        hechos.append((sesion, sub, nuevo))
    return hechos


def clips_descartados():
    out = []
    for m in S.miembros:
        d = S.raiz / m / "Clips" / DESCARTADOS
        if d.is_dir():
            out += [{"rel": (f"{m}/" if S.es_grupo else "") + f"Clips/{DESCARTADOS}/{f.name}", "nombre": f.name}
                    for f in sorted(d.iterdir()) if es_video(f)]
    return out


def clips_sin_asignar():
    usados = {r for p in piezas() for r in p["rels"]}
    return [c for c in clips() if c["rel"] not in usados]


def buscar_asset(aid):
    """El archivo de Flow con ese id. Si hay dos (C1_IMG1.jpg y C1_IMG1.png, uno traído de vuelta por Drive),
    el más nuevo: antes salía el primero en orden alfabético, que solía ser el viejo."""
    return _indice_assets().get(str(aid).lower())


_INDICE_ASSETS = {"clave": None, "mapa": {}}


def _indice_assets():
    """{id en minúsculas: archivo más nuevo} de la carpeta Flow, leída una sola vez mientras no cambie (su fecha de
    modificación cambia al entrar, salir o renombrarse un archivo). Antes buscar_asset() recorría la carpeta entera por
    cada imagen: con 414 fotos, checklist() hacía 219 000 consultas al disco y tardaba ~30 s."""
    try:
        st = S.assets.stat()
    except OSError:
        return {}
    clave = (str(S.assets), st.st_mtime_ns)
    if _INDICE_ASSETS["clave"] != clave:
        mapa, fechas = {}, {}
        with os.scandir(S.assets) as it:
            for e in it:
                ext = os.path.splitext(e.name)[1].lower()
                if ext not in EXT_IMG | EXT_VID or not e.is_file():
                    continue
                k, t = os.path.splitext(e.name)[0].lower(), e.stat().st_mtime
                if k not in fechas or t > fechas[k]:
                    mapa[k], fechas[k] = Path(e.path), t
        _INDICE_ASSETS.update(clave=clave, mapa=mapa)
    return _INDICE_ASSETS["mapa"]


# ---------- 1. Procesar ----------

def limites_de_voz(video):
    """Devuelve (inicio, fin) de la parte con voz, quitando silencios de los extremos."""
    total = duracion(video)
    r = run([FFMPEG, "-hide_banner", "-i", str(video), "-vn", "-af", "silencedetect=n=-35dB:d=0.25", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    ini, fin = 0.0, total
    # el primer silencio empieza casi al inicio (un clic o un roce de hasta 0,4 s antes no cuenta como voz)
    if starts and starts[0] <= 0.4 and ends:
        ini = max(0.0, ends[0] - 0.10)
    if starts and (len(ends) < len(starts) or ends[-1] >= total - 0.05):
        fin = min(total, starts[-1] + 0.15)
    return ini, fin


_modelo = None
_AVANCE_VOZ = [print]  # a quién se avisa que se baja el modelo de voz (lo pone el trabajo que transcribe)


def _modelo_bajado(nombre):
    """¿El modelo de voz ya está en esta PC? (faster-whisper lo guarda en la caché de Hugging Face)."""
    hub = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"
    return any(hub.glob(f"models--*faster-whisper-{nombre}*")) if hub.exists() else False


def transcribir(video):
    global _modelo
    import numpy as np
    from faster_whisper import WhisperModel
    if _modelo is None:
        if not _modelo_bajado(CONFIG["transcripcion"]["modelo"]):
            _AVANCE_VOZ[0]("Bajando el modelo que entiende la voz (solo la primera vez, ~1,5 GB: puede tardar varios minutos)…")
        _modelo = WhisperModel(CONFIG["transcripcion"]["modelo"], device="cpu", compute_type="int8")
    pcm = subprocess.run(creationflags=_prioridad_baja(), args=[FFMPEG, "-hide_banner", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True).stdout
    segs, _ = _modelo.transcribe(np.frombuffer(pcm, dtype=np.float32), language="es", word_timestamps=True,
                                 vad_filter=True, initial_prompt=CONFIG["transcripcion"]["vocabulario"])
    out = []
    for s in segs:  # se va transcribiendo de a pedazos: se puede cancelar en el medio
        revisar_cancelado()
        out += [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for w in s.words]
    return corregir_palabras(out)


# Lo que la transcripción escribe mal siempre igual vive en el perfil de la marca (perfil.py: «correcciones», que
# perfil.aplicar pone en config transcripcion.correcciones). Aquí solo queda lo que sirve para cualquier marca.
CORRECCIONES_MARCA = {}
PERFIL = {}  # el perfil de la marca ya aplicado (perfil.aplicar)


_TABLA_CORR = {"de": None, "tabla": {}}


def _tabla_correcciones():
    """{palabras normalizadas: corrección}: las de la marca + las de config (las de config mandan). Se arma una vez."""
    todas = {**CORRECCIONES_MARCA, **(CONFIG.get("transcripcion", {}).get("correcciones") or {})}
    if _TABLA_CORR["de"] != todas:
        _TABLA_CORR.update(de=todas, tabla={" ".join(norm(x) for x in k.split()): v for k, v in todas.items()})
    return _TABLA_CORR["tabla"]


def firma_correcciones():
    return hashlib.md5(json.dumps(_tabla_correcciones(), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]


def corregir_palabras(pal):
    """Arregla lo que la transcripción escribe mal siempre igual («suplebet» → Suplevet, «calostro ovino» → calostro
    bovino, «laucina» → leucina…). Si la corrección tiene varias palabras, el tiempo se reparte entre ellas; la
    puntuación de la última y la mayúscula del principio se quedan."""
    tabla = _tabla_correcciones()
    out, i = [], 0
    while i < len(pal):
        for largo in (5, 4, 3, 2, 1):
            grupo = pal[i:i + largo]
            clave = " ".join(_norm_palabra(w["w"]) for w in grupo)
            if len(grupo) == largo and clave in tabla:
                cola = re.search(r"[.,;:?!…]*$", grupo[-1]["w"]).group(0)  # la puntuación de la última se queda
                cabeza = re.match(r"^[¿¡«\"']*", grupo[0]["w"]).group(0)
                nuevas = tabla[clave].split()
                if grupo[0]["w"].lstrip("¿¡«\"'")[:1].isupper():
                    nuevas[0] = nuevas[0][:1].upper() + nuevas[0][1:]
                s0, e0 = grupo[0]["s"], grupo[-1]["e"]
                pesos = [max(1, len(x)) for x in nuevas]
                t = s0
                for k, w in enumerate(nuevas):
                    fin = e0 if k == len(nuevas) - 1 else t + (e0 - s0) * pesos[k] / sum(pesos)
                    out.append({**grupo[min(k, len(grupo) - 1)], "w": (cabeza if k == 0 else "") + w + (cola if k == len(nuevas) - 1 else ""),
                                "s": round(t, 3), "e": round(fin, 3)})
                    t = fin
                i += largo
                break
        else:
            out.append(pal[i])
            i += 1
    return out


def corregir_texto(texto):
    """Lo mismo, para un texto suelto (lo que se escuchó de un clip)."""
    return " ".join(w["w"] for w in corregir_palabras([{"w": x, "s": 0, "e": 0} for x in str(texto or "").split()]))


# ---------- Dónde empieza y termina de verdad cada palabra ----------
# La transcripción estira una palabra sobre el silencio de al lado (C11_H3: «Médico» de 0,00 a 1,08 s, pero la voz
# entra en 0,74 s). Eso hace que al cortar el silencio del inicio se vaya también la palabra. Se corrige con la onda
# del audio: el inicio de cada palabra pasa a donde empieza su tramo de voz, y el final a donde termina.
AFINADO = 1  # versión del afinado guardada en el subtítulo (si cambia, se vuelve a afinar)


def _tramos_de_voz(video, paso=0.02):
    """[(desde, hasta)] donde suena la voz, con la energía del audio cada 20 ms (umbral según el ruido de fondo y la voz
    de ESTE clip; pausas de menos de 0,12 s no cortan un tramo)."""
    import numpy as np
    r = subprocess.run(creationflags=_prioridad_baja(), args=[FFMPEG, "-v", "error", "-i", str(video), "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768
    v = int(16000 * paso)
    if len(a) < v * 5:
        return []
    n = len(a) // v
    db = 20 * np.log10(np.sqrt((a[:n * v].reshape(n, v) ** 2).mean(axis=1)) + 1e-9)
    umbral = max(np.percentile(db, 10) + 12, np.percentile(db, 90) - 30)
    voz = db > umbral
    tramos, ini = [], None
    for i, x in enumerate(voz):
        if x and ini is None:
            ini = i
        elif not x and ini is not None:
            tramos.append([ini * paso, i * paso])
            ini = None
    if ini is not None:
        tramos.append([ini * paso, n * paso])
    juntos = []
    for t in tramos:
        if juntos and t[0] - juntos[-1][1] < 0.12:
            juntos[-1][1] = t[1]
        else:
            juntos.append(t)
    return juntos


def afinar_palabras(pal, tramos):
    """Corre el inicio de cada palabra a donde empieza su tramo de voz (y el final a donde termina), solo si la
    transcripción la estiró más de 0,12 s sobre un silencio. No cambia el texto ni el orden."""
    if not tramos:
        return pal
    def tramo_en(t):
        return next((x for x in tramos if x[0] <= t <= x[1]), None)

    def callado(a, b):  # entre a y b no hay voz (a lo más un ruido corto: un clic, un roce)
        return not any(x[0] < b and x[1] > a and min(x[1], b) - max(x[0], a) >= 0.3 for x in tramos)
    out, prev_e = [], 0.0
    for w in pal:
        w = dict(w)
        s, e = float(w["s"]), float(w["e"])
        minimo = min(0.35, 0.045 * len(re.sub(r"\W", "", w.get("w", ""))))  # una palabra larga no puede durar casi nada
        if e - s > 0.2:
            tr = tramo_en(max(s, e - 0.06))  # el tramo donde termina la palabra es el de su voz
            if tr and tr[0] > s + 0.12 and callado(s, tr[0] - 0.02):
                s = min(max(s, prev_e, tr[0] - 0.04), e - minimo)
            tr = tramo_en(min(e, s + 0.06))
            if tr and tr[1] < e - 0.2 and callado(tr[1] + 0.02, e):
                e = max(s + minimo, s + 0.08, tr[1] + 0.06)
        w["s"], w["e"] = round(s, 3), round(max(e, s + 0.05), 3)
        prev_e = w["e"]
        out.append(w)
    return out


def afinar_subtitulo(nombre, tramos=None):
    """Afina los tiempos de las palabras de un subtítulo ya hecho (el texto, las líneas y las marcas se quedan)."""
    f, video = S.subs / f"{nombre}.json", S.proc / f"{nombre}.mp4"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return False
    if d.get("afinado") == AFINADO or not video.exists():
        return False
    tramos = _tramos_de_voz(video) if tramos is None else tramos
    cambio = False
    for ln in d.get("lineas", []):
        pal = ln.get("palabras") or []
        nuevas = afinar_palabras(pal, tramos)
        if [(w["s"], w["e"]) for w in nuevas] != [(w["s"], w["e"]) for w in pal]:
            cambio = True
        ln["palabras"] = nuevas
        if nuevas:
            ln["s"], ln["e"] = nuevas[0]["s"], nuevas[-1]["e"]
    d["afinado"] = AFINADO
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return cambio


def corregir_subtitulo(nombre):
    """Aplica las correcciones a un subtítulo ya hecho, sin rehacerlo (se quedan los tiempos, las líneas y las marcas).
    Solo los que salieron de la transcripción sin tocar a mano; se marca con qué correcciones se revisó."""
    f = S.subs / f"{nombre}.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return False
    firma = firma_correcciones()
    if d.get("correcciones") == firma or not d.get("automatico", True):
        return False
    # toda la pieza junta: una corrección puede cruzar de una línea a la siguiente («proteína de» | «suelo de
    # Suplevet»); antes se revisaba línea por línea y esas quedaban sin corregir
    lineas = d.get("lineas", [])
    todas = [w for ln in lineas for w in (ln.get("palabras") or [])]
    nuevas = corregir_palabras(todas)
    cambio = [w["w"] for w in nuevas] != [w["w"] for w in todas]
    if cambio:
        inicios = [float((ln.get("palabras") or [{"s": ln.get("s", 0)}])[0]["s"]) for ln in lineas]
        por_linea = [[] for _ in lineas]
        for w in nuevas:  # cada palabra vuelve a la línea donde empieza
            k = max([i for i, s0 in enumerate(inicios) if s0 <= float(w["s"]) + 1e-6] or [0])
            por_linea[k].append(w)
        for ln, nuevas_ln in zip(lineas, por_linea):
            pal = ln.get("palabras") or []
            if [w["w"] for w in nuevas_ln] == [w["w"] for w in pal]:
                continue
            if ln.get("texto", "") == " ".join(w["w"] for w in pal):  # el texto no se tocó a mano: se rehace con lo corregido
                ln["texto"] = " ".join(w["w"] for w in nuevas_ln)
            ln["palabras"] = nuevas_ln
    d["correcciones"] = firma
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return cambio


def agrupar_lineas(palabras):
    maxw = CONFIG["subtitulos"]["max_palabras_por_linea"]
    lineas, actual = [], []
    for i, p in enumerate(palabras):
        actual.append(p)
        corte = (len(actual) >= maxw or p["w"][-1:] in ".,?!;:"
                 or (i + 1 < len(palabras) and palabras[i + 1]["s"] - p["e"] > 0.35))
        if corte or i == len(palabras) - 1:
            lineas.append({"texto": " ".join(x["w"] for x in actual), "s": actual[0]["s"],
                           "e": actual[-1]["e"], "palabras": actual})
            actual = []
    return lineas


# Un cuadro clave cada medio segundo en las piezas: el editor salta de un corte a otro sin trabarse (con uno cada 3–8 s,
# cada salto tenía que decodificar segundos de video antes de mostrar algo)
GOP_EDITOR = ["-g", "15", "-keyint_min", "15"]


def _con_gop(firma):
    try:
        d = json.loads(firma.read_text(encoding="utf-8"))
        return isinstance(d, dict) and str(d.get("gop")) == GOP_EDITOR[1]
    except (FileNotFoundError, ValueError):
        return False


# Cómo se unen las frases de un cuerpo grabado en partes. Hasta la 1.54 se unían copiando el audio AAC de cada parte:
# en cada unión se perdían ~30 ms de audio y la voz se iba adelantando (en P05/C1 llegaba a 0,22 s antes que la boca).
# Desde la 2: cada parte guarda el audio sin comprimir (PCM) y al unir se comprime una sola vez, siguiendo la hora de
# cada parte (aresample async): la voz queda pegada a la imagen. Las piezas unidas antes se rehacen solas.
UNION_PARTES = 2
# Volumen de la voz en dos pasadas: primero se mide el clip entero y después se le aplica un solo nivel parejo
# (loudnorm «linear»), en vez de ir adivinando mientras avanza. Y al armar, antes, un compresor suave empareja lo que
# viene de la grabación: al empezar cada clip se habla más fuerte (en P05, de 3 a 8 dB más el primer segundo); con el
# compresor la diferencia baja a la mitad o menos.
NORMA = 2
NIVELAR_VOZ = "acompressor=threshold=-24dB:ratio=3:attack=15:release=250:makeup=2"
_LOUD = "I=-16:TP=-1.5:LRA=11"


def filtro_volumen_parejo(entrada, filtros_antes="", ss=None, to=None):
    """«loudnorm=…:linear=true» con lo medido del audio (una pasada que solo lee el sonido). Si no se puede medir,
    el loudnorm de siempre."""
    cmd = [FFMPEG, "-hide_banner", "-nostats"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.2f}"]
    if to is not None:
        cmd += ["-to", f"{to:.2f}"]
    cmd += ["-i", str(entrada), "-vn", "-af", (f"{filtros_antes}," if filtros_antes else "") + f"loudnorm={_LOUD}:print_format=json",
            "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, creationflags=_prioridad_baja(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
        if float(m["input_i"]) < -60:  # casi silencio: no se mide bien
            raise ValueError
        return (f"loudnorm={_LOUD}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    except (ValueError, KeyError, subprocess.SubprocessError, OSError):
        return f"loudnorm={_LOUD}"





COLCHON_FIN = 0.6  # segundos de más al final de cada frase al preparar (el armado corta el silencio solo)
REPREPARAR = set()  # piezas que se pidió preparar de nuevo («Traer más del clip»)

# Ediciones de una pieza que van en segundos de la pieza (no de una parte): si las partes cambian de largo, se corren
_CLAVES_T = ("t", "a", "b")
_SIN_CORRER = ("recorte", "partes", "zoom_partes", "velocidad_partes", "quitar", "orden", "editado", "voz_revisada")


def _correr_tiempos_pieza(nombre, durs_viejas, durs_nuevas):
    """La pieza se volvió a preparar con partes de otro largo (cada una arranca igual, pero termina después): todo lo
    que va en segundos de la pieza se corre con su parte. Subtítulos, cortes, tramos quitados, zoom, imágenes,
    audio desagrupado y duplicados. Lo de dentro de cada parte (recortes de la parte) no cambia."""
    ini_v, ini_n, a, b = [], [], 0.0, 0.0
    for dv, dn in zip(durs_viejas, durs_nuevas):
        ini_v.append(a); ini_n.append(b)
        a += dv; b += dn
    if all(abs(x - y) < 1e-3 for x, y in zip(ini_v, ini_n)):
        return  # solo cambió el final de la última parte: nada se corre

    def f(t):
        try:
            t = float(t)
        except (TypeError, ValueError):
            return t
        k = max([i for i, x in enumerate(ini_v) if x <= t + 1e-6] or [0])
        return round(t + ini_n[k] - ini_v[k], 3)

    def correr(v, clave=None):
        if isinstance(v, dict):
            return {k: (f(x) if k in _CLAVES_T and isinstance(x, (int, float)) else x if k in _SIN_CORRER else correr(x, k))
                    for k, x in v.items()}
        if isinstance(v, list):
            if clave == "cortes":  # segundos sueltos
                return [f(x) if isinstance(x, (int, float)) else x for x in v]
            if clave in ("quitar_rangos", "duplicados"):  # [[desde, hasta], …]
                return [[f(x[0]), f(x[1]), *x[2:]] if isinstance(x, list) and len(x) >= 2 else x for x in v]
            return [correr(x, clave) for x in v]
        return v
    sub = S.subs / f"{nombre}.json"
    if sub.exists():
        d = json.loads(sub.read_text(encoding="utf-8"))
        for ln in d.get("lineas") or []:
            ln["s"], ln["e"] = f(ln.get("s", 0)), f(ln.get("e", 0))
            for w in ln.get("palabras") or []:
                w["s"], w["e"] = f(w.get("s", 0)), f(w.get("e", 0))
        sub.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    todo = ajustes_piezas()
    cambio = False
    for clave in [k for k in todo if k == nombre or k.startswith(nombre + "@")]:
        todo[clave] = correr(todo[clave])
        cambio = True
    if cambio:
        _ajustes_piezas_path().write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")


def preparar_de_nuevo(nombre, avance=print):
    """«Traer más del clip»: vuelve a preparar UNA pieza con el colchón al final de cada frase, sin correr nada
    de lo que ya se editó (cada parte arranca donde arrancaba; lo de después se corre con ella)."""
    if not any(p["nombre"] == nombre for p in piezas()):
        raise ValueError(f"No encontré la pieza {nombre}")
    REPREPARAR.add(nombre)
    try:
        procesar(avance=avance)
    finally:
        REPREPARAR.discard(nombre)
    return [c["codigo"] for c in combinaciones() if nombre in (c["hook"], c["cuerpo"], c["cta"])]


def procesar(forzar_subs=False, avance=print):
    _AVANCE_VOZ[0] = avance
    for d in (S.proc, S.subs):
        d.mkdir(parents=True, exist_ok=True)
    escuchar_tomas_largas(avance)
    ps = piezas()
    for n, p in enumerate(ps, 1):
        dest = S.proc / f"{p['nombre']}.mp4"
        firma = S.proc / f"{p['nombre']}.partes.json"
        lista = [str(x) for x in p["partes"]]
        vieja = json.loads(firma.read_text(encoding="utf-8")) if firma.exists() else None
        firma_vieja = vieja if isinstance(vieja, dict) else {}
        formato_viejo = vieja.get("formato", "vertical") if isinstance(vieja, dict) else "vertical"
        union_vieja = vieja.get("union") if isinstance(vieja, dict) else None
        if isinstance(vieja, dict):
            vieja = vieja.get("partes")
        desfasada = len(lista) > 1 and union_vieja != UNION_PARTES  # unida a la manera vieja: la voz se adelantaba
        # las mismas partes aunque la pieza se haya preparado en otra PC (bajada de Drive, con sus rutas)
        mismas = isinstance(vieja, list) and [_rel_parte(x) for x in vieja] == [_rel_parte(x) for x in lista]
        if (not dest.exists() or not mismas or formato_viejo != formato() or desfasada or p["nombre"] in REPREPARAR
                or dest.stat().st_mtime < max(x.stat().st_mtime for x in p["partes"])):
            avance(f"Procesando {p['nombre']} ({n}/{len(ps)})" + (f" · {len(lista)} partes" if len(lista) > 1 else ""))
            trozos, cortes = [], []
            W, H = lienzo()
            fps_p = fps_para_piezas(p["partes"])  # 60 si se grabó a 60 (antes todo bajaba a 30 y se perdía la mitad)
            cortes_viejos = firma_vieja.get("cortes") if mismas and not desfasada else None
            for k, parte in enumerate(p["partes"]):
                ini, fin = limites_de_voz(parte)
                if cortes_viejos and k < len(cortes_viejos):  # se vuelve a preparar: cada parte arranca donde arrancaba
                    ini = round(cortes_viejos[k][0], 2 if "fps" not in firma_vieja else 3)  # (así nada se corre)
                # un colchón al final de cada frase: el silencio lo corta solo el armado, y en el editor se puede alargar
                # el clip hasta ahí (antes la pieza terminaba donde el detector creía que acababa la voz, y si la frase
                # terminaba suave, se cortaba y no se podía alargar)
                fin = min(duracion(parte), fin + COLCHON_FIN)
                # un número entero de cuadros: así la imagen y la voz de cada parte duran lo mismo (si no, al unir
                # las partes la voz se iba corriendo un poco en cada una)
                n_cuadros = max(1, round((fin - ini) * fps_p))
                fin = ini + n_cuadros / fps_p
                cortes.append([round(ini, 3), round(fin, 3)])  # para ubicar las palabras del clip en la pieza
                sola = len(lista) == 1
                t = dest if sola else S.proc / f"_{p['nombre']}_{k}.mov"  # en partes: audio sin comprimir hasta unir
                run([FFMPEG, "-hide_banner", "-y", "-ss", f"{ini:.3f}", "-t", f"{n_cuadros / fps_p:.6f}", "-i", str(parte),
                     "-frames:v", str(n_cuadros),
                     "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps_p},setsar=1",
                     "-af", f"{filtro_volumen_parejo(parte, ss=ini, to=fin)},aresample=48000", "-ac", "2", "-dn", "-map_chapters", "-1",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", *GOP_EDITOR,
                     *(["-c:a", "aac", "-b:a", "192k"] if sola else ["-c:a", "pcm_s16le"]), str(t)])
                trozos.append(t)
            duraciones = [round(duracion(t), 3) for t in trozos]
            if len(trozos) > 1:
                lst = S.proc / f"_{p['nombre']}.txt"
                lst.write_text("".join(f"file '{t.name}'\n" for t in trozos), encoding="utf-8")
                run([FFMPEG, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c:v", "copy",
                     "-af", "aresample=async=1:first_pts=0", "-c:a", "aac", "-b:a", "192k", dest.name], cwd=S.proc)
                for t in trozos + [lst]:
                    t.unlink(missing_ok=True)
            firma.write_text(json.dumps({"partes": lista, "duraciones": duraciones, "cortes": cortes, "formato": formato(),
                                         "gop": GOP_EDITOR[1], "union": UNION_PARTES, "norma": NORMA, "fps": fps_p,
                                         "colchon": COLCHON_FIN}, ensure_ascii=False), encoding="utf-8")
            if cortes_viejos and firma_vieja.get("duraciones"):  # partes más largas: lo de después se corre con ellas
                _correr_tiempos_pieza(p["nombre"], firma_vieja["duraciones"], duraciones)
            REPREPARAR.discard(p["nombre"])
            if vieja is not None and (not mismas or desfasada):  # con la voz en su lugar, los tiempos cambian
                # otra toma: el subtítulo se rehace con lo que se dice ahora, pero las marcas (imágenes, sonidos,
                # resaltados) se quedan porque van por palabra; el subtítulo viejo se guarda por si se quiere volver
                _apartar_subs_viejos(p["nombre"])
        elif not _con_gop(firma):
            # pieza preparada antes de la 1.37: un cuadro clave cada 3–8 s hacía que el editor se trabara en cada corte;
            # se recomprime solo la imagen (el audio, los tiempos y los subtítulos no cambian)
            avance(f"Ajustando {p['nombre']} para que el editor no se trabe ({n}/{len(ps)})")
            tmp = dest.with_name(f"_{dest.stem}.gop.mp4")
            run([FFMPEG, "-hide_banner", "-y", "-i", str(dest), "-map", "0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 *GOP_EDITOR, "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(tmp)])
            os.replace(tmp, dest)
            d = json.loads(firma.read_text(encoding="utf-8")) if firma.exists() else None
            d = d if isinstance(d, dict) else {"partes": lista, "formato": formato()}  # firma muy vieja (solo la lista)
            firma.write_text(json.dumps({**d, "gop": GOP_EDITOR[1]}, ensure_ascii=False), encoding="utf-8")
        sub = S.subs / f"{p['nombre']}.json"
        modelo = CONFIG["transcripcion"]["modelo"]
        try:
            viejo = json.loads(sub.read_text(encoding="utf-8")) if sub.exists() else None
        except ValueError:
            viejo = None
        # un subtítulo que salió solo de la transcripción (nadie lo tocó) y se hizo con otro modelo se rehace con el
        # de ahora, que entiende mejor; uno corregido a mano no se toca nunca
        mejorar = bool(viejo and viejo.get("automatico") and viejo.get("modelo") != modelo)
        if forzar_subs or not sub.exists() or mejorar:
            ya_oido = None if mejorar else palabras_de_pieza(p["nombre"])  # lo que se escuchó de sus clips al analizar
            if ya_oido:
                avance(f"Subtítulos de {p['nombre']} con lo ya escuchado ({n}/{len(ps)})")
            else:
                avance(f"Transcribiendo {p['nombre']} ({n}/{len(ps)})" + (" con el modelo más fino" if mejorar else ""))
            pal = afinar_palabras(ya_oido or transcribir(dest), _tramos_de_voz(dest))  # cada palabra donde de verdad suena
            heredadas = _marcas_apartadas(p["nombre"])
            marcas = ({"marcas": viejo["marcas"]} if (mejorar or forzar_subs) and viejo and "marcas" in viejo
                      else {"marcas": heredadas} if heredadas is not None else {})  # van por palabra: se quedan
            sub.write_text(json.dumps({"pieza": p["nombre"], "modelo": modelo, "automatico": True, "correcciones": firma_correcciones(),
                                       "afinado": AFINADO,
                                       "lineas": agrupar_lineas(pal), **marcas}, ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            if corregir_subtitulo(p["nombre"]):
                avance(f"Corrigiendo palabras de {p['nombre']} ({n}/{len(ps)})")
            if afinar_subtitulo(p["nombre"]):
                avance(f"Afinando dónde empieza cada palabra de {p['nombre']} ({n}/{len(ps)})")


def _apartar_subs_viejos(nombre):
    """Guarda el subtítulo de una pieza en subtitulos/<sesión>/_reemplazados/ (con fecha) antes de rehacerlo."""
    sub = S.subs / f"{nombre}.json"
    if not sub.exists():
        return
    guardar = S.subs / "_reemplazados"
    guardar.mkdir(parents=True, exist_ok=True)
    os.replace(sub, guardar / f"{nombre}.{time.strftime('%Y%m%d-%H%M%S')}.json")


def _marcas_apartadas(nombre):
    """Las marcas del último subtítulo apartado de esa pieza (o None)."""
    guardar = S.subs / "_reemplazados"
    if not guardar.is_dir():
        return None
    for f in sorted(guardar.glob(f"{nombre}.*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if d.get("pieza", nombre) == nombre and "marcas" in d:
            return d["marcas"]
        return None
    return None


# ---------- Sugerir a qué pieza corresponde cada clip ----------

def _archivo_transcripcion(c):
    return S.transcripciones / (re.sub(r"[^\w.-]", "_", c["rel"]) + ".json")


def transcripcion_clip(c, crear=True):
    """Texto de un clip (en caché; se rehace si el archivo cambió)."""
    f = _archivo_transcripcion(c)
    firma = f"{c['ruta'].stat().st_size}-{int(c['ruta'].stat().st_mtime)}"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("firma") == firma:
            return d["texto"]
    if not crear:
        return None
    pal = transcribir(c["ruta"])
    texto = " ".join(p["w"] for p in pal)
    f.parent.mkdir(parents=True, exist_ok=True)
    # las palabras con sus tiempos también: al preparar la pieza, sus subtítulos salen de aquí sin volver a escuchar
    f.write_text(json.dumps({"firma": firma, "texto": texto, "palabras": pal}, ensure_ascii=False), encoding="utf-8")
    return texto


def palabras_clip(ruta):
    """Las palabras (con sus tiempos dentro del clip) que se guardaron al escuchar ese clip, o None si no están
    (el clip se escuchó con una versión vieja o cambió después)."""
    c = next((x for x in clips() if Path(x["ruta"]) == Path(ruta)), None)
    if not c:
        return None
    f = _archivo_transcripcion(c)
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        st = Path(ruta).stat()
    except (OSError, ValueError):
        return None
    if d.get("firma") != f"{st.st_size}-{int(st.st_mtime)}" or not isinstance(d.get("palabras"), list):
        return None
    return d["palabras"]


def _rel_parte(ruta):
    """Una parte (clip grabado) relativa a la carpeta de la sesión: «Clips/IMG_1346.MOV». Sirve para comparar piezas
    preparadas en otra PC, cuyas rutas empiezan con la carpeta de esa PC."""
    p = Path(ruta)
    try:
        return p.relative_to(S.grab).as_posix()
    except ValueError:
        partes = [x.lower() for x in p.parts]
        nombre = S.grab.name.lower()
        if nombre in partes:
            i = len(partes) - 1 - partes[::-1].index(nombre)
            return "/".join(p.parts[i + 1:])
        return p.as_posix()


def palabras_de_pieza(nombre):
    """Las palabras de una pieza ya preparada, armadas con lo que se escuchó de cada clip al analizar: de cada parte,
    lo que cae entre donde se cortó (inicio y fin de la voz), corrido a donde quedó esa parte en la pieza. None si falta
    algo (entonces se transcribe la pieza como antes). Así preparar no vuelve a escuchar cada pieza: era la mitad del
    tiempo de «Analizar clips»."""
    try:
        firma = json.loads((S.proc / f"{nombre}.partes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    partes, cortes, durs = firma.get("partes") or [], firma.get("cortes"), firma.get("duraciones")
    if not partes or not cortes or not durs or not len(partes) == len(cortes) == len(durs):
        return None
    out, offset = [], 0.0
    for ruta, (ini, fin), dur in zip(partes, cortes, durs):
        pal = palabras_clip(S.grab / _rel_parte(ruta))  # la pieza pudo prepararse en otra PC
        if pal is None:
            return None
        for w in pal:
            s, e = float(w["s"]) - ini, float(w["e"]) - ini
            if e <= 0.02 or s >= (fin - ini) - 0.02:  # quedó fuera de lo que se cortó
                continue
            out.append({"w": w["w"], "s": round(offset + max(0.0, s), 2), "e": round(offset + min(e, dur), 2)})
        offset += dur
    return corregir_palabras(out) if out else None


def escuchar_clips(avance=print):
    """Transcribe los clips sin asignar para poder sugerir de qué pieza son."""
    _AVANCE_VOZ[0] = avance
    pendientes = clips_sin_asignar()
    for n, c in enumerate(pendientes, 1):
        avance(f"Escuchando {c['ruta'].name} ({n}/{len(pendientes)})")
        transcripcion_clip(c)


def _parecido(a, b):
    import difflib
    wa, wb = [norm(x) for x in a.split() if norm(x)], [norm(x) for x in b.split() if norm(x)]
    if not wa or not wb:
        return 0.0
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()


def _fecha_iso(texto):
    """«2026-10-19T15:32:10-0500» o «…Z» → segundos; None si no se entiende."""
    import datetime
    t = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", str(texto).strip().replace("Z", "+00:00"))
    try:
        return datetime.datetime.fromisoformat(t).timestamp()
    except ValueError:
        return None


def fecha_grabacion(c):
    """Cuándo se grabó el clip: la fecha que el celular guarda dentro del video (no cambia al copiarlo a Drive ni
    entre celulares); si no la trae, la del archivo. En caché, junto a lo escuchado."""
    f = S.transcripciones / "_grabado.json"
    try:
        cache = json.loads(f.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        cache = {}
    st = c["ruta"].stat()
    firma = f"{st.st_size}-{int(st.st_mtime)}"
    if cache.get(c["rel"], {}).get("firma") == firma:
        return cache[c["rel"]]["t"]
    t, modelo = None, None
    try:
        tags = json.loads(run([FFPROBE, "-v", "error", "-show_entries", "format_tags", "-of", "json", str(c["ruta"])]).stdout or "{}")
        tags = {k.lower(): v for k, v in tags.get("format", {}).get("tags", {}).items()}
        t = _fecha_iso(tags.get("com.apple.quicktime.creationdate", "")) or _fecha_iso(tags.get("creation_time", ""))
        modelo = tags.get("com.apple.quicktime.model") or tags.get("model")  # con dos celulares a la vez, cada uno es un ángulo
    except Exception:
        pass
    t = t or st.st_mtime
    cache[c["rel"]] = {"firma": firma, "t": t, **({"m": modelo} if modelo else {})}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return t


def _grabaciones(cs):
    """{rel: {t: hora de grabación, m: modelo del celular o None}} de varios clips, leyendo la caché una sola vez."""
    try:
        cache = json.loads((S.transcripciones / "_grabado.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        cache = {}
    out = {}
    for c in cs:
        try:
            st = c["ruta"].stat()
        except OSError:
            out[c["rel"]] = {"t": 0}
            continue
        d = cache.get(c["rel"]) or {}
        out[c["rel"]] = d if d.get("firma") == f"{st.st_size}-{int(st.st_mtime)}" else {"t": fecha_grabacion(c)}
    return out


def _cuerpo_de_pieza(n):
    """A qué guion pertenece una pieza: «C3_P2», «C3_H1», «C3_CTA1» y «CTA3» → «C3»; «CTA-A» → «CTA-A»."""
    if m := re.match(r"([CP]\d+)", n, re.I):
        return m.group(1).upper()
    if m := re.fullmatch(r"CTA-?(\d+)(?:_P\d+)?", n, re.I):
        return f"C{int(m.group(1))}"
    return n.split("_")[0].upper()


def angulos_por_pasadas(pieza_de, por_rel):
    """{rel: ángulo} cuando un guion se grabó completo dos veces, una por ángulo (todo en un ángulo y después todo
    en el otro): la 1.ª pasada es la de frente («»), la 2.ª «A2», la 3.ª «A3». Se mira guion por guion, en el orden
    en que se grabó: una pieza que vuelve después de haber pasado por otras tres empieza otra pasada (una que se
    repite enseguida es solo otra toma). Una pasada que no repite ni la mitad de la primera no es otro ángulo: son
    tomas repetidas y se quedan con la pasada de antes."""
    grupos = {}
    for rel, pieza in pieza_de.items():
        grupos.setdefault(_cuerpo_de_pieza(pieza), []).append(rel)
    out = {}
    for rels in grupos.values():
        rels.sort(key=lambda r: (fecha_grabacion(por_rel[r]), r))
        pasadas = [[]]
        for r in rels:
            vistas = [pieza_de[x] for x in pasadas[-1]]
            p = pieza_de[r]
            if p in vistas and vistas[-1] != p and len(set(vistas[vistas.index(p) + 1:]) - {p}) >= 3:
                pasadas.append([])
            pasadas[-1].append(r)
        juntas = [pasadas[0]]
        for pas in pasadas[1:]:
            if 2 * len({pieza_de[x] for x in pas}) >= len({pieza_de[x] for x in juntas[0]}):
                juntas.append(pas)
            else:
                juntas[-1] += pas
        tope = max(1, int(CONFIG.get("angulos_maximos", 2)))  # con dos cámaras, una tercera pasada son tomas repetidas
        if len(juntas) > tope:
            juntas = juntas[:tope - 1] + [[r for pas in juntas[tope - 1:] for r in pas]]
        if len(juntas) > 1:
            for k, pas in enumerate(juntas):
                out.update({r: "" if k == 0 else f"A{k + 1}" for r in pas})
    return out


def _orden_natural(texto):
    """IMG_998 antes que IMG_1000: los números se comparan como números."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(texto))]


def sugerencias():
    """{rel: {texto, pieza, parecido, mejor, otra_toma?}} de los clips que quedaron sin pieza, con el MISMO motor que
    asigna (reasignar_por_guion: orden de grabación, vecinos, pasadas). Antes había una lógica aparte que ordenaba por
    nombre y sumaba el bono de vecinos al parecido: sugería cosas como «C6_CTA_P1» con 23 % para una frase de C6.
    «mejor» = la pieza que más se le parece todavía no tiene clip (vale la pena ponerlo); si ya tiene, es otra toma."""
    r = reasignar_por_guion(guardar=False)
    ps = piezas()
    ocupadas = {p["nombre"] for p in ps} | {f"{p['nombre']}_P{k}" for p in ps for ks in p["frases"].values() for k in ks}
    out = {}
    for c in clips_sin_asignar():
        texto = transcripcion_clip(c, crear=False)
        if texto is None:
            continue
        n, v = r["candidatos"].get(c["rel"], (None, 0.0))
        # media frase de un hook o CTA que ya tiene su toma entera (C13_CTA_P2 con C13_CTA puesto) también es otra toma
        otra = bool(n) and (n.upper() in ocupadas or (tipo_de(n) != "cuerpo" and re.sub(r"_P\d+$", "", n.upper()) in ocupadas))
        out[c["rel"]] = {"texto": texto, "pieza": n, "parecido": v, "mejor": bool(n) and not otra and v >= 0.5,
                         **({"otra_toma": True} if otra else {})}
    return out


def oraciones(texto):
    """Las oraciones de un texto («Frente al parvovirus, cada decisión cuenta. ¿También estás evaluando…?» → 2)."""
    return [x.strip() for x in re.split(r"(?<=[.!?…])\s+(?=[¿¡«A-ZÁÉÍÓÚÑ])", str(texto or "").strip()) if len(x.strip()) > 3]


def numerar_hooks(cuerpo=None):
    """Asigna C1_H1, C1_H2… a los clips sin nombre de la carpeta Hooks, en el orden en que se grabaron."""
    cuerpos = [p["nombre"] for p in piezas() if p["tipo"] == "cuerpo"]
    cuerpo = (cuerpo or (cuerpos[0] if len(cuerpos) == 1 else "")).upper()
    if not cuerpo:
        raise ValueError("Hay varios cuerpos: indica a cuál pertenecen los hooks")
    usados = {int(m.group(1)) for p in piezas() if (m := re.fullmatch(rf"{cuerpo}_H(\d+)", p["nombre"]))}
    n, hechas = 1, 0
    for c in clips_sin_asignar():
        if c["tipo_carpeta"] != "hook":
            continue
        while n in usados:
            n += 1
        guardar_asignacion(c["rel"], f"{cuerpo}_H{n}")
        usados.add(n)
        hechas += 1
    return hechas


def informe_clips():
    """Después de analizar: qué clips no se pudieron identificar solos y por qué, y qué piezas del guion
    todavía no tienen clip. Todo con lo que ya se escuchó en esta PC (no usa Claude)."""
    esperadas, mitades = _esperadas()
    guion_toks = {n: _toks(t) for n, t in esperadas.items()}
    dueno = {}  # pieza → nombre del clip que la tiene
    for p in piezas():
        for k, r in enumerate(p["rels"], 1):
            if len(p["rels"]) == 1 and not p["frases"]:
                dueno[p["nombre"]] = Path(r).name
            for n in p["frases"].get(r) or [k]:  # por el número de la frase (C2.3 es la 3 aunque falte la 2)
                dueno[f"{p['nombre']}_P{n}"] = Path(r).name
    sug, rp = sugerencias(), reemplazos()
    dudas = []
    for c in clips_sin_asignar():
        nombre, s = c["ruta"].name, sug.get(c["rel"])
        texto = transcripcion_clip(c, crear=False)
        if c["rel"] in rp:
            r = rp[c["rel"]]
            dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "reemplazado",
                          "por_que": f"Era {r['pieza']}; lo cambiaste por {r['por']}. Si ya no sirve, descártalo."})
            continue
        auto = nombre_automatico(c)
        if auto and (m := PARTE.match(auto)):  # «C3.1» suelto cuando ya está «C3.1 HASTA C3.4»
            dueno_toma = next((Path(r).name for p in piezas() if p["nombre"] == m.group(1).upper() for r in p["rels"]
                               if r != c["rel"] and (rg := leer_nombre(nombre_clip(Path(r)))[1]) and rg[0] <= int(m.group(2)) <= rg[1]), None)
            if dueno_toma:
                dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "otra_toma", "pieza": auto,
                              "por_que": f"Es otra toma de la frase {int(m.group(2))} de {m.group(1).upper()}, que ya está en «{dueno_toma}». "
                                         "Si prefieres esta, asígnala a mano; si no, descártala."})
                continue
        if texto is None:
            dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "sin_escuchar", "por_que": "Todavía no se escuchó: toca «Analizar y renombrar todo»."})
            continue
        if len(texto.split()) < 3:
            dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "sin_voz",
                          "por_que": "Casi no se habla en el clip (¿toma de prueba o se cortó?)." + (f" Se oyó: «{texto}»." if texto else "")})
            continue
        cerca = max(_puntajes(texto, guion_toks).items(), key=lambda x: x[1], default=(None, 0))
        pct = lambda x: f"{round(x * 100)} %"
        if cerca[0] and cerca[0] in dueno:
            dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "otra_toma", "pieza": cerca[0],
                          "por_que": f"Es otra toma de {cerca[0]} ({pct(cerca[1])} parecido), que ya tiene su clip ({dueno[cerca[0]]}). Si esta es mejor, asígnala y reemplaza."})
        elif s and s.get("pieza") and not s.get("mejor"):
            dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "otra_toma", "pieza": s["pieza"],
                          "por_que": f"Parece {s['pieza']} ({pct(s['parecido'])}), pero hay otra toma más parecida sin asignar."})
        elif s and s.get("pieza"):
            dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "poco_parecido", "pieza": s["pieza"],
                          "por_que": f"Parece {s['pieza']}, pero solo se parece {pct(s['parecido'])} al guion: se dijo con otras palabras. Revísalo y asígnalo a mano."})
        else:
            dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "no_se_parece", "pieza": cerca[0],
                          "por_que": f"No se parece a ninguna pieza del guion" + (f" (lo más cercano: {cerca[0]}, {pct(cerca[1])})" if cerca[0] else "")
                                     + ". ¿Se improvisó o es de otra sesión?"})
    hay = set(dueno)
    # los CTA valen como se grabaron: un CTA del guion sin clip no falta si ese cuerpo ya tiene uno grabado
    con_cta = {h.split("_")[0] for h in hay if "CTA" in h.upper()}
    faltan = [n for n in esperadas if n not in hay and not any(h.startswith(n + "_P") for h in hay)
              and not (re.match(r".+_P\d+$", n) and n.rsplit("_P", 1)[0] in mitades)  # media frase de un hook: cuenta el hook
              and not (produccion().get("piezas", {}).get(n) or {}).get("omitida")  # «No se grabó»
              and not (re.match(r".+_P\d+$", n) and n.rsplit("_P", 1)[0] in hay)
              and not ("CTA" in n.upper() and (n.split("_")[0] in con_cta or "_" not in n and con_cta))]
    # una frase que no está entre las grabadas (C2.1, C2.2, C2.3, C2.5): el video saltaría la 4
    faltan += [f"{p['nombre']}_P{k}" for p in piezas() for k in p["faltan_partes"] if f"{p['nombre']}_P{k}" not in faltan]
    return {"dudas": dudas, "faltan": faltan, "texto": {d["rel"]: (sug.get(d["rel"]) or {}).get("texto") for d in dudas}}


def aceptar_sugerencias(minimo=0.5):
    """Después de escuchar: vuelve a decidir de qué pieza es cada clip (ver reasignar_por_guion). Devuelve cuántos
    clips quedaron con pieza."""
    return reasignar_por_guion(minimo)["asignados"]


# ---------- Asignar todo de nuevo con el guion de hoy ----------
# Antes solo se miraban los clips sin pieza: lo que se asignó con un guion anterior (otra versión, otra numeración)
# se quedaba para siempre, ocupaba las piezas y dejaba sin clip a las de verdad (P05: los clips del guion viejo de
# traumatología seguían en C2 y los nuevos de C2 quedaban sueltos). Ahora, cada vez que se analiza, todos los clips
# sueltos se vuelven a comparar con el guion actual. Solo se respeta lo asignado a mano y lo ordenado en carpetas.

def _asig_mano_archivo(sesion):
    return DATOS / "asignaciones" / f"{S.en_drive(sesion)}.a_mano.json"


def marcar_a_mano(rel, si=True):
    """Lo que alguien asignó a mano no lo vuelve a cambiar el análisis."""
    sesion = S.semana
    if S.es_grupo:
        sesion, rel = rel.split("/", 1)
    f = _asig_mano_archivo(sesion)
    try:
        d = set(json.loads(f.read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError, OSError):
        d = set()
    (d.add if si else d.discard)(rel)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(d), ensure_ascii=False, indent=1), encoding="utf-8")


def asignados_a_mano():
    out = set()
    for m in S.miembros:
        try:
            rels = json.loads(_asig_mano_archivo(m).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            continue
        out |= {f"{m}/{r}" if S.es_grupo else r for r in rels}
    return out


def _esperadas():
    """({pieza o frase: texto}, {hook o CTA de varias oraciones: cuántas}). Como en sugerencias()."""
    esperadas, mitades = {}, {}
    for n, i in produccion().get("piezas", {}).items():
        if not i.get("texto"):
            continue
        esperadas[n] = i["texto"]
        tipo = tipo_de(n)  # por el nombre: en algunos guiones «tipo» guarda el estilo del hook («Pregunta», «Dato»…)
        if tipo == "cuerpo" and len(i.get("partes") or []) > 1:
            for k, frase in enumerate(i["partes"], 1):
                esperadas[f"{n}_P{k}"] = frase
        elif tipo in ("hook", "cta") and len(fr := oraciones(i["texto"])) > 1:
            mitades[n] = len(fr)
            for k, frase in enumerate(fr, 1):
                esperadas[f"{n}_P{k}"] = frase
    return esperadas, mitades


# ---------- Puntaje de un clip contra el guion ----------
# Antes cada palabra del guion se volvía a normalizar en cada comparación (82 % del tiempo): con 512 clips tardaba
# ~2 min. Ahora se normaliza una vez y se saltan los pares que no pueden llegar a 0,3 (mismos resultados, ~20× más rápido).
_norm_palabra = __import__("functools").lru_cache(maxsize=200000)(lambda w: norm(w))


def _toks(texto):
    return tuple(x for x in map(_norm_palabra, str(texto or "").split()) if x)


def _puntajes(texto, guion_toks):
    """{pieza o frase: puntaje de 0 a 1}. El puntaje es el mayor entre «qué tan igual es» (ratio) y, si el clip no es
    mucho más largo que la frase, «cuánto de la frase se dijo» (mitad ratio, mitad cobertura): así una retoma con un
    falso arranque («Después del parto… ya. Después del parto, la madre…») le gana a media frase."""
    import difflib
    a = _toks(texto)
    if not a:
        return {n: 0.0 for n in guion_toks}
    sm = difflib.SequenceMatcher(None, autojunk=False)
    sm.set_seq2(a)
    la, out = len(a), {}
    for n, b in guion_toks.items():
        lb = len(b)
        if not lb:
            out[n] = 0.0
            continue
        sm.set_seq1(b)
        cob = lb >= 4 and la <= 2.2 * lb
        qr = sm.quick_ratio()
        tope = max(qr, 0.5 * qr + 0.5 * min(1.0, qr * (la + lb) / (2 * lb))) if cob else qr
        if tope < 0.3:
            out[n] = 0.0
            continue
        m = sum(x.size for x in sm.get_matching_blocks())
        r = 2 * m / (la + lb)
        out[n] = max(r, 0.5 * r + 0.5 * m / lb) if cob else r
    return out


_CACHE_PUNTAJES = {}  # (texto, firma del guion) → puntajes: el texto de un clip no cambia entre análisis


def _firma_guion(esperadas):
    return hashlib.md5(json.dumps(esperadas, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _asig_auto_archivo(sesion):
    return DATOS / "asignaciones" / f"{S.en_drive(sesion)}.auto.json"


def marcar_auto(rel):
    """Un clip que la sala movió a su carpeta (Clips/C2/…) al renombrar: sigue siendo de la sala, no «ordenado a mano»
    (si después se ve que era de otra pieza, el análisis lo puede corregir)."""
    sesion = S.semana
    if S.es_grupo:
        sesion, rel = rel.split("/", 1)
    f = _asig_auto_archivo(sesion)
    try:
        d = set(json.loads(f.read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError, OSError):
        d = set()
    d.add(rel)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(d), ensure_ascii=False, indent=1), encoding="utf-8")


def renombrados_por_la_sala():
    out = set()
    for m in S.miembros:
        try:
            rels = json.loads(_asig_auto_archivo(m).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            continue
        out |= {f"{m}/{r}" if S.es_grupo else r for r in rels}
    return out


_CACHE_REASIGNAR = {"clave": None, "res": None}


def _clave_reasignar(minimo):
    archivos = [f for m in S.miembros for f in _asig_archivos(m)] + [_asig_mano_archivo(m) for m in S.miembros]
    firmas = []
    for c in clips():
        try:
            st = c["ruta"].stat()
            firmas.append((c["rel"], st.st_size, int(st.st_mtime)))
        except OSError:
            pass
    t = lambda f: f.stat().st_mtime if f.exists() else 0
    prod = next((f for f in (S.guion / "produccion.json", S.grab / "produccion.json") if f.exists()), None)
    return (S.semana, minimo, prod and t(prod), tuple(firmas), tuple(t(f) for f in archivos),
            t(S.transcripciones) if S.transcripciones.exists() else 0, len(list(S.transcripciones.glob("*.json"))) if S.transcripciones.exists() else 0)


def reasignar_por_guion(minimo=0.5, guardar=True):
    """Decide de nuevo, con el guion de hoy, la pieza de cada clip suelto (los de carpetas ordenadas y los asignados a
    mano no se tocan; los que la sala movió al renombrar, sí). Cómo:
    1. Cada clip se compara con cada pieza y con cada frase del guion (qué tan igual es y cuánto de la frase se dijo).
    2. Los clips se ordenan por la hora en que se grabaron: un clip dudoso (media frase) va con el cuerpo que se estaba
       grabando (el de los clips seguros de antes y de después). Un hook o CTA dicho en dos clips seguidos se une.
    3. Cada cuerpo se separa en pasadas (todo el cuerpo de un tirón; cuando vuelve atrás, pasa más de media hora o lo
       graba otro celular a la vez, es otra pasada = otro ángulo). Un cuerpo se arma SOLO con clips de su pasada: nunca
       se mezclan ángulos. Una pasada corta (unas frases repetidas) solo rellena lo que le falta a la pasada completa más
       cercana, y solo si está a menos de 20 minutos.
    4. En cada pasada, de varias tomas de la misma frase gana la más parecida al guion (si empatan, la última). Una frase
       dicha con otras palabras entra si está justo entre la anterior y la siguiente de su pasada.
    5. Lo que no se parece lo suficiente, o es de otro cuerpo que todo lo que lo rodea, queda sin pieza para revisar.
    Devuelve {asignados, cambiados, sin_pieza, nuevo: {rel: pieza}, candidatos: {rel: (pieza, puntaje)}}."""
    esperadas, mitades = _esperadas()
    if not esperadas:
        return {"asignados": 0, "cambiados": 0, "sin_pieza": 0, "nuevo": {}, "candidatos": {}}
    clave = _clave_reasignar(minimo)
    if _CACHE_REASIGNAR["clave"] == clave and not guardar:
        return _CACHE_REASIGNAR["res"]
    manual, actuales, auto = asignados_a_mano(), leer_asignaciones(), renombrados_por_la_sala()
    todos = clips()
    fijo = lambda c: (c.get("a_mano") and c["rel"] not in auto) or c["rel"] in manual
    libres = [c for c in todos if not fijo(c)]
    ocupadas = set()
    for c in todos:
        if fijo(c):
            p = actuales.get(c["rel"]) or nombre_automatico(c)
            if p:
                ocupadas.add(p.upper())
    guion_toks = {n: _toks(t) for n, t in esperadas.items()}
    firma = _firma_guion(esperadas)
    if len(_CACHE_PUNTAJES) > 5000:
        _CACHE_PUNTAJES.clear()
    orden = []
    for c in libres:
        texto = transcripcion_clip(c, crear=False)
        if texto is None:
            continue  # sin escuchar: se queda como está
        texto = corregir_texto(texto)
        k = (texto, firma)
        if k not in _CACHE_PUNTAJES:
            _CACHE_PUNTAJES[k] = _puntajes(texto, guion_toks)
        orden.append((c, texto, _CACHE_PUNTAJES[k]))
    grab = _grabaciones([c for c, _, _ in orden])
    orden.sort(key=lambda x: (grab[x[0]["rel"]]["t"], _orden_natural(x[0]["rel"])))
    hora = lambda i: grab[orden[i][0]["rel"]]["t"]
    modelo = lambda i: grab[orden[i][0]["rel"]].get("m")
    cuerpos = {n: _cuerpo_de_pieza(n) for n in esperadas if re.match(r"[CP]\d+", n, re.I)}
    cuerpo_de = cuerpos.get
    seguros = {}
    for i, (_, _, pts) in enumerate(orden):
        n, v = max(pts.items(), key=lambda x: x[1], default=(None, 0))
        # seguro = casi igual a una pieza y sin empate con otro cuerpo (la misma frase en dos guiones no marca nada)
        otro = max((x for k, x in pts.items() if cuerpo_de(k) != cuerpo_de(n)), default=0)
        if v >= 0.75 and cuerpo_de(n) and v - otro >= 0.05:
            seguros[i] = cuerpo_de(n)
    largo = lambda t: len(str(t).split())
    cand = {}  # i → (pieza del guion, puntaje, lejos)
    for i, (c, texto, pts) in enumerate(orden):
        # los vecinos seguros cuentan solo si se grabaron cerca (15 min): un cuerpo de otra hora no decide nada
        antes = next((seguros[j] for j in range(i - 1, max(-1, i - 9), -1) if j in seguros and hora(i) - hora(j) <= 900), None)
        despues = next((seguros[j] for j in range(i + 1, min(len(orden), i + 9)) if j in seguros and hora(j) - hora(i) <= 900), None)
        bono = lambda n, v: v + 0.1 * ((cuerpo_de(n) == antes) + (cuerpo_de(n) == despues)) if cuerpo_de(n) else v
        n, _ = max(((n, bono(n, v)) for n, v in pts.items()), key=lambda x: x[1], default=(None, 0))
        if not n or pts[n] < 0.35:
            continue
        # una mitad de hook o CTA, si el clip trae la frase entera (su largo se acerca más al de la pieza que al de la
        # mitad), es la pieza entera
        if (m := re.match(r"^(.+)_P\d+$", n)) and m[1] in mitades and pts.get(m[1], 0) >= 0.6 \
                and abs(largo(texto) - largo(esperadas[m[1]])) < abs(largo(texto) - largo(esperadas[n])):
            n = m[1]
        # de otro cuerpo que lo que se grabó antes y después: no se pone solo, salvo que sea casi igual al guion
        lejos = cuerpo_de(n) and antes and despues and cuerpo_de(n) not in (antes, despues) and pts[n] < 0.85
        cand[i] = (n, pts[n], bool(lejos))
    candidatos = {orden[i][0]["rel"]: (n, round(v, 2)) for i, (n, v, _) in cand.items()}
    # un hook o CTA dicho en dos clips seguidos (por ejemplo cortado en la coma): si juntos se parecen claramente más a
    # la pieza que cada uno solo, son la pieza en dos partes (C6_H1_P1 + C6_H1_P2)
    pares = {}
    bases = [n for n in esperadas if tipo_de(n) in ("hook", "cta") and not re.search(r"_P\d+$", n)]
    for i in range(len(orden) - 1):
        j = i + 1
        if hora(j) - hora(i) > 90 or i in pares or (modelo(i) and modelo(j) and modelo(i) != modelo(j)):
            continue
        ti, tj = orden[i][1], orden[j][1]
        if largo(ti) < 2 or largo(tj) < 2:
            continue
        mejor = None
        for base in bases:
            si, sj = orden[i][2].get(base, 0), orden[j][2].get(base, 0)
            if max(si, sj) < 0.25 or (cand.get(i, (None, 0))[1] >= 0.9 and cand[i][0] != base and cand[i][0] != f"{base}_P1"):
                continue
            junto = _puntajes(f"{ti} {tj}", {base: guion_toks[base]})[base]
            if junto >= 0.7 and junto >= max(si, sj) + 0.15 and (not mejor or junto > mejor[1]):
                mejor = (base, junto)
        if mejor:
            pares[i] = pares[j] = mejor[0]
            cand[i] = (f"{mejor[0]}_P1", mejor[1], False)
            cand[j] = (f"{mejor[0]}_P2", mejor[1], False)
    en_dos = {b: 2 for b in set(pares.values())}
    # solo lo que de verdad se parece arma las pasadas (un clip dudoso no debe partir ni abrir una pasada)
    cand = {i: x for i, x in cand.items() if x[1] >= minimo and not x[2]}
    tope = max(1, int(CONFIG.get("angulos_maximos", 2)))
    partes_de = {n: len(i.get("partes") or []) for n, i in produccion().get("piezas", {}).items() if tipo_de(n) == "cuerpo"}
    nuevo = {}

    def pasadas_de(idx, num, n_partes):
        """Parte una lista (en el orden en que se grabó) en pasadas. num(i) → número de frase (cuerpo) o None.
        Otra pasada empieza cuando se vuelve atrás dos o más frases a una que ya está (P8 → P1), cuando una pieza
        vuelve después de otras tres (hooks: H6 → H1), o tras más de media hora sin grabar ese cuerpo."""
        pasadas = [[]]
        for i in idx:
            actual = pasadas[-1]
            if actual and hora(i) - hora(actual[-1]) > 1800:
                pasadas.append([])
            elif actual:
                vistas = [cand[x][0] for x in actual]
                p, k, ult = cand[i][0], num(i), num(actual[-1])
                if k is not None and ult is not None:
                    otra = k in {num(x) for x in actual} and ult - k >= 2
                else:
                    otra = p in vistas and vistas[-1] != p and len(set(vistas[vistas.index(p) + 1:]) - {p}) >= 3
                if otra:
                    pasadas.append([])
            pasadas[-1].append(i)
        pasadas = [x for x in pasadas if x]
        # completa = un cuerpo con al menos 3/4 de sus frases (o, en hooks, la mitad de la pasada más grande). Una
        # pasada corta son tomas repetidas o un falso arranque: rellena la completa más cercana si está a menos de 20 min
        cuantas = lambda pas: len({cand[x][0] for x in pas})
        largo_max = max((cuantas(pas) for pas in pasadas), default=0)
        umbral = 0.75 * n_partes if n_partes else largo_max / 2
        llenas = [{"propios": pas, "relleno": []} for pas in pasadas if cuantas(pas) >= umbral]
        if not llenas and pasadas:  # nada completo (un cuerpo grabado a medias): la pasada más grande
            grande = max(pasadas, key=cuantas)
            llenas = [{"propios": grande, "relleno": []}]
        usadas = {id(x["propios"]) for x in llenas}
        for pas in pasadas:
            if id(pas) in usadas:
                continue
            medio = (hora(pas[0]) + hora(pas[-1])) / 2
            dist = lambda j: min(abs(medio - hora(j["propios"][0])), abs(medio - hora(j["propios"][-1])))
            cerca = min(llenas, key=dist)
            if dist(cerca) <= 1200:
                cerca["relleno"] += pas
        return llenas

    def elegir(pas, ang, cu=None, num=None):
        elegido = {}
        for grupo in ("propios", "relleno"):
            for i in pas[grupo]:
                n, v, _ = cand[i]
                if grupo == "relleno" and n in elegido and elegido[n][2] == "propios":
                    continue  # el relleno no reemplaza lo de la pasada
                # de dos tomas casi iguales (3 puntos o menos de diferencia) gana la última: la repetida suele ser la buena
                if n not in elegido or v > elegido[n][0] + 0.03 or (v >= elegido[n][0] - 0.03 and i > elegido[n][1]):
                    elegido[n] = (v, i, grupo)
        # hook o CTA en dos clips: solo si están todas las mitades y no hay una toma entera buena. Si falta una mitad
        # pero la que hay trae casi toda la pieza (un CTA dicho más corto), esa es la pieza entera.
        for base, cuantas in {**mitades, **en_dos}.items():
            partes = [f"{base}_P{j}" for j in range(1, cuantas + 1)]
            if any(x in elegido for x in partes):
                if (base in elegido and elegido[base][0] >= 0.5) or not all(x in elegido for x in partes):
                    sueltas = [elegido.pop(x) for x in partes if x in elegido]
                    if base not in elegido:
                        v, i, g = max(sueltas, key=lambda t: orden[t[1]][2].get(base, 0))
                        if orden[i][2].get(base, 0) >= 0.6:
                            elegido[base] = (orden[i][2][base], i, g)
        # una frase dicha con otras palabras: si cae justo entre la anterior y la siguiente de esta pasada, es esa
        if cu and num:
            usados = {x[1] for x in elegido.values()}
            for k in range(1, (partes_de.get(cu) or 0) + 1):
                nk = f"{cu}_P{k}"
                if nk in elegido or f"{cu}_P{k - 1}" not in elegido:
                    continue
                desde = elegido[f"{cu}_P{k - 1}"][1]
                hasta = elegido[f"{cu}_P{k + 1}"][1] if f"{cu}_P{k + 1}" in elegido else min(len(orden), desde + 3)
                if hasta <= desde:
                    continue
                if f"{cu}_P{k + 1}" not in elegido and hora(min(hasta, len(orden) - 1)) - hora(desde) > 120:
                    hasta = desde + 2  # al final de la pasada: solo el clip de justo después
                opciones = [(orden[j][2].get(nk, 0), j) for j in range(desde + 1, min(hasta, len(orden)))
                            if j not in usados and orden[j][0]["rel"] not in nuevo and largo(orden[j][1]) >= 3]
                v, j = max(opciones, default=(0, None))
                if j is not None and v >= 0.35 and v >= max(orden[j][2].values()) - 0.15:
                    elegido[nk] = (v, j, "orden")
                    usados.add(j)
        for n, (v, i, _) in elegido.items():
            pieza = con_angulo(n, ang).upper() if ang else n.upper()
            if pieza not in ocupadas:
                nuevo[orden[i][0]["rel"]] = pieza

    # cada cuerpo: sus frases (C10, C10_P1…) por un lado y sus hooks y CTA por otro. Un cuerpo de otro ángulo es
    # otra pasada; los hooks se cuentan aparte (casi siempre se graban una sola vez, antes del cuerpo). Si dos celulares
    # graban el mismo cuerpo a la vez, cada celular es su propia pasada (si no, se intercalan y se mezclan).
    grupos = {}
    for i, (n, _, _) in cand.items():
        cu = _cuerpo_de_pieza(n)
        es_cuerpo = re.fullmatch(rf"{re.escape(cu)}(?:_P\d+)?", n, re.I) is not None
        grupos.setdefault((cu, es_cuerpo), []).append(i)
    for (cu, es_cuerpo), idx in grupos.items():
        num = (lambda i: int(m[1]) if (m := re.search(r"_P(\d+)$", cand[i][0])) else 0) if es_cuerpo else (lambda i: None)
        por_modelo = {}
        for i in idx:
            por_modelo.setdefault(modelo(i), []).append(i)
        rangos = [(hora(v[0]), hora(v[-1])) for v in por_modelo.values()]
        a_la_vez = len(por_modelo) > 1 and any(a0 < b1 and b0 < a1 for x, (a0, a1) in enumerate(rangos) for (b0, b1) in rangos[x + 1:])
        llenas = []
        for sub in (por_modelo.values() if a_la_vez else [idx]):
            llenas += pasadas_de(sub, num, partes_de.get(cu) if es_cuerpo else 0)
        llenas.sort(key=lambda pas: hora(pas["propios"][0]))
        for k, pas in enumerate(llenas[:tope]):  # con más pasadas que ángulos, las de sobra quedan como otras tomas
            elegir(pas, f"A{k + 1}" if k else "", cu if es_cuerpo else None, num if es_cuerpo else None)
    # ---- Cuerpos dichos con otras palabras (un guion que cambió al grabar): manda el orden de grabación ----
    # Una frase cambiada no se parece al guion, pero se grabó en su lugar: entre la frase anterior y la siguiente, o
    # justo después de la última. El CTA va después del cuerpo y un hook que falta, justo antes del siguiente hook.
    pos = {orden[i][0]["rel"]: i for i in range(len(orden))}
    libre = lambda j: orden[j][0]["rel"] not in nuevo
    basura = lambda j: largo(orden[j][1]) < 3
    retoma = lambda j: max(orden[j][2].values(), default=0) >= 0.75  # casi igual a otra pieza: es otra toma
    import perfil as _perfil  # las frases con las que la marca pide la compra
    cta_re = re.compile(r"escr[ií]benos|whats ?app|comun[ií]cate|solicita(r)? (m[aá]s )?informaci|mayor informaci", re.I)
    es_cta = lambda j: bool(cta_re.search(orden[j][1])) or _perfil.dice_cta(orden[j][1]) or max((v for k, v in orden[j][2].items() if tipo_de(k) == "cta"), default=0) >= 0.7
    pedacito = lambda j: largo(orden[j][1]) <= 7 and arranca_unido(orden[j][1])  # «Y recuperación.»: completa la de antes
    mismo_cel = lambda a, b: not (modelo(a) and modelo(b) and modelo(a) != modelo(b))
    arranca_unido = lambda t: bool(re.match(r"^(que|y|e|o|ni|pero|porque|con|para|además|también)\b", t.strip(" ¿¡«\"'"), re.I))
    pasadas_cuerpo = {}
    for rel, pieza in nuevo.items():
        m = PARTE.match(pieza)
        if m and tipo_de(pieza) == "cuerpo":
            pasadas_cuerpo.setdefault(m.group(1).upper(), {}).setdefault(int(m.group(2)), []).append((pos[rel], bool(m.group(3))))
    for clave, partes in pasadas_cuerpo.items():
        cu = _cuerpo_de_pieza(sin_angulo(clave)[0])
        total = partes_de.get(cu) or 0
        if not total:
            continue
        ks = sorted(partes)
        ultimo = lambda k: max(i for i, _ in partes[k])
        primero = lambda k: min(i for i, _ in partes[k])
        for a_, b_ in zip(ks, ks[1:]):
            entre = [j for j in range(ultimo(a_) + 1, primero(b_)) if libre(j) and not basura(j) and not retoma(j) and not es_cta(j)
                     and mismo_cel(j, ultimo(a_))]
            faltan = b_ - a_ - 1
            if faltan and len(entre) > faltan:  # los pedacitos que siguen a una frase no cuentan como frase
                entre = [j for j in entre if not pedacito(j)]
            if faltan and len(entre) == faltan:  # tantos clips como frases faltan: van en orden
                for k, j in zip(range(a_ + 1, b_), entre):
                    nuevo[orden[j][0]["rel"]] = f"{clave}_P{k}"
                    partes[k] = [(j, False)]
            elif not faltan and len(entre) == 1:  # ninguna falta: es la mitad de la frase de antes o de la de después
                j = entre[0]
                sig = next((i for i, b in partes[b_] if not b), None)
                if sig is not None and not any(b for _, b in partes[b_]) and arranca_unido(orden[sig][1]):
                    nuevo[orden[j][0]["rel"]] = f"{clave}_P{b_}"
                    nuevo[orden[sig][0]["rel"]] = f"{clave}_P{b_}B"
                elif arranca_unido(orden[j][1]) and not any(b for _, b in partes[a_]):
                    nuevo[orden[j][0]["rel"]] = f"{clave}_P{a_}B"
        # después de la última frase: las que faltan al final, en orden (hasta el CTA, otra pieza o una pausa de 3 min)
        k = max(partes)
        j, antes = ultimo(k) + 1, ultimo(k)
        while k < total and j < len(orden) and hora(j) - hora(antes) <= 180 and mismo_cel(j, antes):
            if not libre(j) or es_cta(j):
                break
            if not basura(j) and not retoma(j) and not pedacito(j):
                k += 1
                nuevo[orden[j][0]["rel"]] = f"{clave}_P{k}"
                partes[k] = [(j, False)]
            antes, j = j, j + 1
    # el CTA de cada cuerpo: el llamado que se grabó justo después del cuerpo (uno o dos clips seguidos)
    ctas = {n for n in esperadas if tipo_de(n) == "cta" and re.fullmatch(r"(?:C|P)\d+_CTA", n, re.I)}
    puestas = {sin_angulo_codigo(x)[0].upper() for x in nuevo.values()} | {re.sub(r"_P\d+$", "", sin_angulo_codigo(x)[0].upper()) for x in nuevo.values()}
    for cta in sorted(ctas):
        if cta.upper() in puestas:
            continue
        cu = _cuerpo_de_pieza(cta)
        fin = [pos[r] for r, x in nuevo.items() if tipo_de(x) == "cuerpo" and _cuerpo_de_pieza(sin_angulo_codigo(x)[0]) == cu]
        if not fin:
            continue
        j, antes = max(fin) + 1, max(fin)
        while j < len(orden) and hora(j) - hora(antes) <= 180 and libre(j) and (basura(j) or not es_cta(j)) and not retoma(j):
            antes, j = j, j + 1
        if j < len(orden) and hora(j) - hora(antes) <= 180 and libre(j) and es_cta(j):
            sig = j + 1
            if sig < len(orden) and libre(sig) and es_cta(sig) and hora(sig) - hora(j) <= 90 and not basura(sig):
                nuevo[orden[j][0]["rel"]], nuevo[orden[sig][0]["rel"]] = f"{cta}_P1", f"{cta}_P2"
            else:
                nuevo[orden[j][0]["rel"]] = cta
    # un hook que falta: el clip libre grabado justo antes del hook siguiente (o justo después del anterior)
    hooks = sorted((n for n in esperadas if re.fullmatch(r"(?:C|P)\d+_H\d+", n, re.I)), key=_orden_natural)
    puestas = {re.sub(r"_P\d+$", "", sin_angulo_codigo(x)[0].upper()) for x in nuevo.values()}
    donde = {}
    for r, x in nuevo.items():
        donde.setdefault(re.sub(r"_P\d+$", "", sin_angulo_codigo(x)[0].upper()), []).append(pos[r])
    for h in hooks:
        if h.upper() in puestas:
            continue
        cu, k = h.upper().split("_H")
        vecinos = [(min(donde[f"{cu}_H{int(k) + 1}"]) - 1, min(donde[f"{cu}_H{int(k) + 1}"]))] if f"{cu}_H{int(k) + 1}" in donde else []
        vecinos += [(max(donde[f"{cu}_H{int(k) - 1}"]) + 1, max(donde[f"{cu}_H{int(k) - 1}"]))] if f"{cu}_H{int(k) - 1}" in donde else []
        for j, ref in vecinos:
            if 0 <= j < len(orden) and libre(j) and not basura(j) and not es_cta(j) and abs(hora(j) - hora(ref)) <= 120 \
                    and (orden[j][2].get(h, 0) >= 0.3 or re.match(r"^(m[eé]dico )?veterinari|^doctor", orden[j][1].strip(" ¿¡«"), re.I)):
                nuevo[orden[j][0]["rel"]] = h
                donde[h.upper()] = [j]
                break
    # Una frase dicha en dos clips seguidos: el clip de al lado (grabado enseguida, sin pieza propia) se une si juntos se
    # parecen claramente más a la frase del guion («La evaluación nutricional es importante.» + «Para acompañar sus
    # necesidades…»), o si es un pedacito que sigue al anterior («…mantener la masa muscular» + «Y recuperación.»)
    sigue = re.compile(r"^(y|e|o|ni|además|también|que|con|para|porque|pero|así|sin)\b", re.I)
    usados = set(nuevo)
    pos = {orden[i][0]["rel"]: i for i in range(len(orden))}
    for rel, pieza in sorted(nuevo.items(), key=lambda x: pos[x[0]]):
        m = PARTE.match(pieza)
        es_parte = bool(m) and tipo_de(pieza) == "cuerpo" and not m.group(3)
        es_entera = not m and tipo_de(pieza) in ("hook", "cta")
        if not (es_parte or es_entera):
            continue
        i = pos[rel]
        base_guion = (f"{_cuerpo_de_pieza(sin_angulo_codigo(pieza)[0])}_P{m.group(2)}" if es_parte else sin_angulo(pieza)[0]).upper()
        base_guion = next((k for k in esperadas if k.upper() == base_guion), None)
        if not base_guion:
            continue
        propio = orden[i][2].get(base_guion, 0)
        for j, antes in ((i + 1, False), (i - 1, True)):
            # justo al lado en el orden de grabación: hasta 1 min, o 2 min y medio para un pedacito que sigue (entre
            # medio pudo haber tomas que se borraron)
            if not 0 <= j < len(orden) or orden[j][0]["rel"] in usados or abs(hora(j) - hora(i)) > 150:
                continue
            if modelo(i) and modelo(j) and modelo(i) != modelo(j):
                continue
            tj = orden[j][1]
            if largo(tj) < 1 or max(orden[j][2].values(), default=0) >= 0.75:
                continue  # ese clip es otra frase entera: no se une
            junto_txt = f"{tj} {orden[i][1]}" if antes else f"{orden[i][1]} {tj}"
            junto = _puntajes(junto_txt, {base_guion: guion_toks[base_guion]})[base_guion]
            completa = junto >= 0.6 and junto >= propio + 0.08 and abs(hora(j) - hora(i)) <= 60
            pedacito = not antes and largo(tj) <= 7 and sigue.match(tj.strip(" ¿¡«\"'")) and max(orden[j][2].values(), default=0) < 0.5
            if not (completa or pedacito):
                continue
            if es_parte:
                primero, segundo = (orden[j][0]["rel"], rel) if antes else (rel, orden[j][0]["rel"])
                nuevo[primero], nuevo[segundo] = pieza, pieza + "B"
            else:  # un hook o CTA en dos clips: sus dos partes
                primero, segundo = (orden[j][0]["rel"], rel) if antes else (rel, orden[j][0]["rel"])
                nb, na = sin_angulo(pieza)
                nuevo[primero], nuevo[segundo] = con_angulo(f"{nb}_P1", na), con_angulo(f"{nb}_P2", na)
            usados.add(orden[j][0]["rel"])
            break
    rels = [c["rel"] for c, _, _ in orden]
    cambiados = sum(1 for r in rels if (actuales.get(r) or "") != nuevo.get(r, ""))
    if guardar and cambiados:
        por_sesion = {}
        for r in rels:
            sesion, rr = r.split("/", 1) if S.es_grupo else (S.semana, r)
            por_sesion.setdefault(sesion, {})[rr] = nuevo.get(r, "")
        for sesion, cambios in por_sesion.items():
            d = _leer_asig(sesion)
            d.update(cambios)
            _escribir_asig(sesion, d)
            rp = _leer_reemp(sesion)
            if any(rp.pop(r, None) for r in list(cambios)):
                _reemp_archivo(sesion).write_text(json.dumps(rp, ensure_ascii=False, indent=1), encoding="utf-8")
    res = {"asignados": sum(1 for r in rels if nuevo.get(r)), "cambiados": cambiados,
           "sin_pieza": sum(1 for r in rels if not nuevo.get(r)), "nuevo": nuevo, "candidatos": candidatos}
    _CACHE_REASIGNAR.update(clave=_clave_reasignar(minimo), res=res)
    return res


# ---------- Director: comparar lo grabado con el guion y aplicar el plan ----------

def dossier():
    """Todo lo que Claude necesita para dirigir la edición de una sesión: por pieza, el texto del guion,
    lo que realmente se dijo (con el segundo de cada palabra), las diferencias y las marcas actuales."""
    import difflib
    prod = produccion()
    out = {"sesion": S.semana, "plantilla_sub": id_plantilla(), "palabras_clave": CONFIG["palabras_clave"],
           "categorias_sfx": sorted(set(CONFIG.get("sfx_mapa", {}))), "musica": prod.get("musica", []), "piezas": {}}
    for p in piezas():
        d = leer_subs(p["nombre"])
        palabras = [{"w": w["w"], "s": w["s"], "e": w["e"]} for ln in d["lineas"] for w in ln["palabras"]]
        dicho = " ".join(w["w"] for w in palabras)
        guion = info_pieza(p["nombre"]).get("texto", "")
        a = [norm(x) for x in guion.split() if norm(x)]
        b = [norm(w["w"]) for w in palabras if norm(w["w"])]
        cambios = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op != "equal":
                cambios.append({"tipo": {"replace": "cambió", "delete": "no se dijo", "insert": "se agregó"}[op],
                                "guion": " ".join(guion.split()[i1:i2]) if a else "", "dicho": " ".join(w for w in b[j1:j2])})
        proc = S.proc / f"{p['nombre']}.mp4"
        out["piezas"][p["nombre"]] = {
            "tipo": p["tipo"], "tipo_hook": info_pieza(p["nombre"]).get("tipo"), "partes": len(p["partes"]),
            "duracion": round(duracion(proc), 2) if proc.exists() else None,
            "guion": guion, "dicho": dicho, "parecido": round(difflib.SequenceMatcher(None, a, b).ratio(), 2) if a and b else None,
            "cambios": cambios, "palabras": palabras, "marcas": marcas_de(p["nombre"]),
            "musica": musica_de_pieza(p["nombre"]), "animo": info_pieza(p["nombre"]).get("animo")}
    return out


def aplicar_plan(plan):
    """Guarda el plan del director: marcas por pieza (en sus subtítulos) y música/videos/plantilla (en _plan.json).
    Devuelve las marcas cuya palabra no aparece en lo dicho, para corregirlas."""
    perdidas = []
    for nombre, info in plan.get("piezas", {}).items():
        if "marcas" in info:
            lineas = leer_subs(nombre)["lineas"]
            for m in info["marcas"]:
                if ubicar(lineas, m.get("palabra", ""), m.get("n", 1)) is None:
                    perdidas.append(f"{nombre}: «{m.get('palabra')}»")
            guardar_marcas(nombre, info["marcas"])
    resto = {k: v for k, v in plan.items() if k != "piezas"}
    resto["piezas"] = {n: {k: v for k, v in i.items() if k != "marcas"} for n, i in plan.get("piezas", {}).items()}
    resto["piezas"] = {n: i for n, i in resto["piezas"].items() if i}
    for k in ("formato", "formato_cambio"):  # lo elige el equipo en la sala: un plan nuevo del director no lo borra
        if k not in resto and k in plan_director():
            resto[k] = plan_director()[k]
    S.subs.mkdir(parents=True, exist_ok=True)
    _plan_path().write_text(json.dumps(resto, ensure_ascii=False, indent=1), encoding="utf-8")
    global _prod_cache
    _prod_cache = None
    return perdidas


def fijar_plantilla_sesion(pid):
    """El guion o el plan pueden fijar «plantilla_sub», y esa gana a la predeterminada. Si el equipo elige otra
    predeterminada en la sala, se anota en el plan de la sesión; si no, elegirla no cambiaba nada."""
    global _prod_cache
    if produccion().get("plantilla_sub") in (None, "", pid):
        return
    S.subs.mkdir(parents=True, exist_ok=True)
    _plan_path().write_text(json.dumps({**plan_director(), "plantilla_sub": pid}, ensure_ascii=False, indent=1), encoding="utf-8")
    _prod_cache = None


# ---------- Revisión del guion contra lo grabado ----------

def revision_guion():
    """Por pieza del guion: qué clip(s) la cubren y, si ya se procesó, lo dicho comparado palabra por palabra."""
    import difflib
    prod = produccion()
    ps = {p["nombre"]: p for p in piezas()}
    sugs = sugerencias()
    out = []
    for g in guion_para_grabar_actual():
        nombre = g["nombre"]
        p = ps.get(nombre)
        fila = {"nombre": nombre, "tipo": g["tipo"], "tipo_hook": g.get("tipo_hook"), "texto": g["texto"], "partes": g["partes"], "todos": g.get("todos") or False,
                "clips": p["rels"] if p else [], "procesada": bool(p and (S.subs / f"{nombre}.json").exists()),
                "diff": [], "parecido": None,
                # clips sin identificar que suenan a esta pieza (para asignarlos desde el guion)
                "sugeridos": sorted(({"rel": rel, "nombre": rel.split("/")[-1], "parecido": s["parecido"], "mejor": s["mejor"],
                                      "texto": s["texto"]} for rel, s in sugs.items() if s["pieza"] == nombre),
                                    key=lambda x: -x["parecido"])}
        if fila["procesada"]:
            dicho = [w["w"] for ln in leer_subs(nombre)["lineas"] for w in ln["palabras"]]
            guion = g["texto"].split()
            a, b = [norm(x) for x in guion], [norm(x) for x in dicho]
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                if op == "equal":
                    fila["diff"].append({"t": "igual", "texto": " ".join(dicho[j1:j2])})
                else:
                    if i2 > i1:
                        fila["diff"].append({"t": "falta", "texto": " ".join(guion[i1:i2])})
                    if j2 > j1:
                        fila["diff"].append({"t": "extra", "texto": " ".join(dicho[j1:j2])})
            fila["parecido"] = round(difflib.SequenceMatcher(None, a, b).ratio(), 2) if a and b else None
            fila["dicho"] = " ".join(dicho)  # lo que se dijo en el clip, para leerlo al lado del guion
        out.append(fila)
    sin_escuchar = sum(1 for c in clips_sin_asignar() if transcripcion_clip(c, crear=False) is None)
    return {"hay_guion": bool(prod.get("piezas")), "origen": prod.get("origen"), "archivo": prod.get("archivo"),
            # ya tiene imágenes, música o marcas (de Claude o de la sala): no es solo «lo que se dijo»
            "con_apoyo": bool(prod.get("assets") or prod.get("musica")
                              or any(i.get("marcas") for i in (prod.get("piezas") or {}).values())),
            "sin_prompts": faltan_prompts_flow(), "piezas": out, "sin_escuchar": sin_escuchar,
            "sin_procesar": sum(1 for f in out if f["clips"] and not f["procesada"]), "sin_asignar": len(sugs)}


def guion_para_grabar_actual():
    """El guion de la sesión o grupo abierto (sin cambiar de sesión)."""
    return guion_para_grabar(None)


# ---------- Forma de onda del audio (para cortar guiándose por la voz) ----------

def sonoridad(f):
    """Qué tan fuerte suena un sonido (LUFS integrados; si es muy corto, su promedio en dB). En caché."""
    f = Path(f)
    cache = DATOS / "ondas" / "sonoridad.json"
    try:
        tabla = json.loads(cache.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        tabla = {}
    firma = f"{f}|{f.stat().st_size}|{int(f.stat().st_mtime)}"
    if firma in tabla:
        return tabla[firma]
    r = run([FFMPEG, "-hide_banner", "-i", str(f), "-vn", "-af", "ebur128,volumedetect", "-f", "null", "-"])
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    prom = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    valor = float(i[-1]) if i and float(i[-1]) > -60 else (float(prom.group(1)) - 3 if prom else -20.0)
    tabla[firma] = valor
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(tabla), encoding="utf-8")
    return valor


def ganancia_normalizada(f):
    """Factor de volumen para que todos los sonidos suenen al mismo nivel (config sonidos.normalizar / objetivo_lufs)."""
    so = {**CONFIG["sonidos"], **CONFIG.get("sonidos_nivel", {})}  # sonidos_nivel: lo elegido en la página Sonidos
    if not so.get("normalizar", True):
        return 1.0
    try:
        db = float(so.get("objetivo_lufs", -20)) - sonoridad(f)
    except Exception:  # noqa: BLE001 — si no se puede medir, queda como está
        return 1.0
    return 10 ** (max(-15.0, min(12.0, db)) / 20)


def ganancia_musica(f, desde=0.0, largo=30.0):
    """Volumen de una pista de música: todas quedan al mismo nivel (config musica.objetivo_lufs, -31: con la voz a -16
    queda 15 dB debajo, y al hablar baja un poco más: unos 20 dB, de fondo), por más que una venga más fuerte que otra. Se mide solo el
    pedazo que suena en el video (muchas pistas empiezan suave). musica.volumen sube o baja eso (1 = tal cual)."""
    mu = CONFIG["musica"]
    try:
        db = float(mu.get("objetivo_lufs", -31)) - sonoridad_tramo(f, desde, largo)
    except Exception:  # noqa: BLE001 — si no se puede medir, el nivel de antes
        return 0.3
    return 10 ** (max(-30.0, min(15.0, db)) / 20) * float(mu.get("volumen", 1.0))


def sonoridad_tramo(f, desde, largo):
    """LUFS de un pedazo de un audio (en caché, por archivo y pedazo redondeado)."""
    f = Path(f)
    desde, largo = round(float(desde), 1), round(max(3.0, float(largo)), 0)
    cache = DATOS / "ondas" / "sonoridad.json"
    try:
        tabla = json.loads(cache.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        tabla = {}
    firma = f"{f}|{f.stat().st_size}|{int(f.stat().st_mtime)}|{desde}|{largo}"
    if firma in tabla:
        return tabla[firma]
    r = run([FFMPEG, "-hide_banner", "-ss", f"{desde}", "-t", f"{largo}", "-i", str(f), "-vn", "-af", "ebur128", "-f", "null", "-"])
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    valor = float(i[-1]) if i and float(i[-1]) > -60 else sonoridad(f)
    tabla[firma] = valor
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(tabla), encoding="utf-8")
    return valor


def parte_grave(f):
    """Qué parte de la energía de un sonido está debajo de 200 Hz (0 a 1), en caché. Un whoosh claro anda por 0.1–0.4;
    los de «cámara lenta» o las animaciones de logo pasan de 0.8 y suenan a bajo."""
    import numpy as np
    f = Path(f)
    cache = DATOS / "ondas" / "graves.json"
    try:
        firma = f"{f}|{f.stat().st_size}|{int(f.stat().st_mtime)}"
    except OSError:
        return 0.0
    if firma in _GRAVES_MEM:
        return _GRAVES_MEM[firma]
    try:
        tabla = json.loads(cache.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        tabla = {}
    if firma in tabla:
        _GRAVES_MEM[firma] = tabla[firma]
        return tabla[firma]
    pcm = subprocess.run(creationflags=_prioridad_baja(), args=[FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(f), "-vn", "-ac", "1", "-ar", "22050",
                          "-t", "4", "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    valor = 0.0
    if len(x) > 256:
        pot = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        fr = np.fft.rfftfreq(len(x), 1 / 22050)
        valor = round(float(pot[fr < 200].sum() / max(pot.sum(), 1e-9)), 3)
    tabla[firma] = valor
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(tabla), encoding="utf-8")
    _GRAVES_MEM[firma] = valor
    return valor


# en memoria: al armar, cada video volvía a leer del disco las mismas ondas y la tabla de graves cientos de veces
# (975 lecturas por video en P05, ~5 s). Se rehacen solas si el archivo cambia (la firma lleva tamaño y fecha).
_ONDAS_MEM = {}
_GRAVES_MEM = {}


def onda_archivo(f, por_seg=50):
    """Picos de volumen de un archivo de audio cualquiera (un sonido de la biblioteca), en caché."""
    import numpy as np
    f = Path(f)
    if not f.is_file():
        return {"por_seg": por_seg, "picos": [], "dur": 0}
    firma = f"{f.stat().st_size}-{int(f.stat().st_mtime)}-{por_seg}"
    if (d := _ONDAS_MEM.get((str(f), firma))) is not None:  # ya leída en esta sesión de la sala
        return d
    cache = DATOS / "ondas" / (hashlib.md5(str(f).encode("utf-8")).hexdigest() + ".json")
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("firma") == firma:
                _ONDAS_MEM[(str(f), firma)] = d
                return d
        except ValueError:
            pass
    sr = 8000
    pcm = subprocess.run(creationflags=_prioridad_baja(), args=[FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(f), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], capture_output=True).stdout
    x = np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.float32)) / 32768
    paso = sr // por_seg
    n = len(x) // paso
    picos = x[:n * paso].reshape(n, paso).max(axis=1) if n else np.zeros(0)
    tope = float(picos.max()) if n else 1.0
    picos = np.clip(picos / max(tope, 1e-4), 0, 1)
    d = {"firma": firma, "por_seg": por_seg, "dur": round(len(x) / sr, 3), "picos": [round(float(v), 3) for v in picos]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(d), encoding="utf-8")
    _ONDAS_MEM[(str(f), firma)] = d
    return d


def espectro_archivo(f, bandas=72):
    """Cuánto suena un sonido en cada frecuencia (20 Hz–20 kHz en escala logarítmica, dB relativos al máximo), para
    dibujarlo detrás de la curva del ecualizador. En caché."""
    import numpy as np
    f = Path(f)
    if not f.is_file():
        return {"f": [], "db": []}
    firma = f"{f.stat().st_size}-{int(f.stat().st_mtime)}-esp{bandas}"
    cache = DATOS / "ondas" / (hashlib.md5(str(f).encode("utf-8")).hexdigest() + ".esp.json")
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("firma") == firma:
            return d
    except (FileNotFoundError, ValueError):
        pass
    sr, n = 44100, 4096
    pcm = subprocess.run(creationflags=_prioridad_baja(), args=[FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(f), "-vn", "-ac", "1", "-ar", str(sr),
                          "-t", "30", "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    marcos = np.lib.stride_tricks.sliding_window_view(x, n)[::n // 2]
    potencia = (np.abs(np.fft.rfft(marcos * np.hanning(n), axis=1)) ** 2).mean(axis=0)
    fr = np.fft.rfftfreq(n, 1 / sr)
    bordes = np.geomspace(20, 20000, bandas + 1)
    db = []
    for a, b in zip(bordes[:-1], bordes[1:]):
        m = (fr >= a) & (fr < b)
        v = potencia[m].mean() if m.any() else potencia[np.argmin(np.abs(fr - (a + b) / 2))]
        db.append(10 * np.log10(v + 1e-12))
    db = np.array(db) - max(db)
    d = {"firma": firma, "f": [round(float(np.sqrt(a * b)), 1) for a, b in zip(bordes[:-1], bordes[1:])],
         "db": [round(float(max(v, -60)), 1) for v in db]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(d), encoding="utf-8")
    return d


def inicio_audible(f):
    """Segundo donde empieza a sonar de verdad un archivo (salta el silencio y el soplido del principio).
    Busca el golpe (primer punto al 20 % del pico) y retrocede mientras siga sonando sobre el umbral
    (config sonidos.umbral_inicio, relativo al pico): así respeta la subida de un whoosh pero no un ruido de fondo
    suelto antes del golpe. Deja 20 ms antes para no comer el ataque."""
    try:
        d = onda_archivo(f, 100)
    except Exception:  # noqa: BLE001 — si no se puede leer, arranca desde 0 como antes
        return 0.0
    picos = d["picos"]
    umbral = float(CONFIG["sonidos"].get("umbral_inicio", 0.05))
    i = next((k for k, v in enumerate(picos) if v >= 0.2), 0)
    while i > 0 and picos[i - 1] >= umbral:
        i -= 1
    return max(0.0, i / d["por_seg"] - 0.02)


def onda(nombre, por_seg=50):
    """Picos de volumen de una pieza procesada: {por_seg, picos: [0–1]} (en caché mientras no cambie el video)."""
    import numpy as np
    f = S.proc / f"{nombre}.mp4"
    if not f.exists():
        return {"por_seg": por_seg, "picos": []}
    cache = S.proc / f"{nombre}.onda.json"
    firma = f"{f.stat().st_size}-{int(f.stat().st_mtime)}-{por_seg}"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("firma") == firma:
            return d
    sr = 8000
    pcm = subprocess.run(creationflags=_prioridad_baja(), args=[FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(f), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], capture_output=True).stdout
    x = np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.float32)) / 32768
    paso = sr // por_seg
    n = len(x) // paso
    picos = x[:n * paso].reshape(n, paso).max(axis=1) if n else np.zeros(0)
    tope = float(np.percentile(picos, 99)) if n else 1.0
    picos = np.clip(picos / max(tope, 1e-4), 0, 1)
    d = {"firma": firma, "por_seg": por_seg, "picos": [round(float(v), 3) for v in picos]}
    cache.write_text(json.dumps(d), encoding="utf-8")
    return d


# ---------- Las dos versiones del guion: para el host y para producción ----------

NOMBRES_SFX = {"impacto": "impacto", "whoosh": "whoosh", "whoosh_inverso": "whoosh inverso", "riser": "riser (sube la tensión)",
               "pop": "pop", "ding": "ding", "brillo": "brillo", "glitch": "glitch", "scratch": "scratch de disco",
               "notificacion": "notificación", "tecleo": "tecleo", "tension": "tensión", "camara": "obturador de cámara",
               "dinero": "caja registradora", "datos": "datos (bip de computadora)", "error": "error (buzzer)", "papel": "papel",
               "liquido": "líquido", "reloj": "reloj", "aplausos": "aplausos", "electrico": "eléctrico", "boing": "boing",
               "golpe_texto": "golpe de texto", "riser_largo": "riser largo", "drama": "drama", "corte_silencio": "corte de silencio",
               "atencion": "«fahhh» (llamar la atención)", "revelar": "magic reveal (revelar)", "click": "click (remarcar texto)",
               "zoom": "whoosh de zoom"}


def plan_sonoro(nombre, info):
    """Lo que va a sonar en una pieza según su guion: marcas del guion y reglas automáticas (sin tiempos:
    se anclan a la palabra). [{momento, efecto, por}]"""
    so = CONFIG["sonidos"]
    out = []
    tipo = tipo_de(nombre) if info.get("tipo") != "cuerpo" else "cuerpo"
    if tipo == "hook":
        t = tipo_hook(nombre, info.get("texto", ""))
        for cat in so["arranque_por_tipo"].get(t) or so["arranque_por_tipo"]["default"]:
            out.append({"momento": "inicio", "efecto": cat, "por": f"arranque de hook ({info.get('tipo') or 'general'})"})
        if t in so.get("riser_en_hooks", ["pregunta"]):
            out.append({"momento": "final", "efecto": "riser", "por": "la pregunta sube hasta el cuerpo"})
        ultimas = [norm(w) for w in info.get("texto", "").split()][-4:]
        if "esto" in ultimas and so.get("revelar_en_hook", True):
            out.append({"momento": "«esto»", "efecto": "revelar", "por": "revela el producto y pasa al cuerpo"})
        else:
            out.append({"momento": "final", "efecto": "whoosh", "por": "corte al cuerpo"})
    if tipo == "cta":
        out.append({"momento": "inicio", "efecto": so.get("transicion_cta", "camara"), "por": "transición al CTA (con destello)"})
    if tipo == "cta":
        texto_cta = {norm(w) for w in info.get("texto", "").split()}
        cat = so["cta_por_tipo"].get(norm(info.get("tipo", ""))) or so["cta_por_tipo"].get(
            "organico" if texto_cta & {"comenta", "comentanos"} and not texto_cta & {"whatsapp", "escribenos"} else "anuncio")
        if cat:
            out.append({"momento": "inicio", "efecto": cat, "por": f"CTA {info.get('tipo', 'anuncio')}"})
    for m in info.get("marcas", []):
        if m.get("sfx"):
            out.append({"momento": f"«{m['palabra']}»", "efecto": m["sfx"], "por": "marca del guion"})
        elif m.get("asset"):
            out.append({"momento": f"«{m['palabra']}»", "efecto": "whoosh", "por": f"entra {m['asset']}"})
        elif m.get("sub") in ("centro_grande", "palabra_sola"):
            out.append({"momento": f"«{m['palabra']}»", "efecto": "impacto", "por": "subtítulo grande"})
    palabras = [norm(w) for w in info.get("texto", "").split()]
    situaciones = dict(CONFIG.get("sfx_palabras", {}))
    for cat, frases in situaciones.items():
        for f in frases:
            fn = [norm(w) for w in f.split()]
            if any(palabras[k:k + len(fn)] == fn for k in range(len(palabras) - len(fn) + 1)):
                out.append({"momento": f"«{f}»", "efecto": cat, "por": "situación (se dice la palabra)"})
    import perfil as _perfil
    ws = info.get("texto", "").split()
    dicha = next((w for i, w in enumerate(ws) if _perfil.es_marca(w, ws[i + 1:])), None) if tipo == "cuerpo" else None
    if dicha:
        out.append({"momento": f"«{dicha.strip('.,;:?!¿¡«»')}»", "efecto": "brillo", "por": "aparece la marca"})
    return out


def _orden_piezas(prod):
    orden = {"cuerpo": 0, "hook": 1, "cta": 2}
    def clave(n):
        m = re.match(r"(?:C|P)(\d+)", n)
        return (int(m.group(1)) if m else 99, orden[tipo_de(n) if prod["piezas"][n].get("tipo") != "cuerpo" else "cuerpo"], n)
    return sorted(prod.get("piezas", {}), key=clave)


def hojas_guion():
    """El guion para grabar en hojas: cada cuerpo con todos sus hooks (como se combinan en los videos) y sus CTA.
    Así se imprime: una hoja por cuerpo. Los CTA que van con todos los cuerpos salen en una hoja al final."""
    prod = produccion()
    piezas_g = {g["nombre"]: g for g in guion_para_grabar_actual()}
    cuerpos = [n for n, g in piezas_g.items() if g["tipo"] == "cuerpo"]
    hojas = []
    for c in cuerpos:
        g = piezas_g[c]
        hooks = sorted((h for n, h in piezas_g.items() if h["tipo"] == "hook" and n.split("_")[0] == c), key=lambda h: h["nombre"])
        ctas = [x for n, x in piezas_g.items() if x["tipo"] == "cta" and "_" in n and n.split("_")[0] == c]
        hojas.append({"cuerpo": c, "partes": g["partes"] if len(g["partes"]) > 1 else [g["texto"]],
                      "hooks": [{"nombre": h["nombre"], "texto": h["texto"], "tipo": h.get("tipo_hook") or ""} for h in hooks],
                      "ctas": [{"nombre": x["nombre"], "texto": x["texto"]} for x in ctas]})
    generales = [{"nombre": x["nombre"], "texto": x["texto"]} for n, x in piezas_g.items()
                 if x["tipo"] == "cta" and not ("_" in n and n.split("_")[0] in cuerpos)]
    return {"titulo": prod.get("semana") or S.semana, "hojas": hojas, "ctas": generales}


PALABRAS_POR_SEG = 2.6  # al hablar rápido, como en los videos


def apoyo_visual():
    """Cuántas imágenes y clips de Flow tiene hoy cada cuerpo y cada hook, y cuántos recomienda la sala:
    una imagen cada ~4 s de voz mantiene la atención; en el hook, una imagen en los primeros segundos engancha."""
    prod = produccion()
    ps = prod.get("piezas") or {}
    assets = {a["id"]: a for a in prod.get("assets", [])}
    filas = []
    for n in _orden_piezas(prod):
        info = ps[n]
        tipo = "cuerpo" if info.get("tipo") == "cuerpo" else tipo_de(n)
        if tipo not in ("cuerpo", "hook"):
            continue
        ids = [x["asset"] for x in info.get("marcas", []) if x.get("asset")]
        clips = sum(1 for i in ids if (assets.get(i) or {}).get("tipo") == "clip")
        filas.append({"pieza": n, "tipo": tipo, "seg": round(len(str(info.get("texto", "")).split()) / PALABRAS_POR_SEG, 1),
                      "imagenes": len(ids) - clips, "clips": clips})
    segs = sorted(f["seg"] for f in filas if f["tipo"] == "cuerpo") or [25.0]
    seg_c = segs[len(segs) // 2]
    segs_h = sorted(f["seg"] for f in filas if f["tipo"] == "hook") or [4.0]
    seg_h = segs_h[len(segs_h) // 2]
    rec = {"cuerpo": max(2, min(10, round(seg_c / 4))), "clips": 1 if seg_c >= 15 else 0, "hook": 2 if seg_h > 6 else 1}
    razon = (f"Los cuerpos duran unos {seg_c:.0f} s: una imagen cada 4 s mantiene la atención sin tapar a la persona. "
             f"Los hooks duran unos {seg_h:.0f} s: {'dos imágenes rápidas en los primeros segundos enganchan' if rec['hook'] == 2 else 'una imagen en los primeros segundos engancha'}.")
    sin_animo = sum(1 for n, i in ps.items() if (i.get("tipo") == "cuerpo" or tipo_de(n) in ("hook", "cta"))
                    and i.get("animo") not in ANIMOS)
    return {"piezas": filas, "recomendado": rec, "razon": razon, "sin_animo": sin_animo}


SALTO_DE_HOJA = "<<<SALTO>>>"  # en el Word, aquí empieza una hoja nueva


def documento_guion(version="host", vista="combinacion"):
    """Markdown del guion. «host»: solo lo que se dice, en orden de grabación, para leer y grabarse.
    «produccion»: pieza por pieza, todo lo que va en el video (estilos, imágenes con su prompt, música, sonidos, portada, copy)."""
    prod = produccion()
    if not prod.get("piezas"):
        return ""
    titulo = prod.get("semana") or S.semana
    if version == "host":
        # video por video, como se va a ver: el hook y después su cuerpo (y su CTA)
        piezas_g = {g["nombre"]: g for g in guion_para_grabar_actual()}
        cuerpos = [n for n, g in piezas_g.items() if g["tipo"] == "cuerpo"]
        if vista in ("hooks", "cuerpos"):
            solo = "hook" if vista == "hooks" else "cuerpo"
            out = [f"# {titulo} — guion para el host · solo {'hooks' if solo == 'hook' else 'cuerpos y CTAs'}", "", "Lee tal cual.", ""]
            for cuerpo in cuerpos:
                if solo == "hook":
                    out += [f"## Hooks de {cuerpo}", ""]
                    for h in sorted((g for n, g in piezas_g.items() if g["tipo"] == "hook" and n.split("_")[0] == cuerpo), key=lambda g: g["nombre"]):
                        out += [f"**{h['nombre']}**" + (f" · {h['tipo_hook']}" if h.get("tipo_hook") else "") + f": {h['texto']}", "", "- [ ] grabado", ""]
                else:
                    c = piezas_g[cuerpo]
                    out += [f"## Cuerpo {cuerpo}", ""]
                    out += [f"{k}. {f}" for k, f in enumerate(c["partes"], 1)] if len(c["partes"]) > 1 else [c["texto"]]
                    out += ["", "- [ ] grabado", ""]
            if solo == "cuerpo":
                ctas = [g for g in piezas_g.values() if g["tipo"] == "cta"]
                if ctas:
                    out += ["## CTAs", ""]
                    out += [f"**{g['nombre']}:** {g['texto']}\n\n- [ ] grabado\n" for g in ctas]
            return "\n".join(out)
        h = hojas_guion()
        out = [f"# {titulo} · guion para grabar", "", "Una hoja por cuerpo: graba sus hooks seguidos y después el cuerpo. Lee tal cual.", ""]
        for k, hoja in enumerate(h["hojas"]):
            if k:
                out.append(SALTO_DE_HOJA)
            out += [f"## {hoja['cuerpo']} · cuerpo con {len(hoja['hooks'])} hook{'s' if len(hoja['hooks']) != 1 else ''}", "", "### Hooks", ""]
            out += [f"☐ **{x['nombre']}**" + (f" · {x['tipo']}" if x["tipo"] else "") + f": {x['texto']}" for x in hoja["hooks"]] or ["(sin hooks)"]
            out += ["", "### Cuerpo", ""]
            out += [f"{n}. {p}" for n, p in enumerate(hoja["partes"], 1)] if len(hoja["partes"]) > 1 else [hoja["partes"][0]]
            out += ["", "☐ cuerpo grabado", ""]
            if hoja["ctas"]:
                out += ["### CTA", ""] + [f"☐ **{x['nombre']}:** {x['texto']}" for x in hoja["ctas"]] + [""]
        if h["ctas"]:
            out += [SALTO_DE_HOJA, "## CTA (van al final de cada video)", ""] + [f"☐ **{x['nombre']}:** {x['texto']}" for x in h["ctas"]]
        return "\n".join(out)
    assets = {a["id"]: a for a in prod.get("assets", [])}
    musica = {m["id"]: m for m in prod.get("musica", [])}
    out = [f"# {titulo} — guion de producción", "",
           f"Público: {prod.get('publico', '—')} · Plantilla de subtítulos: {prod.get('plantilla_sub') or 'la predeterminada'}", ""]
    for n in _orden_piezas(prod):
        info = prod["piezas"][n]
        tipo = "cuerpo" if info.get("tipo") == "cuerpo" else tipo_de(n)
        out += [f"## {n} · {tipo.upper()}" + (f" · {info['tipo']}" if tipo == "hook" and info.get("tipo") not in (None, "", "hook") else ""), "",
                f"**Voz:** {info.get('texto', '')}", ""]
        if info.get("portada"):
            out.append(f"**Portada:** {info['portada'].replace('*', '')}")
        mid = info.get("musica") or next((x["id"] for x in prod.get("musica") or [] if info.get("animo") and x.get("animo") == info["animo"]), None)
        if info.get("animo") in ANIMOS:
            out.append(f"**Ánimo:** {ANIMOS[info['animo']][0]}")
        if mid:
            m = musica.get(mid, {})
            out.append(f"**Música:** {mid}" + (f" — {m.get('uso', '')}" if m else ""))
        estilos = [m for m in info.get("marcas", []) if m.get("sub")]
        if estilos:
            out.append("**Subtítulos:** " + " · ".join(f"«{m['palabra']}» → {m['sub'].replace('_', ' ')}" for m in estilos))
        imgs = [m for m in info.get("marcas", []) if m.get("asset")]
        if imgs:
            out.append("**Imágenes y clips (Google Flow):**")
            for m in imgs:
                a = assets.get(m["asset"], {})
                out.append(f"- `{m['asset']}` en «{m['palabra']}» · {m.get('dur', 1.5)} s · {m.get('modo', 'completa')}")
                if a.get("flow", {}).get("prompt"):
                    out.append(f"  - Prompt: {a['flow']['prompt']}")
        sonidos = plan_sonoro(n, info)
        if sonidos:
            out.append("**Sonidos:**")
            out += [f"- {s_['momento']}: {NOMBRES_SFX.get(s_['efecto'], s_['efecto'])} — {s_['por']}" for s_ in sonidos]
        out.append("")
    if musica:
        out += ["## Música (Google Flow Music)", ""]
        for m in musica.values():
            pr = m.get("prompt") or {}
            out += [f"### {m['id']} — {m.get('uso', '')}", f"- Prompt: {pr.get('prompt', '')}", f"- Evitar: {pr.get('evitar', '')}",
                    f"- Duración: {pr.get('duracion', '')}", ""]
    return "\n".join(out)


def guardar_documentos_guion():
    """Escribe guion_host.md y guion_produccion.md en la carpeta Guion de la sesión."""
    hechos = []
    for v in ("host", "produccion"):
        texto = documento_guion(v)
        f = S.guion / f"guion_{v}.md"
        if texto and not (f.exists() and f.read_text(encoding="utf-8") == texto):  # igual que antes: no se toca
            S.guion.mkdir(parents=True, exist_ok=True)
            f.write_text(texto, encoding="utf-8")
            hechos.append(f.name)
    return hechos


# ---------- Sin guion: el guion sale de lo grabado · Con guion: se sube desde la sala ----------

def _guardar_produccion(prod):
    """Escribe Guion/produccion.json (el anterior queda como produccion.anterior.json) y sus documentos."""
    global _prod_cache
    S.guion.mkdir(parents=True, exist_ok=True)
    f = S.guion / "produccion.json"
    if f.exists():
        shutil.copy2(f, S.guion / "produccion.anterior.json")
        copias = S.guion / "_copias"
        copias.mkdir(exist_ok=True)
        shutil.copy2(f, copias / f"produccion_{time.strftime('%Y%m%d-%H%M%S')}.json")
        for vieja in sorted(copias.glob("produccion_*.json"))[:-30]:  # quedan las 30 últimas
            vieja.unlink(missing_ok=True)
    f.write_text(json.dumps(prod, ensure_ascii=False, indent=1), encoding="utf-8")
    _prod_cache = None
    guardar_documentos_guion()


def _guion_propio():
    """El produccion.json de la sesión tal cual (sin el plan del director), o {} si no hay."""
    for f in (S.guion / "produccion.json", S.grab / "produccion.json"):
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                return {}
    return {}


def faltan_prompts_flow():
    """Hay piezas en el guion y ningún asset de Flow: la sala no tiene imágenes que generar (p. ej. guion armado con lo grabado)."""
    p = _guion_propio()
    return bool(p.get("piezas")) and not p.get("assets")


def guion_desde_grabado():
    """Si la sesión no tiene guion, lo escribe con lo que se dijo en cada pieza ya procesada (las transcripciones):
    así se ven el guion, la revisión y el teleprompter aunque nadie lo haya escrito. Si el guion ya salió de lo
    grabado, agrega las piezas nuevas y pone al día las que se volvieron a transcribir (lo corregido a mano no se
    pisa). Un guion escrito o subido no se toca. Devuelve cuántas piezas agregó o puso al día."""
    base = _guion_propio()
    if base.get("piezas") and base.get("origen") != "grabado":
        return 0
    piezas_g = dict(base.get("piezas") or {})
    nuevas = 0
    for p in piezas():
        if p.get("angulo"):
            continue  # la toma de otro ángulo dice lo mismo que la de frente: no es otra pieza del guion
        lineas = leer_subs(p["nombre"])["lineas"]
        palabras = [w for ln in lineas for w in ln["palabras"]]
        if not palabras:
            continue
        info = {"tipo": p["tipo"], "texto": " ".join(w["w"] for w in palabras)}
        info["dicho"] = info["texto"]  # lo que salió de lo grabado: si el texto sigue igual, nadie lo corrigió
        viejo = piezas_g.get(p["nombre"])
        # una pieza que ya estaba se rehace solo si se volvió a transcribir (otro clip, otro modelo) y su texto
        # no se corrigió a mano
        if viejo and (viejo.get("editado_en_sala") or viejo.get("texto") != viejo.get("dicho", viejo.get("texto"))
                      or viejo.get("texto") == info["texto"]):
            continue
        if len(p["partes"]) > 1:  # en partes: cada toma es una frase (se separa por su duración)
            try:
                dur = json.loads((S.proc / f"{p['nombre']}.partes.json").read_text(encoding="utf-8")).get("duraciones") or []
            except (FileNotFoundError, ValueError, AttributeError):
                dur = []
            if len(dur) == len(p["partes"]):
                cortes, t = [], 0.0
                for x in dur:
                    t += x
                    cortes.append(t)
                frases = [[] for _ in dur]
                for w in palabras:
                    frases[next((i for i, c in enumerate(cortes) if w["s"] < c), len(dur) - 1)].append(w["w"])
                info["partes"] = [" ".join(x) for x in frases if x]
        piezas_g[p["nombre"]] = {**(viejo or {}), **info}
        nuevas += 1
    if nuevas:
        _guardar_produccion({**base, "origen": "grabado",
                             "nota": "Guion armado con lo que se dijo en cada clip (la sesión no tenía guion). "
                                     "Si subes el guion de verdad, reemplaza a este.",
                             "piezas": dict(sorted(piezas_g.items(), key=lambda x: (tipo_de(x[0]) != "cuerpo", x[0])))})
    return nuevas


def _texto_docx(datos):
    """El texto de un Word (.docx), un párrafo por línea, sin librerías extra."""
    import html
    import io
    import zipfile
    xml = zipfile.ZipFile(io.BytesIO(datos)).read("word/document.xml").decode("utf-8", "replace")
    # en una tabla cada celda es un párrafo: se juntan las celdas de cada fila (si no, «H1» y su texto quedaban sueltos)
    xml = re.sub(r"<w:tr[ >].*?</w:tr>", lambda m: re.sub(r"</w:p>", " ", m.group(0)) + "</w:p>", xml, flags=re.S)
    out = []
    for p in re.split(r"</w:p>", xml):
        p = re.sub(r"<w:tab\s*/>", " ", p)
        p = re.sub(r"<w:br\s*/>", "\n", p)
        out.append(html.unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", p))))
    return "\n".join(out)


_NOTA = re.compile(r"^(?:\(|\[|nota|m[uú]sica|imagen|flow|prompt|b-?roll|sfx|sonido|orden|portada|copy|duraci[oó]n|tema)\b", re.I)


def leer_guion_texto(texto):
    """Saca las piezas de un guion escrito (Word, .md o .txt). Entiende el formato de los guiones de Claude
    («## C1 — Tema», «### Cuerpo» con frases numeradas, «**H1.** …», «## CTA») y líneas sueltas con código
    («C1-H2: texto», «C1.3 texto», «CTA 1: texto»). Devuelve {pieza: {tipo, texto, partes?}}."""
    frases, hooks, comunes = {}, {}, {}  # comunes: hooks fuera de un cuerpo («## Hooks» arriba): sirven para todos
    cuerpo, seccion, cta_actual = None, None, None
    sep = r"\s*[.:)\-—–]+\s*"
    for crudo in texto.splitlines():
        s = crudo.strip()
        if not s or re.fullmatch(r"[-*_=—–\s]{3,}", s):
            continue
        titulo = s.startswith("#")
        nivel = len(s) - len(s.lstrip("#"))  # «## CTA» (de todo el guion) no es lo mismo que «### CTA» (dentro de un cuerpo)
        s = re.sub(r"^#+\s*", "", s)
        # primero la negrita y después la viñeta: «**H3.** …» quedaba «*H3.» y no se leía como el hook 3
        s = s.replace("**", "").replace("__", "").replace("`", "").strip()
        s = re.sub(r"^(?:[-*•]\s*)?(?:\[[ xX]\]\s*)?", "", s).strip()
        if not s:
            continue
        corto = titulo or len(s) <= 70

        def titulo_de_seccion():
            """«C1 — Calidad de vida», «CUERPO 2», «Hooks», «CTAs: elige 2»: cambia de cuerpo o de sección."""
            nonlocal cuerpo, seccion, cta_actual
            if not corto:
                return False
            if (m := re.fullmatch(r"(?:C|CUERPO|VIDEO)\s*(\d+)(?:\s*[—:\-–]\s*.*|\s*\(.*\))?", s, re.I)) \
                    or (titulo and (m := re.fullmatch(r"(\d{1,2})\s*[—:\-–.]\s*\D.*", s))):  # «## 1 — SUPLEVET Y PARVOVIRUS»
                cuerpo, seccion, cta_actual = f"C{int(m[1])}", "cuerpo", None
            elif re.fullmatch(r"(?:HOOKS?|GANCHOS?)\s*(?:[:(—–·-].*)?", s, re.I):
                seccion = "hooks"  # antes del primer cuerpo (sin cuerpo arriba): hooks para todos los cuerpos
            elif re.fullmatch(r"CUERPO\s*(?:[:(].*)?", s, re.I):
                seccion = "cuerpo"
            elif m := re.fullmatch(r"(?:CTAS?|CIERRES?|LLAMADOS? A LA ACCI[OÓ]N)(?:[\s_-]*(\d+|[A-Z]))?\s*(?:[—:\-–(].*)?", s, re.I):
                seccion, cta_actual = "cta", (f"CTA{int(m[1])}" if m[1] and m[1].isdigit() else f"CTA-{m[1].upper()}" if m[1] else None)
                if titulo and nivel <= 2:  # «## CTA»: el cierre de todo el guion, no el de un cuerpo
                    cuerpo = None
            else:
                return False
            return True

        def con_codigo():
            """Líneas con su código delante: «H1. …», «C1-H2: …», «C1.3 …», «CTA1. …»."""
            if re.match(r"^CTAS\b", s, re.I):  # «CTAs: elige 2» es el título de la sección, no un CTA «S»
                return False
            if m := re.match(rf"^(?:([CP])\s*(\d+)[\s_-]*)?H(?:OOK)?\s*(\d+)(?:{sep}|\s+)(.+)$", s, re.I):
                c = f"{m[1].upper()}{int(m[2])}" if m[1] else cuerpo
                if c:
                    hooks[f"{c}_H{int(m[3])}"] = m[4].strip()
                elif seccion == "hooks":  # «H1. …» sin cuerpo arriba: para todos los cuerpos
                    comunes[int(m[3])] = m[4].strip()
                return True
            if m := re.match(rf"^([CP])(\d+)[.\s_-]+P?(\d+)(?:{sep}|\s+)(.+)$", s, re.I):
                frases.setdefault(f"{m[1].upper()}{int(m[2])}", {})[int(m[3])] = m[4].strip()
                return True
            if m := re.match(rf"^CTA(?:[\s_-]*([A-Z0-9]+?))?(?:[.\s_-]+(\d+))?{sep}(.+)$", s, re.I):
                suf = m[1].upper() if m[1] else ""
                # un «CTA.» sin número dentro de un cuerpo es el cierre de ese cuerpo (C1_CTA); antes todos los
                # cuerpos se juntaban en un solo CTA con sus cierres uno detrás de otro
                nombre = f"{cuerpo}_CTA" if not suf and cuerpo else "CTA" + (suf if suf.isdigit() else f"-{suf}" if suf else "")
                d = frases.setdefault(nombre, {})
                d[int(m[2]) if m[2] else max(d, default=0) + 1] = m[3].strip()
                return True
            return False

        # «C3.1 HASTA C3.4», «C1.1 al C1.3»: es una indicación de grabación, no la frase 1
        if re.match(r"^[CP]\d+[.\s_-]+\d+\s+(?:HASTA|AL?|Y|-|–|—)\s+[CP]?\d+(?:[.\s_-]+\d+)?\s*$", s, re.I):
            continue
        # un título (#) es título aunque traiga texto; una línea normal primero se lee como pieza con código
        if (titulo_de_seccion() or con_codigo()) if titulo else (con_codigo() or titulo_de_seccion()):
            # «C1: Mi perro se rasca todo el día»: si después del código viene una oración (no un tema corto),
            # además de abrir el cuerpo es su frase
            # (un título «## C5 — 4P · imagen, promesa…» es el tema del cuerpo, nunca una frase)
            if not titulo and seccion == "cuerpo" and cuerpo and (m := re.fullmatch(r"(?:C|CUERPO|VIDEO)\s*\d+\s*[—:\-–]\s*(.+)", s, re.I)):
                resto = m[1].strip()
                if len(resto.split()) >= 6 or re.search(r"[.!?…]$|[¿¡]", resto):
                    d = frases.setdefault(cuerpo, {})
                    d[max(d, default=0) + 1] = resto
            continue
        num = re.match(r"^(\d+)[.)]\s+(.+)$", s)
        resto = num[2].strip() if num else s
        if _NOTA.match(resto):
            continue
        if seccion == "cuerpo" and cuerpo:
            d = frases.setdefault(cuerpo, {})
            d[int(num[1]) if num and int(num[1]) not in d else max(d, default=0) + 1] = resto
        elif seccion == "hooks" and not cuerpo and num:
            comunes[int(num[1])] = resto
        elif seccion == "hooks" and cuerpo and num:
            hooks[f"{cuerpo}_H{int(num[1])}"] = resto
        elif seccion == "hooks" and cuerpo and len(resto.split()) >= 3:
            # lista numerada automática de Word: el «1.» no viene en el texto; se numeran en orden
            n = 1 + sum(1 for k in hooks if k.startswith(f"{cuerpo}_H"))
            hooks[f"{cuerpo}_H{n}"] = resto
        elif seccion == "cta" and (cta_actual or num):  # sin número ni «CTA 1» arriba suele ser una indicación
            d = frases.setdefault(cta_actual or f"CTA{int(num[1])}", {})
            d[max(d, default=0) + 1] = resto
    out = {}
    for nombre, d in frases.items():
        partes = [d[k] for k in sorted(d)]
        out[nombre] = {"tipo": "cta" if "CTA" in nombre else "cuerpo", "texto": " ".join(partes),
                       **({"partes": partes} if len(partes) > 1 else {})}
    for nombre, t in hooks.items():
        out[nombre] = {"tipo": "hook", "texto": t}
    # los hooks para todos quedan como hooks del primer cuerpo con la marca «todos» (como en la plantilla de Excel)
    cuerpos = sorted((k for k in frases if "CTA" not in k), key=lambda x: int(re.sub(r"\D", "", x) or 0))
    primero = cuerpos[0] if cuerpos else "C1"
    for n, t in comunes.items():
        out.setdefault(f"{primero}_H{n}", {"tipo": "hook", "texto": t, "todos": True})
    return dict(sorted(out.items(), key=lambda x: ({"cuerpo": 0, "hook": 1, "cta": 2}[tipo_de(x[0])], x[0])))


_PIEZA_SALA = r"(?:[CP]\d+(?:_[A-Z][\w-]*)?|CTA[\w-]*)"


def leer_formato_sala(texto):
    """El guion que exporta la propia sala (botón Word: «producción» o «para grabar», o los guion_*.md).
    Tiene su propio formato («C1 · CUERPO» + «Voz: …», «☐ C1_H1 · pregunta: …»): el lector general no lo entiende.
    Devuelve None si el texto no es de la sala, o {pieza: {tipo, texto, partes?}} (vacío si no trae piezas)."""
    lineas = []
    for crudo in texto.splitlines():
        s = re.sub(r"^#+\s*", "", crudo.strip()).replace("**", "").replace("`", "").strip()
        s = re.sub(r"^(?:[-*•]\s*)?(?:\[[ xX]\]\s*)?(?:☐\s*)?", "", s).strip()
        if s and s != SALTO_DE_HOJA:
            lineas.append(s)
    if not lineas or not re.search(r"guion (?:de producci[oó]n|para grabar|para el host)", lineas[0], re.I):
        return None
    produccion_ = bool(re.search(r"guion de producci[oó]n", lineas[0], re.I))
    frases, estilo = {}, {}
    actual = cuerpo = seccion = None
    for s in lineas[1:]:
        if produccion_:
            if s.startswith("Música (Google Flow Music)"):
                break
            if m := re.fullmatch(rf"({_PIEZA_SALA}) · (CUERPO|HOOK|CTA)(?: · (.+))?", s):
                actual = m[1]
                if m[3]:
                    estilo[actual] = m[3].strip()
            elif actual and (m := re.match(r"Voz:\s*(.+)", s)):
                frases[actual] = [m[1].strip()]
                actual = None
            continue
        if m := re.fullmatch(r"([CP]\d+) · cuerpo con .*", s):
            cuerpo, seccion = m[1], None
        elif m := re.fullmatch(r"(Cuerpo|Hooks de) ([CP]\d+)", s):
            cuerpo, seccion = m[2], "cuerpo" if m[1] == "Cuerpo" else "hooks"
        elif s in ("Hooks", "Cuerpo"):
            seccion = s.lower()
        elif re.fullmatch(r"CTAs?(?: \(.*\))?", s):
            seccion = "cta"
        elif m := re.fullmatch(rf"({_PIEZA_SALA})(?: · ([^:]+))?:\s*(.+)", s):
            frases[m[1]] = [m[3].strip()]
            if m[2]:
                estilo[m[1]] = m[2].strip()
        elif re.fullmatch(r"(?:cuerpo )?grabado|\(sin hooks\)|Lee tal cual\.|Una hoja por cuerpo.*", s, re.I):
            continue
        elif seccion == "cuerpo" and cuerpo:
            num = re.match(r"^\d+\.\s+(.+)$", s)
            frases.setdefault(cuerpo, []).append(num[1].strip() if num else s)
    out = {}
    for nombre, partes in frases.items():
        tipo = tipo_de(nombre)
        out[nombre] = {"tipo": "cuerpo" if tipo == "cuerpo" else estilo.get(nombre) or tipo, "texto": " ".join(partes),
                       **({"partes": partes} if len(partes) > 1 else {})}
    return dict(sorted(out.items(), key=lambda x: ({"cuerpo": 0, "hook": 1, "cta": 2}[tipo_de(x[0])], x[0])))


def _texto_plano(datos):
    """Un .txt o .md: en UTF-8, o en el formato viejo de Windows (el Bloc de notas «ANSI»): si no, «picazón»
    salía «picaz�n»."""
    try:
        return datos.decode("utf-8-sig")
    except UnicodeDecodeError:
        return datos.decode("cp1252", "replace")


def _pypdf():
    """pypdf viaja con la sala en vendor/ (así sirve con una actualización, sin reinstalar). El .exe de antes no trae
    xml.dom, que pypdf solo usa para los metadatos XMP: si falta, se reemplaza esa parte por una vacía."""
    vendor = str(Path(__file__).resolve().parent / "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        import xml.dom.minidom  # noqa: F401
        import xml.dom.expatbuilder  # noqa: F401
        import xml.dom.xmlbuilder  # noqa: F401
    except ImportError:
        import types
        xmp = types.ModuleType("pypdf.xmp")
        xmp.XmpInformation = type("XmpInformation", (), {"__init__": lambda self, *a, **k: None})
        xmp.XMP_MAX_ELEMENT_COUNT, xmp.XMP_MAX_INPUT_LENGTH = 100_000, 10_000_000
        sys.modules.setdefault("pypdf.xmp", xmp)
    import pypdf
    return pypdf


def _texto_pdf(datos):
    """El texto de un PDF (el guion exportado desde Word o Google Docs), un renglón por línea. Los renglones que el
    PDF partió por el ancho de la hoja se vuelven a juntar en su párrafo, como en el Word."""
    import io as _io
    pypdf = _pypdf()
    lector = pypdf.PdfReader(_io.BytesIO(datos))
    if lector.is_encrypted:
        try:
            lector.decrypt("")
        except Exception:  # noqa: BLE001
            raise ValueError("Ese PDF tiene contraseña: expórtalo de nuevo sin contraseña.")
    lineas = []
    for pag in lector.pages:
        lineas += (pag.extract_text() or "").replace("\r", "").split("\n")
    return _como_titulos(_juntar_renglones(lineas))


def _como_titulos(texto):
    """En el PDF los títulos y las tablas pierden su forma («C1 HVA · gancho», «Cuerpo 1 Tres cosas…», «2 Uno: …»,
    «CTA Guarda…»): se escriben como los entiende el lector de guiones («## C1 — HVA», «1. Tres cosas…», «CTA. …»)."""
    out, pas = [], False
    for ln in texto.split("\n"):
        s = ln.strip()
        # cuerpo en tabla PAS: «Cuerpo · Problema · Agitación · Solución P El parvovirus…» y las filas «A …», «S …»
        if m := re.match(r"^CUERPO\s*·\s*PROBLEMA\b.*?\s([PAS])\s+(.+)$", s, re.I):
            pas = True
            out += ["### Cuerpo", f"{m[1]}. {m[2]}"]
            continue
        if pas and (m := re.match(r"^([PAS])\s+(.+)$", s)):
            out.append(f"{m[1]}. {m[2]}")
            continue
        if m := re.fullmatch(r"(?:C|VIDEO)\s*(\d+)\s+(?![.:\-—–)])(.{1,60})", s, re.I):  # «Cuerpo 1 …» es la fila 1
            if len(m[2].split()) <= 10 and not re.search(r"[.!?]$", m[2]):  # un tema corto, no una frase
                # el tema largo no se pone: el lector tomaría «Calidad de vida (todo en uno)» como la primera frase
                out.append(f"## C{int(m[1])}" + (f" — {m[2]}" if len(m[2].split()) < 6 else ""))
                continue
        # «1. SUPLEVET Y PARVOVIRUS CANINO»: un número y un título en mayúsculas abre el cuerpo 1
        if (m := re.fullmatch(r"(\d{1,2})\s*[.)—–-]?\s+(.{3,70})", s)) and m[2] == m[2].upper() and re.search(r"[A-ZÁÉÍÓÚÑ]{3}", m[2]):
            out.append(f"## C{int(m[1])}")
            continue
        if re.match(r"^(?:HOOKS?|GANCHOS?)\b", s, re.I) and len(s) <= 90 and not re.match(r"^HOOKS?\s*\d", s, re.I):
            out.append("## Hooks")
            continue
        if m := re.match(r"^CUERPO\s+(\d+)\s+(.+)$", s, re.I):  # la primera fila de la tabla del cuerpo
            out += ["### Cuerpo", f"{m[1]}. {m[2]}"]
            continue
        if re.fullmatch(r"(?:CTAS?\s+)?(?:CTAS?|CIERRES?)(?:\s*[·:(—–-].*)?", s, re.I):  # «CTA», «CTA CTAs · elige 2» (tabla)
            out.append("### CTA")
            continue
        if m := re.match(r"^(CTA(?:\s*\d+)?)\s+(?![.:\-—–)])(.+)$", s, re.I):
            out.append(f"{m[1]}. {m[2]}")
            continue
        if m := re.match(r"^(\d{1,2})\s+([^\d\s.].*)$", s):  # «2 Uno: su flora…» = la frase 2
            out.append(f"{m[1]}. {m[2]}")
            continue
        out.append(ln)
    return "\n".join(out)


_TITULO_PDF = re.compile(r"^\s*(?:[-•*#]+\s*)?(?:C\d+|H\d+|CTAS?\d*|HOOKS?|CUERPO|CIERRE|GANCHO|FRASE|PARTE|P\d+|PROMPT|"
                         r"IMAGEN|M[UÚ]SICA|NOTA|SFX|COPY|PORTADA|PLAN B|EVITAR)\b", re.I)
# un renglón que empieza con el código de una pieza o con el número de la frase (la fila de una tabla)
_FILA_PDF = re.compile(r"^\s*(?:H\d+|CTA\s*\d*|C\d+[.\-_ ]*(?:H|P)?\d*|CUERPO\s+\d+|\d{1,2}[\s.)])", re.I)


def _juntar_renglones(lineas):
    """Un PDF no tiene párrafos: corta las frases donde se acaba la hoja o la celda. Un renglón se junta con el anterior
    cuando no empieza con un código («H2», «CTA», «3») ni es un título o una viñeta, y además el anterior no terminó la
    frase, o es una fila con código (en una tabla, lo que sigue sin código es el resto de esa celda)."""
    out = []
    for ln in (x.rstrip() for x in lineas):
        prev = out[-1].strip() if out else ""
        titulo = _TITULO_PDF.match(ln) and not ln.strip()[:1].islower()  # «parte de la evaluación…» sigue la frase, no es «PARTE»
        if (ln.strip() and prev and not titulo and not _FILA_PDF.match(ln) and not re.match(r"^\s*[-•*]\s", ln)
                and (not re.search(r"[.!?:»”\")\]]$", prev) or (_FILA_PDF.match(prev) and not re.fullmatch(r"\S+", prev)))):
            out[-1] = out[-1].rstrip() + " " + ln.strip()
        else:
            out.append(ln)
    return "\n".join(out)


def _leer_guion_archivo(nombre_archivo, datos, hoja=None):
    """El guion de un archivo: produccion.json de Claude (completo), la plantilla en Excel (una hoja: `hoja`) o
    PDF / Word / .md / .txt (solo el texto)."""
    ext = Path(nombre_archivo).suffix.lower()
    if ext == ".xlsx":  # la plantilla de guion: la columna A dice qué es cada fila, no se adivina nada
        import guion_plantilla
        if hoja == guion_plantilla.TODAS:  # todas las hojas (una por ronda) en este proyecto
            piezas_g, avisos = guion_plantilla.juntar_hojas(guion_plantilla.hojas_de_guion(datos))
            return {"origen": "subido", "archivo": Path(nombre_archivo).name, "hoja": hoja, "piezas": piezas_g,
                    **({"avisos_hojas": avisos} if avisos else {})}
        nombre_hoja, piezas_g = guion_plantilla.leer(datos, hoja)
        return {"origen": "subido", "archivo": Path(nombre_archivo).name, "hoja": nombre_hoja, "piezas": piezas_g}
    if ext == ".json":
        try:
            prod = json.loads(datos.decode("utf-8-sig"))
        except ValueError:
            raise ValueError("Ese .json no se puede leer")
        if not isinstance(prod, dict) or not isinstance(prod.get("piezas"), dict) or not prod["piezas"]:
            raise ValueError("Ese .json no es un guion de la sala (le falta «piezas»)")
        prod.pop("origen", None)
    elif ext in (".docx", ".md", ".txt", ".pdf"):
        try:
            texto = _texto_docx(datos) if ext == ".docx" else _texto_pdf(datos) if ext == ".pdf" else _texto_plano(datos)
        except ValueError:
            raise
        except Exception:
            raise ValueError("No pude abrir ese PDF. Expórtalo de nuevo (Archivo → Guardar como PDF) o súbelo en Word."
                             if ext == ".pdf" else "No pude abrir ese Word. Guárdalo de nuevo como .docx o pásalo a .txt")
        if ext == ".pdf" and not texto.strip():
            raise ValueError("Ese PDF no tiene texto (es una imagen escaneada): súbelo en Word o expórtalo como PDF desde Word o Docs.")
        if not texto.strip():
            raise ValueError("El archivo está vacío")
        piezas_g = leer_formato_sala(texto)  # el Word o el .md que exporta la propia sala
        if piezas_g == {}:
            raise ValueError("Ese archivo es un guion exportado por la sala, pero no trae piezas que se puedan leer.")
        if piezas_g is None:
            piezas_g = leer_guion_texto(texto)
        if not piezas_g:
            raise ValueError("No encontré piezas en ese guion. Cada cuerpo tiene que empezar con «C1», «C2»…, "
                             "los hooks con «H1», «H2»… (o «C1-H1: texto») y el cierre con «CTA».")
        prod = {"origen": "subido", "archivo": Path(nombre_archivo).name, "piezas": piezas_g}
    else:
        raise ValueError("Sube el guion en la plantilla de Excel (.xlsx), PDF, Word (.docx), texto (.md o .txt) o el produccion.json de Claude.")
    return prod


def _resumen_guion(prod, ext):
    tipos = [tipo_de(n) for n in prod["piezas"]]
    return {"piezas": len(tipos), "cuerpos": tipos.count("cuerpo"), "hooks": tipos.count("hook"),
            "ctas": tipos.count("cta"), "origen": "claude" if ext == ".json" else "texto"}


def _firma(f):
    st = f.stat()
    return f"{f.name}|{st.st_size}|{int(st.st_mtime)}"


def subir_guion(nombre_archivo, datos, hoja=None):
    """Guion que se sube desde la sala (botón «Subir guion»). Reemplaza al que había (queda una copia) y deja el
    archivo en la carpeta del guion, como si el equipo lo hubiera puesto ahí. Devuelve {piezas, cuerpos, hooks, ctas, origen}."""
    ext = Path(nombre_archivo).suffix.lower()
    prod = _leer_guion_archivo(nombre_archivo, datos, hoja)
    S.guion.mkdir(parents=True, exist_ok=True)
    limpio = re.sub(r'[\\/:*?"<>|]+', "_", Path(nombre_archivo).name)[:80]
    if limpio.lower() in _NO_SON_GUION:
        limpio = "guion_subido" + ext
    destino = S.guion / limpio
    destino.write_bytes(datos)
    with GUION_CANDADO:
        prod = {**prod, **({} if ext == ".json" else {"origen": "subido"}), "archivo": destino.name, "firma": _firma(destino)}
        if ext != ".json":  # un Word o un .txt solo trae el texto: se mezcla con lo que ya tenía el guion
            prod = _mezclar_guion(_guion_propio(), prod, time.time())
        _guardar_produccion(prod)
    return _resumen_guion(prod, ext)


def probar_guion(nombre_archivo, datos, hoja=None):
    """Lee un guion sin guardar nada: qué trae y qué piezas del guion de ahora se quitarían si se sube.
    Un Excel con varias hojas de guion devuelve {"hojas": [...]} para elegir una."""
    ext = Path(nombre_archivo).suffix.lower()
    try:
        prod = _leer_guion_archivo(nombre_archivo, datos, hoja)
    except ValueError as e:
        if getattr(e, "hojas", None):
            return {"hojas": e.hojas}
        raise
    base = _guion_propio() or {}
    viejas = set((base.get("piezas") or {}))
    nuevas = set(_mezclar_guion(base, prod, time.time())["piezas"] if ext != ".json" else prod["piezas"])
    orden = lambda n: ({"cuerpo": 0, "hook": 1, "cta": 2}.get(tipo_de(n), 3), n)
    return {**_resumen_guion(prod, ext), "habia": len(viejas), "se_quitan": sorted(viejas - nuevas, key=orden),
            "avisos_hojas": prod.get("avisos_hojas") or []}


def _mezclar_guion(base, nuevo, cuando):
    """Un guion de texto (Word, .md, .txt) sobre el que ya había: cambia lo que dice cada pieza pero conserva lo que
    el texto no trae (marcas, imágenes, prompts, copies, música). Lo corregido en la sala después de que se escribió
    ese archivo (cuando = fecha del archivo) gana. Con el guion de Claude no se pierde ninguna pieza; con uno de texto,
    las que ya no están en el archivo se quitan."""
    viejas = (base or {}).get("piezas") or {}
    if not viejas:
        return nuevo
    de_claude = not base.get("origen")
    out = {}
    for nombre, pz in nuevo["piezas"].items():
        v = viejas.get(nombre)
        if not v:
            out[nombre] = pz
        elif float(v.get("editado_en_sala_t") or 0) > cuando:
            out[nombre] = v  # se corrigió en la sala después de escribirse el archivo
        else:
            m = {**v, **{k: pz[k] for k in ("tipo", "texto") if k in pz}}
            if pz.get("tipo") in ("hook", "cta") and v.get("tipo"):
                m["tipo"] = v["tipo"]  # «hook»/«cta» a secas no pisa el estilo que ya tenía (pregunta, anuncio…)
            for k in ("todos", "nota"):  # «para todos los cuerpos» y la nota de la plantilla: los manda el archivo nuevo
                if pz.get(k):
                    m[k] = pz[k]
                else:
                    m.pop(k, None)
            if pz.get("partes"):
                m["partes"] = pz["partes"]
            elif " ".join(v.get("partes") or []).strip() != str(pz.get("texto") or "").strip():
                m.pop("partes", None)  # si el texto es el mismo, se quedan sus frases
            m.pop("editado_en_sala", None)
            m.pop("editado_en_sala_t", None)
            out[nombre] = m
    if de_claude:
        out.update({k: v for k, v in viejas.items() if k not in out})
    resto = {k: v for k, v in base.items() if k != "piezas"}
    mezcla = {**resto, **{k: v for k, v in nuevo.items() if k != "piezas"},
              "piezas": dict(sorted(out.items(), key=lambda x: ({"cuerpo": 0, "hook": 1, "cta": 2}.get(tipo_de(x[0]), 3), x[0])))}
    if de_claude:  # sigue siendo el guion de Claude (con sus marcas): no pasa a «subido»
        mezcla.pop("origen", None)
    return mezcla


# lo que la sala misma escribe en la carpeta del guion (no es un guion que haya puesto el equipo)
_NO_SON_GUION = {"produccion.json", "produccion.anterior.json", "guion_host.md", "guion_produccion.md", "resultados.md",
                 "metricas.json", "asignaciones.json"}
_GUION_CON_ERROR = {}  # firma → error, para no intentar leer una y otra vez un archivo que no se entiende


def guion_en_carpeta():
    """El guion más nuevo que el equipo dejó en la carpeta Guion/Guiones (Word, .md, .txt o un .json de Claude)."""
    archivos = [f for d in CARPETAS_GUION if (S.principal / d).is_dir() for f in (S.principal / d).iterdir()
                if f.is_file() and f.suffix.lower() in (".docx", ".pdf", ".md", ".txt", ".json", ".xlsx")
                and f.name.lower() not in _NO_SON_GUION and not f.name.startswith(("~$", "."))
                and not re.search(r"\((?:producci[oó]n|para grabar)\)$", f.stem)]  # los Word que exporta la sala
    con_nombre = [f for f in archivos if "guion" in norm(f.stem) or "script" in norm(f.stem)]
    return max(con_nombre or archivos, key=lambda f: f.stat().st_mtime, default=None)


def importar_guion_de_carpeta():
    """Si en la carpeta del guion hay un archivo nuevo, lo lee y pasa a ser el guion de la sesión: así basta con
    dejar el guion en Drive (Guion/ o Guiones/) para que la sala compare lo que se dijo y lo que no.
    · Sin guion, o con el que la sala armó con lo grabado: se lee el que haya.
    · Con uno ya leído de la carpeta: se lee si el archivo es otro o cambió.
    · Con el produccion.json de Claude (marcas, imágenes, copies): solo si el archivo es más nuevo que ese guion.
    Devuelve {archivo, resumen} si leyó uno, {archivo, error} si no lo entendió, o None si no había nada nuevo."""
    f = guion_en_carpeta()
    if not f:
        return None
    firma, base = _firma(f), _guion_propio()
    if base.get("piezas"):
        if base.get("firma") == firma:  # ese mismo archivo ya se leyó (también si se mezcló con el de Claude)
            return None
        if not base.get("origen"):  # el de Claude: los .md que vienen con él no lo reemplazan
            actual = next((x for x in (S.guion / "produccion.json", S.grab / "produccion.json") if x.exists()), None)
            if actual and f.stat().st_mtime <= actual.stat().st_mtime + 120:
                return None
    if firma in _GUION_CON_ERROR:
        return {"archivo": f.name, "error": _GUION_CON_ERROR[firma]}
    try:
        # el mismo Excel corregido: la misma hoja que se eligió al subirlo
        prod = _leer_guion_archivo(f.name, f.read_bytes(), base.get("hoja") if base.get("archivo") == f.name else None)
    except (ValueError, OSError) as e:
        msg = (f"«{f.name}» tiene varias hojas de guion: súbelo con «Subir guion» para elegir cuál va en esta sesión."
               if getattr(e, "hojas", None) else str(e))
        _GUION_CON_ERROR[firma] = msg
        return {"archivo": f.name, "error": msg}
    # un .json de Claude es un guion completo (no lleva «subido»: ese solo trae el texto). Uno de texto se mezcla con
    # el que había: no se pierden las marcas del guion de Claude ni lo corregido en la sala después del archivo
    with GUION_CANDADO:
        prod = {**prod, **({} if f.suffix.lower() == ".json" else {"origen": "subido"}), "archivo": f.name, "firma": firma}
        if f.suffix.lower() != ".json":
            prod = _mezclar_guion(_guion_propio(), prod, f.stat().st_mtime)
        prod = {k: v for k, v in prod.items() if v is not None}
        _guardar_produccion(prod)
    return {"archivo": f.name, "resumen": _resumen_guion(prod, f.suffix.lower())}


# ---------- Teleprompter ----------

def guion_para_grabar(semana=None):
    """Piezas del guion de una sesión en el orden de la hoja de grabación: cuerpos (por frases), hooks, CTAs."""
    global S, _prod_cache
    anterior, cache = S, _prod_cache
    if semana and semana != S.semana:
        S, _prod_cache = Rutas(semana), None
    try:
        prod = produccion()
    finally:
        S, _prod_cache = anterior, cache
    out = []
    for nombre, info in prod.get("piezas", {}).items():
        texto = info.get("texto", "")
        if not texto:
            continue
        tipo = tipo_de(nombre) if info.get("tipo") not in ("cuerpo",) else "cuerpo"
        partes = info.get("partes") or (re.split(r"(?<=[.!?])\s+", texto.strip()) if tipo == "cuerpo" else [texto])
        out.append({"nombre": nombre, "tipo": tipo, "tipo_hook": info.get("tipo") if tipo == "hook" and info.get("tipo") != "hook" else None,
                    "texto": texto, "partes": [x for x in partes if x.strip()], **({"todos": info["todos"]} if info.get("todos") else {})})
    orden = {"cuerpo": 0, "hook": 1, "cta": 2}
    natural = lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)]  # C2 antes que C10
    return sorted(out, key=lambda x: (orden[x["tipo"]], natural(x["nombre"])))


def leer_subs(pieza):
    f = S.subs / f"{pieza}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"pieza": pieza, "lineas": []}


def _repartir_palabras(palabras, viejas, s, e):
    """Tiempos de las palabras de una línea corregida: las que siguen igual conservan los suyos (así el resaltado
    sigue a la voz); las nuevas o cambiadas se reparten en el hueco que dejaron. viejas = [{w, s, e}]."""
    import difflib
    sm = difflib.SequenceMatcher(None, [norm(w) for w in palabras], [norm(v["w"]) for v in viejas], autojunk=False)
    t = [None] * len(palabras)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            t[a + k] = (viejas[b + k]["s"], viejas[b + k]["e"])
    i = 0
    while i < len(palabras):
        if t[i]:
            i += 1
            continue
        j = i
        while j < len(palabras) and not t[j]:
            j += 1
        desde = t[i - 1][1] if i > 0 else s
        hasta = t[j][0] if j < len(palabras) else e
        if hasta - desde < 0.05 * (j - i):
            # sin hueco (se dijo de corrido): la palabra nueva comparte el tiempo de la de al lado, sin pisar a nadie
            if i > 0:
                a0, b0 = t[i - 1]
                medio = round((a0 + b0) / 2, 3)
                t[i - 1] = (a0, medio)
                desde, hasta = medio, b0
            elif j < len(palabras):
                a0, b0 = t[j]
                medio = round((a0 + b0) / 2, 3)
                t[j] = (medio, b0)
                desde, hasta = a0, medio
            else:
                desde, hasta = s, max(e, s + 0.05 * (j - i))
        paso = (hasta - desde) / (j - i)
        for k in range(i, j):
            t[k] = (round(desde + (k - i) * paso, 3), round(desde + (k - i + 1) * paso, 3))
        i = j
    return [{"w": w, "s": round(a, 3), "e": round(b, 3)} for w, (a, b) in zip(palabras, t)]


def _mover_marcas(marcas, antes, despues):
    """Las marcas van por palabra + cuál vez se dice (n). Si se corrige el texto, cada marca pasa a la misma
    palabra en el texto nuevo (aunque se haya agregado otra igual antes, o se haya cambiado esa palabra)."""
    import difflib
    if not marcas or antes == despues:
        return marcas
    na, nd = [norm(w) for w in antes], [norm(w) for w in despues]
    mapa = {}
    sm = difflib.SequenceMatcher(None, na, nd, autojunk=False)
    for op, a0, a1, d0, d1 in sm.get_opcodes():
        for k in range(a1 - a0):
            if op == "equal":
                mapa[a0 + k] = d0 + k
            elif op == "replace" and d1 > d0:
                mapa[a0 + k] = min(d1 - 1, d0 + k)  # la palabra cambiada: la marca pasa a la que la reemplazó
    out = []
    for m in marcas:
        obj = [norm(x) for x in str(m.get("palabra", "")).split() if norm(x)]
        if not obj:
            out.append(m)
            continue
        vistos, pos = 0, None
        for k in range(len(na) - len(obj) + 1):
            if na[k:k + len(obj)] == obj:
                vistos += 1
                if vistos == max(1, int(m.get("n") or 1)):
                    pos = k
                    break
        if pos is None or pos not in mapa:
            out.append(m)  # ya no se encontraba antes: queda igual (la sala avisa que no encuentra su palabra)
            continue
        nuevo = mapa[pos]
        largo = len(obj)
        palabra = " ".join(despues[nuevo:nuevo + largo])
        n = sum(1 for k in range(nuevo + 1) if nd[k:k + largo] == [norm(x) for x in palabra.split()])
        out.append({**m, "palabra": palabra, "n": max(1, n)})
    return out


def guardar_lineas_editadas(pieza, lineas_nuevas):
    """Recibe las líneas corregidas a mano y recalcula los tiempos de cada palabra."""
    sub = S.subs / f"{pieza}.json"
    datos = leer_subs(pieza)
    viejas = {(round(l["s"], 2), round(l["e"], 2)): l for l in datos["lineas"]}
    antes = [p["w"] for ln in datos["lineas"] for p in ln.get("palabras", [])]
    marcas_antes = marcas_de(pieza)
    out = []
    for ln in lineas_nuevas:
        texto = " ".join(ln["texto"].split())
        if not texto:
            continue
        s, e = float(ln["s"]), float(ln["e"])
        palabras = texto.split(" ")
        vieja = viejas.get((round(s, 2), round(e, 2)))
        dadas = ln.get("palabras") or []
        if len(dadas) == len(palabras) and all("s" in p and "e" in p for p in dadas):  # el editor movió o cortó la línea
            pal = [{"w": w, "s": round(float(p["s"]), 3), "e": round(float(p["e"]), 3)} for w, p in zip(palabras, dadas)]
        elif vieja and len(vieja["palabras"]) == len(palabras):
            pal = [{"w": w, "s": v["s"], "e": v["e"]} for w, v in zip(palabras, vieja["palabras"])]
        elif vieja and vieja.get("palabras"):  # cambió el número de palabras: lo que no cambió conserva sus tiempos
            pal = _repartir_palabras(palabras, vieja["palabras"], s, e)
        else:
            paso = (e - s) / len(palabras)
            pal = [{"w": w, "s": round(s + i * paso, 2), "e": round(s + (i + 1) * paso, 2)} for i, w in enumerate(palabras)]
        out.append({"texto": texto, "s": s, "e": e, "palabras": pal})
    if [ln["texto"] for ln in out] == [ln["texto"] for ln in datos["lineas"]] and \
            [(ln["s"], ln["e"]) for ln in out] == [(float(ln["s"]), float(ln["e"])) for ln in datos["lineas"]]:
        return  # nada cambió: no se marca como corregido a mano (así sigue mejorándose solo con el modelo nuevo)
    datos["lineas"] = out
    despues = [p["w"] for ln in out for p in ln["palabras"]]
    movidas = _mover_marcas(marcas_antes, antes, despues)
    if movidas != marcas_antes:
        datos["marcas"] = movidas
    datos.pop("automatico", None)  # ya lo tocó una persona: no se vuelve a transcribir solo
    sub.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- Marcas del guion ----------

def marcas_de(pieza):
    """Las marcas editadas en la sala ganan; si no hay, las del guion (produccion.json)."""
    d = leer_subs(pieza)
    if "marcas" in d:
        return d["marcas"]
    return info_pieza(pieza).get("marcas", [])


def guardar_marcas(pieza, marcas):
    d = leer_subs(pieza)
    d["marcas"] = marcas
    (S.subs / f"{pieza}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def ubicar(lineas, palabra, n=1):
    """Busca la n-ésima aparición de `palabra` (puede ser de varias palabras). Devuelve (linea, indice) o None."""
    objetivo = [norm(x) for x in palabra.split() if norm(x)]
    if not objetivo:
        return None
    plano = [(li, wi, norm(p["w"])) for li, ln in enumerate(lineas) for wi, p in enumerate(ln["palabras"])]
    vistos = 0
    for k in range(len(plano) - len(objetivo) + 1):
        if all(plano[k + j][2] == objetivo[j] for j in range(len(objetivo))):
            vistos += 1
            if vistos == max(1, int(n or 1)):
                return plano[k][0], plano[k][1]
    return None


def ubicar_parecida(lineas, palabra, minimo=0.72):
    """Si la palabra exacta del guion no se dijo (se grabó «veterinaria» y el guion dice «veterinario»), la más
    parecida que sí se dijo. Devuelve (linea, indice, parecido, lo_que_se_dijo) o None."""
    import difflib
    objetivo = [norm(x) for x in palabra.split() if norm(x)]
    if not objetivo:
        return None
    plano = [(li, wi, norm(p["w"]), p["w"]) for li, ln in enumerate(lineas) for wi, p in enumerate(ln["palabras"])]
    n, mejor = len(objetivo), None
    for k in range(len(plano) - n + 1):
        trozo = plano[k:k + n]
        r = difflib.SequenceMatcher(None, " ".join(objetivo), " ".join(x[2] for x in trozo)).ratio()
        if r >= minimo and (not mejor or r > mejor[2]):
            mejor = (trozo[0][0], trozo[0][1], round(r, 2), " ".join(x[3] for x in trozo))
    return mejor


def resolver_marcas(pieza, lineas):
    """Convierte las marcas en eventos con tiempo (relativo a la pieza). Si la palabra exacta no se dijo, se usa
    la más parecida (así la imagen, el sonido o la música salen igual aunque la persona cambie una palabra)."""
    out, perdidas = [], []
    for m in marcas_de(pieza):
        pos = ubicar(lineas, m.get("palabra", ""), m.get("n", 1))
        if pos is None:
            cerca = ubicar_parecida(lineas, m.get("palabra", ""))
            if cerca:
                li, wi, _, dicho = cerca
                out.append({**m, "linea": li, "indice": wi, "t": lineas[li]["palabras"][wi]["s"], "aproximada": dicho})
                continue
            # una imagen o un clip cuya palabra no se dijo para nada (el guion cambió después de escribir los prompts):
            # no se pierde; va en una palabra larga cerca de un tercio de la pieza, donde no haya otra imagen
            if m.get("asset") and (lugar := _lugar_para_imagen(lineas, out)):
                li, wi = lugar
                out.append({**m, "linea": li, "indice": wi, "t": lineas[li]["palabras"][wi]["s"],
                            "aproximada": lineas[li]["palabras"][wi]["w"], "puesta_sola": True})
                continue
            perdidas.append(f"{pieza}: «{m.get('palabra')}»")
            continue
        li, wi = pos
        out.append({**m, "linea": li, "indice": wi, "t": lineas[li]["palabras"][wi]["s"]})
    return out, perdidas


def _lugar_para_imagen(lineas, ya):
    """(línea, palabra) para una imagen sin palabra: la palabra más larga de la línea más cercana a un tercio de la
    pieza, lejos (2 s o más) de las imágenes que ya hay. None si la pieza no tiene palabras."""
    pal = [(li, wi, w) for li, ln in enumerate(lineas) for wi, w in enumerate(ln.get("palabras") or [])]
    if not pal:
        return None
    fin = pal[-1][2]["e"]
    tiempos = [e["t"] for e in ya if e.get("asset")]
    libres = [x for x in pal if all(abs(x[2]["s"] - t) >= 2 for t in tiempos)] or pal
    objetivo = fin / 3
    li = min({x[0] for x in libres}, key=lambda k: abs(lineas[k]["s"] - objetivo))
    li_, wi, _ = max((x for x in libres if x[0] == li), key=lambda x: len(re.sub(r"\W", "", x[2]["w"])))
    return li_, wi


# ---------- 2. Armar ----------

# ---------- Ediciones (A, B, C…): el mismo material editado de otra forma ----------
# La A es la edición de siempre (el estilo de Plantillas, la música del guion). Cada edición nueva tiene su propio
# estilo de zoom, su música, su plantilla de subtítulos, su ritmo y sus propios cortes y sonidos por pieza.
# El «Plan de la sesión» dice qué ediciones se arman de cada cuerpo: C1-H1-CTA1 (A) y C1-H1-CTA1-B (B).
# Los ajustes de una pieza en la edición B se guardan como «C1@B»; mientras no se toquen, usa los de la A.

EDICIONES_LETRAS = "BCDEFGHIJKLMNOPQRSTUVWXYZ"
MODOS_MUSICA = ("igual", "otra", "pista")


def _ediciones_path():
    return S.subs / "_ediciones.json"


def _leer_ediciones():
    try:
        d = json.loads(_ediciones_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        d = {}
    return {"ediciones": d.get("ediciones") or {}, "plan": d.get("plan") or {}}


def _guardar_ediciones(d):
    S.subs.mkdir(parents=True, exist_ok=True)
    _ediciones_path().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def ediciones():
    """{letra: {nombre, estilo, musica, plantilla, ritmo, cambio}}: la A (la de siempre) primero."""
    d = _leer_ediciones()["ediciones"]
    return {"A": {"nombre": "Edición A", "base": True}, **{k: d[k] for k in sorted(d)}}


def letra_edicion(combo):
    """Letra de la edición de un video: «A» (la de siempre), «B», «C»…"""
    return (combo or {}).get("edicion") or "A"


def clave_pieza(nombre, e="A"):
    """Dónde se guardan los ajustes de una pieza en una edición: «C1» en la A, «C1@B» en la B."""
    return nombre if e in ("A", None, "") else f"{nombre}@{e}"


def ajustes_de_pieza(nombre, e="A", todo=None):
    """Ajustes de una pieza en una edición: los suyos, o los de la A mientras no se hayan tocado en esa edición."""
    todo = ajustes_piezas() if todo is None else todo
    k = clave_pieza(nombre, e)
    return todo[k] if k in todo else todo.get(nombre, {})


def crear_edicion(nombre=None, copiar_de="A"):
    """Una edición nueva (B, C…). copiar_de: arranca con el estilo, la música y la plantilla de otra edición."""
    d = _leer_ediciones()
    usadas = set(d["ediciones"]) | {"A"}
    letra = next((x for x in EDICIONES_LETRAS if x not in usadas), None)
    if not letra:
        raise ValueError("Ya no hay más letras para ediciones")
    base = {k: v for k, v in d["ediciones"].get(copiar_de, {}).items() if k not in ("nombre", "cambio")}
    d["ediciones"][letra] = {"musica": {"modo": "otra"}, **base, "nombre": str(nombre or f"Edición {letra}")[:40],
                             "cambio": time.time()}
    _guardar_ediciones(d)
    return letra


def guardar_edicion(letra, cambios):
    """Cambia el nombre, el estilo (zoom), la música, la plantilla o el ritmo de una edición (no la A)."""
    d = _leer_ediciones()
    if letra not in d["ediciones"]:
        raise ValueError("Esa edición no existe")
    ed = dict(d["ediciones"][letra])
    if "nombre" in cambios:
        ed["nombre"] = str(cambios["nombre"] or f"Edición {letra}")[:40]
    if "estilo" in cambios:
        est = {k: v for k, v in (cambios["estilo"] or {}).items() if k in ESTILO_DEFECTO}
        ed["estilo"] = {**(ed.get("estilo") or {}), **est}
    if "musica" in cambios:
        m = cambios["musica"] or {}
        modo = m.get("modo") if m.get("modo") in MODOS_MUSICA else "otra"
        ed["musica"] = {"modo": modo, **({"pista": str(m.get("pista") or "")} if modo == "pista" else {})}
    if "plantilla" in cambios:
        ed["plantilla"] = cambios["plantilla"] if cambios["plantilla"] in plantillas() else None
    if "ritmo" in cambios:
        ed["ritmo"] = {"acelerar": bool((cambios["ritmo"] or {}).get("acelerar", True))}
    ed["cambio"] = time.time()
    d["ediciones"][letra] = {k: v for k, v in ed.items() if v not in (None, "", {})}
    _guardar_ediciones(d)
    return [c["codigo"] for c in combinaciones() if letra_edicion(c) == letra]


def borrar_edicion(letra):
    """Quita una edición, sus ajustes por pieza y la saca del plan (sus videos ya armados quedan en la carpeta)."""
    d = _leer_ediciones()
    d["ediciones"].pop(letra, None)
    d["plan"] = {c: [e for e in ls if e != letra] for c, ls in d["plan"].items()}
    _guardar_ediciones(d)
    todo = ajustes_piezas()
    if any(k.endswith(f"@{letra}") for k in todo):
        _ajustes_piezas_path().write_text(json.dumps({k: v for k, v in todo.items() if not k.endswith(f"@{letra}")},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")


def plan_sesion():
    """{cuerpo: [letras]}: qué ediciones se arman de cada cuerpo. La A siempre."""
    d, eds = _leer_ediciones(), ediciones()
    cuerpos = [p["nombre"] for p in piezas() if p["tipo"] == "cuerpo"]
    return {c: ["A"] + [e for e in d["plan"].get(c, []) if e in eds and e != "A"] for c in cuerpos}


def pieza_de_toma(nombre):
    """La pieza (C1, C1_H2, CTA1…) a la que pertenece una toma por su nombre (C1_P3 → C1)."""
    return next((p["nombre"] for p in piezas() if nombre in (p.get("nombres_partes") or [p["nombre"]]) or p["nombre"] == nombre), None)


def version_de_cuerpo(cuerpo, desde="A"):
    """«Nueva versión de C1 con los mismos hooks»: una edición nueva solo para ese cuerpo (C1-H1-CTA1-B, C1-H2-CTA1-B…)
    que arranca igual que la de ahora (los mismos cortes, la misma música y el mismo estilo) y se edita aparte; los
    videos de siempre no cambian. Los hooks y el CTA son los mismos (mientras no se toquen en esa versión)."""
    if not any(p["nombre"] == cuerpo and p["tipo"] == "cuerpo" for p in piezas()):
        raise ValueError(f"{cuerpo} no es un cuerpo de esta sesión")
    desde = desde if desde in ediciones() else "A"
    vistas = [e for e, d in ediciones().items() if str(d.get("nombre", "")).startswith(f"{cuerpo} · versión")]
    letra = crear_edicion(f"{cuerpo} · versión {len(vistas) + 2}", copiar_de=desde)
    guardar_edicion(letra, {"musica": {"modo": "igual"}})
    d = _leer_ediciones()
    d["plan"][cuerpo] = sorted(set(d["plan"].get(cuerpo, [])) | {letra})
    _guardar_ediciones(d)
    todo = ajustes_piezas()
    base = dict(ajustes_de_pieza(cuerpo, desde, todo))
    if base:  # arranca con los mismos cortes, encuadre e imágenes del cuerpo
        todo[clave_pieza(cuerpo, letra)] = {**base, "editado": time.time()}
        _ajustes_piezas_path().write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    return letra, [c["codigo"] for c in combinaciones() if c["cuerpo"] == cuerpo and letra_edicion(c) == letra]


def siguiente_toma_de_cuerpo(cuerpo):
    """El nombre para otra toma completa de un cuerpo que usa los mismos hooks: C1~A2, C1~A3… (como un ángulo nuevo)."""
    base = sin_angulo(cuerpo)[0]
    usados = {sin_angulo(p["nombre"])[1] for p in piezas() if sin_angulo(p["nombre"])[0] == base}
    n = 2
    while f"A{n}" in usados:
        n += 1
    return con_angulo(base, f"A{n}")


def guardar_plan(plan):
    """plan = {cuerpo: [letras]}: lo marcado en la tabla «Plan de la sesión»."""
    d, eds = _leer_ediciones(), ediciones()
    limpio = {c: sorted({e for e in (ls or []) if e in eds and e != "A"}) for c, ls in (plan or {}).items()}
    d["plan"] = {c: ls for c, ls in limpio.items() if ls}
    _guardar_ediciones(d)
    return plan_sesion()


def combinaciones():
    return iter(_con_memoria(("combinaciones", S.semana), lambda: list(_combinaciones_sin_memoria())))


def _combinaciones_sin_memoria():
    ps = piezas()
    cuerpos = [p["nombre"] for p in ps if p["tipo"] == "cuerpo"]
    hooks = [p["nombre"] for p in ps if p["tipo"] == "hook"]
    ctas = [p["nombre"] for p in ps if p["tipo"] == "cta"]
    alterna = {p["nombre"]: p.get("alterna") for p in ps}
    vs = leer_versiones()
    plan, eds = _leer_ediciones()["plan"], ediciones()
    # hooks «para todos los cuerpos» (H1 en la plantilla): se graban una vez como hooks del primer cuerpo y van con cada
    # cuerpo de su grupo (su ronda: «todos» = [C6, C7…]; True = todos los de la sesión) que no tenga su propio hook con ese número
    del_guion = produccion().get("piezas", {})
    alcance = {h: (del_guion.get(sin_angulo(h)[0]) or {}).get("todos") for h in hooks}
    comunes = [h for h in hooks if alcance[h]]
    num_hook = lambda h: sin_angulo(h)[0].split("_")[1]  # «C1_H2~A2» → «H2»
    llega = lambda h, cuerpo: alcance[h] is True or (isinstance(alcance[h], list) and cuerpo in alcance[h])
    for c in cuerpos:
        cb, ca = sin_angulo(c)
        # Otro ángulo: usa sus propios hooks y CTA si se grabaron así; si no, los de frente (el mismo número de hook),
        # y si el CTA de este cuerpo solo se grabó en el otro ángulo, ese. Antes se miraba si había CTA en ese ángulo
        # de CUALQUIER cuerpo: un cuerpo sin su CTA en ese ángulo se quedaba sin videos.
        # Un CTA con prefijo (P1_CTA1) es solo de ese cuerpo; uno sin prefijo (CTA-O) sirve para todos
        ctas_de = lambda ang: [x for x in ctas if sin_angulo(x)[1] == ang
                               and ("_" not in sin_angulo(x)[0] or sin_angulo(x)[0].split("_")[0] == cb)]
        propios = ctas_de(ca) or ctas_de("") or next((l for l in map(ctas_de, sorted({sin_angulo(x)[1] for x in ctas})) if l), [])
        if not propios and ctas:
            # este cuerpo no grabó su CTA (y no hay uno general): antes se quedaba SIN videos (P05: C8 y C10, 22 videos).
            # Usa el CTA del cuerpo más cercano (C7 o C9 para C8), de frente si hay; se puede cambiar en el editor.
            num = lambda x: int(m[1]) if (m := re.match(r"[CP](\d+)", sin_angulo(x)[0], re.I)) else 999
            yo = num(cb)
            prestado = min(ctas, key=lambda x: (abs(num(x) - yo), sin_angulo(x)[1] != "", x))
            propios = [prestado]
        de_cuerpo = [h for h in hooks if sin_angulo(h)[0].split("_")[0] == cb]
        propios_n = {num_hook(h) for h in de_cuerpo}
        de_cuerpo += [h for h in comunes if sin_angulo(h)[0].split("_")[0] != cb and num_hook(h) not in propios_n and llega(h, cb)]
        numeros = sorted({sin_angulo(h)[0] for h in de_cuerpo}, key=lambda x: [int(n) for n in re.findall(r"\d+", x)][::-1])
        hooks_c = [next((h for h in de_cuerpo if sin_angulo(h) == (n, ca)), None) or next((h for h in de_cuerpo if sin_angulo(h) == (n, "")), None)
                   or next((h for h in de_cuerpo if sin_angulo(h)[0] == n), None) for n in numeros]
        for i, h in enumerate([h for h in hooks_c if h]):
            # los CTA que salieron de separar uno en partes (CTA1, CTA1B) se turnan: un video con cada uno
            elegidos, familias = [], set()
            for cta in propios:
                fam = alterna.get(cta)
                if not fam:
                    elegidos.append(cta)
                elif fam not in familias:
                    familias.add(fam)
                    grupo = [x for x in propios if alterna.get(x) == fam]
                    elegidos.append(grupo[i % len(grupo)])
            for cta in elegidos:
                base = f"{cb}-{sin_angulo(h)[0].split('_')[1]}-{sin_angulo(cta)[0].split('_')[-1]}" + (f"-{ca}" if ca else "")
                # la A con el código de siempre; las otras ediciones del plan, con su letra al final (C1-H1-CTA1-B)
                for e in ["A"] + [x for x in plan.get(c, []) if x in eds and x != "A"]:
                    codigo = base if e == "A" else f"{base}-{e}"
                    propio = vs.get(codigo) if (vs.get(codigo) or {}).get("propio") else None
                    # con ajustes propios («solo a este video») el video usa los suyos y no los de sus piezas
                    yield {"codigo": codigo, "archivo": f"{S.prefijo}-{codigo}", "hook": h, "cuerpo": c, "cta": cta,
                           **({"edicion": e} if e != "A" else {}), **({"version": propio} if propio else {})}
    nombres = {p["nombre"] for p in ps}
    for vid, v in vs.items():
        if v.get("propio"):
            continue
        if {v.get("hook"), v.get("cuerpo"), v.get("cta")} <= nombres:
            yield {"codigo": vid, "archivo": f"{S.prefijo}-{vid}", "hook": v["hook"], "cuerpo": v["cuerpo"], "cta": v["cta"],
                   "version": v}


# ---------- Versiones editadas (otra combinación, frases quitadas, recortes) ----------

def _versiones_path():
    return S.subs / "_versiones.json"


def leer_versiones():
    try:
        return json.loads(_versiones_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def guardar_version(datos, vid=None):
    """Crea o actualiza una versión. Devuelve su código (C1-H2-CTA-v2).
    Con datos["propio"] y el código de un video, guarda los ajustes solo para ese video (no crea otro):
    deja de seguir lo que se edite en sus piezas hasta que se borren sus ajustes propios."""
    vs = leer_versiones()
    base = f"{datos['cuerpo']}-{datos['hook'].split('_')[-1]}-{datos['cta'].split('_')[-1]}"
    propio = bool(datos.get("propio")) or bool((vs.get(vid) or {}).get("propio"))
    if propio:
        vid = vid or datos.get("codigo")
        c = next((x for x in combinaciones() if x["codigo"] == vid and (not x.get("version") or x["version"].get("propio"))), None)
        if not c:
            raise ValueError("Ese video no existe en esta sesión")
        if (datos["hook"], datos["cuerpo"], datos["cta"]) != (c["hook"], c["cuerpo"], c["cta"]):
            raise ValueError("Para cambiar el hook, el cuerpo o el CTA, guárdalo como versión aparte")
    if not vid:
        n = 1
        while f"{base}-v{n}" in vs:
            n += 1
        vid = f"{base}-v{n}"
    vs[vid] = {"hook": datos["hook"], "cuerpo": datos["cuerpo"], "cta": datos["cta"],
               "quitar": sorted({int(i) for i in datos.get("quitar", [])}), "orden": _limpiar_orden(datos.get("orden")),
               "recorte_inicio": max(0.0, float(datos.get("recorte_inicio") or 0)),
               "recorte_fin": max(0.0, float(datos.get("recorte_fin") or 0)), "nombre": (datos.get("nombre") or "").strip()[:60],
               # recortes por bloque: [entrada, salida] en segundos dentro del hook, del CTA o de cada frase del cuerpo
               "recortes": _limpiar_recortes(datos.get("recortes") or {}),
               # zoom por bloque: {"hook": {"tipo": "in|out|punch", "fuerza": 0.15}, "cta": …, "partes": {"2": …}}
               # y tramos de zoom: {"tramos": {"hook": [...], "cuerpo": [...], "cta": [...]}} (ver _limpiar_tramos)
               "zoom": _limpiar_zoom(datos.get("zoom") or {}),
               "cortes": _limpiar_cortes(datos.get("cortes")), "quitar_rangos": _limpiar_rangos(datos.get("quitar_rangos")),
               "capas": _limpiar_capas(datos.get("capas")), "velocidad": _limpiar_velocidad(datos.get("velocidad")),
               "audio_aparte": _limpiar_aparte(datos.get("audio_aparte")), "duplicados": _limpiar_duplicados(datos.get("duplicados")),
               # guardada desde el editor: los cortes por voz ya los vio una persona (no se agregan solos otra vez)
               "voz_revisada": {k: True for k in ("hook", "cuerpo", "cta")} if datos.get("voz_revisada") else {}}
    if propio:
        vs[vid]["propio"] = True
    S.subs.mkdir(parents=True, exist_ok=True)
    _versiones_path().write_text(json.dumps(vs, ensure_ascii=False, indent=1), encoding="utf-8")
    return vid


def _limpiar_recortes(r):
    def par(x):
        try:
            a, b = float(x[0]), float(x[1])
            return [round(max(0.0, a), 2), round(max(a + 0.2, b), 2)]
        except (TypeError, ValueError, IndexError):
            return None
    out = {k: par(r[k]) for k in ("hook", "cta") if r.get(k)}
    if r.get("partes"):
        out["partes"] = {str(i): par(v) for i, v in r["partes"].items() if par(v)}
    return {k: v for k, v in out.items() if v}


def _limpiar_zoom(z):
    def uno(x):
        if not isinstance(x, dict) or x.get("tipo") not in ("in", "out", "punch"):
            return None
        return {"tipo": x["tipo"], "fuerza": round(min(0.5, max(0.03, float(x.get("fuerza") or 0.15))), 2)}
    out = {k: uno(z.get(k)) for k in ("hook", "cta")}
    out["partes"] = {str(i): uno(v) for i, v in (z.get("partes") or {}).items() if uno(v)}
    out["tramos"] = {k: _limpiar_tramos(v) for k, v in (z.get("tramos") or {}).items() if k in ("hook", "cuerpo", "cta") and _limpiar_tramos(v)}
    out["puntos"] = {k: _limpiar_puntos(v) for k, v in (z.get("puntos") or {}).items() if k in ("hook", "cuerpo", "cta") and _limpiar_puntos(v)}
    return {k: v for k, v in out.items() if v}


# Encuadre con fotogramas clave (como CapCut): en cada punto de la pieza, la escala y hacia dónde mira el cuadro.
# t en segundos del clip de la pieza; s = 1.0 es sin zoom; x/y = centro de lo que se ve (0–1 del ancho y del alto);
# curva = cómo va hacia el punto siguiente. Antes del primer punto y después del último se queda igual.
CURVAS_PUNTO = ("suave", "lineal", "golpe")


def _limpiar_puntos(lista):
    out = {}
    for k in (lista or [])[:80]:
        try:
            t = round(min(3600.0, max(0.0, float(k["t"]))), 3)
            out[t] = {"t": t, "s": round(min(4.0, max(1.0, float(k.get("s", 1)))), 3),
                      "x": round(min(1.0, max(0.0, float(k.get("x", 0.5)))), 4), "y": round(min(1.0, max(0.0, float(k.get("y", 0.5)))), 4),
                      "curva": k.get("curva") if k.get("curva") in CURVAS_PUNTO else "suave"}
        except (TypeError, ValueError, KeyError):
            continue
    return [out[t] for t in sorted(out)]


PISTAS_IMAGEN = 5  # «Imágenes», «Imágenes 2»… hasta 5 pistas en el editor


def _limpiar_capas(c):
    """Pistas «Imágenes» del editor: {pieza: [{id, t, dur, modo, mov, x, y, ancho, pista}]}. id = imagen o clip de
    la carpeta Flow de la sesión; t en segundos del clip de la pieza; en modo ventana, x/y/ancho en px del video
    (1080×1920 o, en 16:9, 1920×1080). pista: 0 = «Imágenes», 1 = «Imágenes 2»…; la de más arriba se ve encima."""
    out = {}
    for k, v in (c or {}).items():
        if k not in ("hook", "cuerpo", "cta"):
            continue
        lista = []
        for x in (v or [])[:60]:
            try:
                aid = str(x["id"])
                if not re.fullmatch(r"[\w.-]{1,80}", aid):
                    continue
                capa = {"id": aid, "t": round(min(3600.0, max(0.0, float(x["t"]))), 3),
                        "dur": round(min(60.0, max(0.3, float(x.get("dur", 2)))), 3),
                        "modo": "ventana" if x.get("modo") == "ventana" else "completa",
                        "mov": x.get("mov") if x.get("mov") in ("fijo", "zoom_in", "zoom_out") else "fijo"}
                if int(x.get("pista") or 0) > 0:
                    capa["pista"] = min(PISTAS_IMAGEN - 1, int(x["pista"]))
                if capa["modo"] == "ventana":
                    W, H = lienzo()
                    vx, vy, va = VENTANA_DEFECTO[formato()]
                    capa.update(ancho=int(min(min(W, H), max(200, float(x.get("ancho", va))))),
                                x=int(min(W, max(-200, float(x.get("x", vx))))), y=int(min(H, max(-200, float(x.get("y", vy))))))
                lista.append(capa)
            except (TypeError, ValueError, KeyError):
                continue
        if lista:
            out[k] = sorted(lista, key=lambda c: c["t"])
    return out


def _limpiar_orden(o):
    """Orden de las frases del cuerpo: [2, 0, 1] (números de parte, sin repetir). El orden normal no se guarda."""
    try:
        o = list(dict.fromkeys(int(i) for i in (o or [])))
    except (TypeError, ValueError):
        return None
    return o if o and o != sorted(o) else None


def _limpiar_velocidad(v):
    """Velocidad de cada bloque (para quien habla lento): {"hook": 1.2, "cta": 1.1, "partes": {"2": 1.25}}. 1 = normal."""
    def f(x):
        try:
            x = round(min(2.0, max(0.5, float(x))), 2)
        except (TypeError, ValueError):
            return None
        return x if abs(x - 1) > 0.001 else None
    out = {k: f((v or {}).get(k)) for k in ("hook", "cta")}
    out["partes"] = {str(i): f(x) for i, x in ((v or {}).get("partes") or {}).items() if f(x)}
    return {k: x for k, x in out.items() if x}


def _limpiar_cortes(c):
    """Cortes hechos con «Cortar en el cabezal»: {pieza: [segundos del clip]}."""
    out = {}
    for k, v in (c or {}).items():
        if k in ("hook", "cuerpo", "cta"):
            try:
                pts = sorted({round(float(t), 3) for t in v if 0 < float(t) < 3600})[:80]
            except (TypeError, ValueError):
                continue
            if pts:
                out[k] = pts
    return out


def _limpiar_rangos(r):
    """Pedazos quitados entre cortes: {pieza: [[desde, hasta], …]} en segundos del clip."""
    out = {}
    for k, v in (r or {}).items():
        if k in ("hook", "cuerpo", "cta"):
            lista = []
            for x in (v or [])[:80]:
                try:
                    a, b = float(x[0]), float(x[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if b - a >= 0.03:
                    lista.append([round(max(0.0, a), 3), round(b, 3)])
            if lista:
                out[k] = sorted(lista)
    return out


DESFASE_MAX = 3.0  # segundos que se puede correr la voz desagrupada (lo mismo en el editor)


def _limpiar_aparte(r):
    """Pedazos con el audio desagrupado del video (Ctrl+G en el editor): {pieza: [{a, b, sin_voz, sin_imagen, desfase}]}
    a/b en segundos del clip · sin_voz: el pedazo se ve mudo · sin_imagen: la voz suena sobre su primer cuadro congelado ·
    desfase: segundos que se corre su voz respecto de su imagen (− antes, + después), como un corte en J o en L."""
    out = {}
    for k, v in (r or {}).items():
        if k not in ("hook", "cuerpo", "cta"):
            continue
        lista = []
        for x in (v or [])[:80]:
            try:
                a, b = float(x["a"]), float(x["b"])
                d = max(-DESFASE_MAX, min(DESFASE_MAX, float(x.get("desfase") or 0)))
            except (TypeError, ValueError, KeyError):
                continue
            if b - a >= 0.03:
                lista.append({"a": round(max(0.0, a), 3), "b": round(b, 3), "sin_voz": bool(x.get("sin_voz")),
                              "sin_imagen": bool(x.get("sin_imagen")), "desfase": round(d, 3)})
        if lista:
            out[k] = sorted(lista, key=lambda x: x["a"])
    return out


def _limpiar_duplicados(r):
    """Pedazos repetidos («Duplicar» en el editor): {pieza: [[desde, hasta], …]} en segundos del clip; la copia va justo después."""
    return _limpiar_rangos(r)


# Tramos de zoom (como los fotogramas clave de CapCut): en un tramo de la pieza, el zoom y el punto de foco
# van de su valor inicial al final con una curva. a/b en segundos del archivo de la pieza; z = 1.0 es sin zoom;
# fx/fy = punto que queda fijo en pantalla (0–1 del ancho y del alto). Después del tramo: vuelve, queda o vuelve suave.
CURVAS_ZOOM = ("suave", "lineal", "entrada", "salida", "rebote", "temblor")
SALIDAS_ZOOM = ("vuelve", "queda", "suave")


def _limpiar_tramos(lista):
    def num(x, lo, hi, defecto):
        try:
            return round(min(hi, max(lo, float(x))), 3)
        except (TypeError, ValueError):
            return defecto
    out = []
    for t in (lista or [])[:60]:
        if not isinstance(t, dict):
            continue
        a = num(t.get("a"), 0, 3600, None)
        b = num(t.get("b"), 0, 3600, None)
        if a is None or b is None or b - a < 0.05:
            continue
        out.append({"a": a, "b": b, "z0": num(t.get("z0"), 1, 3, 1), "z1": num(t.get("z1"), 1, 3, 1.25),
                    "fx0": num(t.get("fx0"), 0, 1, 0.5), "fy0": num(t.get("fy0"), 0, 1, 0.38),
                    "fx1": num(t.get("fx1", t.get("fx0")), 0, 1, 0.5), "fy1": num(t.get("fy1", t.get("fy0")), 0, 1, 0.38),
                    "curva": t.get("curva") if t.get("curva") in CURVAS_ZOOM else "suave",
                    "salida": t.get("salida") if t.get("salida") in SALIDAS_ZOOM else "vuelve",
                    **({"nombre": str(t["nombre"])[:30]} if t.get("nombre") else {})})
    return sorted(out, key=lambda t: t["a"])


# ---------- Ediciones por pieza: lo que se cambia en un cuerpo, hook o CTA vale para todos sus videos ----------

def _ajustes_piezas_path():
    return S.subs / "_ajustes_piezas.json"


def ajustes_piezas():
    try:
        return json.loads(_ajustes_piezas_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def guardar_edicion_piezas(datos):
    """datos = lo que manda el editor (hook, cuerpo, cta, quitar, recortes, zoom). Guarda cada cambio en su pieza
    y devuelve los códigos de los videos que usan alguna pieza que cambió (hay que re-armarlos)."""
    todo = ajustes_piezas()
    r, z = _limpiar_recortes(datos.get("recortes") or {}), _limpiar_zoom(datos.get("zoom") or {})
    cortes, rangos = _limpiar_cortes(datos.get("cortes")), _limpiar_rangos(datos.get("quitar_rangos"))
    capas = _limpiar_capas(datos.get("capas"))
    vel = _limpiar_velocidad(datos.get("velocidad"))
    aparte, dups = _limpiar_aparte(datos.get("audio_aparte")), _limpiar_duplicados(datos.get("duplicados"))
    propio = lambda pz: {"zoom_tramos": (z.get("tramos") or {}).get(pz), "zoom_puntos": (z.get("puntos") or {}).get(pz),
                         "cortes": cortes.get(pz), "quitar_rangos": rangos.get(pz), "capas": capas.get(pz),
                         "audio_aparte": aparte.get(pz), "duplicados": dups.get(pz)}
    e = datos.get("edicion") if datos.get("edicion") in ediciones() else "A"
    nuevos = {
        datos["hook"]: {"recorte": r.get("hook"), "zoom": z.get("hook"), "velocidad": vel.get("hook"), **propio("hook")},
        datos["cuerpo"]: {"partes": r.get("partes") or {}, "zoom_partes": z.get("partes") or {}, **propio("cuerpo"),
                          "velocidad_partes": vel.get("partes"), "quitar": sorted({int(i) for i in datos.get("quitar", [])}),
                          "orden": _limpiar_orden(datos.get("orden"))},
        datos["cta"]: {"recorte": r.get("cta"), "zoom": z.get("cta"), "velocidad": vel.get("cta"), **propio("cta")},
    }
    cambiadas = []
    for pieza, val in nuevos.items():
        if datos.get("voz_revisada"):  # los cortes por voz de la pieza ya los vio una persona: manda lo que dejó
            val["voz_revisada"] = True
        val = {k: v for k, v in val.items() if v not in (None, {}, [])}
        propios = ("editado", "sonidos", "sonidos_quitar")  # los sonidos a mano se guardan aparte (Sonidos del editor)
        actual = ajustes_de_pieza(pieza, e, todo)  # en una edición nueva, arranca de lo de la A
        antes = {k: v for k, v in actual.items() if k not in propios}
        sin_marca = lambda d: {k: v for k, v in d.items() if k != "voz_revisada"}
        if val != antes and sin_marca(val) == sin_marca(antes):
            # solo se anota que una persona ya vio sus cortes por voz: no cambia el video, no se rearma nada
            todo[clave_pieza(pieza, e)] = {**actual, "voz_revisada": True}
        elif val != antes:
            sonidos = {k: v for k, v in actual.items() if k in ("sonidos", "sonidos_quitar")}
            todo[clave_pieza(pieza, e)] = {**val, **sonidos, "editado": time.time()}  # la fecha queda aunque se deshaga: sus videos se re-arman
            cambiadas.append(pieza)
    todo = {k: v for k, v in todo.items() if v}
    S.subs.mkdir(parents=True, exist_ok=True)
    _ajustes_piezas_path().write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    return [c["codigo"] for c in combinaciones() if not c.get("version") and letra_edicion(c) == e
            and {c["hook"], c["cuerpo"], c["cta"]} & set(cambiadas)]


def guardar_quitados(pieza, rangos):
    """«Quitar» en el texto de Piezas: los tramos que se cortan de la pieza (video y audio), en segundos de la pieza.
    Se guardan igual que los cortes de «Editar video» (un corte en cada borde y el pedazo quitado), así que se ven y
    se pueden volver a poner desde las dos partes. Devuelve los códigos de los videos que hay que rearmar."""
    todo = ajustes_piezas()
    actual = dict(ajustes_de_pieza(pieza, "A", todo))
    nuevos = _limpiar_rangos({"cuerpo": rangos}).get("cuerpo", [])
    viejos = actual.get("quitar_rangos") or []
    if nuevos == viejos:
        return []
    bordes = lambda rs: {round(x, 3) for r in rs for x in r}
    # los cortes de los tramos que se volvieron a poner se van (si ningún tramo que queda los usa); los nuevos se suman
    cortes = (set(actual.get("cortes") or []) - (bordes(viejos) - bordes(nuevos))) | bordes(nuevos)
    actual["cortes"] = sorted(t for t in cortes if t > 0)
    actual["quitar_rangos"] = nuevos
    actual = {k: v for k, v in actual.items() if v not in (None, {}, [])}
    todo[clave_pieza(pieza, "A")] = {**actual, "editado": time.time()}
    S.subs.mkdir(parents=True, exist_ok=True)
    _ajustes_piezas_path().write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    return [c["codigo"] for c in combinaciones() if not c.get("version") and letra_edicion(c) == "A"
            and pieza in (c["hook"], c["cuerpo"], c["cta"])]


def ajustes_silencios():
    return {"auto": True, "margen": 0.08, **CONFIG.get("silencios", {})}


def bordes_de_voz(nombre, ini=0.0, fin=None):
    """(desde, hasta): dónde empieza y termina de verdad la voz entre ini y fin (s de la pieza), o None si ahí no
    se habla. La transcripción suele estirar la primera palabra sobre el silencio de antes («Se» de 0,0 a 0,7 s
    cuando la voz entra en 0,68 s), así que el borde se afina con la onda: el golpe de la voz cerca de la primera
    palabra y la última subida cerca de la última. Un soplido o un ruido de fondo bajito no cuentan como voz."""
    f = S.proc / f"{nombre}.mp4"
    if not f.exists():
        return None
    fin = dur_audio(f) if fin is None else fin
    pal = [w for ln in leer_subs(nombre)["lineas"] for w in ln.get("palabras", [])
           if ini <= (w["s"] + w["e"]) / 2 < fin]  # cada palabra va en el tramo donde cae su mitad
    if not pal:
        return None
    a, b = max(ini, pal[0]["s"]), min(fin, pal[-1]["e"])
    try:
        o = onda(nombre)
    except Exception:  # noqa: BLE001 — sin onda, quedan los bordes de la transcripción
        return a, b
    picos, ps = o.get("picos") or [], o.get("por_seg") or 50
    if not picos:
        return a, b
    cuadro = lambda t: max(0, min(len(picos) - 1, int(t * ps)))
    # inicio: el primer golpe fuerte desde un poco antes de la primera palabra, sin pasarse de esa palabra;
    # luego se retrocede mientras siga sonando (la «s» o la «f» del arranque son bajitas)
    k0, k1 = cuadro(max(ini, a - 0.3)), cuadro(min(fin, pal[0]["e"]))
    golpe = next((k for k in range(k0, k1 + 1) if picos[k] >= 0.3), None)
    if golpe is not None:
        g0 = golpe
        while golpe > k0 and picos[golpe - 1] >= 0.08 and g0 - golpe < 0.15 * ps:
            golpe -= 1
        a = max(a, golpe / ps)
    # final: lo último que suena claro cerca del final de la última palabra (la cola de ruido no cuenta)
    k0, k1 = cuadro(max(a, pal[-1]["s"])), cuadro(min(fin, pal[-1]["e"] + 0.15))
    ultimo = next((k for k in range(k1, k0 - 1, -1) if picos[k] >= 0.15), None)
    if ultimo is not None:
        b = min(b + 0.15, (ultimo + 1) / ps)
    return (a, b) if b > a else None


def _con_silencios_fuera(combo, v):
    """Si un bloque no tiene recorte puesto a mano, se le corta el silencio del inicio y del final (según dónde
    empieza y termina la voz de verdad, más un margen). En un cuerpo grabado en partes se recorta cada parte:
    así no queda un hueco mudo donde se pega una toma con la siguiente, y ningún video arranca ni termina mudo."""
    aj = ajustes_silencios()
    if not aj["auto"]:
        return v
    m = max(0.0, min(0.5, float(aj["margen"])))
    r = dict(v.get("recortes") or {})

    for clave in ("hook", "cta"):
        f = S.proc / f"{combo[clave]}.mp4"
        voz = bordes_de_voz(combo[clave]) if not r.get(clave) else None
        if not voz:
            continue
        dur = dur_audio(f)
        a = voz[0] - m if voz[0] - m > 0.06 else 0.0
        b = voz[1] + m * 2 if voz[1] + m * 2 < dur - 0.06 else dur
        if a > 0 or b < dur:
            r[clave] = [round(a, 2), round(b, 2)]
    pr = dict(r.get("partes") or {})
    for p in partes_de(combo["cuerpo"]):
        if str(p["i"]) in pr or p["i"] in (v.get("quitar") or []):
            continue
        voz = bordes_de_voz(combo["cuerpo"], p["ini"], p["fin"])
        if not voz:
            continue
        largo = p["fin"] - p["ini"]
        a = voz[0] - m - p["ini"]
        b = voz[1] + m * 2 - p["ini"]
        a = round(a, 2) if a > 0.06 else 0.0
        b = round(b, 2) if b < largo - 0.06 else round(largo, 2)
        if a > 0 or b < largo:
            pr[str(p["i"])] = [a, b]
    if pr:
        r["partes"] = pr
    return {**v, "recortes": r}


def edicion_de(combo):
    """Recortes, frases quitadas y zoom que valen para este video: los de su versión, o los de sus piezas.
    Los bloques sin recorte puesto a mano empiezan justo antes de que se hable (ver _con_silencios_fuera)."""
    return _con_ritmo(combo, _con_zoom_alterno(combo, _con_silencios_fuera(combo, _sin_tropiezos(combo, _sin_claqueta(combo, _edicion_guardada(combo))))))


def silabas(w):
    """Sílabas de una palabra, contando los grupos de vocales (alcanza para medir qué tan rápido se habla)."""
    w = unicodedata.normalize("NFC", str(w).lower())
    if re.search(r"\d", w) and not re.search(r"[a-záéíóúüñ]", w):
        return 2  # «3», «6»: tres, seis
    return len(re.findall(r"[aeiouáéíóúü]+", w)) if re.search(r"[a-záéíóúüñ]", w) else 0


def frases_lentas(nombre, desde=6.0, objetivo=6.5, maximo=1.12, pausa=0.3):
    """Frases de una pieza que se dicen lento (menos de `desde` sílabas por segundo, sin contar los silencios
    que ya se cortan): [(a, b, factor)] en s de la pieza, con el factor que las lleva a `objetivo` (tope `maximo`).
    Se mide frase por frase (y parte por parte en un cuerpo grabado en partes): así una toma larga donde solo
    una frase se arrastra («las bacterias buenas… de su panza») no acelera todo lo demás."""
    lineas = leer_subs(nombre)["lineas"]
    pal = [w for ln in lineas for w in ln["palabras"]]
    if not pal:
        return []
    cortes = [p["fin"] for p in partes_de(nombre)][:-1] if tipo_de(nombre) == "cuerpo" else []
    grupos, actual = [], []
    for k, w in enumerate(pal):
        actual.append(w)
        fin_parte = k + 1 < len(pal) and any(w["s"] < c <= pal[k + 1]["s"] + 0.05 for c in cortes)
        if w["w"].strip()[-1:] in ".?!" or fin_parte or k == len(pal) - 1:
            grupos.append(actual)
            actual = []
    try:
        o = onda(nombre)
        picos, ps = o.get("picos") or [], o.get("por_seg") or 50
    except Exception:  # noqa: BLE001
        picos, ps = [], 50
    out = []
    for g in grupos:
        sil = sum(silabas(w["w"]) for w in g)
        if sil < 6:
            continue  # frases muy cortas: no se mide (dos palabras dichas con fuerza no son «lentas»)
        voz = bordes_de_voz(nombre, max(0.0, g[0]["s"] - 0.05), g[-1]["e"] + 0.05) or (g[0]["s"], g[-1]["e"])
        a, b = voz
        quieto = 0.0
        if picos:
            # sin pasarse del final de la onda: una frase que termina después (el subtítulo llega hasta el último
            # cuadro, o la pieza se volvió a preparar más corta) hacía fallar «list index out of range» y con eso
            # todos los videos de ese cuerpo (en P05, 154 de 232)
            x, y = max(0, int(a * ps)), min(len(picos), int(b * ps))
            while x < y:
                z = x
                while z < y and (picos[z] < 0.06) == (picos[x] < 0.06):
                    z += 1
                if picos[x] < 0.06 and (z - x) / ps >= pausa:
                    quieto += (z - x) / ps
                x = z
        t = b - a - quieto
        if t <= 0.3:
            continue
        rapidez = sil / t
        if rapidez < desde:
            f = round(min(maximo, objetivo / rapidez), 2)
            if f >= 1.04:
                out.append((round(a, 2), round(b, 2), f))
    return out


def ajustes_ritmo(combo=None):
    """Ritmo de la voz: acelerar (sin cambiar el tono) las frases que se dicen lento. desde/objetivo en sílabas por
    segundo (un hook con energía va a 7–8), maximo = cuánto se acelera como mucho (1.12 = 12 %)."""
    propio = (ediciones().get(letra_edicion(combo), {}).get("ritmo") or {}) if combo else {}
    return {"acelerar": True, "desde": 6.0, "objetivo": 6.5, "maximo": 1.12, **CONFIG.get("ritmo", {}), **propio}


def _con_ritmo(combo, v):
    """Acelera un poco las frases lentas de cada bloque, salvo donde se puso una velocidad a mano."""
    aj = ajustes_ritmo(combo)
    if not aj["acelerar"]:
        return v
    vel = dict(v.get("velocidad") or {})
    pausa = float(ajustes_cortes_voz()["pausa"])
    tramos = {}
    for clave in ("hook", "cuerpo", "cta"):
        if vel.get(clave) or (clave == "cuerpo" and vel.get("partes")):
            continue
        try:
            lista = frases_lentas(combo[clave], float(aj["desde"]), float(aj["objetivo"]), float(aj["maximo"]), pausa)
        except Exception as e:  # noqa: BLE001 — acelerar es un detalle: si falla, el video se arma igual sin acelerar
            print(f"No se pudo medir el ritmo de {combo[clave]}: {e}{donde_fallo(e)}", flush=True)
            lista = []
        if lista:
            tramos[clave] = [list(x) for x in lista]
    return {**v, "velocidad": {**vel, "tramos": tramos}} if tramos else v


_CUENTA = [{"3", "tres"}, {"2", "dos"}, {"1", "uno", "una"}]
_YA = {"pa", "va", "ya", "accion", "vamos", "dale", "go", "pum"}


def claquetas(nombre):
    """Lo que se dice para arrancar una toma y no debe salir en el video: la cuenta «3, 2, 1, va» (y lo que se dijo
    antes, como el nombre de la toma) y los reinicios cortos en medio («una, pa»). [(desde, hasta)] en s de la pieza."""
    pal = [w for ln in leer_subs(nombre)["lineas"] for w in ln["palabras"]]
    k = [norm(w["w"]) for w in pal]
    out = []
    j = 0
    while j < len(pal):
        cuenta = j + 2 < len(pal) and all(k[j + n] in _CUENTA[n] for n in range(3))
        corto = not cuenta and k[j] in {"1", "uno", "una"} and j + 1 < len(pal) and k[j + 1] in _YA
        if cuenta or corto:
            fin = j + (2 if cuenta else 1)
            if fin + 1 < len(pal) and k[fin + 1] in _YA:
                fin += 1
            sigue = pal[fin + 1]["s"] if fin + 1 < len(pal) else pal[fin]["e"]
            desde = 0.0 if cuenta and pal[j]["s"] < 12 else max(0.0, pal[j]["s"] - 0.05)  # al inicio, todo lo de antes también
            out.append((round(desde, 2), round(max(desde + 0.05, sigue - 0.08), 2)))
            j = fin + 1
            continue
        j += 1
    return out + _sueltos(nombre, pal, out)


# Palabras que se agregan a propósito al inicio de un hook (no son «sueltas» aunque vayan seguidas de una pausa)
SALUDOS = {"medico", "medica", "veterinario", "veterinaria", "doctor", "doctora", "doc", "estimado", "estimada"}


def _sueltos(nombre, pal, ya):
    """Lo suelto antes de que empiece a hablar de verdad (y después de que termina), en cada frase grabada:
    una o dos palabras cortas seguidas de un silencio («ya», «carajo», «listo», «a ver») que no son del guion.
    Hablar de verdad = palabras seguidas, en oración. [(desde, hasta)] en s de la pieza."""
    if not pal or not CONFIG.get("quitar_sueltos", True):
        return []
    guion = {norm(x) for x in info_pieza(nombre).get("texto", "").split()} - {""}
    vale = lambda ws: any(len(norm(w["w"])) >= 3 and (norm(w["w"]) in guion or norm(w["w"]) in SALUDOS) for w in ws)
    # cada clip grabado por separado (las duraciones de la preparación); un hook de un solo clip es un solo tramo
    try:
        durs = json.loads((S.proc / f"{nombre}.partes.json").read_text(encoding="utf-8")).get("duraciones") or []
    except (FileNotFoundError, ValueError, AttributeError, OSError):
        durs = []
    tramos, t = [], 0.0
    for d in durs:
        tramos.append((t, t + d))
        t += d
    if not tramos:
        tramos = [(0.0, pal[-1]["e"] + 1)]
    tramos[-1] = (tramos[-1][0], max(tramos[-1][1], pal[-1]["e"] + 1))
    ocupado = lambda a, b: any(x < b and y > a for x, y in ya)
    out = []
    for ini, fin in tramos:
        ws = [w for w in pal if ini <= (w["s"] + w["e"]) / 2 < fin]
        if len(ws) < 3:
            continue
        # tiras de palabras seguidas (una pausa de 0,35 s o más separa una tira de la otra)
        tiras = [[ws[0]]]
        for a, b in zip(ws, ws[1:]):
            (tiras.append([b]) if b["s"] - a["e"] >= 0.35 else tiras[-1].append(b))
        if len(tiras) < 2:
            continue
        primera, segunda = tiras[0], tiras[1]
        if len(primera) <= 2 and primera[-1]["e"] - primera[0]["s"] <= 0.9 and not vale(primera):
            desde = 0.0 if ini == 0.0 else max(ini, primera[0]["s"] - 0.05)
            hasta = round(max(desde + 0.05, segunda[0]["s"] - 0.08), 2)
            if not ocupado(desde, hasta):
                out.append((round(desde, 2), hasta))
        ultima, penultima = tiras[-1], tiras[-2]
        if len(tiras) >= 2 and len(ultima) <= 2 and ultima[0]["s"] - penultima[-1]["e"] >= 0.5 and not vale(ultima):
            desde, hasta = round(penultima[-1]["e"] + 0.08, 2), round(min(fin, ultima[-1]["e"] + 0.05), 2)
            if hasta > desde and not ocupado(desde, hasta):
                out.append((desde, hasta))
    # una grosería se va siempre, esté donde esté (el resto de la frase se queda)
    for w in pal:
        if norm(w["w"]) in GROSERIAS:
            desde, hasta = round(max(0.0, w["s"] - 0.03), 2), round(w["e"] + 0.05, 2)
            if not ocupado(desde, hasta) and not any(x <= desde and hasta <= y for x, y in out):
                out.append((desde, hasta))
    return out


# Groserías e insultos que se escapan al grabar (sin tildes ni signos, como los deja norm)
GROSERIAS = {"carajo", "carajos", "mierda", "mierdas", "puta", "putas", "puto", "putamadre", "chucha", "chuchasumadre",
             "conchatumadre", "conchasumadre", "concha", "ctm", "csm", "huevon", "huevona", "webon", "weon", "cojudo",
             "cojuda", "cojudez", "joder", "jodido", "pendejo", "pendeja", "cabron", "maldicion", "chuta", "verga", "pinche"}


def _sin_claqueta(combo, v):
    """Quita la claqueta de cada pieza (si no se recortó a mano): al inicio, recorte; en medio, tramo quitado."""
    if not CONFIG.get("quitar_claqueta", True):
        return v
    r, qr = dict(v.get("recortes") or {}), {k: list(x) for k, x in (v.get("quitar_rangos") or {}).items()}
    cambio = False
    for clave, nombre in (("hook", combo["hook"]), ("cuerpo", combo["cuerpo"]), ("cta", combo["cta"])):
        cl = claquetas(nombre)
        if not cl:
            continue
        f = S.proc / f"{nombre}.mp4"
        dur = dur_audio(f) if f.exists() else None
        for a, b in cl:
            if a == 0.0:  # al inicio: el clip arranca después de la cuenta
                if clave == "cuerpo":
                    partes = partes_de(nombre)
                    pr = dict(r.get("partes") or {})
                    p0 = next((p for p in partes if p["ini"] <= b < p["fin"]), partes[0] if partes else None)
                    if p0 and str(p0["i"]) not in pr:
                        pr[str(p0["i"])] = [round(b - p0["ini"], 2), round(p0["fin"] - p0["ini"], 2)]
                        quitar_antes = [p["i"] for p in partes if p["fin"] <= b]
                        r["partes"] = pr
                        v = {**v, "quitar": sorted(set(v.get("quitar") or []) | set(quitar_antes))}
                        cambio = True
                elif not r.get(clave) and dur:
                    r[clave] = [b, round(dur, 2)]
                    cambio = True
            else:
                qr.setdefault(clave, []).append([a, b])
                cortes = {k: list(x) for k, x in (v.get("cortes") or {}).items()}  # con sus cortes: así se ve en el editor
                cortes[clave] = sorted({*cortes.get(clave, []), a, b})
                v = {**v, "cortes": cortes}
                cambio = True
    return {**v, "recortes": r, "quitar_rangos": qr} if cambio else v


# ---------- Cortes por voz: lo que se dijo mal o sobra, sacado de las palabras y de la onda de la voz ----------
# Como «editar por texto» de CapCut: la sala lee cada palabra con su tiempo y la onda del audio, y propone cortar
# las tomas repetidas, los tartamudeos, los «eh», las tomas que se cortaron con «perdón, otra vez» y los silencios
# largos. Lo seguro se aplica solo (en las piezas que nadie revisó todavía en el editor); lo dudoso queda sugerido.

MULETILLAS = {"eh", "ehh", "ehhh", "ehm", "em", "emm", "mm", "mmm", "mmmm", "hmm", "hm", "ah", "aah", "ahh", "uh", "um", "umm"}
# palabras cortas que se dicen dos veces a propósito o que empiezan otra palabra sin ser un tartamudeo
_ENFASIS = {"no", "si", "muy", "ya", "mas", "nunca", "mucho", "bien", "claro", "dale", "listo", "nada", "todo", "super"}
_CORTAS = {"la", "el", "lo", "los", "las", "de", "del", "que", "en", "un", "una", "se", "su", "sus", "mi", "mis", "tu", "tus",
           "es", "con", "por", "al", "a", "y", "o", "e", "u", "no", "si", "le", "les", "me", "te", "ya", "muy", "mas", "para",
           "pero", "hay", "va", "son", "sin", "ni", "nos", "fue", "ser", "ve", "da", "dar", "doc"}
# lo que se dice al equivocarse antes de volver a empezar la frase
_RETOMAR = [("perdon",), ("perdona",), ("perdoname",), ("otra", "vez"), ("de", "nuevo"), ("repito",), ("me", "equivoque"),
            ("me", "trabe"), ("otra", "toma"), ("ya", "ya"), ("de", "nuevo", "de", "nuevo")]
TIPOS_CORTE_VOZ = {"repetido": "Lo repitió: queda la última vez", "tartamudeo": "Tartamudeo",
                   "muletilla": "Muletilla («eh», «mmm»)", "equivoco": "Se equivocó y volvió a empezar",
                   "pausa": "Silencio largo", "ruido": "Se oye algo sin palabras (¿un «eh» o un tropiezo?)",
                   "toma_sin_texto": "Lo dijo dos veces y la transcripción solo escribió la última: queda la última",
                   "de_mas": "No está en el guion"}


def ajustes_cortes_voz():
    """auto: aplicar solos los cortes seguros · pausa: desde cuántos segundos de silencio se corta · respiro: lo que
    se deja a cada lado del corte de un silencio, para que no suene pegado."""
    return {"auto": True, "pausa": 0.3, "respiro": 0.08, **CONFIG.get("cortes_voz", {})}


def _cue_en(k, j):
    for cue in _RETOMAR:
        if tuple(k[j:j + len(cue)]) == cue:
            return len(cue)
    return 0


def analizar_voz(palabras, picos=(), por_seg=50, dur=None, guion=None, pausa=0.5, respiro=0.12):
    """Lo que conviene cortar según lo que se dijo: [{a, b, tipo, motivo, texto, seguro}] en segundos del clip.
    palabras = [{w, s, e}] (de la transcripción) · picos = onda de la voz 0–1 · guion = texto que debía decirse."""
    pal = [w for w in palabras if w.get("w", "").strip()]
    k = [norm(w["w"]) for w in pal]
    n = len(pal)
    dur = dur or (pal[-1]["e"] if pal else 0)
    out = []

    def poner(a, b, tipo, i0=None, i1=None, seguro=True):
        a, b = max(0.0, a), min(dur, b)
        if b - a < 0.08:
            return
        texto = " ".join(pal[x]["w"] for x in range(i0, i1)) if i0 is not None else ""
        out.append({"a": round(a, 3), "b": round(b, 3), "tipo": tipo, "motivo": TIPOS_CORTE_VOZ[tipo], "texto": texto,
                    "seguro": seguro})

    antes = lambda i: pal[i - 1]["e"] if i > 0 else 0.0                     # dónde termina lo de antes
    sigue = lambda i: pal[i + 1]["s"] if i + 1 < n else min(dur, pal[i]["e"] + 0.15)  # dónde empieza lo que sigue

    def hueco_antes(i):
        return pal[i]["s"] - antes(i) if i > 0 else 9.0

    # dónde empieza el corte de la palabra i y dónde termina para que la palabra j suene entera: con pausa al lado,
    # un poquito antes; si van pegadas (se habla de corrido), justo en el borde que marca la transcripción
    def desde(i):
        if i == 0:
            return max(0.0, pal[0]["s"] - 0.03)
        return max(antes(i) + 0.02, pal[i]["s"] - 0.03) if hueco_antes(i) > 0.06 else pal[i]["s"]

    def hasta(j):
        return pal[j]["s"] - 0.03 if hueco_antes(j) > 0.06 else pal[j]["s"]

    # 1 · Tomas repetidas: «tu perro se ra-, tu perro se rasca» → queda la última vez que lo dijo
    i = 0
    while i < n:
        hallado = None
        for largo in range(1, 16):
            j = i + largo
            if j >= n or pal[j]["s"] - pal[i]["s"] > 9:
                break
            iguales = 0
            while iguales < largo and j + iguales < n and k[i + iguales] == k[j + iguales]:
                iguales += 1
            if largo == 1:
                ok = iguales == 1 and len(k[i]) >= 2 and k[i] not in _ENFASIS
            else:
                ultima, otra = k[j - 1], k[j + largo - 1] if j + largo - 1 < n else ""
                trunca = bool(otra) and len(ultima) < len(otra) and otra.startswith(ultima)
                ok = iguales == largo or (iguales == largo - 1 and (iguales >= 3 or trunca))
            if ok:
                hallado = j
                break
        if hallado:
            poner(desde(i), hasta(hallado), "repetido", i, hallado)
            i = hallado
            continue
        i += 1

    # 1b · Frase entera dicha otra vez con otras palabras: «Panza tranquila, mejor digestión y mejores popos.
    # Panza tranquila y mejor digestión.» Pasa cuando una toma suelta repite el final de una toma larga.
    # Si dos frases seguidas empiezan igual y casi todo lo de la más corta está en la otra, queda la última.
    import difflib
    frases, ini = [], 0
    for x in range(n):
        if pal[x]["w"].strip()[-1:] in ".?!" or x == n - 1:
            frases.append((ini, x + 1))
            ini = x + 1
    for (a0, a1), (b0, b1) in zip(frases, frases[1:]):
        fa, fb = k[a0:a1], k[b0:b1]
        corta = min(len(fa), len(fb))
        if corta < 3 or fa[:2] != fb[:2] or pal[b0]["s"] - pal[a0]["s"] > 15:
            continue
        iguales = sum(blq.size for blq in difflib.SequenceMatcher(None, fa, fb, autojunk=False).get_matching_blocks())
        if iguales >= 0.75 * corta:
            poner(desde(a0), hasta(b0), "repetido", a0, b0)

    for i in range(n):
        # 2 · Tartamudeo: «pe- perro», «su- suplevet»
        if i + 1 < n:
            crudo = pal[i]["w"].strip()
            corto = len(k[i]) <= 3 and k[i] not in _CORTAS
            if k[i] and k[i + 1] != k[i] and k[i + 1].startswith(k[i]) and pal[i + 1]["s"] - pal[i]["e"] < 0.8 \
                    and (crudo.endswith(("-", "…", "...")) or corto):
                poner(desde(i), hasta(i + 1), "tartamudeo", i, i + 1)
        # 3 · Muletillas: «eh», «mmm»; «este» solo si va suelto entre pausas
        suelto = hueco_antes(i) >= 0.25 and (sigue(i) - pal[i]["e"]) >= 0.25
        if k[i] in MULETILLAS or (k[i] == "este" and suelto):
            poner(desde(i), hasta(i + 1) if i + 1 < n else pal[i]["e"] + 0.05, "muletilla", i, i + 1)
        # 4 · «Perdón, otra vez»: se corta lo que se intentó decir (desde el inicio de esa frase) y el aviso
        largo = _cue_en(k, i)
        if largo:
            fin = i + largo - 1
            despues = sigue(fin) - pal[fin]["e"]
            if hueco_antes(i) >= 0.3 or despues >= 0.4 or fin + 1 >= n:
                ini = i
                while ini > 0 and pal[i]["s"] - pal[ini - 1]["s"] < 10 and hueco_antes(ini) < 0.5 \
                        and pal[ini - 1]["w"].strip()[-1:] not in ".?!":
                    ini -= 1
                poner(desde(ini), hasta(fin + 1) if fin + 1 < n else pal[fin]["e"] + 0.1, "equivoco", ini, fin + 1)

    # 5 · Lo que se dijo de más contra el guion (si hay): solo sugerido, porque la transcripción también se equivoca
    if guion and n:
        import difflib
        g = [x for x in (norm(w) for w in str(guion).split()) if x]
        if len(g) >= 4:
            sm = difflib.SequenceMatcher(None, k, g, autojunk=False)
            if sm.ratio() > 0.6:
                for op, a0, a1, b0, b1 in sm.get_opcodes():
                    if op != "delete" or a1 - a0 > 3:  # «delete» = palabras dichas que el guion no tiene
                        continue
                    if a0 == 0 or a1 >= n:  # al inicio o al final ya se recorta lo que sobra
                        continue
                    poner(desde(a0), hasta(a1), "de_mas", a0, a1, seguro=False)

    # 6 · Silencios largos (con la onda) y voz sin palabras (un «eh» que la transcripción no escribió)
    if picos and n:
        quieto = [p < 0.06 for p in picos]
        habla0, habla1 = pal[0]["s"], pal[-1]["e"]
        # una palabra que la transcripción estira sobre el silencio («Panza» de 8,5 s cuando la voz entra en 8,9 s)
        # no salva la pausa: solo una palabra entera dentro del silencio (algo dicho muy bajito)
        enteras = [(w["s"], w["e"]) for w in pal]
        x = 0
        while x < len(quieto):
            y = x
            while y < len(quieto) and quieto[y] == quieto[x]:
                y += 1
            a, b = x / por_seg, y / por_seg
            if a > habla0 + 0.05 and b < habla1 - 0.05:  # solo en medio: los bordes ya se recortan solos
                if quieto[x] and b - a >= pausa and not any(a + 0.05 < ws and we < b - 0.05 for ws, we in enteras):
                    poner(a + respiro, b - respiro, "pausa")
                elif not quieto[x] and b - a >= 0.3:
                    tapa = sum(max(0.0, min(b, w["e"]) - max(a, w["s"])) for w in pal)
                    if tapa < 0.05:
                        # Whisper no escribe una frase dicha dos veces seguidas: si es voz larga, después de una pausa,
                        # y la frase escrita empieza enseguida, es la primera toma de esa frase → se corta y queda la última
                        prev = max((w["e"] for w in pal if w["e"] <= a + 0.05), default=0.0)
                        prox = min((w["s"] for w in pal if w["s"] >= b - 0.05), default=None)
                        if b - a >= 0.35 and a - prev >= 0.12 and prox is not None and prox - b <= 0.45:
                            poner(a - 0.03, max(b + 0.03, prox - 0.03), "toma_sin_texto")
                        else:
                            poner(a - 0.03, b + 0.03, "ruido", seguro=False)
            x = y

    # se juntan los que se pisan (gana el más largo; si uno es seguro, el junto también)
    out.sort(key=lambda c: c["a"])
    juntos = []
    for c in out:
        if juntos and c["a"] <= juntos[-1]["b"] + 0.03:
            u = juntos[-1]
            mayor = c if c["b"] - c["a"] > u["b"] - u["a"] else u
            juntos[-1] = {**mayor, "a": min(u["a"], c["a"]), "b": max(u["b"], c["b"]), "seguro": u["seguro"] or c["seguro"],
                          "texto": u["texto"] + (" " + c["texto"] if c["texto"] and c["texto"] not in u["texto"] else "")}
        else:
            juntos.append(dict(c))
    return juntos


_VOZ_CACHE = {}


def cortes_por_voz(nombre):
    """Los cortes por voz de una pieza procesada (en caché mientras no cambien su audio, su texto ni los ajustes)."""
    f, sub = S.proc / f"{nombre}.mp4", S.subs / f"{nombre}.json"
    if not f.exists() or not sub.exists():
        return []
    aj = ajustes_cortes_voz()
    texto = (info_pieza(nombre) or {}).get("texto") or ""
    clave = (str(f), f.stat().st_mtime, sub.stat().st_mtime, aj["pausa"], aj["respiro"], hash(texto))
    if _VOZ_CACHE.get(nombre, (None,))[0] == clave:
        return _VOZ_CACHE[nombre][1]
    pal = [w for ln in leer_subs(nombre)["lineas"] for w in ln.get("palabras", [])]
    try:
        o = onda(nombre)
    except Exception:
        o = {"picos": [], "por_seg": 50}
    claq = claquetas(nombre)
    res = [c for c in analizar_voz(pal, o.get("picos") or [], o.get("por_seg") or 50, dur_audio(f), texto,
                                   float(aj["pausa"]), float(aj["respiro"]))
           if not any(c["a"] < qb and c["b"] > qa for qa, qb in claq)]  # la claqueta ya se quita aparte
    _VOZ_CACHE[nombre] = (clave, res)
    return res


def _sin_tropiezos(combo, v):
    """Aplica solos los cortes por voz seguros en las piezas que todavía nadie revisó en el editor.
    Al guardar desde el editor, la pieza queda «revisada» y manda lo que dejó ahí la persona."""
    if not ajustes_cortes_voz()["auto"]:
        return v
    revisada = v.get("voz_revisada") or {}
    cortes = {k: list(x) for k, x in (v.get("cortes") or {}).items()}
    qr = {k: [list(r) for r in x] for k, x in (v.get("quitar_rangos") or {}).items()}
    cambio = False
    for clave in ("hook", "cuerpo", "cta"):
        if revisada.get(clave):
            continue
        for c in cortes_por_voz(combo[clave]):
            if not c["seguro"] or any(c["a"] < b and c["b"] > a for a, b in qr.get(clave, [])):
                continue
            qr.setdefault(clave, []).append([c["a"], c["b"]])
            cortes[clave] = sorted({*cortes.get(clave, []), c["a"], c["b"]})
            cambio = True
    return {**v, "cortes": cortes, "quitar_rangos": {k: sorted(x) for k, x in qr.items()}} if cambio else v


ESTILO_DEFECTO = {
    "zoom_alterno": False,
    "zoom_tipo": "punch",   # punch (salto) | in (acercar despacio) | out (alejar despacio) | mezcla (los tres, por turnos)
    "zoom_fuerza": 0.14,    # cuánto se acerca: 0.08 suave · 0.14 media · 0.22 fuerte
    "zoom_cada": 2,         # 2 = frase sí, frase no · 1 = en todas las frases
    "zoom_hook": False,     # acercamiento lento durante todo el hook
    "zoom_hook_fuerza": 0.10,
    "fundido": 0.12,        # segundos de entrada y salida de imágenes y clips (0.12 = casi de golpe)
    "limpieza_voz": "suave",  # apagada | suave | media | fuerte (quita ruido de fondo y deja la voz pareja)
    "seguir_cara": True,    # el zoom se centra en la cara aunque la persona se mueva
    "lut": "",              # LUT de color (.cube) subido en Estilo de edición; vacío = sin LUT
    "lut_fuerza": 1.0,      # 0.3 suave · 0.6 media · 1 completa
    "efecto": "ninguno",    # ninguno | vineta | grano | vineta_grano
    "transicion": "aleatoria",  # ninguna | aleatoria | flash | zoom | sacudida | glitch | desenfoque | barrido | negro
    "transicion_donde": "frases",  # cta (solo al entrar el CTA) | piezas (hook→cuerpo y cuerpo→CTA) | frases (también entre frases)
}
LIMPIEZAS_VOZ = {  # filtros de ffmpeg sobre la voz ya cortada; al final se vuelve a igualar el volumen
    "suave": "highpass=f=80,afftdn=nr=10:nf=-42:tn=1,deesser=i=0.3",
    "media": "highpass=f=80,afftdn=nr=14:nf=-38:tn=1,deesser=i=0.4,acompressor=threshold=-20dB:ratio=3:attack=5:release=80",
    "fuerte": "highpass=f=90,afftdn=nr=24:nf=-32:tn=1,deesser=i=0.5,equalizer=f=3000:t=q:w=1:g=2,"
              "acompressor=threshold=-22dB:ratio=4:attack=5:release=80",
}
EFECTOS_VIDEO = {"vineta": "vignette=angle=PI/5", "grano": "noise=alls=6:allf=t", "vineta_grano": "vignette=angle=PI/5,noise=alls=6:allf=t"}
TRANSICIONES = ("ninguna", "aleatoria", "flash", "zoom", "sacudida", "glitch", "desenfoque", "barrido", "negro")
# las que entran en «aleatoria» (el CTA siempre entra con destello, como foto) y el sonido que va con cada una
NOMBRES_TRANSICION = {"flash": "destello", "zoom": "zoom", "sacudida": "sacudida", "glitch": "glitch",
                      "desenfoque": "desenfoque", "barrido": "barrido", "negro": "parpadeo a negro"}
TRANS_AZAR = ("zoom", "barrido", "flash", "glitch", "sacudida", "desenfoque", "negro")
SONIDO_TRANSICION = {"flash": "camara", "zoom": "zoom", "sacudida": "impacto", "glitch": "glitch", "desenfoque": "whoosh",
                     "barrido": "whoosh", "negro": "whoosh_inverso"}
DONDE_TRANSICION = ("cta", "piezas", "frases")


def estilo_edicion(combo=None):
    """Estilo de edición (Plantillas → Estilo de edición); en una edición B, C…, con lo que cambia esa edición."""
    propio = (ediciones().get(letra_edicion(combo), {}).get("estilo") or {}) if combo else {}
    base = dict(ESTILO_DEFECTO)
    if not CONFIG["sonidos"].get("flash_transicion_cta", True):  # quien apagó el destello del CTA no lo ve volver
        base["transicion"] = "ninguna"
    return {**base, **CONFIG.get("estilo_edicion", {}), **propio}


# ---------- LUT de color ----------
LUTS = DATOS / "luts"  # los .cube que sube el equipo (Estilo de edición → Color)
EXT_LUT = {".cube", ".3dl"}


def luts():
    return sorted(f.name for f in LUTS.glob("*") if f.suffix.lower() in EXT_LUT) if LUTS.exists() else []


def ruta_lut(nombre):
    f = LUTS / Path(str(nombre)).name
    return f if nombre and f.exists() and f.suffix.lower() in EXT_LUT else None


def guardar_lut(nombre, datos):
    nombre = re.sub(r"[^\w.\- ]", "_", Path(nombre).name).strip() or "lut.cube"
    if Path(nombre).suffix.lower() not in EXT_LUT:
        raise ValueError("El LUT tiene que ser un archivo .cube o .3dl")
    if b"LUT_3D_SIZE" not in datos[:20000] and Path(nombre).suffix.lower() == ".cube":
        raise ValueError("Ese .cube no parece un LUT 3D (le falta LUT_3D_SIZE)")
    # .cube y .3dl son texto con una tabla de colores (tres números por línea): antes un .3dl cualquiera pasaba
    num = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    filas = sum(1 for l in datos[:8_000_000].decode("utf-8", "replace").splitlines() if re.fullmatch(rf"\s*{num}\s+{num}\s+{num}\s*", l))
    if filas < 8:
        raise ValueError(f"Ese {Path(nombre).suffix.lower()} no parece un LUT: no trae la tabla de colores")
    LUTS.mkdir(parents=True, exist_ok=True)
    (LUTS / nombre).write_bytes(datos)
    return nombre


def filtros_color(combo, trabajo, entrada, salida):
    """LUT (con su fuerza) y efecto de imagen (viñeta, grano) del estilo: lista de filtros de [entrada] a [salida].
    El LUT se copia a la carpeta de trabajo como lut.cube para no pelear con las rutas de Windows dentro del filtro."""
    est = estilo_edicion(combo)
    f, previo = [], entrada
    lut = ruta_lut(est.get("lut"))
    if lut:
        local = f"lut{lut.suffix.lower()}"
        shutil.copyfile(lut, trabajo / local)
        fuerza = max(0.0, min(1.0, float(est.get("lut_fuerza", 1.0))))
        if fuerza >= 0.99:
            f.append(f"[{previo}]lut3d=file={local}[lut_o]")
        else:  # mezcla del original con el LUT
            f.append(f"[{previo}]split[lut_a][lut_b];[lut_b]lut3d=file={local}[lut_c];[lut_c][lut_a]blend=all_mode=normal:all_opacity={fuerza:.2f}[lut_o]")
        previo = "lut_o"
    if est.get("efecto") in EFECTOS_VIDEO:
        f.append(f"[{previo}]{EFECTOS_VIDEO[est['efecto']]}[efx_o]")
        previo = "efx_o"
    if not f:
        return []
    f[-1] = f[-1][: f[-1].rindex("[")] + f"[{salida}]"
    return f


# ---------- Seguir la cara (para que el zoom se centre en ella) ----------
MODELO_CARA = BIBLIO_BASE / "modelos" / "cara-ultraface-320.onnx"  # UltraFace RFB-320 (MIT), corre con onnxruntime
_SESION_CARA = {}
_CERROJO_CARA = _hilos.Lock()


def _detector_cara():
    if "s" not in _SESION_CARA:
        try:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.log_severity_level = 3
            _SESION_CARA["s"] = ort.InferenceSession(str(MODELO_CARA), so, providers=["CPUExecutionProvider"]) if MODELO_CARA.exists() else None
        except Exception:
            _SESION_CARA["s"] = None
    return _SESION_CARA["s"]


def caras(nombre, por_seg=4):
    """Centro de la cara (x, y de 0 a 1) a lo largo de una pieza procesada: [(t, x, y)], suavizado y con pocos puntos.
    Se guarda junto a la pieza y se rehace si la pieza cambia. [] si no hay detector o no se ve ninguna cara."""
    with _CERROJO_CARA:
        return _caras(nombre, por_seg)


def _caras(nombre, por_seg):
    video = S.proc / f"{nombre}.mp4"
    cache = S.proc / f"{nombre}.caras.json"
    if not video.exists():
        return []
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("mtime") == video.stat().st_mtime:
            return [tuple(p) for p in d["puntos"]]
    except (FileNotFoundError, ValueError, KeyError):
        pass
    sesion = _detector_cara()
    if not sesion:
        return []
    import numpy as np
    W, H = lienzo()
    # cada cuadro entra entero en 320x240 (con barras), para no deformar la cara
    k = min(320 / W, 240 / H)
    w, h = max(2, int(W * k) // 2 * 2), max(2, int(H * k) // 2 * 2)
    px, py = (320 - w) // 2, (240 - h) // 2
    proc = subprocess.run(args=[FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(video), "-an",
                           "-vf", f"fps={por_seg},scale={w}:{h},pad=320:240:{px}:{py}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                          capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | _prioridad_baja())
    cuadros = np.frombuffer(proc.stdout, np.uint8)
    n = cuadros.size // (320 * 240 * 3)
    crudos = []
    for i in range(n):
        img = cuadros[i * 230400:(i + 1) * 230400].reshape(240, 320, 3).astype(np.float32)
        x = ((img - 127.0) / 128.0).transpose(2, 0, 1)[None]
        scores, boxes = sesion.run(None, {"input": x})
        s, b = scores[0, :, 1], boxes[0]
        ok = s > 0.7
        if not ok.any():
            crudos.append(None)
            continue
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        j = int(np.argmax(np.where(ok, s * area, -1)))  # la cara más grande y segura (la de quien habla)
        cx, cy = (b[j, 0] + b[j, 2]) / 2 * 320, (b[j, 1] + b[j, 3]) / 2 * 240
        crudos.append(((cx - px) / w, (cy - py) / h))
    vistos = [c for c in crudos if c]
    if len(vistos) < max(2, n // 5):  # casi nunca se ve una cara: mejor el encuadre de siempre
        puntos = []
    else:
        ult = vistos[0]
        llenos = []
        for c in crudos:  # donde no se vio, se queda donde estaba
            ult = c or ult
            llenos.append(ult)
        suave = []
        for i in range(len(llenos)):  # mediana de 5 (quita saltos sueltos) y después promedio
            v = llenos[max(0, i - 2):i + 3]
            suave.append((float(np.median([p[0] for p in v])), float(np.median([p[1] for p in v]))))
        suave = [(float(np.mean([p[0] for p in suave[max(0, i - 2):i + 3]])), float(np.mean([p[1] for p in suave[max(0, i - 2):i + 3]])))
                 for i in range(len(suave))]
        puntos = [(0.0, *suave[0])]
        for i in range(1, len(suave)):  # solo los puntos donde la cara se movió de verdad
            if i == len(suave) - 1 or abs(suave[i][0] - puntos[-1][1]) > 0.02 or abs(suave[i][1] - puntos[-1][2]) > 0.02:
                puntos.append((i / por_seg, *suave[i]))
        puntos = [(round(t, 2), round(min(0.9, max(0.1, x)), 3), round(min(0.85, max(0.1, y)), 3)) for t, x, y in puntos]
    cache.write_text(json.dumps({"mtime": video.stat().st_mtime, "puntos": puntos}), encoding="utf-8")
    return puntos


def _cara_en(combo, offs, t):
    """(x, y) de la cara en el segundo t del video sin recortes (números, no expresión); donde suele estar si no se sabe."""
    if estilo_edicion(combo).get("seguir_cara", True):
        for nombre, o, fin in zip((combo["hook"], combo["cuerpo"], combo["cta"]), offs, [*offs[1:], float("inf")]):
            if o <= t < fin and (ps := caras(nombre)):
                tp = t - o
                for p, q in zip(ps, ps[1:]):
                    if p[0] <= tp < q[0]:
                        k = (tp - p[0]) / max(1e-6, q[0] - p[0])
                        return p[1] + (q[1] - p[1]) * k, p[2] + (q[2] - p[2]) * k
                return ps[-1][1], ps[-1][2]
    return 0.5, 0.38


def _expresion_cara(combo, offs, eje):
    """Expresión (según it, el tiempo del video sin recortes) del centro de la cara en un eje (1 = x, 2 = y).
    None si no hay que seguirla o no se encontró."""
    if not estilo_edicion(combo).get("seguir_cara", True):
        return None
    defecto = "0.5" if eje == 1 else "0.38"
    tramos = []  # (t0, t1, v0, v1) en el video sin recortes
    hay = False
    for nombre, o in zip((combo["hook"], combo["cuerpo"], combo["cta"]), offs):
        try:
            ps = caras(nombre)
        except Exception:
            ps = []
        if not ps:
            continue
        hay = True
        for p, q in zip(ps, ps[1:] + [(ps[-1][0] + 3600, *ps[-1][1:])]):
            tramos.append((o + p[0], o + q[0], p[eje], q[eje]))
    if not hay:
        return None
    expr = defecto
    for t0, t1, v0, v1 in sorted(tramos, reverse=True):
        dv = v1 - v0
        val = f"{v0}" if abs(dv) < 1e-4 else f"({v0}+({dv:.4f})*(it-{t0:.2f})/{t1 - t0:.2f})"
        expr = f"if(lt(it,{t1:.2f}),{val},{expr})" if t0 <= 0.001 else f"if(between(it,{t0:.2f},{t1:.2f}),{val},{expr})"
    return expr


# ---------- Transiciones entre cortes ----------
def transiciones_de(combo, offs):
    """[(segundo, tipo)]: cada momento de transición con la que le toca. Con «aleatoria» cambia de un corte a otro
    (nunca dos iguales seguidas) y es siempre la misma para ese video; el CTA entra con destello."""
    trans = estilo_edicion(combo).get("transicion", "flash")
    ts = momentos_transicion(combo, offs)
    if trans != "aleatoria":
        return [(t, trans) for t in ts]
    ed = edicion_de(combo)
    t_cta = round(offs[2] + float(((ed.get("recortes") or {}).get("cta") or [0])[0]), 3)
    out, antes = [], None
    for k, t in enumerate(ts):
        if abs(t - t_cta) < 0.01:
            tipo = "flash"
        else:
            ops = [x for x in TRANS_AZAR if x != antes]
            tipo = ops[int(hashlib.md5(f"{combo['codigo']}|{k}".encode()).hexdigest(), 16) % len(ops)]
        out.append((t, tipo))
        antes = tipo
    return out


def _capas_transicion(trs):
    """Filtros de imagen de las transiciones [(segundo, tipo)] (destello, glitch, desenfoque, barrido, negro), en una
    lista para encadenar con comas. «zoom» y «sacudida» van en el zoompan."""
    capas_t = []
    for tipo in ("flash", "glitch", "desenfoque", "barrido", "negro"):
        momentos = [t for t, k in trs if k == tipo]
        if not momentos:
            continue
        en = lambda a, b, ms=momentos: "+".join(f"between(t,{t + a:.3f},{t + b:.3f})" for t in ms)
        if tipo == "flash":  # como foto: un destello blanco cortito donde entra la voz (con el obturador)
            capas_t.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.85:t=fill:enable='{en(0, 0.07)}',"
                           f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.4:t=fill:enable='{en(0.07, 0.14)}'")
        elif tipo == "glitch":  # los colores se separan y aparece estática un instante
            capas_t.append(f"rgbashift=rh=-18:bh=18:rv=6:enable='{en(0, 0.09)}',rgbashift=rh=10:bh=-10:enable='{en(0.09, 0.16)}',"
                           f"noise=alls=40:allf=t:enable='{en(0, 0.12)}'")
        elif tipo == "desenfoque":  # entra desenfocado y se aclara
            capas_t.append(f"gblur=sigma=24:enable='{en(0, 0.08)}',gblur=sigma=10:enable='{en(0.08, 0.16)}',"
                           f"gblur=sigma=4:enable='{en(0.16, 0.24)}'")
        elif tipo == "barrido":  # paneo rápido: la imagen se estira de lado (desenfoque solo horizontal) y se asienta
            capas_t.append(f"gblur=sigma=60:sigmaV=0.5:enable='{en(0, 0.07)}',gblur=sigma=28:sigmaV=0.5:enable='{en(0.07, 0.14)}',"
                           f"gblur=sigma=8:sigmaV=0.5:enable='{en(0.14, 0.2)}'")
        elif tipo == "negro":  # parpadeo a negro: se apaga un instante y vuelve
            capas_t.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.95:t=fill:enable='{en(0, 0.06)}',"
                           f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.5:t=fill:enable='{en(0.06, 0.13)}'")
    return capas_t


def momentos_transicion(combo, offs):
    """Segundos (del video sin recortes) donde va la transición: entrada del CTA, del cuerpo y, si se pide, de cada frase."""
    est = estilo_edicion(combo)
    if est.get("transicion", "flash") == "ninguna":
        return []
    ed = edicion_de(combo)
    t_cta = offs[2] + float(((ed.get("recortes") or {}).get("cta") or [0])[0])
    if est.get("transicion_donde", "cta") == "cta":
        return [t_cta]
    partes = {p["i"]: p for p in partes_de(combo["cuerpo"])}
    quitar = set(ed.get("quitar") or [])
    orden = [i for i in (ed.get("orden") or []) if i in partes] or sorted(partes)
    orden += [i for i in sorted(partes) if i not in orden]
    visibles = [i for i in orden if i not in quitar]
    recorte_partes = (ed.get("recortes") or {}).get("partes") or {}
    ini = lambda i: offs[1] + max(partes[i]["ini"], float((recorte_partes.get(str(i)) or [partes[i]["ini"]])[0]))
    ts = [ini(visibles[0])] if visibles else [offs[1]]
    if est.get("transicion_donde") == "frases":
        ts += [ini(i) for i in visibles[1:]]
    return sorted(set(round(t, 3) for t in ts + [t_cta]))


def _con_zoom_alterno(combo, v):
    """Estilo de edición (como edita el equipo), aplicado solo donde no hay zoom puesto a mano:
    · «Zoom alternado» en las frases del cuerpo: salto, acercar, alejar o mezcla; frase sí, frase no o en todas.
    · «Zoom en el hook»: acercamiento lento durante el hook.
    Si una pieza tiene fotogramas clave de zoom, se respeta tal cual."""
    est = estilo_edicion(combo)
    if not est["zoom_alterno"] and not est["zoom_hook"]:
        return v
    z = dict(v.get("zoom") or {})
    puntos = z.get("puntos") or {}
    if est["zoom_hook"] and not z.get("hook") and not puntos.get("hook"):
        z["hook"] = {"tipo": "in", "fuerza": float(est["zoom_hook_fuerza"])}
    if est["zoom_alterno"] and not puntos.get("cuerpo"):
        partes = dict(z.get("partes") or {})
        visibles = [p["i"] for p in partes_de(combo["cuerpo"]) if p["i"] not in (v.get("quitar") or [])]
        f, cada, tipo = float(est["zoom_fuerza"]), int(est["zoom_cada"]), est["zoom_tipo"]
        turno = 0
        for k, i in enumerate(visibles):
            if str(i) in partes or (cada == 2 and k % 2 == 0):
                continue
            t = ("punch", "in", "out")[turno % 3] if tipo == "mezcla" else tipo
            # en todas las frases con salto: se alterna la fuerza, para que cada corte se note
            fk = f / 2 if (cada == 1 and t == "punch" and k % 2 == 0) else f
            partes[str(i)] = {"tipo": t, "fuerza": round(fk, 3)}
            turno += 1
        z["partes"] = partes
    return {**v, "zoom": z}


def _edicion_guardada(combo):
    if combo.get("version"):
        return combo["version"]
    a, e = ajustes_piezas(), letra_edicion(combo)
    h, c, t = (ajustes_de_pieza(combo[k], e, a) for k in ("hook", "cuerpo", "cta"))
    por_pieza = lambda campo: {k: v for k, v in (("hook", h.get(campo)), ("cuerpo", c.get(campo)), ("cta", t.get(campo))) if v}
    return {"quitar": c.get("quitar", []), "orden": c.get("orden") or [], "cortes": por_pieza("cortes"), "quitar_rangos": por_pieza("quitar_rangos"), "capas": por_pieza("capas"),
            "voz_revisada": por_pieza("voz_revisada"), "audio_aparte": por_pieza("audio_aparte"), "duplicados": por_pieza("duplicados"),
            "velocidad": {k: v for k, v in (("hook", h.get("velocidad")), ("cta", t.get("velocidad")), ("partes", c.get("velocidad_partes"))) if v},
            "recortes": {k: v for k, v in (("hook", h.get("recorte")), ("cta", t.get("recorte")), ("partes", c.get("partes"))) if v},
            "zoom": {k: v for k, v in (("hook", h.get("zoom")), ("cta", t.get("zoom")), ("partes", c.get("zoom_partes")),
                                       ("tramos", por_pieza("zoom_tramos")), ("puntos", por_pieza("zoom_puntos"))) if v}}


def editado_en(combo):
    """Último cambio (segundos) en algo que usa este video: subtítulos, marcas o recortes de sus piezas, o sus ajustes."""
    a = {} if combo.get("version") else ajustes_piezas()  # una versión guarda sus propios recortes
    e = letra_edicion(combo)
    t = float(ediciones().get(e, {}).get("cambio") or 0)  # el estilo, la música o la plantilla de su edición
    for n in (combo["hook"], combo["cuerpo"], combo["cta"]):
        t = max(t, ajustes_de_pieza(n, e, a).get("editado", 0))
        f = S.subs / f"{n}.json"
        if f.exists():
            t = max(t, f.stat().st_mtime)
    # también si se cambió su plantilla de subtítulos o cuál es la predeterminada
    t = max(t, float(plantilla_sub(combo).get("editado") or 0), float(CONFIG.get("estilo_cambio") or 0),
            float(CONFIG.get("cortes_voz_cambio") or 0), float(produccion().get("formato_cambio") or 0))
    if id_plantilla_de(combo) == id_plantilla():  # usa la predeterminada: cuenta si se cambió cuál es
        t = max(t, float(CONFIG.get("plantilla_activa_cambio") or 0))
    pedida = ajustes_video(combo["codigo"]).get("plantilla")  # la plantilla que eligió para este video se borró
    if pedida:
        t = max(t, float((CONFIG.get("plantillas_borradas_cambio") or {}).get(pedida) or 0))
    cambios_son = CONFIG.get("ajustes_sonidos_cambio") or {}
    if cambios_son:  # un sonido que usa este video se editó en la página Sonidos
        try:
            rec = json.loads((S.recetas / f"{combo['codigo']}.json").read_text(encoding="utf-8"))
            t = max([t] + [float(cambios_son.get(e.get("id"), 0)) for e in rec.get("efectos", [])])
        except (FileNotFoundError, ValueError):
            pass
    return max(t, ajustes_video(combo["codigo"]).get("editado", 0))


def _curva(p, curva):
    return {"lineal": p, "entrada": f"({p})*({p})", "salida": f"(1-(1-({p}))*(1-({p})))",
            "rebote": f"(1+2.70158*pow({p}-1,3)+1.70158*pow({p}-1,2))"}.get(curva, f"({p})*({p})*(3-2*({p}))")


def _expresion_zoom(combo, offs, total):
    """Expresiones de zoompan (zoom, x, y) según el tiempo del video sin recortes (it): primero los tramos de zoom
    (fotogramas clave con punto de foco); donde no hay tramo, el zoom por bloque de siempre. None si no hay zoom."""
    z = edicion_de(combo).get("zoom") or {}
    trs = transiciones_de(combo, offs)
    empujes = [t for t, k in trs if k in ("zoom", "barrido")]  # el barrido también empuja un poco la cámara
    golpes = [t for t, k in trs if k == "sacudida"]
    if not z and not golpes and not empujes:
        return None
    base = _expresion_zoom_bloques(combo, offs, total, z) or "1"
    if empujes:  # transición «zoom»: un empujón de cámara que se suelta en 0.3 s en cada corte
        base = f"({base})+" + "+".join(f"between(it,{t:.3f},{t + 0.3:.3f})*0.16*pow(1-(it-{t:.3f})/0.3,2)" for t in empujes)
    inicio = {"hook": 0.0, "cuerpo": offs[1], "cta": offs[2]}
    fin = {"hook": offs[1], "cuerpo": offs[2], "cta": total}
    segs = []  # (t0, t1, zoom, fx, fy) con fx/fy = punto fijo (ancla)
    puntos_segs = []  # (t0, t1, zoom, cx, cy) con cx/cy = centro de lo que se ve
    for pieza, puntos in (z.get("puntos") or {}).items():
        o, tope = inicio[pieza], fin[pieza]
        ps = sorted(puntos, key=lambda k: k["t"])
        if not ps:
            continue
        primero, ultimo = ps[0], ps[-1]
        if primero["t"] > 0.02:
            puntos_segs.append((o, o + primero["t"], f"{primero['s']}", f"{primero['x']}", f"{primero['y']}"))
        for k1, k2 in zip(ps, ps[1:]):
            a, b = o + k1["t"], min(tope, o + k2["t"])
            if b - a < 0.02:
                continue
            if k1["curva"] == "golpe":  # se queda igual y salta al llegar al punto siguiente
                puntos_segs.append((a, b, f"{k1['s']}", f"{k1['x']}", f"{k1['y']}"))
                continue
            e = _curva(f"min(1,max(0,(it-{a:.3f})/{b - a:.3f}))", "lineal" if k1["curva"] == "lineal" else "suave")
            puntos_segs.append((a, b, f"({k1['s']}+({k2['s'] - k1['s']:.4f})*{e})", f"({k1['x']}+({k2['x'] - k1['x']:.4f})*{e})",
                                f"({k1['y']}+({k2['y'] - k1['y']:.4f})*{e})"))
        if tope - (o + ultimo["t"]) > 0.02:
            puntos_segs.append((o + ultimo["t"], tope, f"{ultimo['s']}", f"{ultimo['x']}", f"{ultimo['y']}"))
    for pieza, tramos in (z.get("tramos") or {}).items():
        if (z.get("puntos") or {}).get(pieza):
            continue  # en esa pieza ya mandan los puntos
        o, tope = inicio[pieza], fin[pieza]
        tramos = sorted(tramos, key=lambda t: t["a"])
        for k, t in enumerate(tramos):
            a, b = o + t["a"], min(tope, o + t["b"])
            if b - a < 0.05:
                continue
            if t["curva"] == "temblor":  # acercado fijo y el encuadre tiembla
                segs.append((a, b, f"{max(t['z1'], 1.06)}", f"({t['fx0']}+0.015*sin(it*53))", f"({t['fy0']}+0.015*cos(it*47))"))
                continue
            e = _curva(f"min(1,max(0,(it-{a:.3f})/{b - a:.3f}))", t["curva"])
            segs.append((a, b, f"({t['z0']}+({t['z1'] - t['z0']:.3f})*{e})",
                         f"({t['fx0']}+({t['fx1'] - t['fx0']:.3f})*{e})", f"({t['fy0']}+({t['fy1'] - t['fy0']:.3f})*{e})"))
            siguiente = o + tramos[k + 1]["a"] if k + 1 < len(tramos) else tope
            if t["salida"] == "queda" and siguiente - b > 0.02:
                segs.append((b, min(siguiente, tope), f"{t['z1']}", f"{t['fx1']}", f"{t['fy1']}"))
            elif t["salida"] == "suave":
                c = min(b + 0.35, siguiente, tope)
                if c - b > 0.02:
                    q = _curva(f"min(1,max(0,(it-{b:.3f})/{c - b:.3f}))", "suave")
                    segs.append((b, c, f"({t['z1']}+({1 - t['z1']:.3f})*{q})", f"{t['fx1']}", f"{t['fy1']}"))
    # el punto fijo del zoom de siempre: la cara (seguida en el tiempo) o, si no se encontró, donde suele estar
    fx, fy = _expresion_cara(combo, offs, 1) or "0.5", _expresion_cara(combo, offs, 2) or "0.38"
    if golpes:  # transición «sacudida»: la cámara tiembla 0.3 s en cada corte
        for t in golpes:  # con la cara de ese momento en número (la trayectoria entera repetida en cada corte no cabe en Windows)
            x, y = _cara_en(combo, offs, t)
            segs.append((t, t + 0.3, "1.08", f"({x:.3f}+0.03*sin(it*61))", f"({y:.3f}+0.03*cos(it*53))"))
    if not segs and not puntos_segs and base == "1":
        return None
    # Todo se lleva a «centro de lo que se ve» (cx, cy). Un punto fijo f (tramos y zoom de siempre, hacia la cara)
    # equivale a un centro f·(1−1/zoom) + 0.5/zoom.
    ancla = lambda f: f"(({f})*(1-1/zoom)+0.5/zoom)"
    zoom, cx, cy = f"({base})", ancla(fx), ancla(fy)
    todos = [(t0, t1, ze, ancla(xe), ancla(ye)) for t0, t1, ze, xe, ye in segs] + puntos_segs
    for t0, t1, ze, xe, ye in sorted(todos, key=lambda s: s[0], reverse=True):
        cond = f"between(it,{t0:.3f},{t1:.3f})"
        zoom, cx, cy = f"if({cond},{ze},{zoom})", f"if({cond},{xe},{cx})", f"if({cond},{ye},{cy})"
    return {"z": f"max(1,{zoom})", "x": f"iw*min(max({cx}-0.5/zoom,0),1-1/zoom)", "y": f"ih*min(max({cy}-0.5/zoom,0),1-1/zoom)"}


def _expresion_zoom_bloques(combo, offs, total, z):
    """Zoom por bloque entero: acercar, alejar o «punch» en el hook, una frase del cuerpo o el CTA."""
    tramos = []
    if z.get("hook"):
        tramos.append((0.0, offs[1], z["hook"]))
    if z.get("cta"):
        tramos.append((offs[2], total, z["cta"]))
    if z.get("partes"):
        partes = {p["i"]: p for p in partes_de(combo["cuerpo"])}
        for i, zz in z["partes"].items():
            if int(i) in partes:
                tramos.append((offs[1] + partes[int(i)]["ini"], offs[1] + partes[int(i)]["fin"], zz))
    sumas = []
    for a, b, zz in tramos:
        f, d = zz["fuerza"], max(0.1, b - a)
        cuerpo = {"in": f"{f}*(it-{a:.3f})/{d:.3f}", "out": f"{f}*(1-(it-{a:.3f})/{d:.3f})", "punch": f"{f}"}[zz["tipo"]]
        sumas.append(f"between(it,{a:.3f},{b:.3f})*({cuerpo})")
    return "1+" + "+".join(sumas) if sumas else None


def borrar_version(vid):
    """Borra una versión. Si eran los ajustes propios de un video, el video vuelve a seguir a sus piezas
    (su archivo se queda; hay que re-armarlo). Devuelve True si eran ajustes propios."""
    vs = leer_versiones()
    v = vs.pop(vid, None)
    if v is not None:
        _versiones_path().write_text(json.dumps(vs, ensure_ascii=False, indent=1), encoding="utf-8")
        if v.get("propio"):
            return True
        for f in (S.salida / f"{S.prefijo}-{vid}.mp4", S.portadas / f"{S.prefijo}-{vid}.jpg", S.recetas / f"{vid}.json"):
            f.unlink(missing_ok=True)


def partes_de(nombre):
    """Frases de una pieza procesada con su inicio y fin (en segundos de esa pieza).
    Si se grabó en partes, son las partes; si no, se separa por frases usando la transcripción."""
    lineas = leer_subs(nombre)["lineas"]
    palabras = [w for ln in lineas for w in ln["palabras"]]
    firma = S.proc / f"{nombre}.partes.json"
    try:
        dur = json.loads(firma.read_text(encoding="utf-8")).get("duraciones")
    except (FileNotFoundError, ValueError, AttributeError):
        dur = None
    tramos = []
    if dur and len(dur) > 1:
        t = 0.0
        for d in dur:
            tramos.append((t, t + d))
            t += d
    else:  # por frases: corta donde termina una oración
        ini = 0.0
        for k, w in enumerate(palabras):
            if w["w"][-1:] in ".?!" or k == len(palabras) - 1:
                fin = palabras[k + 1]["s"] if k + 1 < len(palabras) else (duracion(S.proc / f"{nombre}.mp4") if (S.proc / f"{nombre}.mp4").exists() else w["e"])
                tramos.append((ini, fin))
                ini = fin
    out = []
    for i, (a, b) in enumerate(tramos):
        texto = " ".join(w["w"] for w in palabras if a - 0.05 <= w["s"] < b - 0.05)
        out.append({"i": i, "ini": round(a, 2), "fin": round(b, 2), "texto": texto})
    return out


def estructura(codigo, hook=None, cuerpo=None, cta=None):
    """Lo que necesita el editor de un video: sus piezas, las frases del cuerpo y las opciones para cambiarlas.
    hook/cuerpo/cta: otra combinación elegida en el editor (aunque ese video todavía no exista)."""
    c = next((x for x in combinaciones() if x["codigo"] == codigo), None)
    if not c:
        raise ValueError("Ese video no existe en esta sesión")
    ps = piezas()
    original = {"hook": c["hook"], "cuerpo": c["cuerpo"], "cta": c["cta"]}
    otra = {k: v for k, v in (("hook", hook), ("cuerpo", cuerpo), ("cta", cta))
            if v and v != c[k] and any(p["nombre"] == v and p["tipo"] == k for p in ps)}
    if otra:
        c = {**c, **otra}

    def bloque(nombre):
        f = S.proc / f"{nombre}.mp4"
        lineas = leer_subs(nombre)["lineas"]
        return {"nombre": nombre, "dur": round(duracion(f), 2) if f.exists() else None,
                "texto": " ".join(l["texto"] for l in lineas), "archivo": str(f) if f.exists() else None,
                "lineas": [{"texto": l["texto"], "s": l["s"], "e": l["e"],
                            "palabras": [{"w": p["w"], "s": p["s"], "e": p["e"]} for p in l.get("palabras", [])]} for l in lineas],
                "voz": cortes_por_voz(nombre)}

    v = edicion_de({k: c[k] for k in ("hook", "cuerpo", "cta")} if otra else c)  # otra combinación: parte de lo de sus piezas
    return {"codigo": codigo, "edicion": letra_edicion(c), "base": not c.get("version"), "propio": bool((c.get("version") or {}).get("propio")), "original": original, "hook": bloque(c["hook"]), "cuerpo": {**bloque(c["cuerpo"]), "partes": partes_de(c["cuerpo"])},
            "cta": bloque(c["cta"]), "quitar": v.get("quitar", []), "orden": v.get("orden") or [], "recorte_inicio": v.get("recorte_inicio", 0),
            "recorte_fin": v.get("recorte_fin", 0), "nombre": v.get("nombre", ""), "recortes": v.get("recortes", {}),
            "zoom": v.get("zoom", {}), "cortes": v.get("cortes", {}), "quitar_rangos": v.get("quitar_rangos", {}), "capas": v.get("capas", {}),
            "velocidad": v.get("velocidad", {}), "voz_revisada": v.get("voz_revisada") or {},
            "audio_aparte": v.get("audio_aparte") or {}, "duplicados": v.get("duplicados") or {},
            # cuadros por segundo del video (30 o 60): las flechas del editor avanzan un cuadro de verdad
            "fps": max([fps_redondo(fps_de(S.proc / f"{c[k]}.mp4")) for k in ("hook", "cuerpo", "cta")
                        if (S.proc / f"{c[k]}.mp4").exists()] or [30]),
            "opciones": {t: [p["nombre"] for p in ps if p["tipo"] == t] for t in ("hook", "cuerpo", "cta")}}


def _dentro_de(rangos, desde, hasta):
    """Los rangos recortados a [desde, hasta]: un corte guardado nunca se pasa a la pieza de al lado. Si una pieza se
    vuelve a preparar y queda más corta, sus cortes guardados (en segundos) podían caer en la pieza siguiente: un corte
    al final del hook se comía el comienzo del cuerpo."""
    out = []
    for a, b in rangos:
        a, b = max(desde, float(a)), min(hasta, float(b))
        if b - a > 1e-3:
            out.append((a, b))
    return out


def _rangos_a_quitar(combo, offs, total):
    v = edicion_de(combo)
    rangos = []
    lim = {"hook": (0.0, offs[1]), "cuerpo": (offs[1], offs[2]), "cta": (offs[2], total)}
    if v.get("quitar"):
        partes = {p["i"]: p for p in partes_de(combo["cuerpo"])}
        rangos += _dentro_de([(offs[1] + partes[i]["ini"], offs[1] + partes[i]["fin"]) for i in v["quitar"] if i in partes], *lim["cuerpo"])
    r = v.get("recortes") or {}
    durs = [offs[1], offs[2] - offs[1], total - offs[2]]
    for clave, off, dur in (("hook", 0.0, durs[0]), ("cta", offs[2], durs[2])):
        if r.get(clave):
            a, b = r[clave]
            rangos += _dentro_de(([(off, off + a)] if a > 0 else []) + ([(off + b, off + dur)] if b < dur - 0.02 else []), *lim[clave])
    if r.get("partes"):
        partes = {p["i"]: p for p in partes_de(combo["cuerpo"])}
        for i, (a, b) in r["partes"].items():
            p = partes.get(int(i))
            if not p or int(i) in v.get("quitar", []):
                continue
            ini, fin = offs[1] + p["ini"], offs[1] + p["fin"]
            rangos += _dentro_de(([(ini, ini + a)] if a > 0 else []) + ([(ini + b, fin)] if ini + b < fin - 0.02 else []),
                                 ini, min(fin, offs[2]))
    for pz, lista in (v.get("quitar_rangos") or {}).items():
        if pz in lim:
            rangos += _dentro_de([(lim[pz][0] + a, lim[pz][0] + b) for a, b in lista], *lim[pz])
    if v.get("recorte_inicio"):
        rangos.append((0.0, min(float(v["recorte_inicio"]), total - 1)))
    if v.get("recorte_fin"):
        rangos.append((max(0.0, total - float(v["recorte_fin"])), total))
    return sorted(rangos)


def _rangos_velocidad(combo, offs, total):
    """[(desde, hasta, factor)] en el video armado sin recortes: el hook, el CTA o una frase del cuerpo más rápidos."""
    vel = edicion_de(combo).get("velocidad") or {}
    out = []
    if vel.get("hook"):
        out.append((0.0, offs[1], vel["hook"]))
    if vel.get("cta"):
        out.append((offs[2], total, vel["cta"]))
    if vel.get("partes"):
        partes = {p["i"]: p for p in partes_de(combo["cuerpo"])}
        for i, f in vel["partes"].items():
            p = partes.get(int(i))
            if p:
                out.append((offs[1] + p["ini"], min(offs[2], offs[1] + p["fin"]), f))
    lim = {"hook": (0.0, offs[1]), "cuerpo": (offs[1], offs[2]), "cta": (offs[2], total)}
    for clave, lista in (vel.get("tramos") or {}).items():  # frases lentas aceleradas solas (ver _con_ritmo)
        if clave in lim:  # sin pasarse a la pieza de al lado (ver _dentro_de)
            out += [(a2, b2, f) for a, b, f in lista
                    for a2, b2 in _dentro_de([(lim[clave][0] + a, lim[clave][0] + b)], *lim[clave])]
    return out


def _trozos(total, rangos, velocidades=(), bordes=()):
    """Lo que queda del video armado sin recortes: [(desde, hasta, factor)], partido donde cambia la velocidad
    y en los bordes pedidos (pedazos con el audio desagrupado o duplicados)."""
    juntos = []
    # un rango que empieza después del final (o termina antes del comienzo) no se mira: antes, uno que empezaba pasado
    # el final dejaba un tramo «fuera del video» que se pisaba con el último
    for a, b in sorted((max(0.0, a), min(total, b)) for a, b in rangos if min(total, b) > max(0.0, a)):
        if juntos and a <= juntos[-1][1] + 0.01:
            juntos[-1] = (juntos[-1][0], max(juntos[-1][1], b))
        else:
            juntos.append((a, b))
    quedan, t = [], 0.0
    for a, b in juntos:
        if a - t > 0.05:
            quedan.append((t, a))
        t = max(t, b)
    if total - t > 0.05:
        quedan.append((t, total))
    trozos = []
    for a, b in quedan:
        cortes = sorted({a, b, *(x for va, vb, _ in velocidades for x in (va, vb) if a < x < b), *(x for x in bordes if a < x < b)})
        for x, y in zip(cortes, cortes[1:]):
            if y - x < 0.02:
                continue
            medio = (x + y) / 2
            fac = next((fv for va, vb, fv in velocidades if va <= medio <= vb), 1.0)
            trozos.append((x, y, fac))
    return trozos


def _en_su_orden(combo, offs, trozos):
    """(trozos, reordenado): los tramos que quedan, con las partes del cuerpo en el orden elegido en el editor
    (edición «orden»: [2, 0, 1] = la frase 3 primero). El hook va siempre primero y el CTA al final.
    Como los subtítulos, las imágenes y el zoom ya van pegados a la imagen, se mueven con su frase."""
    orden = [int(i) for i in (edicion_de(combo).get("orden") or [])]
    partes = partes_de(combo["cuerpo"])
    if not orden or not partes or orden == sorted(orden):
        return trozos, False
    cortes = sorted({offs[1] + p["ini"] for p in partes} | {offs[1] + p["fin"] for p in partes})
    partidos = []  # un tramo que cruza el borde entre dos frases se parte ahí
    for a, b, fac in trozos:
        bordes = [a] + [c for c in cortes if a + 0.01 < c < b - 0.01] + [b]
        partidos += [(x, y, fac) for x, y in zip(bordes, bordes[1:])]
    puesto = {i: k for k, i in enumerate(orden)}

    def clave(tr):
        medio = (tr[0] + tr[1]) / 2
        if medio < offs[1]:
            return (0, 0, tr[0])
        if medio >= offs[2]:
            return (2, 0, tr[0])
        i = next((p["i"] for p in partes if offs[1] + p["ini"] <= medio < offs[1] + p["fin"]), partes[-1]["i"])
        return (1, puesto.get(i, len(orden) + i), tr[0])
    nuevos = sorted(partidos, key=clave)
    return nuevos, nuevos != sorted(partidos)


def _global(combo, offs, campo):
    """{pieza: [...]} de la edición, con a/b pasados a segundos del video sin recortes."""
    o = {"hook": offs[0], "cuerpo": offs[1], "cta": offs[2]}
    out = []
    for pz, lista in (edicion_de(combo).get(campo) or {}).items():
        for x in lista:
            a, b = (x["a"], x["b"]) if isinstance(x, dict) else (x[0], x[1])
            out.append({**(x if isinstance(x, dict) else {}), "a": o.get(pz, 0) + a, "b": o.get(pz, 0) + b})
    return out


def _con_duplicados(trozos, dups):
    """La copia de cada pedazo duplicado va justo después de él (con lo que quedó de ese pedazo, en su orden)."""
    if not dups:
        return trozos, False
    out = list(trozos)
    for d in sorted(dups, key=lambda x: x["a"]):
        dentro = [k for k, (a, b, _) in enumerate(out) if d["a"] - 1e-3 <= (a + b) / 2 <= d["b"] + 1e-3]
        if not dentro:
            continue
        copia = [out[k] for k in dentro]
        k = max(dentro) + 1
        out[k:k] = copia
    return out, True


def _a_cuadros(trozos, fps=30):
    """Los tramos con sus bordes en cuadros enteros. La imagen solo se puede cortar en un cuadro (cada 1/30 o 1/60 s) y
    el sonido en cualquier punto: con bordes sueltos cada tramo de imagen quedaba hasta un cuadro más corto o más largo
    que su voz y, en un cuerpo con 15 cortes, la voz se corría hasta 130 ms al final (se nota en los labios). Con los
    bordes en cuadros, imagen y voz duran lo mismo en cada tramo."""
    out = []
    for a, b, fac in trozos:
        a2, b2 = round(a * fps) / fps, round(b * fps) / fps
        if b2 - a2 >= 1 / fps - 1e-9:
            out.append((a2, b2, fac))
    return out


def _mapa_tiempo(trozos):
    """(f, duración): f(t) pasa un segundo del video sin recortes al video editado (con las velocidades).
    Si ese segundo se quitó, devuelve None; con pegar=True, el inicio de lo que sigue (en el video sin recortes).
    Los tramos pueden venir en otro orden (partes del cuerpo reordenadas): se busca el tramo que tiene ese segundo."""
    tabla, g = [], 0.0
    for a, b, fac in trozos:
        tabla.append((a, b, fac, g))
        g += (b - a) / fac

    def f(t, pegar=False):
        for a, b, fac, g0 in tabla:
            if a - 1e-6 <= t <= b + 1e-6:
                return g0 + (min(max(t, a), b) - a) / fac
        if not pegar:
            return None
        sigue = [x for x in tabla if x[0] > t]
        return min(sigue, key=lambda x: x[0])[3] if sigue else g
    return f, g


def _filtro_tramos(v_in, a_in, quedan, v_out, a_out, pref="t", aparte=(), fps=30):
    """Filtros que dejan solo los tramos [(desde, hasta, factor)] de [v_in][a_in] y los unen en [v_out][a_out].
    Va dentro del filtro del armado: corta en la misma pasada en que pone los subtítulos (un solo codificado).
    La música y los sonidos se ponen después, sobre el video ya cortado, para que no salten en cada corte.
    aparte: pedazos con el audio desagrupado [{a, b, sin_voz, sin_imagen, desfase}] (segundos del video sin recortes):
    sin imagen, se ve su primer cuadro congelado mientras habla; sin voz, se ve mudo; con desfase, su voz suena corrida
    (encima de lo de al lado, como un corte en J o en L)."""
    n = len(quedan)
    de = lambda a, b: next((x for x in aparte if x["a"] - 1e-3 <= (a + b) / 2 <= x["b"] + 1e-3), None)
    corridos, g = [], 0.0  # (k, segundo del video editado donde empieza a sonar su voz)
    for k, (a, b, fac) in enumerate(quedan):
        x = de(a, b)
        if x and abs(x.get("desfase") or 0) > 0.005 and not x.get("sin_voz"):
            corridos.append((k, g + x["desfase"]))
        g += (b - a) / fac
    na = n + len(corridos)
    f = []
    if n > 1:
        f.append(f"[{v_in}]split={n}" + "".join(f"[{pref}sv{k}]" for k in range(n)))
    if na > 1:
        f.append(f"[{a_in}]asplit={na}" + "".join(f"[{pref}sa{k}]" for k in range(na)))
    medio = 0.5 / fps  # la imagen se corta medio cuadro antes de cada borde: entran justo los cuadros del tramo
    for k, (a, b, fac) in enumerate(quedan):
        vel_v = f"/{fac:.4f}" if abs(fac - 1) > 0.001 else ""
        vel_a = f",atempo={fac:.4f}" if abs(fac - 1) > 0.001 else ""
        vi = f"{pref}sv{k}" if n > 1 else v_in
        ai = f"{pref}sa{k}" if na > 1 else a_in
        x = de(a, b) or {}
        if x.get("sin_imagen"):  # su primer cuadro, quieto, todo lo que dura el pedazo
            d = (b - a) / fac
            f.append(f"[{vi}]trim=start={max(0.0, a - medio):.6f}:end={a + medio:.6f},setpts=PTS-STARTPTS,"
                     f"tpad=stop_mode=clone:stop_duration={d:.6f},trim=duration={d:.6f},setpts=PTS-STARTPTS[{pref}v{k}]")
        else:
            f.append(f"[{vi}]trim=start={max(0.0, a - medio):.6f}:end={b - medio:.6f},setpts=(PTS-STARTPTS){vel_v}[{pref}v{k}]")
        mudo = ",volume=0" if x.get("sin_voz") or any(c == k for c, _ in corridos) else ""  # la corrida suena aparte
        f.append(f"[{ai}]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS{vel_a}{mudo}[{pref}a{k}]")
    a_concat = f"{pref}acat" if corridos else a_out
    f.append("".join(f"[{pref}v{k}][{pref}a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[{v_out}][{a_concat}]")
    if corridos:
        for j, (k, t) in enumerate(corridos):
            a, b, fac = quedan[k]
            vel_a = f",atempo={fac:.4f}" if abs(fac - 1) > 0.001 else ""
            inicio = a + max(0.0, -t) * fac  # si se corre antes del segundo 0, se pierde lo que no entra
            ms = int(max(0.0, t) * 1000)
            f.append(f"[{pref}sa{n + j}]atrim=start={inicio:.3f}:end={b:.3f},asetpts=PTS-STARTPTS{vel_a},adelay={ms}:all=1[{pref}x{j}]")
        f.append(f"[{a_concat}]" + "".join(f"[{pref}x{j}]" for j in range(len(corridos)))
                 + f"amix=inputs={len(corridos) + 1}:normalize=0:duration=first[{a_out}]")
    return f


def t_ass(t):
    cs = int(round(max(0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def color_ass(hexrgb):
    h = hexrgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&"


def color_ass_alfa(hexrgb, opacidad):
    """Color ASS con transparencia (opacidad 0–100 %)."""
    h = hexrgb.lstrip("#")
    alfa = 255 - round(max(0, min(100, float(opacidad))) * 2.55)
    return f"&H{alfa:02X}{h[4:6]}{h[2:4]}{h[0:2]}&"


def es_clave(w):
    return norm(w) in {norm(x) for x in CONFIG["palabras_clave"]}


def limpiar(w):
    return w.replace("{", "").replace("}", "").replace("\\", "")


ESTILOS_SUB = ["normal", "centro_grande", "arriba", "palabra_sola", "resaltado"]
ESCALA_ESTILO = {"normal": 1.0, "arriba": 1.0, "centro_grande": 1.35, "sobre_imagen": 1.2, "palabra_sola": 2.0}


def carpeta_trabajo():
    """Carpeta temporal de armado, con las fuentes al lado (ffmpeg no acepta bien rutas con espacios y ':').
    Una por proceso: si hay dos salas abiertas a la vez (o la sala y una prueba), no se pisan los archivos."""
    trabajo = DATOS / "_trabajo" / (f"p{os.getpid()}" + (f"_{_LOCAL.slot}" if getattr(_LOCAL, "slot", None) else ""))
    (trabajo / "fuentes").mkdir(parents=True, exist_ok=True)
    import fuentes
    for f in [*(RAIZ / "fuentes").glob("*.ttf"), *fuentes.PROPIAS.glob("*.[ot]tf")]:
        d = trabajo / "fuentes" / f.name
        if not d.exists() or d.stat().st_size != f.stat().st_size:
            shutil.copy2(f, d)
    return trabajo


ELEMENTOS = DATOS / "elementos"  # imágenes, logos y GIF que el equipo sube para usar en las plantillas


def ruta_elemento(nombre):
    """Archivo de un elemento de plantilla: subido por el equipo o de la marca (biblioteca/marca)."""
    nombre = Path(str(nombre)).name
    for base in (ELEMENTOS, BIBLIO_BASE / "marca"):
        if nombre and (base / nombre).is_file():
            return base / nombre
    return None


def plantillas():
    """Las plantillas que trae la sala + las que creó el equipo (config.local → plantillas_propias)."""
    return {**CONFIG.get("plantillas_sub", {}), **CONFIG.get("plantillas_propias", {})}


def id_plantilla():
    """La del guion de la sesión manda; si no, la predeterminada de esta PC; si no, la de config.json."""
    todas = plantillas()
    for pid in (produccion().get("plantilla_sub"), CONFIG.get("plantilla_activa"), CONFIG["subtitulos"].get("plantilla")):
        if pid in todas:
            return pid
    return next(iter(todas))


# lo mínimo que necesita el armado: una plantilla propia vieja o a medio llenar no lo rompe
PLANTILLA_BASE = {"fuente": "Anton-Regular.ttf", "tamano": 80, "mayusculas": True, "color_texto": "#FFFFFF", "color_activo": "#FFFFFF",
                  "color_borde": "#000000", "borde": 3, "sombra": 2, "color_sombra": "#000000", "opacidad_sombra": 50,
                  "escala_activa": 108, "espaciado": 0, "desenfoque": 0, "x_centro": 540, "ancho_max": 960}


def id_plantilla_de(combo=None):
    """Qué plantilla usa un video: la suya, si no la de su edición (B, C…), si no la predeterminada."""
    todas = plantillas()
    if combo:
        for pid in (ajustes_video(combo["codigo"]).get("plantilla"), ediciones().get(letra_edicion(combo), {}).get("plantilla")):
            if pid in todas:
                return pid
    return id_plantilla()


# Límites de los números de una plantilla: un valor escrito a mano fuera de rango (tamaño 900, centro 5000) rompía
# el armado. Lo que no está aquí pasa tal cual.
LIMITES_PLANTILLA = {"tamano": (20, 260), "borde": (0, 40), "sombra": (0, 40), "desenfoque": (0, 40), "x_centro": (0, 1920),
                     "ancho_max": (200, 1900), "altura_desde_abajo": (0, 1900), "altura_hook": (0, 1900), "altura_cta": (0, 1900),
                     "escala_activa": (50, 300), "palabras_max": (1, 20), "interlineado": (0.5, 3)}


def limpiar_plantilla(d):
    """Una plantilla guardada desde la sala, con los números dentro de lo que el armado aguanta y una fuente que
    exista (si no, la de la sala)."""
    out = dict(d or {})
    for k, (lo, hi) in LIMITES_PLANTILLA.items():
        if out.get(k) in (None, ""):
            continue
        try:
            out[k] = max(lo, min(hi, float(out[k])))
        except (TypeError, ValueError):
            out.pop(k)
    try:
        import fuentes
        for k in ("fuente", "fuente_linea2"):
            if out.get(k) and not fuentes.ruta(out[k]).exists():
                out.pop(k)
    except (ImportError, AttributeError):
        pass
    return out


def plantilla_sub(combo=None):
    """Plantilla de subtítulos (fuente, colores, resaltador, posición). Si el video tiene una propia, esa."""
    base = plantillas()[id_plantilla_de(combo)]
    pl = {**PLANTILLA_BASE, **{k: v for k, v in base.items() if v not in (None, "")},
          **(ajustes_video(combo["codigo"]).get("ajustes_sub", {}) if combo else {})}
    if not float(pl.get("tamano") or 0):
        pl["tamano"] = PLANTILLA_BASE["tamano"]
    # hasta la 1.47.8, guardar una plantilla dejaba «cuánto crece la que se dice» en 3 (un tope mal puesto): eso es
    # la palabra activa al 3 %, invisible. Una así vuelve a la de fábrica
    if float(pl.get("escala_activa") or 0) < 50:
        pl["escala_activa"] = PLANTILLA_BASE["escala_activa"]
    return pl


def _ajustes_video_path():
    return S.subs / "_ajustes_videos.json"


def ajustes_video(codigo):
    """Ajustes propios de un video: plantilla de subtítulos, posición del subtítulo."""
    try:
        return json.loads(_ajustes_video_path().read_text(encoding="utf-8")).get(codigo, {})
    except (FileNotFoundError, ValueError):
        return {}


ALTURA_SEGURA = 330  # px desde abajo: más abajo lo tapan la descripción y los botones de TikTok e Instagram


def subir_subtitulos(minimo=ALTURA_SEGURA):
    """«Subir el subtítulo a 330 px»: en cada video con el subtítulo más abajo de lo seguro, lo sube a esa altura.
    Si la altura viene del video (ajustes propios), se cambia en el video; si viene de su plantilla, en la plantilla
    (una vez para todos los videos que la usan). Devuelve los videos que hay que rearmar."""
    campos = ("altura_desde_abajo", "altura_hook", "altura_cta")
    plantillas_a_subir, codigos = set(), []
    for c in combinaciones():
        aj = (ajustes_video(c["codigo"]).get("ajustes_sub") or {})
        propias = {k: aj[k] for k in campos if aj.get(k) not in (None, "")}
        bajas = {k: v for k, v in propias.items() if float(v) < minimo}
        if bajas:
            guardar_ajustes_video(c["codigo"], {"ajustes_sub": {**aj, **{k: minimo for k in bajas}}})
            codigos.append(c["codigo"])
        pl = plantilla_sub(c)
        if any(pl.get(k) not in (None, "") and float(pl[k]) < minimo and k not in propias for k in campos):
            plantillas_a_subir.add(id_plantilla_de(c))
            codigos.append(c["codigo"])
    if plantillas_a_subir:
        todas, propias = plantillas(), dict(CONFIG.get("plantillas_propias") or {})
        for pid in plantillas_a_subir:
            base = dict(propias.get(pid) or todas.get(pid) or {})
            for k in campos:
                if base.get(k) not in (None, "") and float(base[k]) < minimo:
                    base[k] = minimo
            if not base.get("altura_desde_abajo") or float(base["altura_desde_abajo"]) < minimo:
                base["altura_desde_abajo"] = max(minimo, float(base.get("altura_desde_abajo") or 0))
            propias[pid] = {**base, "editado": time.time()}
        guardar_config({"plantillas_propias": propias})
    return sorted(set(codigos))


def guardar_ajustes_video(codigo, datos):
    try:
        todos = json.loads(_ajustes_video_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        todos = {}
    actual = {**todos.get(codigo, {}), **datos}
    if set(datos) - {"estado"}:  # el estado de trabajo no cambia cómo se ve el video
        actual["editado"] = time.time()
    todos[codigo] = {k: v for k, v in actual.items() if v not in (None, "", {})}
    S.subs.mkdir(parents=True, exist_ok=True)
    _ajustes_video_path().write_text(json.dumps(todos, ensure_ascii=False, indent=1), encoding="utf-8")


def _caja(w, h, r):
    """Rectángulo redondeado en dibujo ASS (origen arriba a la izquierda)."""
    r = max(0, min(r, h / 2, w / 2))
    k = r * 0.45

    def q(v):
        return f"{v:.0f}"
    return (f"m {q(r)} 0 l {q(w - r)} 0 b {q(w - k)} 0 {q(w)} {q(k)} {q(w)} {q(r)} l {q(w)} {q(h - r)} "
            f"b {q(w)} {q(h - k)} {q(w - k)} {q(h)} {q(w - r)} {q(h)} l {q(r)} {q(h)} b {q(k)} {q(h)} 0 {q(h - k)} 0 {q(h - r)} "
            f"l 0 {q(r)} b 0 {q(k)} {q(k)} 0 {q(r)} 0")


def cabecera_ass(fuente_nombre, pl):
    W, H = lienzo()
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: W,{fuente_nombre},{pl['tamano']},{color_ass(pl['color_texto'])},&H00FFFFFF&,{color_ass(pl['color_borde'])},"
        f"{color_ass_alfa(pl.get('color_sombra', '#000000'), pl.get('opacidad_sombra', 50))},0,0,0,0,100,100,0,0,1,{pl['borde']},{pl['sombra']},5,0,0,0,1",
        "Style: Caja,Arial,20,&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H00000000&,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", ""])


def _frases(lineas, max_pal=8, max_car=44, pausa=0.6):
    """Junta las líneas cortas de la transcripción en frases completas (como subtitula el editor del equipo):
    hasta un punto, una pausa o un largo máximo. Devuelve (lineas_nuevas, {(li, wi): (li_nueva, wi_nueva)})."""
    out, mapa = [], {}
    for li, ln in enumerate(lineas):
        act = out[-1] if out else None
        texto = (act["texto"] + " " + ln["texto"]) if act else ln["texto"]
        junta = (act and act["palabras"] and not re.search(r"[.?!…]$", act["texto"].strip())
                 and ln["s"] - act["e"] < pausa and len(texto.split()) <= max_pal and len(texto) <= max_car)
        if junta:
            base = len(act["palabras"])
            act["palabras"] = act["palabras"] + ln["palabras"]
            act["texto"], act["e"] = texto, ln["e"]
        else:
            base = 0
            out.append({**ln, "palabras": list(ln["palabras"])})
        for wi in range(len(ln["palabras"])):
            mapa[(li, wi)] = (len(out) - 1, base + wi)
    return out, mapa


def escribir_ass(segmentos, ruta, tapas=(), combo=None):
    """segmentos: [(offset, lineas, marcas_resueltas)]. tapas: [(ini, fin)] donde hay imagen a pantalla completa.

    Cada palabra se ubica a mano (con el ancho real de la fuente) para poder poner el resaltador
    —una caja del color de la marca— detrás de la palabra clave o de la que se está diciendo."""
    import fuentes
    st = CONFIG["subtitulos"]
    pl = _al_lienzo(plantilla_sub(combo))
    W, H = lienzo()
    fu = fuentes.cargar(pl["fuente"])
    res = pl.get("resaltador") or {}
    modo = res.get("modo", "")  # "clave", "activa", "ambas" o "" (sin caja)
    c_activo, c_texto = color_ass(pl["color_activo"]), color_ass(pl["color_texto"])
    c_marca = color_ass(pl.get("color_resaltado", st.get("color_resaltado", "#4DE1FF")))
    ev = []

    # altura del subtítulo normal por parte: el hook y el CTA pueden tener la suya (ajuste del video); si no, la de siempre
    alt_base = pl.get("altura_desde_abajo", st["altura_desde_abajo"])
    alturas = [pl.get("altura_hook"), None, pl.get("altura_cta")]
    todas = []
    for k_seg, (off, lineas, marcas) in enumerate(segmentos):
        altura_seg = float(alturas[k_seg] if k_seg < len(alturas) and alturas[k_seg] not in (None, "") else alt_base)
        if pl.get("frase_completa"):  # la frase entera en pantalla, no de a 2 o 3 palabras
            lineas, mapa_li = _frases(lineas)
            marcas = [{**m, "linea": mapa_li.get((m["linea"], m["indice"]), (m["linea"], m["indice"]))[0],
                       "indice": mapa_li.get((m["linea"], m["indice"]), (m["linea"], m["indice"]))[1]} for m in marcas]
        estilo_linea, solas, resaltadas = {}, set(), set()
        for m in marcas:
            sub = m.get("sub")
            if sub in ("normal", "centro_grande", "arriba"):
                estilo_linea[m["linea"]] = sub
            elif sub == "palabra_sola":
                solas.add((m["linea"], m["indice"]))
            elif sub == "resaltado":
                resaltadas.add((m["linea"], m["indice"]))
        for li, ln in enumerate(lineas):
            todas.append((off, li, ln, estilo_linea.get(li), solas, resaltadas, altura_seg))

    # Segunda línea con otra fuente (opcional): la frase se parte en dos renglones; el de abajo usa su propia fuente,
    # tamaño y color. Si la fuente no está, se usa la principal.
    l2 = pl.get("linea2") or {}
    fu2 = fu
    if l2.get("activa") and l2.get("fuente"):
        try:
            fu2 = fuentes.cargar(l2["fuente"])
        except Exception:
            fu2 = fu
    c_texto2 = color_ass(l2["color"]) if l2.get("color") else c_texto
    # cuadro de texto: dónde va el subtítulo (centro horizontal y ancho máximo, en píxeles del video)
    x_centro = float(pl.get("x_centro", W / 2))
    ancho_max = max(200.0, min(float(W), float(pl.get("ancho_max", 960))))
    blur = f"\\blur{float(pl['desenfoque']):.1f}" if pl.get("desenfoque") else ""

    def partir(textos, tam0=None):
        """Índices de cada renglón: un solo renglón, o dos parejos por cantidad de letras (con «segunda línea», o con
        «salto_auto» cuando la frase no entra en el ancho del cuadro)."""
        n = len(textos)
        if n < 2:
            return [list(range(n))]
        no_entra = pl.get("salto_auto") and tam0 and fu.ancho(" ".join(textos), tam0) * 1.05 > ancho_max
        if not l2.get("activa") and not no_entra:
            return [list(range(n))]
        largos = [len(t) for t in textos]
        mejor = min(range(1, n), key=lambda k: abs(sum(largos[:k]) - sum(largos[k:])))
        return [list(range(mejor)), list(range(mejor, n))]

    def poner_linea(palabras, activa, estilo, ini, fin, cajas, coloreadas, pop, altura=None):
        tam0 = pl["tamano"] * ESCALA_ESTILO[estilo]
        textos = [limpiar(w.upper() if pl.get("mayusculas", True) else w) for w in palabras]
        filas = partir(textos, tam0) if estilo != "palabra_sola" else [list(range(len(textos)))]
        medidas = []
        for r, idx in enumerate(filas):
            otra = r == 1 and l2.get("activa")  # la segunda línea con su propia letra (si no, es la misma frase en dos renglones)
            f_ = fu2 if otra else fu
            tam = tam0 * (l2.get("escala", 100) / 100 if otra else 1)
            esp = f_.ancho(" ", tam) * 1.1
            # la palabra activa se ve más grande: se le reserva ese espacio para que no pise a sus vecinas
            crece = {j: (pl.get("escala_activa", 108) / 100 if j == activa else 1.0) for j in idx}
            letras = float(pl.get("espaciado", 0)) * tam / 100  # espaciado entre letras (% del tamaño)
            anchos = [(f_.ancho(textos[j], tam) + letras * len(textos[j])) * crece[j] for j in idx]
            total = sum(anchos) + esp * (len(idx) - 1)
            if total > ancho_max:  # no entra en el cuadro de texto: se achica el renglón entero
                f = ancho_max / total
                tam, esp, anchos, total = tam * f, esp * f, [a * f for a in anchos], ancho_max
            medidas.append({"fu": f_, "tam": tam, "esp": esp, "anchos": anchos, "total": total, "idx": idx, "crece": crece, "r": r if otra or r == 0 else 0,
                            "fsp": letras * tam / (tam0 * (l2.get("escala", 100) / 100 if otra else 1))})
        altos = [m["tam"] * (1.02 if len(medidas) == 1 or l2.get("activa") else 1.12) for m in medidas]
        bloque = sum(altos)
        # la altura del subtítulo normal se mueve desde la plantilla (o desde el ajuste del video)
        if estilo == "normal":
            y_fin = H - (alt_base if altura is None else altura)
            y0 = y_fin - bloque
        elif estilo == "arriba":
            y0 = pl.get("altura_desde_arriba", st.get("altura_desde_arriba", 300))
        else:
            y0 = H / 2 - bloque / 2
        a, b = t_ass(ini), t_ass(fin)
        for m, alto_fila in zip(medidas, altos):
            y = y0 + alto_fila / 2
            y0 += alto_fila
            x, tam = x_centro - m["total"] / 2, m["tam"]
            fn = f"\\fn{m['fu'].nombre}" if m["fu"] is not fu else ""
            for j, w in zip(m["idx"], m["anchos"]):
                t = textos[j]
                cx = x + w / 2
                if j in cajas:
                    px, alto = tam * res.get("margen", 0.16), tam * res.get("alto", 0.95) * m["crece"][j]
                    fade = "\\fad(60,0)" if j == activa and pop else ""
                    ev.append(f"Dialogue: 1,{a},{b},Caja,,0,0,0,,{{\\pos({x - px:.0f},{y - alto / 2:.0f})\\p1"
                              f"\\1c{color_ass(res['color'])}\\1a&H{res.get('transparencia', '00')}&{fade}}}"
                              f"{_caja(w + 2 * px, alto, tam * res.get('radio', 0.22))}")
                    color, extra = color_ass(res.get("texto", "#253C61")), "\\bord0\\shad0"
                elif j == activa:
                    color, extra = c_activo, ""
                elif j in coloreadas:
                    color, extra = c_marca, ""
                else:
                    color, extra = (c_texto2 if m["r"] == 1 else c_texto), ""
                escala = pl.get("escala_activa", 108) if j == activa else 100
                anim = "\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)" if pop and j == 0 and activa == 0 else ""
                ev.append(f"Dialogue: 2,{a},{b},W,,0,0,0,,{{\\an5\\pos({cx:.0f},{y:.0f}){fn}\\fs{tam:.0f}\\fsp{m['fsp']:.1f}{blur}\\1c{color}{extra}"
                          f"\\fscx{escala}\\fscy{escala}{anim}}}{t}")
                x += w + m["esp"]

    for idx, (off, li, ln, estilo, solas, resaltadas, altura_seg) in enumerate(todas):
        pal = ln["palabras"]
        if not pal:
            continue
        sig = todas[idx + 1] if idx + 1 < len(todas) else None
        siguiente = sig[0] + sig[2]["s"] if sig else None
        fin_linea = off + ln["e"]
        if siguiente is not None and siguiente - fin_linea < 0.4:
            fin_linea = siguiente
        claves = {j for j, q in enumerate(pal) if es_clave(q["w"]) or (li, j) in resaltadas}
        for i, p in enumerate(pal):
            ini = off + (ln["s"] if i == 0 else p["s"])
            fin = off + pal[i + 1]["s"] if i + 1 < len(pal) else fin_linea
            if fin <= ini:
                continue
            # regla automática: sobre una imagen a pantalla completa el subtítulo va al centro, pero solo mientras se ve
            # la imagen (como en la vista previa). Antes se decidía por la frase entera: con «frase completa» la frase
            # seguía al centro, encima de la cara, cuando la imagen ya se había ido, y la altura de la plantilla no contaba.
            if estilo is not None:
                est = estilo
            else:
                medio = (ini + fin) / 2
                est = "sobre_imagen" if any(ta <= medio < tb for ta, tb in tapas) else "normal"
            if (li, i) in solas:
                poner_linea([p["w"]], 0, "palabra_sola", ini, fin, {0} if modo else set(), set(), True)
                continue
            cajas = set()
            if modo in ("clave", "ambas"):
                cajas |= claves
            if modo in ("activa", "ambas"):
                cajas.add(i)
            poner_linea([q["w"] for q in pal], i, est, ini, fin, cajas, claves - cajas, i == 0, altura_seg)
    ruta.write_text(cabecera_ass(fu.nombre, pl) + "\n".join(ev) + "\n", encoding="utf-8")


# ---------- Sonido ----------

def _audios(ruta):
    if ruta.is_file():
        return [ruta] if ruta.suffix.lower() in EXT_AUDIO else []
    if ruta.is_dir():
        return sorted(f for f in ruta.iterdir() if f.suffix.lower() in EXT_AUDIO and f.stat().st_size > 0)
    return []


_sfx_cache = {}


def sfx_de(categoria):
    """Archivos de una categoría. Orden: mapa de config (carpetas o archivos de la librería del equipo),
    carpeta <biblioteca>/sfx/<categoria>/, y por último los provisionales que trae el código."""
    if categoria in _sfx_cache:
        return _sfx_cache[categoria]
    out = []
    for rel in CONFIG.get("sfx_mapa", {}).get(categoria, []):
        out += _audios(BIBLIO / rel)
    if not out:
        out = _audios(BIBLIO / "sfx" / categoria) or _audios(BIBLIO_BASE / "sfx" / categoria)
    _sfx_cache[categoria] = out
    return out


def id_sonido(f):
    """Nombre estable de un archivo de sonido para la app: b:<ruta en la biblioteca> o p:<ruta en la que trae la sala>."""
    f = Path(f)
    for pre, base in (("b:", BIBLIO), ("p:", BIBLIO_BASE)):
        try:
            return pre + f.relative_to(base).as_posix()
        except ValueError:
            pass
    return None


def ruta_sonido(sid):
    """El archivo de un id de sonido, solo si está dentro de las bibliotecas (nada de rutas sueltas)."""
    base = {"b:": BIBLIO, "p:": BIBLIO_BASE}.get(str(sid)[:2])
    if not base:
        return None
    f = (base / str(sid)[2:]).resolve()
    return f if base.resolve() in f.parents and f.is_file() and f.suffix.lower() in EXT_AUDIO else None


def categoria_de_sonido(sid):
    """La categoría donde está un sonido exacto (para que siga las reglas de esa categoría), o «marca»."""
    f = ruta_sonido(sid)
    cats = list(CONFIG.get("sfx_mapa", {})) + [d.name for d in (BIBLIO_BASE / "sfx").iterdir() if d.is_dir()]
    return next((c for c in cats if f in sfx_de(c)), "marca")


def biblioteca_sonidos():
    """Las categorías de sonido con sus archivos: [{cat, nombre, archivos: [{id, nombre}]}]."""
    cats = list(CONFIG.get("sfx_mapa", {})) + [d.name for d in (BIBLIO_BASE / "sfx").iterdir() if d.is_dir()]
    out, vistas = [], set()
    for cat in cats:
        if cat in vistas:
            continue
        vistas.add(cat)
        archivos = [{"id": id_sonido(f), "nombre": f.stem, **({"aj": aj} if (aj := ajustes_sonido(id_sonido(f))) else {})}
                    for f in sfx_de(cat) if id_sonido(f)]
        nombre = NOMBRES_SFX.get(cat, cat.replace("_", " "))
        out.append({"cat": cat, "nombre": nombre[:1].upper() + nombre[1:], "archivos": archivos})  # tipo oración
    return out


# ---------- Sonido editado (página Sonidos): queda como predeterminado de ese archivo en todos los videos ----------

AJUSTES_SONIDO = {  # campo: (mínimo, máximo, neutro)
    "desde": (0.0, 600.0, 0.0), "hasta": (0.0, 600.0, 0.0),  # recorte del archivo; hasta 0 = hasta el final
    "vel": (0.5, 2.0, 1.0), "tono": (-12.0, 12.0, 0.0),  # velocidad (x) y tono (semitonos), por separado
    "eco": (0.0, 1.0, 0.0),
    # ecualizador paramétrico: cortes (Hz, 0 = apagado), graves y agudos en estante, medios en campana (ganancia dB, Hz, ancho Q)
    "corte_graves": (0.0, 1000.0, 0.0), "corte_agudos": (0.0, 20000.0, 0.0),
    "graves": (-15.0, 15.0, 0.0), "graves_f": (40.0, 600.0, 150.0),
    "medios": (-15.0, 15.0, 0.0), "medios_f": (150.0, 10000.0, 1000.0), "medios_q": (0.3, 6.0, 1.0),
    "agudos": (-15.0, 15.0, 0.0), "agudos_f": (1500.0, 14000.0, 4000.0),
    "graves_q": (0.3, 6.0, 1.0), "agudos_q": (0.3, 6.0, 1.0),  # ancho, solo cuando la banda va en campana
    "fade_in": (0.0, 3.0, 0.0), "fade_out": (0.0, 3.0, 0.0), "vol": (0.0, 3.0, 1.0),
}


AJUSTES_SONIDO_SI_NO = ("invertir", "sin_silencios", "graves_campana", "agudos_campana")  # opciones de sí o no


def ajustes_sonido(sid):
    """Lo editado de un sonido (solo lo que no está neutro). invertir: suena al revés."""
    return dict((CONFIG.get("ajustes_sonidos") or {}).get(sid) or {})


def guardar_ajustes_sonido(sid, aj):
    """Guarda (o borra, si todo queda neutro) la edición de un sonido. Devuelve lo guardado."""
    if not ruta_sonido(sid):
        raise ValueError("Ese sonido no está en la biblioteca")
    limpio = {}
    for k, (lo, hi, neutro) in AJUSTES_SONIDO.items():
        if aj.get(k) in (None, ""):
            continue
        v = round(max(lo, min(hi, float(aj[k]))), 3)
        if v != neutro:
            limpio[k] = v
    for k in AJUSTES_SONIDO_SI_NO:
        if aj.get(k):
            limpio[k] = True
    todos = dict(CONFIG.get("ajustes_sonidos") or {})
    cambio = (todos.get(sid) or {}) != limpio
    if limpio:
        todos[sid] = limpio
    else:
        todos.pop(sid, None)
    extra = {}
    if cambio:  # así los videos que lo usan salen «⟳ rearmar» (antes el aviso lo decía, pero no quedaban marcados)
        extra["ajustes_sonidos_cambio"] = {**(CONFIG.get("ajustes_sonidos_cambio") or {}), sid: time.time()}
    guardar_config({"ajustes_sonidos": todos, **extra})
    return limpio


def videos_con_sonido(sid):
    """Los videos ya armados que usan ese sonido (según su receta)."""
    out = []
    for c in combinaciones():
        try:
            r = json.loads((S.recetas / f"{c['codigo']}.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            continue
        if any(e.get("id") == sid for e in r.get("efectos", [])):
            out.append(c["codigo"])
    return out


def _atempo(factor):
    """atempo solo acepta 0.5–2 por paso: se encadena para factores mayores o menores."""
    pasos = []
    while factor > 2.0:
        pasos.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        pasos.append(0.5)
        factor /= 0.5
    if abs(factor - 1) > 1e-3:
        pasos.append(factor)
    return ",".join(f"atempo={p:.4f}" for p in pasos)


_limpieza_sfx = [0.0]


def _limpiar_sfx_editados(dias=7):
    """Borra las versiones editadas que nadie usó en una semana (las pruebas de la página Sonidos se acumulan).
    Las de sonidos guardados se vuelven a crear solas si hacen falta. Como mucho una vez por hora."""
    if time.time() - _limpieza_sfx[0] < 3600:
        return
    _limpieza_sfx[0] = time.time()
    limite = time.time() - dias * 86400
    for f in (DATOS / "sfx_editados").glob("*.wav"):
        try:
            if f.stat().st_mtime < limite:
                f.unlink()
        except OSError:
            pass


def sonido_editado(f, prueba=None):
    """El archivo con su edición de la página Sonidos aplicada (wav en caché), o el original si no se editó.
    prueba: ajustes sin guardar, para escucharlos antes de guardar.
    El volumen no se mete en el archivo: se aplica al mezclar (si no, normalizar lo desharía)."""
    f = Path(f)
    sid = id_sonido(f)
    aj = prueba if prueba is not None else (ajustes_sonido(sid) if sid else {})
    aj = {k: v for k, v in aj.items() if k != "vol" and v not in (None, "", 0, False)
          and not (k == "vel" and float(v) == 1.0)}
    if not aj:
        return f
    firma = hashlib.md5(f"{f}|{f.stat().st_size}|{int(f.stat().st_mtime)}|{json.dumps(aj, sort_keys=True)}".encode()).hexdigest()
    destino = DATOS / "sfx_editados" / f"{firma}.wav"
    if destino.exists() and destino.stat().st_size > 0:
        os.utime(destino)  # recién usado: no se limpia
        return destino
    _limpiar_sfx_editados()
    sr = 48000
    d = dur_audio(f)
    desde = min(float(aj.get("desde", 0)), max(0.0, d - 0.05))
    hasta = float(aj.get("hasta", 0)) or d
    hasta = max(desde + 0.05, min(hasta, d))
    destino.parent.mkdir(parents=True, exist_ok=True)
    if aj.get("sin_silencios"):
        # pasada previa: el recorte sin ningún silencio (ni al inicio, ni en medio, ni al final); umbral relativo al
        # pico del propio sonido, y deja 15 ms en cada unión para que no haga clic
        r = run([FFMPEG, "-hide_banner", "-i", str(f), "-vn", "-af", f"atrim={desde:.3f}:{hasta:.3f},volumedetect", "-f", "null", "-"])
        pico = float((re.findall(r"max_volume: (-?[\d.]+) dB", r.stderr) or ["0"])[-1])
        umbral = max(-70.0, pico - 40)
        previa = destino.with_name(destino.stem + ".sin_silencios.wav")
        run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(f), "-vn", "-af",
             f"atrim={desde:.3f}:{hasta:.3f},asetpts=N/SR/TB,"
             f"silenceremove=start_periods=1:start_threshold={umbral:.0f}dB:start_silence=0.01:"
             f"stop_periods=-1:stop_duration=0.06:stop_threshold={umbral:.0f}dB:stop_silence=0.015",
             "-c:a", "pcm_s16le", "-ar", str(sr), str(previa)])
        if previa.exists() and previa.stat().st_size > 1000:
            f, desde = previa, 0.0
            hasta = duracion(previa)
    vel, tono = float(aj.get("vel", 1)), float(aj.get("tono", 0))
    largo = (hasta - desde) / vel
    fil = [f"atrim={desde:.3f}:{hasta:.3f}", "asetpts=N/SR/TB", f"aformat=sample_rates={sr}:channel_layouts=stereo"]
    if aj.get("invertir"):
        fil.append("areverse")
    if tono:  # el tono cambia la velocidad junto con él: atempo la deja en la velocidad pedida
        r = 2 ** (tono / 12)
        fil += [f"asetrate={sr * r:.0f}", f"aresample={sr}"]
        t = _atempo(vel / r)
    else:
        t = _atempo(vel)
    if t:
        fil.append(t)
    # ecualizador (los mismos filtros que dibuja la curva de la página Sonidos: biquads de 2 polos)
    if aj.get("corte_graves"):
        fil.append(f"highpass=f={float(aj['corte_graves']):.0f}:p=2:t=q:w=0.707")
    # graves y agudos: en estante (sube o baja todo desde esa frecuencia hacia el extremo) o en campana (solo alrededor)
    if aj.get("graves") and aj.get("graves_campana"):
        fil.append(f"equalizer=f={float(aj.get('graves_f', 150)):.0f}:t=q:w={float(aj.get('graves_q', 1)):.2f}:g={aj['graves']}")
    elif aj.get("graves"):
        fil.append(f"lowshelf=g={aj['graves']}:f={float(aj.get('graves_f', 150)):.0f}:t=s:w=1")
    if aj.get("medios"):
        fil.append(f"equalizer=f={float(aj.get('medios_f', 1000)):.0f}:t=q:w={float(aj.get('medios_q', 1)):.2f}:g={aj['medios']}")
    if aj.get("agudos") and aj.get("agudos_campana"):
        fil.append(f"equalizer=f={float(aj.get('agudos_f', 4000)):.0f}:t=q:w={float(aj.get('agudos_q', 1)):.2f}:g={aj['agudos']}")
    elif aj.get("agudos"):
        fil.append(f"highshelf=g={aj['agudos']}:f={float(aj.get('agudos_f', 4000)):.0f}:t=s:w=1")
    if aj.get("corte_agudos"):
        fil.append(f"lowpass=f={float(aj['corte_agudos']):.0f}:p=2:t=q:w=0.707")
    if aj.get("eco"):
        e = float(aj["eco"])
        fil += [f"apad=pad_dur={0.4 + e:.2f}", f"aecho=0.8:0.85:{int(60 + 140 * e)}|{int(120 + 280 * e)}:{0.25 + 0.4 * e:.2f}|{0.15 + 0.3 * e:.2f}"]
        largo += 0.4 + e
    if aj.get("fade_in"):
        fil.append(f"afade=t=in:st=0:d={min(float(aj['fade_in']), largo):.3f}")
    if aj.get("fade_out"):
        fo = min(float(aj["fade_out"]), largo)
        fil.append(f"afade=t=out:st={max(0.0, largo - fo):.3f}:d={fo:.3f}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp.wav")
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(f), "-vn", "-af", ",".join(fil),
         "-c:a", "pcm_s16le", "-ar", str(sr), str(tmp)])
    if not tmp.exists() or tmp.stat().st_size == 0:
        return f  # si ffmpeg no pudo, suena el original antes que nada
    tmp.replace(destino)
    return destino


def editar_sonido(pieza, accion, t, cat, sid=None, cat_nuevo=None, t_nuevo=None, desde=None, largo=None, vol=None, vel=None):
    """Sonido puesto o quitado a mano en una pieza (vale para todos los videos que la usan). t = segundo dentro de la pieza.
    accion: agregar | quitar | cambiar (quita el que había en ese segundo y pone el nuevo) | mover (a t_nuevo) |
    ajustar (mover y/o recortar: t_nuevo, desde, largo, vol). t es el segundo con el que la sala conoce ese sonido
    (para los automáticos, el de su marca). Devuelve los videos a re-armar."""
    todo = ajustes_piezas()
    if "@" in pieza and pieza not in todo:  # primer cambio en esta edición: parte de lo que tiene la pieza en la A
        todo[pieza] = json.loads(json.dumps(todo.get(pieza.split("@")[0], {})))
    a = todo.setdefault(pieza, {})
    t = round(float(t), 2)
    manuales = a.setdefault("sonidos", [])
    if accion in ("mover", "ajustar"):  # se quita de donde estaba y se pone el mismo sonido (recortado) en el segundo nuevo
        editar_sonido(pieza, "quitar", t, cat)
        return editar_sonido(pieza, "agregar", max(0.0, float(t if t_nuevo is None else t_nuevo)), cat,
                             sid if sid and ruta_sonido(sid) else None, desde=desde, largo=largo, vol=vol, vel=vel)
    if accion in ("quitar", "cambiar"):
        antes = len(manuales)
        manuales[:] = [x for x in manuales if not (abs(x["t"] - t) < 0.2 and x["cat"] == cat)]
        if len(manuales) == antes:  # era un sonido automático: se recuerda que no va
            a.setdefault("sonidos_quitar", []).append({"t": t, "cat": cat})
    if accion in ("agregar", "cambiar"):
        if sid and not ruta_sonido(sid):
            raise ValueError("Ese sonido no está en la biblioteca")
        extra = {k: round(float(v), 3) for k, v in (("desde", desde), ("largo", largo), ("vol", vol), ("vel", vel)) if v not in (None, "")}
        if extra.get("vel") == 1.0:
            extra.pop("vel")
        manuales.append({"t": t, "cat": cat_nuevo or cat, **({"archivo": sid} if sid else {}), **extra})
    a["editado"] = time.time()
    todo[pieza] = {k: v for k, v in a.items() if v not in ([], None)}
    S.subs.mkdir(parents=True, exist_ok=True)
    _ajustes_piezas_path().write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    nombre, e = (pieza.split("@") + ["A"])[:2]
    return [c["codigo"] for c in combinaciones() if nombre in (c["hook"], c["cuerpo"], c["cta"]) and letra_edicion(c) == e]


def largo_audible(f):
    """Cuánto suena de verdad un archivo (del primer al último punto fuerte), sin el silencio que trae alrededor."""
    try:
        picos = onda_archivo(f, 100)["picos"]
    except Exception:  # noqa: BLE001
        return None
    fuertes = [i for i, v in enumerate(picos) if v >= 0.15]
    return (fuertes[-1] - fuertes[0]) / 100 if fuertes else 0.0


def elegir_sfx(categoria, semilla):
    """Rota entre las variantes de la categoría para que no suenen iguales en todos los videos.
    En las categorías con tope de largo (config sonidos.largo_audible_maximo: los whoosh de los cortes) solo entran
    los archivos rápidos: la biblioteca también trae whoosh en cámara lenta de varios segundos que arrastran el corte."""
    topes = CONFIG["sonidos"].get("largo_audible_maximo", {})
    tope_graves = float(CONFIG["sonidos"].get("graves_maximo", 0.6))
    con_graves = set(CONFIG["sonidos"].get("categorias_con_graves", ["impacto", "tension", "drama", "riser_largo"]))
    for cat in [categoria] + CONFIG["sonidos"].get("respaldo", {}).get(categoria, []):
        opciones = sfx_de(cat)
        if cat in topes:
            rapidos = [f for f in opciones if 0.12 <= (largo_audible(f) or 0) <= float(topes[cat])]
            opciones = rapidos or opciones
        if cat not in con_graves:
            # fuera los que son casi puro bajo (whoosh «slow motion», animaciones de logo): en el celular suenan
            # como un contrabajo o un retumbe, no como el aire de un whoosh
            claros = [f for f in opciones if parte_grave(f) <= tope_graves]
            opciones = claros or opciones
        if opciones:
            k = int(hashlib.md5(semilla.encode()).hexdigest(), 16) % len(opciones)
            return opciones[k]
    return None


_dur_cache = {}


def dur_audio(f):
    if f not in _dur_cache:
        _dur_cache[f] = duracion(f)
    return _dur_cache[f]


def es_numero(w):
    n = norm(w)
    return n.isdigit() or n in {"dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez"}


def tipo_hook(nombre, texto=None):
    """Tipo de un hook (promesa, pregunta, numero, contra, producto): el del guion, o si el guion no lo dice (el que
    sale de lo grabado dice solo «hook»), lo que se deduce de lo que se dice: «Deja de…», «…no es la solución» → contra;
    un número al empezar → numero; termina en «?» → pregunta."""
    t = norm(info_pieza(nombre).get("tipo", "") or "")
    if t and t not in ("hook", "default"):
        return t
    if texto is None:
        texto = " ".join(w["w"] for ln in leer_subs(nombre)["lineas"] for w in ln["palabras"]) or info_pieza(nombre).get("texto", "")
    k = [norm(w) for w in str(texto).split() if norm(w)]
    junto = " ".join(k)
    if k[:2] in (["deja", "de"], ["dejen", "de"]) or k[:1] in (["nunca"], ["olvida"], ["olvidate"])             or any(x in junto for x in ("no es la solucion", "no funciona", "estas haciendo mal", "error que", "no viene de")):
        return "contra"
    if any(es_numero(w) for w in k[:3]):
        return "numero"
    if str(texto).strip().endswith("?"):
        return "pregunta"
    return "default"


def planear_sonidos(combo, offs, total, subs, marcas):
    """Decide qué efecto suena y cuándo. Prioridad 3 = siempre; 1-2 = solo si no hay otro muy cerca."""
    so = CONFIG["sonidos"]
    cand = []
    hook, cuerpo, cta = combo["hook"], combo["cuerpo"], combo["cta"]

    def add(cat, t, prio, motivo, pegar=False):
        # pegar: si ese segundo se corta (el silencio antes de la voz), suena donde sigue el video en vez de perderse
        # cat también puede ser un sonido exacto («b:…» o «p:…», elegido en la galería): suena ese archivo
        exacto = ruta_sonido(cat) and {"archivo": cat, "cat": categoria_de_sonido(cat)}
        cand.append({"cat": cat, "t": max(0.0, t), "prio": prio, "motivo": motivo, **({"pegar": True} if pegar else {}), **(exacto or {})})

    # Arranque del hook según su tipo (Promesa, Pregunta, Número, Contra, Producto): un solo golpe, justo donde
    # entra la voz (si el hook arranca con silencio, ese pedazo se corta y el golpe cae en la primera sílaba)
    tipo = tipo_hook(hook)
    arranque = so["arranque_por_tipo"].get(tipo) or so["arranque_por_tipo"]["default"]
    for k, cat in enumerate(arranque):
        add(cat, 0.0 + 0.05 * k, 3, f"arranque hook ({tipo})", pegar=True)
    # Riser: solo en los hooks que dejan una pregunta en el aire (config sonidos.riser_en_hooks) y solo en su
    # último tramo; en un hook que se habla de corrido tapa la voz y se suma al golpe y al whoosh sin aportar nada
    if tipo in so.get("riser_en_hooks", ["pregunta"]):
        add("riser", offs[1], 3, f"riser antes del cuerpo (hook {tipo})")
    v_ed = edicion_de(combo)
    # «Magic reveal para revelar algo»: un hook que termina mostrando el producto («…sería esto», «esto sí») revela
    # con brillo en ese «esto», y ese brillo hace de paso al cuerpo (no se suma un whoosh encima)
    pal_hook = [w for ln in subs[0] for w in ln["palabras"]]
    revela = next((w for w in reversed(pal_hook[-4:]) if norm(w["w"]) == "esto"), None) if so.get("revelar_en_hook", True) else None
    if revela:
        add("revelar", revela["s"] - 0.05, 3, f"revela el producto en «{revela['w']}»")
    trs = transiciones_de(combo, offs)
    t_cta = offs[2] + float(((v_ed.get("recortes") or {}).get("cta") or [0])[0])
    if trs:  # cada transición suena con lo suyo: barrido → whoosh, destello → obturador, sacudida → golpe…
        for t, tipo in trs:
            if revela and abs(t - offs[1]) < 1.5:
                continue  # el brillo del «esto» ya hace de paso al cuerpo
            cat = SONIDO_TRANSICION.get(tipo, "whoosh")
            if abs(t - t_cta) < 0.01:
                cat = so.get("transicion_cta", "camara") if tipo == "flash" else cat
            antes = 0.03 if cat in ("camara", "impacto", "glitch") else 0.15  # el whoosh sube antes del corte
            add(cat, t - antes, 3, f"transición {NOMBRES_TRANSICION.get(tipo, tipo)}", pegar=True)
    if not revela and not any(abs(t - offs[1]) < 1.5 for t, _ in trs):
        add("whoosh", offs[1] - 0.2, 3, "corte hook→cuerpo", pegar=True)
    if not trs:
        # «Camera shutter para hacer transiciones»: el paso al CTA suena a foto (con un destello blanco en la imagen)
        add(so.get("transicion_cta", "camara"), t_cta - 0.03, 3, "transición al CTA", pegar=True)
    # «Whoosh para hacer zoom in y zoom out»: un whoosh corto donde salta o empieza el zoom de una frase
    partes_c = {p["i"]: p for p in partes_de(cuerpo)}
    rec_p = (v_ed.get("recortes") or {}).get("partes") or {}
    for i, zz in ((v_ed.get("zoom") or {}).get("partes") or {}).items():
        p = partes_c.get(int(i))
        if p and zz and int(i) not in (v_ed.get("quitar") or []):
            add("zoom", offs[1] + p["ini"] + float((rec_p.get(str(i)) or [0])[0]) - 0.08, 2, f"zoom {zz.get('tipo', '')} en la frase {int(i) + 1}", pegar=True)
    for k_pz, (pz, off) in enumerate((("hook", 0.0), ("cuerpo", offs[1]), ("cta", offs[2]))):
        pts = sorted(((v_ed.get("zoom") or {}).get("puntos") or {}).get(pz) or [], key=lambda x: x.get("t", 0))
        for k1, k2 in zip(pts, pts[1:]):
            if abs(float(k2.get("s", 1)) - float(k1.get("s", 1))) >= 0.08:  # se acerca o se aleja de verdad
                t_mov = k1["t"] if k1.get("curva") != "golpe" else k2["t"]
                add("zoom", off + float(t_mov) - 0.05, 2, f"zoom en {pz}", pegar=True)
    # CTA: notificación (anuncio) o tecleo (orgánico)
    # sin tipo en el guion: por lo que se dice (escribir por WhatsApp → notificación; comentar → tecleo)
    dicho_cta = {norm(w["w"]) for ln in leer_subs(cta)["lineas"] for w in ln["palabras"]}
    tipo_cta = norm(info_pieza(cta).get("tipo", ""))
    if tipo_cta not in so["cta_por_tipo"]:  # el guion sacado de lo grabado dice solo «cta»: no es un tipo de CTA
        tipo_cta = ""
    tipo_cta = tipo_cta or ("anuncio" if dicho_cta & {"whatsapp", "escribenos", "mensaje"}
                                                         else "organico" if dicho_cta & {"comenta", "comentanos"}
                                                         else "anuncio" if cta.upper().endswith("A") else "organico")
    cat_cta = so["cta_por_tipo"].get(tipo_cta)
    if cat_cta:
        add(cat_cta, offs[2] + 0.3, 3, f"CTA {tipo_cta}", pegar=True)
    # Marcas del guion
    silencios = []
    for off, ms in zip(offs, marcas):
        for m in ms:
            t = off + m["t"]
            if m.get("sfx") == "corte_silencio":
                silencios.append((max(0, t - 0.55), t))
                add("impacto", t, 3, f"vuelve el sonido en «{m['palabra']}»")
            elif m.get("sfx"):
                add(m["sfx"], t - (0.15 if m.get("asset") else 0), 3, f"marca en «{m['palabra']}»")
            elif m.get("asset") and buscar_asset(m["asset"]):
                add("whoosh", t - 0.15, 2, f"entra {m['asset']}")
            if m.get("sub") in ("centro_grande", "palabra_sola") and not m.get("sfx"):
                add("impacto", t, 2, f"subtítulo grande «{m['palabra']}»")
            elif m.get("sub") == "resaltado" and not m.get("sfx"):
                add("click", t, 2, f"texto resaltado «{m['palabra']}»")
    # Situaciones: si se dice una palabra de la lista, su sonido (ingresos → caja registradora, ficha → papel…)
    situaciones = dict(CONFIG.get("sfx_palabras", {}))
    if produccion().get("publico") in ("duenos", "dueños"):
        situaciones.update(CONFIG.get("sfx_palabras_solo_duenos", {}))
    frases = {cat: [[norm(w) for w in f.split()] for f in fr] for cat, fr in situaciones.items()}
    for (off, lineas) in zip(offs, subs):
        pal = [p for ln in lineas for p in ln["palabras"]]
        normas = [norm(p["w"]) for p in pal]
        for cat, lista in frases.items():
            for f in lista:
                for k in range(len(normas) - len(f) + 1):
                    if normas[k:k + len(f)] == f:
                        add(cat, off + pal[k]["s"], 2, f"situación «{' '.join(p['w'] for p in pal[k:k + len(f)])}»")
    # Automáticos por palabra: producto, números, palabras clave
    marca_vista = False
    import perfil as _perfil
    for (off, lineas), nombre in zip(zip(offs, subs), (hook, cuerpo, cta)):
        for ln in lineas:
            ws = [q["w"] for q in ln["palabras"]]
            for i, p in enumerate(ln["palabras"]):
                t = off + p["s"]
                if not marca_vista and nombre == cuerpo and _perfil.es_marca(p["w"], ws[i + 1:]):
                    add("brillo", t, 2, f"aparece {p['w'].strip('.,;:?!¿¡«»')}")
                    marca_vista = True
                elif es_numero(p["w"]):
                    add("ding", t, 1, f"número «{p['w']}»")
                elif es_clave(p["w"]):
                    add(so.get("sonido_palabra_clave", "click"), t, 1, f"palabra clave «{p['w']}»")

    # Lo que el equipo cambió a mano en cada pieza (Sonidos del editor): se quitan los marcados y se suman los agregados
    a_mano = ajustes_piezas()
    for off, n in zip(offs, (hook, cuerpo, cta)):
        ap = ajustes_de_pieza(n, letra_edicion(combo), a_mano)
        for q in ap.get("sonidos_quitar", []):
            cand = [c for c in cand if not (c["cat"] == q["cat"] and abs(c["t"] - (off + q["t"])) < 0.25)]
        for sm in ap.get("sonidos", []):
            cand.append({"cat": sm["cat"], "t": off + sm["t"], "prio": 4, "motivo": "puesto a mano", "archivo": sm.get("archivo"),
                         "a_mano": {k: sm[k] for k in ("desde", "largo", "vol", "vel") if k in sm}})
    # Filtro de densidad: un efecto opcional no puede caer muy cerca de otro
    elegidos = []
    sep = so["separacion_minima"]
    for c in sorted(cand, key=lambda c: (-c["prio"], c["t"])):
        if c["t"] >= total - 0.1:
            continue
        if c["prio"] < 3 and any(abs(c["t"] - e["t"]) < sep for e in elegidos if e["cat"] != "riser"):
            continue
        if c["prio"] == 3 and c["motivo"].startswith("transición") and any(
                abs(c["t"] - e["t"]) < 0.35 and e["prio"] >= 3 for e in elegidos):
            continue  # ya suena algo justo ahí (una marca del guion, el CTA): no se apilan dos golpes
        f = (ruta_sonido(c["archivo"]) if c.get("archivo") else None) or elegir_sfx(c["cat"], f"{combo['codigo']}|{c['cat']}|{len(elegidos)}")
        if not f:
            continue
        maxd = so.get("duracion_maxima", {}).get(c["cat"], so.get("duracion_maxima", {}).get("default", 2.5))
        fuente, aj = f, ajustes_sonido(id_sonido(f))
        if aj:  # editado en la página Sonidos (más lento, con eco…): suena entero, como se ve allá
            maxd = max(maxd, 30.0)
        try:
            f = sonido_editado(f)  # con lo editado en la página Sonidos (velocidad, tono, eco…)
        except Exception:  # noqa: BLE001 — si no se pudo editar, va el original
            pass
        d = dur_audio(f)
        # el sonido entra justo donde empieza su onda, sin el silencio que trae el archivo al inicio
        # (salvo que en la página Sonidos se haya puesto a mano desde dónde empieza)
        desde = inicio_audible(f) if so.get("quitar_silencio_inicial", True) and "desde" not in aj else 0.0
        if desde >= d - 0.05:
            desde = 0.0
        t, largo = c["t"], min(d - desde, maxd)
        if c["cat"] == "riser" and c["prio"] < 4:
            # el riser automático termina en el corte y ocupa como mucho el último 40 % del hook; si es largo
            # se usa solo su final (la parte que más sube)
            largo = min(largo, max(0.6, 0.4 * offs[1]))
            desde = max(0.0, d - largo)
            t = max(0.0, c["t"] - largo)
        am = c.get("a_mano") or {}
        if "desde" in am:  # recortado a mano en el editor
            desde = max(0.0, min(float(am["desde"]), d - 0.05))
        if "largo" in am:
            largo = max(0.05, min(float(am["largo"]), d - desde))
        elegidos.append({**c, "t": round(t, 2), "tm": round(c["t"], 2), "archivo": f, "fuente": fuente, "id": id_sonido(fuente),
                         "desde": round(desde, 2), "largo": round(largo, 2), "dur_archivo": round(d, 2),
                         "vol": float(am.get("vol", 1.0)), "vol_sonido": float(aj.get("vol", 1.0)),
                         "vel": max(0.5, min(2.0, float(am.get("vel", 1.0))))})
    return sorted(elegidos, key=lambda e: e["t"]), silencios


def info_musica(mid):
    return next((m for m in produccion().get("musica", []) if m.get("id", "").lower() == str(mid).lower()), {})


def videos_que_usan(aid, tipo):
    """Códigos de los videos que usan esa música (tipo «musica») o esa imagen o clip de Flow (tipo «assets»):
    por el guion, por las marcas editadas en la sala o por la pista «Imágenes» del editor."""
    return videos_que_usan_varios([aid], tipo)[aid]


def videos_que_usan_varios(ids, tipo):
    """{id: [códigos de video]} para varios a la vez: recorre las piezas y los videos UNA sola vez. Con P05 (232 videos)
    preguntar foto por foto tardaba 20-28 s cada una y las 400 fotos de Flow entraban a 7 por minuto.
    Las capas puestas a mano salen de la edición guardada (_edicion_guardada): edicion_de() además calcula cortes y
    silencios, que aquí no hacen falta y eran lo lento."""
    ids_l = {str(i).lower(): i for i in ids}
    out = {i: [] for i in ids}
    if not ids_l:
        return out
    prod = produccion().get("piezas", {})
    usan = {}  # pieza → ids (en minúsculas) que usa
    for n in {p["nombre"] for p in piezas()}:
        ms = marcas_de(n) or []
        if tipo == "musica":
            hay = {(musica_de_pieza(n) or "").lower()} | {(m.get("musica") or "").lower() for m in ms}
        else:
            hay = {(m.get("asset") or "").lower() for m in ms}
        hay &= ids_l.keys()
        if hay:
            usan[n] = hay
    for c in combinaciones():
        hay = set().union(*(usan.get(c[k], set()) for k in ("hook", "cuerpo", "cta")))
        if tipo != "musica":
            hay |= {str(x.get("id", "")).lower() for cs in (_edicion_guardada(c).get("capas") or {}).values() for x in cs} & ids_l.keys()
        for i in hay:
            out[ids_l[i]].append(c["codigo"])
    return out


def buscar_musica(mid):
    """Pista de Suno por id: carpeta Musica de la sesión, luego la carpeta de música general."""
    for d in (S.musica, CONFIG.get("carpeta_musica"), BIBLIO / "musica", BIBLIO_BASE / "musica"):
        if d and Path(d).is_dir():
            for f in Path(d).iterdir():
                if f.stem.lower() == str(mid).lower() and f.suffix.lower() in EXT_AUDIO:
                    return f
    return None


# ---------- Música por estado de ánimo ----------
# Cada pieza (hook, cuerpo, CTA) lleva su «animo» en el guion y la sesión tiene una pista por ánimo. Como cada video
# junta un hook, un cuerpo y un CTA cualquiera, la música cambia justo donde cambia el ánimo (con fundido) y no en
# cada pieza. Todas con adrenalina (regla de la marca): cambia el color, nunca a algo suave.
_SIEMPRE = ("instrumental, no vocals, no lyrics, background music for voiceover, starts with full energy, no intro, "
            "steady energy, no sudden drops")
ANIMOS = {
    "tension": ("Tensión (el problema)", "Dark high-energy electronic trap, 135 BPM, heavy 808 sub-bass, punchy drums, tense "
                "minor-key synth stabs, suspenseful and driving"),
    "revelacion": ("Revelación (el dato, la ciencia)", "Driving modern electronic groove, 128 BPM, crisp punchy drums, bright "
                   "plucked synth arpeggios, deep bass, curious and intriguing forward momentum"),
    "solucion": ("Solución (el producto)", "Uplifting energetic EDM-pop, 128 BPM, four-on-the-floor kick, bright supersaw chords, "
                 "strong bassline, confident and optimistic, powerful drive"),
    "triunfo": ("Triunfo (el resultado)", "Euphoric high-energy electro house, 130 BPM, big punchy kick, soaring synth lead, "
                "pumping bass, triumphant and celebratory hype"),
    "urgencia": ("Urgencia (el llamado a actuar)", "Fast energetic drum and bass, 140 BPM, rolling breakbeat, deep reese bass, "
                 "urgent and propulsive, high adrenaline"),
}
EVITAR_MUSICA = "soft, acoustic, calm, gentle, warm, cozy, relaxed, lo-fi, ambient, piano ballad, vocals, singing"


def pista_de_animo(animo):
    """El id de la pista de la sesión para ese estado de ánimo (o None)."""
    if not animo:
        return None
    return next((m["id"] for m in produccion().get("musica") or [] if m.get("animo") == animo), None)


def musica_de_pieza(nombre):
    """La pista de una pieza: la que el guion le pone a mano, o la de su estado de ánimo."""
    info = info_pieza(nombre)
    return info.get("musica") or pista_de_animo(info.get("animo"))


def pista_para_animo(animo):
    """La entrada de `musica` del guion para un estado de ánimo, lista para generar en Flow Music."""
    nombre, prompt = ANIMOS[animo]
    mu = PERFIL.get("musica") or {}  # la marca puede cambiar el estilo de un ánimo o lo que se evita
    prompt = (mu.get("animos") or {}).get(animo) or prompt
    return {"id": f"MUS_{animo.upper()}", "animo": animo, "uso": nombre, "desde": 0,
            "prompt": {"herramienta": "Google Flow Music", "prompt": f"{prompt}, {_SIEMPRE}.", "evitar": mu.get("evitar") or EVITAR_MUSICA,
                       "duracion": "60 s"}}


def planear_musica(combo, offs, total, marcas, n_musica=0):
    """Tramos de música [{id, archivo, t0, t1, desde}]. Una pista por video (la del cuerpo); solo cambia donde
    el guion lo pide: el hook o el CTA con su propia pista, o una marca {"palabra", "musica"}."""
    hook, cuerpo, cta = combo["hook"], combo["cuerpo"], combo["cta"]
    mus_ed = ediciones().get(letra_edicion(combo), {}).get("musica") or {}
    if letra_edicion(combo) != "A" and mus_ed.get("modo") in ("otra", "pista"):
        # otra edición con su propia música: una sola pista para todo el video
        rotacion = pistas_musica()
        archivo = next((f for f in rotacion if f.name == mus_ed.get("pista")), None) if mus_ed.get("modo") == "pista" else None
        if not archivo and rotacion:  # «otra»: una distinta de la que usa la A, que rota con el video
            letra = max(1, EDICIONES_LETRAS.find(letra_edicion(combo)) + 1)
            archivo = rotacion[(n_musica + letra * max(1, len(rotacion) // 3)) % len(rotacion)]
        return ([{"id": None, "archivo": archivo, "t0": 0.0, "t1": total, "desde": 0.0}] if archivo else []), []
    base = (produccion().get("videos", {}).get(combo["codigo"], {}).get("musica")
            or musica_de_pieza(cuerpo) or produccion().get("musica_por_defecto"))
    # el hook y el CTA suenan con la pista de su estado de ánimo; si es el mismo que el del cuerpo, no cambia nada
    cambios = [(0.0, musica_de_pieza(hook) or base)]
    if base:
        cambios.append((offs[1], base))
    if musica_de_pieza(cta):
        cambios.append((offs[2], musica_de_pieza(cta)))
    for off, ms in zip(offs, marcas):
        cambios += [(off + m["t"], m["musica"]) for m in ms if m.get("musica")]
    cambios.sort(key=lambda x: x[0])
    tramos, faltan = [], []
    rotacion = pistas_musica()
    for t0, mid in cambios:
        if tramos and tramos[-1]["id"] == mid:
            continue
        archivo = buscar_musica(mid) if mid else None
        if mid and not archivo:
            faltan.append(mid)
        if not archivo and rotacion:  # sin la pista de Suno todavía: una de la carpeta general para no dejarlo mudo
            archivo = rotacion[n_musica % len(rotacion)]
        if not archivo:
            continue
        if tramos:
            tramos[-1]["t1"] = t0
        tramos.append({"id": mid, "archivo": archivo, "t0": t0, "t1": total,
                       "desde": float(info_musica(mid).get("desde", 0)) if mid else 0.0})
    return [t for t in tramos if t["t1"] - t["t0"] > 0.3], sorted(set(faltan))


def pistas_musica():
    for d in (CONFIG.get("carpeta_musica"), BIBLIO / "musica", BIBLIO_BASE / "musica"):
        if d and _audios(Path(d)):
            return _audios(Path(d))
    return []


# ---------- Imágenes y clips (b-roll) ----------

def filtro_broll(i, ov, fps=30):
    """Cadena de filtro para una imagen/clip que entra en ov['t'] y dura ov['dur'] (a los cuadros del video: 30 o 60)."""
    dur, t, modo = ov["dur"], ov["t"], ov.get("modo", "completa")
    vx, vy, va = VENTANA_DEFECTO[formato()]
    W, H, x, y = (*lienzo(), 0, 0) if modo == "completa" else (va, va, vx, vy)
    if modo != "completa" and ov.get("ancho"):  # ventana colocada a mano en el editor
        W = H = int(ov["ancho"]) // 2 * 2
        x, y = int(ov.get("x", x)), int(ov.get("y", y))
    n = max(1, int(dur * fps))
    if ov["es_imagen"]:
        mov = ov.get("mov", "zoom_in")
        # estilo «zoom_imagenes»: «subir» (lo de siempre desde la 1.57.8) = toda imagen se acerca despacio de principio a
        # fin, para llamar la atención; «alternar» = como dice el guion (una se acerca, la otra se aleja)
        if mov == "zoom_out" and estilo_edicion().get("zoom_imagenes", "subir") == "subir":
            mov = "zoom_in"
        # zoom lento (10 % en toda la duración). Se agranda 3 veces antes del zoompan para que el movimiento
        # sea suave (zoompan mueve de a pixel entero: en tamaño normal la imagen tiembla). La foto entra como UN
        # cuadro (sin -loop) y zoompan saca de él los n cuadros (d=n): así se agranda una sola vez y no en cada
        # cuadro (con -loop eran 45 agrandadas a 3240×5760 por foto). La imagen sale idéntica.
        z = {"zoom_in": f"1+0.10*on/{n}", "zoom_out": f"1.10-0.10*on/{n}"}.get(mov, "1")
        cadena = (f"[{i}:v]scale={W * 3}:{H * 3}:force_original_aspect_ratio=increase,"
                  f"crop={W * 3}:{H * 3},zoompan=z='{z}':x='trunc(iw/2-(iw/zoom/2))':y='trunc(ih/2-(ih/zoom/2))'"
                  f":d={n}:s={W}x{H}:fps={fps},setsar=1")
    else:
        cadena = f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1"
    if modo != "completa":
        cadena += f",pad={W + 16}:{H + 16}:8:8:white"
        x, y = x - 8, y - 8
    fd = max(0.05, min(float(estilo_edicion().get("fundido", 0.12)), dur / 3))  # «fundido»: entra y sale suave
    cadena += (f",format=yuva420p,fade=t=in:st=0:d={fd:.2f}:alpha=1"
               + (f",fade=t=out:st={max(0, dur - fd):.2f}:d={fd:.2f}:alpha=1" if fd > 0.15 else "")
               + f",setpts=PTS-STARTPTS+{t:.3f}/TB[o{i}]")
    return cadena, x, y


# ---------- Portada ----------


def _portadas_path():
    return S.subs / "_portadas.json"


def ajustes_portada(codigo):
    """Lo que el equipo eligió para la portada de ese video (cuadro, título, posición, logo)."""
    try:
        return json.loads(_portadas_path().read_text(encoding="utf-8")).get(codigo, {})
    except (FileNotFoundError, ValueError):
        return {}


def guardar_ajustes_portada(codigo, datos):
    try:
        todos = json.loads(_portadas_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        todos = {}
    limpio = {k: datos[k] for k in CAMPOS_PORTADA if k in datos}
    todos[codigo] = {**todos.get(codigo, {}), **limpio}
    S.subs.mkdir(parents=True, exist_ok=True)
    _portadas_path().write_text(json.dumps(todos, ensure_ascii=False, indent=1), encoding="utf-8")


def texto_portada(combo):
    t = ajustes_portada(combo["codigo"]).get("titulo")
    if t:
        return t
    p = produccion()
    v = p.get("videos", {}).get(combo["codigo"], {})
    t = v.get("portada") or info_pieza(combo["hook"]).get("portada")
    if not t:  # sin guion: la primera frase del hook, sin el saludo ("Estimado médico veterinario,")
        texto = " ".join(l["texto"] for l in leer_subs(combo["hook"])["lineas"])
        partes = [x.strip() for x in re.split(r"(?<=[,.?!:])\s+", texto) if x.strip()]
        while len(partes) > 1 and len(partes[0].split()) <= 4 and partes[0].endswith(","):
            partes.pop(0)
        frase = re.split(r"(?<=[.?!])\s", partes[0] if partes else texto)[0] if partes else texto
        palabras = frase.rstrip(",.").split()
        if len(palabras) > 9:  # larga: se corta en una coma (no a media frase); si no hay, en 7 palabras con «…»
            coma = max((i for i, w in enumerate(palabras[:9]) if w.endswith((",", ":", ";")) and i >= 2), default=None)
            t = " ".join(palabras[:coma + 1]).rstrip(",:;") if coma is not None else " ".join(palabras[:7]) + "…"
        else:
            t = " ".join(palabras)
    return t


def mejor_cuadro(hook):
    """El segundo del hook que mejor sirve de portada: el cuadro más nítido y quieto (sin mano movida ni parpadeo a
    medias), dentro de la parte con voz. Se calcula una vez por clip."""
    import numpy as np
    f = S.proc / f"{hook}.mp4"
    if not f.exists():
        return CONFIG["portada"]["segundo"]
    cache = S.proc / f"{hook}.portada.json"
    firma = f"{f.stat().st_size}-{int(f.stat().st_mtime)}"
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("firma") == firma:
            return d["segundo"]
    except (FileNotFoundError, ValueError):
        pass
    fps, w, h = 6, 90, 160
    crudo = subprocess.run(creationflags=_prioridad_baja(), args=[FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(f), "-vf", f"fps={fps},scale={w}:{h},format=gray",
                            "-f", "rawvideo", "-"], capture_output=True).stdout
    n = len(crudo) // (w * h)
    if n < 3:
        return CONFIG["portada"]["segundo"]
    cuadros = np.frombuffer(crudo[:n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)
    lineas = leer_subs(hook)["lineas"]
    ini = max(0.3, (lineas[0]["s"] if lineas else 0.3))
    fin = (lineas[-1]["e"] if lineas else n / fps) - 0.2
    mejor, puntos = None, -1.0
    for i in range(1, n - 1):
        t = i / fps
        if not (ini <= t <= fin):
            continue
        c = cuadros[i][: h * 2 // 3]  # la parte de arriba (la cara), no las manos sobre la mesa
        nitidez = float(np.var(np.diff(c, axis=0)) + np.var(np.diff(c, axis=1)))
        mov = float(np.mean(np.abs(cuadros[i] - cuadros[i - 1])) + np.mean(np.abs(cuadros[i + 1] - cuadros[i])))
        p = nitidez / (1.0 + mov * 0.8)
        if p > puntos:
            mejor, puntos = round(t, 2), p
    segundo = mejor if mejor is not None else CONFIG["portada"]["segundo"]
    cache.write_text(json.dumps({"firma": firma, "segundo": segundo}), encoding="utf-8")
    return segundo


def segundo_portada(combo):
    """El elegido a mano; si no, el mejor cuadro del hook (config portada.auto, sí por defecto). El segundo que trae el
    guion solo se usa sin auto: se escribió antes de grabar y no sabe qué cuadro sirve."""
    a = ajustes_portada(combo["codigo"])
    if a.get("segundo") is not None:
        return float(a["segundo"])
    if CONFIG["portada"].get("auto", True):
        return mejor_cuadro(combo["hook"])
    return float(info_pieza(combo["hook"]).get("portada_segundo", CONFIG["portada"]["segundo"]))


def cuadro(combo, segundo, destino, ancho=540):
    """Un cuadro del hook (sin título) para elegir la portada."""
    run([FFMPEG, "-hide_banner", "-y", "-ss", f"{float(segundo):.2f}", "-i", str(S.proc / f"{combo['hook']}.mp4"),
         "-frames:v", "1", "-vf", f"scale={ancho}:-2", "-q:v", "4", str(destino)])
    return destino


CAMPOS_PORTADA = ("segundo", "titulo", "posicion", "y", "x", "ancho_titulo", "logo", "logo_archivo", "logo_x", "logo_y", "logo_ancho", "oscurecer", "fuente",
                  "tamano", "color_texto", "color_acento", "borde", "color_borde", "caja", "color_caja", "fondo", "solo_imagen", "elementos")


def hacer_portada(combo, archivo_video=None, prueba=None, destino=None):
    """JPG del tamaño del video (1080x1920 o 1920x1080): un cuadro del hook (o una imagen propia) con el título y el logo, y los elementos que se agreguen.
    *palabra* entre asteriscos sale con el color de acento. prueba: ajustes sin guardar (para ver mientras se edita)."""
    S.portadas.mkdir(parents=True, exist_ok=True)
    trabajo = carpeta_trabajo()
    cfg = CONFIG["portada"]
    aj = {**ajustes_portada(combo["codigo"]), **(prueba or {})}
    import fuentes
    pl = plantilla_sub(combo)
    # la portada tiene su propio estilo (config portada.fuente / mayusculas): no cambia si el subtítulo es más sobrio
    try:
        fuente = fuentes.cargar(aj.get("fuente") or cfg.get("fuente") or pl["fuente"]).nombre
    except Exception:
        fuente = fuentes.cargar(pl["fuente"]).nombre
    acento = color_ass(aj.get("color_acento") or (pl.get("resaltador") or {}).get("color") or pl["color_activo"])
    blanco = color_ass(aj.get("color_texto") or "#FFFFFF")
    titulo = aj.get("titulo") or texto_portada(combo)
    if cfg.get("mayusculas", pl.get("mayusculas", True)) and not aj.get("fuente"):
        titulo = titulo.upper()
    else:  # en minúsculas, la primera letra va en mayúscula (también después de ¿ o ¡)
        titulo = re.sub(r"^([¿¡*\s]*)(\w)", lambda m: m.group(1) + m.group(2).upper(), titulo)
    titulo = re.sub(r"\*(.+?)\*", lambda m: f"{{\\c{acento}}}{limpiar(m.group(1))}{{\\c{blanco}}}", limpiar(titulo.replace("{", "")))
    W, H = lienzo()
    y = int(float(aj["y"])) if aj.get("y") not in (None, "") else POSICIONES_TITULO[formato()].get(aj.get("posicion", "centro"), H // 2)
    x = int(float(aj["x"])) if aj.get("x") not in (None, "") else W // 2  # el centro del título (se arrastra en la vista)
    # el ancho de la caja del título (se estira de los costados): el texto se acomoda en más o menos filas
    ancho_t = max(160, min(W, int(float(aj.get("ancho_titulo") or (W - 180)))))
    margen = max(0, (W - ancho_t) // 2)
    # dentro de lo que deja el formulario (Tamaño 50–220, Borde 0–30): 5000 o −20 rompían la imagen
    tam = max(50, min(220, int(float(aj.get("tamano") or cfg["tamano"]))))
    borde = max(0.0, min(30.0, float(aj["borde"]))) if aj.get("borde") not in (None, "") else 12
    if aj.get("caja"):  # caja de color detrás del título (estilo de borde 3: el borde se vuelve caja)
        estilo, contorno, sombra = 3, color_ass(aj.get("color_caja") or "#99D3DA"), 0
        borde = max(borde, 18)
    else:
        estilo, contorno, sombra = 1, color_ass(aj.get("color_borde") or pl.get("color_borde", "#000000")), 7
    cab = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: T,{fuente},{tam},{blanco},&H00FFFFFF&,{contorno},&H90000000&,0,0,0,0,100,100,0,0,{estilo},{borde},{sombra},5,90,90,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        "" if aj.get("solo_imagen") else f"Dialogue: 0,0:00:00.00,0:00:10.00,T,,{margen},{margen},0,,{{\\an5\\pos({x},{y})}}{titulo}",
    ])
    (trabajo / "portada.ass").write_text(cab + "\n", encoding="utf-8")
    salida = destino or S.portadas / f"{combo['archivo']}.jpg"
    oscuro = float(aj.get("oscurecer", 0))
    llenar = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    fondo = ruta_elemento(aj["fondo"]) if aj.get("fondo") else None
    if fondo:  # imagen propia de portada
        cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(fondo)]
    else:
        segundo = float(aj["segundo"]) if aj.get("segundo") is not None else segundo_portada(combo)
        cmd = [FFMPEG, "-hide_banner", "-y", "-ss", f"{segundo:.2f}", "-i", str(S.proc / f"{combo['hook']}.mp4")]
    f = [f"[0:v]{llenar}" + (f",drawbox=x=0:y=0:w=iw:h=ih:color=black@{oscuro}:t=fill" if oscuro > 0 else "") + "[base]"]
    if not fondo and (color := filtros_color(combo, trabajo, "base0", "base")):  # el mismo color (LUT) que el video
        f[0] = f[0].replace("[base]", "[base0]")
        f += color
    previo, k = "base", 1
    # elementos encima de la imagen (stickers, logos, fotos del producto) y debajo del título
    for el in aj.get("elementos") or []:
        arch = ruta_elemento(el.get("archivo", ""))
        if not arch:
            continue
        cmd += ["-i", str(arch)]
        ancho = max(8, int(float(el.get("ancho", 300))))
        opac = max(0.0, min(1.0, float(el.get("opacidad", 100)) / 100))
        f.append(f"[{k}:v]scale={ancho}:-1,format=rgba,colorchannelmixer=aa={opac:.2f}[e{k}]")
        f.append(f"[{previo}][e{k}]overlay={int(float(el.get('x', 0)))}:{int(float(el.get('y', 0)))}[p{k}]")
        previo, k = f"p{k}", k + 1
    f.append(f"[{previo}]subtitles=portada.ass:fontsdir=fuentes[t]")
    previo = "t"
    # el logo de la marca del equipo (_MARCA): el elegido para esta portada o el principal para fondo oscuro
    import marca as _marca
    logo = _marca.logo_para("oscuro", aj.get("logo_archivo")) or (BIBLIO_BASE / "marca" / "logo-blanco.png")
    if aj.get("logo", True) and logo.exists() and not aj.get("solo_imagen"):
        # el logo va arriba si el título está abajo o al centro, y abajo si el título está arriba
        ylogo = (1490 if y < 900 else 330) if H > W else (H - 190 if y < H / 2 else 70)
        if aj.get("logo_y") not in (None, ""):  # lo puso la persona (arrastrándolo en la vista)
            ylogo = int(float(aj["logo_y"]))
        ancho_logo = max(40, int(float(aj.get("logo_ancho") or 380)))
        xlogo = f"{int(float(aj['logo_x']))}" if aj.get("logo_x") not in (None, "") else "(W-w)/2"
        cmd += ["-i", str(logo)]
        f.append(f"[{k}:v]scale={ancho_logo}:-1,split[l][s];[s]colorchannelmixer=rr=0:gg=0:bb=0:aa=0.6,boxblur=8[sombra];"  # sombra suave
                 f"[t][sombra]overlay={xlogo}+3:{ylogo}+4[t2];[t2][l]overlay={xlogo}:{ylogo}[v]")
        previo = "v"
    run(cmd + ["-filter_complex", ";".join(f), "-map", f"[{previo}]", "-frames:v", "1", "-q:v", "2", str(salida)], cwd=trabajo)
    return salida


# ---------- Armar un video ----------

# ---------- Compresor del armado: la tarjeta de video si hay (3 veces más rápido), si no el procesador ----------
# El video armado es la copia de trabajo (la que se ve en la sala): al exportar se vuelve a comprimir con calidad final.
COMPRESOR_CPU = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
COMPRESORES_GPU = [("nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "22", "-b:v", "0", "-pix_fmt", "yuv420p"]),
                   ("qsv", ["-c:v", "h264_qsv", "-global_quality", "21", "-pix_fmt", "nv12"]),
                   ("amf", ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23", "-pix_fmt", "yuv420p"])]
_COMPRESOR = {}


def compresor_armado():
    """El compresor más rápido que funciona en esta PC (se prueba una vez con un video chiquito).
    Config «compresor_armado»: "cpu" para no usar la tarjeta de video, o "nvenc" / "qsv" / "amf" para forzar uno."""
    if _COMPRESOR.get("roto"):
        return COMPRESOR_CPU
    if "elegido" not in _COMPRESOR:
        pedido = CONFIG.get("compresor_armado") or "auto"
        elegido = COMPRESOR_CPU
        if pedido != "cpu":
            for nombre, args in COMPRESORES_GPU:
                if pedido not in ("auto", nombre):
                    continue
                prueba = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=0.3:r=30",
                                         *args, "-f", "null", "-"], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if prueba.returncode == 0:
                    elegido = args
                    break
        _COMPRESOR["elegido"] = elegido
    return _COMPRESOR["elegido"]


def n_musica_de(combo):
    """Qué pista de la rotación le toca a un video: sale de su lugar en la sesión, no del lote que se está armando.
    Antes era el número dentro del lote: el mismo video salía con otra canción si se armaba solo, con todos o al exportar."""
    if not CONFIG["musica"]["rotar_pistas"]:
        return 0
    codigos = [c["codigo"] for c in combinaciones()]
    return codigos.index(combo["codigo"]) if combo["codigo"] in codigos else 0


def armar_uno(combo, n_musica=None, extras=None):
    """Arma un video. Paso 1: imagen (zoom, imágenes, subtítulos) con la voz. Paso 2: cortes y velocidades.
    Paso 3: música y sonidos sobre el video ya cortado (así la música sigue de corrido y no se acelera).
    extras: carpeta donde dejar además los elementos por separado (sin subtítulos, voz, música, efectos, .srt)."""
    if n_musica is None:
        n_musica = n_musica_de(combo)
    for d in (S.salida, S.recetas):
        d.mkdir(parents=True, exist_ok=True)
    huella = huella_video(combo)  # al empezar: si algo cambia mientras se arma, la próxima vez se rehace
    trabajo = carpeta_trabajo()
    orden = [combo["hook"], combo["cuerpo"], combo["cta"]]
    if viejas := piezas_en_otro_formato(orden):
        raise RuntimeError(f"{', '.join(viejas)} sigue en el formato anterior: prepara las piezas otra vez antes de armar")
    archivos = [S.proc / f"{n}.mp4" for n in orden]
    falta = [Path(a).stem for a in archivos if not Path(a).exists()]
    if falta:
        raise ValueError(f"{', '.join(falta)} no está preparado en esta PC: toca «Analizar los clips» en Inicio y vuelve a armar.")
    durs = [duracion(a) for a in archivos]
    # cuadros por segundo del video: 60 si alguna de sus piezas es de 60 (las de 30 se igualan); las tres piezas pasan
    # por «fps» para que todo el video tenga cuadros parejos y los cortes caigan justo en un cuadro
    fps_v = max(fps_redondo(fps_de(a)) for a in archivos)
    offs = [0, durs[0], durs[0] + durs[1]]
    total = sum(durs)
    subs = [leer_subs(n)["lineas"] for n in orden]
    marcas, perdidas = [], []
    for n, lineas in zip(orden, subs):
        m, p = resolver_marcas(n, lineas)
        marcas.append(m)
        perdidas += p

    # Imágenes y clips de Flow
    overlays, faltan = [], []
    en_capas = {c["id"].lower() for cs in (edicion_de(combo).get("capas") or {}).values() for c in cs}
    for off, ms in zip(offs, marcas):
        for m in ms:
            if not m.get("asset") or m["asset"].lower() in en_capas:  # movida a mano en el editor: va como capa
                continue
            f = buscar_asset(m["asset"])
            if not f:
                faltan.append(m["asset"])
                continue
            t = max(0.0, off + m["t"] - 0.05)
            dur = min(float(m.get("dur", 1.5)), total - t - 0.05)
            if f.suffix.lower() in EXT_VID:
                dur = min(dur, duracion(f))
            if dur > 0.2:
                overlays.append({"asset": m["asset"], "archivo": f, "t": t, "dur": dur, "modo": m.get("modo", "completa"),
                                 "mov": m.get("mov", "zoom_in"), "es_imagen": f.suffix.lower() in EXT_IMG})
    # Pista «Imágenes y escenas» del editor: imágenes o clips puestos a mano en un segundo de cada pieza
    # se ponen de la pista de abajo a la de arriba: la de más arriba («Imágenes 3») queda encima, como en CapCut
    o_pieza = {"hook": offs[0], "cuerpo": offs[1], "cta": offs[2]}
    por_pista = sorted(((pz, c) for pz, capas in (edicion_de(combo).get("capas") or {}).items() for c in capas),
                       key=lambda x: x[1].get("pista", 0))
    for pz, c in por_pista:
        f = buscar_asset(c["id"])
        if not f:
            faltan.append(c["id"])
            continue
        t = o_pieza.get(pz, 0) + c["t"]
        dur = min(c["dur"], total - t - 0.05)
        if f.suffix.lower() in EXT_VID:
            dur = min(dur, duracion(f))
        if dur > 0.2:
            overlays.append({"asset": c["id"], "archivo": f, "t": t, "dur": dur, "modo": c["modo"], "mov": c["mov"],
                             "es_imagen": f.suffix.lower() in EXT_IMG, **{k: c[k] for k in ("x", "y", "ancho") if k in c}})
    tapas = [(o["t"], o["t"] + o["dur"]) for o in overlays if o["modo"] == "completa"]
    escribir_ass(list(zip(offs, subs, marcas)), trabajo / "subs.ass", tapas, combo)

    eventos, silencios = planear_sonidos(combo, offs, total, subs, marcas)
    tramos, faltan_musica = planear_musica(combo, offs, total, marcas, n_musica)
    mudo = ajustes_video(combo["codigo"]).get("silenciar") or {}  # pistas silenciadas en el editor de este video
    if mudo.get("sonidos"):
        eventos = []
    if mudo.get("musica"):
        tramos = []

    cmd = [FFMPEG, "-hide_banner", "-y"]
    for a in archivos:
        cmd += ["-i", str(a)]
    idx = 3  # la música y los sonidos entran en el paso 3
    f = [f"[{k}:v]fps={fps_v}[pv{k}]" for k in range(3)]
    # después de unir, otra vez «fps»: si la imagen de una pieza es un poco más corta que su sonido, la unión deja un
    # hueco; así se rellena con su último cuadro (si no, el zoom lo «cerraba» y la voz quedaba corrida el resto del video)
    f.append(f"[pv0][0:a][pv1][1:a][pv2][2:a]concat=n=3:v=1:a=1[vcu][ac];[vcu]fps={fps_v}[vc]")
    v_prev = "vc"
    zexpr = _expresion_zoom(combo, offs, total)
    if zexpr:  # zoom (antes de imágenes y subtítulos, que no se acercan); el punto de foco queda fijo en pantalla
        f.append(f"[vc]zoompan=z='{zexpr['z']}':x='{zexpr['x']}':y='{zexpr['y']}':d=1:s={'x'.join(map(str, lienzo()))}:fps={fps_v},setsar=1[vz]")
        v_prev = "vz"
    # color (LUT) y efecto de imagen: solo sobre lo grabado, no sobre las imágenes de Flow ni los subtítulos
    if color := filtros_color(combo, trabajo, v_prev, "vcol"):
        f += color
        v_prev = "vcol"
    for ov in overlays:
        if ov["es_imagen"]:
            cmd += ["-i", str(ov["archivo"])]  # un solo cuadro: filtro_broll lo alarga con zoompan (d=cuadros)
        else:
            cmd += ["-t", f"{ov['dur']:.2f}", "-i", str(ov["archivo"])]
        cadena, x, y = filtro_broll(idx, ov, fps_v)
        f.append(cadena)
        f.append(f"[{v_prev}][o{idx}]overlay={x}:{y}:eof_action=pass:enable='between(t,{ov['t']:.2f},{ov['t'] + ov['dur']:.2f})'[v{idx}]")
        v_prev = f"v{idx}"
        idx += 1
    # Elementos de la plantilla (logo, imágenes, GIF): encima del video y debajo del subtítulo, todo el video
    for el in _al_lienzo(plantilla_sub(combo)).get("elementos") or []:
        arch = ruta_elemento(el.get("archivo", ""))
        if not arch:
            continue
        if arch.suffix.lower() == ".gif":
            cmd += ["-ignore_loop", "0", "-t", f"{total:.2f}", "-i", str(arch)]
        else:
            cmd += ["-loop", "1", "-framerate", str(fps_v), "-t", f"{total:.2f}", "-i", str(arch)]
        ancho = max(8, int(float(el.get("ancho", 200))))
        opac = max(0.0, min(1.0, float(el.get("opacidad", 100)) / 100))
        f.append(f"[{idx}:v]scale={ancho}:-1,format=rgba,colorchannelmixer=aa={opac:.2f}[e{idx}]")
        f.append(f"[{v_prev}][e{idx}]overlay={int(float(el.get('x', 0)))}:{int(float(el.get('y', 0)))}:eof_action=pass[v{idx}]")
        v_prev = f"v{idx}"
        idx += 1
    # con «aleatoria» cada corte trae la suya; «zoom» y «sacudida» ya van dentro del zoom de arriba
    capas_t = _capas_transicion(transiciones_de(combo, offs))
    if capas_t:
        f.append(f"[{v_prev}]{','.join(capas_t)}[vtrans]")
        v_prev = "vtrans"
    if extras:
        f.append(f"[{v_prev}]split=2[vlimpio][vs]")
        f.append("[vs]subtitles=subs.ass:fontsdir=fuentes[v]")
        f.append("[ac]asplit=2[ac1][ac2]")
    else:
        f.append(f"[{v_prev}]subtitles=subs.ass:fontsdir=fuentes[v]")
    # cortes, frases quitadas y velocidades: en la misma pasada (antes era otra pasada que volvía a codificar todo
    # el video y tardaba lo mismo que el armado)
    rangos, velocidades = _rangos_a_quitar(combo, offs, total), _rangos_velocidad(combo, offs, total)
    aparte, dups = _global(combo, offs, "audio_aparte"), _global(combo, offs, "duplicados")
    bordes = [x[k] for x in aparte + dups for k in ("a", "b")]
    trozos, reordenado = _en_su_orden(combo, offs, _trozos(total, rangos, velocidades, bordes))
    trozos, duplicado = _con_duplicados(trozos, dups)
    trozos = _a_cuadros(trozos, fps_v)  # cada corte justo en un cuadro: la voz no se corre de la imagen
    salidas_v = [("v", "ac1" if extras else "ac")] + ([("vlimpio", "ac2")] if extras else [])
    if (rangos or velocidades or reordenado or aparte or duplicado) and trozos:
        for k, (vl, al) in enumerate(salidas_v):
            f += _filtro_tramos(vl, al, trozos, f"{vl}_c", f"{al}_c", pref=f"q{k}", aparte=aparte, fps=fps_v)
        salidas_v = [(f"{vl}_c", f"{al}_c") for vl, al in salidas_v]
    # paso 1: la imagen con la voz, ya cortada (sin música ni sonidos todavía); en .mkv con el audio sin comprimir
    base, limpio = trabajo / "base.mkv", trabajo / "limpio.mkv"
    grafo = ";".join(f)
    if len(grafo) > 20000:  # Windows corta la línea de comandos en ~32 000 letras: ffmpeg lo lee de un archivo
        (trabajo / "grafo.txt").write_text(grafo, encoding="utf-8")
        cmd += ["-/filter_complex", "grafo.txt"]
    else:
        cmd += ["-filter_complex", grafo]

    def con_salidas(comp):
        venc = [*comp, "-c:a", "pcm_s16le", "-t", f"{max(total, sum((b - a) / fac for a, b, fac in trozos)) + 0.05:.2f}"]  # con duplicados puede durar más que sin cortes
        return cmd + [x for (vl, al), destino in zip(salidas_v, (base, limpio))
                      for x in ("-map", f"[{vl}]", "-map", f"[{al}]", "-dn", "-map_chapters", "-1", *venc, str(destino))]
    comp = compresor_armado()
    try:
        run(con_salidas(comp), cwd=trabajo)
    except RuntimeError:
        if comp == COMPRESOR_CPU:
            raise
        run(con_salidas(COMPRESOR_CPU), cwd=trabajo)
        _COMPRESOR["roto"] = True  # la tarjeta de video falló (driver, demasiados videos a la vez…): sigue con el procesador
    mapa, total_ed = _mapa_tiempo(trozos) if trozos else ((lambda t, pegar=False: t), total)
    # los tiempos de la música y los sonidos pasan al video editado
    tramos_ed = []
    for tr in tramos:
        t0, t1 = mapa(tr["t0"], True), mapa(tr["t1"], True)
        if t1 - t0 > 0.3:
            tramos_ed.append({**tr, "t0": t0, "t1": t1})
    silencios_ed = [(mapa(a, True), mapa(b, True)) for a, b in silencios]
    silencios_ed = [(a, b) for a, b in silencios_ed if b - a > 0.05]
    eventos_ed = [{**e, "t": mapa(e["t"], e.get("pegar", False))} for e in eventos]
    eventos_ed = [e for e in eventos_ed if e["t"] is not None]
    _mezclar_audio(base, S.salida / f"{combo['archivo']}.mp4", tramos_ed, silencios_ed, eventos_ed, total_ed, extras,
                   sin_voz=bool(mudo.get("voz")), limpieza=LIMPIEZAS_VOZ.get(estilo_edicion(combo).get("limpieza_voz")))
    if extras:
        _elementos_por_separado(extras, limpio, combo, orden, offs, subs, mapa, overlays)
    total = duracion(S.salida / f"{combo['archivo']}.mp4")
    hacer_portada(combo)

    receta = {**combo, "duracion": round(total, 2), "fps": fps_v, "cortes": [round(o, 2) for o in offs[1:]],
              # dónde termina el hook y dónde empieza el CTA en el video editado
              "limites": [round(mapa(offs[1], True), 2), round(mapa(offs[2], True), 2)],
              "musica": " → ".join(f"{t['id'] or t['archivo'].stem} ({t['t0']:.1f} s)" for t in tramos_ed) or None,
              "faltan_musica": faltan_musica,
              # t: segundo del video sin recortes (lo usa el editor); te: segundo en el video armado (None si se cortó)
              # desde/largo/dur_archivo: sobre el sonido ya editado en la página Sonidos (el que sirve /sfx)
              "efectos": [{"sfx": f"{e['cat']}/{Path(e.get('fuente', e['archivo'])).name}", "cat": e["cat"],
                           "id": e.get("id") or id_sonido(e["archivo"]), "t": e["t"],
                           "tm": e.get("tm", e["t"]), "desde": e.get("desde", 0), "largo": e.get("largo"), "dur_archivo": e.get("dur_archivo"),
                           "vol": e.get("vol", 1.0), "vel": e.get("vel", 1.0),
                           "te": None if mapa(e["t"], e.get("pegar", False)) is None else round(mapa(e["t"], e.get("pegar", False)), 2),
                           "motivo": e["motivo"]} for e in eventos],
              "imagenes": [{"asset": o["asset"], "t": round(o["t"], 2), "te": round(mapa(o["t"], True), 2), "dur": round(o["dur"], 2),
                            "modo": o["modo"]} for o in overlays],
              # pistas de música en el video sin recortes, para escucharlas en la vista previa del editor
              "pistas": [{"t0": round(t["t0"], 2), "t1": round(t["t1"], 2), "desde": t["desde"], "archivo": str(t["archivo"]),
                          "id": t["id"], "vol": round(ganancia_musica(t["archivo"], t["desde"], t["t1"] - t["t0"]), 3)} for t in tramos],
              "volumen_musica": CONFIG["musica"]["volumen"], "silenciar": mudo,
              "faltan_assets": sorted(set(faltan)), "marcas_sin_encontrar": perdidas,
              "huella": None if extras else huella}  # con elementos por separado no se compara
    (S.recetas / f"{combo['codigo']}.json").write_text(json.dumps(receta, ensure_ascii=False, indent=1), encoding="utf-8")
    for v in (base, limpio):
        v.unlink(missing_ok=True)
    return receta


def _mezclar_audio(base, salida, tramos, silencios, eventos, total, extras=None, sin_voz=False, limpieza=None):
    """Paso 3 del armado: a la voz del video ya cortado se le suman la música (baja sola cuando se habla)
    y los sonidos. La imagen se copia tal cual. Con extras, deja también voz, música y efectos en .wav.
    sin_voz: la pista de voz silenciada (la música igual baja donde se habla).
    limpieza: filtros que limpian la voz (LIMPIEZAS_VOZ); después se vuelve a igualar su volumen."""
    cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(base)]
    idx = 1
    i_mus = []
    for tr in tramos:
        cmd += ["-stream_loop", "-1", "-i", str(tr["archivo"])]
        i_mus.append(idx)
        idx += 1
    f = []
    # la voz: limpieza (si hay), el compresor que empareja lo fuerte y lo suave, y un solo nivel parejo medido en
    # todo el video (dos pasadas)
    previos = ",".join(x for x in (limpieza, NIVELAR_VOZ) if x)
    f.append(f"[0:a]{previos},{filtro_volumen_parejo(base, previos)},aresample=48000[vlimpia]")
    voz_in = "[vlimpia]"
    salidas_voz = ["[voz]"] + (["[sc]"] if tramos else []) + (["[vozx]"] if extras else [])
    f.append(f"{voz_in}asplit={len(salidas_voz)}{''.join(salidas_voz)}" if len(salidas_voz) > 1 else f"{voz_in}anull[voz]")
    mezcla = ["[voz]"]
    if sin_voz:
        f.append("[voz]volume=0[vozm]")
        mezcla = ["[vozm]"]
    if tramos:
        cruce = 0.4  # segundos de fundido cuando cambia la música
        for k, (tr, i) in enumerate(zip(tramos, i_mus)):
            largo = tr["t1"] - tr["t0"] + (cruce if k < len(tramos) - 1 else 0)
            ms = int(tr["t0"] * 1000)
            f.append(f"[{i}:a]atrim=start={tr['desde']:.2f}:duration={largo:.2f},asetpts=N/SR/TB,"
                     f"aformat=sample_rates=48000:channel_layouts=stereo,volume={ganancia_musica(tr['archivo'], tr['desde'], largo):.3f},"
                     f"afade=t=in:st=0:d={0.05 if k == 0 else cruce},afade=t=out:st={max(0, largo - cruce):.2f}:d={cruce},"
                     f"adelay={ms}|{ms}[mt{k}]")
        juntar = "".join(f"[mt{k}]" for k in range(len(tramos)))
        f.append(f"{juntar}amix=inputs={len(tramos)}:normalize=0:duration=longest,atrim=0:{total:.2f}[mraw]"
                 if len(tramos) > 1 else f"[mt0]atrim=0:{total:.2f}[mraw]")
        mute = "".join(f",volume=0:enable='between(t,{a:.2f},{b:.2f})'" for a, b in silencios)
        f.append(f"[mraw]{mute[1:] or 'anull'}[m0]")
        # baja un poco donde se habla (antes bajaba tanto que bajo la voz no se oía nada)
        duck = "sidechaincompress=threshold=0.06:ratio=3:attack=20:release=300"
        if extras:
            f.append(f"[m0][sc]{duck},asplit=2[mus][musx]")
        else:
            f.append(f"[m0][sc]{duck}[mus]")
        mezcla.append("[mus]")
    vol = CONFIG["sonidos"]["volumen_sfx"]
    con_graves = set(CONFIG["sonidos"].get("categorias_con_graves", ["impacto", "tension", "drama", "riser_largo"]))
    sfx = []
    for k, e in enumerate(eventos):
        cmd += ["-i", str(e["archivo"])]
        ms = int(e["t"] * 1000)
        vk = (vol * CONFIG["sonidos"].get("volumen_por_categoria", {}).get(e["cat"], 1.0) * float(e.get("vol", 1.0))
              * float(e.get("vol_sonido", 1.0))  # el volumen del sonido editado en la página Sonidos
              # todos al mismo nivel (medido sobre el sonido ya editado: si se recortó o se invirtió, suena distinto
              # al archivo entero); encima van su tipo, el volumen editado y lo puesto a mano
              * ganancia_normalizada(e["archivo"]))
        vel = float(e.get("vel", 1.0))  # velocidad de este efecto en este video (la del sonido ya viene en el archivo)
        sale = e["largo"] / vel
        recorte = (f"atrim={e['desde']:.2f}:{e['desde'] + e['largo']:.2f},asetpts=N/SR/TB,"
                   + (f"{_atempo(vel)}," if abs(vel - 1) > 1e-3 else "")
                   + f"afade=t=out:st={max(0, sale - 0.15):.2f}:d=0.15,")
        bajos = "" if e["cat"] in con_graves else "highpass=f=110,"  # el bajo de un whoosh retumba en el celular
        f.append(f"[{idx}:a]{recorte}{bajos}aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms},volume={vk:.2f}"
                 + (f",asplit=2[s{k}][sx{k}]" if extras else f"[s{k}]"))
        mezcla.append(f"[s{k}]")
        sfx.append(f"[sx{k}]")
        idx += 1
    f.append(f"{''.join(mezcla)}amix=inputs={len(mezcla)}:normalize=0:duration=first,alimiter=limit=0.89:level=disabled[a]")
    cmd += ["-filter_complex", ";".join(f), "-map", "0:v", "-map", "[a]", "-dn", "-map_chapters", "-1", "-t", f"{total:.2f}",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(salida)]
    if extras:  # las pistas por separado, del largo del video
        extras.mkdir(parents=True, exist_ok=True)
        wav = ["-t", f"{total:.2f}", "-c:a", "pcm_s16le", "-ar", "48000"]
        cmd += ["-map", "[vozx]", *wav, str(extras / "voz.wav")]
        if tramos:
            cmd += ["-map", "[musx]", *wav, str(extras / "musica.wav")]
        if sfx:
            f2 = f"{''.join(sfx)}amix=inputs={len(sfx)}:normalize=0:duration=longest,apad[fx]" if len(sfx) > 1 else f"{sfx[0]}apad[fx]"
            cmd[cmd.index("-filter_complex") + 1] += ";" + f2
            cmd += ["-map", "[fx]", *wav, str(extras / "efectos.wav")]
    run(cmd)


def _t_srt(t):
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _elementos_por_separado(destino, limpio, combo, orden, offs, subs, mapa, overlays):
    """Video sin subtítulos, subtítulos en .srt (para CapCut o Premiere) y las imágenes que usa el video."""
    destino.mkdir(parents=True, exist_ok=True)
    run([FFMPEG, "-hide_banner", "-y", "-i", str(limpio), "-map", "0:v", "-map", "0:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(destino / "video_sin_subtitulos_solo_voz.mp4")])
    filas = []
    for off, lineas in zip(offs, subs):
        for ln in lineas:
            a, b = mapa(off + ln["s"]), mapa(off + ln["e"], True)
            a = mapa(off + ln["s"], True) if a is None else a
            if b - a > 0.05:
                filas.append((a, b, ln["texto"]))
    (destino / "subtitulos.srt").write_text(
        "".join(f"{k}\n{_t_srt(a)} --> {_t_srt(b)}\n{t}\n\n" for k, (a, b, t) in enumerate(filas, 1)), encoding="utf-8")
    if overlays:
        (destino / "imagenes").mkdir(exist_ok=True)
        for o in overlays:
            shutil.copy2(o["archivo"], destino / "imagenes" / o["archivo"].name)


def carpeta_de_exporte():
    """Nombre de la carpeta de cada exporte: la fecha de hoy y el nombre de la sesión («23-10-2026 P05 VETERINARIOS»).
    Siempre se exporta dentro de una carpeta así, aunque sea un solo video."""
    nombre = re.sub(r'[<>:"/\\|?*]+', " ", S.semana).strip() or "Sesión"
    return f"{time.strftime('%d-%m-%Y')} {nombre}"


def exportar_partes(codigos, avance=print, raiz=None):
    """«Exportar por partes»: cada video en su carpeta (<exporte>/POR_PARTES/<video>) con el video completo, el hook,
    el cuerpo y el CTA sueltos, el video sin subtítulos, voz, música y efectos en .wav, los subtítulos en .srt
    y las imágenes que usa. Sirve para terminarlo en CapCut, Premiere o pasarlo a otro editor."""
    por_codigo = {c["codigo"]: c for c in combinaciones()}
    hechos = []
    codigos = list(dict.fromkeys(codigos))  # sin repetidos
    for n, cod in enumerate(codigos):
        c = por_codigo.get(cod)
        if not c:
            continue
        destino = Path(raiz or S.listos / carpeta_de_exporte()) / "POR_PARTES" / c["archivo"]
        if destino.exists():
            shutil.rmtree(destino)
        avance(f"Exportando por partes {c['archivo']} ({n + 1}/{len(codigos)})")
        r = armar_uno(c, extras=destino)
        video = S.salida / f"{c['archivo']}.mp4"
        shutil.copy2(video, destino / "video_completo.mp4")
        a, b = r["limites"]
        for nombre, desde, hasta in (("1_hook", 0.0, a), ("2_cuerpo", a, b), ("3_cta", b, r["duracion"])):
            if hasta - desde > 0.2:
                run([FFMPEG, "-hide_banner", "-y", "-i", str(video), "-ss", f"{desde:.3f}", "-to", f"{hasta:.3f}",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                     str(destino / f"{nombre}_{c[nombre[2:]]}.mp4")])
        portada = S.portadas / f"{c['archivo']}.jpg"
        if portada.exists():
            shutil.copy2(portada, destino / "portada.jpg")
        cp = copy_de(cod)
        (destino / "copy.txt").write_text(
            f"INSTAGRAM\n{cp.get('instagram', '')}\n\nTIKTOK\n{cp.get('tiktok', '')}\n\n"
            f"YOUTUBE · título\n{cp.get('youtube_titulo', '')}\n\nYOUTUBE · descripción\n{cp.get('youtube', '')}\n", encoding="utf-8")
        hechos.append(cod)
    return hechos


def piezas_en_otro_formato(nombres=None):
    """Piezas ya preparadas con el tamaño del otro formato (se cambió el formato de la sesión después)."""
    out = []
    for n in nombres or [p["nombre"] for p in piezas()]:
        if not (S.proc / f"{n}.mp4").exists():
            continue
        try:  # sin anotación (preparada antes de que existiera el 16:9): es vertical
            f = json.loads((S.proc / f"{n}.partes.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            f = {}
        if (f.get("formato", "vertical") if isinstance(f, dict) else "vertical") != formato():
            out.append(n)
    return out


# ---------- Armar solo lo que cambió ----------
# Cada video guarda en su receta una «huella» de todo lo que usa: sus tres piezas y sus subtítulos, lo que dice el
# guion de ellas (texto, marcas, ánimo, música), sus recortes y ajustes, las fotos, clips y pistas que usa, la plantilla,
# el estilo de su edición y los sonidos. «Armar todos los videos» salta los que tienen la huella igual: cambiar una foto
# o una pista ya no rehace los 232 videos, solo los que la usan. Si cambia cómo se arma, se sube ARMADO.
ARMADO = 3  # 2: voz nivelada y música de fondo · 3: las imágenes siempre se acercan despacio
_HUELLA_SFX = {"t": 0, "v": None}


def _estado_archivo(f):
    try:
        st = os.stat(f)
        return [st.st_size, st.st_mtime_ns]
    except (OSError, TypeError):
        return None


def _huella_biblioteca_sfx():
    """Cambia si se agrega, quita o edita un sonido de la biblioteca (se mira a lo sumo cada 30 s)."""
    if time.time() - _HUELLA_SFX["t"] > 30:
        partes = []
        for base in (BIBLIO / "sfx", BIBLIO_BASE / "sfx"):
            if base.is_dir():
                partes += [(d.name, d.stat().st_mtime_ns) for d in sorted(base.iterdir()) if d.is_dir()]
        _HUELLA_SFX.update(t=time.time(), v=partes)
    return _HUELLA_SFX["v"]


def huella_video(combo):
    """La huella de todo lo que entra en un video (ver arriba)."""
    piezas3 = [combo["hook"], combo["cuerpo"], combo["cta"]]
    infos = {n: info_pieza(n) for n in piezas3}
    ed = _edicion_guardada(combo)
    assets = sorted({m.get("asset") for i in infos.values() for m in i.get("marcas", []) if m.get("asset")}
                    | {str(x.get("id")) for cs in (ed.get("capas") or {}).values() for x in (cs or []) if x.get("id")})
    prod = produccion()
    musicas = sorted({x for x in [musica_de_pieza(n) for n in piezas3] + [prod.get("musica_por_defecto"),
                      (prod.get("videos") or {}).get(combo["codigo"], {}).get("musica")]
                      + [m.get("musica") for i in infos.values() for m in i.get("marcas", [])] if x})
    datos = {"armado": ARMADO, "lienzo": lienzo(),
             "piezas": {n: [_estado_archivo(S.proc / f"{n}.mp4"), _estado_archivo(S.subs / f"{n}.json")] for n in piezas3},
             "guion": infos, "edicion": ed, "editado": editado_en(combo),
             "assets": {a: _estado_archivo(buscar_asset(a)) for a in assets},
             "musica": {m: [_estado_archivo(buscar_musica(m)), info_musica(m)] for m in musicas},
             "musica_general": [f.name for f in pistas_musica()],
             "estilo": [ediciones().get(letra_edicion(combo)), estilo_edicion(combo), plantilla_sub(combo)],
             "video": [ajustes_video(combo["codigo"]), ajustes_portada(combo["codigo"]), combo.get("version")],
             "config": [CONFIG.get("sonidos"), CONFIG.get("musica"), CONFIG.get("sfx_mapa")], "sfx": _huella_biblioteca_sfx()}
    return hashlib.md5(json.dumps(datos, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def video_al_dia(combo):
    """True si el video ya está armado con todo lo que usa hoy (su huella no cambió). Un video armado antes de las
    huellas (1.57.5) se da por al día si es más nuevo que todo lo que usa, y desde ahí queda con su huella."""
    salida = S.salida / f"{combo['archivo']}.mp4"
    rec_f = S.recetas / f"{combo['codigo']}.json"
    if not salida.exists() or not rec_f.exists():
        return False
    try:
        rec = json.loads(rec_f.read_text(encoding="utf-8"))
    except ValueError:
        return False
    huella = huella_video(combo)
    if rec.get("huella"):
        return rec["huella"] == huella
    # sin huella: más nuevo que sus piezas, subtítulos, fotos, música, guion y ajustes
    usados = [S.proc / f"{n}.mp4" for n in (combo["hook"], combo["cuerpo"], combo["cta"])]
    usados += [S.subs / f"{n}.json" for n in (combo["hook"], combo["cuerpo"], combo["cta"])]
    usados += [buscar_asset(i["asset"]) for i in rec.get("imagenes", [])] + [p.get("archivo") for p in rec.get("pistas", [])]
    usados += [S.guion / "produccion.json"]
    t = max([editado_en(combo)] + [(_estado_archivo(f) or [0, 0])[1] / 1e9 for f in usados if f])
    if salida.stat().st_mtime <= t + 1:
        return False
    rec["huella"] = huella  # desde ahora se compara por huella
    rec_f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def armar(pieza=None, avance=print, codigos=None, solo_cambios=False):
    """Arma los videos (todos, los de una pieza o los códigos pedidos). Con solo_cambios («Armar todos los videos»)
    salta los que ya están al día (video_al_dia)."""
    hechos = []
    todos = [c for c in combinaciones() if (not pieza or pieza in (c["hook"], c["cuerpo"], c["cta"]))
             and (codigos is None or c["codigo"] in codigos)]
    if codigos:  # en el orden pedido (el video que se está editando va primero)
        orden = {c: k for k, c in enumerate(codigos)}
        todos.sort(key=lambda c: orden.get(c["codigo"], len(orden)))
    for n in sorted({x for c in todos for x in (c["hook"], c["cuerpo"], c["cta"])}):
        corregir_subtitulo(n)  # rápido: solo si cambió la tabla de correcciones; no toca lo corregido a mano
    if solo_cambios:
        avance("Revisando qué videos cambiaron")
        antes = len(todos)
        todos = [c for c in todos if not video_al_dia(c)]
        armar.saltados = antes - len(todos)
        if armar.saltados:
            avance(f"{armar.saltados} de {antes} videos ya están al día: se arman solo {len(todos)}")
        if not todos:
            return hechos
    else:
        armar.saltados = 0
    if piezas_en_otro_formato():  # se cambió el formato: primero las piezas con el tamaño nuevo
        avance("Preparando las piezas en el formato nuevo")
        procesar(avance=avance)
    elif any(not (S.proc / f"{n}.mp4").exists() for c in todos for n in (c["hook"], c["cuerpo"], c["cta"])):
        # una pieza que todavía no se preparó en esta PC (p. ej. sus subtítulos vinieron de otra): se prepara antes
        avance("Preparando las piezas que faltan")
        procesar(avance=avance)
    # un video que falla no frena a los demás (antes, en P05, uno con error cortó un lote de casi 2 horas): se anota,
    # se sigue y al final se dice cuál y por qué. «Cancelar» sí para todo.
    armar.fallidos = []

    def fallo(combo, e):
        import traceback
        print(f"No se pudo armar {combo['codigo']}:", flush=True)
        traceback.print_exception(e)
        armar.fallidos.append((combo["codigo"], f"{e}{donde_fallo(e)}"))
        avance(f"⚠ {combo['archivo']} no se pudo armar ({e}{donde_fallo(e)}). Sigo con los demás.")

    a_la_vez = min(armados_a_la_vez(), len(todos))
    if a_la_vez <= 1:
        for n, combo in enumerate(todos):
            avance(f"Armando {combo['archivo']} ({n + 1}/{len(todos)})")
            try:
                r = armar_uno(combo)
            except Cancelado:
                raise
            except Exception as e:  # noqa: BLE001
                fallo(combo, e)
                continue
            hechos.append(r["codigo"])
        return hechos
    # varios a la vez: el zoom, los subtítulos y las imágenes usan un solo núcleo del procesador por video,
    # así que dos o tres videos en paralelo aprovechan la PC sin pelearse
    cola, listos, errores, cerrojo = list(enumerate(todos)), {}, [], _hilos.Lock()
    es_trabajo = _hilos.get_ident() == HILO_TRABAJO["id"]  # si lo cancelan, se cancelan también los ayudantes

    def ayudante(slot):
        _LOCAL.slot = f"h{slot}"
        if es_trabajo:
            HILO_TRABAJO["ayudantes"].add(_hilos.get_ident())
        try:
            while True:
                with cerrojo:
                    if errores or not cola:
                        return
                    n, combo = cola.pop(0)
                    avance(f"Armando {combo['archivo']} ({n + 1}/{len(todos)}) · {a_la_vez} a la vez")
                try:
                    r = armar_uno(combo)
                except (Cancelado, KeyboardInterrupt, SystemExit) as e:  # cancelado: paran todos (no empiezan otros)
                    with cerrojo:
                        errores.append(e)
                    return
                except Exception as e:  # noqa: BLE001 — un video con error: se anota y se sigue con el próximo
                    with cerrojo:
                        fallo(combo, e)
                    continue
                with cerrojo:
                    listos[n] = r["codigo"]
        finally:
            HILO_TRABAJO["ayudantes"].discard(_hilos.get_ident())

    hilos = [_hilos.Thread(target=ayudante, args=(k,), daemon=True) for k in range(a_la_vez)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    if errores:
        raise errores[0]
    return [listos[k] for k in sorted(listos)]


def donde_fallo(e):
    """Para un error del programa (no un aviso para la persona): « · motor.py línea 7406, armar_uno». Así, con solo
    ver Procesos se sabe dónde arreglarlo (el detalle completo queda en datos/sala.log de esa PC)."""
    if isinstance(e, (ValueError, RuntimeError, Cancelado, OSError)) and not isinstance(e, (IndexError, KeyError)):
        return ""
    import traceback
    pila = [f for f in traceback.extract_tb(e.__traceback__) if f.filename.endswith(".py")]
    if not pila:
        return ""
    f = pila[-1]
    return f" · {Path(f.filename).name} línea {f.lineno}, {f.name}"


def armados_a_la_vez():
    """Cuántos videos se arman a la vez: config «armar_a_la_vez» o, si no, según los núcleos de la PC (2 con 6 o más, 3 con 12 o más)."""
    n = CONFIG.get("armar_a_la_vez")
    if n:
        return max(1, min(4, int(n)))
    nucleos = os.cpu_count() or 2
    return 3 if nucleos >= 12 else 2 if nucleos >= 6 else 1


def rehacer_portadas(avance=print):
    for c in combinaciones():
        if (S.proc / f"{c['hook']}.mp4").exists():
            avance(f"Portada {c['archivo']}")
            hacer_portada(c)


# ---------- Copies y calendario ----------

def copy_de(codigo):
    """Copy por red: primera línea según el hook, cuerpo base, cierre según el CTA, hashtags."""
    c = next((x for x in combinaciones() if x["codigo"] == codigo), None)
    if not c:
        return {}
    editados = json.loads(S.copies_editados.read_text(encoding="utf-8")) if S.copies_editados.exists() else {}
    cp = produccion().get("copies", {})
    cuerpo_f, hook_f, cta_f = (sin_angulo(c[k])[0] for k in ("cuerpo", "hook", "cta"))  # el de otro ángulo, como el de frente
    base = cp.get("cuerpos", {}).get(cuerpo_f, {})
    gancho = cp.get("hooks", {}).get(hook_f, "")
    cta_id = cta_f if cta_f in cp.get("ctas", {}) else cta_f.split("_")[-1]
    cierre = cp.get("ctas", {}).get(cta_id, "")
    tags = base.get("hashtags", "")
    texto = "\n\n".join(x for x in (gancho, base.get("texto", ""), cierre) if x)
    out = {
        "instagram": "\n\n".join(x for x in (texto, tags) if x),
        "tiktok": "\n\n".join(x for x in (texto, tags) if x),
        "youtube_titulo": cp.get("youtube_titulos", {}).get(c["hook"]) or gancho[:95],
        "youtube": "\n\n".join(x for x in (texto, tags + " #shorts" if tags else "#shorts") if x),
    }
    out.update(editados.get(codigo, {}))
    return out


def guardar_copy(codigo, cambios):
    S.copies_editados.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(S.copies_editados.read_text(encoding="utf-8")) if S.copies_editados.exists() else {}
    d.setdefault(codigo, {}).update(cambios)
    S.copies_editados.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def calendario():
    """{'DIA_1': [codigo, ...]}. Usa el del guion; si no hay, rotación de cuadrado latino con el CTA orgánico."""
    cal = produccion().get("calendario")
    if cal:
        return cal
    combos = list(combinaciones())
    cuerpos = sorted({c["cuerpo"] for c in combos})
    pref = CONFIG.get("cta_organico", "CTA-O")
    out = {}
    hooks_de = {cu: sorted({c["hook"] for c in combos if c["cuerpo"] == cu}) for cu in cuerpos}
    ndias = max((len(h) for h in hooks_de.values()), default=0)
    for d in range(ndias):
        dia = []
        for n, cu in enumerate(cuerpos):
            hs = hooks_de[cu]
            h = hs[(n + d) % len(hs)]
            opciones = [c for c in combos if c["hook"] == h]
            elegido = next((c for c in opciones if c["cta"].endswith(pref)), opciones[-1] if opciones else None)
            if elegido:
                dia.append(elegido["codigo"])
        out[f"DIA_{d + 1}"] = dia
    return out


# «cuadros»: 0 = los del video (30 o 60). Reemplaza a «fps», que siempre quedaba en 30 (el «recomendado» de antes):
# un video de 60 se exportaba a 30 y perdía la mitad de sus cuadros
AJUSTES_EXPORTAR = {"resolucion": "1080", "fps": 30, "cuadros": 0, "codec": "h264", "calidad": "recomendada", "portada_inicio": True,
                    "por_dia": False, "peso_max": 0}  # MB por video; 0 = sin límite
RESOLUCIONES = {"720": (720, 1280), "1080": (1080, 1920), "1440": (1440, 2560), "2160": (2160, 3840)}
CALIDADES = {"ligera": 26, "recomendada": 22, "alta": 19, "maxima": 16}  # CRF: menos = mejor y más pesado


def ajustes_exportar():
    return {**AJUSTES_EXPORTAR, **CONFIG.get("exportar", {})}


def render_final(video, portada, destino, aj, avance=print):
    """Exporta con la calidad elegida. Con «peso_max» (MB), si el video sale más pesado se vuelve a exportar
    con el bitrate justo para que quede por debajo de ese peso (y si ni así entra, se avisa). Se exporta a un
    archivo temporal y recién al final toma su nombre: si se cancela o se apaga la PC no queda un video cortado."""
    tmp = destino.with_name(f".{destino.stem}.exportando{destino.suffix}")
    try:
        _render_final(video, portada, tmp, aj)
        limite = float(aj.get("peso_max") or 0)
        if limite > 0 and tmp.stat().st_size > limite * 1048576:
            dur = max(1.0, duracion(tmp))
            kbps = int((limite * 0.94 * 8 * 1024) / dur - 192)  # deja margen para el contenedor y el audio de 192 kbps
            _render_final(video, portada, tmp, {**aj, "bitrate": max(400, kbps)})
            mb = tmp.stat().st_size / 1048576
            if mb > limite:
                avance(f"Ojo: {destino.name} quedó en {mb:.1f} MB y el límite era {limite:g} MB (es muy largo para ese peso)")
        os.replace(tmp, destino)
    finally:
        tmp.unlink(missing_ok=True)


def _render_final(video, portada, destino, aj):
    """Video final para subir a redes con los ajustes de exportación. Con «portada_inicio», el primer cuadro del video
    es la portada (Instagram y TikTok la toman como miniatura si eliges el primer cuadro)."""
    w, h = RESOLUCIONES.get(str(aj["resolucion"]), (1080, 1920))
    if formato() == "horizontal":  # las resoluciones se nombran por el lado corto: 1080p en 16:9 es 1920×1080
        w, h = h, w
    fps = int(aj.get("cuadros") or 0) or fps_redondo(fps_de(video))
    crf = CALIDADES.get(aj.get("calidad"), 19)
    venc = (["-c:v", "libx265", "-tag:v", "hvc1", "-crf", str(crf + 4)] if aj.get("codec") == "hevc"
            else ["-c:v", "libx264", "-crf", str(crf), "-profile:v", "high"])
    if aj.get("bitrate"):  # peso máximo: bitrate fijo con tope (una pasada con maxrate es precisa y rápida)
        b = int(aj["bitrate"])
        venc = [*venc[:2], *(["-tag:v", "hvc1"] if aj.get("codec") == "hevc" else ["-profile:v", "high"]),
                "-b:v", f"{b}k", "-maxrate", f"{int(b * 1.15)}k", "-bufsize", f"{b * 2}k"]
    escala = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps}"
    if aj.get("portada_inicio") and portada and portada.exists():
        cuadro = 1 / fps
        ms = round(cuadro * 1000)
        cmd = [FFMPEG, "-hide_banner", "-y", "-loop", "1", "-framerate", str(fps), "-t", f"{cuadro:.4f}", "-i", str(portada), "-i", str(video),
               # la portada (JPG) es de rango completo: se pasa al rango de video (tv) para que no contagie su marca a todo el video
               "-filter_complex", f"[0:v]{escala},scale=out_range=tv:out_color_matrix=bt709,format=yuv420p[p];[1:v]{escala},format=yuv420p[v];[p][v]concat=n=2:v=1:a=0[vo];"
                                  f"[1:a]adelay={ms}:all=1,aresample=48000[ao]",
               "-map", "[vo]", "-map", "[ao]"]
    else:
        cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(video), "-vf", f"{escala},format=yuv420p", "-af", "aresample=48000"]
    # colores marcados como video normal (rango tv, bt709): si no, algunos reproductores lo ven lavado o con otro contraste
    run(cmd + [*venc, "-preset", "fast", "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
               "-dn", "-map_chapters", "-1", "-movflags", "+faststart", str(destino)])


def exportar(avance=print, codigos=None, ajustes=None, raiz=None):
    """Deja cada video con los ajustes de exportación, su portada (.jpg) y su copy (.txt) en la carpeta del exporte
    (raiz; si no se da, V.FINALES/<fecha sesión>). Por días según el calendario (DIA_X) si «por_dia» o si no se
    eligieron videos; si no, todos juntos."""
    raiz = Path(raiz or S.listos / carpeta_de_exporte())
    aj = {**ajustes_exportar(), **(ajustes or {})}
    por_codigo = {c["codigo"]: c for c in combinaciones()}
    if codigos is None:  # todos los armados (antes: solo los que estaban en el calendario)
        codigos = [c["codigo"] for c in por_codigo.values() if (S.salida / f"{c['archivo']}.mp4").exists()]
    codigos = list(dict.fromkeys(codigos))  # sin repetidos, en el mismo orden
    hechos = []
    if codigos and not aj.get("por_dia"):
        plan = {"": list(codigos)}
    else:
        cal = calendario()
        plan = {d: [c for c in cs if not codigos or c in codigos] for d, cs in cal.items()}
        sueltos = [c for c in (codigos or []) if not any(c in cs for cs in cal.values())]
        if sueltos:
            plan["SIN_DIA"] = sueltos
    semana = semana_de_exporte()
    for dia, codigos in plan.items():
        destino = raiz / dia if dia else raiz
        destino.mkdir(parents=True, exist_ok=True)
        paquete = []
        for cod in codigos:
            c = por_codigo.get(cod)
            if not c:
                continue
            v, p = S.salida / f"{c['archivo']}.mp4", S.portadas / f"{c['archivo']}.jpg"
            if not v.exists():
                continue
            avance(f"Exportando {len(hechos) + 1} · {(dia + ' · ') if dia else ''}{c['archivo']}")
            render_final(v, p, destino / v.name, aj, avance)
            if p.exists():
                shutil.copy2(p, destino / p.name)
            cp = copy_de(cod)
            (destino / f"{c['archivo']}.txt").write_text(
                f"INSTAGRAM\n{cp.get('instagram', '')}\n\nTIKTOK\n{cp.get('tiktok', '')}\n\n"
                f"YOUTUBE · título\n{cp.get('youtube_titulo', '')}\n\nYOUTUBE · descripción\n{cp.get('youtube', '')}\n",
                encoding="utf-8")
            m = re.match(r"DIA_(\d+)", dia)
            paquete.append({"codigo": c["archivo"], "tipo": "video", "archivo": v.name,
                            "portada": p.name if p.exists() else None, "semana": semana,
                            "cuerpo": sin_angulo(c["cuerpo"])[0], "hook": c["hook"], "cta": c["cta"],
                            "dia_sugerido": int(m.group(1)) if m else None, "copy_borrador": cp,
                            "exportado": time.strftime("%Y-%m-%d %H:%M")})
            hechos.append(cod)
        if paquete:
            guardar_paquete(destino, paquete)
    return hechos


def semana_de_exporte():
    """La semana de la estrategia de lo que se exporta (S03 = la del lunes 19/10/2026): la que dice el guion
    o, si no dice, la de hoy. La misma cuenta que la Sala de diseño y el Publicador."""
    import datetime as dt
    s = str(produccion().get("semana_estrategia") or produccion().get("semana") or "").strip().upper()
    if re.fullmatch(r"S\d{1,3}", s):
        return f"S{int(s[1:]):02d}"
    b = CONFIG.get("semana_base") or {}
    lunes = dt.date.fromisoformat(b.get("lunes", "2026-10-05"))
    return f"S{int(b.get('numero', 1)) + (dt.date.today() - lunes).days // 7:02d}"


def guardar_paquete(destino, piezas):
    """paquete.json en la carpeta del exporte (contrato de ARQUITECTURA.md): el Publicador lee de aquí la semana,
    el cuerpo, el día y el copy de cada video. Se suma a lo que ya había (un video exportado otra vez se reemplaza)."""
    f = Path(destino) / "paquete.json"
    try:
        antes = json.loads(f.read_text(encoding="utf-8")).get("piezas", [])
    except (FileNotFoundError, ValueError):
        antes = []
    nuevos = {x["archivo"] for x in piezas}
    f.write_text(json.dumps({"piezas": [x for x in antes if x.get("archivo") not in nuevos] + piezas},
                            ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- Revisión de calidad antes de exportar ----------

def _calidad_path():
    return S.recetas / "_calidad.json"


def _medir_audio(video):
    """(pico dB, promedio dB, [(desde, dura)] silencios de más de 1 s) del video armado."""
    r = run([FFMPEG, "-hide_banner", "-i", str(video), "-vn", "-af", "volumedetect,silencedetect=n=-42dB:d=1.0", "-f", "null", "-"])
    pico = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr)
    prom = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    ini = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    dur = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", r.stderr)]
    return (float(pico.group(1)) if pico else None, float(prom.group(1)) if prom else None, list(zip(ini, dur)))


def revisar_calidad(codigos=None, avance=print):
    """Revisa cada video armado antes de exportar y dice qué tiene mal: muy largo, sin música, faltan imágenes,
    volumen muy bajo o saturado, silencios largos, subtítulo donde lo tapan los botones de TikTok o Instagram,
    o cambios sin armar. Se guarda (y se recalcula solo si el video cambió)."""
    try:
        cache = json.loads(_calidad_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        cache = {}
    out = {}
    lista = [c for c in combinaciones() if not codigos or c["codigo"] in codigos]
    for n, c in enumerate(lista, 1):
        v = S.salida / f"{c['archivo']}.mp4"
        if not v.exists():
            out[c["codigo"]] = {"problemas": [{"nivel": "err", "txt": "Todavía no está armado"}]}
            continue
        firma = f"{int(v.stat().st_mtime)}-{v.stat().st_size}"
        if cache.get(c["codigo"], {}).get("firma") == firma:
            out[c["codigo"]] = cache[c["codigo"]]
        else:
            avance(f"Revisando calidad {c['archivo']} ({n}/{len(lista)})")
            p = []
            try:
                r = json.loads((S.recetas / f"{c['codigo']}.json").read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                r = {}
            dur = r.get("duracion") or duracion(v)
            vertical = formato() == "vertical"  # el 16:9 es para la web o YouTube: el largo de Reels y TikTok no aplica
            if vertical and dur > 90:
                p.append({"nivel": "err", "txt": f"Dura {dur:.0f} s: muy largo para Reels o TikTok (ideal menos de 60 s)"})
            elif vertical and dur > 60:
                p.append({"nivel": "warn", "txt": f"Dura {dur:.0f} s: más de 60 s"})
            if not r.get("musica"):
                p.append({"nivel": "warn", "txt": "No tiene música"})
            if r.get("faltan_musica"):
                p.append({"nivel": "warn", "txt": "Falta la música del guion: " + ", ".join(r["faltan_musica"]) + " (usó otra pista)"})
            if r.get("faltan_assets"):
                p.append({"nivel": "err", "txt": "Faltan imágenes: " + ", ".join(r["faltan_assets"])})
            if r.get("marcas_sin_encontrar"):
                p.append({"nivel": "warn", "txt": "Marcas del guion sin su palabra (no salió la imagen o el efecto): "
                          + ", ".join(r["marcas_sin_encontrar"][:4])})
            pico, prom, silencios = _medir_audio(v)
            if pico is not None and pico > -0.3:
                p.append({"nivel": "warn", "txt": f"El audio llega al tope ({pico:.1f} dB): puede sonar saturado"})
            if prom is not None and prom < -30:
                p.append({"nivel": "err", "txt": f"El audio está muy bajo (promedio {prom:.0f} dB)"})
            largos = [(a, d) for a, d in silencios if d >= 1.2 and a > 0.3]
            if largos:
                p.append({"nivel": "warn", "txt": "Silencio largo: " + ", ".join(f"{d:.1f} s en el segundo {a:.0f}" for a, d in largos[:3])})
            out[c["codigo"]] = {"firma": firma, "problemas": p}
        # lo que depende de la plantilla o de cambios pendientes se mira siempre (no se guarda)
        extra = []
        pl = plantilla_sub(c)
        altura = min(float(x) for x in (pl.get("altura_desde_abajo") or 560, pl.get("altura_hook"), pl.get("altura_cta")) if x not in (None, ""))
        if altura < 330 and formato() == "vertical":
            extra.append({"nivel": "warn", "txt": f"Subtítulo muy abajo ({altura:.0f} px): lo tapan el texto y los botones de TikTok o Instagram (mínimo ~330 px)"})
        if editado_en(c) > v.stat().st_mtime + 1:
            extra.append({"nivel": "warn", "txt": "Tiene cambios sin armar: re-ármalo antes de exportar"})
        out[c["codigo"]] = {**out[c["codigo"]], "problemas": out[c["codigo"]]["problemas"] + extra}
    guardar = {k: {**v, "problemas": [x for x in v["problemas"] if x.get("nivel") != "info" and "cambios sin armar" not in x["txt"]
                                      and "Subtítulo muy abajo" not in x["txt"]]} for k, v in out.items() if v.get("firma")}
    S.recetas.mkdir(parents=True, exist_ok=True)
    _calidad_path().write_text(json.dumps({**cache, **guardar}, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# ---------- Resultados: qué hook funciona mejor ----------
# Las vistas y la retención que tuvo cada video en redes. Se guardan en la sesión (Drive) para todo el equipo.

def _metricas_path():
    return S.principal / "metricas.json"


def metricas():
    try:
        return json.loads(_metricas_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def guardar_metricas(datos):
    """datos: {codigo: {"vistas": 1200, "retencion": 45, "me_gusta": 30, "compartidos": 4, "guardados": 2, "red": "tiktok"}}."""
    m = metricas()
    for cod, v in datos.items():
        limpio = {}
        for k in ("vistas", "retencion", "me_gusta", "comentarios", "compartidos", "guardados"):
            try:
                if v.get(k) not in (None, ""):
                    limpio[k] = round(float(str(v[k]).replace(",", ".").replace("%", "")), 2)
            except ValueError:
                pass
        if limpio:
            m[cod] = {**m.get(cod, {}), **limpio, "actualizado": time.strftime("%Y-%m-%d")}
        elif cod in m and v == {}:
            m.pop(cod)
    _metricas_path().write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    escribir_resumen_resultados()
    return m


def ranking():
    """Promedios por tipo de hook, por hook y por cuerpo, con lo que ya se cargó. Lo que gana sale primero."""
    m = metricas()
    filas = []
    for c in combinaciones():
        if c.get("version") and not c["version"].get("propio"):
            continue
        d = m.get(c["codigo"])
        if not d or not d.get("vistas"):
            continue
        filas.append({**d, "codigo": c["codigo"], "hook": c["hook"], "cuerpo": c["cuerpo"], "cta": c["cta"],
                      "tipo_hook": info_pieza(c["hook"]).get("tipo") or "sin tipo",
                      "texto_hook": info_pieza(c["hook"]).get("texto", "")})

    def agrupar(campo):
        g = {}
        for f in filas:
            g.setdefault(f[campo], []).append(f)
        out = []
        for k, fs in g.items():
            ret = [f["retencion"] for f in fs if f.get("retencion") is not None]
            inter = [(f.get("me_gusta", 0) + f.get("comentarios", 0) + f.get("compartidos", 0) + f.get("guardados", 0)) / f["vistas"] * 100
                     for f in fs if f.get("vistas")]
            out.append({"clave": k, "videos": len(fs), "vistas": round(sum(f["vistas"] for f in fs) / len(fs)),
                        "retencion": round(sum(ret) / len(ret), 1) if ret else None,
                        "interaccion": round(sum(inter) / len(inter), 2) if inter else None,
                        "texto": fs[0].get("texto_hook", "") if campo == "hook" else ""})
        return sorted(out, key=lambda x: (x["retencion"] if x["retencion"] is not None else -1, x["vistas"]), reverse=True)
    return {"videos": len(filas), "tipo_hook": agrupar("tipo_hook"), "hook": agrupar("hook"), "cuerpo": agrupar("cuerpo")}


def escribir_resumen_resultados():
    """Guion/resultados.md: lo que funcionó en esta sesión, para que Claude lo use al escribir los próximos guiones."""
    r = ranking()
    if not r["videos"]:
        (S.guion / "resultados.md").unlink(missing_ok=True)
        return
    lineas = [f"# Resultados de {S.semana}", "", f"Videos con datos: {r['videos']}. Ordenado por retención (y vistas).", "",
              "## Por tipo de hook", ""]
    lineas += [f"- {x['clave']}: retención {x['retencion']} % · {x['vistas']} vistas promedio · {x['videos']} videos" for x in r["tipo_hook"]]
    lineas += ["", "## Hooks que mejor funcionaron", ""]
    lineas += [f"- {x['clave']} («{x['texto']}»): retención {x['retencion']} % · {x['vistas']} vistas" for x in r["hook"][:5]]
    lineas += ["", "## Hooks que peor funcionaron", ""]
    lineas += [f"- {x['clave']} («{x['texto']}»): retención {x['retencion']} % · {x['vistas']} vistas" for x in r["hook"][-3:]]
    lineas += ["", "## Por cuerpo", ""]
    lineas += [f"- {x['clave']}: retención {x['retencion']} % · {x['vistas']} vistas" for x in r["cuerpo"]]
    S.guion.mkdir(parents=True, exist_ok=True)
    (S.guion / "resultados.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")


# ---------- Checklist ----------

def checklist():
    """Qué falta grabar y qué imágenes/clips de Flow faltan."""
    p = produccion()
    hay = {x["nombre"] for x in piezas()}
    con_cta = {n.split("_")[0] for n in hay if "CTA" in n.upper()}  # los CTA valen como se grabaron
    omitidas = [n for n, i in p.get("piezas", {}).items() if i.get("omitida") and n not in hay]  # «No se grabó»
    falta_piezas = [n for n in p.get("piezas", {}) if n not in hay and n not in omitidas
                    and not ("CTA" in n.upper() and (n.split("_")[0] in con_cta or ("_" not in n and con_cta)))]
    assets = []
    for a in p.get("assets", []):
        f = buscar_asset(a["id"])
        # «Rehacer con Claude» le dio un prompt nuevo: la imagen de antes ya no vale hasta que llegue una más nueva
        viejo = bool(f) and float(a.get("prompt_nuevo_t") or 0) > f.stat().st_mtime
        assets.append({**a, "listo": bool(f) and not viejo, "por_regenerar": viejo, "archivo_actual": f.name if f else None})
    musica = [{**m, "listo": bool(buscar_musica(m["id"])), "archivo_actual": (buscar_musica(m["id"]) or Path("")).name or None}
              for m in produccion().get("musica", []) if m.get("id")]
    sug = sugerencias()
    rp = reemplazos()
    sin = [{"rel": c["rel"], "nombre": c["ruta"].name, "carpeta": c["tipo_carpeta"], **sug.get(c["rel"], {}),
            **({"reemplazado": rp[c["rel"]]} if c["rel"] in rp else {})}
           for c in clips_sin_asignar()]
    return {"piezas_faltan": falta_piezas, "omitidas": omitidas, "assets": assets, "sin_asignar": sin, "musica": musica}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["procesar", "armar", "exportar", "portadas", "todo", "dossier", "aplicar-plan"])
    ap.add_argument("--plan", help="archivo JSON del plan (para aplicar-plan)")
    ap.add_argument("--salida", help="dónde escribir el dossier (si no, se imprime)")
    ap.add_argument("--pieza")
    ap.add_argument("--semana")
    ap.add_argument("--rehacer-subtitulos", action="store_true")
    a = ap.parse_args()
    if a.semana:
        usar_semana(a.semana)
    if a.accion == "dossier":
        texto = json.dumps(dossier(), ensure_ascii=False, indent=1)
        if a.salida:
            Path(a.salida).write_text(texto, encoding="utf-8")
            print(a.salida)
        else:
            print(texto)
        sys.exit(0)
    if a.accion == "aplicar-plan":
        perdidas = aplicar_plan(json.loads(Path(a.plan).read_text(encoding="utf-8")))
        print("Plan aplicado." + (" Palabras no encontradas: " + ", ".join(perdidas) if perdidas else ""))
        sys.exit(0)
    if a.accion in ("procesar", "todo"):
        procesar(a.rehacer_subtitulos)
    if a.accion in ("armar", "todo"):
        armar(a.pieza)
    if a.accion == "portadas":
        rehacer_portadas()
    if a.accion in ("exportar", "todo"):
        exportar()


# ---------- Muestras de Estilo de edición ----------
MUESTRAS = DATOS / "muestras"
QUE_MUESTRA = ("transicion", "lut", "efecto", "zoom", "zoom_hook", "fundido", "voz")


def _clips_muestra():
    """Dos clips de la sesión para las muestras: un hook y un cuerpo (o los que haya)."""
    vids = sorted((f for f in S.proc.glob("*.mp4") if duracion(f) > 3), key=lambda f: f.name) if S.proc.exists() else []
    if not vids:
        raise ValueError("Todavía no hay clips analizados en esta sesión: analiza los clips y vuelve a probar la muestra.")
    hook = next((f for f in vids if "_H" in f.stem.upper()), vids[0])
    otro = next((f for f in vids if f != hook and "_" not in f.stem), next((f for f in vids if f != hook), hook))
    return hook, otro


def muestra_estilo(que, cambios=None):
    """Muestra corta de una opción de Estilo de edición, hecha con ffmpeg sobre pedazos de lo grabado de la sesión y con
    los mismos filtros del armado: así se ve técnicamente cómo queda. cambios: lo elegido en pantalla encima de lo
    guardado. Devuelve {"archivo", "nota", "marcas": [(segundo, texto)]}. En caché por opción y ajustes."""
    if que not in QUE_MUESTRA:
        raise ValueError("Esa opción no tiene muestra")
    est = {**estilo_edicion(), **(cambios or {})}
    a, b = _clips_muestra()
    claves = {"transicion": ("transicion",), "lut": ("lut", "lut_fuerza"), "efecto": ("efecto",),
              "zoom": ("zoom_tipo", "zoom_fuerza", "zoom_cada"), "zoom_hook": ("zoom_hook_fuerza",), "fundido": ("fundido",),
              "voz": ("limpieza_voz",)}[que]
    firma = json.dumps([que, {k: est.get(k) for k in claves}, str(a), a.stat().st_mtime, str(b), b.stat().st_mtime, 3],
                       sort_keys=True, default=str)
    nombre = hashlib.md5(firma.encode()).hexdigest()[:16] + ".mp4"
    MUESTRAS.mkdir(parents=True, exist_ok=True)
    salida = MUESTRAS / nombre
    W, H = (540, 960) if lienzo()[1] >= lienzo()[0] else (960, 540)
    norma = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30,format=yuv420p"
    aud = "aformat=sample_rates=48000:channel_layouts=stereo"
    trabajo = MUESTRAS / ("t_" + nombre[:-4])
    trabajo.mkdir(exist_ok=True)
    cmd, f, marcas, nota, sfx = [FFMPEG, "-hide_banner", "-y"], [], [], "", []
    ini = lambda v, k=0: min(max(0.5, duracion(v) * 0.15) + 0.9 * k, max(0.0, duracion(v) - 1.6))

    def pedazos(lista, dur):
        """[(clip, desde)] de dur segundos cada uno, unidos: [vc][ac]."""
        for k, (v, d0) in enumerate(lista):
            cmd.extend(["-ss", f"{d0:.2f}", "-t", f"{dur:.2f}", "-i", str(v)])
            f.append(f"[{k}:v]{norma}[p{k}];[{k}:a]{aud},asetpts=N/SR/TB[q{k}]")
        f.append("".join(f"[p{k}][q{k}]" for k in range(len(lista))) + f"concat=n={len(lista)}:v=1:a=1[vc][ac]")
        return len(lista)

    def zoompan(z, x="0.5", y="0.38", entrada="vc", salida_="vz"):
        f.append(f"[{entrada}]zoompan=z='max(1,{z})':x='iw*min(max(({x})*(1-1/zoom),0),1-1/zoom)':"
                 f"y='ih*min(max(({y})*(1-1/zoom),0),1-1/zoom)':d=1:s={W}x{H}:fps=30,setsar=1[{salida_}]")

    if que == "transicion":
        tipo = est.get("transicion") if est.get("transicion") not in (None, "ninguna") else "aleatoria"
        tipos = ["barrido", "zoom", "flash", "glitch", "sacudida"] if tipo == "aleatoria" else [tipo, tipo]
        largo = 1.3
        n = pedazos([((a, b)[k % 2], ini((a, b)[k % 2], k // 2)) for k in range(len(tipos) + 1)], largo)
        trs = [(round(largo * (k + 1), 3), t) for k, t in enumerate(tipos)]
        empujes = [t for t, k in trs if k in ("zoom", "barrido")]
        golpes = [t for t, k in trs if k == "sacudida"]
        v = "vc"
        if empujes or golpes:
            z = "1" + "".join(f"+between(it,{t:.3f},{t + 0.3:.3f})*0.16*pow(1-(it-{t:.3f})/0.3,2)" for t in empujes) \
                + "".join(f"+between(it,{t:.3f},{t + 0.3:.3f})*0.08" for t in golpes)
            jx = "".join(f"+between(it,{t:.3f},{t + 0.3:.3f})*0.03*sin(it*61)" for t in golpes)
            jy = "".join(f"+between(it,{t:.3f},{t + 0.3:.3f})*0.03*cos(it*53)" for t in golpes)
            zoompan(z, f"0.5{jx}", f"0.4{jy}")
            v = "vz"
        capas = _capas_transicion(trs)
        f.append(f"[{v}]{','.join(capas) if capas else 'null'}[v]")
        for t, k in trs:
            cat = SONIDO_TRANSICION.get(k, "whoosh")
            sfx.append((cat, t - (0.03 if cat in ("camara", "impacto", "glitch") else 0.15)))
            marcas.append((t, NOMBRES_TRANSICION.get(k, k)))
        total = largo * n
        nota = ("Al azar: cada corte trae una distinta (en el video, el CTA entra siempre con destello)." if tipo == "aleatoria"
                else f"{NOMBRES_TRANSICION.get(tipo, tipo).capitalize()} en cada corte, con su sonido.")
    elif que in ("lut", "efecto"):
        total = 3.5
        pedazos([(a, ini(a))], total)
        if que == "lut":
            lut = ruta_lut(est.get("lut"))
            if not lut:
                raise ValueError("Sube un LUT primero para ver la muestra.")
            local = f"lut{lut.suffix.lower()}"
            shutil.copyfile(lut, trabajo / local)
            fuerza = max(0.0, min(1.0, float(est.get("lut_fuerza", 1.0))))
            filtro = (f"lut3d=file={local}" if fuerza >= 0.99 else None)
            if filtro:
                f.append(f"[vc]split[o][p];[p]{filtro}[q]")
            else:
                f.append(f"[vc]split=3[o][p][lb];[p]lut3d=file={local}[lc];[lc][lb]blend=all_mode=normal:all_opacity={fuerza:.2f}[q]")
            nota = f"Izquierda: como se grabó · derecha: con el LUT {est.get('lut')}."
        else:
            ef = est.get("efecto") if est.get("efecto") in EFECTOS_VIDEO else "vineta"
            f.append(f"[vc]split[o][p];[p]{EFECTOS_VIDEO[ef]}[q]")
            nota = "Izquierda: sin efecto · derecha: con " + {"vineta": "viñeta", "grano": "grano", "vineta_grano": "viñeta y grano"}[ef] + "."
        f.append(f"[o]crop={W // 2}:{H}:0:0[l];[q]crop={W // 2}:{H}:{W // 2}:0[r];[l][r]hstack=2,"
                 f"drawbox=x={W // 2 - 1}:y=0:w=2:h={H}:color=white@0.9:t=fill[v]")
    elif que == "zoom":
        largo, fu = 1.5, float(est.get("zoom_fuerza", 0.14))
        lista = [((a, b)[k % 2], ini((a, b)[k % 2], k // 2)) for k in range(4)]
        pedazos(lista, largo)
        tipo, cada = est.get("zoom_tipo", "punch"), int(est.get("zoom_cada", 2))
        partes = []
        for k in range(4):
            if k % cada:
                continue
            t0, t1 = k * largo, (k + 1) * largo
            tk = ("punch", "in", "out")[(k // cada) % 3] if tipo == "mezcla" else tipo
            pr = f"((it-{t0:.2f})/{largo:.2f})"
            zk = {"punch": f"{1 + fu:.3f}", "in": f"(1+{fu:.3f}*{pr})", "out": f"(1+{fu:.3f}*(1-{pr}))"}[tk]
            partes.append(f"between(it,{t0:.2f},{t1 - 0.001:.3f})*({zk}-1)")
            sfx.append(("zoom", t0 - 0.08))
            marcas.append((t0, {"punch": "salto", "in": "acercar", "out": "alejar"}[tk]))
        zoompan("1+" + "+".join(partes) if partes else "1")
        f.append("[vz]null[v]")
        total = largo * 4
        nota = ("Frase sí, frase no" if cada == 2 else "En todas las frases") + ": así se mueve la cámara en el cuerpo."
    elif que == "zoom_hook":
        total, fu = 3.5, float(est.get("zoom_hook_fuerza", 0.1))
        pedazos([(a, ini(a))], total)
        zoompan(f"1+{fu:.3f}*it/{total}")
        f.append("[vz]null[v]")
        nota = "El hook se acerca despacio de principio a fin."
    elif que == "fundido":
        total = 4.0
        pedazos([(a, ini(a))], total)
        imgs = sorted((x for x in S.assets.iterdir() if x.suffix.lower() in EXT_IMG), key=lambda x: x.name) if S.assets.exists() else []
        fd = float(est.get("fundido", 0.12))
        dur_i = 2.0
        if imgs:
            cmd.extend(["-loop", "1", "-framerate", "30", "-t", f"{dur_i}", "-i", str(imgs[0])])
        else:  # sin imágenes de Flow: un cuadro del otro clip hace de imagen
            cmd.extend(["-ss", f"{ini(b):.2f}", "-t", f"{dur_i}", "-i", str(b)])
        fade = (f",fade=t=in:st=0:d={fd:.2f}:alpha=1,fade=t=out:st={dur_i - fd:.2f}:d={fd:.2f}:alpha=1" if fd > 0.15 else "")
        f.append(f"[1:v]{norma},format=yuva420p{fade},setpts=PTS+1/TB[img]")
        f.append(f"[vc][img]overlay=0:0:eof_action=pass:enable='between(t,1,{1 + dur_i})'[v]")
        marcas += [(1.0, "entra la imagen"), (1 + dur_i, "sale")]
        nota = (f"La imagen entra y sale con un fundido de {fd:.1f} s." if fd > 0.15 else "Sin fundido: la imagen aparece y se va de golpe.")
    else:  # voz: el mismo pedazo dos veces, primero como se grabó y después limpio
        total = 3.0
        nivel = est.get("limpieza_voz") if est.get("limpieza_voz") in LIMPIEZAS_VOZ else "suave"
        for k in range(2):
            cmd.extend(["-ss", f"{ini(a):.2f}", "-t", f"{total:.2f}", "-i", str(a)])
            f.append(f"[{k}:v]{norma}[p{k}]")
        f.append(f"[0:a]{aud},loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,asetpts=N/SR/TB[q0]")
        f.append(f"[1:a]{aud},{LIMPIEZAS_VOZ[nivel]},loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,asetpts=N/SR/TB[q1]")
        f.append("[p0][q0][p1][q1]concat=n=2:v=1:a=1[v][ac]")
        marcas += [(0.0, "como se grabó"), (total, f"voz limpia ({nivel})")]
        total *= 2
        nota = "Escúchala con sonido: primero como se grabó y después con la voz limpia."
    # los sonidos de cada corte, igual que en el armado (sin retumbe de bajos)
    mezcla = ["[ac]"]
    idx = len([x for x in cmd if x == "-i"])
    for k, (cat, t) in enumerate(sfx):
        arch = elegir_sfx(cat, f"muestra|{cat}|{k}")
        if not arch:
            continue
        d = dur_audio(arch)
        desde = inicio_audible(arch)
        if desde >= d - 0.05:
            desde = 0.0
        largo_s = min(d - desde, 1.0)
        ms = int(max(0.0, t) * 1000)
        vk = CONFIG["sonidos"]["volumen_sfx"] * CONFIG["sonidos"].get("volumen_por_categoria", {}).get(cat, 1.0) * ganancia_normalizada(arch)
        cmd.extend(["-i", str(arch)])
        f.append(f"[{idx}:a]atrim={desde:.2f}:{desde + largo_s:.2f},asetpts=N/SR/TB,afade=t=out:st={max(0, largo_s - 0.15):.2f}:d=0.15,"
                 f"highpass=f=110,{aud},adelay={ms}|{ms},volume={vk:.2f}[s{k}]")
        mezcla.append(f"[s{k}]")
        idx += 1
    f.append(f"{''.join(mezcla)}amix=inputs={len(mezcla)}:normalize=0:duration=first,alimiter=limit=0.89:level=disabled[a]"
             if len(mezcla) > 1 else "[ac]anull[a]")
    if not salida.exists():
        run(cmd + ["-filter_complex", ";".join(f), "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}", "-c:v", "libx264",
                   "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                   "-movflags", "+faststart", str(salida)], cwd=trabajo)
    shutil.rmtree(trabajo, ignore_errors=True)
    for viejo in sorted(MUESTRAS.glob("*.mp4"), key=lambda x: x.stat().st_mtime)[:-40]:  # se guardan las últimas 40
        viejo.unlink(missing_ok=True)
    return {"archivo": nombre, "nota": nota, "marcas": [[round(t, 2), m] for t, m in marcas], "dur": round(total, 2)}
