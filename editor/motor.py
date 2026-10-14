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


def grupos():
    try:
        return json.loads(GRUPOS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def guardar_grupos(g):
    GRUPOS_PATH.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")


class Rutas:
    def __init__(self, semana):
        self.semana = semana
        base = Path(CONFIG.get("carpeta_trabajo") or DATOS / "grabaciones")
        self.base = base
        g = grupos().get(semana)
        self.es_grupo = bool(g)
        self.miembros = list(g["sesiones"]) if g else [semana]
        # sesión principal: ahí viven el guion, Flow, la música y V.FINALES (en un grupo, la que tiene el guion)
        self.drive = (g.get("guion") or g["sesiones"][0]) if g else semana
        self.principal = base / self.drive
        # en un grupo, los clips de todas sus sesiones se ven desde la carpeta de arriba («11／10 SESIÓN/Clips/…»)
        self.grab = base if g else base / semana
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
        fecha = re.match(r"^(\d{1,2})\D+(\d{1,2})", self.drive)   # "13 10 SESIÓN" -> 1310
        n = re.search(r"(\d+)", self.drive)
        self.prefijo = (f"{int(fecha.group(1)):02d}{int(fecha.group(2)):02d}" if fecha
                        else f"S{int(n.group(1)):02d}" if n else "S00")

    def carpeta(self, tipo):
        return _primera(self.principal, CARPETAS[tipo])

    def carpetas_sesiones(self):
        return [self.base / m for m in self.miembros]


S = Rutas(CONFIG["semana"])


def usar_semana(semana):
    global S, _prod_cache
    S = Rutas(semana)
    _prod_cache = None
    if S.es_grupo:
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
        n = d.name.upper()
        return d.is_dir() and ("SESI" in n or n.startswith("SEMANA") or (d / "Clips").is_dir())

    def orden(nombre):  # "13 10 SESIÓN" -> (10, 13); el resto al final por nombre
        m = re.match(r"^(\d{1,2})\D+(\d{1,2})", nombre)
        return (0, int(m.group(2)), int(m.group(1)), nombre) if m else (1, 0, 0, nombre)

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
    return S.base / sesion / "asignaciones.json", DATOS / "asignaciones" / f"{sesion}.json"


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


def guardar_asignacion(rel, pieza):
    """Devuelve la sesión donde quedó guardada (para subir su asignaciones.json a Drive)."""
    sesion = S.semana
    if S.es_grupo:
        sesion, rel = rel.split("/", 1)
        if sesion not in S.miembros:
            raise ValueError("Ese clip no es de este grupo")
    d = _leer_asig(sesion)
    if pieza:
        if not CODIGO_PIEZA.match(pieza):
            raise ValueError(f"«{pieza}» no es un código de pieza (C1_H2, C3, C1_P2, CTA-A…)")
        d[rel] = pieza.upper()
    else:
        d.pop(rel, None)
    texto = json.dumps(d, ensure_ascii=False, indent=1)
    drive_f, local_f = _asig_archivos(sesion)
    local_f.parent.mkdir(parents=True, exist_ok=True)
    local_f.write_text(texto, encoding="utf-8")
    try:
        drive_f.write_text(texto, encoding="utf-8")
    except OSError:
        pass
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
        out.append(fila)
    sin_escuchar = sum(1 for c in clips_sin_asignar() if transcripcion_clip(c, crear=False) is None)
    return {"hay_guion": bool(prod.get("piezas")), "piezas": out, "sin_escuchar": sin_escuchar,
            "sin_procesar": sum(1 for f in out if f["clips"] and not f["procesada"]), "sin_asignar": len(sugs)}


def guion_para_grabar_actual():
    """El guion de la sesión o grupo abierto (sin cambiar de sesión)."""
    return guion_para_grabar(None)


# ---------- Forma de onda del audio (para cortar guiándose por la voz) ----------

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
        if vieja and len(vieja["palabras"]) == len(palabras):
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


def resolver_marcas(pieza, lineas):
    """Convierte las marcas en eventos con tiempo (relativo a la pieza)."""
    out, perdidas = [], []
    for m in marcas_de(pieza):
        pos = ubicar(lineas, m.get("palabra", ""), m.get("n", 1))
        if pos is None:
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
    for c in cuerpos:
        # Un CTA con prefijo (P1_CTA1) es solo de ese cuerpo; uno sin prefijo (CTA-O) sirve para todos
        propios = [x for x in ctas if "_" not in x or x.split("_")[0] == c]
        for h in [h for h in hooks if h.split("_")[0] == c]:
            for cta in propios:
                codigo = f"{c}-{h.split('_')[1]}-{cta.split('_')[-1]}"
                yield {"codigo": codigo, "archivo": f"{S.prefijo}-{codigo}", "hook": h, "cuerpo": c, "cta": cta}
    nombres = {p["nombre"] for p in ps}
    for vid, v in leer_versiones().items():
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
    """Crea o actualiza una versión. Devuelve su código (C1-H2-CTA-v2)."""
    vs = leer_versiones()
    base = f"{datos['cuerpo']}-{datos['hook'].split('_')[-1]}-{datos['cta'].split('_')[-1]}"
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
               "zoom": _limpiar_zoom(datos.get("zoom") or {})}
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
    return {k: v for k, v in out.items() if v}


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
    nuevos = {
        datos["hook"]: {"recorte": r.get("hook"), "zoom": z.get("hook")},
        datos["cuerpo"]: {"partes": r.get("partes") or {}, "zoom_partes": z.get("partes") or {},
                          "quitar": sorted({int(i) for i in datos.get("quitar", [])})},
        datos["cta"]: {"recorte": r.get("cta"), "zoom": z.get("cta")},
    }
    cambiadas = []
    for pieza, val in nuevos.items():
        val = {k: v for k, v in val.items() if v not in (None, {}, [])}
        antes = {k: v for k, v in todo.get(pieza, {}).items() if k != "editado"}
        if val != antes:
            todo[pieza] = {**val, "editado": time.time()}  # la fecha queda aunque se deshaga: sus videos se re-arman
            cambiadas.append(pieza)
    todo = {k: v for k, v in todo.items() if v}
    S.subs.mkdir(parents=True, exist_ok=True)
    _ajustes_piezas_path().write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    return [c["codigo"] for c in combinaciones() if not c.get("version")
            and {c["hook"], c["cuerpo"], c["cta"]} & set(cambiadas)]


