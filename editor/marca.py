"""La marca del equipo: la sala no trae ninguna marca adentro, la lee del Drive del proyecto (la misma carpeta que usa
la Sala de diseño, así cada equipo carga la suya una sola vez).

  _MARCA/
    marca.json   nombre, colores [{nombre, hex, uso}], fuentes {titulo, cuerpo, subtitulos} (archivos de fuentes/),
                 logos {archivo: {nombre, uso, fondo}}, evitar, obligatorio, cierres, cta, notas_para_claude
    logos/       logos en PNG (mejor con fondo transparente): principal, horizontal, ícono, para fondo claro u oscuro
    productos/   fotos reales del producto sin fondo
    fuentes/     las tipografías de la marca (.ttf u .otf)

Sin _MARCA (equipo nuevo) la sala funciona igual con una marca neutra.
"""
import json
import os
import shutil
import threading
import time
from pathlib import Path

import motor

CARPETA = "_MARCA"
TIPOS = {"logos": (".png", ".webp", ".jpg", ".jpeg"), "productos": (".png", ".webp", ".jpg", ".jpeg"), "fuentes": (".ttf", ".otf")}
USOS_COLOR = ("principal", "secundario", "acento", "fondo", "texto", "otro")
USOS_LOGO = ("principal", "horizontal", "icono", "otro")
FONDOS_LOGO = ("oscuro", "claro", "cualquiera")  # sobre qué fondo se ve bien
NEUTRA = {
    "nombre": "",
    "colores": [{"nombre": "Tinta", "hex": "#1F2933", "uso": "texto"}, {"nombre": "Claro", "hex": "#F4F4F5", "uso": "fondo"},
                {"nombre": "Acento", "hex": "#2563EB", "uso": "acento"}],
    "texto_oscuro": "#1F2933", "texto_claro": "#FFFFFF",
    "fuentes": {}, "logos": {},
    "evitar": [], "obligatorio": [], "cierres": [],
    "cta": {"titulo": "", "texto": "", "boton": ""},
    "notas_para_claude": "",
}
_cerrojo = threading.Lock()


def raiz():
    """Dónde vive la copia de _MARCA en esta PC: junto a las sesiones de Drive, o en los datos de la sala sin Drive."""
    import drive
    return (drive.CACHE if drive.estado()["activo"] else motor.DATOS) / CARPETA


