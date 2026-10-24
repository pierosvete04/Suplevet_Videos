"""Paquete para Antigravity: las imágenes y clips de Flow que faltan, listos para que Gemini Flash los haga sin Claude.

Deja en Descargas una carpeta «Paquete Antigravity - <sesión>» con:
  INSTRUCCIONES.md   lo que Gemini tiene que hacer, paso a paso (sin pensar: pegar de corrido y bajar todo al final)
  prompts.txt        un prompt por línea con su código delante («C1_IMG2: …»), en el orden en que se pegan
  prompts.json       lo mismo con el plan B de cada uno (si Google rechaza un prompt)
  bajar_flow.js      baja de un golpe las imágenes de Flow con su nombre (C1_IMG2.jpg), ya con la lista de nombres
Después, en la sala: Faltantes → «Poner en los videos» las pone en su lugar y las sube a Drive.

armar_prompts() es el otro paquete: Gemini escribe los prompts (lo que hace «Agregar los prompts» con Claude)
sobre una copia del guion, y traer_prompts() la revisa y la pasa a la sesión.
"""
import json
import re
import shutil
import subprocess
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


def exe():
    """Antigravity.exe si está instalado en esta PC."""
    import os
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
4. Para cada línea de `prompts.txt` que sea imagen: pega la línea completa (con su código, por ejemplo `C1_IMG2: …`) en el cuadro
   de texto y envíala. **No esperes a que termine**: pega la siguiente enseguida. Flow las genera todas en paralelo.
5. Si Google rechaza un prompt, pega en su lugar el `plan_b` de ese código (en `prompts.json`), con el mismo código delante.
6. Cuando ya no quede ninguna generándose, corre `bajar_flow.js` en la pestaña de Flow con la herramienta para ejecutar
   JavaScript en la página. Baja cada imagen a Descargas como `CODIGO.jpg`. Revisa que la respuesta no tenga errores; si a
   alguna le falta, bájala a mano con ese nombre.
{f'''7. Clips en movimiento ({len(clips)}): cambia el generador a **Video** (9:16), pega cada línea de clip igual que las imágenes
   y, cuando terminen, baja cada video a Descargas con el nombre `CODIGO.mp4`.
''' if clips else ""}
Si aparece un inicio de sesión, términos o un captcha: detente y avisa a la persona. No lo hagas tú.

Al terminar, responde en 2 líneas: cuántas bajaste y cuáles fallaron.
Después, en la Sala de edición: Faltantes → «Poner en los videos» las pone en su lugar.
"""


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
    abierto = _abrir(carpeta, abrir)
    mensaje = (f"Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo tal cual, con el navegador. "
               f"Son {len(nombres)} imágenes" + (f" y {len(orden) - len(nombres)} clips" if len(orden) > len(nombres) else "")
               + " de Google Flow. Al final responde en 2 líneas cuántas bajaste y cuáles fallaron.")
    return {"carpeta": str(carpeta), "imagenes": len(nombres), "clips": len(orden) - len(nombres), "mensaje": mensaje,
            "abierto": abierto, "instalado": bool(exe())}


def armar_prompts(por_cuerpo=3, clips=1, por_hook=0, abrir=False):
    """«Agregar los prompts» sin Claude: Gemini, en Antigravity, agrega los prompts de Flow a una copia del guion
    (produccion.json) en Descargas, con las mismas reglas que sigue Claude. Luego traer_prompts() la pasa a la sesión."""
    import asistente
    prod = motor.S.guion / "produccion.json"
    if not prod.exists():
        raise ValueError("Esta sesión no tiene guion: primero arma el guion con lo grabado")
    try:
        asistente.actualizar_conocimiento(lambda *_: None)
    except RuntimeError:
        pass
    origen = asistente.CONOCIMIENTO / ".claude" / "skills" / "suplevet-guionista-modular" / "SKILL.md"
    if not origen.exists():
        raise ValueError("Faltan las reglas para escribir los prompts (las skills de la sala, en Drive _SALA/claude). Conecta Drive y vuelve a intentar.")
    sesion, carpeta = _carpeta("Prompts Antigravity")
    carpeta.mkdir(parents=True, exist_ok=True)
    copia, reglas = carpeta / "produccion.json", carpeta / "REGLAS_PROMPTS.md"
    shutil.copyfile(prod, copia)
    shutil.copyfile(origen, reglas)
    try:
        pedido = asistente.pedido_apoyo_visual(por_cuerpo, clips, por_hook, ruta=copia, reglas=reglas)
    except RuntimeError as e:
        raise ValueError(str(e))
    pedido = pedido.replace("Usa Edit para cambiar el archivo.", "Edita solo el produccion.json de esta carpeta.")
    texto = f"""# Prompts de Flow para Antigravity · {sesion}

Trabaja solo con los archivos de esta carpeta ({carpeta}). Aquí hay que escribir bien, así que usa el modelo de
Gemini que piensa más (no Flash). Empieza una conversación nueva.

{pedido}

Cuando termines, en la Sala de edición se toca «Traer los prompts»: la sala revisa el archivo y lo pasa a la sesión.
"""
    (carpeta / "INSTRUCCIONES.md").write_text(texto, encoding="utf-8")
    (carpeta / "GEMINI.md").write_text(texto, encoding="utf-8")  # Antigravity lee GEMINI.md solo al abrir la carpeta
    antes = len(json.loads(prod.read_text(encoding="utf-8")).get("assets") or [])
    (carpeta / "_sala.json").write_text(json.dumps({"semana": motor.S.semana, "assets_antes": antes,
                                                    "mtime": prod.stat().st_mtime}), encoding="utf-8")
    abierto = _abrir(carpeta, abrir)
    mensaje = (f"Lee {carpeta / 'INSTRUCCIONES.md'} y hazlo tal cual: agrega los prompts de Flow editando el produccion.json "
               "de esa carpeta. Al final responde en 3 líneas cuántas imágenes y clips agregaste y en qué piezas.")
    return {"carpeta": str(carpeta), "mensaje": mensaje, "abierto": abierto, "instalado": bool(exe())}


def traer_prompts():
    """Pasa a la sesión el guion con los prompts que escribió Gemini, si es JSON válido y tiene prompts nuevos."""
    _, carpeta = _carpeta("Prompts Antigravity")
    meta_f, copia = carpeta / "_sala.json", carpeta / "produccion.json"
    if not meta_f.exists() or not copia.exists():
        raise ValueError("No hay prompts de Antigravity pendientes para esta sesión: primero toca «Agregar los prompts» con Antigravity")
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    if meta.get("semana") != motor.S.semana:
        raise ValueError("Esos prompts son de otra sesión: abre esa sesión en la sala para traerlos")
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
