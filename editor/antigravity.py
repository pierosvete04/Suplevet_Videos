"""Paquete para Antigravity: las imágenes y clips de Flow que faltan, listos para que Gemini Flash los haga sin Claude.

Deja en Descargas una carpeta «Paquete Antigravity - <sesión>» con:
  INSTRUCCIONES.md   lo que Gemini tiene que hacer, paso a paso (sin pensar: pegar de corrido y bajar todo al final)
  prompts.txt        un prompt por línea con su código delante («C1_IMG2: …»), en el orden en que se pegan
  prompts.json       lo mismo con el plan B de cada uno (si Google rechaza un prompt)
  bajar_flow.js      baja de un golpe las imágenes de Flow con su nombre (C1_IMG2.jpg), ya con la lista de nombres
Después, en la sala: Faltantes → «Poner en los videos» las pone en su lugar y las sube a Drive.

armar_prompts() es el otro paquete: Gemini escribe los prompts (lo que hace «Agregar los prompts» con Claude)
sobre una copia del guion, y traer_prompts() la revisa y la pasa a la sesión.

En los dos, la sala le pasa el mensaje a Gemini sola con «agentapi» de Antigravity (enviar(), lo mismo que en la Sala
de diseño: gemini.py). Si no puede, queda como antes: el mensaje copiado para pegarlo a mano.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import motor

BAJAR = r"""// Baja de un golpe las imágenes generadas en Google Flow, en tamaño completo y con su nombre.
// Se corre en la pestaña de Flow (herramienta para ejecutar JavaScript en la página) después de generar todo.
// Devuelve la lista de lo que bajó y cómo emparejó cada nombre con su imagen.
async () => {
  const NOMBRES = __NOMBRES__;

  const tarjetas = [...document.querySelectorAll('[aria-label="Abrir imagen en el editor"]')]
    .map(t => t.querySelector("img"))
    .filter(img => img && img.src.includes("/asb/"));
  const grande = src => src.replace(/=s\d+.*$/, "=s0");

  // 1) Emparejar por el texto «C1_IMG2:» que Flow deja cerca de cada imagen (gana la más nueva).
  const porTexto = {};
  for (const img of [...tarjetas].reverse()) {
    let p = img.parentElement;
    for (let i = 0; i < 8 && p; i++, p = p.parentElement) {
      const t = (p.innerText || "").slice(0, 400);
      const hallado = NOMBRES.filter(n => t.includes(n + ":"));
      if (hallado.length === 1) { porTexto[hallado[0]] ??= img.src; break; }
      if (hallado.length > 1) break;
    }
  }
  // 2) Lo que no se emparejó por texto sale de las últimas imágenes, en orden.
  const ultimas = tarjetas.slice(-NOMBRES.length).map(img => img.src);
  const usadas = new Set(Object.values(porTexto));
  const libres = ultimas.filter(s => !usadas.has(s));

  const resultado = [];
  for (const nombre of NOMBRES) {
    const metodo = porTexto[nombre] ? "texto" : "orden";
    const src = porTexto[nombre] || libres.shift();
    if (!src) { resultado.push({ nombre, error: "no encontré la imagen" }); continue; }
    try {
      const blob = await (await fetch(grande(src))).blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = nombre + ".jpg";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      resultado.push({ nombre: nombre + ".jpg", kb: Math.round(blob.size / 1024), metodo });
    } catch (e) {
      resultado.push({ nombre, error: e.message });
    }
  }
  return resultado;
}
"""


MENSAJE = "MENSAJE_PARA_GEMINI.txt"
PEDIDOS = {"imagenes": "Paquete Antigravity", "musica": "Musica Antigravity", "prompts": "Prompts Antigravity"}


def pedidos():
    """Los pedidos a Antigravity de esta sesión que siguen sin terminar, con su mensaje (para volver a copiarlo)."""
    ck = motor.checklist()
    hecho = {"imagenes": not any(not a.get("listo") for a in ck.get("assets", [])),
             "musica": bool(ck.get("musica")) and all(m.get("listo") for m in ck["musica"]),
             "prompts": not (_carpeta(PEDIDOS["prompts"])[1] / "_sala.json").exists()}
    out = []
    for tipo, que in PEDIDOS.items():
        carpeta = _carpeta(que)[1]
        f = carpeta / MENSAJE
        if f.exists() and not hecho[tipo]:
            out.append({"tipo": tipo, "carpeta": str(carpeta), "mensaje": f.read_text(encoding="utf-8"),
                        "fecha": f.stat().st_mtime, "instalado": bool(exe()), "abierto": bool(_servidor())})
    return out


def abrir_pedido(tipo):
    """Abre Antigravity con la carpeta del pedido (o la carpeta en el Explorador si no está instalado)."""
    carpeta = _carpeta(PEDIDOS.get(tipo, "Paquete Antigravity"))[1]
    if not carpeta.is_dir():
        raise ValueError("Ese pedido ya no está en Descargas: vuelve a tocar el botón de Antigravity")
    return _abrir(carpeta, True)


def _carpeta(que):
    """(nombre de la producción, carpeta del paquete en Descargas). No la crea."""
    sesion = motor.produccion().get("semana") or motor.S.semana  # el nombre de la producción (también en un grupo)
    casa = Path.home()
    base = next((d for d in (casa / "Downloads", casa / "Descargas", casa / "Desktop") if d.is_dir()), casa)
    return sesion, base / re.sub(r'[<>:"/\\|?*]+', " ", f"{que} - {sesion}").strip()


def _abrir(carpeta, abrir):
    """Abre la carpeta en Antigravity (como espacio de trabajo) o, si no está instalado, en el Explorador."""
    ag = exe() if abrir else None
    try:
        if ag:
            subprocess.Popen([str(ag), str(carpeta)])
            return True
        subprocess.Popen(["explorer", str(carpeta)])
    except OSError:
        pass
    return False


def _pasar(carpeta, mensaje, modelo, abrir, titulo=None, pedidos=None):
    """Le pasa el mensaje a Gemini en Antigravity (o varios: `pedidos` = [(título, mensaje)], una conversación cada uno).
    Si Antigravity está cerrado, lo abre con la carpeta del paquete. Devuelve (abierto, enviado, por qué no se pudo enviar)."""
    if not abrir:
        return False, False, ""
    ya_estaba = bool(_servidor())
    abierto = ya_estaba or _abrir(carpeta, True)
    porque = (enviar_varios(pedidos or [(titulo or f"Sala · {motor.S.semana}", mensaje)], modelo) if abierto
              else "Antigravity no está instalado")
    if porque and ya_estaba:  # para pegarlo a mano: la carpeta del paquete a la vista
        _abrir(carpeta, True)
    return abierto, not porque, porque


# ---------- Mandarle el mensaje a Antigravity sin que nadie pegue nada (igual que gemini.py de la Sala de diseño) ----------
# agentapi pide la dirección del servidor de Antigravity, su código CSRF y un proyecto: salen del proceso abierto
# (puertos que escucha y su línea de comandos) y de la última conversación guardada.
GEMINI_DIR = Path.home() / ".gemini" / "antigravity"
_NO_EXISTE = "00000000-0000-0000-0000-000000000000"


def _sin_ventana():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def _servidor():
    """(language_server.exe, código CSRF, pid) del Antigravity abierto, o None."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='language_server.exe'\" | "
          "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20, **_sin_ventana())
        procesos = json.loads(r.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    for p in procesos if isinstance(procesos, list) else [procesos]:
        csrf = re.search(r"--csrf_token[ =](\S+)", p.get("CommandLine") or "")
        if csrf and "antigravity" in (p.get("CommandLine") or "") and p.get("ExecutablePath"):
            return p["ExecutablePath"], csrf.group(1), int(p["ProcessId"])
    return None


def _puertos(pid):
    """Los puertos de 127.0.0.1 en los que escucha ese proceso."""
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20, **_sin_ventana())
    except (OSError, subprocess.SubprocessError):
        return []
    # «0.0.0.0:0» = escuchando (la palabra LISTENING sale traducida en Windows en español: ESCUCHANDO)
    return [int(m.group(1)) for m in re.finditer(rf"127\.0\.0\.1:(\d+)\s+0\.0\.0\.0:0\s+\S+\s+{pid}\s*$", r.stdout, re.M)]


