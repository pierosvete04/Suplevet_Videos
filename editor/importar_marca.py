"""Importar la marca sin Claude: la plantilla (carpetas + ficha en Word) y lo que se sube se acomoda con reglas fijas.

  - Plantilla de marca.zip: MI MARCA/ con logos/, fuentes/, productos/, LÉEME.txt y «ficha de marca.docx».
  - La ficha es una tabla de dos columnas hecha por la sala: se lee por los títulos de la izquierda (sin IA).
  - Lo que se sube (carpeta, .zip o archivos sueltos) queda aparte (_importar_marca/<id>) hasta que la persona revisa
    la propuesta y la guarda: la carpeta dice qué es cada cosa; si no hay carpeta, el tipo de archivo, el nombre y si
    la imagen tiene fondo transparente. Los colores salen de la ficha o, si no hay, de los píxeles del logo (con ffmpeg,
    o con el lector de imágenes de Windows si la app no trae ffmpeg).

Es el mismo archivo en la Sala de edición y en la Sala de diseño (las dos escriben _MARCA): si se cambia en una, se
copia igual a la otra. Lo que cada app guarda distinto lo resuelve su marca.py (logos_info, guardar, guardar_archivo…).
"""
import base64
import html
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import unicodedata
import zipfile
from pathlib import Path

import marca

try:  # Sala de edición
    import motor
    DATOS, FFMPEG = motor.DATOS, motor.FFMPEG
except ImportError:  # Sala de diseño: no trae ffmpeg (las imágenes se leen con Windows)
    import nucleo
    DATOS, FFMPEG = nucleo.DATOS, shutil.which("ffmpeg")

BASE = DATOS / "_importar_marca"
IMAGENES = (".png", ".webp", ".jpg", ".jpeg")
FUENTES = (".ttf", ".otf")
TIPOS = ("logos", "productos", "fuentes", "manual", "ficha", "ignorar")
POR_CARPETA = {"logos": ("logos", "logo", "logotipos", "logotipo"), "fuentes": ("fuentes", "fuente", "tipografias", "tipografia", "fonts"),
               "productos": ("productos", "producto", "fotos de producto", "fotos")}
POR_NOMBRE = {"logos": ("logo", "isotipo", "imagotipo", "icono", "marca"), "productos": ("producto", "pack", "bolsa", "envase", "frasco", "caja", "empaque")}

# (clave, título de la izquierda, ayuda en gris). El título no se cambia: así se reconoce cada respuesta.
FICHA = [
    ("nombre", "Nombre de la marca", "Ej.: Suplevet"),
    ("color_principal", "Color principal", "Su código, ej.: #253C61. Si no lo sabes, déjalo vacío: la sala lo saca de tu logo."),
    ("color_secundario", "Color secundario", "Ej.: #99D3DA"),
    ("color_acento", "Color de acento", "El que resalta (botones, palabras clave). Ej.: #EA8C43"),
    ("color_fondo", "Color de fondo", "Ej.: #F2F2F2"),
    ("color_texto", "Color del texto", "Ej.: #1B2C48"),
    ("otros_colores", "Otros colores", "Uno por línea, con nombre y código. Ej.: Naranja tinta #A8530F"),
    ("fuente_titulo", "Tipografía de los títulos", "El nombre de la letra o de su archivo. Ej.: Bebas Neue"),
    ("fuente_cuerpo", "Tipografía del texto", "Ej.: DM Sans"),
    ("fuente_subtitulos", "Tipografía de los subtítulos de los videos", "Ej.: Montserrat Black"),
    ("notas", "Cómo habla la marca", "Tono, a quién le habla, qué la hace distinta."),
    ("evitar", "Palabras o frases que nunca se usan", "Una por línea."),
    ("obligatorio", "Lo que siempre tiene que ir", "Una por línea. Ej.: Consulta con tu veterinario"),
    ("cierres", "Frases de cierre", "Una por línea."),
    ("cta_titulo", "Llamado a la acción: título", "Ej.: Pide el tuyo"),
    ("cta_texto", "Llamado a la acción: texto", "Ej.: Envíos a todo el Perú"),
    ("cta_boton", "Llamado a la acción: botón", "Ej.: Escríbenos"),
]

LEEME = """CÓMO LLENAR ESTA CARPETA
========================

logos/
  Tus logos en PNG con fondo transparente. Ponles de nombre cuál es y sobre qué fondo va:
    principal_fondo_oscuro.png    el principal, para poner sobre fondo oscuro (suele ser el blanco)
    principal_fondo_claro.png     el principal, para poner sobre fondo claro
    horizontal_fondo_oscuro.png
    icono_fondo_claro.png
  Si no sabes cómo nombrarlos, súbelos igual: los acomodas en la sala antes de guardar.

fuentes/
  Tus tipografías en .ttf u .otf, tal como las descargaste.

productos/
  Fotos de tu producto, mejor sin fondo (PNG).

{ficha}

CUANDO TERMINES
  Abre la Sala de edición (Configuración → Marca) o la Sala de diseño (pestaña Marca),
  toca «Importar mi marca» y sube esta carpeta tal cual (o comprímela en .zip). La sala
  te muestra lo que encontró y nada se guarda hasta que tú lo apruebes. Las dos salas
  usan la misma marca: se carga una sola vez.
"""


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------- Plantilla: carpetas, LÉEME y la ficha en Word (un .docx hecho a mano, sin librerías) ----------

