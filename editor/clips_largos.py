"""Clips de un video largo y video suelto en automático, sin Claude: todo con reglas fijas y en esta PC.

  1. Se sube un video (largo o corto, horizontal o vertical) → se transcribe aquí (faster-whisper, como los clips).
  2. Se proponen clips de 18 a 60 s: empiezan y terminan en una frase, con un inicio que engancha (pregunta, número,
     palabras fuertes), mucha voz y pocas pausas. La persona elige, mueve el inicio y el final, o usa el video entero.
  3. Cada clip se arma en vertical: reencuadre que sigue la cara (el detector de caras de la sala), centro o fondo
     difuminado; sin los silencios del medio; color; audio mejorado o tal cual; subtítulos con la plantilla; zoom alternado.

  clips_largos/<id>/  fuente.<ext> · info.json · palabras.json · propuesta.json · hechos.json
  Los clips armados quedan en la sesión abierta: V.FINALES/CLIPS <nombre del video>/ (y se suben a Drive).
"""
import json
import re
import secrets
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

import motor

BASE = motor.DATOS / "clips_largos"
EXT_VIDEO = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")
SILENCIOS = {"no": None, "normal": 0.5, "rapido": 0.3}   # pausas más largas que esto se cortan
COLORES = {"ninguno": "", "natural": "eq=contrast=1.05:saturation=1.12:gamma=1.02", "vivo": "eq=contrast=1.1:saturation=1.25:gamma=1.0"}
OPCIONES = {"silencios": "normal", "audio": "suave", "igualar": True, "color": "natural", "subtitulos": True, "zoom": True, "encuadre": "cara"}
GANCHO = ("como", "por que", "porque nadie", "error", "errores", "secreto", "nadie", "nunca", "truco", "clave", "importante",
          "problema", "verdad", "mito", "deja de", "evita", "necesitas", "tienes que", "debes", "mejor", "peor", "gratis",
          "rapido", "facil", "sabias", "cuidado", "ojo", "atencion", "increible", "resultado", "cambio", "dinero", "salud")