def _agentapi(srv, env, *args):
    ruta, csrf, _ = srv
    r = subprocess.run([ruta, "agentapi", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60, env={**os.environ, "ANTIGRAVITY_CSRF_TOKEN": csrf, **env}, **_sin_ventana())
    try:
        return json.loads(r.stdout or "{}")
    except ValueError:
        return {"error": (r.stdout or r.stderr or "sin respuesta").strip()[:300]}


def _proyecto(srv, env):
    """El proyecto de Antigravity de la última conversación guardada (sin proyecto, no deja abrir una)."""
    brain = GEMINI_DIR / "brain"
    if not brain.is_dir():
        return None
    ids = sorted((d for d in brain.iterdir() if d.is_dir() and re.fullmatch(r"[0-9a-f-]{36}", d.name)),
                 key=lambda d: d.stat().st_mtime, reverse=True)
    for d in ids[:5]:
        meta = _agentapi(srv, env, "get-conversation-metadata", d.name)
        pid = re.search(r'"projectId":\s*"([0-9a-f-]{36})"', json.dumps(meta))
        if pid:
            return pid.group(1)
    return None


def enviar(mensaje, modelo="flash", esperar=45, titulo=None):
    """Abre una conversación de Gemini en el Antigravity abierto con el mensaje. Devuelve "" si se pudo o por qué no."""
    return enviar_varios([(titulo or f"Sala · {motor.S.semana}", mensaje)], modelo, esperar)


def enviar_varios(pedidos, modelo="flash", esperar=45):
    """Una conversación de Gemini por cada (título, mensaje), todas a la vez (cada una trabaja en paralelo).
    Devuelve "" si se mandaron todas o por qué no."""
    fin = time.time() + esperar
    srv = _servidor()
    while not srv and time.time() < fin:  # se acaba de abrir: su servidor tarda unos segundos en arrancar
        time.sleep(3)
        srv = _servidor()
    if not srv:
        return "Antigravity no está abierto"
    try:
        env = None
        while not env and time.time() < fin:
            for puerto in _puertos(srv[2]):
                prueba = _agentapi(srv, {"ANTIGRAVITY_LS_ADDRESS": f"127.0.0.1:{puerto}"}, "get-conversation-metadata", _NO_EXISTE)
                if "connection error" not in json.dumps(prueba) and "CSRF" not in json.dumps(prueba):
                    env = {"ANTIGRAVITY_LS_ADDRESS": f"127.0.0.1:{puerto}"}
                    break
            else:
                time.sleep(3)
        if not env:
            return "no encontré el servidor de Antigravity"
        proyecto = _proyecto(srv, env)
        if not proyecto:
            return "Antigravity no tiene ningún proyecto (ábrelo una vez y empieza una conversación)"
        for titulo, mensaje in pedidos:
            r = _agentapi(srv, {**env, "ANTIGRAVITY_PROJECT_ID": proyecto}, "new-conversation", f"--model={modelo}",
                          f"--title={titulo}", mensaje)
            if not (r.get("response") or {}).get("newConversation"):
                return str(r.get("error") or "no respondió")[:200]
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)[:200]
    return ""


