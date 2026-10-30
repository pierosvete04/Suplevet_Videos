"""La plantilla de guion en Excel: columna A el código (H1, C1.1, C1 CTA…), columna B lo que se dice, columna C una
nota que no se graba. Una hoja por sesión (por ejemplo, una por ronda). La sala la lee sin adivinar nada, y también la
escribe (para descargarla vacía o para guardar un guion que Antigravity pasó a la plantilla). Sin librerías.

Códigos de la columna A:
  H1, H2…        hook que sirve para TODOS los cuerpos de la hoja (se graba una vez y se combina con cada cuerpo)
  C1 H1          hook solo del cuerpo 1
  C1.1, C1.2…    frases del cuerpo 1, en orden («C1» solo: la frase que sigue)
  C1 CTA         el cierre del cuerpo 1 (varias filas = varias frases del mismo cierre)
  CTA, CTA1…     cierre que sirve para todos los cuerpos
"""
import io
import re
import unicodedata
import zipfile
from xml.sax.saxutils import escape

COLUMNAS = ["Código", "Lo que se dice", "Nota (no se graba)"]
_NO_GUION = ("leeme", "instrucciones", "ejemplo", "indice", "como usar")


def _norm(t):
    t = unicodedata.normalize("NFD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t).split())


def hojas_de_guion(contenido):
    """{nombre de la hoja: filas} solo de las hojas que son guion (sin «Léeme», índice ni ejemplos)."""
    import importar_marca
    try:
        hojas = importar_marca.hojas_excel(contenido)
    except ValueError:
        raise ValueError("Ese archivo no es un Excel (.xlsx) que la sala pueda abrir. Guárdalo de nuevo como .xlsx.")
    return {n: f for n, f in hojas.items() if not _norm(n).startswith(_NO_GUION) and any(len(x) > 1 and x[1] for x in f)}


_SEP = r"[\s._\-–—:]*"


def leer_codigo(codigo):
    """(tipo, cuerpo, número) de un código de la columna A, o None si no se entiende.
    tipo: «hook_todos», «hook», «frase», «cuerpo» (frase sin número), «cta_cuerpo», «cta»."""
    c = " ".join(str(codigo or "").upper().replace("·", " ").split())
    if m := re.fullmatch(rf"(?:H|HOOK){_SEP}(\d+)", c):
        return "hook_todos", None, int(m[1])
    if m := re.fullmatch(rf"([CP]){_SEP}(\d+){_SEP}(?:H|HOOK){_SEP}(\d+)", c):
        return "hook", f"{m[1]}{int(m[2])}", int(m[3])
    if m := re.fullmatch(rf"([CP]){_SEP}(\d+){_SEP}CTA{_SEP}(\d*)", c):
        return "cta_cuerpo", f"{m[1]}{int(m[2])}", int(m[3]) if m[3] else 0
    if m := re.fullmatch(rf"CTA{_SEP}([A-Z0-9]*)", c):
        return "cta", None, m[1]
    if m := re.fullmatch(r"([CP])\s*(\d+)\s*[._\-–—: ]\s*(\d+)", c):
        return "frase", f"{m[1]}{int(m[2])}", int(m[3])
    if m := re.fullmatch(r"([CP])\s*(\d+)", c):
        return "cuerpo", f"{m[1]}{int(m[2])}", 0
    return None


def piezas_de_filas(filas):
    """Las filas de una hoja ([código, texto, nota]) → {pieza: {tipo, texto, partes?, todos?, nota?}}.
    Si hay códigos que no se entienden, avisa cuáles (fila y código) en vez de adivinar."""
    frases, hooks, todos, notas, malos = {}, {}, {}, {}, []
    for i, f in enumerate(filas, 1):
        cod = (f[0] if f else "").strip()
        txt = " ".join((f[1] if len(f) > 1 else "").split())
        nota = (f[2] if len(f) > 2 else "").strip()
        if _norm(cod) in ("codigo", "cod") or not txt:  # la fila de títulos, o un código todavía sin texto
            continue
        if not cod:
            malos.append(f"fila {i} sin código")
            continue
        r = leer_codigo(cod)
        if not r:
            malos.append(f"fila {i}: «{cod}»")
            continue
        tipo, cuerpo, n = r
        if tipo == "hook_todos":
            todos[n] = txt
            continue
        if tipo == "hook":
            nombre = f"{cuerpo}_H{n}"
            if nombre in hooks:
                malos.append(f"fila {i}: «{cod}» está dos veces")
            hooks[nombre] = txt
            continue
        nombre = (cuerpo if tipo in ("frase", "cuerpo") else f"{cuerpo}_CTA{n or ''}" if tipo == "cta_cuerpo"
                  else "CTA" + (n if not n or n.isdigit() else f"-{n}"))
        d = frases.setdefault(nombre, {})
        k = n if tipo == "frase" else max(d, default=0) + 1
        if k in d:
            malos.append(f"fila {i}: «{cod}» está dos veces")
        d[k] = txt
        if nota and nombre not in notas:
            notas[nombre] = nota
    if malos:
        raise ValueError("Revisa la columna A (el código): " + "; ".join(malos[:6])
                         + ("…" if len(malos) > 6 else "") + ". Usa H1, C1.1, C1 CTA o CTA (ver la hoja «Léeme»).")
    # los hooks para todos los cuerpos quedan como hooks del primer cuerpo, con la marca «todos»: se graban una vez
    # (Hooks/H1.MOV) y la sala los combina con cada cuerpo
    cuerpos = sorted((k for k in frases if "CTA" not in k), key=lambda x: (x[0], int(re.sub(r"\D", "", x) or 0)))
    primero = cuerpos[0] if cuerpos else "C1"
    for n, t in todos.items():
        nombre = f"{primero}_H{n}"
        if nombre in hooks:
            raise ValueError(f"«H{n}» (para todos los cuerpos) choca con «{primero} H{n}». Usa uno de los dos.")
        hooks[nombre] = t
    out = {}
    for nombre, d in frases.items():
        partes = [d[k] for k in sorted(d)]
        out[nombre] = {"tipo": "cta" if "CTA" in nombre else "cuerpo", "texto": " ".join(partes),
                       **({"partes": partes} if len(partes) > 1 else {}), **({"nota": notas[nombre]} if nombre in notas else {})}
    for nombre, t in hooks.items():
        out[nombre] = {"tipo": "hook", "texto": t, **({"todos": True} if int(nombre.split("_H")[1]) in todos
                                                       and nombre.startswith(primero + "_") else {})}
    orden = {"cuerpo": 0, "hook": 1, "cta": 2}
    return dict(sorted(out.items(), key=lambda x: (orden[x[1]["tipo"]], x[0])))


def leer(contenido, hoja=None):
    """El guion de una plantilla: (nombre de la hoja, piezas). Con varias hojas de guion hay que decir cuál."""
    hojas = hojas_de_guion(contenido)
    if not hojas:
        raise ValueError("Ese Excel no trae guion: la columna A tiene que tener los códigos (H1, C1.1, C1 CTA) y la B el texto.")
    if hoja is None and len(hojas) > 1:
        e = ValueError("Ese Excel tiene varias hojas de guion: elige cuál va en esta sesión.")
        e.hojas = list(hojas)
        raise e
    nombre = hoja if hoja is not None else next(iter(hojas))
    if nombre not in hojas:
        raise ValueError(f"Ese Excel no tiene la hoja «{nombre}».")
    piezas = piezas_de_filas(hojas[nombre])
    if not piezas:
        raise ValueError(f"La hoja «{nombre}» no trae piezas.")
    return nombre, piezas


def filas_de_piezas(piezas):
    """Lo contrario de piezas_de_filas: el guion de la sala en filas de la plantilla (para descargarlo y corregirlo)."""
    filas, ctas = [], []
    for nombre, pz in piezas.items():
        partes = pz.get("partes") or [pz.get("texto", "")]
        if pz.get("tipo") == "hook" or re.search(r"_H\d+$", nombre):
            filas.append(("0" if pz.get("todos") else "1", nombre, [f"H{nombre.split('_H')[1]}" if pz.get("todos")
                                                                    else nombre.replace("_", " "), pz.get("texto", ""), ""]))
        elif "CTA" in nombre:
            cod = nombre.replace("_", " ")
            ctas += [[cod, t, ""] for t in partes]
        else:
            filas += [("2", nombre, [f"{nombre}.{i}", t, pz.get("nota", "") if i == 1 else ""]) for i, t in enumerate(partes, 1)]
    filas.sort(key=lambda x: (x[0], int(re.sub(r"\D", "", x[1].split("_")[0]) or 0)))
    return [f for _, _, f in filas] + ctas


# ---------- Escribir el Excel (sin librerías: un .xlsx es un zip con XML) ----------

def _celda(ref, v, estilo=0):
    v = str(v if v is not None else "")
    s = f' s="{estilo}"' if estilo else ""
    return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{escape(v)}</t></is></c>'


def _hoja_xml(filas, anchos, titulo=True):
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(anchos, 1))
    filas_xml = []
    for r, f in enumerate(filas, 1):
        celdas = "".join(_celda(f"{chr(65 + c)}{r}", v, 1 if titulo and r == 1 else 2) for c, v in enumerate(f))
        filas_xml.append(f'<row r="{r}">{celdas}</row>')
    congelar = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
                'state="frozen"/></sheetView></sheetViews>') if titulo else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{congelar}<cols>{cols}</cols><sheetData>{"".join(filas_xml)}</sheetData></worksheet>')