PREGUNTA = ("que ", "como ", "por que", "cual", "cuales", "cuanto", "cuantos", "cuando", "donde", "sabias", "alguna vez", "te has")
CONECTORES = ("y ", "pero ", "entonces ", "o sea", "porque ", "que ", "osea", "pues ", "bueno ", "este ", "eh ")
NUMEROS = ("uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "cien", "mil", "primer", "segundo", "tercer")


def _n(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9?¿ ]", " ", s)).strip()


def _carpeta(i):
    if not re.fullmatch(r"[0-9a-f]{10}", str(i or "")):
        raise ValueError("Video no válido")
    d = BASE / i
    if not d.is_dir():
        raise ValueError("Ese video ya no está")
    return d


def _leer(f, sino=None):
    try:
        return json.loads(Path(f).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return sino


def _escribir(f, d):
    tmp = Path(f).with_suffix(".part")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def fuente(i):
    d = _carpeta(i)
    return next((f for f in d.iterdir() if f.stem == "fuente" and f.suffix.lower() in EXT_VIDEO), None)


# ---------- Subir y preparar ----------

def recibir(nombre, leer, n):
    """Guarda el video que se sube (de a pedazos: puede pesar varios GB). Devuelve su id."""
    ext = Path(str(nombre)).suffix.lower()
    if ext not in EXT_VIDEO:
        raise ValueError("Sube un video (.mp4, .mov, .mkv, .webm…)")
    i = secrets.token_hex(5)
    d = BASE / i
    d.mkdir(parents=True)
    with open(d / f"fuente{ext}", "wb") as fh:
        while n > 0:
            trozo = leer(min(n, 4 << 20))
            if not trozo:
                break
            fh.write(trozo)
            n -= len(trozo)
    titulo = re.sub(r"[_\-]+", " ", Path(str(nombre)).stem).strip()[:80] or "Video"
    _escribir(d / "info.json", {"nombre": titulo, "archivo": Path(str(nombre)).name, "creado": time.time(), "estado": "subido"})
    return i


def _medidas(f):
    r = subprocess.run([motor.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:stream_side_data=rotation:format=duration",
                        "-of", "json", str(f)], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    d = json.loads(r.stdout or "{}")
    s = (d.get("streams") or [{}])[0]
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    rot = next((abs(int(x.get("rotation", 0))) for x in s.get("side_data_list") or [] if "rotation" in x), 0)
    if rot in (90, 270):
        w, h = h, w
    return w, h, float((d.get("format") or {}).get("duration") or 0)


def preparar(i, avance=print):
    """Medidas, transcripción (con el tiempo de cada palabra) y la propuesta de clips."""
    d, f = _carpeta(i), fuente(i)
    if not f:
        raise RuntimeError("No encuentro el video subido")
    info = _leer(d / "info.json", {})
    w, h, dur = _medidas(f)
    if not dur or not w:
        raise RuntimeError("No pude leer ese video (¿está dañado?)")
    info.update(ancho=w, alto=h, duracion=round(dur, 2), estado="transcribiendo")
    _escribir(d / "info.json", info)
    if not (d / "palabras.json").exists():
        avance(f"Escuchando «{info['nombre']}» ({int(dur // 60)} min {int(dur % 60)} s): se pasa a texto en esta PC")
        _escribir(d / "palabras.json", _transcribir(f, dur, avance))
    palabras = _leer(d / "palabras.json", [])
    avance("Buscando los mejores clips")
    clips = proponer(palabras, dur)
    _escribir(d / "propuesta.json", clips)
    info["estado"] = "listo"
    _escribir(d / "info.json", info)
    avance(f"Listo: {len(clips)} clips propuestos de «{info['nombre']}»" if clips else f"«{info['nombre']}» no tiene voz para proponer clips: puedes usar el video entero")
    return clips


def _transcribir(f, dur, avance):
    import numpy as np
    from faster_whisper import WhisperModel
    if motor._modelo is None:
        motor._modelo = WhisperModel(motor.CONFIG["transcripcion"]["modelo"], device="cpu", compute_type="int8")
    pcm = subprocess.run([motor.FFMPEG, "-hide_banner", "-i", str(f), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    segs, _ = motor._modelo.transcribe(np.frombuffer(pcm, dtype=np.float32), language="es", word_timestamps=True,
                                       vad_filter=True, initial_prompt=motor.CONFIG["transcripcion"]["vocabulario"])
    out, ultimo = [], 0
    for s in segs:
        motor.revisar_cancelado()
        out += [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for w in s.words]
        if s.end - ultimo > 30:
            ultimo = s.end
            avance(f"Pasando a texto: {int(s.end // 60)}:{int(s.end % 60):02d} de {int(dur // 60)}:{int(dur % 60):02d}")
    return motor.corregir_palabras(out)


# ---------- Proponer clips (reglas fijas, sin IA) ----------

def frases(palabras):
    out, actual = [], []
    for k, p in enumerate(palabras):
        actual.append(p)
        sig = palabras[k + 1] if k + 1 < len(palabras) else None
        if p["w"][-1:] in ".?!" or not sig or sig["s"] - p["e"] > 0.8 or len(actual) >= 40:
            out.append({"s": actual[0]["s"], "e": actual[-1]["e"], "texto": " ".join(x["w"] for x in actual), "n": len(actual), "palabras": actual})
            actual = []
    return out


def _puntaje(fr, i, j):
    primera = fr[i]["texto"]
    p = _n(primera)
    dur = fr[j]["e"] - fr[i]["s"]
    pal = [w for x in fr[i:j + 1] for w in x["palabras"]]
    pausas = sum(max(0.0, b["s"] - a["e"] - 0.7) for a, b in zip(pal, pal[1:]))
    s, razones = 0.0, []
    if "?" in primera or p.startswith(PREGUNTA):
        s += 2; razones.append("Empieza con una pregunta")
    if re.search(r"\d", primera) or any(re.search(rf"\b{x}\b", p) for x in NUMEROS):
        s += 1.5; razones.append("Tiene un número")
    gancho = sum(1 for g in GANCHO if re.search(rf"\b{g}\b", p))
    if gancho:
        s += min(3, gancho) * 0.8; razones.append("Palabras que enganchan")
    if fr[i]["n"] <= 14:
        s += 0.5
    if p.startswith(CONECTORES):
        s -= 1.5  # empieza a mitad de una idea
    densidad = len(pal) / max(1.0, dur)
    s += max(-1.5, min(2.0, (densidad - 2.0) * 1.5))
    if densidad > 2.6:
        razones.append("Mucha voz, sin relleno")
    s -= pausas * 0.3
    if 25 <= dur <= 45:
        s += 1; razones.append("Buen largo")
    return s, razones


def proponer(palabras, total, minimo=18, maximo=60):
    fr = frases(palabras)
    candidatos = []
    for i in range(len(fr)):
        for j in range(i, len(fr)):
            dur = fr[j]["e"] - fr[i]["s"]
            if dur > maximo:
                break
            if dur >= minimo:
                s, razones = _puntaje(fr, i, j)
                candidatos.append((s, i, j, razones))
    candidatos.sort(key=lambda c: -c[0])
    cuantos = max(3, min(15, int(total / 90) + 2))
    elegidos = []
    for s, i, j, razones in candidatos:
        a, b = fr[i]["s"], fr[j]["e"]
        if all(b <= x["ini"] or a >= x["fin"] for x in elegidos):
            texto = " ".join(x["texto"] for x in fr[i:j + 1])
            elegidos.append({"ini": round(max(0.0, a - 0.15), 2), "fin": round(min(total, b + 0.3), 2), "puntaje": round(s, 1),
                             "titulo": fr[i]["texto"][:90], "texto": texto[:700], "razones": razones})
        if len(elegidos) >= cuantos:
            break
    elegidos.sort(key=lambda x: x["ini"])
    for k, c in enumerate(elegidos, 1):
        c["id"] = f"c{k}"
        c["dur"] = round(c["fin"] - c["ini"], 1)
    return elegidos


# ---------- Lista y estado ----------

def lista():
    out = []
    if BASE.is_dir():
        for d in BASE.iterdir():
            info = _leer(d / "info.json")
            if info and re.fullmatch(r"[0-9a-f]{10}", d.name):
                out.append({"id": d.name, **info, "propuestos": len(_leer(d / "propuesta.json", [])), "hechos": len(_leer(d / "hechos.json", []))})
    return sorted(out, key=lambda x: -x.get("creado", 0))


def uno(i):
    d = _carpeta(i)
    return {"id": i, **_leer(d / "info.json", {}), "propuesta": _leer(d / "propuesta.json", []), "hechos": _leer(d / "hechos.json", []),
            "texto": " ".join(p["w"] for p in _leer(d / "palabras.json", []))[:20000], "opciones": {**OPCIONES, **motor.CONFIG.get("clips_opciones", {})}}


def borrar(i):
    shutil.rmtree(_carpeta(i), ignore_errors=True)


# ---------- Armar ----------

def _tramos_con_voz(palabras, ini, fin, pausa):
    """Los pedazos que quedan: sin las pausas más largas que «pausa» (con un respiro antes y después de la voz)."""
    dentro = [p for p in palabras if p["s"] >= ini - 0.05 and p["e"] <= fin + 0.05]
    if pausa is None or not dentro:
        return [(ini, fin)]
    tramos = []
    a, b = max(ini, dentro[0]["s"] - 0.08), dentro[0]["e"]
    for p in dentro[1:]:
        if p["s"] - b > pausa:
            tramos.append((a, min(fin, b + 0.15)))
            a = max(ini, p["s"] - 0.08)
        b = p["e"]
    tramos.append((a, min(fin, b + 0.15)))
    return [(round(x, 3), round(y, 3)) for x, y in tramos if y - x > 0.12]


def _seguir_cara(f, ini, fin, W, H, por_seg=3):
    """[(t, x)] centro horizontal de la cara (0 a 1) desde ini, calmado como una cámara: solo se mueve si la persona
    se movió de verdad. [] si no hay detector o no se ve una cara."""
    sesion = motor._detector_cara()
    if not sesion:
        return []
    import numpy as np
    k = min(320 / W, 240 / H)
    w, h = max(2, int(W * k) // 2 * 2), max(2, int(H * k) // 2 * 2)
    px, py = (320 - w) // 2, (240 - h) // 2
    proc = subprocess.run([motor.FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{ini:.2f}", "-t", f"{fin - ini:.2f}", "-i", str(f), "-an",
                           "-vf", f"fps={por_seg},scale={w}:{h},pad=320:240:{px}:{py}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                          capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    cuadros = np.frombuffer(proc.stdout, np.uint8)
    n = cuadros.size // 230400
    crudos = []
    for q in range(n):
        img = cuadros[q * 230400:(q + 1) * 230400].reshape(240, 320, 3).astype(np.float32)
        scores, boxes = sesion.run(None, {"input": ((img - 127.0) / 128.0).transpose(2, 0, 1)[None]})
        s, b = scores[0, :, 1], boxes[0]
        ok = s > 0.7
        if not ok.any():
            crudos.append(None)
            continue
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        j = int(np.argmax(np.where(ok, s * area, -1)))
        crudos.append(((b[j, 0] + b[j, 2]) / 2 * 320 - px) / w)
    vistos = [c for c in crudos if c is not None]
    if len(vistos) < max(2, n // 5):
        return []
    ult, llenos = vistos[0], []
    for c in crudos:
        ult = c if c is not None else ult
        llenos.append(ult)
    med = [float(np.median(llenos[max(0, q - 3):q + 4])) for q in range(len(llenos))]
    suave = [float(np.mean(med[max(0, q - 4):q + 5])) for q in range(len(med))]
    puntos = [(0.0, suave[0])]
    for q in range(1, len(suave)):
        if abs(suave[q] - puntos[-1][1]) > 0.05 or q == len(suave) - 1:
            puntos.append((q / por_seg, suave[q]))
    return [(round(t, 2), round(x, 3)) for t, x in puntos]


def _expr_tramos(puntos, valor):
    """Expresión de ffmpeg que va de un punto al otro en línea recta (t en segundos)."""
    if not puntos:
        return "0"
    e = f"{valor(puntos[-1][1]):.1f}"
    for (t0, a), (t1, b) in reversed(list(zip(puntos, puntos[1:]))):
        va, vb = valor(a), valor(b)
        e = f"if(lt(t,{t1:.2f}),{va:.1f}+({vb - va:.1f})*(t-{t0:.2f})/{max(0.01, t1 - t0):.2f},{e})"
    return e


def armar_clip(i, clip, op, destino, avance=print):
    """Arma un clip vertical listo para publicar. clip: {ini, fin, titulo, encuadre?, x?}. Devuelve la ruta."""
    d, f = _carpeta(i), fuente(i)
    info = _leer(d / "info.json", {})
    palabras = _leer(d / "palabras.json", [])
    ini, fin = float(clip["ini"]), float(clip["fin"])
    if fin - ini < 1:
        raise ValueError("El clip es muy corto")
    W, H = info["ancho"], info["alto"]
    OW, OH = motor.lienzo()
    trabajo = motor.carpeta_trabajo()
    tramos = _tramos_con_voz(palabras, ini, fin, SILENCIOS.get(op.get("silencios"), 0.5))
    rel = [(a - ini, b - ini) for a, b in tramos]  # con -ss, el video empieza en 0
    # las palabras pasan al tiempo del clip ya cortado
    nuevas, off = [], 0.0
    for (a, b), (ra, rb) in zip(tramos, rel):
        for p in palabras:
            if a - 0.01 <= p["s"] and p["e"] <= b + 0.01:
                nuevas.append({**p, "s": round(off + p["s"] - a, 2), "e": round(off + min(p["e"], b) - a, 2)})
        off += b - a
    total = off

    f_ = []
    encuadre = clip.get("encuadre") or op.get("encuadre", "cara")
    vertical = W / H <= OW / OH + 0.02
    if vertical:
        f_.append(f"[0:v]fps=30,scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},setsar=1[vb]")
    elif encuadre == "difuminado":
        f_.append(f"[0:v]fps=30,split[fa][fb];[fa]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},gblur=sigma=40,eq=brightness=-0.08[fondo];"
                  f"[fb]scale={OW}:-2[frente];[fondo][frente]overlay=0:(H-h)/2,setsar=1[vb]")
    else:
        cw = int(H * OW / OH) // 2 * 2
        mover = lambda cx: max(0.0, min(W - cw, cx * W - cw / 2))
        if clip.get("x") is not None:  # la persona lo acomodó a mano
            xexpr = f"{mover(float(clip['x'])):.1f}"
        elif encuadre == "cara":
            avance(f"Buscando a la persona en «{clip.get('titulo', '')[:40]}»")
            puntos = _seguir_cara(f, ini, fin, W, H)
            xexpr = _expr_tramos(puntos, mover) if puntos else f"{mover(0.5):.1f}"
        else:
            xexpr = f"{mover(0.5):.1f}"
        f_.append(f"[0:v]fps=30,crop={cw}:{H}:x='{xexpr}':y=0,scale={OW}:{OH},setsar=1[vb]")
    sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in rel)
    f_.append(f"[vb]select='{sel}',setpts=N/FRAME_RATE/TB[vs]")
    previo = "vs"
    lineas = motor.agrupar_lineas(nuevas) if nuevas else []
    if op.get("zoom") and lineas:  # frase sí, frase no: un salto de cerca (como el zoom alternado de CapCut)
        z = "+".join(f"between(it,{l['s']:.2f},{l['e']:.2f})" for k, l in enumerate(lineas) if k % 2 == 1) or "0"
        f_.append(f"[{previo}]zoompan=z='1+0.12*({z})':x='iw/2-iw/zoom/2':y='ih*0.4-ih/zoom*0.4':d=1:s={OW}x{OH}:fps=30,setsar=1[vz]")
        previo = "vz"
    color = op.get("color", "natural")
    if color == "estilo":
        if c := motor.filtros_color(None, trabajo, previo, "vcol"):
            f_ += c
            previo = "vcol"
    elif COLORES.get(color):
        f_.append(f"[{previo}]{COLORES[color]}[vcol]")
        previo = "vcol"
    if op.get("subtitulos", True) and lineas:
        motor.escribir_ass([(0, lineas, [])], trabajo / "subs.ass")
        f_.append(f"[{previo}]subtitles=subs.ass:fontsdir=fuentes[v]")
    else:
        f_.append(f"[{previo}]null[v]")
    audio = [f"aselect='{sel}'", "asetpts=N/SR/TB"]
    if motor.LIMPIEZAS_VOZ.get(op.get("audio")):
        audio.append(motor.LIMPIEZAS_VOZ[op["audio"]])
    if op.get("igualar", True) and op.get("audio") != "original":
        audio.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    audio.append("aresample=48000")
    f_.append("[0:a]" + ",".join(audio) + "[a]")
    grafo = ";".join(f_)
    (trabajo / "grafo_clip.txt").write_text(grafo, encoding="utf-8")
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.stem + ".part.mp4")
    base = [motor.FFMPEG, "-hide_banner", "-y", "-ss", f"{ini:.3f}", "-t", f"{fin - ini:.3f}", "-i", str(f),
            "-/filter_complex", "grafo_clip.txt", "-map", "[v]", "-map", "[a]"]
    fin_cmd = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total + 0.05:.2f}", str(tmp)]
    comp = motor.compresor_armado()
    try:
        motor.run(base + comp + fin_cmd, cwd=trabajo)
    except RuntimeError:
        if comp == motor.COMPRESOR_CPU:
            raise
        motor.run(base + motor.COMPRESOR_CPU + ["-pix_fmt", "yuv420p"] + fin_cmd, cwd=trabajo)
    tmp.replace(destino)
    return destino, total


def _nombre_archivo(k, titulo):
    t = re.sub(r'[<>:"/\\|?*¿¡]+', "", str(titulo or "")).strip()
    t = " ".join(t.split()[:8])[:50].rstrip(" .,")
    return f"{k:02d} {t or 'Clip'}.mp4"


def carpeta_salida(i):
    info = _leer(_carpeta(i) / "info.json", {})
    nombre = re.sub(r'[<>:"/\\|?*]+', " ", info.get("nombre", "Video")).strip()[:60]
    return motor.S.listos / f"CLIPS {nombre}"


def armar(i, clips, op, avance=print):
    """Arma los clips elegidos uno por uno. Devuelve la lista de hechos (y la deja en hechos.json)."""
    d = _carpeta(i)
    op = {**OPCIONES, **{k: v for k, v in (op or {}).items() if k in OPCIONES}}
    motor.guardar_config({"clips_opciones": op})  # la próxima vez salen las mismas opciones
    salida = carpeta_salida(i)
    hechos = [h for h in _leer(d / "hechos.json", []) if (salida / h["archivo"]).exists()]
    for k, c in enumerate(clips, 1):
        motor.revisar_cancelado()
        avance(f"Armando el clip {k} de {len(clips)}: {str(c.get('titulo', ''))[:50]}")
        n = len(hechos) + 1
        destino = salida / _nombre_archivo(n, c.get("titulo"))
        ruta, dur = armar_clip(i, c, op, destino, avance)
        hechos.append({"archivo": ruta.name, "titulo": c.get("titulo", ""), "ini": c["ini"], "fin": c["fin"], "dur": round(dur, 1),
                       "creado": time.time()})
        _escribir(d / "hechos.json", hechos)
    return hechos, salida
