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
import perfil

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


def cancelar_pedido(tipo):
    """Deja de esperar un pedido a Gemini: borra su carpeta en Descargas (con su mensaje, sus hojas y su FALTAN.txt),
    así no sale como «en curso» y la sala no trae nada de él. Lo que ya entró a la sesión se queda."""
    if tipo not in PEDIDOS:
        raise ValueError("Ese pedido no existe")
    carpeta = _carpeta(PEDIDOS[tipo])[1]
    if carpeta.is_dir():
        shutil.rmtree(carpeta, ignore_errors=True)
    return not carpeta.exists()


def abrir_pedido(tipo):
    """Abre Antigravity con la carpeta del pedido (o la carpeta en el Explorador si no está instalado)."""
    carpeta = _carpeta_plantilla() if tipo == "plantilla" else _carpeta(PEDIDOS.get(tipo, "Paquete Antigravity"))[1]
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


TANDA_IMG, TANDA_CLIP = 10, 5  # prompts por mensaje a Flow: imágenes y clips en movimiento (los clips tardan más)
FALTAN = "FALTAN.txt"  # lo que todavía no llegó a la sesión: la sala lo reescribe cada vez que revisa Descargas


def anotar_faltan(ids, que="Paquete Antigravity", esperando=""):
    """Escribe en la carpeta del pedido (imágenes o música) qué códigos todavía no llegaron a la sesión. Gemini lo usa
    para saber qué bajar: la sala se lleva cada archivo de Descargas apenas llega, así que mirar Descargas no sirve (con
    P05, Gemini pasó 27 minutos volviendo a bajar fotos que la sala ya había tomado). `esperando`: la sala todavía no
    puede saber qué falta (la música que Gemini escribe aún no llegó al guion)."""
    carpeta = _carpeta(que)[1]
    if not (carpeta / "INSTRUCCIONES.md").exists():
        return
    lo = "toda la música" if que == PEDIDOS["musica"] else "todas las imágenes y clips"
    texto = (esperando + "\n" if esperando else
             f"No falta nada: la sala ya tiene {lo}. Terminaste.\n" if not ids else
             f"Faltan {len(ids)} (la sala revisa Descargas cada 20 s y actualiza esta lista):\n" + "\n".join(sorted(ids)) + "\n")
    try:
        f = carpeta / FALTAN
        if not f.exists() or f.read_text(encoding="utf-8") != texto:
            f.write_text(texto, encoding="utf-8")
    except OSError:
        pass


def _tandas_flow(orden):
    """[("TANDA_01", [items])…]: primero las imágenes de 10 en 10, después los clips de 5 en 5."""
    out = []
    for tipo, n in (("imagen", TANDA_IMG), ("clip", TANDA_CLIP)):
        xs = [x for x in orden if x["tipo"] == tipo]
        out += [xs[i:i + n] for i in range(0, len(xs), n)]
    return [(f"TANDA_{k:02d}", g) for k, g in enumerate(out, 1)]


def _orden_flow(grupo):
    """La primera línea de cada tanda: la orden para el asistente de Flow. Sin ella, al pegarle solo la lista, Flow
    responde «¿cómo quieres trabajar con esto?» y no genera nada (pasó con P05 el 2026-10-28)."""
    n = len(grupo)
    if grupo[0]["tipo"] == "clip":
        return (f"Generate these {n} videos now: one video per line, vertical 9:16, 4 to 6 seconds each. Do not ask anything "
                "and do not suggest options: start generating right away. Each line starts with its code (like C1_CLIP1): "
                "use that code as the name of its video.")
    return (f"Generate these {n} images now: one image per line, vertical 9:16, exactly 1 image per line. Do not ask anything "
            "and do not suggest options: start generating right away. Each line starts with its code (like C1_IMG2): "
            "use that code as the name of its image.")


