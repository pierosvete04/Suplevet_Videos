"""Motor de edición de Suplevet: piezas grabadas -> videos terminados con portada y copy.

Flujo:
  1. procesar:  recorta silencios, iguala volumen y formato (1080x1920) y transcribe cada pieza.
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
SUBCARPETAS = {"guion", "1_guion", "clips", "flow", "assets", "musica", "música", "v.finales", "videos finales", "listos"}


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
    if any((carpeta / x).is_dir() for x in ("Guion", "1_guion")):
        guion = ""
    else:
        guion = next((d for d in dias if any((carpeta / d / x).is_dir() for x in ("Guion", "1_guion"))), dias[0])
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
    if not nuevo or len(nuevo) > 60 or re.search(r'[\\/:*?"<>|]', nuevo):
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
        self.guion = _primera(self.principal, ["Guion", "1_guion"])
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
PROCESOS = set()  # ffmpeg, rclone o Claude corriendo ahora: se cortan al cancelar


class Cancelado(Exception):
    pass


HILO_TRABAJO = {"id": None}  # solo el trabajo en segundo plano se cancela; lo demás (ver un video, una portada) no


def revisar_cancelado():
    if CANCELAR.is_set() and _hilos.get_ident() == HILO_TRABAJO["id"]:
        raise Cancelado("Cancelado")


def cancelar():
    CANCELAR.set()
    for p in list(PROCESOS):
        try:
            p.kill()
        except OSError:
            pass


def run(cmd, cwd=None):
    revisar_cancelado()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    PROCESOS.add(p)
    try:
        out, err = p.communicate()
    finally:
        PROCESOS.discard(p)
    revisar_cancelado()
    if p.returncode != 0:
        raise RuntimeError(f"Falló: {' '.join(map(str, cmd))[:300]}\n{err[-1500:]}")
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def duracion(p):
    return float(run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]).stdout.strip())


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
                _prod_cache = ((f, m), json.loads(f.read_text(encoding="utf-8")))
            return _sumar_plan(_prod_cache[1])
    return _sumar_plan({})


def _sumar_plan(base):
    """El plan del director (subtitulos/<sesión>/_plan.json) manda sobre el guion: música, videos y piezas."""
    plan = plan_director()
    if not plan:
        return base
    out = {**base}
    for k in ("musica", "musica_por_defecto", "plantilla_sub", "calendario"):
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


def info_pieza(nombre):
    return produccion().get("piezas", {}).get(nombre, {})


# C1_H2 (hook), C1 (cuerpo), C1_P3 (parte 3 del cuerpo C1), CTA / CTA-A / C1_CTA1
# C1_H2 (hook), C1_H2_P1 (parte 1 del hook 2), C1 (cuerpo), C1_P3 (parte 3 del cuerpo), CTA / CTA-A / C1_CTA1
CODIGO_PIEZA = re.compile(r"^((?:C|P)\d+_H\d+(?:_P\d+)?|(?:C|P)\d+(?:_P\d+)?|(?:(?:C|P)\d+_)?CTA[-_]?[A-Z0-9]*)$", re.I)
PARTE = re.compile(r"^((?:C|P)\d+(?:_H\d+)?)_P(\d+)$", re.I)


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
        if not CODIGO_PIEZA.match(pieza):
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


def clips():
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
                        if f.is_file() and f.suffix.lower() in EXT_VID and f not in vistos:
                            vistos.add(f)
                            out.append({"rel": f.relative_to(S.grab).as_posix(), "ruta": f, "tipo_carpeta": tipo})
      for sueltos in [sesion / CARPETA_SUELTOS] + por_cuerpo:
        if sueltos.is_dir():
            for f in sorted(sueltos.iterdir()):
                if f.is_file() and f.suffix.lower() in EXT_VID and f not in vistos:
                    vistos.add(f)
                    out.append({"rel": f.relative_to(S.grab).as_posix(), "ruta": f, "tipo_carpeta": None})
    return out


def nombre_automatico(c):
    """Nombre de pieza que se deduce sin asignar a mano:
    C1_H2.mov · Cuerpo/3.MOV -> C1_P3 · Cuerpo/C2/1.MOV -> C2_P1 · CTA/CTA.MOV -> CTA."""
    stem, tipo = c["ruta"].stem, c["tipo_carpeta"]

    def cuerpo_de(ruta):  # Cuerpo/C2/1.MOV o C2/Cuerpo/1.MOV → C2; si no dice, C1
        for d in (ruta.parent.name, ruta.parent.parent.name):
            if re.fullmatch(r"(?:C|P)\d+", d, re.I):
                return d.upper()
        return "C1"
    if CODIGO_PIEZA.match(stem):
        return stem.upper()
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


def piezas():
    """Piezas listas para editar: [{nombre, tipo, partes: [rutas], original, rel}].
    Un cuerpo grabado en partes (C1_P1, C1_P2…) es una sola pieza C1 con sus partes en orden.
    Si dos clips dicen ser la misma pieza, gana el asignado a mano."""
    asig = leer_asignaciones()
    por_nombre, origen_manual = {}, set()
    for c in clips():
        manual = asig.get(c["rel"])
        nombre = manual or nombre_automatico(c)
        if not nombre or (nombre in por_nombre and nombre in origen_manual and not manual):
            continue
        por_nombre[nombre] = c
        if manual:
            origen_manual.add(nombre)
    agrupadas = {}
    for nombre, c in por_nombre.items():
        m = PARTE.match(nombre)
        base, orden = (m.group(1).upper(), int(m.group(2))) if m else (nombre, 0)
        agrupadas.setdefault(base, []).append((orden, c))
    out = []
    for nombre, lista in agrupadas.items():
        lista.sort(key=lambda x: x[0])
        partes = [c["ruta"] for _, c in lista]
        out.append({"nombre": nombre, "tipo": tipo_de(nombre), "partes": partes, "original": partes[0],
                    "rel": lista[0][1]["rel"], "rels": [c["rel"] for _, c in lista]})
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
        nuevo = f"{_carpeta_de_codigo(codigo)}/{codigo}{c['ruta'].suffix.upper()}"
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
    t_viejo = _archivo_transcripcion({"rel": cambio["rel"]})
    if t_viejo.exists():
        t_viejo.replace(_archivo_transcripcion({"rel": rel_nuevo}))


def editar_texto_pieza(nombre, texto, partes=None):
    """Cambia lo que dice una pieza en el guion (produccion.json): se ve en Guion, en el teleprompter y en el panel."""
    f = next((x for x in (S.guion / "produccion.json", S.grab / "produccion.json") if x.exists()), None)
    if not f:
        raise ValueError("Esta sesión no tiene guion (produccion.json)")
    prod = json.loads(f.read_text(encoding="utf-8"))
    pz = prod.get("piezas", {}).get(nombre)
    if pz is None:
        raise ValueError(f"{nombre} no está en el guion")
    partes = [" ".join(str(p).split()) for p in (partes or []) if str(p).strip()]
    if partes:
        pz["partes"] = partes
        pz["texto"] = " ".join(partes)
    else:
        pz["texto"] = " ".join(str(texto).split())
    pz["editado_en_sala"] = time.strftime("%Y-%m-%d %H:%M")
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
                    for f in sorted(d.iterdir()) if f.suffix.lower() in EXT_VID]
    return out


def clips_sin_asignar():
    usados = {r for p in piezas() for r in p["rels"]}
    return [c for c in clips() if c["rel"] not in usados]


def buscar_asset(aid):
    if not S.assets.exists():
        return None
    for f in S.assets.iterdir():
        if f.stem.lower() == aid.lower() and f.suffix.lower() in EXT_IMG | EXT_VID:
            return f
    return None


# ---------- 1. Procesar ----------

def limites_de_voz(video):
    """Devuelve (inicio, fin) de la parte con voz, quitando silencios de los extremos."""
    total = duracion(video)
    r = run([FFMPEG, "-hide_banner", "-i", str(video), "-af", "silencedetect=n=-35dB:d=0.25", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    ini, fin = 0.0, total
    if starts and starts[0] <= 0.05 and ends:
        ini = max(0.0, ends[0] - 0.10)
    if starts and (len(ends) < len(starts) or ends[-1] >= total - 0.05):
        fin = min(total, starts[-1] + 0.15)
    return ini, fin


_modelo = None


def transcribir(video):
    global _modelo
    import numpy as np
    from faster_whisper import WhisperModel
    if _modelo is None:
        _modelo = WhisperModel(CONFIG["transcripcion"]["modelo"], device="cpu", compute_type="int8")
    pcm = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True).stdout
    segs, _ = _modelo.transcribe(np.frombuffer(pcm, dtype=np.float32), language="es", word_timestamps=True,
                                 vad_filter=True, initial_prompt=CONFIG["transcripcion"]["vocabulario"])
    out = []
    for s in segs:  # se va transcribiendo de a pedazos: se puede cancelar en el medio
        revisar_cancelado()
        out += [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for w in s.words]
    return out


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


def procesar(forzar_subs=False, avance=print):
    for d in (S.proc, S.subs):
        d.mkdir(parents=True, exist_ok=True)
    ps = piezas()
    for n, p in enumerate(ps, 1):
        dest = S.proc / f"{p['nombre']}.mp4"
        firma = S.proc / f"{p['nombre']}.partes.json"
        lista = [str(x) for x in p["partes"]]
        vieja = json.loads(firma.read_text(encoding="utf-8")) if firma.exists() else None
        if isinstance(vieja, dict):
            vieja = vieja.get("partes")
        if (not dest.exists() or vieja != lista
                or dest.stat().st_mtime < max(x.stat().st_mtime for x in p["partes"])):
            avance(f"Procesando {p['nombre']} ({n}/{len(ps)})" + (f" · {len(lista)} partes" if len(lista) > 1 else ""))
            trozos = []
            for k, parte in enumerate(p["partes"]):
                ini, fin = limites_de_voz(parte)
                t = dest if len(lista) == 1 else S.proc / f"_{p['nombre']}_{k}.mp4"
                run([FFMPEG, "-hide_banner", "-y", "-ss", f"{ini:.2f}", "-to", f"{fin:.2f}", "-i", str(parte),
                     "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1",
                     "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000", "-ac", "2", "-dn", "-map_chapters", "-1",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(t)])
                trozos.append(t)
            duraciones = [round(duracion(t), 3) for t in trozos]
            if len(trozos) > 1:
                lst = S.proc / f"_{p['nombre']}.txt"
                lst.write_text("".join(f"file '{t.name}'\n" for t in trozos), encoding="utf-8")
                run([FFMPEG, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", dest.name], cwd=S.proc)
                for t in trozos + [lst]:
                    t.unlink(missing_ok=True)
            firma.write_text(json.dumps({"partes": lista, "duraciones": duraciones}, ensure_ascii=False), encoding="utf-8")
            (S.subs / f"{p['nombre']}.json").unlink(missing_ok=True) if vieja is not None and vieja != lista else None
        sub = S.subs / f"{p['nombre']}.json"
        if forzar_subs or not sub.exists():
            avance(f"Transcribiendo {p['nombre']} ({n}/{len(ps)})")
            pal = transcribir(dest)
            sub.write_text(json.dumps({"pieza": p["nombre"], "lineas": agrupar_lineas(pal)},
                                      ensure_ascii=False, indent=1), encoding="utf-8")


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
    texto = " ".join(p["w"] for p in transcribir(c["ruta"]))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"firma": firma, "texto": texto}, ensure_ascii=False), encoding="utf-8")
    return texto


def escuchar_clips(avance=print):
    """Transcribe los clips sin asignar para poder sugerir de qué pieza son."""
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


def sugerencias():
    """{rel: {texto, pieza, parecido, mejor}}. «mejor» marca la toma más parecida de cada pieza;
    si hay empate gana la última grabada (suele ser la buena)."""
    esperadas = {n: i.get("texto", "") for n, i in produccion().get("piezas", {}).items() if i.get("texto")}
    # un cuerpo grabado frase por frase: cada toma se compara también con cada frase (C2_P1, C2_P2…)
    for n, i in produccion().get("piezas", {}).items():
        if i.get("tipo") == "cuerpo" and len(i.get("partes") or []) > 1:
            for k, frase in enumerate(i["partes"], 1):
                esperadas[f"{n}_P{k}"] = frase
    ya = {p["nombre"] for p in piezas()}
    ya |= {f"{p['nombre']}_P{k}" for p in piezas() for k in range(1, len(p["partes"]) + 1) if len(p["partes"]) > 1}
    out = {}
    for c in clips_sin_asignar():
        texto = transcripcion_clip(c, crear=False)
        if texto is None:
            continue
        mejor = max(((n, _parecido(texto, t)) for n, t in esperadas.items() if n not in ya), key=lambda x: x[1], default=(None, 0))
        out[c["rel"]] = {"texto": texto, "pieza": mejor[0] if mejor[1] >= 0.35 else None, "parecido": round(mejor[1], 2)}
    por_pieza = {}
    for rel, sug in out.items():
        if sug["pieza"]:
            actual = por_pieza.get(sug["pieza"])
            if not actual or (sug["parecido"], rel) >= (out[actual]["parecido"], actual):
                por_pieza[sug["pieza"]] = rel
    for rel, sug in out.items():
        sug["mejor"] = bool(sug["pieza"]) and por_pieza.get(sug["pieza"]) == rel
    return out


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
    esperadas = {n: i.get("texto", "") for n, i in produccion().get("piezas", {}).items() if i.get("texto")}
    for n, i in produccion().get("piezas", {}).items():
        if i.get("tipo") == "cuerpo" and len(i.get("partes") or []) > 1:
            for k, frase in enumerate(i["partes"], 1):
                esperadas[f"{n}_P{k}"] = frase
    dueno = {}  # pieza → nombre del clip que la tiene
    for p in piezas():
        for k, r in enumerate(p["rels"], 1):
            dueno[p["nombre"] if len(p["rels"]) == 1 else f"{p['nombre']}_P{k}"] = Path(r).name
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
        if texto is None:
            dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "sin_escuchar", "por_que": "Todavía no se escuchó: toca «Analizar y renombrar todo»."})
            continue
        if len(texto.split()) < 3:
            dudas.append({"clip": nombre, "rel": c["rel"], "tipo": "sin_voz",
                          "por_que": "Casi no se habla en el clip (¿toma de prueba o se cortó?)." + (f" Se oyó: «{texto}»." if texto else "")})
            continue
        cerca = max(((n, _parecido(texto, t)) for n, t in esperadas.items()), key=lambda x: x[1], default=(None, 0))
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
              and not (re.match(r".+_P\d+$", n) and n.rsplit("_P", 1)[0] in hay)
              and not ("CTA" in n.upper() and (n.split("_")[0] in con_cta or "_" not in n and con_cta))]
    return {"dudas": dudas, "faltan": faltan, "texto": {d["rel"]: (sug.get(d["rel"]) or {}).get("texto") for d in dudas}}


def aceptar_sugerencias(minimo=0.5):
    hechas = 0
    for rel, sug in sugerencias().items():
        if sug["mejor"] and sug["parecido"] >= minimo:
            guardar_asignacion(rel, sug["pieza"])
            hechas += 1
    return hechas


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
            "musica": info_pieza(p["nombre"]).get("musica")}
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
    S.subs.mkdir(parents=True, exist_ok=True)
    _plan_path().write_text(json.dumps(resto, ensure_ascii=False, indent=1), encoding="utf-8")
    global _prod_cache
    _prod_cache = None
    return perdidas


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
        fila = {"nombre": nombre, "tipo": g["tipo"], "tipo_hook": g.get("tipo_hook"), "texto": g["texto"], "partes": g["partes"],
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
    return {"hay_guion": bool(prod.get("piezas")), "piezas": out, "sin_escuchar": sin_escuchar,
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


def onda_archivo(f, por_seg=50):
    """Picos de volumen de un archivo de audio cualquiera (un sonido de la biblioteca), en caché."""
    import numpy as np
    f = Path(f)
    if not f.is_file():
        return {"por_seg": por_seg, "picos": [], "dur": 0}
    firma = f"{f.stat().st_size}-{int(f.stat().st_mtime)}-{por_seg}"
    cache = DATOS / "ondas" / (hashlib.md5(str(f).encode("utf-8")).hexdigest() + ".json")
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("firma") == firma:
                return d
        except ValueError:
            pass
    sr = 8000
    pcm = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(f), "-vn", "-ac", "1", "-ar", str(sr),
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
    return d


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
    pcm = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(f), "-vn", "-ac", "1", "-ar", str(sr),
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
               "golpe_texto": "golpe de texto", "riser_largo": "riser largo", "drama": "drama", "corte_silencio": "corte de silencio"}


def plan_sonoro(nombre, info):
    """Lo que va a sonar en una pieza según su guion: marcas del guion y reglas automáticas (sin tiempos:
    se anclan a la palabra). [{momento, efecto, por}]"""
    so = CONFIG["sonidos"]
    out = []
    tipo = tipo_de(nombre) if info.get("tipo") != "cuerpo" else "cuerpo"
    if tipo == "hook":
        t = norm(info.get("tipo", "")) or "default"
        for cat in so["arranque_por_tipo"].get(t) or so["arranque_por_tipo"]["default"]:
            out.append({"momento": "inicio", "efecto": cat, "por": f"arranque de hook ({info.get('tipo') or 'general'})"})
        out.append({"momento": "final", "efecto": "whoosh", "por": "corte al cuerpo"})
    if tipo == "cuerpo":
        out.append({"momento": "inicio", "efecto": "riser", "por": "entrada del cuerpo"})
        out.append({"momento": "final", "efecto": "whoosh_inverso", "por": "corte al CTA"})
    if tipo == "cta":
        cat = so["cta_por_tipo"].get(norm(info.get("tipo", "")) or "anuncio")
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
    if tipo == "cuerpo" and "suplevet" in palabras:
        out.append({"momento": "«Suplevet»", "efecto": "brillo", "por": "aparece la marca"})
    return out


def _orden_piezas(prod):
    orden = {"cuerpo": 0, "hook": 1, "cta": 2}
    def clave(n):
        m = re.match(r"(?:C|P)(\d+)", n)
        return (int(m.group(1)) if m else 99, orden[tipo_de(n) if prod["piezas"][n].get("tipo") != "cuerpo" else "cuerpo"], n)
    return sorted(prod.get("piezas", {}), key=clave)


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
        out = [f"# {titulo} — guion para el host · hook + cuerpo", "",
               "Cada video tal como se va a ver: primero el hook y después el cuerpo. Lee tal cual.", ""]
        for cuerpo in cuerpos:
            c = piezas_g[cuerpo]
            hooks = sorted((g for n, g in piezas_g.items() if g["tipo"] == "hook" and n.split("_")[0] == cuerpo),
                           key=lambda g: g["nombre"])
            ctas = [g for n, g in piezas_g.items() if g["tipo"] == "cta" and ("_" not in n or n.split("_")[0] == cuerpo)]
            for h in hooks:
                out += [f"## Video {h['nombre']} + {cuerpo}" + (f" · {h['tipo_hook']}" if h.get("tipo_hook") else ""), "",
                        f"**Hook:** {h['texto']}", "", "**Cuerpo:**"]
                out += [f"{k}. {f}" for k, f in enumerate(c["partes"], 1)] if len(c["partes"]) > 1 else [c["texto"]]
                if ctas:
                    out += ["", "**CTA:** " + " / ".join(g["texto"] for g in ctas)]
                out += ["", "- [ ] grabado", ""]
        return "\n".join(out)
    assets = {a["id"]: a for a in prod.get("assets", [])}
    musica = {m["id"]: m for m in prod.get("musica", [])}
    out = [f"# {titulo} — guion de producción", "",
           f"Público: {prod.get('publico', '—')} · Plantilla de subtítulos: {prod.get('plantilla_sub') or 'la predeterminada'}", ""]
    for n in _orden_piezas(prod):
        info = prod["piezas"][n]
        tipo = "cuerpo" if info.get("tipo") == "cuerpo" else tipo_de(n)
        out += [f"## {n} · {tipo.upper()}" + (f" · {info['tipo']}" if tipo == "hook" and info.get("tipo") else ""), "",
                f"**Voz:** {info.get('texto', '')}", ""]
        if info.get("portada"):
            out.append(f"**Portada:** {info['portada'].replace('*', '')}")
        if info.get("musica"):
            m = musica.get(info["musica"], {})
            out.append(f"**Música:** {info['musica']}" + (f" — {m.get('uso', '')}" if m else ""))
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
        if texto:
            S.guion.mkdir(parents=True, exist_ok=True)
            (S.guion / f"guion_{v}.md").write_text(texto, encoding="utf-8")
            hechos.append(f"guion_{v}.md")
    return hechos


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
        out.append({"nombre": nombre, "tipo": tipo, "tipo_hook": info.get("tipo") if tipo == "hook" else None,
                    "texto": texto, "partes": [x for x in partes if x.strip()]})
    orden = {"cuerpo": 0, "hook": 1, "cta": 2}
    return sorted(out, key=lambda x: (orden[x["tipo"]], x["nombre"]))


def leer_subs(pieza):
    f = S.subs / f"{pieza}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"pieza": pieza, "lineas": []}


def guardar_lineas_editadas(pieza, lineas_nuevas):
    """Recibe las líneas corregidas a mano y recalcula los tiempos de cada palabra."""
    sub = S.subs / f"{pieza}.json"
    datos = leer_subs(pieza)
    viejas = {(round(l["s"], 2), round(l["e"], 2)): l for l in datos["lineas"]}
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
        else:  # cambió el número de palabras: reparte el tiempo de la línea en partes iguales
            paso = (e - s) / len(palabras)
            pal = [{"w": w, "s": round(s + i * paso, 2), "e": round(s + (i + 1) * paso, 2)} for i, w in enumerate(palabras)]
        out.append({"texto": texto, "s": s, "e": e, "palabras": pal})
    datos["lineas"] = out
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
            perdidas.append(f"{pieza}: «{m.get('palabra')}»")
            continue
        li, wi = pos
        out.append({**m, "linea": li, "indice": wi, "t": lineas[li]["palabras"][wi]["s"]})
    return out, perdidas


# ---------- 2. Armar ----------

def combinaciones():
    ps = piezas()
    cuerpos = [p["nombre"] for p in ps if p["tipo"] == "cuerpo"]
    hooks = [p["nombre"] for p in ps if p["tipo"] == "hook"]
    ctas = [p["nombre"] for p in ps if p["tipo"] == "cta"]
    vs = leer_versiones()
    for c in cuerpos:
        # Un CTA con prefijo (P1_CTA1) es solo de ese cuerpo; uno sin prefijo (CTA-O) sirve para todos
        propios = [x for x in ctas if "_" not in x or x.split("_")[0] == c]
        for h in [h for h in hooks if h.split("_")[0] == c]:
            for cta in propios:
                codigo = f"{c}-{h.split('_')[1]}-{cta.split('_')[-1]}"
                propio = vs.get(codigo) if (vs.get(codigo) or {}).get("propio") else None
                # con ajustes propios («solo a este video») el video usa los suyos y no los de sus piezas
                yield {"codigo": codigo, "archivo": f"{S.prefijo}-{codigo}", "hook": h, "cuerpo": c, "cta": cta,
                       **({"version": propio} if propio else {})}
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
               "quitar": sorted({int(i) for i in datos.get("quitar", [])}),
               "recorte_inicio": max(0.0, float(datos.get("recorte_inicio") or 0)),
               "recorte_fin": max(0.0, float(datos.get("recorte_fin") or 0)), "nombre": (datos.get("nombre") or "").strip()[:60],
               # recortes por bloque: [entrada, salida] en segundos dentro del hook, del CTA o de cada frase del cuerpo
               "recortes": _limpiar_recortes(datos.get("recortes") or {}),
               # zoom por bloque: {"hook": {"tipo": "in|out|punch", "fuerza": 0.15}, "cta": …, "partes": {"2": …}}
               # y tramos de zoom: {"tramos": {"hook": [...], "cuerpo": [...], "cta": [...]}} (ver _limpiar_tramos)
               "zoom": _limpiar_zoom(datos.get("zoom") or {}),
               "cortes": _limpiar_cortes(datos.get("cortes")), "quitar_rangos": _limpiar_rangos(datos.get("quitar_rangos")),
               "capas": _limpiar_capas(datos.get("capas")), "velocidad": _limpiar_velocidad(datos.get("velocidad"))}
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


def _limpiar_capas(c):
    """Pista «Imágenes y escenas» del editor: {pieza: [{id, t, dur, modo, mov, x, y, ancho}]}. id = imagen o clip de
    la carpeta Flow de la sesión; t en segundos del clip de la pieza; en modo ventana, x/y/ancho en px del video 1080×1920."""
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
                if capa["modo"] == "ventana":
                    capa.update(ancho=int(min(1080, max(200, float(x.get("ancho", 860))))),
                                x=int(min(1080, max(-200, float(x.get("x", 110))))), y=int(min(1920, max(-200, float(x.get("y", 330))))))
                lista.append(capa)
            except (TypeError, ValueError, KeyError):
                continue
        if lista:
            out[k] = sorted(lista, key=lambda c: c["t"])
    return out


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
    propio = lambda pz: {"zoom_tramos": (z.get("tramos") or {}).get(pz), "zoom_puntos": (z.get("puntos") or {}).get(pz),
                         "cortes": cortes.get(pz), "quitar_rangos": rangos.get(pz), "capas": capas.get(pz)}
    nuevos = {
        datos["hook"]: {"recorte": r.get("hook"), "zoom": z.get("hook"), "velocidad": vel.get("hook"), **propio("hook")},
        datos["cuerpo"]: {"partes": r.get("partes") or {}, "zoom_partes": z.get("partes") or {}, **propio("cuerpo"),
                          "velocidad_partes": vel.get("partes"), "quitar": sorted({int(i) for i in datos.get("quitar", [])})},
        datos["cta"]: {"recorte": r.get("cta"), "zoom": z.get("cta"), "velocidad": vel.get("cta"), **propio("cta")},
    }
    cambiadas = []
    for pieza, val in nuevos.items():
        val = {k: v for k, v in val.items() if v not in (None, {}, [])}
        propios = ("editado", "sonidos", "sonidos_quitar")  # los sonidos a mano se guardan aparte (Sonidos del editor)
        antes = {k: v for k, v in todo.get(pieza, {}).items() if k not in propios}
        if val != antes:
            sonidos = {k: v for k, v in todo.get(pieza, {}).items() if k in ("sonidos", "sonidos_quitar")}
            todo[pieza] = {**val, **sonidos, "editado": time.time()}  # la fecha queda aunque se deshaga: sus videos se re-arman
            cambiadas.append(pieza)
    todo = {k: v for k, v in todo.items() if v}
    S.subs.mkdir(parents=True, exist_ok=True)
    _ajustes_piezas_path().write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    return [c["codigo"] for c in combinaciones() if not c.get("version")
            and {c["hook"], c["cuerpo"], c["cta"]} & set(cambiadas)]


def ajustes_silencios():
    return {"auto": True, "margen": 0.08, **CONFIG.get("silencios", {})}


def _con_silencios_fuera(combo, v):
    """Si un bloque no tiene recorte puesto a mano, se le corta el silencio del inicio y del final (según dónde
    empieza la primera palabra y termina la última, más un margen). Así ningún video arranca mudo."""
    aj = ajustes_silencios()
    if not aj["auto"]:
        return v
    m = max(0.0, min(0.5, float(aj["margen"])))
    r = dict(v.get("recortes") or {})

    def limites(nombre):
        lineas = leer_subs(nombre)["lineas"]
        f = S.proc / f"{nombre}.mp4"
        if not lineas or not f.exists():
            return None
        return lineas[0]["s"], lineas[-1]["e"], dur_audio(f)

    for clave in ("hook", "cta"):
        if r.get(clave) or not limites(combo[clave]):
            continue
        s0, e1, dur = limites(combo[clave])
        a = s0 - m if s0 - m > 0.06 else 0.0
        b = e1 + m * 2 if e1 + m * 2 < dur - 0.06 else dur
        if a > 0 or b < dur:
            r[clave] = [round(a, 2), round(b, 2)]
    partes = partes_de(combo["cuerpo"]) if limites(combo["cuerpo"]) else []
    if partes:
        s0, e1, _ = limites(combo["cuerpo"])
        pr = dict(r.get("partes") or {})
        p0, pn = partes[0], partes[-1]
        if str(p0["i"]) not in pr and s0 - m - p0["ini"] > 0.06:
            pr[str(p0["i"])] = [round(s0 - m - p0["ini"], 2), round(p0["fin"] - p0["ini"], 2)]
        if str(pn["i"]) not in pr and pn["fin"] - (e1 + m * 2) > 0.06:
            ini = pr[str(pn["i"])][0] if str(pn["i"]) in pr else 0.0
            pr[str(pn["i"])] = [ini, round(e1 + m * 2 - pn["ini"], 2)]
        if pr:
            r["partes"] = pr
    return {**v, "recortes": r}


def edicion_de(combo):
    """Recortes, frases quitadas y zoom que valen para este video: los de su versión, o los de sus piezas.
    Los bloques sin recorte puesto a mano empiezan justo antes de que se hable (ver _con_silencios_fuera)."""
    return _con_zoom_alterno(combo, _con_silencios_fuera(combo, _sin_claqueta(combo, _edicion_guardada(combo))))


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
    return out


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
                cambio = True
    return {**v, "recortes": r, "quitar_rangos": qr} if cambio else v


def estilo_edicion():
    return {"zoom_alterno": False, "fundido": 0.12, **CONFIG.get("estilo_edicion", {})}


def _con_zoom_alterno(combo, v):
    """«Zoom alternado» (como edita el equipo): una frase del cuerpo de lejos y la siguiente más cerca, en cada corte.
    Solo en las frases sin zoom puesto a mano y si el cuerpo no tiene fotogramas clave."""
    if not estilo_edicion()["zoom_alterno"]:
        return v
    z = dict(v.get("zoom") or {})
    if (z.get("puntos") or {}).get("cuerpo"):
        return v
    partes = dict(z.get("partes") or {})
    visibles = [p["i"] for p in partes_de(combo["cuerpo"]) if p["i"] not in (v.get("quitar") or [])]
    for k, i in enumerate(visibles):
        if k % 2 == 1 and str(i) not in partes:
            partes[str(i)] = {"tipo": "punch", "fuerza": 0.14}
    z["partes"] = partes
    return {**v, "zoom": z}


def _edicion_guardada(combo):
    if combo.get("version"):
        return combo["version"]
    a = ajustes_piezas()
    h, c, t = a.get(combo["hook"], {}), a.get(combo["cuerpo"], {}), a.get(combo["cta"], {})
    por_pieza = lambda campo: {k: v for k, v in (("hook", h.get(campo)), ("cuerpo", c.get(campo)), ("cta", t.get(campo))) if v}
    return {"quitar": c.get("quitar", []), "cortes": por_pieza("cortes"), "quitar_rangos": por_pieza("quitar_rangos"), "capas": por_pieza("capas"),
            "velocidad": {k: v for k, v in (("hook", h.get("velocidad")), ("cta", t.get("velocidad")), ("partes", c.get("velocidad_partes"))) if v},
            "recortes": {k: v for k, v in (("hook", h.get("recorte")), ("cta", t.get("recorte")), ("partes", c.get("partes"))) if v},
            "zoom": {k: v for k, v in (("hook", h.get("zoom")), ("cta", t.get("zoom")), ("partes", c.get("zoom_partes")),
                                       ("tramos", por_pieza("zoom_tramos")), ("puntos", por_pieza("zoom_puntos"))) if v}}


def editado_en(combo):
    """Último cambio (segundos) en algo que usa este video: subtítulos, marcas o recortes de sus piezas, o sus ajustes."""
    a = {} if combo.get("version") else ajustes_piezas()  # una versión guarda sus propios recortes
    t = 0.0
    for n in (combo["hook"], combo["cuerpo"], combo["cta"]):
        t = max(t, a.get(n, {}).get("editado", 0))
        f = S.subs / f"{n}.json"
        if f.exists():
            t = max(t, f.stat().st_mtime)
    # también si se cambió su plantilla de subtítulos o cuál es la predeterminada
    t = max(t, float(plantilla_sub(combo).get("editado") or 0), float(CONFIG.get("estilo_cambio") or 0))
    if not ajustes_video(combo["codigo"]).get("plantilla"):
        t = max(t, float(CONFIG.get("plantilla_activa_cambio") or 0))
    return max(t, ajustes_video(combo["codigo"]).get("editado", 0))


def _curva(p, curva):
    return {"lineal": p, "entrada": f"({p})*({p})", "salida": f"(1-(1-({p}))*(1-({p})))",
            "rebote": f"(1+2.70158*pow({p}-1,3)+1.70158*pow({p}-1,2))"}.get(curva, f"({p})*({p})*(3-2*({p}))")


def _expresion_zoom(combo, offs, total):
    """Expresiones de zoompan (zoom, x, y) según el tiempo del video sin recortes (it): primero los tramos de zoom
    (fotogramas clave con punto de foco); donde no hay tramo, el zoom por bloque de siempre. None si no hay zoom."""
    z = edicion_de(combo).get("zoom") or {}
    if not z:
        return None
    base = _expresion_zoom_bloques(combo, offs, total, z) or "1"
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
    if not segs and not puntos_segs and base == "1":
        return None
    # Todo se lleva a «centro de lo que se ve» (cx, cy). Un punto fijo f (tramos y zoom de siempre, hacia la cara)
    # equivale a un centro f·(1−1/zoom) + 0.5/zoom.
    ancla = lambda f: f"(({f})*(1-1/zoom)+0.5/zoom)"
    zoom, cx, cy = f"({base})", ancla("0.5"), ancla("0.38")
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
                            "palabras": [{"s": p["s"], "e": p["e"]} for p in l.get("palabras", [])]} for l in lineas]}

    v = edicion_de({k: c[k] for k in ("hook", "cuerpo", "cta")} if otra else c)  # otra combinación: parte de lo de sus piezas
    return {"codigo": codigo, "base": not c.get("version"), "propio": bool((c.get("version") or {}).get("propio")), "original": original, "hook": bloque(c["hook"]), "cuerpo": {**bloque(c["cuerpo"]), "partes": partes_de(c["cuerpo"])},
            "cta": bloque(c["cta"]), "quitar": v.get("quitar", []), "recorte_inicio": v.get("recorte_inicio", 0),
            "recorte_fin": v.get("recorte_fin", 0), "nombre": v.get("nombre", ""), "recortes": v.get("recortes", {}),
            "zoom": v.get("zoom", {}), "cortes": v.get("cortes", {}), "quitar_rangos": v.get("quitar_rangos", {}), "capas": v.get("capas", {}),
            "velocidad": v.get("velocidad", {}),
            "opciones": {t: [p["nombre"] for p in ps if p["tipo"] == t] for t in ("hook", "cuerpo", "cta")}}


def _rangos_a_quitar(combo, offs, total):
    v = edicion_de(combo)
    rangos = []
    if v.get("quitar"):
        partes = {p["i"]: p for p in partes_de(combo["cuerpo"])}
        rangos += [(offs[1] + partes[i]["ini"], offs[1] + partes[i]["fin"]) for i in v["quitar"] if i in partes]
    r = v.get("recortes") or {}
    durs = [offs[1], offs[2] - offs[1], total - offs[2]]
    for clave, off, dur in (("hook", 0.0, durs[0]), ("cta", offs[2], durs[2])):
        if r.get(clave):
            a, b = r[clave]
            rangos += [(off, off + a)] if a > 0 else []
            rangos += [(off + b, off + dur)] if b < dur - 0.02 else []
    if r.get("partes"):
        partes = {p["i"]: p for p in partes_de(combo["cuerpo"])}
        for i, (a, b) in r["partes"].items():
            p = partes.get(int(i))
            if not p or int(i) in v.get("quitar", []):
                continue
            ini, fin = offs[1] + p["ini"], offs[1] + p["fin"]
            rangos += [(ini, ini + a)] if a > 0 else []
            rangos += [(ini + b, fin)] if ini + b < fin - 0.02 else []
    o = {"hook": 0.0, "cuerpo": offs[1], "cta": offs[2]}
    for pz, lista in (v.get("quitar_rangos") or {}).items():
        if pz in o:
            rangos += [(o[pz] + a, o[pz] + b) for a, b in lista]
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
                out.append((offs[1] + p["ini"], offs[1] + p["fin"], f))
    return out


def _trozos(total, rangos, velocidades=()):
    """Lo que queda del video armado sin recortes: [(desde, hasta, factor)], partido donde cambia la velocidad."""
    juntos = []
    for a, b in sorted((max(0.0, a), min(total, b)) for a, b in rangos):
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
        cortes = sorted({a, b, *(x for va, vb, _ in velocidades for x in (va, vb) if a < x < b)})
        for x, y in zip(cortes, cortes[1:]):
            if y - x < 0.02:
                continue
            medio = (x + y) / 2
            fac = next((fv for va, vb, fv in velocidades if va <= medio <= vb), 1.0)
            trozos.append((x, y, fac))
    return trozos


def _mapa_tiempo(trozos):
    """(f, duración): f(t) pasa un segundo del video sin recortes al video editado (con las velocidades).
    Si ese segundo se quitó, devuelve None; con pegar=True, el inicio de lo que sigue."""
    tabla, g = [], 0.0
    for a, b, fac in trozos:
        tabla.append((a, b, fac, g))
        g += (b - a) / fac

    def f(t, pegar=False):
        for a, b, fac, g0 in tabla:
            if t < a - 1e-6:
                return g0 if pegar else None
            if t <= b + 1e-6:
                return g0 + (min(t, b) - a) / fac
        return g if pegar else None
    return f, g


def _filtro_tramos(v_in, a_in, quedan, v_out, a_out, pref="t"):
    """Filtros que dejan solo los tramos [(desde, hasta, factor)] de [v_in][a_in] y los unen en [v_out][a_out].
    Va dentro del filtro del armado: corta en la misma pasada en que pone los subtítulos (un solo codificado).
    La música y los sonidos se ponen después, sobre el video ya cortado, para que no salten en cada corte."""
    n = len(quedan)
    f = [f"[{v_in}]split={n}" + "".join(f"[{pref}sv{k}]" for k in range(n)),
         f"[{a_in}]asplit={n}" + "".join(f"[{pref}sa{k}]" for k in range(n))] if n > 1 else []
    for k, (a, b, fac) in enumerate(quedan):
        vel_v = f"/{fac:.4f}" if abs(fac - 1) > 0.001 else ""
        vel_a = f",atempo={fac:.4f}" if abs(fac - 1) > 0.001 else ""
        vi, ai = (f"{pref}sv{k}", f"{pref}sa{k}") if n > 1 else (v_in, a_in)
        f.append(f"[{vi}]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS){vel_v}[{pref}v{k}]")
        f.append(f"[{ai}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS{vel_a}[{pref}a{k}]")
    f.append("".join(f"[{pref}v{k}][{pref}a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[{v_out}][{a_out}]")
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
    trabajo = DATOS / "_trabajo" / f"p{os.getpid()}"
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


def plantilla_sub(combo=None):
    """Plantilla de subtítulos (fuente, colores, resaltador, posición). Si el video tiene una propia, esa."""
    todas = plantillas()
    propia = ajustes_video(combo["codigo"]).get("plantilla") if combo else None
    base = todas.get(propia) or todas[id_plantilla()]
    pl = {**PLANTILLA_BASE, **{k: v for k, v in base.items() if v not in (None, "")},
          **(ajustes_video(combo["codigo"]).get("ajustes_sub", {}) if combo else {})}
    if not float(pl.get("tamano") or 0):
        pl["tamano"] = PLANTILLA_BASE["tamano"]
    return pl


def _ajustes_video_path():
    return S.subs / "_ajustes_videos.json"


def ajustes_video(codigo):
    """Ajustes propios de un video: plantilla de subtítulos, posición del subtítulo."""
    try:
        return json.loads(_ajustes_video_path().read_text(encoding="utf-8")).get(codigo, {})
    except (FileNotFoundError, ValueError):
        return {}


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
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2",
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
    pl = plantilla_sub(combo)
    fu = fuentes.cargar(pl["fuente"])
    res = pl.get("resaltador") or {}
    modo = res.get("modo", "")  # "clave", "activa", "ambas" o "" (sin caja)
    c_activo, c_texto = color_ass(pl["color_activo"]), color_ass(pl["color_texto"])
    c_marca = color_ass(pl.get("color_resaltado", st.get("color_resaltado", "#4DE1FF")))
    ev = []

    todas = []
    for off, lineas, marcas in segmentos:
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
            todas.append((off, li, ln, estilo_linea.get(li), solas, resaltadas))

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
    # cuadro de texto: dónde va el subtítulo (centro horizontal y ancho máximo, en píxeles del video 1080×1920)
    x_centro = float(pl.get("x_centro", 540))
    ancho_max = max(200.0, min(1080.0, float(pl.get("ancho_max", 960))))
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

    def poner_linea(palabras, activa, estilo, ini, fin, cajas, coloreadas, pop):
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
            y_fin = 1920 - pl.get("altura_desde_abajo", st["altura_desde_abajo"])
            y0 = y_fin - bloque
        elif estilo == "arriba":
            y0 = pl.get("altura_desde_arriba", st.get("altura_desde_arriba", 300))
        else:
            y0 = 960 - bloque / 2
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

    for idx, (off, li, ln, estilo, solas, resaltadas) in enumerate(todas):
        pal = ln["palabras"]
        if not pal:
            continue
        sig = todas[idx + 1] if idx + 1 < len(todas) else None
        siguiente = sig[0] + sig[2]["s"] if sig else None
        fin_linea = off + ln["e"]
        if siguiente is not None and siguiente - fin_linea < 0.4:
            fin_linea = siguiente
        if estilo is None:  # regla automática: sobre una imagen a pantalla completa el subtítulo va al centro
            ta0, tb0 = off + ln["s"], fin_linea
            estilo = "sobre_imagen" if any(ta0 < tb and tb0 > ta for ta, tb in tapas) else "normal"
        claves = {j for j, q in enumerate(pal) if es_clave(q["w"]) or (li, j) in resaltadas}
        for i, p in enumerate(pal):
            ini = off + (ln["s"] if i == 0 else p["s"])
            fin = off + pal[i + 1]["s"] if i + 1 < len(pal) else fin_linea
            if fin <= ini:
                continue
            if (li, i) in solas:
                poner_linea([p["w"]], 0, "palabra_sola", ini, fin, {0} if modo else set(), set(), True)
                continue
            cajas = set()
            if modo in ("clave", "ambas"):
                cajas |= claves
            if modo in ("activa", "ambas"):
                cajas.add(i)
            poner_linea([q["w"] for q in pal], i, estilo, ini, fin, cajas, claves - cajas, i == 0)
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


def biblioteca_sonidos():
    """Las categorías de sonido con sus archivos: [{cat, nombre, archivos: [{id, nombre}]}]."""
    cats = list(CONFIG.get("sfx_mapa", {})) + [d.name for d in (BIBLIO_BASE / "sfx").iterdir() if d.is_dir()]
    out, vistas = [], set()
    for cat in cats:
        if cat in vistas:
            continue
        vistas.add(cat)
        archivos = [{"id": id_sonido(f), "nombre": f.stem} for f in sfx_de(cat) if id_sonido(f)]
        out.append({"cat": cat, "nombre": NOMBRES_SFX.get(cat, cat.replace("_", " ")), "archivos": archivos})
    return out


def editar_sonido(pieza, accion, t, cat, sid=None, cat_nuevo=None, t_nuevo=None, desde=None, largo=None, vol=None):
    """Sonido puesto o quitado a mano en una pieza (vale para todos los videos que la usan). t = segundo dentro de la pieza.
    accion: agregar | quitar | cambiar (quita el que había en ese segundo y pone el nuevo) | mover (a t_nuevo) |
    ajustar (mover y/o recortar: t_nuevo, desde, largo, vol). t es el segundo con el que la sala conoce ese sonido
    (para los automáticos, el de su marca). Devuelve los videos a re-armar."""
    todo = ajustes_piezas()
    a = todo.setdefault(pieza, {})
    t = round(float(t), 2)
    manuales = a.setdefault("sonidos", [])
    if accion in ("mover", "ajustar"):  # se quita de donde estaba y se pone el mismo sonido (recortado) en el segundo nuevo
        editar_sonido(pieza, "quitar", t, cat)
        return editar_sonido(pieza, "agregar", max(0.0, float(t if t_nuevo is None else t_nuevo)), cat,
                             sid if sid and ruta_sonido(sid) else None, desde=desde, largo=largo, vol=vol)
    if accion in ("quitar", "cambiar"):
        antes = len(manuales)
        manuales[:] = [x for x in manuales if not (abs(x["t"] - t) < 0.2 and x["cat"] == cat)]
        if len(manuales) == antes:  # era un sonido automático: se recuerda que no va
            a.setdefault("sonidos_quitar", []).append({"t": t, "cat": cat})
    if accion in ("agregar", "cambiar"):
        if sid and not ruta_sonido(sid):
            raise ValueError("Ese sonido no está en la biblioteca")
        extra = {k: round(float(v), 3) for k, v in (("desde", desde), ("largo", largo), ("vol", vol)) if v not in (None, "")}
        manuales.append({"t": t, "cat": cat_nuevo or cat, **({"archivo": sid} if sid else {}), **extra})
    a["editado"] = time.time()
    todo[pieza] = {k: v for k, v in a.items() if v not in ([], None)}
    S.subs.mkdir(parents=True, exist_ok=True)
    _ajustes_piezas_path().write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    return [c["codigo"] for c in combinaciones() if pieza in (c["hook"], c["cuerpo"], c["cta"])]


def elegir_sfx(categoria, semilla):
    """Rota entre las variantes de la categoría para que no suenen iguales en todos los videos."""
    for cat in [categoria] + CONFIG["sonidos"].get("respaldo", {}).get(categoria, []):
        opciones = sfx_de(cat)
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


def planear_sonidos(combo, offs, total, subs, marcas):
    """Decide qué efecto suena y cuándo. Prioridad 3 = siempre; 1-2 = solo si no hay otro muy cerca."""
    so = CONFIG["sonidos"]
    cand = []
    hook, cuerpo, cta = combo["hook"], combo["cuerpo"], combo["cta"]

    def add(cat, t, prio, motivo):
        cand.append({"cat": cat, "t": max(0.0, t), "prio": prio, "motivo": motivo})

    # Arranque del hook según su tipo (Promesa, Pregunta, Número, Contra, Producto)
    tipo = norm(info_pieza(hook).get("tipo", "")) or "default"
    arranque = so["arranque_por_tipo"].get(tipo) or so["arranque_por_tipo"]["default"]
    for k, cat in enumerate(arranque):
        add(cat, 0.0 + 0.05 * k, 3, f"arranque hook ({tipo})")
    # Riser que termina justo en el cuerpo, whoosh en los cortes
    add("riser", offs[1], 3, "riser antes del cuerpo")
    add("whoosh", offs[1] - 0.2, 3, "corte hook→cuerpo")
    add("whoosh_inverso", offs[2] - 0.25, 3, "corte cuerpo→CTA")
    # CTA: notificación (anuncio) o tecleo (orgánico)
    tipo_cta = norm(info_pieza(cta).get("tipo", "")) or ("anuncio" if cta.upper().endswith("A") else "organico")
    cat_cta = so["cta_por_tipo"].get(tipo_cta)
    if cat_cta:
        add(cat_cta, offs[2] + 0.3, 3, f"CTA {tipo_cta}")
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
    for (off, lineas), nombre in zip(zip(offs, subs), (hook, cuerpo, cta)):
        for ln in lineas:
            for p in ln["palabras"]:
                t = off + p["s"]
                if norm(p["w"]) == "suplevet" and not marca_vista and nombre == cuerpo:
                    add("brillo", t, 2, "aparece Suplevet")
                    marca_vista = True
                elif es_numero(p["w"]):
                    add("ding", t, 1, f"número «{p['w']}»")
                elif es_clave(p["w"]):
                    add("pop", t, 1, f"palabra clave «{p['w']}»")

    # Lo que el equipo cambió a mano en cada pieza (Sonidos del editor): se quitan los marcados y se suman los agregados
    a_mano = ajustes_piezas()
    for off, n in zip(offs, (hook, cuerpo, cta)):
        ap = a_mano.get(n, {})
        for q in ap.get("sonidos_quitar", []):
            cand = [c for c in cand if not (c["cat"] == q["cat"] and abs(c["t"] - (off + q["t"])) < 0.25)]
        for sm in ap.get("sonidos", []):
            cand.append({"cat": sm["cat"], "t": off + sm["t"], "prio": 4, "motivo": "puesto a mano", "archivo": sm.get("archivo"),
                         "a_mano": {k: sm[k] for k in ("desde", "largo", "vol") if k in sm}})
    # Filtro de densidad: un efecto opcional no puede caer muy cerca de otro
    elegidos = []
    sep = so["separacion_minima"]
    for c in sorted(cand, key=lambda c: (-c["prio"], c["t"])):
        if c["t"] >= total - 0.1:
            continue
        if c["prio"] < 3 and any(abs(c["t"] - e["t"]) < sep for e in elegidos if e["cat"] != "riser"):
            continue
        f = (ruta_sonido(c["archivo"]) if c.get("archivo") else None) or elegir_sfx(c["cat"], f"{combo['codigo']}|{c['cat']}|{len(elegidos)}")
        if not f:
            continue
        maxd = so.get("duracion_maxima", {}).get(c["cat"], so.get("duracion_maxima", {}).get("default", 2.5))
        d = dur_audio(f)
        t, desde, largo = c["t"], 0.0, min(d, maxd)
        if c["cat"] == "riser" and c["prio"] < 4:
            # el riser automático termina en el corte; si es largo se usa solo su final
            desde = max(0.0, d - maxd)
            t = max(0.0, c["t"] - largo)
        am = c.get("a_mano") or {}
        if "desde" in am:  # recortado a mano en el editor
            desde = max(0.0, min(float(am["desde"]), d - 0.05))
        if "largo" in am:
            largo = max(0.05, min(float(am["largo"]), d - desde))
        elegidos.append({**c, "t": round(t, 2), "tm": round(c["t"], 2), "archivo": f, "desde": round(desde, 2), "largo": round(largo, 2),
                         "dur_archivo": round(d, 2), "vol": float(am.get("vol", 1.0))})
    return sorted(elegidos, key=lambda e: e["t"]), silencios


def info_musica(mid):
    return next((m for m in produccion().get("musica", []) if m.get("id", "").lower() == str(mid).lower()), {})


def videos_que_usan(aid, tipo):
    """Códigos de los videos que usan esa música (tipo «musica») o esa imagen o clip de Flow (tipo «assets»):
    por el guion, por las marcas editadas en la sala o por la pista «Imágenes» del editor."""
    aid_l = aid.lower()
    prod = produccion().get("piezas", {})
    usan = set()
    for n in {p["nombre"] for p in piezas()}:
        ms = marcas_de(n) or []
        if tipo == "musica":
            if (prod.get(n, {}).get("musica") or "").lower() == aid_l or any((m.get("musica") or "").lower() == aid_l for m in ms):
                usan.add(n)
        elif any((m.get("asset") or "").lower() == aid_l for m in ms):
            usan.add(n)
    out = []
    for c in combinaciones():
        if {c["hook"], c["cuerpo"], c["cta"]} & usan:
            out.append(c["codigo"])
        elif tipo != "musica" and any(x["id"].lower() == aid_l for cs in (edicion_de(c).get("capas") or {}).values() for x in cs):
            out.append(c["codigo"])
    return out


def buscar_musica(mid):
    """Pista de Suno por id: carpeta Musica de la sesión, luego la carpeta de música general."""
    for d in (S.musica, CONFIG.get("carpeta_musica"), BIBLIO / "musica"):
        if d and Path(d).is_dir():
            for f in Path(d).iterdir():
                if f.stem.lower() == str(mid).lower() and f.suffix.lower() in EXT_AUDIO:
                    return f
    return None


def planear_musica(combo, offs, total, marcas, n_musica=0):
    """Tramos de música [{id, archivo, t0, t1, desde}]. Una pista por video (la del cuerpo); solo cambia donde
    el guion lo pide: el hook o el CTA con su propia pista, o una marca {"palabra", "musica"}."""
    hook, cuerpo, cta = combo["hook"], combo["cuerpo"], combo["cta"]
    base = (produccion().get("videos", {}).get(combo["codigo"], {}).get("musica")
            or info_pieza(cuerpo).get("musica") or produccion().get("musica_por_defecto"))
    cambios = [(0.0, info_pieza(hook).get("musica") or base)]
    if base:
        cambios.append((offs[1], base))
    if info_pieza(cta).get("musica"):
        cambios.append((offs[2], info_pieza(cta)["musica"]))
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

def filtro_broll(i, ov):
    """Cadena de filtro para una imagen/clip que entra en ov['t'] y dura ov['dur']."""
    dur, t, modo = ov["dur"], ov["t"], ov.get("modo", "completa")
    W, H, x, y = (1080, 1920, 0, 0) if modo == "completa" else (860, 860, 110, 330)
    if modo != "completa" and ov.get("ancho"):  # ventana colocada a mano en el editor
        W = H = int(ov["ancho"]) // 2 * 2
        x, y = int(ov.get("x", x)), int(ov.get("y", y))
    n = max(1, int(dur * 30))
    if ov["es_imagen"]:
        mov = ov.get("mov", "zoom_in")
        # zoom lento (10 % en toda la duración). Se agranda 3 veces antes del zoompan para que el movimiento
        # sea suave (zoompan mueve de a pixel entero: en tamaño normal la imagen tiembla)
        z = {"zoom_in": f"1+0.10*on/{n}", "zoom_out": f"1.10-0.10*on/{n}"}.get(mov, "1")
        cadena = (f"[{i}:v]scale={W * 3}:{H * 3}:force_original_aspect_ratio=increase,"
                  f"crop={W * 3}:{H * 3},zoompan=z='{z}':x='trunc(iw/2-(iw/zoom/2))':y='trunc(ih/2-(ih/zoom/2))'"
                  f":d=1:s={W}x{H}:fps=30,setsar=1")
    else:
        cadena = f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,setsar=1"
    if modo != "completa":
        cadena += f",pad={W + 16}:{H + 16}:8:8:white"
        x, y = x - 8, y - 8
    fd = max(0.05, min(float(estilo_edicion().get("fundido", 0.12)), dur / 3))  # «fundido»: entra y sale suave
    cadena += (f",format=yuva420p,fade=t=in:st=0:d={fd:.2f}:alpha=1"
               + (f",fade=t=out:st={max(0, dur - fd):.2f}:d={fd:.2f}:alpha=1" if fd > 0.15 else "")
               + f",setpts=PTS-STARTPTS+{t:.3f}/TB[o{i}]")
    return cadena, x, y


# ---------- Portada ----------

POSICION_TITULO = {"arriba": 560, "centro": 960, "abajo": 1380}  # dentro del recorte 4:5 que muestra el perfil de Instagram


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
        t = " ".join(palabras[:7]) + ("…" if len(palabras) > 7 else "")
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
    crudo = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(f), "-vf", f"fps={fps},scale={w}:{h},format=gray",
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


CAMPOS_PORTADA = ("segundo", "titulo", "posicion", "y", "logo", "oscurecer", "fuente", "tamano", "color_texto", "color_acento",
                  "borde", "color_borde", "caja", "color_caja", "fondo", "solo_imagen", "elementos")


def hacer_portada(combo, archivo_video=None, prueba=None, destino=None):
    """JPG 1080x1920: un cuadro del hook (o una imagen propia) con el título y el logo, y los elementos que se agreguen.
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
    y = int(float(aj["y"])) if aj.get("y") not in (None, "") else POSICION_TITULO.get(aj.get("posicion", "centro"), 960)
    tam = int(float(aj.get("tamano") or cfg["tamano"]))
    borde = float(aj["borde"]) if aj.get("borde") not in (None, "") else 12
    if aj.get("caja"):  # caja de color detrás del título (estilo de borde 3: el borde se vuelve caja)
        estilo, contorno, sombra = 3, color_ass(aj.get("color_caja") or "#99D3DA"), 0
        borde = max(borde, 18)
    else:
        estilo, contorno, sombra = 1, color_ass(aj.get("color_borde") or pl.get("color_borde", "#000000")), 7
    cab = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: T,{fuente},{tam},{blanco},&H00FFFFFF&,{contorno},&H90000000&,0,0,0,0,100,100,0,0,{estilo},{borde},{sombra},5,90,90,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        "" if aj.get("solo_imagen") else f"Dialogue: 0,0:00:00.00,0:00:10.00,T,,0,0,0,,{{\\an5\\pos(540,{y})}}{titulo}",
    ])
    (trabajo / "portada.ass").write_text(cab + "\n", encoding="utf-8")
    salida = destino or S.portadas / f"{combo['archivo']}.jpg"
    oscuro = float(aj.get("oscurecer", 0))
    llenar = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
    fondo = ruta_elemento(aj["fondo"]) if aj.get("fondo") else None
    if fondo:  # imagen propia de portada
        cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(fondo)]
    else:
        segundo = float(aj["segundo"]) if aj.get("segundo") is not None else segundo_portada(combo)
        cmd = [FFMPEG, "-hide_banner", "-y", "-ss", f"{segundo:.2f}", "-i", str(S.proc / f"{combo['hook']}.mp4")]
    f = [f"[0:v]{llenar}" + (f",drawbox=x=0:y=0:w=iw:h=ih:color=black@{oscuro}:t=fill" if oscuro > 0 else "") + "[base]"]
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
    logo = BIBLIO_BASE / "marca" / "logo-blanco.png"
    if aj.get("logo", True) and logo.exists() and not aj.get("solo_imagen"):
        # el logo va arriba si el título está abajo o al centro, y abajo si el título está arriba
        ylogo = 1490 if y < 900 else 330
        cmd += ["-i", str(logo)]
        f.append(f"[{k}:v]scale=380:-1,split[l][s];[s]colorchannelmixer=rr=0:gg=0:bb=0:aa=0.6,boxblur=8[sombra];"  # sombra suave
                 f"[t][sombra]overlay=(W-w)/2+3:{ylogo}+4[t2];[t2][l]overlay=(W-w)/2:{ylogo}[v]")
        previo = "v"
    run(cmd + ["-filter_complex", ";".join(f), "-map", f"[{previo}]", "-frames:v", "1", "-q:v", "2", str(salida)], cwd=trabajo)
    return salida


