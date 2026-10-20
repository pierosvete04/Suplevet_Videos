"""Carruseles: armar láminas de 1080×1350 con fotos de Flow, quitar fondos y exportar a V.FINALES.

Dónde vive cada cosa (en la sesión abierta):
  <sesión>/Carruseles/<id>/carrusel.json   láminas y capas (lo que se edita en la pestaña Carruseles)
  <sesión>/Carruseles/<id>/img/            fotos subidas y recortes sin fondo
  <sesión>/V.FINALES/<id>/L1.png…          lo exportado + copy.txt (el Publicador lo toma como carrusel)

Quitar fondo: modelo ISNet (el mismo de rembg, «isnet-general-use») con onnxruntime, que la sala ya trae.
La página manda la foto achicada a 1024×1024 en bytes RGB y recibe la máscara: así no hace falta Pillow.
El modelo (170 MB) se baja una sola vez a datos/modelos.
"""
import json
import re
import threading
import time
import unicodedata
from pathlib import Path

import motor

MODELO_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx"
MODELO = motor.DATOS / "modelos" / "isnet-general-use.onnx"
MODELO_REMBG = Path.home() / ".rembg" / "models" / "isnet-general-use" / "isnet-general-use.onnx"  # si ya se usó rembg en esta PC
LADO = 1024
MARCA = motor.RAIZ / "carrusel_marca"  # bolsas reales sin fondo y logo: van con la sala
_sesion = {"s": None}
_candado = threading.Lock()
ESTADO_MODELO = {"bajando": False, "avance": 0, "error": None}


def base():
    return motor.S.principal / "Carruseles"


def finales():
    return motor.S.listos or (motor.S.principal / "V.FINALES")


def _id_seguro(cid):
    if not cid or not re.fullmatch(r"[\w\-]+", cid):
        raise ValueError("Carrusel no válido")
    return cid


def _slug(texto):
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40] or "carrusel"


def listar():
    out = []
    if base().is_dir():
        for d in sorted(base().iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            j = d / "carrusel.json"
            if not j.is_file():
                continue
            try:
                c = json.loads(j.read_text(encoding="utf-8"))
            except ValueError:
                continue
            exp = finales() / d.name
            out.append({"id": d.name, "titulo": c.get("titulo", d.name), "laminas": len(c.get("laminas", [])),
                        "estilo": c.get("estilo", ""), "nivel": c.get("nivel", ""),
                        "exportado": exp.is_dir() and any(exp.glob("L*.png"))})
    return out


def leer(cid):
    return json.loads((base() / _id_seguro(cid) / "carrusel.json").read_text(encoding="utf-8"))


def guardar(cid, datos):
    d = base() / _id_seguro(cid)
    d.mkdir(parents=True, exist_ok=True)
    datos["actualizado"] = time.strftime("%Y-%m-%d %H:%M")
    tmp = d / "carrusel.json.tmp"
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / "carrusel.json")
    return datos


def nuevo(titulo, laminas=4):
    existentes = [int(m.group(1)) for m in (re.match(r"C(\d+)", x["id"]) for x in listar()) if m]
    cid = f"C{(max(existentes) + 1) if existentes else 1:02d}-{_slug(titulo)}"
    vacia = lambda: {"fondo": {"tipo": "color", "color": "#99D3DA"}, "capas": []}
    return cid, guardar(cid, {"titulo": titulo, "estilo": "", "nivel": "", "copy": "", "laminas": [vacia() for _ in range(laminas)]})


def guardar_imagen(cid, nombre, datos):
    d = base() / _id_seguro(cid) / "img"
    d.mkdir(parents=True, exist_ok=True)
    tallo, ext = Path(nombre).stem, (Path(nombre).suffix.lower() or ".png")
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError("Solo fotos PNG, JPG o WEBP")
    tallo = re.sub(r"[^\w\-]+", "_", tallo)[:50] or "foto"
    destino, n = d / f"{tallo}{ext}", 2
    while destino.exists():
        destino, n = d / f"{tallo}_{n}{ext}", n + 1
    destino.write_bytes(datos)
    return destino.name


