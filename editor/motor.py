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


class Rutas:
    def __init__(self, semana):
        self.semana = semana
        base = Path(CONFIG.get("carpeta_trabajo") or DATOS / "grabaciones")
        self.base = base
        self.grab = base / semana                    # en Drive: lo que suben tú y el editor
        self.assets = _primera(self.grab, ["Flow", "assets"])
        self.listos = _primera(self.grab, ["V.FINALES", "VIDEOS FINALES", "listos"])
        self.guion = _primera(self.grab, ["Guion", "1_guion"])
        self.asignaciones = self.grab / "asignaciones.json"
        self.proc = DATOS / "procesado" / semana      # local: pesado, no se sincroniza
        self.subs = DATOS / "subtitulos" / semana
        self.salida = DATOS / "salidas" / semana
        self.portadas = DATOS / "salidas" / semana / "portadas"
        self.recetas = DATOS / "recetas" / semana
        self.transcripciones = DATOS / "transcripciones" / semana
        self.copies_editados = DATOS / "subtitulos" / semana / "_copies_editados.json"
        fecha = re.match(r"^(\d{1,2})\D+(\d{1,2})", semana)   # "13 10 SESIÓN" -> 1310
        n = re.search(r"(\d+)", semana)
        self.prefijo = (f"{int(fecha.group(1)):02d}{int(fecha.group(2)):02d}" if fecha
                        else f"S{int(n.group(1)):02d}" if n else "S00")

    def carpeta(self, tipo):
        return _primera(self.grab, CARPETAS[tipo])


S = Rutas(CONFIG["semana"])


def usar_semana(semana):
    global S, _prod_cache
    S = Rutas(semana)
    _prod_cache = None


def semanas_disponibles():
    if not S.base.exists():
        return []
    def es_sesion(d):
        n = d.name.upper()
        return d.is_dir() and ("SESI" in n or n.startswith("SEMANA") or (d / "Clips").is_dir())

    def orden(nombre):  # "13 10 SESIÓN" -> (10, 13); el resto al final por nombre
        m = re.match(r"^(\d{1,2})\D+(\d{1,2})", nombre)
        return (0, int(m.group(2)), int(m.group(1)), nombre) if m else (1, 0, 0, nombre)

    return sorted((d.name for d in S.base.iterdir() if es_sesion(d)), key=orden)


def guardar_config(cambios):
    """Guarda en config.local.json (no se comparte entre PCs)."""
    CONFIG.update(cambios)
    local = json.loads(LOCAL_PATH.read_text(encoding="utf-8")) if LOCAL_PATH.exists() else {}
    local.update(cambios)
    LOCAL_PATH.write_text(json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Utilidades ----------

def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"Falló: {' '.join(map(str, cmd))[:300]}\n{r.stderr[-1500:]}")
    return r


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
              RAIZ.parent / "semanas" / S.semana / "produccion.json"):
        if f.exists():
            m = f.stat().st_mtime
            if not _prod_cache or _prod_cache[0] != (f, m):
                _prod_cache = ((f, m), json.loads(f.read_text(encoding="utf-8")))
            return _prod_cache[1]
    return {}


def info_pieza(nombre):
    return produccion().get("piezas", {}).get(nombre, {})


# C1_H2 (hook), C1 (cuerpo), C1_P3 (parte 3 del cuerpo C1), CTA / CTA-A / C1_CTA1
CODIGO_PIEZA = re.compile(r"^((?:C|P)\d+_H\d+|(?:C|P)\d+(?:_P\d+)?|(?:(?:C|P)\d+_)?CTA[-_]?[A-Z0-9]*)$", re.I)
PARTE = re.compile(r"^((?:C|P)\d+)_P(\d+)$", re.I)


def tipo_de(nombre):
    n = nombre.upper()
    return "cta" if "CTA" in n else "hook" if re.search(r"_H\d+$", n) else "cuerpo"


def _asig_local():
    return DATOS / "asignaciones" / f"{S.semana}.json"