def _x(s):
    return html.escape(str(s), quote=False)


def _parrafo(texto, negrita=False, tam=None, color=None, espacio=60):
    rpr = "".join(x for x in ("<w:b/>" if negrita else "", f'<w:color w:val="{color}"/>' if color else "",
                                f'<w:sz w:val="{tam}"/>' if tam else "") if x)
    return (f'<w:p><w:pPr><w:spacing w:after="{espacio}"/></w:pPr>'
            f'<w:r>{f"<w:rPr>{rpr}</w:rPr>" if rpr else ""}<w:t xml:space="preserve">{_x(texto)}</w:t></w:r></w:p>')


def ficha_docx():
    borde = '<w:{0} w:val="single" w:sz="6" w:space="0" w:color="C9D2DE"/>'
    bordes = "".join(borde.format(k) for k in ("top", "left", "bottom", "right", "insideH", "insideV"))
    filas = []
    for _, titulo, ayuda in FICHA:
        filas.append(
            '<w:tr><w:trPr><w:cantSplit/></w:trPr>'
            '<w:tc><w:tcPr><w:tcW w:w="3600" w:type="dxa"/><w:shd w:val="clear" w:color="auto" w:fill="EEF2F7"/></w:tcPr>'
            f'{_parrafo(titulo, negrita=True, espacio=20)}{_parrafo(ayuda, tam=16, color="7A8699", espacio=20)}</w:tc>'
            '<w:tc><w:tcPr><w:tcW w:w="6000" w:type="dxa"/></w:tcPr><w:p/></w:tc></w:tr>')
    cuerpo = (_parrafo("Ficha de marca", negrita=True, tam=40, espacio=80)
              + _parrafo("Escribe solo en la columna de la derecha. No cambies los títulos de la izquierda: así la sala lee tus respuestas sola.", tam=20, color="4A5568")
              + _parrafo("Si algo no lo sabes, déjalo vacío: lo completas después en la sala (pantalla Marca).", tam=20, color="4A5568", espacio=200)
              + f'<w:tbl><w:tblPr><w:tblW w:w="9600" w:type="dxa"/><w:tblBorders>{bordes}</w:tblBorders>'
                '<w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:left w:w="120" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tblCellMar></w:tblPr>'
                '<w:tblGrid><w:gridCol w:w="3600"/><w:gridCol w:w="6000"/></w:tblGrid>' + "".join(filas) + "</w:tbl>"
              + '<w:p/><w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>')
    documento = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 f'<w:body>{cuerpo}</w:body></w:document>')
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", documento)
    return b.getvalue()


FICHA_EXCEL = Path(__file__).parent / "marcas" / "ficha de marca.xlsx"  # la trae la Sala de edición (marcas/hacer_ficha_excel.py)
LEEME_EXCEL = """ficha de marca.xlsx
  Un Excel con casillas: tu marca, tus colores (con su código, ej. #253C61), todas tus palabras
  (el nombre de la marca, las del producto, las que se resaltan, cómo pides la compra, lo que
  nunca se dice), cómo deben verse las imágenes y sonar la música, y a quiénes les hablas.
  Llena las casillas amarillas. Lo que no sepas, déjalo vacío: después lo completa la IA."""
LEEME_WORD = """ficha de marca.docx
  Llena solo la columna de la derecha. No cambies los títulos de la izquierda.
  Los colores van con su código, por ejemplo #253C61. Si no lo sabes, déjalo vacío:
  la sala saca los colores de tu logo."""


def plantilla_zip():
    b = io.BytesIO()
    excel = FICHA_EXCEL.exists()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for d in ("logos", "fuentes", "productos"):
            z.writestr(f"MI MARCA/{d}/", "")
        leeme = LEEME.replace("{ficha}", LEEME_EXCEL if excel else LEEME_WORD)
        z.writestr("MI MARCA/LÉEME.txt", leeme.replace("\n", "\r\n").encode("utf-8-sig"))
        if excel:
            z.writestr("MI MARCA/ficha de marca.xlsx", FICHA_EXCEL.read_bytes())
        else:
            z.writestr("MI MARCA/ficha de marca.docx", ficha_docx())
    return b.getvalue()


def guardar_plantilla():
    """La deja en Descargas (o en el escritorio) y abre la carpeta con el archivo marcado."""
    casa = Path.home()
    destino = next((d for d in (casa / "Downloads", casa / "Descargas", casa / "Desktop", casa / "Escritorio") if d.is_dir()), casa)
    f = destino / "Plantilla de marca.zip"
    f.write_bytes(plantilla_zip())
    try:
        subprocess.Popen(["explorer", "/select,", str(f)])
    except OSError:
        pass
    return f


# ---------- Leer la ficha ----------