def edicion_de(combo):
    """Recortes, frases quitadas y zoom que valen para este video: los de su versión, o los de sus piezas."""
    if combo.get("version"):
        return combo["version"]
    a = ajustes_piezas()
    h, c, t = a.get(combo["hook"], {}), a.get(combo["cuerpo"], {}), a.get(combo["cta"], {})
    return {"quitar": c.get("quitar", []),
            "recortes": {k: v for k, v in (("hook", h.get("recorte")), ("cta", t.get("recorte")), ("partes", c.get("partes"))) if v},
            "zoom": {k: v for k, v in (("hook", h.get("zoom")), ("cta", t.get("zoom")), ("partes", c.get("zoom_partes"))) if v}}


def editado_en(combo):
    """Último cambio (segundos) en algo que usa este video: subtítulos, marcas o recortes de sus piezas, o sus ajustes."""
    a = {} if combo.get("version") else ajustes_piezas()  # una versión guarda sus propios recortes
    t = 0.0
    for n in (combo["hook"], combo["cuerpo"], combo["cta"]):
        t = max(t, a.get(n, {}).get("editado", 0))
        f = S.subs / f"{n}.json"
        if f.exists():
            t = max(t, f.stat().st_mtime)
    return max(t, ajustes_video(combo["codigo"]).get("editado", 0))


def _expresion_zoom(combo, offs, total):
    """Expresión de zoom para zoompan según el tiempo del video (it): acercar, alejar o «punch» por bloque."""
    z = edicion_de(combo).get("zoom") or {}
    if not z:
        return None
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
    vs = leer_versiones()
    if vs.pop(vid, None) is not None:
        _versiones_path().write_text(json.dumps(vs, ensure_ascii=False, indent=1), encoding="utf-8")
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