# ---------- Armar un video ----------

def armar_uno(combo, n_musica=0, extras=None):
    """Arma un video. Paso 1: imagen (zoom, imágenes, subtítulos) con la voz. Paso 2: cortes y velocidades.
    Paso 3: música y sonidos sobre el video ya cortado (así la música sigue de corrido y no se acelera).
    extras: carpeta donde dejar además los elementos por separado (sin subtítulos, voz, música, efectos, .srt)."""
    for d in (S.salida, S.recetas):
        d.mkdir(parents=True, exist_ok=True)
    trabajo = carpeta_trabajo()
    orden = [combo["hook"], combo["cuerpo"], combo["cta"]]
    archivos = [S.proc / f"{n}.mp4" for n in orden]
    durs = [duracion(a) for a in archivos]
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
    o_pieza = {"hook": offs[0], "cuerpo": offs[1], "cta": offs[2]}
    for pz, capas in (edicion_de(combo).get("capas") or {}).items():
        for c in capas:
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

    cmd = [FFMPEG, "-hide_banner", "-y"]
    for a in archivos:
        cmd += ["-i", str(a)]
    idx = 3  # la música y los sonidos entran en el paso 3
    f = ["[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[vc][ac]"]
    v_prev = "vc"
    zexpr = _expresion_zoom(combo, offs, total)
    if zexpr:  # zoom (antes de imágenes y subtítulos, que no se acercan); el punto de foco queda fijo en pantalla
        f.append(f"[vc]zoompan=z='{zexpr['z']}':x='{zexpr['x']}':y='{zexpr['y']}':d=1:s=1080x1920:fps=30,setsar=1[vz]")
        v_prev = "vz"
    for ov in overlays:
        if ov["es_imagen"]:
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{ov['dur']:.2f}", "-i", str(ov["archivo"])]
        else:
            cmd += ["-t", f"{ov['dur']:.2f}", "-i", str(ov["archivo"])]
        cadena, x, y = filtro_broll(idx, ov)
        f.append(cadena)
        f.append(f"[{v_prev}][o{idx}]overlay={x}:{y}:eof_action=pass:enable='between(t,{ov['t']:.2f},{ov['t'] + ov['dur']:.2f})'[v{idx}]")
        v_prev = f"v{idx}"
        idx += 1
    # Elementos de la plantilla (logo, imágenes, GIF): encima del video y debajo del subtítulo, todo el video
    for el in plantilla_sub(combo).get("elementos") or []:
        arch = ruta_elemento(el.get("archivo", ""))
        if not arch:
            continue
        if arch.suffix.lower() == ".gif":
            cmd += ["-ignore_loop", "0", "-t", f"{total:.2f}", "-i", str(arch)]
        else:
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{total:.2f}", "-i", str(arch)]
        ancho = max(8, int(float(el.get("ancho", 200))))
        opac = max(0.0, min(1.0, float(el.get("opacidad", 100)) / 100))
        f.append(f"[{idx}:v]scale={ancho}:-1,format=rgba,colorchannelmixer=aa={opac:.2f}[e{idx}]")
        f.append(f"[{v_prev}][e{idx}]overlay={int(float(el.get('x', 0)))}:{int(float(el.get('y', 0)))}:eof_action=pass[v{idx}]")
        v_prev = f"v{idx}"
        idx += 1
    if extras:
        f.append(f"[{v_prev}]split=2[vlimpio][vs]")
        f.append("[vs]subtitles=subs.ass:fontsdir=fuentes[v]")
        f.append("[ac]asplit=2[ac1][ac2]")
    else:
        f.append(f"[{v_prev}]subtitles=subs.ass:fontsdir=fuentes[v]")
    # cortes, frases quitadas y velocidades: en la misma pasada (antes era otra pasada que volvía a codificar todo
    # el video y tardaba lo mismo que el armado)
    rangos, velocidades = _rangos_a_quitar(combo, offs, total), _rangos_velocidad(combo, offs, total)
    trozos = _trozos(total, rangos, velocidades)
    salidas_v = [("v", "ac1" if extras else "ac")] + ([("vlimpio", "ac2")] if extras else [])
    if (rangos or velocidades) and trozos:
        for k, (vl, al) in enumerate(salidas_v):
            f += _filtro_tramos(vl, al, trozos, f"{vl}_c", f"{al}_c", pref=f"q{k}")
        salidas_v = [(f"{vl}_c", f"{al}_c") for vl, al in salidas_v]
    # paso 1: la imagen con la voz, ya cortada (sin música ni sonidos todavía); en .mkv con el audio sin comprimir
    base, limpio = trabajo / "base.mkv", trabajo / "limpio.mkv"
    venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "pcm_s16le", "-t", f"{total:.2f}"]
    cmd += ["-filter_complex", ";".join(f)]
    for (vl, al), destino in zip(salidas_v, (base, limpio)):
        cmd += ["-map", f"[{vl}]", "-map", f"[{al}]", "-dn", "-map_chapters", "-1", *venc, str(destino)]
    run(cmd, cwd=trabajo)
    mapa, total_ed = _mapa_tiempo(trozos) if trozos else ((lambda t, pegar=False: t), total)
    # los tiempos de la música y los sonidos pasan al video editado
    tramos_ed = []
    for tr in tramos:
        t0, t1 = mapa(tr["t0"], True), mapa(tr["t1"], True)
        if t1 - t0 > 0.3:
            tramos_ed.append({**tr, "t0": t0, "t1": t1})
    silencios_ed = [(mapa(a, True), mapa(b, True)) for a, b in silencios]
    silencios_ed = [(a, b) for a, b in silencios_ed if b - a > 0.05]
    eventos_ed = [{**e, "t": mapa(e["t"])} for e in eventos]
    eventos_ed = [e for e in eventos_ed if e["t"] is not None]
    _mezclar_audio(base, S.salida / f"{combo['archivo']}.mp4", tramos_ed, silencios_ed, eventos_ed, total_ed, extras)
    if extras:
        _elementos_por_separado(extras, limpio, combo, orden, offs, subs, mapa, overlays)
    total = duracion(S.salida / f"{combo['archivo']}.mp4")
    hacer_portada(combo)

    receta = {**combo, "duracion": round(total, 2), "cortes": [round(o, 2) for o in offs[1:]],
              # dónde termina el hook y dónde empieza el CTA en el video editado
              "limites": [round(mapa(offs[1], True), 2), round(mapa(offs[2], True), 2)],
              "musica": " → ".join(f"{t['id'] or t['archivo'].stem} ({t['t0']:.1f} s)" for t in tramos_ed) or None,
              "faltan_musica": faltan_musica,
              # t: segundo del video sin recortes (lo usa el editor); te: segundo en el video armado (None si se cortó)
              "efectos": [{"sfx": f"{e['cat']}/{e['archivo'].name}", "cat": e["cat"], "id": id_sonido(e["archivo"]), "t": e["t"],
                           "tm": e.get("tm", e["t"]), "desde": e.get("desde", 0), "largo": e.get("largo"), "dur_archivo": e.get("dur_archivo"),
                           "vol": e.get("vol", 1.0),
                           "te": None if mapa(e["t"]) is None else round(mapa(e["t"]), 2), "motivo": e["motivo"]} for e in eventos],
              "imagenes": [{"asset": o["asset"], "t": round(o["t"], 2), "te": round(mapa(o["t"], True), 2), "dur": round(o["dur"], 2),
                            "modo": o["modo"]} for o in overlays],
              # pistas de música en el video sin recortes, para escucharlas en la vista previa del editor
              "pistas": [{"t0": round(t["t0"], 2), "t1": round(t["t1"], 2), "desde": t["desde"], "archivo": str(t["archivo"]),
                          "id": t["id"]} for t in tramos],
              "volumen_musica": CONFIG["musica"]["volumen"],
              "faltan_assets": sorted(set(faltan)), "marcas_sin_encontrar": perdidas}
    (S.recetas / f"{combo['codigo']}.json").write_text(json.dumps(receta, ensure_ascii=False, indent=1), encoding="utf-8")
    for v in (base, limpio):
        v.unlink(missing_ok=True)
    return receta