def leer_ficha(contenido):
    """{clave: texto} de la tabla de la ficha. Se reconoce cada fila por el título de la izquierda."""
    try:
        xml = zipfile.ZipFile(io.BytesIO(contenido)).read("word/document.xml").decode("utf-8")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("La ficha no es un Word (.docx) que la sala pueda abrir")
    titulos = sorted(((k, _norm(t)) for k, t, _ in FICHA), key=lambda x: -len(x[1]))  # el más largo primero

    def texto(celda):
        parrafos = re.findall(r"<w:p[ >].*?</w:p>", celda, re.S)
        out = []
        for p in parrafos:
            p = re.sub(r"<w:(br|cr)/>", "\n", p).replace("<w:tab/>", " ")
            out.append(html.unescape("".join(re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", p, re.S))))
        return out

    datos = {}
    for fila in re.findall(r"<w:tr[ >].*?</w:tr>", xml, re.S):
        celdas = re.findall(r"<w:tc>.*?</w:tc>|<w:tc .*?</w:tc>", fila, re.S)
        if len(celdas) < 2:
            continue
        izq = texto(celdas[0])
        clave = next((k for k, t in titulos if izq and _norm(izq[0]).startswith(t)), None)
        valor = "\n".join(x for x in texto(celdas[1]) if x.strip()).strip()
        if clave and valor:
            datos[clave] = valor
    return datos


HEX = re.compile(r"#?\b([0-9a-fA-F]{6})\b")


# ---------- Leer la ficha en Excel (sin librerías: un .xlsx es un zip con XML) ----------

_XL = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_XR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _columna(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref or "A").group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def hojas_excel(contenido):
    """{nombre de la hoja: [[texto de cada celda de la fila]]}."""
    import xml.etree.ElementTree as ET
    try:
        z = zipfile.ZipFile(io.BytesIO(contenido))
        libro = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise ValueError("La ficha no es un Excel (.xlsx) que la sala pueda abrir")
    compartidos = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{_XL}si"):
            compartidos.append("".join(t.text or "" for t in si.iter(f"{_XL}t")))
    hojas = {}
    for h in libro.find(f"{_XL}sheets"):
        destino = rels.get(h.get(f"{_XR}id"), "")
        ruta = destino.lstrip("/") if destino.startswith("/") else "xl/" + destino
        try:
            datos = ET.fromstring(z.read(ruta))
        except (KeyError, ET.ParseError):
            continue
        filas = []
        for fila in datos.iter(f"{_XL}row"):
            celdas = {}
            for c in fila.findall(f"{_XL}c"):
                t, v = c.get("t"), c.find(f"{_XL}v")
                if t == "s" and v is not None:
                    txt = compartidos[int(v.text)] if int(v.text) < len(compartidos) else ""
                elif t == "inlineStr":
                    txt = "".join(x.text or "" for x in c.iter(f"{_XL}t"))
                else:
                    txt = v.text if v is not None else ""
                celdas[_columna(c.get("r"))] = str(txt or "").strip()
            if any(celdas.values()):
                filas.append([celdas.get(k, "") for k in range(max(celdas) + 1)])
        hojas[h.get("name")] = filas
    return hojas


# las preguntas de la hoja «Marca» (el principio basta: «Tono (cómo hablas)» → tono)
PREGUNTAS_EXCEL = [("nombre", "nombre de la marca"), ("que_vende", "que vendes"), ("distinto", "que te hace distinto"),
                   ("publico", "a quien le vendes"), ("tono", "tono"), ("compra", "como te compran"),
                   ("cta_titulo", "llamado a la accion titulo"), ("cta_texto", "llamado a la accion texto"),
                   ("cta_boton", "llamado a la accion boton"), ("fuente_titulo", "tipografia de los titulos"),
                   ("fuente_cuerpo", "tipografia del texto"), ("fuente_subtitulos", "tipografia de los subtitulos"),
                   ("no_prometer", "lo que nunca se promete"), ("mas", "mas para la ia")]
TIPOS_PALABRA = {"nombre de la marca": "marca", "palabra del producto": "producto", "resaltar en subtitulos": "clave",
                 "frase para pedir la compra": "cta", "nunca se dice": "evitar", "siempre tiene que ir": "obligatorio",
                 "frase de cierre": "cierre"}


def _hoja(hojas, *nombres):
    """La hoja con ese nombre; si alguien la renombró («Mi marca»), la que lo contiene."""
    for nombre, filas in hojas.items():
        if _norm(nombre) in nombres:
            return filas
    for nombre, filas in hojas.items():
        if any(n in _norm(nombre) for n in nombres):
            return filas
    return []


def _filas_bajo(filas, titulo):
    """Las filas que vienen después de la fila de títulos (la que empieza con `titulo`), sin los ejemplos."""
    for k, f in enumerate(filas):
        if f and _norm(f[0]).startswith(titulo):
            return [x for x in filas[k + 1:] if x and any(x) and not _norm(x[0]).startswith("ej ")]
    return []


def leer_excel(contenido):
    """La ficha en Excel → (marca con la forma de marca.json, perfil para los videos con la forma de perfil.json)."""
    hojas = hojas_excel(contenido)
    resp = {}
    for f in _hoja(hojas, "marca"):
        if len(f) >= 2 and f[1]:
            q = _norm(f[0])
            clave = next((k for k, t in PREGUNTAS_EXCEL if q.startswith(t)), None)
            if clave:
                resp[clave] = f[1]
    m, pf = {}, {}
    if resp.get("nombre"):
        m["nombre"] = pf["nombre"] = resp["nombre"].splitlines()[0].strip()[:80]
    colores, usados, avisos = [], set(), []
    for f in _filas_bajo(_hoja(hojas, "colores"), "nombre del color"):
        h = _hex(" ".join(f[:2]))
        if not h:
            avisos.append(f"El color «{f[0]}» no tiene un código que sirva ({(f[1] if len(f) > 1 else '') or 'vacío'}): "
                          "va con # y 6 letras o números, ej. #253C61. No se usó.")
            continue
        uso = _norm(f[2]) if len(f) > 2 else ""
        uso = uso if uso in ("principal", "secundario", "acento", "fondo", "texto") and uso not in usados else "otro"
        usados.add(uso)
        colores.append({"nombre": (f[0] if not HEX.fullmatch(f[0].strip()) else "")[:40] or _nombre_color(h), "hex": h, "uso": uso})
    if colores:
        m["colores"] = colores
    fu = {k: resp[c].splitlines()[0].strip() for k, c in (("titulo", "fuente_titulo"), ("cuerpo", "fuente_cuerpo"),
                                                          ("subtitulos", "fuente_subtitulos")) if resp.get(c)}
    if fu:
        m["fuentes"] = fu
    cta = {k: resp[c] for k, c in (("titulo", "cta_titulo"), ("texto", "cta_texto"), ("boton", "cta_boton")) if resp.get(c)}
    if cta:
        m["cta"] = cta
    por_tipo, correcciones = {}, {}
    for f in _filas_bajo(_hoja(hojas, "palabras"), "palabra o frase"):
        palabra = f[0].strip()
        tipo = TIPOS_PALABRA.get(_norm(f[1]) if len(f) > 1 else "", "producto")
        por_tipo.setdefault(tipo, []).append(palabra)
        for mal in re.split(r"[,;\n]", f[2] if len(f) > 2 else ""):
            if mal.strip() and _norm(mal) != _norm(palabra):
                correcciones[mal.strip().lower()] = palabra
    for k_marca, tipo in (("evitar", "evitar"), ("obligatorio", "obligatorio"), ("cierres", "cierre")):
        if por_tipo.get(tipo):
            m[k_marca] = por_tipo[tipo]
    if resp.get("no_prometer"):
        m["evitar"] = m.get("evitar", []) + [x.strip() for x in re.split(r"[,\n]", resp["no_prometer"]) if x.strip()]
    notas = [resp[k] for k in ("tono", "distinto", "mas") if resp.get(k)]
    if notas:
        m["notas_para_claude"] = "\n".join(notas)
    for k_pf, k_resp in (("que_vende", "que_vende"), ("publico", "publico"), ("tono", "tono")):
        if resp.get(k_resp):
            pf[k_pf] = resp[k_resp]
    if resp.get("distinto"):
        pf["que_vende"] = (pf.get("que_vende", "") + ". " + resp["distinto"]).strip(". ")
    extra = [f"Cómo compran: {resp['compra']}" if resp.get("compra") else "", f"Nunca se promete: {resp['no_prometer']}" if resp.get("no_prometer") else "",
             resp.get("mas", "")]
    if any(extra):
        pf["notas"] = "\n".join(x for x in extra if x)
    if por_tipo.get("marca"):
        pf["palabras_marca"] = por_tipo["marca"]
    vocab = por_tipo.get("marca", []) + por_tipo.get("producto", [])
    if vocab:
        pf["vocabulario"] = vocab
    if por_tipo.get("clave"):
        pf["palabras_clave"] = por_tipo["clave"]
    if por_tipo.get("cta"):
        pf["cta"] = por_tipo["cta"]
    if correcciones:
        pf["correcciones"] = correcciones
    img, mus, mostrar = [], [], ""
    for f in _filas_bajo(_hoja(hojas, "imagenes y musica"), "regla"):
        para = _norm(f[1]) if len(f) > 1 else ""
        if para.startswith("musica"):
            mus.append(f[0])
        elif para.startswith("lo que mas"):
            mostrar = f[0]
        else:
            img.append(f[0])
    if img or mostrar:
        pf["imagenes"] = {**({"reglas": img} if img else {}), **({"que_mostrar": mostrar} if mostrar else {})}
    if mus:
        pf["musica"] = {"reglas": mus}
    publicos = [f[0] for f in _filas_bajo(_hoja(hojas, "publicos"), "publico") if f[0]]
    if publicos:
        pf["publicos"] = publicos
        pf.setdefault("publico", publicos[0])
    if not resp and not colores and not por_tipo:
        avisos.append("El Excel no tiene nada lleno: ¿es la ficha de marca de la sala?")
    return m, pf, avisos


def _hex(texto):
    m = HEX.search(texto or "")
    if m:
        return "#" + m.group(1).upper()
    corto = re.search(r"#([0-9a-fA-F]{3})\b", texto or "")  # #F36 = #FF3366
    return "#" + "".join(c * 2 for c in corto.group(1)).upper() if corto else None


def marca_de_ficha(f):
    """Lo de la ficha con la forma de marca.json."""
    out = {}
    if f.get("nombre"):
        out["nombre"] = f["nombre"].splitlines()[0].strip()
    colores = []
    for clave, uso, nombre in (("color_principal", "principal", "Principal"), ("color_secundario", "secundario", "Secundario"),
                               ("color_acento", "acento", "Acento"), ("color_fondo", "fondo", "Fondo"), ("color_texto", "texto", "Texto")):
        h = _hex(f.get(clave))
        if h:
            resto = HEX.sub("", f[clave]).strip(" -:·,")
            colores.append({"nombre": resto[:40] or nombre, "hex": h, "uso": uso})
    for linea in (f.get("otros_colores") or "").splitlines():
        h = _hex(linea)
        if h:
            colores.append({"nombre": HEX.sub("", linea).strip(" -:·,")[:40] or _nombre_color(h), "hex": h, "uso": "otro"})
    if colores:
        out["colores"] = colores
    fu = {k: f[c].splitlines()[0].strip() for k, c in (("titulo", "fuente_titulo"), ("cuerpo", "fuente_cuerpo"), ("subtitulos", "fuente_subtitulos")) if f.get(c)}
    if fu:
        out["fuentes"] = fu
    for k in ("evitar", "obligatorio", "cierres"):
        if f.get(k):
            out[k] = [x.strip(" -•·\t") for x in f[k].splitlines() if x.strip(" -•·\t")]
    cta = {k: f[c] for k, c in (("titulo", "cta_titulo"), ("texto", "cta_texto"), ("boton", "cta_boton")) if f.get(c)}
    if cta:
        out["cta"] = cta
    if f.get("notas"):
        out["notas_para_claude"] = f["notas"]
    return out


# ---------- Mirar una imagen: transparencia, luz y colores (con ffmpeg, sin librerías) ----------

def _pixeles(ruta, lado=48):
    if not FFMPEG:
        return _pixeles_windows([ruta], lado).get(str(ruta), [])
    try:
        r = subprocess.run([FFMPEG, "-v", "error", "-i", str(ruta), "-frames:v", "1", "-vf",
                            f"scale={lado}:{lado}:force_original_aspect_ratio=decrease", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                           capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return []
    d = r.stdout
    return [tuple(d[i:i + 4]) for i in range(0, len(d) - 3, 4)]


# Sin ffmpeg: el lector de imágenes que trae Windows (System.Drawing), todas de una vez (abrir PowerShell tarda ~1 s).
# Lee PNG, JPG, GIF y BMP; un WEBP queda sin colores (y sin transparencia: su carpeta o su nombre dicen qué es).
_PS_PIXELES = r"""
Add-Type -AssemblyName System.Drawing
foreach ($r in Get-Content -LiteralPath $env:LISTA_IMAGENES -Encoding UTF8) {
  try {
    $img = [System.Drawing.Image]::FromFile($r)
    $e = [Math]::Min(1.0, [Math]::Min(LADO / $img.Width, LADO / $img.Height))
    $w = [Math]::Max(1, [int]($img.Width * $e)); $h = [Math]::Max(1, [int]($img.Height * $e))
    $bmp = New-Object System.Drawing.Bitmap $w, $h, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp); $g.DrawImage($img, 0, 0, $w, $h); $g.Dispose(); $img.Dispose()
    $d = $bmp.LockBits((New-Object System.Drawing.Rectangle 0, 0, $w, $h), 'ReadOnly', $bmp.PixelFormat)
    $b = New-Object byte[] ($d.Stride * $h)
    [Runtime.InteropServices.Marshal]::Copy($d.Scan0, $b, 0, $b.Length); $bmp.UnlockBits($d); $bmp.Dispose()
    "$w $($d.Stride) " + [Convert]::ToBase64String($b)
  } catch { "x" }
}
"""


def _pixeles_windows(rutas, lado=48):
    """{ruta: [(r, g, b, a), …]} con a lo más lado×lado píxeles por imagen."""
    rutas = [str(r) for r in rutas]
    if not rutas or os.name != "nt":
        return {}
    lista = BASE / f".lista_{secrets.token_hex(4)}.txt"
    lista.parent.mkdir(parents=True, exist_ok=True)
    lista.write_text("\n".join(rutas), encoding="utf-8")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_PIXELES.replace("LADO", str(lado))],
                           capture_output=True, text=True, timeout=60 + 5 * len(rutas), env={**os.environ, "LISTA_IMAGENES": str(lista)},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lineas = [l for l in r.stdout.splitlines() if l.strip()]
    except (OSError, subprocess.TimeoutExpired):
        lineas = []
    finally:
        lista.unlink(missing_ok=True)
    out = {}
    for ruta, linea in zip(rutas, lineas):
        partes = linea.split(" ")
        if len(partes) != 3:
            continue
        ancho, paso, datos = int(partes[0]), int(partes[1]), base64.b64decode(partes[2])
        px = []
        for fila in range(0, len(datos), paso):
            for i in range(fila, fila + ancho * 4, 4):  # Windows guarda azul, verde, rojo, alfa
                b, g, r_, a = datos[i:i + 4]
                px.append((r_, g, b, a))
        out[ruta] = px
    return out


_VISTAS = {}  # sin ffmpeg, analizar() lee todas las imágenes de una vez y aquí quedan


def mirar_imagen(ruta):
    px = _VISTAS.pop(str(ruta), None)
    if px is None:
        px = _pixeles(ruta)
    if not px:
        return {"transparente": False, "luz": 0.5, "colores": []}
    opacos = [p for p in px if p[3] > 200]
    transparente = sum(1 for p in px if p[3] < 30) / len(px) > 0.08
    luz = sum(.2126 * r + .7152 * g + .0722 * b for r, g, b, _ in opacos) / 255 / max(1, len(opacos))
    return {"transparente": transparente, "luz": round(luz, 3), "colores": _dominantes(opacos)}


def _dominantes(opacos, cuantos=4):
    cubos = {}
    for r, g, b, _ in opacos:
        k = (r // 32, g // 32, b // 32)
        c = cubos.setdefault(k, [0, 0, 0, 0])
        c[0] += r; c[1] += g; c[2] += b; c[3] += 1
    medios = sorted(((c[3], (c[0] // c[3], c[1] // c[3], c[2] // c[3])) for c in cubos.values()), reverse=True)
    out = []
    for n, rgb in medios:
        if n < len(opacos) * 0.03:
            break
        casi_blanco, casi_negro = min(rgb) > 232, max(rgb) < 22
        if (casi_blanco or casi_negro) and len(medios) > 1:
            continue  # el blanco y el negro puros casi nunca son «el color» de la marca
        if all(sum((a - b) ** 2 for a, b in zip(rgb, o)) > 55 ** 2 for o in out):
            out.append(rgb)
        if len(out) >= cuantos:
            break
    return ["#%02X%02X%02X" % c for c in out]


def _nombre_color(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < .08:
        return "Blanco" if mx > .9 else "Negro" if mx < .15 else "Gris"
    if mx == r:
        t = ((g - b) / (mx - mn)) % 6
    elif mx == g:
        t = (b - r) / (mx - mn) + 2
    else:
        t = (r - g) / (mx - mn) + 4
    tono = t * 60
    nombre = next(n for lim, n in ((15, "Rojo"), (40, "Naranja"), (65, "Amarillo"), (160, "Verde"), (195, "Celeste"), (255, "Azul"),
                                   (290, "Morado"), (340, "Rosado"), (361, "Rojo")) if tono < lim)
    return nombre + (" oscuro" if mx < .45 else "")


# ---------- Subir, proponer y guardar ----------

def nuevo():
    """Un lugar aparte para lo que se sube (se borran los de antes)."""
    shutil.rmtree(BASE, ignore_errors=True)
    i = secrets.token_hex(6)
    (BASE / i / "archivos").mkdir(parents=True)
    return i


def _carpeta(i):
    if not re.fullmatch(r"[0-9a-f]{12}", str(i or "")):
        raise ValueError("Importación no válida")
    d = BASE / i / "archivos"
    if not d.is_dir():
        raise ValueError("Esa importación ya no está: vuelve a subir la carpeta")
    return d


def _ruta_segura(rel):
    partes = [p.strip() for p in re.split(r"[\\/]+", str(rel or "")) if p.strip() not in ("", ".", "..")]
    partes = [re.sub(r'[<>:"|?*\x00-\x1f]', "_", p) for p in partes]
    if not partes or any(p.startswith(".") or p == "__MACOSX" for p in partes) or partes[-1].lower() in ("thumbs.db", "desktop.ini"):
        return None
    return "/".join(partes)


def recibir(i, rel, leer, n):
    """Guarda un archivo subido (si es .zip, lo abre). leer(k) entrega los bytes de a poco."""
    d = _carpeta(i)
    rel = _ruta_segura(rel)
    if not rel:
        return 0
    if rel.lower().endswith(".zip"):
        b = io.BytesIO()
        while n > 0:
            trozo = leer(min(n, 1 << 20)); n -= len(trozo); b.write(trozo)
            if not trozo:
                break
        cuantos = 0
        try:
            with zipfile.ZipFile(b) as z:
                for info in z.infolist():
                    if info.is_dir() or info.file_size > 60 * 1024 * 1024:
                        continue
                    r = _ruta_segura(info.filename)
                    if r:
                        destino = d / r
                        destino.parent.mkdir(parents=True, exist_ok=True)
                        destino.write_bytes(z.read(info))
                        cuantos += 1
        except zipfile.BadZipFile:
            raise ValueError("Ese .zip está dañado o no es un .zip")
        return cuantos
    destino = d / rel
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "wb") as fh:
        while n > 0:
            trozo = leer(min(n, 1 << 20))
            if not trozo:
                break
            fh.write(trozo); n -= len(trozo)
    return 1


def ruta_staging(i, rel):
    d = _carpeta(i).resolve()
    f = (d / str(rel)).resolve()
    if d not in f.parents or not f.is_file():
        raise ValueError("Archivo no válido")
    return f


def _por_carpeta(rel):
    for parte in reversed(rel.split("/")[:-1]):
        n = _norm(parte)
        for tipo, nombres in POR_CARPETA.items():
            if n in nombres:
                return tipo
    return None


def _datos_logo(nombre, vista):
    n = _norm(Path(nombre).stem)
    m = re.search(r"(?:fondo|para) (oscuro|claro)", n)
    fondo = m.group(1) if m else None
    if not fondo:
        fondo = "oscuro" if vista["luz"] > .72 else "claro" if vista["luz"] < .38 else "cualquiera"
    uso = "principal" if "principal" in n else marca._adivinar(nombre, marca.USO_POR_NOMBRE, "principal")
    limpio = " ".join(re.sub(r"\b(para )?(fondo|para) (oscuro|claro)\b", "", n).split()) or n
    return {"uso": uso, "fondo": fondo, "nombre_logo": limpio.capitalize()[:40]}


def analizar(i):
    """La propuesta: qué es cada archivo y la marca que sale de la ficha (o del logo). No guarda nada todavía."""
    d = _carpeta(i)
    archivos, ficha, avisos = [], {}, []
    excel_marca, excel_perfil = {}, {}
    if not FFMPEG:
        _VISTAS.update(_pixeles_windows([p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in IMAGENES]))
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        rel = f.relative_to(d).as_posix()
        ext = f.suffix.lower()
        item = {"ruta": rel, "nombre": f.name, "kb": max(1, f.stat().st_size // 1024)}
        n = _norm(f.stem)
        if n in ("leeme", "readme", "lee me"):
            item["tipo"] = "ignorar"  # las instrucciones de la plantilla
        elif ext in FUENTES:
            item["tipo"] = "fuentes"
        elif ext in (".xlsx", ".xlsm"):
            try:
                m_x, pf_x, av_x = leer_excel(f.read_bytes())
                avisos += [f"«{f.name}»: {x}" for x in av_x]
            except ValueError as e:
                m_x, pf_x = {}, {}
                avisos.append(f"«{f.name}»: {e}")
            if m_x or pf_x:
                excel_marca.update(m_x)
                excel_perfil.update(pf_x)
                item["tipo"] = "ficha"
            else:
                item["tipo"] = "manual"
        elif ext == ".docx":
            try:
                datos = leer_ficha(f.read_bytes())
            except ValueError as e:
                datos = {}
                avisos.append(f"«{f.name}»: {e}")
            if datos:
                ficha.update(datos)
                item["tipo"] = "ficha"
            else:
                item["tipo"] = "manual"
        elif ext in (".pdf", ".ai", ".eps", ".svg", ".psd", ".txt", ".md", ".doc"):
            item["tipo"] = "manual"
        elif ext in IMAGENES:
            vista = mirar_imagen(f)
            item["colores"] = vista["colores"]
            tipo = _por_carpeta(rel)
            if not tipo:
                tipo = next((t for t, palabras in POR_NOMBRE.items() if any(p in n.split() or p in n for p in palabras)), None)
            if not tipo:
                tipo = "logos" if vista["transparente"] else "productos"
            item["tipo"] = tipo if tipo in ("logos", "productos") else "productos"
            item.update(_datos_logo(f.name, vista))
        else:
            item["tipo"] = "ignorar"
        archivos.append(item)
    propuesta = {**marca_de_ficha(ficha), **excel_marca}  # el Excel manda sobre el Word
    colores_de = "ficha" if propuesta.get("colores") else ""
    vistos = []
    for a in archivos:
        if a["tipo"] == "logos":
            for h in a.get("colores", []):
                if h not in vistos:
                    vistos.append(h)
    if propuesta.get("colores") and vistos and not any(c["uso"] == "principal" for c in propuesta["colores"]):
        # la ficha no dice el principal: va el del logo
        h = next((x for x in vistos if x not in [c["hex"] for c in propuesta["colores"]]), None)
        if h:
            propuesta["colores"].insert(0, {"nombre": _nombre_color(h), "hex": h, "uso": "principal"})
            colores_de = "ficha y logo"
    if not propuesta.get("colores"):
        if vistos:
            usos = ["principal", "acento", "secundario"]
            propuesta["colores"] = [{"nombre": _nombre_color(h), "hex": h, "uso": usos[k] if k < len(usos) else "otro"} for k, h in enumerate(vistos[:5])]
            colores_de = "logo"
    # la tipografía que dice la ficha se busca entre las fuentes que vienen en la carpeta
    fuentes_sub = [a["nombre"] for a in archivos if a["tipo"] == "fuentes"]
    for k, v in list((propuesta.get("fuentes") or {}).items()):
        propuesta["fuentes"][k] = _buscar_fuente(v, fuentes_sub, d)
    if not archivos:
        avisos.append("No llegó ningún archivo.")
    return {"id": i, "archivos": archivos, "marca": propuesta, "ficha": bool(ficha or excel_marca or excel_perfil),
            "perfil": excel_perfil, "colores_de": colores_de, "avisos": avisos}


def _buscar_fuente(valor, subidas, d):
    q = _norm(valor).replace(" ", "")
    if not q:
        return valor
    for nombre in subidas:
        if q in _norm(Path(nombre).stem).replace(" ", ""):
            return nombre
    try:
        import fuentes  # el lector de tipografías de la Sala de edición
    except ImportError:
        return valor
    for f in sorted(p for p in d.rglob("*") if p.suffix.lower() in FUENTES):
        try:
            if q in _norm(fuentes.Fuente(f).nombre).replace(" ", ""):
                return f.name
        except Exception:  # noqa: BLE001
            pass
    for f in fuentes.disponibles():
        if q in _norm(f.get("nombre")).replace(" ", "") or q in _norm(Path(f["archivo"]).stem).replace(" ", ""):
            return f["archivo"]
    return valor


def aplicar(i, propuesta):
    """Guarda en la marca lo que la persona aprobó (los archivos y lo de la ficha)."""
    d = _carpeta(i)
    actual = marca.datos()
    logos = marca.logos_info()
    nombres_fuente = {}
    hechos = {"logos": 0, "productos": 0, "fuentes": 0, "manual": 0}
    for a in propuesta.get("archivos") or []:
        tipo = a.get("tipo")
        if tipo not in TIPOS or tipo in ("ignorar",):
            continue
        f = ruta_staging(i, a.get("ruta"))
        if tipo in ("manual", "ficha"):
            destino = marca.raiz() / "manual"
            destino.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, destino / f.name)
            hechos["manual"] += 1
            continue
        nombre = marca.guardar_archivo(tipo, f.name, f.read_bytes(), subir=False)
        hechos[tipo] += 1
        if tipo == "fuentes":
            nombres_fuente[f.name] = nombre
        if tipo == "logos":
            logos[nombre] = {"archivo": nombre, "nombre": str(a.get("nombre_logo") or Path(nombre).stem)[:40],
                             "uso": a.get("uso") if a.get("uso") in marca.USOS_LOGO else "principal",
                             "fondo": a.get("fondo") if a.get("fondo") in marca.FONDOS_LOGO else "cualquiera"}
    m = propuesta.get("marca") or {}
    nueva = {k: actual.get(k) for k in ("nombre", "colores", "texto_oscuro", "texto_claro", "fuentes", "evitar", "obligatorio", "cierres", "cta", "notas_para_claude")}
    if m.get("nombre"):
        nueva["nombre"] = m["nombre"]
    if m.get("colores"):
        nueva["colores"] = m["colores"]
    fu = dict(nueva.get("fuentes") or {})
    for k, v in (m.get("fuentes") or {}).items():
        if v:
            fu[k] = nombres_fuente.get(v, v)
    nueva["fuentes"] = fu
    for k in ("evitar", "obligatorio", "cierres"):
        if m.get(k):
            nueva[k] = m[k]
    cta = dict(nueva.get("cta") or {})
    cta.update({k: v for k, v in (m.get("cta") or {}).items() if v})
    nueva["cta"] = cta
    if m.get("notas_para_claude"):
        nueva["notas_para_claude"] = m["notas_para_claude"]
    nueva["logos"] = list(logos.values())
    if hechos["fuentes"]:
        marca.copiar_fuentes()
    if propuesta.get("perfil"):  # lo de la ficha en Excel para los videos: antes de guardar la marca, así sube junto
        hechos["perfil"] = _guardar_perfil(propuesta["perfil"])
    datos = marca.guardar(nueva)  # sube todo a Drive de una vez
    shutil.rmtree(BASE / i, ignore_errors=True)
    return {"hechos": hechos, "marca": datos}


def _guardar_perfil(pf):
    """Suma lo de la ficha al perfil para los videos de _MARCA/perfil.json (no borra lo que la ficha no trae). En la Sala
    de edición pasa por perfil.py; en la de diseño se escribe tal cual y la Sala de edición lo lee de Drive."""
    try:
        import perfil
        actual = perfil.propio()

        def escribir(d):
            perfil.guardar(d, subir=False)
    except ImportError:
        f = marca.raiz() / "perfil.json"
        try:
            actual = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            actual = {}

        def escribir(d):
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(".perfil.json.part")
            tmp.write_text(json.dumps({**d, "cambio": time.time()}, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, f)
    junto = {**actual, **{k: v for k, v in pf.items() if v not in (None, "", [], {})}}
    for k in ("imagenes", "musica"):  # campo por campo: el Excel no borra lo demás
        if isinstance(pf.get(k), dict):
            junto[k] = {**(actual.get(k) or {}), **pf[k]}
    if pf.get("correcciones"):
        junto["correcciones"] = {**(actual.get("correcciones") or {}), **pf["correcciones"]}
    escribir(junto)
    return True


if __name__ == "__main__":
    print(json.dumps(leer_ficha(ficha_docx()), ensure_ascii=False))