def estructura(codigo):
    """Lo que necesita el editor de un video: sus piezas, las frases del cuerpo y las opciones para cambiarlas."""
    c = next((x for x in combinaciones() if x["codigo"] == codigo), None)
    if not c:
        raise ValueError("Ese video no existe en esta sesión")
    ps = piezas()

    def bloque(nombre):
        f = S.proc / f"{nombre}.mp4"
        lineas = leer_subs(nombre)["lineas"]
        return {"nombre": nombre, "dur": round(duracion(f), 2) if f.exists() else None,
                "texto": " ".join(l["texto"] for l in lineas), "archivo": str(f) if f.exists() else None,
                "lineas": [{"texto": l["texto"], "s": l["s"], "e": l["e"]} for l in lineas]}

    v = edicion_de(c)
    return {"codigo": codigo, "base": not c.get("version"), "hook": bloque(c["hook"]), "cuerpo": {**bloque(c["cuerpo"]), "partes": partes_de(c["cuerpo"])},
            "cta": bloque(c["cta"]), "quitar": v.get("quitar", []), "recorte_inicio": v.get("recorte_inicio", 0),
            "recorte_fin": v.get("recorte_fin", 0), "nombre": v.get("nombre", ""), "recortes": v.get("recortes", {}),
            "zoom": v.get("zoom", {}),
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
    if v.get("recorte_inicio"):
        rangos.append((0.0, min(float(v["recorte_inicio"]), total - 1)))
    if v.get("recorte_fin"):
        rangos.append((max(0.0, total - float(v["recorte_fin"])), total))
    return sorted(rangos)


def _quitar_tramos(video, rangos):
    """Saca del video ya armado los tramos indicados (frases quitadas, recortes): se quedan los tramos que
    sobran, cortados con trim (preciso al cuadro) y unidos; voz, música y subtítulos van juntos."""
    total = duracion(video)
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
    if not quedan:
        return
    f = []
    for k, (a, b) in enumerate(quedan):
        f.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{k}]")
        f.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{k}]")
    f.append("".join(f"[v{k}][a{k}]" for k in range(len(quedan))) + f"concat=n={len(quedan)}:v=1:a=1[v][a]")
    tmp = video.with_name(video.stem + "_corto.mp4")
    run([FFMPEG, "-hide_banner", "-y", "-i", str(video), "-filter_complex", ";".join(f), "-map", "[v]", "-map", "[a]", "-dn", "-map_chapters", "-1",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)])
    tmp.replace(video)


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
    """Carpeta temporal de armado, con las fuentes al lado (ffmpeg no acepta bien rutas con espacios y ':')."""
    trabajo = DATOS / "_trabajo"
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


def plantilla_sub(combo=None):
    """Plantilla de subtítulos (fuente, colores, resaltador, posición). Si el video tiene una propia, esa."""
    todas = plantillas()
    propia = ajustes_video(combo["codigo"]).get("plantilla") if combo else None
    base = todas.get(propia) or todas[id_plantilla()]
    return {**base, **(ajustes_video(combo["codigo"]).get("ajustes_sub", {}) if combo else {})}


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

    def partir(textos):
        """Índices de cada renglón: un solo renglón, o dos parejos por cantidad de letras."""
        n = len(textos)
        if not l2.get("activa") or n < 2:
            return [list(range(n))]
        largos = [len(t) for t in textos]
        mejor = min(range(1, n), key=lambda k: abs(sum(largos[:k]) - sum(largos[k:])))
        return [list(range(mejor)), list(range(mejor, n))]

    def poner_linea(palabras, activa, estilo, ini, fin, cajas, coloreadas, pop):
        tam0 = pl["tamano"] * ESCALA_ESTILO[estilo]
        textos = [limpiar(w.upper() if pl.get("mayusculas", True) else w) for w in palabras]
        filas = partir(textos) if estilo != "palabra_sola" else [list(range(len(textos)))]
        medidas = []
        for r, idx in enumerate(filas):
            f_ = fu2 if r == 1 else fu
            tam = tam0 * (l2.get("escala", 100) / 100 if r == 1 else 1)
            esp = f_.ancho(" ", tam) * 1.1
            # la palabra activa se ve más grande: se le reserva ese espacio para que no pise a sus vecinas
            crece = {j: (pl.get("escala_activa", 108) / 100 if j == activa else 1.0) for j in idx}
            letras = float(pl.get("espaciado", 0)) * tam / 100  # espaciado entre letras (% del tamaño)
            anchos = [(f_.ancho(textos[j], tam) + letras * len(textos[j])) * crece[j] for j in idx]
            total = sum(anchos) + esp * (len(idx) - 1)
            if total > ancho_max:  # no entra en el cuadro de texto: se achica el renglón entero
                f = ancho_max / total
                tam, esp, anchos, total = tam * f, esp * f, [a * f for a in anchos], ancho_max
            medidas.append({"fu": f_, "tam": tam, "esp": esp, "anchos": anchos, "total": total, "idx": idx, "crece": crece, "r": r,
                            "fsp": letras * tam / (tam0 * (l2.get("escala", 100) / 100 if r == 1 else 1))})
        altos = [m["tam"] * 1.02 for m in medidas]
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

    # Filtro de densidad: un efecto opcional no puede caer muy cerca de otro
    elegidos = []
    sep = so["separacion_minima"]
    for c in sorted(cand, key=lambda c: (-c["prio"], c["t"])):
        if c["t"] >= total - 0.1:
            continue
        if c["prio"] < 3 and any(abs(c["t"] - e["t"]) < sep for e in elegidos if e["cat"] != "riser"):
            continue
        f = elegir_sfx(c["cat"], f"{combo['codigo']}|{c['cat']}|{len(elegidos)}")
        if not f:
            continue
        maxd = so.get("duracion_maxima", {}).get(c["cat"], so.get("duracion_maxima", {}).get("default", 2.5))
        d = dur_audio(f)
        t, desde = c["t"], 0.0
        if c["cat"] == "riser":  # el riser termina en el corte; si es largo se usa solo su final
            desde = max(0.0, d - maxd)
            t = max(0.0, c["t"] - min(d, maxd))
        elegidos.append({**c, "t": round(t, 2), "archivo": f, "desde": round(desde, 2), "largo": round(min(d, maxd), 2)})
    return sorted(elegidos, key=lambda e: e["t"]), silencios


