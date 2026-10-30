"""Claude dentro de la sala: escribe el guion de una sesión y revisa lo grabado, con la suscripción de Claude
(Claude Code en esta PC). No usa API ni claves: Claude Code entra una sola vez con la cuenta de Claude.

Cómo funciona:
  - Busca Claude Code (claude.exe): el que trae la app de escritorio de Claude o el que se instala aparte.
  - Lo que Claude necesita saber (skills del guionista y del director, contexto de la marca) vive en Drive,
    en «_SALA/claude» (privado, lo sube Claude desde la PC del dueño con preparar_claude.py). Se baja a datos/claude/.
  - Se corre «claude -p» en esa carpeta: lee las skills de .claude/skills y escribe solo en la carpeta de la sesión.
"""
import glob
import json
import re
import os
import shutil
import subprocess
import time
from pathlib import Path

import motor
import perfil

CONOCIMIENTO = motor.DATOS / "claude"
_estado = {"t": 0, "datos": None}


def ruta_skill(nombre="guionista"):
    """La SKILL.md que usa este equipo para «guionista», «director» o «flow» (perfil.skill): la suya, de _SALA/claude,
    o la general que trae la sala (marcas/skills)."""
    propia = CONOCIMIENTO / ".claude" / "skills" / perfil.skill(nombre) / "SKILL.md"
    if propia.exists():
        return propia
    return CONOCIMIENTO / ".claude" / "skills" / perfil.SKILLS[nombre] / "SKILL.md"  # la del perfil no está: la general