def instrucciones(sesion, carpeta, items, tandas=None):
    imgs = [x for x in items if x["tipo"] == "imagen"]
    clips = [x for x in items if x["tipo"] == "clip"]
    tandas = tandas or _tandas_flow(items)
    t_img = [n for n, g in tandas if g[0]["tipo"] == "imagen"]
    t_clip = [n for n, g in tandas if g[0]["tipo"] == "clip"]
    return f"""# Paquete para Antigravity · {sesion}

Hay que generar en Google Flow **{len(imgs)} imágenes**{f" y **{len(clips)} clips**" if clips else ""} y bajarlas con su nombre.
Usa el modelo **Gemini Flash** (no el que piensa más: aquí no hay nada que decidir, solo pegar y bajar).
Trabaja en una conversación nueva y no revises cada imagen: el equipo las revisa después en la sala.

Archivos de esta carpeta ({carpeta}):
- `TANDA_01.txt`, `TANDA_02.txt`…: los prompts de a {TANDA_IMG} (los clips de a {TANDA_CLIP}), uno por línea con su código delante.
  Se pegan tal cual, una tanda por mensaje, en orden.
- `bajar_tanda_01.js`, `bajar_tanda_02.js`…: bajan las imágenes de esa tanda, con su nombre.
- `prompts.json`: todos los prompts con el plan B de cada uno (si Google rechaza alguno).

## Pasos

1. En el navegador, abre https://labs.google/flow (la sesión de Google ya está abierta).
2. Abre o crea el proyecto **{sesion}**.
3. En los ajustes del generador (ícono de deslizadores) elige **9:16** y **x1** (una imagen por prompt). Guarda.
4. En la configuración del agente de Flow deja «Confirmar antes de generar» en **Nunca**.
5. **Por tandas, nunca todo junto** ({len(t_img)} tandas de imágenes: {t_img[0] if t_img else ""} … {t_img[-1] if t_img else ""}). Para cada tanda, en orden:
   a. Pega `TANDA_XX.txt` COMPLETO en un solo mensaje al asistente de Flow y envíalo. Su primera línea es la orden
      («Generate these 10 images now…»): nunca la quites. Debajo van los prompts, cada uno con su código (`C1_IMG2: …`).
   b. Comprueba que Flow empezó a generar (aparecen tarjetas cargando). Si el asistente responde con preguntas, un menú de
      opciones («How would you like to work with these?») o «I can help you…», NO elijas opciones: respóndele
      «Generate all of them now, one image per line, 9:16, 1 image each.» y espera a que arranque.
      Espera a que terminen de generarse esas imágenes.
   c. Bájalas a Descargas como `CODIGO.jpg` corriendo `bajar_tanda_XX.js` en la pestaña de Flow (si a alguna le falta,
      bájala a mano con ese nombre; la galería no muestra el código: usa el orden en que se pegaron).
   d. Recién ahí sigue con la tanda siguiente. La sala pone sola en los videos lo que va llegando a Descargas.
   e. **Para saber qué falta, NO mires Descargas:** la sala se lleva cada archivo apenas llega, así que ahí parece que
   falta todo. Lee `FALTAN.txt` en esta carpeta (la sala lo actualiza cada 20 segundos) y baja solo esos códigos.
   Cuando diga «No falta nada», terminaste. No vuelvas a bajar nada que no esté en `FALTAN.txt`.
6. Si Google rechaza un prompt, pega en su lugar el `plan_b` de ese código (en `prompts.json`), con el mismo código delante.
{f'''7. Clips en movimiento ({len(clips)}, {", ".join(t_clip)}): cambia el generador a **Video** (9:16) y haz lo mismo, de {TANDA_CLIP} en {TANDA_CLIP}.
   Cuando terminen, baja cada video a Descargas con el nombre `CODIGO.mp4`.
''' if clips else ""}
Si aparece un inicio de sesión, términos o un captcha: detente y avisa a la persona. No lo hagas tú.

Al terminar (cuando `FALTAN.txt` diga «No falta nada»), responde en 2 líneas: cuántas bajaste y cuáles fallaron.
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
    # por tandas: Flow no aguanta cientos de prompts de golpe (se traba y no se sabe cuál imagen es cuál). Cada tanda
    # se pega, se genera y se baja antes de la siguiente; lo bajado entra solo a la sesión mientras sigue la otra.
    for f in carpeta.glob("TANDA_*"):
        f.unlink()
    for f in carpeta.glob("bajar_tanda_*.js"):
        f.unlink()
    tandas = _tandas_flow(orden)
    for nombre, grupo in tandas:
        (carpeta / f"{nombre}.txt").write_text(_orden_flow(grupo) + "\n" + "\n".join(f"{x['codigo']}: {x['prompt']}" for x in grupo) + "\n",
                                               encoding="utf-8")
        if grupo[0]["tipo"] == "imagen":
            (carpeta / f"bajar_{nombre.lower()}.js").write_text(BAJAR.replace("__NOMBRES__", json.dumps([x["codigo"] for x in grupo])),
                                                                encoding="utf-8")
    texto = instrucciones(sesion, carpeta, orden, tandas)
    (carpeta / "INSTRUCCIONES.md").write_text(texto, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(texto, encoding="utf-8")  # Antigravity lee GEMINI.md solo al abrir la carpeta
    # Probado el 2026-10-27: lo que funciona es el navegador de Antigravity, que solo se activa cuando una persona
    # elige «browser» en la lista que sale al escribir «/» (un mensaje mandado desde afuera no lo activa, ni
    # «/browser» escrito o pegado como texto: Gemini se pone a escribir programas para manejar Chrome, lento). Por eso
    # la sala abre Antigravity y deja el mensaje copiado SIN «/browser»: la persona elige «browser» y lo pega detrás.
    mensaje = (f"Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo con el navegador. En Google Flow (https://labs.google/flow) "
               f"abre o crea el proyecto {sesion}, en la configuración del agente deja «Confirmar antes de generar» en Nunca, "
               f"imágenes 9:16 y x1. Pega los prompts POR TANDAS, nunca todos juntos: TANDA_01.txt completo en un solo "
               "mensaje (su primera línea es la orden «Generate these 10 images now…», no la quites; si Flow responde con "
               "preguntas u opciones en vez de generar, contéstale «Generate all of them now»), espera a que se generen, bájalas a "
               "Descargas con su nombre (C1_IMG2.jpg…) corriendo bajar_tanda_01.js, y recién ahí sigue con TANDA_02.txt, y así. "
               f"Son {len(nombres)} imágenes en {sum(1 for _, g in tandas if g[0]['tipo'] == 'imagen')} tandas"
               + (f" y {len(orden) - len(nombres)} clips (con el generador en Video, de {TANDA_CLIP} en {TANDA_CLIP}, se bajan como .mp4)"
                  if len(orden) > len(nombres) else "")
               + ". Para saber qué falta NO mires Descargas (la sala se lleva cada archivo apenas llega): lee FALTAN.txt de "
               "esa carpeta y baja solo esos; cuando diga «No falta nada», terminaste. "
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
    origen = asistente.ruta_skill()
    if not origen.exists():
        raise ValueError("Faltan las reglas para escribir los prompts (las skills de la sala, en Drive _SALA/claude). Conecta Drive y vuelve a intentar.")
    m = re.search(r"^### Prompts limpios.*?(?=^#{1,3} )", origen.read_text(encoding="utf-8"), re.S | re.M)
    texto = m.group(0) if m else ""
    return "\n".join(ln for ln in texto.splitlines() if "Música (Flow Music" not in ln)  # la música va en su propio pedido


TANDA = 3  # cuerpos por hoja, cada uno con sus hooks (~50 líneas): Flash la escribe en 2-3 minutos; cada hoja va en su
# propia conversación y todas trabajan a la vez (una hoja de 6 cuerpos, ~100 líneas, se quedó trabada sin escribir)


def _grupo_de(pieza):
    """El cuerpo al que pertenece una pieza (C1_H3 → C1, C1_CTA → C1); un CTA suelto va aparte."""
    m = re.match(r"(C\d+)", pieza)
    return m.group(1) if m else pieza


def _huecos(por_cuerpo, clips, por_hook):
    """Lo que le falta a cada hook, cuerpo y CTA para estar completo: imágenes y clips (con los códigos nuevos ya
    numerados), su estado de ánimo (para la música) y, en un cuerpo, el golpe de silencio.
    [{"pieza", "tipo", "texto", "usadas", "ids": [(código, "imagen"|"clip")], "animo": bool, "sfx": bool}]."""
    prod = motor.produccion()
    piezas, assets = prod.get("piezas") or {}, prod.get("assets") or []
    tomados = {a["id"] for a in assets}
    fotos = {f["pieza"]: f for f in motor.apoyo_visual()["piezas"]}
    out = []
    for n in motor._orden_piezas(prod):
        info = piezas.get(n) or {}
        tipo = "cuerpo" if info.get("tipo") == "cuerpo" else motor.tipo_de(n)
        if tipo not in ("cuerpo", "hook", "cta"):
            continue
        f = fotos.get(n)
        img = max(0, (por_cuerpo if tipo == "cuerpo" else por_hook) - f["imagenes"]) if f else 0
        cl = max(0, clips - f["clips"]) if f and tipo == "cuerpo" else 0
        ids = []
        for clase, cuantos, t in (("IMG", img, "imagen"), ("CLIP", cl, "clip")):
            k = 1
            while cuantos:
                cod = f"{n}_{clase}{k}"
                if cod not in tomados:
                    ids.append((cod, t))
                    tomados.add(cod)
                    cuantos -= 1
                k += 1
        animo = info.get("animo") not in motor.ANIMOS
        sfx = tipo == "cuerpo" and not any(m.get("sfx") == "corte_silencio" for m in info.get("marcas", []))
        if ids or animo or sfx:
            out.append({"pieza": n, "tipo": tipo, "texto": str(info.get("texto") or ""), "ids": ids, "animo": animo, "sfx": sfx,
                        "usadas": [x["palabra"] for x in info.get("marcas", []) if x.get("asset") and x.get("palabra")]})
    return out


def _hoja(n, total, grupo, sesion):
    filas = [f"# Hoja {n} de {total} · {sesion}", "",
             f"Escribe RESPUESTA_{n}.jsonl con UNA línea por cada código de abajo (ni más ni menos). Ejemplos de cada tipo:",
             "", ejemplo_linea(), EJEMPLO_ANIMO, ejemplo_sfx()]
    for p in grupo:
        filas += ["", f"## {p['pieza']} · {p['tipo']}", f"Texto: «{p['texto']}»"]
        if p["animo"]:
            filas.append(f"Ánimo: ANIMO_{p['pieza']} (uno de: {', '.join(motor.ANIMOS)})")
        if p["usadas"]:
            filas.append("Ya tiene imagen en: " + ", ".join(f"«{u}»" for u in p["usadas"]) + " (no las repitas)")
        fotos = [c for c, t in p["ids"] if t == "imagen"]
        movs = [c for c, t in p["ids"] if t == "clip"]
        if fotos or movs:
            filas.append("Imágenes: " + " · ".join(x for x in (
                ", ".join(fotos) + (" (fotos" + (", en las primeras palabras)" if p["tipo"] == "hook" else ", repartidas en todo el cuerpo)")) if fotos else "",
                ", ".join(movs) + " (clip en movimiento de 4 a 6 s)" if movs else "") if x))
        if p["sfx"]:
            filas.append(f"Golpe de silencio: SFX_{p['pieza']} (la palabra de la frase clave)")
    return "\n".join(filas) + "\n"


def ejemplo_linea():
    """Una línea de imagen de ejemplo, con la escena de ejemplo del perfil de la marca."""
    e = perfil.leer()["imagenes"]["ejemplo"]
    return json.dumps({"id": "C1_IMG2", "palabra": e.get("palabra") or "resultado", "prompt": e.get("prompt", ""),
                       "plan_b": e.get("plan_b", ""), "que_muestra": e.get("que_muestra", "")}, ensure_ascii=False)


EJEMPLO_ANIMO = '{"id": "ANIMO_C1", "animo": "tension"}'


def ejemplo_sfx():
    return json.dumps({"id": "SFX_C1", "palabra": perfil.ejemplo_marca()}, ensure_ascii=False)


def armar_prompts(por_cuerpo=3, clips=1, por_hook=0, abrir=False):
    """«Completar el guion» sin Claude: lo que le falta a cada pieza para que el video salga completo, en un solo pedido.
    La sala le da a Gemini hojas cortas (TANDA cuerpos con sus hooks y su CTA, con el texto y lo que falta) y Gemini
    escribe una línea por cosa en RESPUESTA_<n>.jsonl: imágenes y clips de Flow, el estado de ánimo de cada pieza (de
    ahí sale la música: una pista por ánimo, ver motor.ANIMOS) y el golpe de silencio de cada cuerpo. traer_prompts()
    las mete al guion apenas aparecen. Antes Gemini editaba el produccion.json entero (80 KB) y se iba 10 minutos
    explorándolo, y la música iba en otro pedido."""
    if not (motor.S.guion / "produccion.json").exists():
        raise ValueError("Esta sesión no tiene guion: primero arma el guion con lo grabado")
    huecos = _huecos(por_cuerpo, clips, por_hook)
    if not huecos:
        raise ValueError("El guion ya está completo: cada pieza tiene sus imágenes, su ánimo y su sonido. Sube los números para agregar más imágenes")
    limpios = _limpios()
    import asistente
    sesion, carpeta = _carpeta("Prompts Antigravity")
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)  # el pedido anterior: sus respuestas ya no sirven
    carpeta.mkdir(parents=True, exist_ok=True)
    orden = list(dict.fromkeys(_grupo_de(p["pieza"]) for p in huecos))
    tandas = [[p for p in huecos if _grupo_de(p["pieza"]) in orden[i:i + TANDA]] for i in range(0, len(orden), TANDA)]
    for n, grupo in enumerate(tandas, 1):
        (carpeta / f"HOJA_{n}.md").write_text(_hoja(n, len(tandas), grupo, sesion), encoding="utf-8")
    n_img = sum(1 for p in huecos for _, t in p["ids"] if t == "imagen")
    n_clip = sum(1 for p in huecos for _, t in p["ids"] if t == "clip")
    n_animo = sum(1 for p in huecos if p["animo"])
    animos = "\n".join(f"  - `{k}`: {v[0]}" for k, v in motor.ANIMOS.items())
    texto = f"""# Completar el guion · {sesion}