def exe():
    """Antigravity.exe si está instalado en esta PC."""
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        f = Path(base) / ("Programs" if base == os.environ.get("LOCALAPPDATA") else "") / "Antigravity" / "Antigravity.exe"
        if base and f.exists():
            return f
    return None


def _una_linea(t):
    return " ".join(str(t or "").split())


def pendientes(todas=False):
    """Las imágenes y clips de Flow de la sesión (las que faltan, o todas), con su prompt."""
    out = []
    for a in motor.checklist()["assets"]:
        if a.get("listo") and not todas:
            continue
        flow = a.get("flow") or {}
        if not flow.get("prompt"):
            continue
        out.append({"codigo": a["id"], "tipo": "clip" if a.get("tipo") == "clip" else "imagen", "pieza": a.get("pieza", ""),
                    "palabra": a.get("palabra", ""), "prompt": _una_linea(flow["prompt"]), "plan_b": _una_linea(flow.get("plan_b")),
                    "evitar": _una_linea(flow.get("evitar"))})
    return out


def instrucciones(sesion, carpeta, items):
    imgs = [x for x in items if x["tipo"] == "imagen"]
    clips = [x for x in items if x["tipo"] == "clip"]
    return f"""# Paquete para Antigravity · {sesion}

Hay que generar en Google Flow **{len(imgs)} imágenes**{f" y **{len(clips)} clips**" if clips else ""} y bajarlas con su nombre.
Usa el modelo **Gemini Flash** (no el que piensa más: aquí no hay nada que decidir, solo pegar y bajar).
Trabaja en una conversación nueva y no revises cada imagen: el equipo las revisa después en la sala.

Archivos de esta carpeta ({carpeta}):
- `prompts.txt`: un prompt por línea, con su código delante. Se pegan tal cual, en ese orden.
- `prompts.json`: lo mismo, con el plan B de cada prompt.
- `bajar_flow.js`: baja todas las imágenes de una vez, con su nombre.

## Pasos

1. En el navegador, abre https://labs.google/flow (la sesión de Google ya está abierta).
2. Abre o crea el proyecto **{sesion}**.
3. En los ajustes del generador (ícono de deslizadores) elige **9:16** y **x1** (una imagen por prompt). Guarda.
4. En la configuración del agente de Flow deja «Confirmar antes de generar» en **Nunca**. Después pega **todas** las líneas de
   imagen de `prompts.txt` juntas en un solo mensaje (cada una con su código, por ejemplo `C1_IMG2: …`) y envíalo: Flow
   las genera todas solo.
5. Si Google rechaza un prompt, pega en su lugar el `plan_b` de ese código (en `prompts.json`), con el mismo código delante.
6. Cuando ya no quede ninguna generándose, baja cada imagen a Descargas como `CODIGO.jpg` (puedes usar `bajar_flow.js`).
   Ojo: la galería de Flow ya no muestra el código de cada imagen; usa la descripción o el orden en que se pegaron los
   prompts para saber cuál es cuál. Si a alguna le falta, bájala a mano con ese nombre.
{f'''7. Clips en movimiento ({len(clips)}): cambia el generador a **Video** (9:16), pega cada línea de clip igual que las imágenes
   y, cuando terminen, baja cada video a Descargas con el nombre `CODIGO.mp4`.
''' if clips else ""}
Si aparece un inicio de sesión, términos o un captcha: detente y avisa a la persona. No lo hagas tú.

Al terminar, responde en 2 líneas: cuántas bajaste y cuáles fallaron.
Después, en la Sala de edición: Faltantes → «Poner en los videos» las pone en su lugar.
"""


# ---------- El Chrome que maneja Gemini ----------
# Gemini maneja el navegador por el «puerto de depuración» de Chrome (9222). Chrome no deja abrir así el perfil de
# siempre, y tampoco conviene (tiene todas las cuentas): la sala abre un Chrome aparte, con su propio perfil
# (datos/chrome_flow), solo para Flow. La primera vez hay que entrar a Flow con la cuenta que tiene los créditos;
# después la sesión queda guardada en ese perfil.
PUERTO_CHROME = 9222
PERFIL_CHROME = motor.DATOS / "chrome_flow"


def chrome_exe():
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), os.environ.get("LOCALAPPDATA", "")):
        f = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and f.exists():
            return f
    return None


def chrome_flow_abierto():
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PUERTO_CHROME}/json/version", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def abrir_chrome_flow(esperar=20):
    """Abre (si no está abierto) el Chrome de Flow con el puerto para Gemini. Devuelve "" si quedó listo o por qué no."""
    if chrome_flow_abierto():
        return ""
    ch = chrome_exe()
    if not ch:
        return "No encontré Google Chrome en esta PC: instálalo para que Gemini pueda usar Flow"
    PERFIL_CHROME.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen([str(ch), f"--remote-debugging-port={PUERTO_CHROME}", f"--user-data-dir={PERFIL_CHROME}",
                          "--no-first-run", "--no-default-browser-check", "https://labs.google/flow"])
    except OSError as e:
        return f"No pude abrir Chrome: {e}"
    fin = time.time() + esperar
    while time.time() < fin:
        if chrome_flow_abierto():
            return ""
        time.sleep(1)
    return "Chrome se abrió pero no respondió a tiempo"