def _nombre_hoja(n, usados):
    n = " ".join(re.sub(r"[\[\]:*?/\\]+", " ", str(n)).split())
    if len(n) > 31:  # Excel no deja más de 31 letras: se corta en una palabra entera
        n = n[:31].rsplit(" ", 1)[0] if " " in n[:31] else n[:31]
    n = n or "Guion"
    base, k = n, 2
    while n.lower() in usados:
        n = f"{base[:28]} {k}"
        k += 1
    usados.add(n.lower())
    return n


LEEME = [
    ["Plantilla de guion"],
    ["Una hoja por sesión (por ejemplo, una por ronda). La sala lee la columna A (el código) y la B (lo que se dice)."],
    ["La columna C es una nota para ti (la estructura, una indicación): no se graba."],
    [""],
    ["Código", "Qué es"],
    ["H1, H2, H3…", "Hook que sirve para TODOS los cuerpos de la hoja: se graba una vez y se combina con cada cuerpo."],
    ["C1 H1", "Hook solo del cuerpo 1."],
    ["C1.1, C1.2…", "Frases del cuerpo 1, en orden (una fila por frase)."],
    ["C1 CTA", "El cierre del cuerpo 1."],
    ["CTA", "Un cierre que sirve para todos los cuerpos."],
    [""],
    ["Al grabar, guarda los clips así: Clips/Hooks/H1.MOV, Clips/C1/Cuerpo/1.MOV, Clips/C1/C1_CTA.MOV (o deja que la sala los reconozca por lo que dicen)."],
    ["Para subirlo: Guion → «Subir guion» → elige este Excel (y la hoja, si hay varias)."],
]