def ruta_imagen(cid):
    return base() / _id_seguro(cid) / "img"


def imagenes(cid):
    d = ruta_imagen(cid)
    return sorted((f.name for f in d.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")),
                  key=lambda n: (d / n).stat().st_mtime) if d.is_dir() else []


def marca():
    return sorted(f.name for f in MARCA.iterdir() if f.suffix.lower() == ".png") if MARCA.is_dir() else []


def exportar(cid, pngs, copy=""):
    """pngs: bytes de cada lámina, en orden. Reemplaza lo exportado antes."""
    destino = finales() / _id_seguro(cid)
    destino.mkdir(parents=True, exist_ok=True)
    for viejo in destino.glob("L*.png"):
        viejo.unlink()
    for i, b in enumerate(pngs, 1):
        if not b.startswith(b"\x89PNG"):
            raise ValueError(f"La lámina {i} no llegó bien")
        (destino / f"L{i:02d}.png").write_bytes(b)
    if copy.strip():
        (destino / "copy.txt").write_text(f"INSTAGRAM\n{copy.strip()}\n\nTIKTOK\n{copy.strip()}\n", encoding="utf-8")
    return str(destino)


# ---------- Quitar fondo ----------
def modelo_listo():
    return MODELO.is_file() or MODELO_REMBG.is_file()


def _bajar_modelo():
    import requests
    MODELO.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODELO.with_suffix(".parte")
    ESTADO_MODELO.update(bajando=True, avance=0, error=None)
    try:
        with requests.get(MODELO_URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            total, hecho = int(r.headers.get("Content-Length") or 0), 0
            with open(tmp, "wb") as f:
                for trozo in r.iter_content(1 << 20):
                    f.write(trozo)
                    hecho += len(trozo)
                    ESTADO_MODELO["avance"] = round(hecho * 100 / total) if total else 0
        tmp.replace(MODELO)
    except Exception as e:
        ESTADO_MODELO["error"] = f"No pude bajar el recortador: {e}"
        tmp.unlink(missing_ok=True)
    finally:
        ESTADO_MODELO["bajando"] = False


def preparar_modelo():
    """Si falta el modelo, empieza a bajarlo (una vez) y devuelve False; la página vuelve a preguntar."""
    if modelo_listo():
        return True
    if not ESTADO_MODELO["bajando"]:
        threading.Thread(target=_bajar_modelo, daemon=True).start()
    return False


def _sesion_onnx():
    if _sesion["s"] is None:
        import onnxruntime as ort
        ruta = MODELO if MODELO.is_file() else MODELO_REMBG
        _sesion["s"] = ort.InferenceSession(str(ruta), providers=["CPUExecutionProvider"])
    return _sesion["s"]


def mascara(rgb):
    """rgb: bytes de una foto de 1024×1024 (R, G, B por píxel). Devuelve 1024×1024 bytes: 255 = se queda, 0 = fondo."""
    import numpy as np
    if len(rgb) != LADO * LADO * 3:
        raise ValueError("La foto no llegó en 1024×1024")
    im = np.frombuffer(rgb, dtype=np.uint8).reshape(LADO, LADO, 3).astype(np.float32)
    im = im / max(float(im.max()), 1e-6)            # igual que rembg: se divide por el píxel más claro
    im = (im - 0.5) / 1.0
    x = im.transpose(2, 0, 1)[None].astype(np.float32)
    with _candado:
        s = _sesion_onnx()
        pred = s.run(None, {s.get_inputs()[0].name: x})[0][0, 0]
    mi, ma = float(pred.min()), float(pred.max())
    pred = (pred - mi) / max(ma - mi, 1e-6)
    return (pred * 255).clip(0, 255).astype(np.uint8).tobytes()