def _leer_json():
    try:
        return json.loads((raiz() / "marca.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _archivos(sub):
    d = raiz() / sub
    return sorted(f.name for f in d.iterdir() if f.is_file() and f.suffix.lower() in TIPOS[sub]) if d.is_dir() else []


# un logo subido sin datos: por su nombre se sabe casi siempre cuál es (logo_blanco va sobre fondo oscuro)
USO_POR_NOMBRE = (("icono", ("icono", "ícono", "icon", "isotipo", "simbolo", "símbolo")), ("horizontal", ("horizontal", "horiz")))
FONDO_POR_NOMBRE = (("oscuro", ("blanco", "white", "claro", "light", "negativo")), ("claro", ("negro", "black", "oscuro", "dark", "azul", "color")))


def _adivinar(archivo, tabla, sino):
    n = Path(archivo).stem.lower()
    return next((valor for valor, palabras in tabla if any(p in n for p in palabras)), sino)


def datos():
    """La marca completa (lo guardado sobre la neutra) con la lista de archivos de cada carpeta."""
    propia = _leer_json()
    out = {**NEUTRA, **propia}
    info = out.get("logos") if isinstance(out.get("logos"), dict) else {}  # la Sala de diseño guardaba una lista
    out["logos"] = [{"archivo": a, "nombre": (info.get(a) or {}).get("nombre") or Path(a).stem,
                     "uso": (info.get(a) or {}).get("uso") or _adivinar(a, USO_POR_NOMBRE, "principal"),
                     "fondo": (info.get(a) or {}).get("fondo") or _adivinar(a, FONDO_POR_NOMBRE, "cualquiera")}
                    for a in _archivos("logos")]
    # la Sala de diseño guarda los colores sin «uso»: el primero queda de principal y el resto se puede elegir
    colores = [dict(c) for c in out.get("colores") or [] if isinstance(c, dict)]
    for c in colores:
        if c.get("uso") not in USOS_COLOR:
            c["uso"] = "otro"
    if colores and not any(c["uso"] == "principal" for c in colores):
        colores[0]["uso"] = "principal"
    out["colores"] = colores
    out["productos"] = _archivos("productos")
    out["fuentes_archivos"] = _archivos("fuentes")
    out["tiene_marca"] = bool(propia)
    return out


def resumen():
    """Lo que la pantalla necesita siempre (colores y logos para portadas y plantillas)."""
    d = datos()
    return {"nombre": d.get("nombre", ""), "colores": d.get("colores", []), "logos": d["logos"], "fuentes": d.get("fuentes", {}),
            "tiene_marca": d["tiene_marca"]}


def _limpio(d):
    out = {}
    out["nombre"] = str(d.get("nombre") or "")[:80]
    colores = []
    for c in (d.get("colores") or [])[:24]:
        h = str(c.get("hex") or "").strip()
        if len(h) == 7 and h.startswith("#"):
            colores.append({"nombre": str(c.get("nombre") or "")[:40], "hex": h.upper(),
                            "uso": c.get("uso") if c.get("uso") in USOS_COLOR else "otro"})
    out["colores"] = colores
    for k in ("texto_oscuro", "texto_claro"):
        if d.get(k):
            out[k] = str(d[k])[:7]
    out["fuentes"] = {k: v for k, v in (d.get("fuentes") or {}).items() if k in ("titulo", "cuerpo", "subtitulos") and isinstance(v, str) and v}
    logos = {}
    for l in d.get("logos") or []:
        if l.get("archivo") in _archivos("logos"):
            logos[l["archivo"]] = {"nombre": str(l.get("nombre") or "")[:40], "uso": l.get("uso") if l.get("uso") in USOS_LOGO else "otro",
                                   "fondo": l.get("fondo") if l.get("fondo") in FONDOS_LOGO else "cualquiera"}
    out["logos"] = logos
    for k in ("evitar", "obligatorio", "cierres"):
        out[k] = [str(x).strip()[:200] for x in (d.get(k) or []) if str(x).strip()][:60]
    cta = d.get("cta") or {}
    out["cta"] = {k: str(cta.get(k) or "")[:200] for k in ("titulo", "texto", "boton")}
    out["notas_para_claude"] = str(d.get("notas_para_claude") or "")[:4000]
    return out


def guardar(d):
    """Guarda marca.json (lo que no se edita en la sala, como la dirección visual de la Sala de diseño, se conserva)."""
    with _cerrojo:
        r = raiz()
        r.mkdir(parents=True, exist_ok=True)
        actual = _leer_json()
        nuevo = {**actual, **_limpio(d), "cambio": time.time()}
        tmp = r / ".marca.json.part"
        tmp.write_text(json.dumps(nuevo, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, r / "marca.json")
    subir_en_segundo_plano()
    return datos()


def guardar_archivo(tipo, nombre, contenido, subir=True):
    """Un logo, una foto de producto o una tipografía. Devuelve el nombre con que quedó.
    subir=False: al importar muchos de una vez se suben todos juntos al final."""
    if tipo not in TIPOS:
        raise ValueError("Tipo de archivo de marca no válido")
    nombre = "".join(c if c.isalnum() or c in "._- " else "_" for c in Path(nombre).name).strip()[:80]
    if not nombre or Path(nombre).suffix.lower() not in TIPOS[tipo]:
        raise ValueError({"logos": "Sube el logo en PNG (mejor con fondo transparente), WEBP o JPG",
                          "productos": "Sube la foto en PNG (sin fondo), WEBP o JPG",
                          "fuentes": "Sube la tipografía en .ttf u .otf"}[tipo])
    if not contenido:
        raise ValueError("El archivo llegó vacío")
    d = raiz() / tipo
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{nombre}.part"
    tmp.write_bytes(contenido)
    if tipo == "fuentes":
        import fuentes
        try:
            fuentes.Fuente(tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise ValueError("Ese archivo no es una tipografía que la sala pueda leer")
    os.replace(tmp, d / nombre)
    if tipo == "fuentes" and subir:
        copiar_fuentes()
    if subir:
        subir_en_segundo_plano()
    return nombre


def borrar(tipo, nombre):
    """Aparta un archivo de la marca (a _MARCA/_BORRADOS, en la PC y en Drive): no se borra del todo."""
    if tipo not in TIPOS or "/" in nombre or "\\" in nombre or nombre.startswith("."):
        raise ValueError("Archivo no válido")
    f = raiz() / tipo / nombre
    if not f.is_file():
        return
    guardar_en = raiz() / "_BORRADOS"
    guardar_en.mkdir(exist_ok=True)
    os.replace(f, guardar_en / f"{time.strftime('%Y%m%d-%H%M%S')}_{tipo}_{nombre}")
    import drive
    if drive.estado()["activo"]:
        def tarea():
            try:
                drive._rc("moveto", f"{drive.REMOTO}:{CARPETA}/{tipo}/{nombre}",
                          f"{drive.REMOTO}:{CARPETA}/_BORRADOS/{time.strftime('%Y%m%d-%H%M%S')}_{tipo}_{nombre}", timeout=120)
            except Exception as e:  # noqa: BLE001
                print("No se pudo apartar en Drive:", e)
        threading.Thread(target=tarea, daemon=True).start()


def ruta_archivo(rel):
    """logos/x.png, productos/x.png o fuentes/x.ttf, sin salir de _MARCA."""
    base = raiz().resolve()
    ruta = (base / rel).resolve()
    if base not in ruta.parents or ruta.parent.name not in TIPOS:
        raise ValueError("Archivo de marca no válido")
    return ruta


def logo_para(fondo="oscuro", archivo=None):
    """El logo para la portada: el elegido, o el principal que se ve bien sobre ese fondo (las portadas suelen ser oscuras)."""
    logos = datos()["logos"]
    if archivo:
        l = next((x for x in logos if x["archivo"] == archivo), None)
        if l:
            return raiz() / "logos" / l["archivo"]
    orden = sorted(logos, key=lambda l: (l["fondo"] not in (fondo, "cualquiera"), l["uso"] != "principal", l["uso"] != "horizontal"))
    return raiz() / "logos" / orden[0]["archivo"] if orden else None


def copiar_fuentes():
    """Las tipografías de la marca pasan a las fuentes de la sala (así salen en las plantillas y las portadas)."""
    import fuentes
    d = raiz() / "fuentes"
    if not d.is_dir():
        return
    fuentes.PROPIAS.mkdir(parents=True, exist_ok=True)
    for f in d.iterdir():
        if f.suffix.lower() in TIPOS["fuentes"]:
            destino = fuentes.PROPIAS / f.name
            if not destino.exists() or destino.stat().st_size != f.stat().st_size:
                shutil.copy2(f, destino)
    fuentes.cargar.cache_clear()


def bajar():
    """Trae _MARCA de Drive (al abrir la sala y cada tanto)."""
    import drive
    if not drive.estado()["activo"]:
        return
    try:
        drive._rc("copy", f"{drive.REMOTO}:{CARPETA}", raiz(), "--update", "--exclude", "_BORRADOS/**", "--exclude", "referencias/**", timeout=300)
    except Exception as e:  # noqa: BLE001 — sin internet, o el equipo todavía no tiene marca
        if "not found" not in str(e).lower():
            print("No se pudo bajar la marca:", e)
    copiar_fuentes()


def subir_en_segundo_plano():
    import drive
    if not drive.estado()["activo"]:
        return

    def tarea():
        try:
            drive._rc("copy", raiz(), f"{drive.REMOTO}:{CARPETA}", "--update", "--exclude", "_BORRADOS/**", "--exclude", ".*", timeout=300)
        except Exception as e:  # noqa: BLE001
            print("No se pudo subir la marca:", e)
            drive._anotar_pendiente("", [CARPETA], e)
    threading.Thread(target=tarea, daemon=True).start()
