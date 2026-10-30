"""El perfil de marca para los videos: lo que la sala necesita saber de la marca para transcribir, resaltar palabras,
reconocer el cierre (CTA) y pedirle a la IA (Claude, o Gemini en Antigravity) guiones, imágenes y música.

Vive en Drive, en _MARCA/perfil.json, junto a marca.json (la parte visual: logos, colores, letras). Así cada equipo
tiene el suyo y todas sus PCs lo comparten (marca.bajar trae la carpeta entera). Sin perfil, la sala usa uno neutro
que sirve para cualquier producto. El de Suplevet viene en marcas/suplevet.json y se pone solo la primera vez en un
equipo cuya marca se llama Suplevet: para ellos nada cambia.

Lo que usa cada parte de la sala:
  transcripción  vocabulario, correcciones
  subtítulos     palabras_clave (se resaltan)
  efectos        palabras_marca (la primera vez que se dicen en un cuerpo: brillo; y son el golpe de silencio de ejemplo)
  armar          cta (frases con las que la marca pide la compra: así se reconoce un CTA grabado)
  IA             nombre, que_vende, publico, tono, imagenes, musica, notas, skills
"""
import copy
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path

import motor

ARCHIVO = "perfil.json"
BASE = motor.RAIZ / "marcas"  # perfiles de ejemplo y skills generales que trae la sala

NEUTRO = {
    "nombre": "",
    "que_vende": "",
    "publico": "",
    "publicos": [],              # para elegir al pedir temas o un guion («Médicos veterinarios», «Dueños de mascotas»)
    "plantilla": "marca",        # la plantilla de subtítulos de la marca (por defecto, la que toma sus colores)
    "tono": "Venta directa, cercana y con ritmo: frases cortas, nada de palabras técnicas sin explicar.",
    "palabras_marca": [],
    "vocabulario": [],
    "palabras_clave": [],
    "correcciones": {},
    "cta": [],
    "imagenes": {
        "reglas": [
            "Cada imagen muestra una escena real y cotidiana que se entiende al instante: una persona, el producto en uso o "
            "el resultado. Nunca diagramas, gráficos, íconos ni cosas abstractas.",
            "La escena muestra el estado que dice la frase: el problema si habla del problema (sin exagerar ni dar miedo), "
            "el resultado si habla del resultado (contento, tranquilo, mejor).",
            "Sin texto, sin logos ni envases con marca.",
        ],
        "que_mostrar": "el problema, el producto en uso, el resultado",
        "evitar": "text, logos, watermark, deformed hands, extra fingers, cartoon style",
        "ejemplo": {"palabra": "resultado", "prompt": "Photorealistic vertical 9:16 photo of a smiling young woman at home "
                    "in soft morning light, relaxed and happy, natural colors, shallow depth of field.",
                    "plan_b": "Photorealistic vertical 9:16 shot of a happy person in a bright living room, warm daylight.",
                    "que_muestra": "Una persona contenta en su casa"},
        "ejemplo_clip": "a smiling person turning toward the camera in a bright room, slow camera push-in",
    },
    "musica": {"reglas": [], "evitar": ""},
    "skills": {},
    "notas": "",
}
CAMPOS_TEXTO = ("nombre", "que_vende", "publico", "tono", "notas")
CAMPOS_LISTA = ("palabras_marca", "vocabulario", "palabras_clave", "cta", "publicos")
SKILLS = {"guionista": "guionista-marca", "director": "director-marca", "flow": "flow-marca"}  # las generales de la sala

_cerrojo = threading.Lock()
_cache = {"clave": None, "datos": None}
_BASE_CFG = {}


def _ruta():
    import marca
    return marca.raiz() / ARCHIVO


def _norm(t):
    t = unicodedata.normalize("NFD", str(t).lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if unicodedata.category(c) != "Mn")).strip()


