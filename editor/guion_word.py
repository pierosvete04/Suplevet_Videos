"""El guion en Word (.docx) para imprimir o pasarlo, hecho a mano (sin librerías: la sala no las trae).

Toma el mismo Markdown que se ve en la pestaña Guion (motor.documento_guion) y lo pasa a un Word con títulos,
negritas, listas y casillas para marcar lo grabado. La versión del host va en letra grande para leerla de pie.
"""
import html
import io
import re
import subprocess
import zipfile
from pathlib import Path


def _runs(texto, tam, color=None):
    """**negrita** y `código` dentro de una línea."""
    out = []
    for parte in re.split(r"(\*\*.+?\*\*|`.+?`)", texto):
        if not parte:
            continue
        negrita = parte.startswith("**") and parte.endswith("**")
        codigo = parte.startswith("`") and parte.endswith("`")
        limpio = parte[2:-2] if negrita else parte[1:-1] if codigo else parte
        rpr = (("<w:b/>" if negrita else "") + ('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>' if codigo else "")
               + (f'<w:color w:val="{color}"/>' if color else "") + f'<w:sz w:val="{tam}"/>')
        out.append(f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{html.escape(limpio, quote=False)}</w:t></w:r>')
    return "".join(out)


def _p(texto, tam, antes=0, despues=120, sangria=0, color=None, borde=False):
    ppr = f'<w:spacing w:before="{antes}" w:after="{despues}"/>' + (f'<w:ind w:left="{sangria}"/>' if sangria else "")
    if borde:
        ppr += '<w:pBdr><w:top w:val="single" w:sz="6" w:space="6" w:color="BBBBBB"/></w:pBdr>'
    return f"<w:p><w:pPr>{ppr}</w:pPr>{_runs(texto, tam, color)}</w:p>"


def docx_de_markdown(md, grande=False):
    base = 30 if grande else 22  # medios puntos: 15 pt para leer de pie, 11 pt para el detalle
    cuerpo, num = [], 0
    for l in md.splitlines():
        m = re.match(r"^(#{1,3}) (.*)", l)
        if m:
            n = len(m.group(1))
            cuerpo.append(_p(f"**{m.group(2)}**", {1: base + 16, 2: base + 6, 3: base + 2}[n], antes=240 if n > 1 else 0,
                             despues=120, borde=n == 2))
            num = 0
        elif (m := re.match(r"^\s*- \[ \] (.*)", l)):
            cuerpo.append(_p(f"☐ {m.group(1)}", base - 4, color="666666"))
        elif (m := re.match(r"^(\s*)- (.*)", l)):
            cuerpo.append(_p(f"• {m.group(2)}", base, sangria=360 + (360 if m.group(1) else 0), despues=60))
        elif (m := re.match(r"^\d+\. (.*)", l)):
            num += 1
            cuerpo.append(_p(f"**{num}.** {m.group(1)}", base, sangria=360, despues=80))
        elif l.strip():
            cuerpo.append(_p(l, base))
            num = 0
    documento = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                 + "".join(cuerpo)
                 + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>'
                 '</w:body></w:document>')
    estilos = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault><w:rPr>'
               '<w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI" w:cs="Segoe UI"/><w:lang w:val="es-PE"/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>')
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("word/document.xml", documento)
        z.writestr("word/styles.xml", estilos)
    return b.getvalue()


def guardar(md, sesion, version, carpeta_guion=None):
    """Lo deja en Descargas (y en la carpeta Guion de la sesión) y abre la carpeta con el archivo marcado."""
    nombre = re.sub(r'[<>:"/\\|?*]+', " ", f"Guion {sesion} ({'para grabar' if version == 'host' else 'producción'}).docx").strip()
    datos = docx_de_markdown(md, grande=version == "host")
    casa = Path.home()
    destino = next((d for d in (casa / "Downloads", casa / "Descargas", casa / "Desktop") if d.is_dir()), casa)
    f = destino / nombre
    f.write_bytes(datos)
    if carpeta_guion:
        try:
            Path(carpeta_guion).mkdir(parents=True, exist_ok=True)
            (Path(carpeta_guion) / nombre).write_bytes(datos)
        except OSError:
            pass
    try:
        subprocess.Popen(["explorer", "/select,", str(f)])
    except OSError:
        pass
    return f