Hay {len(tandas)} hojas (HOJA_1.md … HOJA_{len(tandas)}.md): {n_img} fotos y {n_clip} clips de Flow por escribir y
{n_animo} piezas sin estado de ánimo. Cada conversación llena SOLO la hoja que dice su mensaje: lee HOJA_<n>.md y escribe
RESPUESTA_<n>.jsonl en esta carpeta ({carpeta}), de una sola vez, con la herramienta de escribir archivos. Las otras hojas
las hacen otras conversaciones a la vez. (Si el mensaje no dice qué hoja, hazlas todas, en orden.)

PROHIBIDO, para ir rápido: leer otros archivos, buscar en otras carpetas, escribir scripts o correr comandos.
Todo lo que necesitas está aquí y en la hoja. La sala revisa las respuestas y las pone sola en el guion.

## Cada línea de RESPUESTA_<n>.jsonl
Un JSON por línea (sin corchetes, sin comas entre líneas, sin ```). Hay tres tipos:

**Imagen o clip** (códigos `…_IMG<n>` y `…_CLIP<n>`):
{ejemplo_linea()}
- `palabra`: 1 a 3 palabras que aparecen tal cual en el texto de ESA pieza, en minúsculas. Ahí aparece la imagen.
  En un hook, en las primeras palabras. En un cuerpo, repartidas al principio, medio y final, en lo que más se puede ver
  ({perfil.leer()['imagenes']['que_mostrar']}). Nunca dos en la misma frase ni en una palabra que ya tiene imagen.
- `prompt` y `plan_b`: en inglés, fotorrealistas, «vertical 9:16». El plan B es otra escena parecida por si Flow rechaza la primera.
  En un clip, un plano corto con un movimiento simple de lo que se ve o de la cámara (por ejemplo
  «{perfil.leer()['imagenes']['ejemplo_clip']}»).
- `que_muestra`: en español, qué se ve, en pocas palabras.

**Estado de ánimo** (códigos `ANIMO_<pieza>`): de él sale la música. Cada video junta un hook, un cuerpo y un CTA, y la
música cambia solo donde cambia el ánimo; por eso elige el que domina en ESA pieza, uno de:
{animos}
{EJEMPLO_ANIMO}
Guía: un hook casi siempre es `tension` (un problema) o `revelacion` (un dato, una pregunta); un cuerpo, el ánimo que más
dura en su texto (si cuenta un problema y la mayor parte es la solución, `solucion`); un CTA, `urgencia` o `solucion`.

**Golpe de silencio** (códigos `SFX_<cuerpo>`): la música se corta medio segundo antes de esa palabra y vuelve con un
golpe. Uno por cuerpo, en la frase clave (normalmente donde entra {perfil.leer()['nombre'] or 'el producto'} o el resultado). `palabra` va tal cual en el texto.
{ejemplo_sfx()}

## Reglas de las imágenes (las de la marca)
{perfil.reglas_imagenes()}

{limpios}

Al terminar responde en 1 línea cuántas líneas escribiste.
"""
    (carpeta / "INSTRUCCIONES.md").write_text(texto, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(texto, encoding="utf-8")  # Antigravity lee GEMINI.md solo al abrir la carpeta
    esperados = {c: [p["pieza"], t, p["tipo"]] for p in huecos for c, t in p["ids"]}
    esperados.update({f"ANIMO_{p['pieza']}": [p["pieza"], "animo", p["tipo"]] for p in huecos if p["animo"]})
    opcionales = {f"SFX_{p['pieza']}": [p["pieza"], "sfx", p["tipo"]] for p in huecos if p["sfx"]}
    (carpeta / "_sala.json").write_text(json.dumps({"semana": motor.S.semana, "esperados": esperados, "opcionales": opcionales,
                                                    "puestos": []}, ensure_ascii=False), encoding="utf-8")
    mensaje = (f"Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo tal cual: para cada HOJA_<n>.md de esa carpeta escribe su "
               "RESPUESTA_<n>.jsonl. No leas nada más, no escribas scripts ni corras comandos. "
               "Al final responde en 2 líneas cuántas líneas escribiste.")
    (carpeta / MENSAJE).write_text(mensaje, encoding="utf-8")
    # una conversación por hoja, todas a la vez: 8 hojas tardan lo que tarda una (~2-3 min) y no 20 minutos en fila.
    # El mensaje de todas juntas queda para pegarlo a mano si la sala no pudo mandarlas.
    pedidos = [(f"Completar guion {sesion} · hoja {n} de {len(tandas)}",
                f"Lee {carpeta / 'INSTRUCCIONES.md'} y llena solo la hoja {n}: lee {carpeta / f'HOJA_{n}.md'} y escribe "
                f"{carpeta / f'RESPUESTA_{n}.jsonl'}. No leas nada más, no escribas scripts ni corras comandos.")
               for n in range(1, len(tandas) + 1)]
    abierto, enviado, porque = _pasar(carpeta, mensaje, "flash", abrir, pedidos=pedidos)  # tarea guiada: el rápido alcanza
    return {"carpeta": str(carpeta), "mensaje": mensaje, "abierto": abierto, "enviado": enviado, "porque": porque,
            "instalado": bool(exe()), "hojas": len(tandas), "imagenes": n_img, "clips": n_clip, "animos": n_animo}


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


def _limpiar_palabra(p):
    return re.sub(r"\s+", " ", str(p or "")).strip(" .,;:¡!¿?«»\"").lower()


def ordenar_musica_por_animo(prod):
    """Con los ánimos puestos, la música del guion queda una pista por ánimo: crea en `musica` la de cada ánimo usado,
    quita la pista a mano de una pieza si esa pista todavía no se generó (manda su ánimo) y borra de la lista las
    pistas sin archivo que ya nadie usa. Lo que ya se generó (tiene su MP3) se respeta. Devuelve si cambió algo."""
    piezas, lista = prod.get("piezas") or {}, prod.setdefault("musica", [])
    if not any(p.get("animo") in motor.ANIMOS for p in piezas.values()):
        return False
    antes = json.dumps([lista, {n: p.get("musica") for n, p in piezas.items()}, prod.get("musica_por_defecto")], sort_keys=True)
    tiene_mp3 = {m["id"]: bool(motor.buscar_musica(m["id"])) for m in lista}
    for p in piezas.values():
        if p.get("animo") in motor.ANIMOS and p.get("musica") and not tiene_mp3.get(p["musica"]):
            del p["musica"]
    # solo los ánimos que de verdad suenan: una pieza con su pista ya generada (a mano) no necesita la de su ánimo
    usados = {p["animo"] for p in piezas.values() if p.get("animo") in motor.ANIMOS and not p.get("musica")}
    hay = {m.get("animo") for m in lista}
    lista += [motor.pista_para_animo(a) for a in motor.ANIMOS if a in usados and a not in hay]
    tiene_mp3.update({m["id"]: bool(motor.buscar_musica(m["id"])) for m in lista if m["id"] not in tiene_mp3})
    en_uso = {p.get("musica") for p in piezas.values()} | {m.get("musica") for p in piezas.values() for m in p.get("marcas", [])}
    en_uso |= {v.get("musica") for v in (prod.get("videos") or {}).values() if isinstance(v, dict)}
    prod["musica"] = [m for m in lista if tiene_mp3.get(m["id"]) or m["id"] in en_uso or m.get("animo") in usados]
    cuerpos = [p["animo"] for p in piezas.values() if p.get("tipo") == "cuerpo" and p.get("animo") in motor.ANIMOS]
    if cuerpos:
        mas = max(set(cuerpos), key=cuerpos.count)
        prod["musica_por_defecto"] = next(m["id"] for m in prod["musica"] if m.get("animo") == mas)
    return antes != json.dumps([prod["musica"], {n: p.get("musica") for n, p in piezas.items()}, prod.get("musica_por_defecto")], sort_keys=True)


def traer_prompts():
    """Pone en el guion de la sesión las líneas que Gemini ya escribió (aunque falten hojas): cada imagen o clip como
    asset de Flow con su marca en la pieza, cada ánimo en su pieza (y la música queda una pista por ánimo) y cada golpe
    de silencio como marca. Lo puesto queda anotado en _sala.json para no repetirlo."""
    _, carpeta = _carpeta("Prompts Antigravity")
    meta_f = carpeta / "_sala.json"
    if not meta_f.exists():
        raise ValueError("No hay nada de Antigravity pendiente para esta sesión: primero toca «Completar el guion» con Antigravity")
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    if meta.get("semana") != motor.S.semana:
        raise ValueError("Ese pedido es de otra sesión: abre esa sesión en la sala para traerlo")
    if "esperados" not in meta:
        return _traer_prompts_copia(carpeta, meta_f, meta)
    esperados, opcionales, puestos = meta["esperados"], meta.get("opcionales") or {}, set(meta.get("puestos") or [])
    todos = {**opcionales, **esperados}
    nuevas = {}
    for d in _lineas_respuesta(carpeta):
        cod = str(d["id"]).strip()
        if cod not in todos or cod in puestos:
            continue
        clase = todos[cod][1]
        vale = (str(d.get("animo") or "").strip().lower() in motor.ANIMOS if clase == "animo"
                else bool(_limpiar_palabra(d.get("palabra"))) if clase == "sfx" else bool(str(d.get("prompt") or "").strip()))
        if vale:
            nuevas[cod] = d  # si Gemini reescribió una línea, vale la última
    if not nuevas:
        raise ValueError("Gemini todavía no escribió nada. Espera a que termine una hoja en Antigravity y vuelve a tocar el botón")
    prod_f = motor.S.guion / "produccion.json"
    prod = json.loads(prod_f.read_text(encoding="utf-8"))
    piezas, assets = prod.setdefault("piezas", {}), prod.setdefault("assets", [])
    ya = {a["id"] for a in assets}
    try:
        import respaldos
        respaldos.hacer("Antes de completar el guion con Antigravity", auto=True)
    except Exception:
        pass
    puestos_ahora, animos_ahora = 0, 0
    for cod, d in nuevas.items():
        pieza, clase, tipo_pieza = todos[cod]
        info = piezas.get(pieza)
        puestos.add(cod)
        if info is None:
            continue
        if clase == "animo":
            info["animo"] = str(d["animo"]).strip().lower()
            animos_ahora += 1
        elif clase == "sfx":
            if not any(m.get("sfx") == "corte_silencio" for m in info.get("marcas", [])):
                info.setdefault("marcas", []).append({"palabra": _limpiar_palabra(d.get("palabra")), "sfx": "corte_silencio"})
        elif cod not in ya:
            palabra = _limpiar_palabra(d.get("palabra"))
            clip = clase == "clip"
            assets.append({"id": cod, "tipo": clase, "pieza": pieza, "palabra": palabra, "flow": {
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
        puestos_ahora += 1
    if animos_ahora:
        ordenar_musica_por_animo(prod)
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
        raise ValueError("Gemini todavía no escribió nada nuevo")
    return {"nuevos": puestos_ahora, "faltan": faltan, "animos": animos_ahora}


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
def reglas_musica_basicas():
    """Lo básico de la música cuando el equipo no tiene skills propias en Drive (_SALA/claude)."""
    return f"""# Reglas de la música

## Música
- Una pista por grupo de cuerpos con el mismo tono (2 a 4 pistas por guion). Siempre instrumental: sin voces ni letra.
- Prompt en inglés, sin nombres de artistas ni de canciones.
{perfil.reglas_musica()}
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
    # con los ánimos ya puestos («Completar el guion»), la música es una pista por ánimo: la sala la escribe sola y
    # Gemini solo la genera (no hace falta que la invente pieza por pieza)
    if ordenar_musica_por_animo(datos):
        tmp = prod.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, prod)
        motor._prod_cache = None
    sesion, carpeta = _carpeta("Musica Antigravity")
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)  # un pedido viejo no debe volver a meter su música al guion
    carpeta.mkdir(parents=True, exist_ok=True)
    escribir = not datos.get("musica")
    if escribir:
        try:
            asistente.actualizar_conocimiento(lambda *_: None)
        except RuntimeError:
            pass
        origen = asistente.ruta_skill()
        copia, reglas = carpeta / "produccion.json", carpeta / "REGLAS_MUSICA.md"
        shutil.copyfile(prod, copia)
        if origen.exists():
            shutil.copyfile(origen, reglas)
        else:  # sin las skills de Drive (_SALA/claude): lo básico de la música, que el pedido ya repite
            reglas.write_text(reglas_musica_basicas(), encoding="utf-8")
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
5. **Apenas se baje, ANTES de pedir la pista siguiente**, cámbiale el nombre en Descargas por el id de la pista: `MUS_A.mp3`,
   `MUS_B.mp3`… (con la terminal si hace falta). Flow Music la baja con su propio título («Tense Shadow Drive.mp3») y con
   ese nombre la sala no sabe qué pista es: no dejes los nombres para el final. Ya renombrada, la sala la toma sola y la
   pasa a la carpeta del proyecto en segundos: por eso después ya no está en Descargas.
6. **Para saber qué falta, NO mires Descargas:** lee `FALTAN.txt` en esta carpeta (la sala lo actualiza cada 20 segundos)
   y haz solo esas pistas. Cuando diga «No falta nada», terminaste. No vuelvas a bajar una pista que no esté en la lista.

Si aparece un inicio de sesión, términos, un pago o un captcha: detente y avisa a la persona. No lo hagas tú.

Al terminar (cuando `FALTAN.txt` diga «No falta nada»), responde en 2 líneas: qué pistas bajaste y cuáles fallaron.
"""
    (carpeta / "INSTRUCCIONES.md").write_text(texto, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(texto, encoding="utf-8")
    # Igual que con las imágenes: el navegador solo se activa si una persona elige «browser» en la lista del «/».
    mensaje = (f"Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo tal cual"
               + (": primero agrega la música editando el produccion.json de esa carpeta y después genera cada pista"
                  if escribir else f": genera las pistas {n}")
               + " en Google Flow Music (botón «Flow Music» en https://labs.google/flow), instrumental, y baja cada una "
               "a Descargas como MP3 y, apenas se baje y antes de pedir la siguiente, renómbrala con su id (MUS_A.mp3…). "
               "La sala se lleva cada MP3 de Descargas apenas tiene su nombre: "
               "para saber qué falta lee FALTAN.txt de esa carpeta, no Descargas. Al final responde en 2 líneas qué pistas bajaste "
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


# ---------- El guion con Gemini (para los equipos que trabajan sin Claude) ----------

def _carpeta_guion(sesion):
    casa = Path.home()
    base = next((d for d in (casa / "Downloads", casa / "Descargas", casa / "Desktop") if d.is_dir()), casa)
    return base / re.sub(r'[<>:"/\|?*]+', " ", f"Guion Antigravity - {sesion}").strip()


def escribir_guion(sesion, pedido, abrir=True):
    """Lo mismo que «Escribir guion con Claude», con Gemini en Antigravity: escribe guiones.md, hoja_de_grabacion.md y
    produccion.json en <sesión>/Guion con la skill del guionista y el perfil de la marca. La pantalla espera el
    produccion.json (estado_guion) y la sala lo sube a Drive."""
    import asistente
    try:
        asistente.actualizar_conocimiento(lambda *_: None)
    except RuntimeError:
        asistente.poner_generales()
    guion = motor.Rutas(sesion).principal / "Guion"
    guion.mkdir(parents=True, exist_ok=True)
    prod = guion / "produccion.json"
    if prod.exists():  # el guion de antes queda como copia
        shutil.copyfile(prod, guion / f"produccion.antes-{time.strftime('%Y%m%d-%H%M')}.json")
    carpeta = _carpeta_guion(sesion)
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)
    carpeta.mkdir(parents=True, exist_ok=True)
    texto = f"""# Escribir el guion · {sesion}

Primero lee completas las reglas del guionista: {asistente.ruta_skill()}
y el perfil de la marca: {asistente.CONOCIMIENTO / "perfil_marca.md"}
{perfil.contexto()}

## Pedido del equipo
{pedido.strip() or "(sin indicaciones: elige tú 1 o 2 ideas y 5 ángulos según el perfil de la marca)"}

Trabajas sola, sin nadie a quien preguntarle: no pidas confirmación, decide con criterio.
Respeta la estructura del pedido (cuántos cuerpos, hooks por cuerpo y CTAs) y el tema de cada cuerpo.
En produccion.json deja decidido todo: estilos de subtítulo, imágenes de Flow con su prompt, música con su prompt y sonidos.
Las sesiones anteriores (para no repetir cuerpos ni hooks) están en {motor.S.base}, cada una con su carpeta Guion.

## Qué escribir
Los tres archivos, directamente en esta carpeta: {guion}
  - guiones.md
  - hoja_de_grabacion.md
  - produccion.json (JSON válido, con el formato de las reglas; escríbelo al final, cuando los otros dos estén listos)
PROHIBIDO, para ir rápido: escribir scripts o correr comandos. Usa solo la herramienta de escribir archivos.
Al terminar responde en 5 líneas: ideas, ángulos y cuántas imágenes y pistas hay que generar.
"""
    (carpeta / "INSTRUCCIONES.md").write_text(texto, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(texto, encoding="utf-8")
    (carpeta / "_sala.json").write_text(json.dumps({"sesion": sesion, "guion": str(guion), "pedido": time.time()},
                                                   ensure_ascii=False), encoding="utf-8")
    mensaje = f"Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo tal cual. No escribas scripts ni corras comandos."
    abierto, enviado, porque = _pasar(carpeta, mensaje, "flash", abrir, titulo=f"Sala · Guion {sesion}")
    return {"carpeta": str(carpeta), "mensaje": mensaje, "abierto": abierto, "enviado": enviado, "porque": porque,
            "instalado": bool(exe())}


def estado_guion(sesion):
    """¿Ya escribió Gemini el guion pedido? {"pedido", "listo", "error", "subido"}."""
    meta_f = _carpeta_guion(sesion) / "_sala.json"
    try:
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pedido": False}
    prod = Path(meta["guion"]) / "produccion.json"
    if not prod.exists() or prod.stat().st_mtime < meta["pedido"]:
        return {"pedido": True, "listo": False}
    if time.time() - prod.stat().st_mtime < 3:  # todavía lo está escribiendo
        return {"pedido": True, "listo": False}
    try:
        json.loads(prod.read_text(encoding="utf-8"))
    except ValueError as e:
        return {"pedido": True, "listo": False, "error": f"El produccion.json salió con un error de formato ({e}). Pídele a Gemini que lo corrija."}
    return {"pedido": True, "listo": True, "subido": bool(meta.get("subido")), "meta": str(meta_f)}


# ---------- Pasar un guion cualquiera (el PDF para imprimir, un Word…) a la plantilla de la sala ----------

def _carpeta_plantilla(sesion=None):
    """«Plantilla Antigravity - <producción>» en Descargas (como los otros pedidos: _carpeta)."""
    return _carpeta("Plantilla Antigravity")[1]


INSTRUCCIONES_PLANTILLA = """# Pasar el guion a la plantilla de la sala · {sesion}

El equipo tiene su guion en otro formato ({archivo}). Pásalo a la plantilla que entiende la sala de edición.
El guion está en: {original}
(y su texto, ya sacado, en: {texto})

## Qué escribir
Un solo archivo, en esta carpeta: {salida}
JSON válido, con esta forma exacta:
{{"hojas": [{{"nombre": "R1 Se está apagando", "filas": [["H1", "texto del hook", ""], ["C1.1", "frase", "PAS"], ...]}}]}}

Cada fila es [código, lo que se dice, nota]. Los códigos:
- H1, H2, H3…: hook que sirve para TODOS los cuerpos (por ejemplo, «hooks universales» o «sirven para los 5 cuerpos»).
- C1 H1, C2 H3…: hook que es solo de ese cuerpo (aparece dentro de ese cuerpo).
- C1.1, C1.2, C1.3…: las frases del cuerpo 1, en orden, una fila por frase.
- C1 CTA: el cierre (CTA) del cuerpo 1. Si el cierre tiene varias frases, una fila por frase, todas con «C1 CTA».
- CTA: un cierre que sirve para todos los cuerpos.
La nota (tercera columna) es opcional: el nombre de la estructura del cuerpo (PAS, AIDA…) en su primera frase.

## Reglas
- Copia el texto TAL CUAL está escrito: no lo corrijas, no lo resumas, no inventes frases.
- Sáltate todo lo que no se dice en cámara: índice, títulos de página, nombres de sección, casillas, notas de grabación, explicaciones.
- Si el documento trae varias rondas, temas o sesiones (cada una con sus propios hooks y cuerpos), haz una hoja por cada una,
  con un nombre corto (máximo 30 letras), y empieza los cuerpos de cada hoja en C1.
- Si trae una sola, una sola hoja llamada «{sesion}».
PROHIBIDO, para ir rápido: escribir scripts o correr comandos. Usa solo la herramienta de escribir archivos.
Al terminar responde en una línea: cuántas hojas, cuerpos y hooks pasaste.
"""


def pasar_a_plantilla(sesion, nombre_archivo, datos, abrir=True):
    """Le pide a Gemini (Antigravity) que pase un guion de cualquier formato a la plantilla: deja plantilla.json en la
    carpeta del pedido. La pantalla espera (estado_plantilla) y muestra la tabla para revisarla antes de usarla."""
    ext = Path(nombre_archivo).suffix.lower()
    if ext not in (".pdf", ".docx", ".md", ".txt"):
        raise ValueError("Antigravity puede pasar a la plantilla un PDF, un Word (.docx) o un texto (.md o .txt).")
    try:
        texto = motor._texto_docx(datos) if ext == ".docx" else motor._texto_pdf(datos) if ext == ".pdf" else motor._texto_plano(datos)
    except Exception:  # noqa: BLE001 — igual le queda el archivo original
        texto = ""
    carpeta = _carpeta_plantilla(sesion)
    if carpeta.exists():
        shutil.rmtree(carpeta, ignore_errors=True)
    carpeta.mkdir(parents=True, exist_ok=True)
    original = carpeta / ("guion_original" + ext)
    original.write_bytes(datos)
    (carpeta / "guion_original_texto.txt").write_text(texto, encoding="utf-8")
    salida = carpeta / "plantilla.json"
    instr = INSTRUCCIONES_PLANTILLA.format(sesion=sesion, archivo=Path(nombre_archivo).name, original=original,
                                           texto=carpeta / "guion_original_texto.txt", salida=salida)
    (carpeta / "INSTRUCCIONES.md").write_text(instr, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(instr, encoding="utf-8")
    (carpeta / "_sala.json").write_text(json.dumps({"sesion": sesion, "archivo": Path(nombre_archivo).name, "pedido": time.time()},
                                                   ensure_ascii=False), encoding="utf-8")
    mensaje = f"Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo tal cual. No escribas scripts ni corras comandos."
    abierto, enviado, porque = _pasar(carpeta, mensaje, "flash", abrir, titulo=f"Sala · Plantilla {sesion}")
    return {"carpeta": str(carpeta), "mensaje": mensaje, "abierto": abierto, "enviado": enviado, "porque": porque,
            "instalado": bool(exe())}


def plantilla_de_antigravity(sesion):
    """Lo que escribió Gemini: [(nombre de la hoja, filas)], o None si todavía no llega. ValueError si salió mal."""
    meta_f = _carpeta_plantilla(sesion) / "_sala.json"
    try:
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    f = meta_f.parent / "plantilla.json"
    if not f.exists() or f.stat().st_mtime < meta["pedido"] or time.time() - f.stat().st_mtime < 3:
        return None
    try:
        hojas = json.loads(f.read_text(encoding="utf-8-sig")).get("hojas")
    except (ValueError, AttributeError) as e:
        raise ValueError(f"La plantilla salió con un error de formato ({e}). Pídele a Antigravity que la corrija.")
    if not isinstance(hojas, list) or not hojas:
        raise ValueError("La plantilla salió sin hojas. Pídele a Antigravity que la corrija.")
    out = []
    for h in hojas:
        filas = [[str(x if x is not None else "") for x in (f or [])][:3] for f in (h or {}).get("filas") or [] if isinstance(f, list)]
        out.append((str((h or {}).get("nombre") or f"Hoja {len(out) + 1}")[:31], filas))
    return out


def estado_plantilla(sesion):
    """{"pedido", "listo", "error", "hojas": [{nombre, cuerpos, hooks, para_todos, ctas, filas, error}]}."""
    import guion_plantilla
    if not (_carpeta_plantilla(sesion) / "_sala.json").exists():
        return {"pedido": False}
    try:
        hojas = plantilla_de_antigravity(sesion)
    except ValueError as e:
        return {"pedido": True, "listo": False, "error": str(e)}
    if hojas is None:
        return {"pedido": True, "listo": False}
    resumen = []
    for nombre, filas in hojas:
        r = {"nombre": nombre, "filas": filas[:200]}
        try:
            pz = guion_plantilla.piezas_de_filas(filas)
            r.update(cuerpos=sum(1 for v in pz.values() if v["tipo"] == "cuerpo"), hooks=sum(1 for v in pz.values() if v["tipo"] == "hook"),
                     para_todos=sum(1 for v in pz.values() if v.get("todos")), ctas=sum(1 for v in pz.values() if v["tipo"] == "cta"))
        except ValueError as e:
            r["error"] = str(e)
        resumen.append(r)
    return {"pedido": True, "listo": True, "hojas": resumen, "carpeta": str(_carpeta_plantilla(sesion))}


def marcar_guion_subido(sesion):
    meta_f = _carpeta_guion(sesion) / "_sala.json"
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    meta["subido"] = time.time()
    meta_f.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