def _mezclar_audio(base, salida, tramos, silencios, eventos, total, extras=None):
    """Paso 3 del armado: a la voz del video ya cortado se le suman la música (baja sola cuando se habla)
    y los sonidos. La imagen se copia tal cual. Con extras, deja también voz, música y efectos en .wav."""
    cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(base)]
    idx = 1
    i_mus = []
    for tr in tramos:
        cmd += ["-stream_loop", "-1", "-i", str(tr["archivo"])]
        i_mus.append(idx)
        idx += 1
    f = []
    salidas_voz = ["[voz]"] + (["[sc]"] if tramos else []) + (["[vozx]"] if extras else [])
    f.append(f"[0:a]asplit={len(salidas_voz)}{''.join(salidas_voz)}" if len(salidas_voz) > 1 else "[0:a]anull[voz]")
    mezcla = ["[voz]"]
    if tramos:
        cruce = 0.4  # segundos de fundido cuando cambia la música
        for k, (tr, i) in enumerate(zip(tramos, i_mus)):
            largo = tr["t1"] - tr["t0"] + (cruce if k < len(tramos) - 1 else 0)
            ms = int(tr["t0"] * 1000)
            f.append(f"[{i}:a]atrim=start={tr['desde']:.2f}:duration={largo:.2f},asetpts=N/SR/TB,"
                     f"aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"afade=t=in:st=0:d={0.05 if k == 0 else cruce},afade=t=out:st={max(0, largo - cruce):.2f}:d={cruce},"
                     f"adelay={ms}|{ms}[mt{k}]")
        juntar = "".join(f"[mt{k}]" for k in range(len(tramos)))
        f.append(f"{juntar}amix=inputs={len(tramos)}:normalize=0:duration=longest,atrim=0:{total:.2f}[mraw]"
                 if len(tramos) > 1 else f"[mt0]atrim=0:{total:.2f}[mraw]")
        mute = "".join(f",volume=0:enable='between(t,{a:.2f},{b:.2f})'" for a, b in silencios)
        f.append(f"[mraw]volume={CONFIG['musica']['volumen']}{mute}[m0]")
        if extras:
            f.append("[m0][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350,asplit=2[mus][musx]")
        else:
            f.append("[m0][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[mus]")
        mezcla.append("[mus]")
    vol = CONFIG["sonidos"]["volumen_sfx"]
    sfx = []
    for k, e in enumerate(eventos):
        cmd += ["-i", str(e["archivo"])]
        ms = int(e["t"] * 1000)
        vk = (vol * CONFIG["sonidos"].get("volumen_por_categoria", {}).get(e["cat"], 1.0) * float(e.get("vol", 1.0))
              * ganancia_normalizada(e["archivo"]))  # todos al mismo nivel; luego el volumen de su tipo y el puesto a mano
        recorte = (f"atrim={e['desde']:.2f}:{e['desde'] + e['largo']:.2f},asetpts=N/SR/TB,"
                   f"afade=t=out:st={max(0, e['largo'] - 0.15):.2f}:d=0.15,")
        f.append(f"[{idx}:a]{recorte}aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms},volume={vk:.2f}"
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


def exportar_partes(codigos, avance=print):
    """«Exportar por partes»: cada video en su carpeta (Listos/POR_PARTES/<video>) con el video completo, el hook,
    el cuerpo y el CTA sueltos, el video sin subtítulos, voz, música y efectos en .wav, los subtítulos en .srt
    y las imágenes que usa. Sirve para terminarlo en CapCut, Premiere o pasarlo a otro editor."""
    por_codigo = {c["codigo"]: c for c in combinaciones()}
    hechos = []
    for n, cod in enumerate(codigos):
        c = por_codigo.get(cod)
        if not c:
            continue
        destino = S.listos / "POR_PARTES" / c["archivo"]
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
        hechos.append(cod)
    return hechos


def armar(pieza=None, avance=print, codigos=None):
    hechos = []
    todos = [c for c in combinaciones() if (not pieza or pieza in (c["hook"], c["cuerpo"], c["cta"]))
             and (codigos is None or c["codigo"] in codigos)]
    for n, combo in enumerate(todos):
        avance(f"Armando {combo['archivo']} ({n + 1}/{len(todos)})")
        r = armar_uno(combo, n if CONFIG["musica"]["rotar_pistas"] else 0)
        hechos.append(r["codigo"])
    return hechos


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
    base = cp.get("cuerpos", {}).get(c["cuerpo"], {})
    gancho = cp.get("hooks", {}).get(c["hook"], "")
    cta_id = c["cta"] if c["cta"] in cp.get("ctas", {}) else c["cta"].split("_")[-1]
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


AJUSTES_EXPORTAR = {"resolucion": "1080", "fps": 30, "codec": "h264", "calidad": "alta", "portada_inicio": True, "por_dia": False,
                    "peso_max": 0}  # MB por video; 0 = sin límite
RESOLUCIONES = {"720": (720, 1280), "1080": (1080, 1920), "1440": (1440, 2560), "2160": (2160, 3840)}
CALIDADES = {"ligera": 26, "recomendada": 22, "alta": 19, "maxima": 16}  # CRF: menos = mejor y más pesado


def ajustes_exportar():
    return {**AJUSTES_EXPORTAR, **CONFIG.get("exportar", {})}


def render_final(video, portada, destino, aj):
    """Exporta con la calidad elegida. Con «peso_max» (MB), si el video sale más pesado se vuelve a exportar
    en dos pasadas con el bitrate justo para que quede por debajo de ese peso."""
    _render_final(video, portada, destino, aj)
    limite = float(aj.get("peso_max") or 0)
    if limite > 0 and destino.stat().st_size > limite * 1048576:
        dur = max(1.0, duracion(destino))
        kbps = int((limite * 0.94 * 8 * 1024) / dur - 192)  # deja margen para el contenedor y el audio de 192 kbps
        _render_final(video, portada, destino, {**aj, "bitrate": max(400, kbps)})


def _render_final(video, portada, destino, aj):
    """Video final para subir a redes con los ajustes de exportación. Con «portada_inicio», el primer cuadro del video
    es la portada (Instagram y TikTok la toman como miniatura si eliges el primer cuadro)."""
    w, h = RESOLUCIONES.get(str(aj["resolucion"]), (1080, 1920))
    fps = int(aj.get("fps") or 30)
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
               "-filter_complex", f"[0:v]{escala},format=yuv420p[p];[1:v]{escala},format=yuv420p[v];[p][v]concat=n=2:v=1:a=0[vo];"
                                  f"[1:a]adelay={ms}:all=1,aresample=48000[ao]",
               "-map", "[vo]", "-map", "[ao]"]
    else:
        cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(video), "-vf", f"{escala},format=yuv420p", "-af", "aresample=48000"]
    run(cmd + [*venc, "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
               "-dn", "-map_chapters", "-1", "-movflags", "+faststart", str(destino)])