def armar(todas=False, abrir=False):
    items = pendientes(todas)
    if not items:
        raise ValueError("No falta ninguna imagen ni clip de Flow en esta sesión")
    sesion, carpeta = _carpeta("Paquete Antigravity")
    carpeta.mkdir(parents=True, exist_ok=True)
    orden = sorted(items, key=lambda x: (x["tipo"] == "clip", x["codigo"]))  # primero las imágenes
    (carpeta / "prompts.txt").write_text("\n".join(f"{x['codigo']}: {x['prompt']}" for x in orden) + "\n", encoding="utf-8")
    (carpeta / "prompts.json").write_text(json.dumps(orden, ensure_ascii=False, indent=1), encoding="utf-8")
    nombres = [x["codigo"] for x in orden if x["tipo"] == "imagen"]
    (carpeta / "bajar_flow.js").write_text(BAJAR.replace("__NOMBRES__", json.dumps(nombres)), encoding="utf-8")
    texto = instrucciones(sesion, carpeta, orden)
    (carpeta / "INSTRUCCIONES.md").write_text(texto, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(texto, encoding="utf-8")  # Antigravity lee GEMINI.md solo al abrir la carpeta
    # Probado el 2026-10-27: lo que funciona es el navegador de Antigravity, que solo se activa cuando una persona
    # escribe «/browser» en el chat (un mensaje mandado desde afuera no lo activa y Gemini se pone a escribir
    # programas para manejar Chrome, lento). Por eso la sala abre Antigravity y deja el mensaje copiado con
    # «/browser» delante, para pegarlo y enviarlo.
    mensaje = (f"/browser Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo con el navegador. En Google Flow (https://labs.google/flow) "
               f"abre o crea el proyecto {sesion}, en la configuración del agente deja «Confirmar antes de generar» en Nunca, "
               "imágenes 9:16 y x1, y pega TODOS los prompts de "
               f"{carpeta / 'prompts.txt'} juntos en un solo mensaje (cada línea con su código delante, por ejemplo «C1_IMG1: …»). "
               f"Son {len(nombres)} imágenes" + (f" y {len(orden) - len(nombres)} clips" if len(orden) > len(nombres) else "")
               + ". Cuando estén todas generadas, bájalas a Descargas con su nombre (C1_IMG1.jpg…). "
               "Al final responde en 2 líneas cuántas bajaste y cuáles fallaron.")
    (carpeta / MENSAJE).write_text(mensaje, encoding="utf-8")  # para volver a verlo en la sala
    abierto = _abrir(carpeta, True) if abrir and not _servidor() else bool(_servidor())
    enviado, porque = False, ""
    return {"carpeta": str(carpeta), "imagenes": len(nombres), "clips": len(orden) - len(nombres), "mensaje": mensaje,
            "abierto": abierto, "enviado": enviado, "porque": porque, "instalado": bool(exe())}


def _limpios():
    """La sección «Prompts limpios» de las reglas de la skill (lo único que hace falta para escribir prompts de Flow)."""
    import asistente
    try:
        asistente.actualizar_conocimiento(lambda *_: None)
    except RuntimeError:
        pass
    origen = asistente.CONOCIMIENTO / ".claude" / "skills" / "suplevet-guionista-modular" / "SKILL.md"
    if not origen.exists():
        raise ValueError("Faltan las reglas para escribir los prompts (las skills de la sala, en Drive _SALA/claude). Conecta Drive y vuelve a intentar.")
    m = re.search(r"^### Prompts limpios.*?(?=^#{1,3} )", origen.read_text(encoding="utf-8"), re.S | re.M)
    texto = m.group(0) if m else ""
    return "\n".join(ln for ln in texto.splitlines() if "Música (Flow Music" not in ln)  # la música va en su propio pedido


TANDA = 3  # cuerpos por hoja, cada uno con sus hooks (~50 líneas): Flash la escribe en 2-3 minutos; cada hoja va en su
# propia conversación y todas trabajan a la vez (una hoja de 6 cuerpos, ~100 líneas, se quedó trabada sin escribir)


def _huecos(por_cuerpo, clips, por_hook):
    """Lo que falta en cada cuerpo y cada hook, con los códigos nuevos ya numerados:
    [{"pieza", "tipo", "texto", "usadas": [palabras con imagen], "ids": [(código, "imagen"|"clip")]}]."""
    prod = motor.produccion()
    piezas, assets = prod.get("piezas") or {}, prod.get("assets") or []
    tomados = {a["id"] for a in assets}
    out = []
    for f in motor.apoyo_visual()["piezas"]:
        info = piezas.get(f["pieza"]) or {}
        img = max(0, (por_cuerpo if f["tipo"] == "cuerpo" else por_hook) - f["imagenes"])
        cl = max(0, clips - f["clips"]) if f["tipo"] == "cuerpo" else 0
        ids = []
        for clase, cuantos, tipo in (("IMG", img, "imagen"), ("CLIP", cl, "clip")):
            n = 1
            while cuantos:
                cod = f"{f['pieza']}_{clase}{n}"
                if cod not in tomados:
                    ids.append((cod, tipo))
                    tomados.add(cod)
                    cuantos -= 1
                n += 1
        if ids:
            out.append({"pieza": f["pieza"], "tipo": f["tipo"], "texto": str(info.get("texto") or ""),
                        "usadas": [x["palabra"] for x in info.get("marcas", []) if x.get("asset") and x.get("palabra")], "ids": ids})
    return out


def _hoja(n, total, grupo, sesion):
    filas = [f"# Hoja {n} de {total} · {sesion}", "",
             f"Escribe RESPUESTA_{n}.jsonl con UNA línea por cada código de abajo (ni más ni menos), en este formato:", ""]
    for p in grupo:
        filas += ["", f"## {p['pieza']} · {p['tipo']}", f"Texto: «{p['texto']}»"]
        if p["usadas"]:
            filas.append("Ya tiene imagen en: " + ", ".join(f"«{u}»" for u in p["usadas"]) + " (no las repitas)")
        fotos = [c for c, t in p["ids"] if t == "imagen"]
        movs = [c for c, t in p["ids"] if t == "clip"]
        filas.append("Faltan: " + " · ".join(x for x in (
            ", ".join(fotos) + (" (fotos" + (", en las primeras palabras)" if p["tipo"] == "hook" else ", repartidas en todo el cuerpo)")) if fotos else "",
            ", ".join(movs) + " (clip en movimiento de 4 a 6 s)" if movs else "") if x))
    return "\n".join(filas[:4] + [EJEMPLO_LINEA] + filas[4:]) + "\n"


EJEMPLO_LINEA = ('{"id": "C1_IMG2", "palabra": "defensas", "prompt": "Photorealistic vertical 9:16 photo of a happy energetic '
                 'puppy playing with a ball on green grass in a sunny backyard, soft natural light, shallow depth of field.", '
                 '"plan_b": "Photorealistic vertical 9:16 shot of a playful puppy running on a lawn, warm daylight.", '
                 '"que_muestra": "Cachorro jugando con energía en el jardín"}')


def armar_prompts(por_cuerpo=3, clips=1, por_hook=0, abrir=False):
    """«Agregar los prompts» sin Claude. La sala le da a Gemini hojas cortas (TANDA cuerpos con sus hooks, con el texto y
    los códigos que faltan) y Gemini solo escribe una línea por imagen en RESPUESTA_<n>.jsonl. traer_prompts() las mete al
    guion apenas aparecen. Antes Gemini editaba el produccion.json entero (80 KB) y se iba 10 minutos explorándolo."""
    if not (motor.S.guion / "produccion.json").exists():
        raise ValueError("Esta sesión no tiene guion: primero arma el guion con lo grabado")
    huecos = _huecos(por_cuerpo, clips, por_hook)
    if not huecos:
        raise ValueError("Cada cuerpo y cada hook ya tienen esa cantidad de imágenes: sube los números para agregar más")
    limpios = _limpios()
    import asistente
    sesion, carpeta = _carpeta("Prompts Antigravity")
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)  # el pedido anterior: sus respuestas ya no sirven
    carpeta.mkdir(parents=True, exist_ok=True)
    # tandas: cada cuerpo con sus hooks (C1, C1_H1…), TANDA cuerpos por hoja
    def cuerpo_de(p):
        return p["pieza"].split("_")[0]
    orden = list(dict.fromkeys(cuerpo_de(p) for p in huecos))
    tandas = [[p for p in huecos if cuerpo_de(p) in orden[i:i + TANDA]] for i in range(0, len(orden), TANDA)]
    for n, grupo in enumerate(tandas, 1):
        (carpeta / f"HOJA_{n}.md").write_text(_hoja(n, len(tandas), grupo, sesion), encoding="utf-8")
    n_img = sum(1 for p in huecos for _, t in p["ids"] if t == "imagen")
    n_clip = sum(1 for p in huecos for _, t in p["ids"] if t == "clip")
    texto = f"""# Prompts de Flow · {sesion}

Hay {len(tandas)} hojas (HOJA_1.md … HOJA_{len(tandas)}.md) con {n_img} fotos y {n_clip} clips por escribir.
Cada conversación llena SOLO la hoja que dice su mensaje: lee HOJA_<n>.md y escribe RESPUESTA_<n>.jsonl en esta
carpeta ({carpeta}), de una sola vez, con la herramienta de escribir archivos. Las otras hojas las hacen otras
conversaciones a la vez. (Si el mensaje no dice qué hoja, hazlas todas, en orden.)

PROHIBIDO, para ir rápido: leer otros archivos, buscar en otras carpetas, escribir scripts o correr comandos.
Todo lo que necesitas está aquí y en la hoja. La sala revisa las respuestas y las pone sola en el guion.

## Cada línea de RESPUESTA_<n>.jsonl
Un JSON por línea (sin corchetes, sin comas entre líneas, sin ```):
{EJEMPLO_LINEA}
- `id`: el código de la hoja, tal cual. Uno por línea; no inventes otros.
- `palabra`: 1 a 3 palabras que aparecen tal cual en el texto de ESA pieza, en minúsculas. Ahí aparece la imagen.
  En un hook, en las primeras palabras. En un cuerpo, repartidas al principio, medio y final, en lo que más se puede ver
  (el problema, la mascota, la comida, el resultado). Nunca dos en la misma frase ni en una palabra que ya tiene imagen.
- `prompt` y `plan_b`: en inglés, fotorrealistas, «vertical 9:16». El plan B es otra escena parecida por si Flow rechaza la primera.
  En un clip, un plano corto con un movimiento simple de la mascota o de la cámara (por ejemplo «a happy dog running
  toward the camera in a sunny garden, slow motion»).
- `que_muestra`: en español, qué se ve, en pocas palabras.

## Reglas de las imágenes
- Siempre hay una mascota: cada imagen o clip muestra a un perro o gato real, nunca solo un objeto o una persona sin animal.
- La mascota muestra el estado que dice la frase (tranquila y descansando si la frase habla de un problema, activa y
  contenta si habla del resultado). Lo interno (defensas, digestión, piel, pelo) se muestra en el animal, nunca células,
  órganos, intestinos ni microscopios.
{asistente.COMIDA}
- Sin texto, sin logos ni envases con marca. Solo imágenes y clips: nada de música.

{limpios}

Al terminar responde en 1 línea cuántas líneas escribiste.
"""
    (carpeta / "INSTRUCCIONES.md").write_text(texto, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(texto, encoding="utf-8")  # Antigravity lee GEMINI.md solo al abrir la carpeta
    esperados = {c: [p["pieza"], t, p["tipo"]] for p in huecos for c, t in p["ids"]}
    (carpeta / "_sala.json").write_text(json.dumps({"semana": motor.S.semana, "esperados": esperados, "puestos": []},
                                                   ensure_ascii=False), encoding="utf-8")
    mensaje = (f"Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo tal cual: para cada HOJA_<n>.md de esa carpeta escribe su "
               "RESPUESTA_<n>.jsonl. No leas nada más, no escribas scripts ni corras comandos. "
               "Al final responde en 2 líneas cuántas líneas escribiste.")
    (carpeta / MENSAJE).write_text(mensaje, encoding="utf-8")
    # una conversación por hoja, todas a la vez: 8 hojas tardan lo que tarda una (~2-3 min) y no 20 minutos en fila.
    # El mensaje de todas juntas queda para pegarlo a mano si la sala no pudo mandarlas.
    pedidos = [(f"Prompts {sesion} · hoja {n} de {len(tandas)}",
                f"Lee {carpeta / 'INSTRUCCIONES.md'} y llena solo la hoja {n}: lee {carpeta / f'HOJA_{n}.md'} y escribe "
                f"{carpeta / f'RESPUESTA_{n}.jsonl'}. No leas nada más, no escribas scripts ni corras comandos.")
               for n in range(1, len(tandas) + 1)]
    abierto, enviado, porque = _pasar(carpeta, mensaje, "flash", abrir, pedidos=pedidos)  # tarea guiada: el rápido alcanza
    return {"carpeta": str(carpeta), "mensaje": mensaje, "abierto": abierto, "enviado": enviado, "porque": porque,
            "instalado": bool(exe()), "hojas": len(tandas), "imagenes": n_img, "clips": n_clip}


def _lineas_respuesta(carpeta):
    """Las líneas JSON de todas las RESPUESTA_*.jsonl (una mal escrita no tumba las demás)."""
    out = []
    for f in sorted(carpeta.glob("RESPUESTA_*.json*")):
        try:
            texto = f.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for ln in texto.splitlines():
            ln = ln.strip().rstrip(",")
            if not ln.startswith("{"):
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("id"):
                out.append(d)
    return out


def traer_prompts():
    """Pone en el guion de la sesión las líneas que Gemini ya escribió (aunque falten hojas): cada una como asset de
    Flow con su marca en la pieza. Lo puesto queda anotado en _sala.json para no repetirlo."""
    _, carpeta = _carpeta("Prompts Antigravity")
    meta_f = carpeta / "_sala.json"
    if not meta_f.exists():
        raise ValueError("No hay prompts de Antigravity pendientes para esta sesión: primero toca «Agregar los prompts» con Antigravity")
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    if meta.get("semana") != motor.S.semana:
        raise ValueError("Esos prompts son de otra sesión: abre esa sesión en la sala para traerlos")
    if "esperados" not in meta:
        return _traer_prompts_copia(carpeta, meta_f, meta)
    esperados, puestos = meta["esperados"], set(meta.get("puestos") or [])
    nuevas = {}
    for d in _lineas_respuesta(carpeta):
        cod = str(d["id"]).strip()
        if cod in esperados and cod not in puestos and str(d.get("prompt") or "").strip():
            nuevas[cod] = d  # si Gemini reescribió una línea, vale la última
    if not nuevas:
        raise ValueError("Gemini todavía no escribió prompts. Espera a que termine una hoja en Antigravity y vuelve a tocar el botón")
    prod_f = motor.S.guion / "produccion.json"
    prod = json.loads(prod_f.read_text(encoding="utf-8"))
    piezas, assets = prod.setdefault("piezas", {}), prod.setdefault("assets", [])
    ya = {a["id"] for a in assets}
    try:
        import respaldos
        respaldos.hacer("Antes de los prompts de Antigravity", auto=True)
    except Exception:
        pass
    puestos_ahora = 0
    for cod, d in nuevas.items():
        pieza, tipo, tipo_pieza = esperados[cod]
        info = piezas.get(pieza)
        if info is None or cod in ya:
            puestos.add(cod)
            continue
        palabra = re.sub(r"\s+", " ", str(d.get("palabra") or "")).strip(" .,;:¡!¿?«»\"").lower()
        clip = tipo == "clip"
        assets.append({"id": cod, "tipo": tipo, "pieza": pieza, "palabra": palabra, "flow": {
            "herramienta": "Google Flow · Video" if clip else "Google Flow · Imagen",
            "formato": "9:16 vertical, 4 a 6 segundos" if clip else "9:16 vertical",
            "prompt": str(d.get("prompt") or "").strip(), "plan_b": str(d.get("plan_b") or "").strip(),
            "que_muestra": str(d.get("que_muestra") or "").strip(),
            "evitar": "text, logos, watermark, deformed paws, extra legs, cartoon style" + (", fast cuts" if clip else "")}})
        marcas = info.setdefault("marcas", [])
        n_img = sum(1 for x in marcas if x.get("asset"))
        marcas.append({"palabra": palabra, "asset": cod, "modo": "completa",
                       "dur": 2.5 if clip else (1.2 if tipo_pieza == "hook" else 1.5),
                       "mov": "fijo" if clip else ("zoom_in" if n_img % 2 == 0 else "zoom_out")})
        ya.add(cod)
        puestos.add(cod)
        puestos_ahora += 1
    if puestos_ahora:
        tmp = prod_f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(prod, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, prod_f)
        motor._prod_cache = None
    faltan = len(esperados) - len(puestos & set(esperados))
    if faltan:
        meta["puestos"] = sorted(puestos)
        meta_f.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    else:
        meta_f.unlink()  # ya está todo: no se vuelve a mirar
    if not puestos_ahora:
        raise ValueError("Gemini todavía no escribió prompts nuevos")
    return {"nuevos": puestos_ahora, "faltan": faltan}


def _traer_prompts_copia(carpeta, meta_f, meta):
    """Pedidos de antes de las hojas (1.55.7 o anterior): Gemini editaba una copia del guion entero."""
    copia = carpeta / "produccion.json"
    if not copia.exists():
        raise ValueError("No hay prompts de Antigravity pendientes para esta sesión: primero toca «Agregar los prompts» con Antigravity")
    try:
        datos = json.loads(copia.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ValueError(f"El guion que dejó Gemini tiene un error de formato ({e}). Pídele en Antigravity que lo corrija y vuelve a tocar el botón")
    nuevos = len(datos.get("assets") or []) - int(meta.get("assets_antes") or 0)
    if nuevos <= 0:
        raise ValueError("Gemini todavía no agregó prompts. Espera a que termine en Antigravity y vuelve a tocar el botón")
    prod = motor.S.guion / "produccion.json"
    if prod.stat().st_mtime != meta.get("mtime"):
        raise ValueError("El guion de esta sesión cambió mientras Gemini trabajaba. Vuelve a pedir los prompts para no perder esos cambios")
    try:
        import respaldos
        respaldos.hacer("Antes de los prompts de Antigravity", auto=True)
    except Exception:
        pass
    shutil.copyfile(copia, prod)
    motor._prod_cache = None
    meta_f.unlink()
    return {"nuevos": nuevos}


# ---------- Música con Antigravity: Gemini escribe las pistas (si el guion no las trae) y las genera en Flow Music ----------
REGLAS_MUSICA = """# Reglas de la música de Suplevet

## Música
- Una pista por grupo de cuerpos con el mismo tono (2 a 4 pistas por guion). Siempre instrumental: sin voces ni letra.
- Siempre con adrenalina: enérgica, de 120 a 150 BPM, percusión marcada, bajo fuerte, tensión que empuja
  (trap, EDM, phonk, drum & bass, hip-hop enérgico). Nunca suave, acústica, cálida, relajada, lo-fi, ambiental ni «corporate».
- Prompt en inglés, sin nombres de artistas ni de canciones. En «evitar»: soft, acoustic, calm, gentle, warm, lo-fi, ambient.

## Prompts limpios
- Nada de nombres de artistas, canciones, marcas ni personas reales. Nada de palabras de violencia.
"""
def _pistas_faltan():
    return [{"id": m["id"], "uso": m.get("uso", ""), "prompt": _una_linea((m.get("prompt") or {}).get("prompt")),
             "evitar": _una_linea((m.get("prompt") or {}).get("evitar"))}
            for m in motor.checklist().get("musica", []) if not m.get("listo")]


def armar_musica(abrir=False):
    """Deja en Descargas el paquete de la música para Gemini en Antigravity: si el guion no tiene música, primero la
    escribe en una copia del guion (con las mismas reglas que Claude); después genera cada pista en Google Flow Music y
    la baja como MUS_A.mp3. La sala trae sola la copia del guion y los MP3 (vigilar_descargas)."""
    import asistente
    prod = motor.S.guion / "produccion.json"
    if not prod.exists():
        raise ValueError("Esta sesión no tiene guion: primero arma el guion con lo grabado")
    datos = json.loads(prod.read_text(encoding="utf-8"))
    sesion, carpeta = _carpeta("Musica Antigravity")
    carpeta.mkdir(parents=True, exist_ok=True)
    escribir = not datos.get("musica")
    if escribir:
        try:
            asistente.actualizar_conocimiento(lambda *_: None)
        except RuntimeError:
            pass
        origen = asistente.CONOCIMIENTO / ".claude" / "skills" / "suplevet-guionista-modular" / "SKILL.md"
        copia, reglas = carpeta / "produccion.json", carpeta / "REGLAS_MUSICA.md"
        shutil.copyfile(prod, copia)
        if origen.exists():
            shutil.copyfile(origen, reglas)
        else:  # sin las skills de Drive (_SALA/claude): lo básico de la música, que el pedido ya repite
            reglas.write_text(REGLAS_MUSICA, encoding="utf-8")
        (carpeta / "_sala.json").write_text(json.dumps({"semana": motor.S.semana}), encoding="utf-8")
        pedido = asistente.pedido_musica(copia, reglas).replace("Usa Edit.", "Edita solo el produccion.json de esta carpeta.")
        paso1 = f"""## Paso 1 · Escribir la música (sin navegador)

{pedido}

## Paso 2 · Generar las pistas en Google Flow Music

Por cada pista de `musica` del produccion.json de esta carpeta (MUS_A, MUS_B…):"""
        n = "las pistas que escribiste"
    else:
        faltan = _pistas_faltan()
        if not faltan:
            raise ValueError("Esta sesión ya tiene todas sus pistas de música")
        (carpeta / "pistas.json").write_text(json.dumps(faltan, ensure_ascii=False, indent=1), encoding="utf-8")
        for f in ("produccion.json", "_sala.json", "REGLAS_MUSICA.md"):
            (carpeta / f).unlink(missing_ok=True)  # de un pedido anterior: el guion ya tiene su música
        paso1 = f"""## Generar las pistas en Google Flow Music

El guion ya trae la música. Faltan {len(faltan)} pistas, en `pistas.json` (id, prompt y lo que hay que evitar). Por cada una:"""
        n = ", ".join(x["id"] for x in faltan)
    texto = f"""# Música para Antigravity · {sesion}

Trabaja solo con los archivos de esta carpeta ({carpeta}). Empieza una conversación nueva.

{paso1}
1. Entra a Google Flow (https://labs.google/flow) y toca el botón **«Flow Music»** de arriba (se abre flowmusic.app en
   otra pestaña). La sesión de Google ya está abierta.
2. En el cuadro «Vamos a crear…» (o «New session») pega el prompt de la pista empezando así:
   «Instrumental only, no vocals, no lyrics. <prompt de la pista>. Avoid: <evitar>». Envíalo con la flecha.
3. Cada pedido crea dos versiones en más o menos un minuto (salen de unos 3 minutos aunque se pida 60 s: está bien,
   la sala la recorta). Quédate con una que no tenga voces.
4. Bájala: menú «⋯» (More options) de esa canción → Download → **MP3**. Ojo: el menú se abre pegado a la fila de abajo;
   revisa el nombre de la canción antes de elegir.
5. En Descargas, cámbiale el nombre al archivo por el id de la pista: `MUS_A.mp3`, `MUS_B.mp3`… (con la terminal si hace falta).
   La sala lo toma sola de Descargas.

Si aparece un inicio de sesión, términos, un pago o un captcha: detente y avisa a la persona. No lo hagas tú.

Al terminar, responde en 2 líneas: qué pistas bajaste y cuáles fallaron.
"""
    (carpeta / "INSTRUCCIONES.md").write_text(texto, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(texto, encoding="utf-8")
    # Igual que con las imágenes: el navegador de Antigravity solo se activa si una persona escribe «/browser».
    mensaje = (f"/browser Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo tal cual"
               + (": primero agrega la música editando el produccion.json de esa carpeta y después genera cada pista"
                  if escribir else f": genera las pistas {n}")
               + " en Google Flow Music (botón «Flow Music» en https://labs.google/flow), instrumental, y baja cada una "
               "a Descargas como MP3 con el nombre de su id (MUS_A.mp3…). Al final responde en 2 líneas qué pistas bajaste "
               "y cuáles fallaron.")
    (carpeta / MENSAJE).write_text(mensaje, encoding="utf-8")  # para volver a verlo en la sala
    abierto = _abrir(carpeta, True) if abrir and not _servidor() else bool(_servidor())
    return {"carpeta": str(carpeta), "escribir": escribir, "pistas": n, "mensaje": mensaje, "abierto": abierto,
            "enviado": False, "porque": "", "instalado": bool(exe())}


def traer_musica():
    """Pasa al guion de la sesión la música que escribió Gemini en la copia (solo `musica`, `musica_por_defecto` y la
    pista de cada pieza: lo demás del guion no se toca). None si no hay nada que traer."""
    _, carpeta = _carpeta("Musica Antigravity")
    meta_f, copia = carpeta / "_sala.json", carpeta / "produccion.json"
    if not meta_f.exists() or not copia.exists():
        return None
    try:
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        datos = json.loads(copia.read_text(encoding="utf-8"))
    except ValueError:
        return None  # Gemini todavía lo está escribiendo
    pistas = [m for m in datos.get("musica") or [] if isinstance(m, dict) and m.get("id")]
    if meta.get("semana") != motor.S.semana or not pistas:
        return None
    prod = motor.S.guion / "produccion.json"
    actual = json.loads(prod.read_text(encoding="utf-8"))
    if actual.get("musica"):
        meta_f.unlink()
        return None
    try:
        import respaldos
        respaldos.hacer("Antes de la música de Antigravity", auto=True)
    except Exception:
        pass
    ids = {m["id"] for m in pistas}
    actual["musica"] = pistas
    defecto = datos.get("musica_por_defecto")
    actual["musica_por_defecto"] = defecto if defecto in ids else pistas[0]["id"]
    for nombre, info in (datos.get("piezas") or {}).items():
        if isinstance(info, dict) and info.get("musica") in ids and nombre in actual.get("piezas", {}):
            actual["piezas"][nombre]["musica"] = info["musica"]
    prod.write_text(json.dumps(actual, ensure_ascii=False, indent=1), encoding="utf-8")
    motor._prod_cache = None
    meta_f.unlink()
    return {"pistas": sorted(ids)}
