"""Sube los videos terminados al panel de edición (edicion.suplevet.pe/videos).

Pasos por lote:
  1. POST {url}/api/contenido/preparar   -> URLs firmadas de R2 para subir video y portada
  2. PUT de cada archivo directo a R2
  3. POST {url}/api/contenido/registrar  -> guarda copy, portada, día y receta de cada video

Se configura en config.local.json -> "panel": {"url": "https://edicion.suplevet.pe", "token": "..."}.
El token es el CONTENIDO_TOKEN del panel; no lo pegues en ningún otro lado.
"""
import json
import urllib.request

import motor


def _post(ruta, datos):
    cfg = motor.CONFIG["panel"]
    req = urllib.request.Request(cfg["url"].rstrip("/") + ruta, data=json.dumps(datos).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['token']}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _put(url, ruta, tipo, cache):
    # Content-Type y Cache-Control van firmados en la URL: si no coinciden, R2 rechaza la subida
    with open(ruta, "rb") as f:
        req = urllib.request.Request(url, data=f.read(), method="PUT",
                                     headers={"Content-Type": tipo, "Cache-Control": cache})
    with urllib.request.urlopen(req, timeout=600) as r:
        if r.status >= 300:
            raise RuntimeError(f"R2 respondió {r.status} al subir {ruta.name}")


def subir_semana(avance=print):
    cfg = motor.CONFIG.get("panel", {})
    if not cfg.get("url") or not cfg.get("token"):
        raise RuntimeError("Falta configurar el panel (url y token) en config.json")
    S = motor.S
    dia_de = {cod: dia for dia, cods in motor.calendario().items() for cod in cods}
    combos = [c for c in motor.combinaciones() if (S.salida / f"{c['archivo']}.mp4").exists()]
    subidos = 0
    for n, c in enumerate(combos, 1):
        avance(f"Subiendo al panel {c['archivo']} ({n}/{len(combos)})")
        video, portada = S.salida / f"{c['archivo']}.mp4", S.portadas / f"{c['archivo']}.jpg"
        archivos = [{"nombre": video.name, "tipo": "video/mp4"}]
        if portada.exists():
            archivos.append({"nombre": portada.name, "tipo": "image/jpeg"})
        prep = _post("/api/contenido/preparar", {"semana": S.semana, "archivos": archivos})
        claves, keys = {}, []
        for sub in prep["subidas"]:
            ruta = video if sub["nombre"] == video.name else portada
            _put(sub["uploadUrl"], ruta, sub["tipo"], sub["cacheControl"])
            claves[sub["nombre"]] = sub["publicUrl"]
            keys.append(sub["key"])
        receta_f = S.recetas / f"{c['codigo']}.json"
        receta = json.loads(receta_f.read_text(encoding="utf-8")) if receta_f.exists() else {}
        _post("/api/contenido/registrar", {"videos": [{
            "semana": S.semana, "codigo": c["archivo"], "hook": c["hook"], "cuerpo": c["cuerpo"], "cta": c["cta"],
            "tipo_hook": motor.info_pieza(c["hook"]).get("tipo"), "dia": dia_de.get(c["codigo"]),
            "titulo_portada": motor.texto_portada(c), "duracion": receta.get("duracion"),
            "video_url": claves.get(video.name), "portada_url": claves.get(portada.name), "r2_keys": keys,
            "copy": motor.copy_de(c["codigo"]),
        }]})
        subidos += 1
    return subidos
