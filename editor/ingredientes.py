"""Ingredientes de Google Flow: las fotos reales de la marca (productos y logos de _MARCA) que se adjuntan a un pedido
para que Flow las dibuje tal cual. Probado el 2026-10-30: con la foto de la bolsa adjunta, Flow (Nano Banana 2.1) pone
la bolsa de Suplevet con su logo, su etiqueta y sus perros; sin ella, inventa un envase.

Cómo llegan a Flow: viven una sola vez en un proyecto de Flow propio, «INGREDIENTES <MARCA>». El «+» del cuadro
«¿Qué quieres crear?» deja elegir recursos de OTRO proyecto, así que cada tanda los toma de ahí con clics. Así sirve
también para Antigravity, cuyo navegador no puede elegir un archivo en la ventana de Windows.

Qué imagen lleva qué: el asset lo dice en flow.ingredientes (["bolsa_150g"]); si no lo dice y su prompt pide el
producto («the Suplevet bag…»), va la foto del producto."""
import re
from pathlib import Path

import marca

SUBCARPETAS = ("productos", "logos")
_PRODUCTO = re.compile(r"\b(bag|pouch|package|packaging|product|bolsa|envase|pote|sachet)\b", re.I)
_NEGADO = re.compile(r"\b(no|not|without|never|sin|nunca)\b[^.;:]{0,40}$", re.I)  # «no text, no logos or packaging»
_LOGO = re.compile(r"\blogo\b", re.I)


def proyecto():
    """El proyecto de Flow donde viven los ingredientes de la marca."""
    nombre = " ".join(str(marca.datos().get("nombre") or "").split()).upper()
    return f"INGREDIENTES {nombre}" if nombre else "INGREDIENTES MARCA"


def disponibles():
    """{nombre de archivo: Path} de los productos y logos de _MARCA."""
    out = {}
    for sub in SUBCARPETAS:
        d = marca.raiz() / sub
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in marca.TIPOS[sub]:
                    out[f.name] = f
    return out


def fichas():
    """{archivo: {nombre, que_es, beneficio}} de las fotos de producto (la ficha que se llena en Marca)."""
    return {p["archivo"]: p for p in marca.datos().get("productos_info") or []}


def en_imagenes():
    """Cuántas imágenes de Flow muestran un producto: uno_por_cuerpo, todas o nunca (se elige en Marca)."""
    return marca.datos().get("producto_en_imagenes") or "uno_por_cuerpo"


def _pide(regex, prompt):
    return any(not _NEGADO.search(prompt[:m.start()]) for m in regex.finditer(prompt))


def de(flow, hay=None):
    """Los ingredientes (nombres de archivo) de un asset de Flow: los que pide su guion o, si no dice nada, la foto del
    producto cuando el prompt lo nombra (y el logo cuando lo pide sin negarlo)."""
    hay = disponibles() if hay is None else hay
    flow = flow or {}
    pedidos = flow.get("ingredientes")
    if isinstance(pedidos, str):
        pedidos = [pedidos]
    if isinstance(pedidos, list):  # el guion manda, aunque sea una lista vacía (= sin ingredientes)
        out = []
        for x in pedidos:
            x = str(x).strip().lower()
            f = next((n for n in hay if n.lower() == x or Path(n).stem.lower() == x), None)
            if f and f not in out:
                out.append(f)
        return out
    prompt = str(flow.get("prompt") or "")
    productos = [n for n, f in hay.items() if f.parent.name == "productos"]
    out = []
    if en_imagenes() == "nunca":
        productos = []
    # solo el producto de la marca: «a bag of kibble» no lleva la bolsa; «the Suplevet bag» o «the bag from the reference» sí
    nombre = str(marca.datos().get("nombre") or "").strip().lower()
    de_marca = bool(nombre and nombre in prompt.lower()) or "reference" in prompt.lower()
    if productos and de_marca and _pide(_PRODUCTO, prompt):
        # el que nombra el prompt por el nombre de su ficha, por su tamaño («the 250g bag») o por su archivo; si no, el primero
        bajo, fs = prompt.lower(), fichas()
        nombrado = ([n for n in productos if len((fs.get(n) or {}).get("nombre", "")) >= 3
                     and (fs[n]["nombre"].lower() in bajo or Path(n).stem.lower() in bajo) and fs[n]["nombre"].lower() != nombre]
                    or [n for n in productos if any(t and t in bajo for t in re.split(r"[_\-\s]+", Path(n).stem.lower())
                                                    if any(c.isdigit() for c in t))])
        out.append((nombrado or productos)[0])
    logos = [n for n, f in hay.items() if f.parent.name == "logos"]
    if logos and _pide(_LOGO, prompt):
        out.append(logos[0])
    return out


def frase(ingredientes, clip=False):
    """Lo que se le dice a Flow en la primera línea de una tanda que lleva ingredientes."""
    if not ingredientes:
        return ""
    cosa = "video" if clip else "image"
    fs = fichas()
    que = "; ".join(f"{i} = {fs[i]['nombre']}" + (f" ({fs[i]['que_es']})" if fs[i].get("que_es") else "")
                    for i in ingredientes if i in fs)
    return (f" Every {cosa} uses the attached reference image{'s' if len(ingredientes) > 1 else ''} "
            f"({', '.join(ingredientes)}): it is the real brand product or logo{f' [{que}]' if que else ''}. Keep it exactly as "
            "in the reference: same shape, colors, logo and label text; do not invent or change any text on it.")