EJEMPLO = [COLUMNAS,
           ["H1", "(Hook 1: sirve para todos los cuerpos)", "Fórmula: Deja de…"],
           ["H2", "(Hook 2)", "Curiosidad"],
           ["H3", "(Hook 3)", "Atajo"],
           ["C1.1", "(Cuerpo 1, frase 1)", "Estructura: PAS"],
           ["C1.2", "(Cuerpo 1, frase 2)", ""],
           ["C1.3", "(Cuerpo 1, frase 3)", ""],
           ["C1 CTA", "(Cierre del cuerpo 1)", ""],
           ["C2.1", "(Cuerpo 2, frase 1)", "Estructura: AIDA"],
           ["C2.2", "(Cuerpo 2, frase 2)", ""],
           ["C2 CTA", "(Cierre del cuerpo 2)", ""]]


def escribir_xlsx(hojas, con_leeme=True):
    """hojas = [(nombre, filas con la de títulos primero)] → bytes del .xlsx."""
    todas, usados = [], set()
    if con_leeme:
        todas.append((_nombre_hoja("Léeme", usados), _hoja_xml(LEEME, [22, 110], titulo=False)))
    for nombre, filas in hojas:
        todas.append((_nombre_hoja(nombre, usados), _hoja_xml(filas, [12, 95, 30])))
    tipos = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    for i in range(1, len(todas) + 1))
    libro = "".join(f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>' for i, (n, _) in enumerate(todas, 1))
    rels = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                   for i in range(1, len(todas) + 1))
    n = len(todas) + 1
    estilos = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
               '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
               '<fill><patternFill patternType="solid"><fgColor rgb="FF1F2937"/><bgColor indexed="64"/></patternFill></fill></fills>'
               '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
               '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
               '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
               '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
               '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>'
               '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
               '</styleSheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   f'{tipos}</Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f'<sheets>{libro}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'{rels}<Relationship Id="rId{n}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '</Relationships>')
        z.writestr("xl/styles.xml", estilos)
        for i, (_, xml) in enumerate(todas, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return buf.getvalue()


def plantilla_vacia():
    return escribir_xlsx([("Guion", EJEMPLO)])


def guardar_en_descargas(nombre, contenido):
    """La deja en Descargas (o en el escritorio) y abre la carpeta con el archivo marcado."""
    import subprocess
    from pathlib import Path
    casa = Path.home()
    destino = next((d for d in (casa / "Downloads", casa / "Descargas", casa / "Desktop", casa / "Escritorio") if d.is_dir()), casa)
    f = destino / re.sub(r'[<>:"/\\|?*]+', " ", nombre).strip()
    f.write_bytes(contenido)
    try:
        subprocess.Popen(["explorer", "/select,", str(f)])
    except OSError:
        pass
    return f
