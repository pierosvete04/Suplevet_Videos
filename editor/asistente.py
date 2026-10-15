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
import os
import shutil
import subprocess
import time
from pathlib import Path

import motor

CONOCIMIENTO = motor.DATOS / "claude"
_estado = {"t": 0, "datos": None}


def encontrar():
    """Ruta de claude.exe o None."""
    candidatos = [shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude.exe")]
    appdata, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
    # el que trae la app de escritorio de Claude (pestaña Code), el más nuevo. Si la app vino de la Microsoft Store,
    # Windows guarda su carpeta dentro del paquete (Packages\Claude_…\LocalCache\Roaming) y fuera de él no se ve en APPDATA.
    patrones = [os.path.join(appdata, "Claude", "claude-code", "*", "*", "claude.exe"),
                os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code", "*", "*", "claude.exe")]
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
        except Exception as e:
            if not (CONOCIMIENTO / ".claude").is_dir():
                raise RuntimeError(f"No pude bajar las skills de Drive (_SALA/claude): {e}")
    if not (CONOCIMIENTO / ".claude" / "skills").is_dir():
        raise RuntimeError("Faltan las skills de Claude. Pídele a Claude, en la PC principal, «sube las skills a la sala».")


def correr(prompt, carpetas, avance=print, max_turnos=80):
    """Corre Claude Code sin ventana y va contando lo que hace. Devuelve el texto final."""
    ruta = encontrar()
    if not ruta:
        raise RuntimeError("No encuentro Claude Code en esta PC (ver Inicio → Claude)")
    cmd = [ruta, "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
           "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep", "Skill", "TodoWrite",
           "--disallowedTools", "Bash", "WebFetch", "WebSearch", "--max-turns", str(max_turnos)]
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
                    avance(f"Claude · {nombres.get(b.get('name'), b.get('name'))} {Path(str(que)).name}".strip())
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
    prompt = f"""Usa la skill suplevet-guionista-modular y escribe el guion de la sesión «{sesion}».

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


def proponer_temas(cuerpos, publico, idea, avance=print):
    """Claude propone de qué habla cada cuerpo (idea + ángulo), para elegir antes de escribir el guion.
    Devuelve [{"tema", "angulo", "por_que"}] (2 opciones por cuerpo, para que el equipo elija)."""
    actualizar_conocimiento(avance)
    prompt = f"""Lee el contexto de la marca en contexto/ (marca, productos, guía de hooks y guiones aprobados) y las sesiones anteriores
en {motor.S.base} (cada una con Guion/), y propone temas para {cuerpos} cuerpos de video para {publico}.
{('Idea del equipo: ' + idea.strip()) if idea.strip() else 'Elige tú las ideas según lo que más vende y lo que no se ha hecho.'}
Sigue la skill suplevet-guionista-modular para elegir ideas y ángulos (no repitas temas ya hechos).
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
    prompt = f"""Usa la skill suplevet-director con la sesión «{sesion}».

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
        avance("Plan aplicado. Re-arma los videos para verlo.")
        return {"texto": texto, "aplicado": r}
    return {"texto": texto, "aplicado": None}