def encontrar():
    """Ruta de claude.exe o None."""
    candidatos = [shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude.exe")]
    appdata, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
    # el que trae la app de escritorio de Claude (pestaña Code), el más nuevo. Si la app vino de la Microsoft Store,
    # Windows guarda su carpeta dentro del paquete (Packages\Claude_…\LocalCache\Roaming) y fuera de él no se ve en APPDATA.
    patrones = [os.path.join(appdata, "Claude", "claude-code", "*", "*", "claude.exe"),
                os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code", "*", "*", "claude.exe"),
                os.path.join(local, "Claude-Data", "*", "claude-code", "*", "*", "claude.exe")]  # app de escritorio desde 2026-10
    candidatos += sorted((p for pat in patrones for p in glob.glob(pat)), key=lambda p: os.path.getmtime(p), reverse=True)
    candidatos.append(os.path.join(appdata, "npm", "claude.cmd"))
    return next((c for c in candidatos if c and os.path.isfile(c)), None)


def _sin_ventana():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW


def estado(forzar=False):
    if not forzar and _estado["datos"] and time.time() - _estado["t"] < 30:
        return _estado["datos"]
    ruta = encontrar()
    out = {"instalado": bool(ruta), "sesion": False, "conocimiento": (CONOCIMIENTO / ".claude" / "skills").is_dir()}
    if ruta:
        try:
            r = subprocess.run([ruta, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30, cwd=str(motor.DATOS), **_sin_ventana())
            out["sesion"] = bool(json.loads(r.stdout or "{}").get("loggedIn"))
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    _estado.update(t=time.time(), datos=out)
    return out


def iniciar_sesion():
    """Abre una ventana donde Claude Code pide entrar con la cuenta de Claude (suscripción). Una sola vez."""
    ruta = encontrar()
    if not ruta:
        raise RuntimeError("No encuentro Claude Code en esta PC")
    _estado["datos"] = None
    subprocess.Popen(["cmd", "/c", "start", "Claude - iniciar sesion", "/wait", ruta, "auth", "login", "--claudeai"],
                     cwd=str(motor.DATOS))


def actualizar_conocimiento(avance=print):
    """Baja de Drive las skills y el contexto (_SALA/claude). Si no hay Drive, usa lo que ya haya."""
    import drive
    if drive.estado()["activo"]:
        try:
            avance("Bajando lo que Claude necesita saber (skills y contexto)…")
            drive._rc("copy", f"{drive.REMOTO}:_SALA/claude", CONOCIMIENTO, timeout=300)
        except Exception as e:  # noqa: BLE001 — un equipo nuevo no tiene skills propias: usa las generales de la sala
            print("Sin skills propias en Drive (_SALA/claude):", str(e)[:120])
    poner_generales()
    if not ruta_skill().exists():
        raise RuntimeError(f"Falta la skill «{perfil.skill('guionista')}» que pide el perfil de la marca (en Drive, _SALA/claude). "
                           "Pídele a Claude, en la PC principal, «sube las skills a la sala», o borra esa skill del perfil.")


def poner_generales():
    """Las skills generales de la sala (marcas/skills: guionista, director y Flow para cualquier marca) quedan junto a las
    del equipo sin pisarlas (tienen otros nombres), y el perfil de la marca queda en perfil_marca.md para que Claude o
    Gemini lo lean."""
    origen, destino = perfil.BASE / "skills", CONOCIMIENTO / ".claude" / "skills"
    if origen.is_dir():
        for d in origen.iterdir():
            if d.is_dir():
                shutil.copytree(d, destino / d.name, dirs_exist_ok=True)
    CONOCIMIENTO.mkdir(parents=True, exist_ok=True)
    (CONOCIMIENTO / "perfil_marca.md").write_text(perfil.como_markdown(), encoding="utf-8")


def correr(prompt, carpetas, avance=print, max_turnos=80, chrome=False):
    """Corre Claude Code sin ventana y va contando lo que hace. Devuelve el texto final.
    chrome=True: también maneja el Chrome de esta PC con la extensión de Claude (Flow y Flow Music en la cuenta del usuario)."""
    ruta = encontrar()
    if not ruta:
        raise RuntimeError("No encuentro Claude Code en esta PC (ver Inicio → Claude)")
    cmd = [ruta, "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
           "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep", "Skill", "TodoWrite", *(["mcp__claude-in-chrome"] if chrome else []),
           "--disallowedTools", "Bash", "WebFetch", "WebSearch", "--max-turns", str(max_turnos), *(["--chrome"] if chrome else [])]
    for c in carpetas:
        cmd += ["--add-dir", str(c)]
    p = subprocess.Popen(cmd, cwd=str(CONOCIMIENTO), stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace", **_sin_ventana())
    motor.PROCESOS.add(p)
    final, pasos = None, 0
    nombres = {"Read": "Leyendo", "Write": "Escribiendo", "Edit": "Corrigiendo", "Glob": "Buscando", "Grep": "Buscando",
               "Skill": "Usando la skill", "TodoWrite": "Planificando"}
    for linea in p.stdout:
        try:
            ev = json.loads(linea)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    pasos += 1
                    arg = b.get("input", {})
                    que = arg.get("file_path") or arg.get("pattern") or arg.get("skill") or ""
                    nombre = b.get("name", "")
                    if nombre.startswith("mcp__claude-in-chrome__"):
                        avance("Claude · Chrome · " + (arg.get("action_summary") or arg.get("action") or nombre.split("__")[-1]))
                    elif nombre == "Edit":
                        # el mismo archivo una y otra vez («Corrigiendo produccion.json» ×80) parecía colgado: se dice
                        # qué pieza va y en qué paso está
                        cambio = str(arg.get("new_string") or arg.get("old_string") or "")
                        pieza = re.search(r'"((?:[CP]\d+)(?:_H\d+|_CTA)?|CTA[\w-]*)"\s*:', cambio)
                        avance(f"Claude · {'trabajando en ' + pieza[1] if pieza else 'corrigiendo ' + Path(str(que)).name} · paso {pasos}")
                    else:
                        avance(f"Claude · {nombres.get(nombre, nombre)} {Path(str(que)).name} · paso {pasos}".strip())
                elif b.get("type") == "text" and b.get("text", "").strip():
                    avance("Claude · " + b["text"].strip().splitlines()[0][:140])
        elif ev.get("type") == "result":
            final = ev
    err = p.stderr.read()
    p.wait()
    motor.PROCESOS.discard(p)
    motor.revisar_cancelado()
    if not final:
        raise RuntimeError("Claude no respondió. " + err.strip()[-300:])
    if final.get("is_error"):
        texto = final.get("result") or final.get("subtype") or "error"
        if "login" in texto.lower():
            raise RuntimeError("Claude Code no tiene la sesión iniciada: Inicio → «Iniciar sesión en Claude».")
        raise RuntimeError("Claude: " + texto[:300])
    return final.get("result") or ""


def generar_guion(sesion, pedido, avance=print):
    """Escribe guiones.md, hoja_de_grabacion.md y produccion.json en <sesión>/Guion con la skill del guionista."""
    actualizar_conocimiento(avance)
    carpeta = motor.Rutas(sesion).principal  # en un grupo, la sesión que guarda el guion
    guion = carpeta / "Guion"
    guion.mkdir(parents=True, exist_ok=True)
    anteriores = motor.S.base
    prompt = f"""Usa la skill {ruta_skill().parent.name} y escribe el guion de la sesión «{sesion}».
{perfil.contexto()}
El perfil de la marca (qué vende, para quién, tono, palabras, reglas de imágenes y música) está en {CONOCIMIENTO / "perfil_marca.md"}: respétalo.

Pedido del equipo:
{pedido.strip() or "(sin indicaciones: elige tú 1 o 2 ideas y 5 ángulos según el contexto y lo que funcionó antes)"}

Estás dentro de la sala de edición, sin nadie a quien preguntarle: no pidas confirmación, decide con criterio.
Respeta la estructura del pedido (cuántos cuerpos, hooks por cuerpo y CTAs) y el tema elegido para cada cuerpo, aunque la skill hable de 5×5.
En produccion.json deja decidido todo: estilos de subtítulo, imágenes de Flow con su prompt, música con su prompt y sonidos (marcas sfx).
Escribe los tres archivos (guiones.md, hoja_de_grabacion.md y produccion.json) directamente en:
{guion}
Las sesiones anteriores (para no repetir cuerpos ni hooks) están en {anteriores} (cada una con su carpeta Guion).
Si alguna tiene Guion/resultados.md, son las vistas y la retención reales de sus videos: usa más los tipos de hook
y los temas que ganaron, y evita los que peor funcionaron (dilo en tu resumen final).
Los prompts de Flow y de música van siempre. Al terminar responde en 5 líneas: ideas, ángulos y cuántas imágenes y pistas hay que generar."""
    texto = correr(prompt, [carpeta, anteriores], avance=avance)
    prod = guion / "produccion.json"
    if not prod.exists():
        raise RuntimeError("Claude terminó pero no dejó produccion.json. " + texto[:200])
    try:
        json.loads(prod.read_text(encoding="utf-8"))
    except ValueError as e:
        avance("El produccion.json salió con un error de formato: Claude lo corrige…")
        correr(f"El archivo {prod} no es JSON válido ({e}). Corrígelo sin cambiar el contenido.", [carpeta], avance=avance, max_turnos=15)
        json.loads(prod.read_text(encoding="utf-8"))
    return texto




def generar_prompts_flow(avance=print):
    """Agrega a produccion.json los prompts de Flow (assets) y las marcas de imagen de cada pieza, a partir de su texto.
    No cambia el texto, los tiempos ni lo que ya tiene el guion. Sirve para un guion armado con lo grabado."""
    actualizar_conocimiento(avance)
    motor.usar_semana(motor.S.semana)
    prod = motor.S.guion / "produccion.json"
    if not prod.exists():
        raise RuntimeError("Esta sesión no tiene guion: primero arma el guion con lo grabado")
    reglas = ruta_skill()
    prompt = f"""Lee el guion de la sesión «{motor.S.semana}»: {prod}
Las reglas para escribir prompts de Flow están en la sección «Prompts limpios» de {reglas}. Léela antes de escribir.

Esta sala no tiene imágenes para sus clips todavía. Agrega, sin cambiar nada más del archivo:
1. En `assets`, una imagen por cada cuerpo (C1, C2…) y una por cada hook (C1_H1, C1_H2…). Los CTA no llevan imagen.
   Cada asset: {{"id": "<pieza>_IMG1", "tipo": "imagen", "pieza": "<pieza>", "palabra": "<palabra marcada>",
   "flow": {{"herramienta": "Google Flow · Imagen", "formato": "9:16 vertical", "prompt": "…", "plan_b": "…",
   "que_muestra": "…", "evitar": "{perfil.leer()['imagenes']['evitar']}"}}}}
2. En cada pieza con imagen, agrega a `marcas` una marca por asset: {{"palabra": "…", "asset": "<id>", "dur": 1.5,
   "modo": "completa", "mov": "zoom_in"}} (alterna zoom_in y zoom_out entre marcas).

Reglas:
{perfil.reglas_imagenes()}
- La imagen sirve para lo que dice la frase, sin dar datos.
- `palabra` debe aparecer tal cual en el texto de SU pieza (en minúsculas, 1 a 3 palabras). Si no aparece, no pongas la marca.
- Prompts en inglés, fotorrealistas, sin texto ni logos. Nada de palabras de la lista prohibida de «Prompts limpios».
- No toques `texto`, `partes`, `dicho`, `origen`, `nota`, ni las marcas que ya existen (`sub`, `sfx`, `musica`).
- Usa Edit para cambiar el archivo. Al final el archivo debe ser JSON válido.

Al terminar responde en 3 líneas: cuántas imágenes agregaste y en qué piezas."""
    texto = correr(prompt, [motor.S.guion], avance=avance)
    try:
        datos = json.loads(prod.read_text(encoding="utf-8"))
    except ValueError as e:
        avance("El produccion.json salió con un error de formato: Claude lo corrige…")
        correr(f"El archivo {prod} no es JSON válido ({e}). Corrígelo sin cambiar el contenido.", [motor.S.guion], avance=avance, max_turnos=15)
        datos = json.loads(prod.read_text(encoding="utf-8"))
    if not datos.get("assets"):
        raise RuntimeError("Claude no agregó prompts de Flow. " + texto[:200])
    return texto


def mas_apoyo_visual(por_cuerpo=3, clips=1, por_hook=0, avance=print):
    """«Más apoyo visual»: deja cada cuerpo con `por_cuerpo` imágenes y `clips` clips en movimiento, y cada hook con
    `por_hook` imágenes (cuentan las que ya tiene: solo se agregan las que faltan), en las palabras clave.
    Devuelve el resumen de Claude."""
    actualizar_conocimiento(avance)
    prompt = pedido_apoyo_visual(por_cuerpo, clips, por_hook)
    prod = motor.S.guion / "produccion.json"
    n_antes = len(json.loads(prod.read_text(encoding="utf-8")).get("assets") or [])
    texto = correr(prompt, [motor.S.guion], avance=avance)
    try:
        datos = json.loads(prod.read_text(encoding="utf-8"))
    except ValueError as e:
        avance("El produccion.json salió con un error de formato: Claude lo corrige…")
        correr(f"El archivo {prod} no es JSON válido ({e}). Corrígelo sin cambiar el contenido.", [motor.S.guion], avance=avance, max_turnos=15)
        datos = json.loads(prod.read_text(encoding="utf-8"))
    if len(datos.get("assets") or []) <= n_antes:
        raise RuntimeError("Claude no agregó imágenes nuevas. " + texto[:200])
    motor._prod_cache = None
    return texto


def pedido_apoyo_visual(por_cuerpo=3, clips=1, por_hook=0, ruta=None, reglas=None):
    """El pedido de «Más apoyo visual», listo para dárselo a Claude o a Gemini (Antigravity trabaja sobre una copia del
    guion: `ruta` y `reglas` apuntan a esa copia y a las reglas copiadas a su carpeta)."""
    motor.usar_semana(motor.S.semana)
    pedido = []
    for f in motor.apoyo_visual()["piezas"]:
        img = max(0, (por_cuerpo if f["tipo"] == "cuerpo" else por_hook) - f["imagenes"])
        cl = max(0, clips - f["clips"]) if f["tipo"] == "cuerpo" else 0
        if img or cl:
            pedido.append(f"- {f['pieza']} ({f['tipo']}): " + " y ".join(x for x in (
                f"{img} imagen{'es' if img != 1 else ''}" if img else "", f"{cl} clip{'s' if cl != 1 else ''} en movimiento" if cl else "") if x))
    if not pedido:
        raise RuntimeError("Cada cuerpo y cada hook ya tienen esa cantidad de imágenes: sube los números para agregar más")
    prod = motor.S.guion / "produccion.json"
    if not prod.exists():
        raise RuntimeError("Esta sesión no tiene guion: primero arma el guion con lo grabado")
    reglas = reglas or ruta_skill()
    return f"""Lee el guion de la sesión «{motor.S.semana}»: {ruta or prod}
Las reglas para escribir prompts de Flow están en la sección «Prompts limpios» de {reglas}. Léela antes de escribir.

Los videos necesitan más apoyo visual. Agrega exactamente esto, sin tocar lo que ya hay (no agregues en otras piezas):
{chr(10).join(pedido)}

1. En `assets`, cada imagen nueva: {{"id": "<pieza>_IMG<n>", "tipo": "imagen", "pieza": "<pieza>", "palabra": "…",
   "flow": {{"herramienta": "Google Flow · Imagen", "formato": "9:16 vertical", "prompt": "…", "plan_b": "…",
   "que_muestra": "…", "evitar": "{perfil.leer()['imagenes']['evitar']}"}}}}
   (sigue la numeración: si ya existe C1_IMG1, las nuevas son C1_IMG2, C1_IMG3…).
   Cada clip: {{"id": "<pieza>_CLIP<n>", "tipo": "clip", "pieza": "<pieza>", "palabra": "…",
   "flow": {{"herramienta": "Google Flow · Video", "formato": "9:16 vertical, 4 a 6 segundos", "prompt": "…", "plan_b": "…",
   "que_muestra": "…", "evitar": "{perfil.leer()['imagenes']['evitar']}, fast cuts"}}}}
   El prompt del clip describe un plano corto con un movimiento simple de lo que se ve o de la cámara (por ejemplo
   «{perfil.leer()['imagenes']['ejemplo_clip']}»).
2. En `marcas` de esa pieza, una marca por asset nuevo: imágenes {{"palabra": "…", "asset": "<id>", "dur": 1.5, "modo": "completa",
   "mov": "zoom_in"}} (alterna zoom_in y zoom_out), clips {{"palabra": "…", "asset": "<id>", "dur": 2.5, "modo": "completa", "mov": "fijo"}}.

Dónde ponerlas:
- En un hook, la imagen va en las primeras palabras (los primeros 2 segundos), para enganchar; su marca dura 1.2 s.
- En un cuerpo, repártelas a lo largo de todo el cuerpo (principio, medio y final), en las palabras que más se pueden ver:
  {perfil.leer()['imagenes']['que_mostrar']}. Nunca dos marcas en la misma frase ni en la misma palabra que una que ya existe.
- `palabra` debe aparecer tal cual en el texto de ESA pieza (en minúsculas, 1 a 3 palabras). Si no aparece, elige otra.

Reglas de las imágenes (las de la marca):
{perfil.reglas_imagenes()}
- Prompts en inglés, fotorrealistas. Nada de la lista prohibida de «Prompts limpios».
- No toques `texto`, `partes`, `dicho`, `origen`, `nota`, ni las marcas ni los assets que ya existen.
- Usa Edit para cambiar el archivo. Al final el archivo debe ser JSON válido.

Al terminar responde en 3 líneas: cuántas imágenes y cuántos clips agregaste y en qué cuerpos."""

def pedido_musica(ruta, reglas):
    """El pedido para agregar la música a un produccion.json (lo usan Claude y Gemini en Antigravity)."""
    return f"""Lee el guion de la sesión «{motor.S.semana}»: {ruta}
Las reglas de la música están en la sección «Música» de {reglas}, y las palabras prohibidas en «Prompts limpios». Léelas antes de escribir.

Este guion no tiene música. Agrega, sin cambiar nada más del archivo:
1. `musica`: de 2 a 4 pistas según el tono de los cuerpos, con el formato de la skill:
   {{"id": "MUS_A", "uso": "C1 y C3 (…)", "desde": 0, "prompt": {{"herramienta": "Google Flow Music", "prompt": "…", "evitar": "…", "duracion": "60 s"}}}}
   Los id son MUS_A, MUS_B, MUS_C… El prompt en inglés, siempre instrumental, sin nombres de artistas ni canciones.
{perfil.reglas_musica("   ")}2. En cada cuerpo (C1, C2…), `"musica": "<id>"` con la pista que le toca. Los hooks y el CTA no llevan, salvo que la skill lo pida.
3. `"musica_por_defecto": "<id de la pista más usada>"`.
No toques `texto`, `partes`, `dicho`, `marcas`, `assets` ni nada de lo que ya existe. Usa Edit. Al final el archivo debe ser JSON válido.
Al terminar responde en 2 líneas: cuántas pistas y qué cuerpo usa cada una."""


def generar_prompts_musica(avance=print):
    """Agrega a produccion.json la música (prompts de Flow Music) y qué pista usa cada cuerpo, si el guion no la trae.
    No cambia el texto, los tiempos ni lo que ya tiene el guion."""
    actualizar_conocimiento(avance)
    motor.usar_semana(motor.S.semana)
    prod = motor.S.guion / "produccion.json"
    if not prod.exists():
        raise RuntimeError("Esta sesión no tiene guion: primero arma el guion con lo grabado")
    reglas = ruta_skill()
    prompt = pedido_musica(prod, reglas)
    texto = correr(prompt, [motor.S.guion], avance=avance, max_turnos=25)
    try:
        datos = json.loads(prod.read_text(encoding="utf-8"))
    except ValueError as e:
        avance("El produccion.json salió con un error de formato: Claude lo corrige…")
        correr(f"El archivo {prod} no es JSON válido ({e}). Corrígelo sin cambiar el contenido.", [motor.S.guion], avance=avance, max_turnos=15)
        datos = json.loads(prod.read_text(encoding="utf-8"))
    if not datos.get("musica"):
        raise RuntimeError("Claude no agregó la música. " + texto[:200])
    return texto


def generar_en_flow(imagenes, musica, carpeta_descargas, avance=print):
    """Claude abre el Chrome de esta PC (con la extensión de Claude) y genera en Google Flow las imágenes y clips, y en
    Flow Music las pistas, con la cuenta del usuario. Las deja en Descargas y dice qué archivo es cada id.
    imagenes: [{id, tipo, prompt, plan_b, formato, que_muestra}] · musica: [{id, prompt, evitar}]
    Devuelve {"archivos": {id: nombre en Descargas}, "urls": {id de pista: URL del audio}, "pendientes": [{id, motivo}]}."""
    actualizar_conocimiento(avance)
    skill = ruta_skill("flow")
    pedido = json.dumps({"imagenes": imagenes, "musica": musica}, ensure_ascii=False, indent=1)
    prompt = f"""Genera en el Chrome de esta PC lo que le falta a la sesión «{motor.S.semana}». Lo que hay que hacer:
{pedido}

Sigue la skill {skill} (léela primero: tiene los pasos y el mapa de pantallas de Flow y Flow Music).
- Imágenes y clips: en Google Flow (flow.google.com), en el proyecto con el nombre de la sesión (si no existe, créalo).
  Revisa que la configuración del agente esté en 9:16 y x1 (o el formato que diga cada una). Pide todas en un solo
  pedido, cada una con su código. Si Google rechaza un prompt, reenvíalo una vez; si vuelve a fallar, usa su plan_b.
- Música: en Flow Music (botón «Flow Music» de flow.google.com). El prompt empieza con «Instrumental only, no vocals, no lyrics.».
  Cada pedido saca dos versiones: quédate con la que no tenga voces ni cortes.
- Música: no la bajes. Para cada pista elegida, saca la URL de su audio (storage.googleapis.com/producer-app-public/clips/…,
  enganchando HTMLMediaElement.prototype.play y tocando «Play», como dice la skill) y ponla en «urls»: la sala la baja sola.
- Imágenes y clips: van a la carpeta Descargas de Chrome ({carpeta_descargas}). Baja cada archivo desde la página con fetch
  y un enlace <a download> cuyo nombre empiece con su id (por ejemplo «C1_IMG1.jpg»), en tamaño original (en la URL de la
  imagen cambia «=s512-rw» por «=s0»).
- Si Google pide iniciar sesión, elegir cuenta, aceptar términos o un captcha, o si se acabó el cupo: para y dilo. Nunca
  escribas contraseñas, no compres créditos ni cambies el plan. No generes nada que no esté en la lista.
- Cierra las pestañas que abriste al terminar. No edites archivos de la PC.
Al final responde SOLO con JSON, sin texto antes ni después:
{{"archivos": {{"<id de imagen o clip>": "<nombre exacto del archivo en Descargas>"}}, "urls": {{"<id de pista>": "<URL del audio>"}},
 "pendientes": [{{"id": "<id>", "motivo": "…"}}]}}"""
    respuesta = correr(prompt, [], avance=avance, max_turnos=150, chrome=True)
    inicio, fin = respuesta.find("{"), respuesta.rfind("}")
    try:
        datos = json.loads(respuesta[inicio:fin + 1]) if 0 <= inicio < fin else {}
    except ValueError:
        datos = {}
    if "archivos" not in datos:
        raise RuntimeError("Claude no terminó en Chrome: " + respuesta.strip()[:300])
    return datos


def rehacer_musica(mus_id, nota="", avance=print):
    """«⟳ Regenerar prompt» de una pista (MUS_A…): Claude reescribe su prompt de Flow Music con la regla de la marca
    (las reglas de música de la marca) y, si hay, la nota del equipo. El prompt anterior queda en las notas de la pista."""
    actualizar_conocimiento(avance)
    motor.usar_semana(motor.S.semana)
    prod = motor.S.guion / "produccion.json"
    if not prod.exists():
        raise RuntimeError("Esta sesión no tiene guion")
    datos = json.loads(prod.read_text(encoding="utf-8"))
    m = next((x for x in datos.get("musica", []) if str(x.get("id", "")).lower() == mus_id.lower()), None)
    if not m:
        raise RuntimeError(f"No encuentro {mus_id} en el guion")
    pr = m.setdefault("prompt", {})
    reglas = ruta_skill()
    prompt = f"""Hay que reescribir el prompt de Google Flow Music de la pista «{m['id']}» de unos videos{(' de ' + perfil.leer()['nombre']) if perfil.leer()['nombre'] else ''}.
Para qué se usa: {m.get('uso', '')}
Prompt actual: {pr.get('prompt') or '(no hay: escríbelo desde cero)'}
{('Lo que pide el equipo: ' + nota.strip()) if nota.strip() else ''}

Las reglas de la música están en la sección «Música» de {reglas}. Léela antes de escribir. Además:
{perfil.reglas_musica()}- En inglés, instrumental, sin voces, sin nombres de artistas ni canciones, que sirva de fondo para la voz.
- En «evitar» pon lo que la música no debe tener{(' (siempre: ' + perfil.leer()['musica']['evitar'] + ')') if perfil.leer()['musica']['evitar'] else ''} y lo que ya estaba que siga valiendo.
No edites ningún archivo. Responde SOLO con JSON, sin texto antes ni después:
{{"prompt": "…", "evitar": "…", "duracion": "{pr.get('duracion') or '60 s'}", "cambio": "qué cambiaste, en una frase en español"}}"""
    respuesta = correr(prompt, [], avance=avance, max_turnos=10)
    inicio, fin = respuesta.find("{"), respuesta.rfind("}")
    nuevo = json.loads(respuesta[inicio:fin + 1]) if 0 <= inicio < fin else {}
    if not str(nuevo.get("prompt", "")).strip():
        raise RuntimeError("Claude no devolvió el prompt nuevo. Intenta de nuevo.")
    m.setdefault("notas", []).append({"fecha": time.strftime("%Y-%m-%d %H:%M"), "nota": nota.strip() or "regenerar",
                                      "prompt_anterior": pr.get("prompt", "")})
    pr.setdefault("herramienta", "Google Flow Music")
    pr.update({k: nuevo[k] for k in ("prompt", "evitar", "duracion") if nuevo.get(k)})
    prod.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    return nuevo


def rehacer_imagen(asset_id, nota, avance=print):
    """La nota del equipo sobre una imagen o clip de Flow («tiene arándanos, que se vea el polvo») -> Claude reescribe su prompt.
    El prompt nuevo y la nota quedan en produccion.json; la imagen se vuelve a generar en Flow con ese prompt."""
    actualizar_conocimiento(avance)
    motor.usar_semana(motor.S.semana)
    prod = motor.S.guion / "produccion.json"
    if not prod.exists():
        raise RuntimeError("Esta sesión no tiene guion")
    datos = json.loads(prod.read_text(encoding="utf-8"))
    a = next((x for x in datos.get("assets", []) if str(x.get("id", "")).lower() == asset_id.lower()), None)
    if not a:
        raise RuntimeError(f"No encuentro {asset_id} en el guion")
    flow = a.setdefault("flow", {})
    pieza = (datos.get("piezas") or {}).get(a.get("pieza"), {})
    antes = [n["nota"] for n in a.get("notas", []) if n.get("nota")]
    reglas = ruta_skill()
    prompt = f"""Hay que rehacer en Google Flow {'el clip' if a.get('tipo') == 'clip' else 'la imagen'} «{a['id']}» de un video.
Lo que dice la pieza {a.get('pieza', '')}: «{pieza.get('texto', '') if isinstance(pieza, dict) else ''}»
La imagen aparece cuando se dice «{a.get('palabra', '')}».
Prompt con el que se hizo: {flow.get('prompt') or '(no hay: escríbelo desde cero)'}
Lo que está mal, según el equipo: {nota.strip()}
{('Notas anteriores de esta misma imagen (también valen): ' + ' | '.join(antes)) if antes else ''}

Lee la sección «Prompts limpios» de {reglas} antes de escribir. Además:
{perfil.reglas_imagenes()}
- Formato {flow.get('formato') or '9:16 vertical'}, fotorrealista, en inglés, sin texto, sin logos ni envases con marca.
Escribe el prompt nuevo completo (no solo el cambio): corrige lo que dice el equipo y mantén lo que estaba bien.
No edites ningún archivo. Responde SOLO con JSON, sin texto antes ni después:
{{"prompt": "…", "plan_b": "el mismo plano dicho más neutro", "que_muestra": "en español, qué se ve", "cambio": "qué cambiaste, en una frase en español"}}"""
    respuesta = correr(prompt, [], avance=avance, max_turnos=10)
    inicio, fin = respuesta.find("{"), respuesta.rfind("}")
    nuevo = json.loads(respuesta[inicio:fin + 1]) if 0 <= inicio < fin else {}
    if not str(nuevo.get("prompt", "")).strip():
        raise RuntimeError("Claude no devolvió el prompt nuevo. Intenta de nuevo.")
    a.setdefault("notas", []).append({"fecha": time.strftime("%Y-%m-%d %H:%M"), "nota": nota.strip(), "prompt_anterior": flow.get("prompt", "")})
    flow.update({k: nuevo[k] for k in ("prompt", "plan_b", "que_muestra") if nuevo.get(k)})
    # la imagen que ya estaba es la del prompt viejo: queda «por regenerar» hasta que llegue una más nueva que esto
    a["prompt_nuevo_t"] = time.time()
    prod.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    return nuevo


def proponer_temas(cuerpos, publico, idea, avance=print):
    """Claude propone de qué habla cada cuerpo (idea + ángulo), para elegir antes de escribir el guion.
    Devuelve [{"tema", "angulo", "por_que"}] (2 opciones por cuerpo, para que el equipo elija)."""
    actualizar_conocimiento(avance)
    prompt = f"""Lee el perfil de la marca en perfil_marca.md y, si existe, el contexto de la marca en contexto/ (marca, productos, guía de hooks
y guiones aprobados), y las sesiones anteriores
en {motor.S.base} (cada una con Guion/), y propone temas para {cuerpos} cuerpos de video para {publico}.
{('Idea del equipo: ' + idea.strip()) if idea.strip() else 'Elige tú las ideas según lo que más vende y lo que no se ha hecho.'}
Sigue la skill {ruta_skill().parent.name} para elegir ideas y ángulos (no repitas temas ya hechos).
Si alguna sesión tiene Guion/resultados.md (vistas y retención reales), prioriza los temas y ángulos que mejor funcionaron.
Responde SOLO con JSON, sin texto antes ni después: una lista de {cuerpos * 2} objetos, 2 opciones por cuerpo, en orden
(cuerpo 1 opción A, cuerpo 1 opción B, cuerpo 2 opción A…), cada uno así:
{{"cuerpo": 1, "tema": "frase corta", "angulo": "qué es | cómo se hace | error común | caso real | mito | pregunta frecuente", "por_que": "una línea"}}"""
    texto = correr(prompt, [motor.S.base], avance=avance, max_turnos=25)
    inicio, fin = texto.find("["), texto.rfind("]")
    if inicio < 0 or fin < 0:
        raise RuntimeError("Claude no devolvió la lista de temas. Intenta de nuevo.")
    return json.loads(texto[inicio:fin + 1])


def revisar_grabacion(sesion, avance=print):
    """La skill del director sobre lo grabado: informe de qué cambió y plan de edición; la sala aplica el plan."""
    actualizar_conocimiento(avance)
    motor.usar_semana(sesion)
    motor.procesar(avance=avance)  # lo que falte transcribir (lo ya procesado se salta)
    trabajo = motor.S.guion / "director"
    trabajo.mkdir(parents=True, exist_ok=True)
    avance("Preparando lo grabado para Claude (dossier)…")
    (trabajo / "dossier.json").write_text(json.dumps(motor.dossier(), ensure_ascii=False, indent=1), encoding="utf-8")
    (trabajo / "plan.json").unlink(missing_ok=True)
    prompt = f"""Usa la skill {ruta_skill('director').parent.name} con la sesión «{sesion}».

Estás dentro de la sala de edición: no corras comandos. La sala ya procesó los clips y te deja el dossier en
{trabajo / "dossier.json"}
Escribe en esa misma carpeta:
  - informe.md: el informe «qué cambió» (corto, por pieza) y la lista de decisiones, con los prompts de Flow y música que falten.
  - plan.json: el plan de edición con el formato de la skill.
La sala aplica el plan sola cuando termines."""
    texto = correr(prompt, [trabajo], avance=avance)
    plan = trabajo / "plan.json"
    if plan.exists():
        r = motor.aplicar_plan(json.loads(plan.read_text(encoding="utf-8")))
        avance("Plan aplicado. Rearma los videos para verlo.")
        return {"texto": texto, "aplicado": r}
    return {"texto": texto, "aplicado": None}