def info_musica(mid):
    return next((m for m in produccion().get("musica", []) if m.get("id", "").lower() == str(mid).lower()), {})


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
    n = max(1, int(dur * 30))
    if ov["es_imagen"]:
        mov = ov.get("mov", "zoom_in")
        z = {"zoom_in": f"1+0.12*on/{n}", "zoom_out": f"1.12-0.12*on/{n}"}.get(mov, "1")
        cadena = (f"[{i}:v]scale={int(W * 1.2)}:{int(H * 1.2)}:force_original_aspect_ratio=increase,"
                  f"crop={int(W * 1.2)}:{int(H * 1.2)},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":d=1:s={W}x{H}:fps=30,setsar=1")
    else:
        cadena = f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,setsar=1"
    if modo != "completa":
        cadena += f",pad={W + 16}:{H + 16}:8:8:white"
        x, y = x - 8, y - 8
    cadena += f",format=yuva420p,fade=t=in:st=0:d=0.12:alpha=1,setpts=PTS-STARTPTS+{t:.3f}/TB[o{i}]"
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
    limpio = {k: datos[k] for k in ("segundo", "titulo", "posicion", "logo", "oscurecer") if k in datos}
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


def segundo_portada(combo):
    a = ajustes_portada(combo["codigo"])
    return float(a.get("segundo", info_pieza(combo["hook"]).get("portada_segundo", CONFIG["portada"]["segundo"])))


def cuadro(combo, segundo, destino, ancho=540):
    """Un cuadro del hook (sin título) para elegir la portada."""
    run([FFMPEG, "-hide_banner", "-y", "-ss", f"{float(segundo):.2f}", "-i", str(S.proc / f"{combo['hook']}.mp4"),
         "-frames:v", "1", "-vf", f"scale={ancho}:-2", "-q:v", "4", str(destino)])
    return destino


def hacer_portada(combo, archivo_video=None, prueba=None, destino=None):
    """prueba: ajustes sin guardar (para ver la portada mientras se edita); destino: dónde dejar el JPG."""
    """JPG 1080x1920 a color: un cuadro del hook con el título grande y el logo de Suplevet.
    *palabra* entre asteriscos sale con el color de la plantilla. Todo queda dentro del recorte 4:5 del perfil."""
    S.portadas.mkdir(parents=True, exist_ok=True)
    trabajo = carpeta_trabajo()
    cfg = CONFIG["portada"]
    aj = {**ajustes_portada(combo["codigo"]), **(prueba or {})}
    import fuentes
    pl = plantilla_sub(combo)
    fuente = fuentes.cargar(pl["fuente"]).nombre
    acento = color_ass((pl.get("resaltador") or {}).get("color") or pl["color_activo"])
    titulo = aj.get("titulo") or texto_portada(combo)
    if pl.get("mayusculas", True):
        titulo = titulo.upper()
    titulo = re.sub(r"\*(.+?)\*", lambda m: f"{{\\c{acento}}}{limpiar(m.group(1))}{{\\c&H00FFFFFF&}}", limpiar(titulo.replace("{", "")))
    y = POSICION_TITULO.get(aj.get("posicion", "centro"), 960)
    cab = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: T,{fuente},{cfg['tamano']},&H00FFFFFF&,&H00FFFFFF&,{color_ass(pl.get('color_borde', '#000000'))},&H90000000&,0,0,0,0,100,100,0,0,1,12,7,5,90,90,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,0:00:00.00,0:00:10.00,T,,0,0,0,,{{\\an5\\pos(540,{y})}}{titulo}",
    ])
    (trabajo / "portada.ass").write_text(cab + "\n", encoding="utf-8")
    salida = destino or S.portadas / f"{combo['archivo']}.jpg"
    oscuro = float(aj.get("oscurecer", 0))
    filtro = (f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{oscuro}:t=fill," if oscuro > 0 else "") + "subtitles=portada.ass:fontsdir=fuentes"
    logo = BIBLIO_BASE / "marca" / "logo-blanco.png"
    segundo = float(aj["segundo"]) if aj.get("segundo") is not None else segundo_portada(combo)
    cmd = [FFMPEG, "-hide_banner", "-y", "-ss", f"{segundo:.2f}", "-i", str(S.proc / f"{combo['hook']}.mp4")]
    if aj.get("logo", True) and logo.exists():
        # el logo va arriba si el título está abajo o al centro, y abajo si el título está arriba
        ylogo = 1490 if aj.get("posicion") == "arriba" else 330
        cmd += ["-i", str(logo), "-filter_complex",
                f"[0:v]{filtro}[t];[1:v]scale=380:-1,split[l][s];"
                f"[s]colorchannelmixer=rr=0:gg=0:bb=0:aa=0.6,boxblur=8[sombra];"  # sombra suave para que se lea sobre paredes claras
                f"[t][sombra]overlay=(W-w)/2+3:{ylogo}+4[t2];[t2][l]overlay=(W-w)/2:{ylogo}[v]", "-map", "[v]"]
    else:
        cmd += ["-vf", filtro]
    run(cmd + ["-frames:v", "1", "-q:v", "2", str(salida)], cwd=trabajo)
    return salida


# ---------- Armar un video ----------

def armar_uno(combo, n_musica=0):
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
    for off, ms in zip(offs, marcas):
        for m in ms:
            if not m.get("asset"):
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
    tapas = [(o["t"], o["t"] + o["dur"]) for o in overlays if o["modo"] == "completa"]
    escribir_ass(list(zip(offs, subs, marcas)), trabajo / "subs.ass", tapas, combo)

    eventos, silencios = planear_sonidos(combo, offs, total, subs, marcas)
    tramos, faltan_musica = planear_musica(combo, offs, total, marcas, n_musica)

    cmd = [FFMPEG, "-hide_banner", "-y"]
    for a in archivos:
        cmd += ["-i", str(a)]
    idx = 3
    i_mus = []
    for tr in tramos:
        cmd += ["-stream_loop", "-1", "-i", str(tr["archivo"])]
        i_mus.append(idx)
        idx += 1
    f = ["[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[vc][ac]"]
    v_prev = "vc"
    zexpr = _expresion_zoom(combo, offs, total)
    if zexpr:  # zoom por bloque (antes de imágenes y subtítulos, que no se acercan); encuadre hacia la cara
        f.append(f"[vc]zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*0.38':d=1:s=1080x1920:fps=30,setsar=1[vz]")
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
    f.append(f"[{v_prev}]subtitles=subs.ass:fontsdir=fuentes[v]")
    mezcla = []
    if tramos:
        f.append("[ac]asplit=2[voz][sc]")
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
        f.append("[m0][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[mus]")
        mezcla += ["[voz]", "[mus]"]
    else:
        mezcla.append("[ac]")
    vol = CONFIG["sonidos"]["volumen_sfx"]
    for k, e in enumerate(eventos):
        cmd += ["-i", str(e["archivo"])]
        ms = int(e["t"] * 1000)
        vk = vol * CONFIG["sonidos"].get("volumen_por_categoria", {}).get(e["cat"], 1.0)
        recorte = (f"atrim={e['desde']:.2f}:{e['desde'] + e['largo']:.2f},asetpts=N/SR/TB,"
                   f"afade=t=out:st={max(0, e['largo'] - 0.15):.2f}:d=0.15,")
        f.append(f"[{idx}:a]{recorte}aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms},volume={vk:.2f}[s{k}]")
        mezcla.append(f"[s{k}]")
        idx += 1
    f.append(f"{''.join(mezcla)}amix=inputs={len(mezcla)}:normalize=0:duration=first,alimiter=limit=0.95[a]")
    salida = S.salida / f"{combo['archivo']}.mp4"
    cmd += ["-filter_complex", ";".join(f), "-map", "[v]", "-map", "[a]", "-dn", "-map_chapters", "-1", "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(salida)]
    run(cmd, cwd=trabajo)
    rangos = _rangos_a_quitar(combo, offs, total)
    if rangos:
        _quitar_tramos(salida, rangos)
        total = duracion(salida)
    hacer_portada(combo)

    receta = {**combo, "duracion": round(total, 2), "cortes": [round(o, 2) for o in offs[1:]],
              "musica": " → ".join(f"{t['id'] or t['archivo'].stem} ({t['t0']:.1f} s)" for t in tramos) or None,
              "faltan_musica": faltan_musica,
              "efectos": [{"sfx": f"{e['cat']}/{e['archivo'].name}", "t": e["t"], "motivo": e["motivo"]} for e in eventos],
              "imagenes": [{"asset": o["asset"], "t": round(o["t"], 2), "dur": round(o["dur"], 2), "modo": o["modo"]} for o in overlays],
              "faltan_assets": sorted(set(faltan)), "marcas_sin_encontrar": perdidas}
    (S.recetas / f"{combo['codigo']}.json").write_text(json.dumps(receta, ensure_ascii=False, indent=1), encoding="utf-8")
    return receta


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


AJUSTES_EXPORTAR = {"resolucion": "1080", "fps": 30, "codec": "h264", "calidad": "alta", "portada_inicio": True, "por_dia": False}
RESOLUCIONES = {"720": (720, 1280), "1080": (1080, 1920), "1440": (1440, 2560), "2160": (2160, 3840)}
CALIDADES = {"ligera": 26, "recomendada": 22, "alta": 19, "maxima": 16}  # CRF: menos = mejor y más pesado


def ajustes_exportar():
    return {**AJUSTES_EXPORTAR, **CONFIG.get("exportar", {})}


def render_final(video, portada, destino, aj):
    """Video final para subir a redes con los ajustes de exportación. Con «portada_inicio», el primer cuadro del video
    es la portada (Instagram y TikTok la toman como miniatura si eliges el primer cuadro)."""
    w, h = RESOLUCIONES.get(str(aj["resolucion"]), (1080, 1920))
    fps = int(aj.get("fps") or 30)
    crf = CALIDADES.get(aj.get("calidad"), 19)
    venc = (["-c:v", "libx265", "-tag:v", "hvc1", "-crf", str(crf + 4)] if aj.get("codec") == "hevc"
            else ["-c:v", "libx264", "-crf", str(crf), "-profile:v", "high"])
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


# ---------- Checklist ----------

def checklist():
    """Qué falta grabar y qué imágenes/clips de Flow faltan."""
    p = produccion()
    hay = {x["nombre"] for x in piezas()}
    falta_piezas = [n for n in p.get("piezas", {}) if n not in hay]
    assets = []
    for a in p.get("assets", []):
        f = buscar_asset(a["id"])
        assets.append({**a, "listo": bool(f), "archivo_actual": f.name if f else None})
    musica = [{**m, "listo": bool(buscar_musica(m["id"])), "archivo_actual": (buscar_musica(m["id"]) or Path("")).name or None}
              for m in produccion().get("musica", []) if m.get("id")]
    sug = sugerencias()
    sin = [{"rel": c["rel"], "nombre": c["ruta"].name, "carpeta": c["tipo_carpeta"], **sug.get(c["rel"], {})}
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