def exportar(avance=print, codigos=None, ajustes=None):
    """Deja en V.FINALES (listos) cada video con los ajustes de exportación, su portada (.jpg) y su copy (.txt).
    Por días según el calendario (DIA_X) si «por_dia» o si no se eligieron videos; si no, todos juntos."""
    aj = {**ajustes_exportar(), **(ajustes or {})}
    por_codigo = {c["codigo"]: c for c in combinaciones()}
    hechos = []
    if codigos and not aj.get("por_dia"):
        plan = {"": list(codigos)}
    else:
        cal = calendario()
        plan = {d: [c for c in cs if not codigos or c in codigos] for d, cs in cal.items()}
        sueltos = [c for c in (codigos or []) if not any(c in cs for cs in cal.values())]
        if sueltos:
            plan["SIN_DIA"] = sueltos
    for dia, codigos in plan.items():
        destino = S.listos / dia if dia else S.listos
        destino.mkdir(parents=True, exist_ok=True)
        for cod in codigos:
            c = por_codigo.get(cod)
            if not c:
                continue
            v, p = S.salida / f"{c['archivo']}.mp4", S.portadas / f"{c['archivo']}.jpg"
            if not v.exists():
                continue
            avance(f"Exportando {len(hechos) + 1} · {(dia + ' · ') if dia else ''}{c['archivo']}")
            render_final(v, p, destino / v.name, aj)
            if p.exists():
                shutil.copy2(p, destino / p.name)
            cp = copy_de(cod)
            (destino / f"{c['archivo']}.txt").write_text(
                f"INSTAGRAM\n{cp.get('instagram', '')}\n\nTIKTOK\n{cp.get('tiktok', '')}\n\n"
                f"YOUTUBE · título\n{cp.get('youtube_titulo', '')}\n\nYOUTUBE · descripción\n{cp.get('youtube', '')}\n",
                encoding="utf-8")
            hechos.append(cod)
    return hechos


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
            if dur > 90:
                p.append({"nivel": "err", "txt": f"Dura {dur:.0f} s: muy largo para Reels o TikTok (ideal menos de 60 s)"})
            elif dur > 60:
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
        altura = float(ajustes_video(c["codigo"]).get("altura") or pl.get("altura_desde_abajo") or 560)
        if altura < 330:
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
    falta_piezas = [n for n in p.get("piezas", {}) if n not in hay
                    and not ("CTA" in n.upper() and (n.split("_")[0] in con_cta or ("_" not in n and con_cta)))]
    assets = []
    for a in p.get("assets", []):
        f = buscar_asset(a["id"])
        assets.append({**a, "listo": bool(f), "archivo_actual": f.name if f else None})
    musica = [{**m, "listo": bool(buscar_musica(m["id"])), "archivo_actual": (buscar_musica(m["id"]) or Path("")).name or None}
              for m in produccion().get("musica", []) if m.get("id")]
    sug = sugerencias()
    rp = reemplazos()
    sin = [{"rel": c["rel"], "nombre": c["ruta"].name, "carpeta": c["tipo_carpeta"], **sug.get(c["rel"], {}),
            **({"reemplazado": rp[c["rel"]]} if c["rel"] in rp else {})}
           for c in clips_sin_asignar()]
    return {"piezas_faltan": falta_piezas, "assets": assets, "sin_asignar": sin, "musica": musica}


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