def leer_asignaciones():
    """Une la copia de Drive (compartida con el equipo) y la local de esta PC; la local manda.
    La local existe porque Drive borra en silencio lo que escribe una cuenta con permiso de solo lectura."""
    d = {}
    for f in (S.asignaciones, _asig_local()):
        try:
            d.update(json.loads(f.read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError, OSError):
            pass
    return d


def guardar_asignacion(rel, pieza):
    d = leer_asignaciones()
    if pieza:
        if not CODIGO_PIEZA.match(pieza):
            raise ValueError(f"«{pieza}» no es un código de pieza (C1_H2, C3, C1_P2, CTA-A…)")
        d[rel] = pieza.upper()
    else:
        d.pop(rel, None)
    texto = json.dumps(d, ensure_ascii=False, indent=1)
    _asig_local().parent.mkdir(parents=True, exist_ok=True)
    _asig_local().write_text(texto, encoding="utf-8")
    try:
        S.asignaciones.write_text(texto, encoding="utf-8")
    except OSError:
        pass


def clips():
    """Todos los videos de la sesión: [{rel, ruta, tipo_carpeta}]. tipo_carpeta = None si están sueltos en Clips/."""
    vistos, out = set(), []
    for tipo, opciones in CARPETAS.items():
        for o in opciones:
            d = S.grab / o
            if d.is_dir():
                for f in sorted(d.rglob("*"), key=lambda x: x.as_posix().lower()):
                    if f.is_file() and f.suffix.lower() in EXT_VID and f not in vistos:
                        vistos.add(f)
                        out.append({"rel": f.relative_to(S.grab).as_posix(), "ruta": f, "tipo_carpeta": tipo})
    sueltos = S.grab / CARPETA_SUELTOS
    if sueltos.is_dir():
        for f in sorted(sueltos.iterdir()):
            if f.is_file() and f.suffix.lower() in EXT_VID and f not in vistos:
                out.append({"rel": f.relative_to(S.grab).as_posix(), "ruta": f, "tipo_carpeta": None})
    return out


def nombre_automatico(c):
    """Nombre de pieza que se deduce sin asignar a mano:
    C1_H2.mov · Cuerpo/3.MOV -> C1_P3 · Cuerpo/C2/1.MOV -> C2_P1 · CTA/CTA.MOV -> CTA."""
    stem, tipo = c["ruta"].stem, c["tipo_carpeta"]
    if CODIGO_PIEZA.match(stem):
        return stem.upper()
    if tipo == "cuerpo" and stem.isdigit():
        padre = c["ruta"].parent.name
        cuerpo = padre.upper() if re.fullmatch(r"(?:C|P)\d+", padre, re.I) else "C1"
        return f"{cuerpo}_P{int(stem)}"
    if tipo == "cta" and stem.upper().startswith("CTA"):
        return stem.upper()
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
    return [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for s in segs for w in s.words]


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
        if (not dest.exists() or vieja != lista
                or dest.stat().st_mtime < max(x.stat().st_mtime for x in p["partes"])):
            avance(f"Procesando {p['nombre']} ({n}/{len(ps)})" + (f" · {len(lista)} partes" if len(lista) > 1 else ""))
            trozos = []
            for k, parte in enumerate(p["partes"]):
                ini, fin = limites_de_voz(parte)
                t = dest if len(lista) == 1 else S.proc / f"_{p['nombre']}_{k}.mp4"
                run([FFMPEG, "-hide_banner", "-y", "-ss", f"{ini:.2f}", "-to", f"{fin:.2f}", "-i", str(parte),
                     "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1",
                     "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000", "-ac", "2",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(t)])
                trozos.append(t)
            if len(trozos) > 1:
                lst = S.proc / f"_{p['nombre']}.txt"
                lst.write_text("".join(f"file '{t.name}'\n" for t in trozos), encoding="utf-8")
                run([FFMPEG, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", dest.name], cwd=S.proc)
                for t in trozos + [lst]:
                    t.unlink(missing_ok=True)
            firma.write_text(json.dumps(lista, ensure_ascii=False), encoding="utf-8")
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
    ya = {p["nombre"] for p in piezas()}
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


def t_ass(t):
    cs = int(round(max(0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def color_ass(hexrgb):
    h = hexrgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&"


def es_clave(w):
    return norm(w) in {norm(x) for x in CONFIG["palabras_clave"]}


def limpiar(w):
    return w.replace("{", "").replace("}", "").replace("\\", "")


def cabecera_ass():
    st = CONFIG["subtitulos"]
    f, t = st["fuente"], st["tamano"]
    estilo = "{n},{f},{t},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H64000000&,-1,0,0,0,100,100,0,0,1,{o},{sh},{al},90,90,{mv},1"
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: " + estilo.format(n="normal", f=f, t=t, o=7, sh=3, al=2, mv=st["altura_desde_abajo"]),
        "Style: " + estilo.format(n="centro_grande", f=f, t=int(t * 1.35), o=8, sh=3, al=5, mv=0),
        "Style: " + estilo.format(n="arriba", f=f, t=t, o=7, sh=3, al=8, mv=st.get("altura_desde_arriba", 300)),
        "Style: " + estilo.format(n="sobre_imagen", f=f, t=int(t * 1.2), o=10, sh=5, al=5, mv=0),
        "Style: " + estilo.format(n="palabra_sola", f=f, t=int(t * 2.0), o=11, sh=4, al=5, mv=0),
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", ""])


ESTILOS_SUB = ["normal", "centro_grande", "arriba", "palabra_sola", "resaltado"]


def escribir_ass(segmentos, ruta, tapas=()):
    """segmentos: [(offset, lineas, marcas_resueltas)]. tapas: [(ini, fin)] donde hay imagen a pantalla completa."""
    st = CONFIG["subtitulos"]
    activo, resalte = color_ass(st["color_activo"]), color_ass(st.get("color_resaltado", "#4DE1FF"))
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
            a, b = off + ln["s"], fin_linea
            estilo = "sobre_imagen" if any(a < tb and b > ta for ta, tb in tapas) else "normal"
        for i, p in enumerate(pal):
            ini = off + (ln["s"] if i == 0 else p["s"])
            fin = off + pal[i + 1]["s"] if i + 1 < len(pal) else fin_linea
            if fin <= ini:
                continue
            if (li, i) in solas:
                w = limpiar(p["w"].upper() if st["mayusculas"] else p["w"])
                ev.append(f"Dialogue: 1,{t_ass(ini)},{t_ass(fin)},palabra_sola,,0,0,0,,"
                          f"{{\\c{activo}\\fscx60\\fscy60\\t(0,110,\\fscx110\\fscy110)\\t(110,180,\\fscx100\\fscy100)}}{w}")
                continue
            partes = []
            for j, q in enumerate(pal):
                w = limpiar(q["w"].upper() if st["mayusculas"] else q["w"])
                base = resalte if (li, j) in resaltadas else "&H00FFFFFF&"
                if j == i:
                    escala = 118 if es_clave(q["w"]) or (li, j) in resaltadas else 108
                    w = f"{{\\c{activo}\\fscx{escala}\\fscy{escala}}}{w}{{\\c{base}\\fscx100\\fscy100}}"
                elif base != "&H00FFFFFF&":
                    w = f"{{\\c{base}}}{w}{{\\c&H00FFFFFF&}}"
                partes.append(w)
            pop = "{\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
            ev.append(f"Dialogue: 0,{t_ass(ini)},{t_ass(fin)},{estilo},,0,0,0,,{pop}{' '.join(partes)}")
    ruta.write_text(cabecera_ass() + "\n".join(ev) + "\n", encoding="utf-8")


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

def texto_portada(combo):
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


def hacer_portada(combo, archivo_video=None):
    """JPG 1080x1920: un cuadro del hook, oscurecido, con el título grande al centro.
    *palabra* entre asteriscos sale en amarillo. El título queda dentro del recorte 4:5 del perfil de Instagram."""
    S.portadas.mkdir(parents=True, exist_ok=True)
    trabajo = DATOS / "_trabajo"
    trabajo.mkdir(exist_ok=True)
    cfg = CONFIG["portada"]
    st = CONFIG["subtitulos"]
    titulo = texto_portada(combo).upper()
    amarillo = color_ass(st["color_activo"])
    titulo = re.sub(r"\*(.+?)\*", lambda m: f"{{\\c{amarillo}}}{limpiar(m.group(1))}{{\\c&H00FFFFFF&}}", limpiar(titulo.replace("{", "")))
    cab = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: T,{st['fuente']},{cfg['tamano']},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H80000000&,-1,0,0,0,100,100,0,0,1,12,6,5,80,80,0,1",
        f"Style: M,{st['fuente']},44,&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H80000000&,-1,0,0,0,100,100,4,0,1,5,2,8,80,80,{cfg['margen_marca']},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,0:00:00.00,0:00:10.00,T,,0,0,0,,{titulo}",
    ])
    if cfg.get("marca"):
        cab += f"\nDialogue: 0,0:00:00.00,0:00:10.00,M,,0,0,0,,{limpiar(cfg['marca'])}"
    (trabajo / "portada.ass").write_text(cab + "\n", encoding="utf-8")
    seg = info_pieza(combo["hook"]).get("portada_segundo", cfg["segundo"])
    fuente = S.proc / f"{combo['hook']}.mp4"
    salida = S.portadas / f"{combo['archivo']}.jpg"
    run([FFMPEG, "-hide_banner", "-y", "-ss", f"{float(seg):.2f}", "-i", str(fuente), "-frames:v", "1",
         "-vf", f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{cfg['oscurecer']}:t=fill,subtitles=portada.ass",
         "-q:v", "2", str(salida)], cwd=trabajo)
    return salida


# ---------- Armar un video ----------

def armar_uno(combo, n_musica=0):
    for d in (S.salida, S.recetas):
        d.mkdir(parents=True, exist_ok=True)
    trabajo = DATOS / "_trabajo"
    trabajo.mkdir(exist_ok=True)
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
    escribir_ass(list(zip(offs, subs, marcas)), trabajo / "subs.ass", tapas)

    eventos, silencios = planear_sonidos(combo, offs, total, subs, marcas)
    musica = pistas_musica()
    pista = musica[n_musica % len(musica)] if musica else None

    cmd = [FFMPEG, "-hide_banner", "-y"]
    for a in archivos:
        cmd += ["-i", str(a)]
    idx = 3
    i_mus = None
    if pista:
        cmd += ["-stream_loop", "-1", "-i", str(pista)]
        i_mus, idx = idx, idx + 1
    f = ["[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[vc][ac]"]
    v_prev = "vc"
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
    f.append(f"[{v_prev}]subtitles=subs.ass[v]")
    mezcla = []
    if pista:
        f.append("[ac]asplit=2[voz][sc]")
        mute = "".join(f",volume=0:enable='between(t,{a:.2f},{b:.2f})'" for a, b in silencios)
        f.append(f"[{i_mus}:a]atrim=0:{total:.2f},asetpts=N/SR/TB,aformat=sample_rates=48000:channel_layouts=stereo,"
                 f"volume={CONFIG['musica']['volumen']}{mute}[m0]")
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
    cmd += ["-filter_complex", ";".join(f), "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(salida)]
    run(cmd, cwd=trabajo)
    hacer_portada(combo)

    receta = {**combo, "duracion": round(total, 2), "cortes": [round(o, 2) for o in offs[1:]],
              "musica": pista.name if pista else None,
              "efectos": [{"sfx": f"{e['cat']}/{e['archivo'].name}", "t": e["t"], "motivo": e["motivo"]} for e in eventos],
              "imagenes": [{"asset": o["asset"], "t": round(o["t"], 2), "dur": round(o["dur"], 2), "modo": o["modo"]} for o in overlays],
              "faltan_assets": sorted(set(faltan)), "marcas_sin_encontrar": perdidas}
    (S.recetas / f"{combo['codigo']}.json").write_text(json.dumps(receta, ensure_ascii=False, indent=1), encoding="utf-8")
    return receta


def armar(pieza=None, avance=print):
    hechos = []
    todos = [c for c in combinaciones() if not pieza or pieza in (c["hook"], c["cuerpo"], c["cta"])]
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


def exportar(avance=print):
    """Copia a listos/DIA_X: video, portada y copy.txt de cada publicación del calendario."""
    por_codigo = {c["codigo"]: c for c in combinaciones()}
    hechos = []
    for dia, codigos in calendario().items():
        destino = S.listos / dia
        destino.mkdir(parents=True, exist_ok=True)
        for cod in codigos:
            c = por_codigo.get(cod)
            if not c:
                continue
            v, p = S.salida / f"{c['archivo']}.mp4", S.portadas / f"{c['archivo']}.jpg"
            if not v.exists():
                continue
            avance(f"Exportando {dia} · {c['archivo']}")
            shutil.copy2(v, destino / v.name)
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
    sug = sugerencias()
    sin = [{"rel": c["rel"], "nombre": c["ruta"].name, "carpeta": c["tipo_carpeta"], **sug.get(c["rel"], {})}
           for c in clips_sin_asignar()]
    return {"piezas_faltan": falta_piezas, "assets": assets, "sin_asignar": sin}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["procesar", "armar", "exportar", "portadas", "todo"])
    ap.add_argument("--pieza")
    ap.add_argument("--semana")
    ap.add_argument("--rehacer-subtitulos", action="store_true")
    a = ap.parse_args()
    if a.semana:
        usar_semana(a.semana)
    if a.accion in ("procesar", "todo"):
        procesar(a.rehacer_subtitulos)
    if a.accion in ("armar", "todo"):
        armar(a.pieza)
    if a.accion == "portadas":
        rehacer_portadas()
    if a.accion in ("exportar", "todo"):
        exportar()
