"""Sube los videos terminados al panel de edición (edicion.suplevet.pe/videos).

Pasos por lote:
  1. POST {url}/api/contenido/preparar   -> URLs firmadas de R2 para subir video y portada
  2. PUT de cada archivo directo a R2
  3. POST {url}/api/contenido/registrar  -> guarda copy, portada, día y receta de cada video

Se configura en config.local.json -> "panel": {"url": "https://edicion.suplevet.pe", "token": "..."}.
El token es el CONTENIDO_TOKEN del panel; no lo pegues en ningún otro lado.
"""
import json
import re
import tempfile
import urllib.request
from pathlib import Path

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


def subir_guion(avance=print):
    """Sube el guion de la sesión (produccion.json) al panel: Creador, Música y teleprompter."""
    prod = motor.produccion()
    if not prod.get("piezas"):
        return False
    _post("/api/contenido/guion", {"sesion": motor.S.semana, "datos": prod})
    avance(f"Guion de «{motor.S.semana}» subido al panel")
    return True


def _subir_archivos(sesion, video, portada):
    archivos = [{"nombre": video.name, "tipo": "video/mp4"}]
    if portada and portada.exists():
        archivos.append({"nombre": portada.name, "tipo": "image/jpeg"})
    prep = _post("/api/contenido/preparar", {"semana": re.sub(r"[^A-Za-z0-9_-]", "_", sesion)[:40], "archivos": archivos})
    urls, keys = {}, []
    for sub in prep["subidas"]:
        _put(sub["uploadUrl"], video if sub["nombre"] == video.name else portada, sub["tipo"], sub["cacheControl"])
        urls[sub["nombre"]] = sub["publicUrl"]
        keys.append(sub["key"])
    return urls.get(video.name), urls.get(portada.name) if portada else None, keys


def importar_finales(avance=print):
    """Trae al panel los videos que ya están en V.FINALES (o VIDEOS FINALES) de cada sesión de Drive.
    Cada uno entra como «importado», con una portada sacada del segundo 1 y el nombre del archivo como código."""
    import drive
    if not drive.estado()["activo"]:
        raise RuntimeError("Conecta la sala con Google Drive primero (Ajustes y notas → Google Drive)")
    total = 0
    for sesion in drive.actualizar_lista():
        for carpeta in ("V.FINALES", "VIDEOS FINALES"):
            try:
                lista = json.loads(drive._rc("lsjson", f"{drive.REMOTO}:{sesion}/{carpeta}", "--files-only", timeout=60) or "[]")
            except RuntimeError:
                continue
            for f in lista:
                if not f["Name"].lower().endswith((".mp4", ".mov")):
                    continue
                codigo = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{sesion.split()[0]}-{Path(f['Name']).stem}").strip("-")[:80]
                avance(f"Importando {sesion} / {f['Name']}")
                with tempfile.TemporaryDirectory() as tmp:
                    local = Path(tmp) / (codigo + Path(f["Name"]).suffix.lower())
                    drive._rc("copyto", f"{drive.REMOTO}:{sesion}/{carpeta}/{f['Name']}", local, timeout=1800)
                    video = local
                    if local.suffix != ".mp4":  # el panel solo reproduce mp4
                        video = local.with_suffix(".mp4")
                        motor.run([motor.FFMPEG, "-hide_banner", "-y", "-i", str(local), "-c:v", "libx264", "-preset", "veryfast",
                                   "-crf", "21", "-c:a", "aac", "-movflags", "+faststart", str(video)])
                    portada = Path(tmp) / f"{codigo}.jpg"
                    motor.run([motor.FFMPEG, "-hide_banner", "-y", "-ss", "1", "-i", str(video), "-frames:v", "1",
                               "-vf", "scale=1080:-2", "-q:v", "3", str(portada)])
                    video_url, portada_url, keys = _subir_archivos(sesion, video, portada)
                    _post("/api/contenido/registrar", {"videos": [{
                        "semana": sesion, "codigo": codigo, "video_url": video_url, "portada_url": portada_url,
                        "r2_keys": keys, "origen": "importado", "duracion": round(motor.duracion(video), 2),
                        "titulo_portada": Path(f["Name"]).stem, "copy": {},
                    }]})
                    total += 1
    return total


def subir_semana(avance=print):
    cfg = motor.CONFIG.get("panel", {})
    if not cfg.get("url") or not cfg.get("token"):
        raise RuntimeError("Falta configurar el panel (url y token) en config.json")
    S = motor.S
    try:
        subir_guion(avance)
    except Exception as e:
        avance(f"No se pudo subir el guion: {e}")
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