def propio():
    """Lo que el equipo guardó (sin lo neutro). {} si no tiene perfil."""
    try:
        return json.loads(_ruta().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def leer():
    """El perfil completo: lo neutro con lo del equipo encima (las imágenes y la música, campo por campo)."""
    f = _ruta()
    try:
        clave = (str(f), f.stat().st_mtime)
    except OSError:
        clave = (str(f), None)
    if _cache["clave"] == clave and _cache["datos"] is not None:
        return _cache["datos"]
    p = propio()
    out = copy.deepcopy(NEUTRO)
    for k, v in p.items():
        if k in ("imagenes", "musica") and isinstance(v, dict):
            out[k].update({x: y for x, y in v.items() if y not in (None, "", [], {})})
        elif k in out and v not in (None, ""):
            out[k] = v
    if not out["palabras_marca"] and out["nombre"]:
        out["palabras_marca"] = [out["nombre"]]
    out["tiene_perfil"] = bool(p)
    _cache.update(clave=clave, datos=out)
    return out


def _limpio(d):
    out = {}
    for k in CAMPOS_TEXTO:
        out[k] = str(d.get(k) or "").strip()[:4000 if k == "notas" else 600]
    for k in CAMPOS_LISTA:
        v = d.get(k) or []
        if isinstance(v, str):
            v = re.split(r"[,\n]", v)
        elif not isinstance(v, (list, tuple)):  # un número o algo suelto: una sola palabra
            v = [v]
        out[k] = [str(x).strip()[:120] for x in v if x is not None and str(x).strip()][:300]
    if str(d.get("plantilla") or "").strip():
        out["plantilla"] = str(d["plantilla"]).strip()[:60]
    corr = d.get("correcciones") or {}
    if isinstance(corr, str):  # «mal → bien» una por línea
        corr = dict(x.split("→", 1) if "→" in x else x.split("=", 1) for x in corr.splitlines() if "→" in x or "=" in x)
    if not isinstance(corr, dict):
        corr = {}
    out["correcciones"] = {str(k).strip()[:80]: str(v).strip()[:80] for k, v in corr.items()
                           if str(k).strip() and str(v).strip() and str(k).strip() != str(v).strip()}  # «fos» → «FOS» sí sirve (mayúsculas)
    im = d.get("imagenes") or {}
    if not isinstance(im, dict):  # solo reglas, como texto o lista
        im = {"reglas": im}
    reglas = im.get("reglas") or []
    if isinstance(reglas, str):
        reglas = reglas.splitlines()
    out["imagenes"] = {"reglas": [str(x).strip(" -•\t")[:600] for x in reglas if str(x).strip(" -•\t")][:30]}
    for k in ("que_mostrar", "evitar", "ejemplo_clip"):
        if im.get(k):
            out["imagenes"][k] = str(im[k]).strip()[:400]
    if isinstance(im.get("ejemplo"), dict) and im["ejemplo"].get("prompt"):
        out["imagenes"]["ejemplo"] = {k: str(im["ejemplo"].get(k) or "")[:500] for k in ("palabra", "prompt", "plan_b", "que_muestra")}
    mu = d.get("musica") or {}
    if not isinstance(mu, dict):
        mu = {"reglas": mu}
    reglas = mu.get("reglas") or []
    if isinstance(reglas, str):
        reglas = reglas.splitlines()
    out["musica"] = {"reglas": [str(x).strip(" -•\t")[:400] for x in reglas if str(x).strip(" -•\t")][:20],
                     "evitar": str(mu.get("evitar") or "").strip()[:400]}
    if isinstance(mu.get("animos"), dict):
        out["musica"]["animos"] = {k: str(v)[:400] for k, v in mu["animos"].items() if k in motor.ANIMOS and str(v).strip()}
    sk = d.get("skills") if isinstance(d.get("skills"), dict) else {}
    out["skills"] = {k: str(v).strip() for k, v in sk.items() if k in SKILLS and str(v).strip()}
    return out


def guardar(d, subir=True):
    """Guarda el perfil del equipo (en la PC y, por detrás, en Drive)."""
    import marca
    with _cerrojo:
        f = _ruta()
        f.parent.mkdir(parents=True, exist_ok=True)
        nuevo = {**_limpio(d), "cambio": time.time()}
        tmp = f.with_name(f".{f.name}.part")
        tmp.write_text(json.dumps(nuevo, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, f)
        _cache["clave"] = None
    aplicar()
    if subir:
        marca.subir_en_segundo_plano()
    return leer()


def asegurar(subir=True):
    """La primera vez en un equipo cuya marca se llama como un perfil de ejemplo (marcas/<nombre>.json), ese perfil
    queda como el del equipo. Si el equipo ya lo tenía, se le suman solo los campos que el ejemplo trae y el suyo no
    (los de una versión nueva de la sala); lo que el equipo cambió no se toca. Devuelve True si cambió algo."""
    import marca
    nombre = _norm(marca.datos().get("nombre") or "")
    ejemplo = BASE / f"{re.sub(r'[^a-z0-9]+', '-', nombre).strip('-')}.json" if nombre else None
    if not ejemplo or not ejemplo.exists():
        return False
    base, suyo = json.loads(ejemplo.read_text(encoding="utf-8")), propio()
    if not suyo:
        guardar(base, subir=subir)
        return True
    faltan = {k: v for k, v in base.items() if k not in suyo}
    if not faltan:
        return False
    guardar({**suyo, **faltan}, subir=subir)
    return True


def aplicar():
    """Pone el perfil en la configuración de la sala (solo en memoria: config.json queda neutro)."""
    p = leer()
    cfg = motor.CONFIG
    if not _BASE_CFG:
        _BASE_CFG.update(copy.deepcopy({k: cfg.get(k) for k in ("palabras_clave", "transcripcion", "portada")}))
    tr = copy.deepcopy(_BASE_CFG.get("transcripcion") or {})
    voc = ", ".join(p["vocabulario"])
    tr["vocabulario"] = (voc + ("" if voc.endswith(".") else ".")) if voc else tr.get("vocabulario", "")
    tr["correcciones"] = {**(tr.get("correcciones") or {}), **p["correcciones"]}
    cfg["transcripcion"] = tr
    cfg["palabras_clave"] = list(p["palabras_clave"]) or list(_BASE_CFG.get("palabras_clave") or [])
    portada = copy.deepcopy(_BASE_CFG.get("portada") or {})
    if p["nombre"] and not portada.get("marca"):
        portada["marca"] = p["nombre"].upper()
    cfg["portada"] = portada
    sub = copy.deepcopy(_BASE_CFG.setdefault("subtitulos", copy.deepcopy(cfg.get("subtitulos") or {})))
    if p.get("plantilla") in motor.plantillas():
        sub["plantilla"] = p["plantilla"]  # la de la marca, si esta PC no eligió otra (plantilla_activa)
    cfg["subtitulos"] = sub
    _colores_de_marca()
    motor.PERFIL = p
    return p


def _colores_de_marca():
    """Las plantillas «colores_de_marca» (como «Colores de tu marca») toman el acento y el principal de la marca."""
    import marca
    try:
        colores = marca.datos().get("colores") or []
    except Exception:  # noqa: BLE001 — sin marca: quedan los colores de fábrica
        return
    uso = lambda *usos: next((c["hex"] for u in usos for c in colores if c.get("uso") == u and c.get("hex")), None)
    acento, principal = uso("acento", "secundario", "principal"), uso("principal", "texto")
    if not acento:
        return
    h = acento.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    claro = .2126 * r + .7152 * g + .0722 * b > .6
    for pl in (motor.CONFIG.get("plantillas_sub") or {}).values():
        if pl.get("colores_de_marca"):
            res = dict(pl.get("resaltador") or {})
            res.update(color=acento, texto=(principal if claro and principal else "#111111") if claro else "#FFFFFF")
            pl.update(resaltador=res, color_resaltado=acento)


# ---------- lo que usan las demás partes ----------

def es_marca(palabra, siguientes=()):
    """¿Con esta palabra empieza el nombre de la marca? Una marca de varias palabras («Ropa Fit») se mira con las que
    vienen después (`siguientes`), así «ropa» sola no cuenta."""
    limpio = lambda x: _norm(re.sub(r"[^\w\s]", "", x))
    w = limpio(palabra)
    if not w:
        return False
    for marca in leer()["palabras_marca"]:
        partes = _norm(re.sub(r"[^\w\s]", " ", marca)).split()
        if partes and partes[0] == w and [limpio(x) for x in list(siguientes)[:len(partes) - 1]] == partes[1:]:
            return True
    return False


def dice_cta(texto):
    """¿El texto tiene una de las frases con las que la marca pide la compra?"""
    t = _norm(re.sub(r"[^\w\s]", " ", texto))
    return any(_norm(re.sub(r"[^\w\s]", " ", f)) in t for f in leer()["cta"] if len(f.strip()) > 3)


def ejemplo_marca():
    """La palabra que se pone de ejemplo en el golpe de silencio («donde entra la marca»)."""
    p = leer()
    return (p["palabras_marca"] or [p["nombre"] or "producto"])[0].lower()


def reglas_imagenes():
    """Las reglas de las imágenes y clips de Flow, como lista con guiones (para los pedidos a la IA)."""
    return "\n".join(f"- {r}" for r in leer()["imagenes"]["reglas"])


def contexto():
    """Unas líneas sobre la marca para el principio de un pedido a la IA."""
    p = leer()
    partes = [f"Marca: {p['nombre']}." if p["nombre"] else "",
              f"Vende: {p['que_vende']}." if p["que_vende"] else "",
              f"Para: {p['publico']}." if p["publico"] else "",
              f"Tono: {p['tono']}" if p["tono"] else ""]
    return " ".join(x for x in partes if x)


def skill(nombre):
    """El nombre de la skill que usa este equipo para «guionista», «director» o «flow»: la suya (en _SALA/claude) o
    la general que trae la sala."""
    return leer()["skills"].get(nombre) or SKILLS[nombre]


def como_markdown():
    """El perfil en palabras, para que Claude o Gemini lo lean junto a la skill (perfil_marca.md)."""
    p = leer()
    lineas = [f"# Perfil de la marca{': ' + p['nombre'] if p['nombre'] else ''}", ""]
    for titulo, k in (("Qué vende", "que_vende"), ("Para quién", "publico"), ("Tono", "tono")):
        if p[k]:
            lineas += [f"## {titulo}", p[k], ""]
    if p["palabras_marca"]:
        lineas += ["## Cómo se escribe la marca", ", ".join(p["palabras_marca"]), ""]
    if p["vocabulario"]:
        lineas += ["## Palabras del producto", ", ".join(p["vocabulario"]), ""]
    if p["cta"]:
        lineas += ["## Cómo pide la compra (CTA)", *[f"- {x}" for x in p["cta"]], ""]
    lineas += ["## Reglas de las imágenes y clips", reglas_imagenes(), f"Lo que más se puede mostrar: {p['imagenes']['que_mostrar']}.", ""]
    if p["musica"]["reglas"]:
        lineas += ["## Reglas de la música", *[f"- {x}" for x in p["musica"]["reglas"]], ""]
    if p["notas"]:
        lineas += ["## Más sobre la marca", p["notas"], ""]
    return "\n".join(lineas)


def reglas_musica(sangria=""):
    """Las reglas de música de la marca para un pedido a la IA («» si la marca no tiene)."""
    p = leer()["musica"]
    lineas = [f"{sangria}REGLAS DE LA MARCA para la música:"] + [f"{sangria}- {r}" for r in p["reglas"]] if p["reglas"] else []
    if p["evitar"]:
        lineas.append(f"{sangria}- En «evitar» pon siempre: {p['evitar']}.")
    return "\n".join(lineas) + ("\n" if lineas else "")


# ---------- llenar el perfil con IA (Claude o Antigravity) ----------

def pedido_ia(descripcion, destino=None, base=None, comentario=""):
    """El pedido para que la IA proponga el perfil a partir de lo que cuenta el equipo (o el texto de su web). Con `base`
    (el perfil que ya tiene) y `comentario` («las frases de compra son por web, no WhatsApp»), lo mejora en vez de
    empezar de cero: así se va afinando de a pocos."""
    import marca
    m = marca.datos()
    ejemplo = json.dumps({k: v for k, v in NEUTRO.items() if k not in ("skills",)}, ensure_ascii=False, indent=1)
    salida = (f"Escribe el resultado en {destino} (un solo JSON, con la herramienta de escribir archivos) y responde en 1 línea "
              "que terminaste.") if destino else "Responde SOLO con el JSON, sin texto antes ni después."
    return f"""Arma el perfil de una marca para una sala que edita sus videos cortos (reels y anuncios) en español.
Marca: {m.get('nombre') or '(sin nombre todavía)'}
Lo que cuenta el equipo sobre su marca:
\"\"\"
{descripcion.strip()[:6000] or '(no contó más: usa el perfil que ya tiene)'}
\"\"\"
{('El perfil que ya tiene (consérvalo: mejóralo, complétalo donde esté vacío y cambia solo lo que el equipo pide):' + chr(10) + json.dumps(base, ensure_ascii=False, indent=1)) if base else ''}
{('Lo que el equipo pide ahora: ' + comentario.strip()[:2000]) if comentario.strip() else ''}
{('Ya dijeron que evitan: ' + '; '.join(m.get('evitar') or [])) if m.get('evitar') else ''}
{('Notas que ya tenían: ' + (m.get('notas_para_claude') or '')) if m.get('notas_para_claude') else ''}

Devuelve un JSON con estos campos (el ejemplo es el perfil neutro; reemplázalo con lo de ESTA marca):
{ejemplo}

Cómo llenarlo:
- nombre, que_vende, publico, tono: en español simple, una o dos frases cada uno.
- palabras_marca: el nombre de la marca como se escribe (y sus productos, si tienen nombre propio).
- vocabulario: 10 a 25 palabras o frases que se dicen en sus videos (producto, componentes, problemas, cierre), para que la
  transcripción las escriba bien.
- palabras_clave: 8 a 15 palabras que conviene resaltar en los subtítulos.
- correcciones: cómo puede escribir mal la transcripción el nombre de la marca y las palabras difíciles («mal»: «bien»).
- cta: 2 a 5 frases con las que pide la compra o el contacto (por WhatsApp, link, tienda…).
- imagenes.reglas: 3 a 6 reglas para las imágenes que acompañan sus videos (qué siempre se ve, qué nunca), pensando en
  Google Flow: escenas reales, sin texto ni logos. imagenes.que_mostrar: lo que más se puede mostrar. imagenes.evitar: en
  inglés, lo que nunca debe salir. imagenes.ejemplo: una imagen típica de sus videos (prompt en inglés, vertical 9:16,
  fotorrealista). imagenes.ejemplo_clip: un plano corto en inglés con un movimiento simple.
- musica.reglas: 1 a 3 reglas de la música de fondo según el tono de la marca. musica.evitar: en inglés.
- notas: lo demás que la IA debe saber al escribir sus guiones (lo que nunca se promete, cómo se nombra el producto…).
No inventes datos del producto que el equipo no dijo. {salida}"""


def _json_de(texto):
    i, j = texto.find("{"), texto.rfind("}")
    if i < 0 or j <= i:
        raise RuntimeError("La IA no devolvió el perfil. Intenta de nuevo con más detalle de tu marca.")
    try:
        return _limpio(json.loads(texto[i:j + 1]))
    except ValueError as e:
        raise RuntimeError(f"La IA devolvió el perfil con un error de formato ({e}). Intenta de nuevo.") from e


def llenar_claude(descripcion, avance=print, base=None, comentario=""):
    """Claude propone el perfil (no lo guarda: la persona lo revisa y toca «Guardar marca»)."""
    import asistente
    avance("Claude está armando el perfil de tu marca…")
    return _json_de(asistente.correr(pedido_ia(descripcion, base=base, comentario=comentario), [], avance=avance, max_turnos=8))


def _carpeta_ia():
    casa = Path.home()
    base = next((d for d in (casa / "Downloads", casa / "Descargas", casa / "Desktop") if d.is_dir()), casa)
    return base / "Perfil de marca - Antigravity"


def llenar_antigravity(descripcion, base=None, comentario=""):
    """Le pide el perfil a Gemini en Antigravity: lo escribe en Descargas/Perfil de marca - Antigravity/perfil_propuesto.json
    y la pantalla lo trae sola (propuesta_antigravity)."""
    import antigravity
    import shutil
    carpeta = _carpeta_ia()
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / "perfil_propuesto.json"
    texto = pedido_ia(descripcion, destino, base=base, comentario=comentario)
    (carpeta / "INSTRUCCIONES.md").write_text(texto, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(texto, encoding="utf-8")
    mensaje = (f"Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo tal cual: escribe {destino}. No leas nada más, no escribas scripts "
               "ni corras comandos.")
    abierto, enviado, porque = antigravity._pasar(carpeta, mensaje, "flash", True, titulo="Sala · Perfil de marca")
    return {"carpeta": str(carpeta), "mensaje": mensaje, "abierto": abierto, "enviado": enviado, "porque": porque,
            "pedido": time.time()}


def propuesta_antigravity():
    """El perfil que dejó Gemini, si ya lo escribió ({} si todavía no)."""
    f = _carpeta_ia() / "perfil_propuesto.json"
    try:
        return {**_json_de(f.read_text(encoding="utf-8")), "_cuando": f.stat().st_mtime}
    except (OSError, ValueError, RuntimeError):
        return {}
