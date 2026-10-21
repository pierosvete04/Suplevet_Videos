"""Actualizaciones automáticas desde GitHub Releases.

Cada versión publicada (tag vX.Y.Z) trae dos archivos que arma GitHub Actions:
  - app-vX.Y.Z.zip          solo el código (motor, servidor, app.html…). Es lo que se baja al actualizar.
  - SalaSuplevet-vX.Y.Z.zip la versión completa con el .exe; solo hace falta si cambia el lanzador.

La sala pregunta al arrancar y cada hora. Si hay una versión nueva muestra el aviso; al aceptar,
se baja app-*.zip, se reemplaza la carpeta del código (los datos están aparte) y la sala se reinicia.
Si el release dice "lanzador mínimo" mayor al de este .exe, pide bajar la versión completa.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import motor

LANZADOR = os.environ.get("SALA_LANZADOR")  # versión del .exe; vacío si se corre con python (desarrollo)
CODIGO_REINICIO = 42
_cache = {"t": 0, "datos": None}


def _cfg():
    a = motor.CONFIG.get("actualizaciones", {})
    return a.get("repo", ""), motor.CONFIG.get("github_token") or a.get("token", "")


def _pedir(url, binario=False, intentos=3):
    """Pide algo a GitHub. Si la conexión se corta (pasa a ratos con algunas redes), vuelve a intentar."""
    repo, token = _cfg()
    h = {"Accept": "application/octet-stream" if binario else "application/vnd.github+json", "User-Agent": "SalaSuplevet"}
    if token and "api.github.com" in url:
        h["Authorization"] = f"Bearer {token}"
    ultimo = None
    for k in range(intentos):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120 if binario else 15) as r:
                return r.read()
        except urllib.error.HTTPError:
            raise  # GitHub respondió (404, 403…): reintentar no cambia nada
        except Exception as e:  # se cortó o no respondió a tiempo
            ultimo = e
            time.sleep(1.5 * (k + 1))
    raise ultimo


def _ultima_por_la_web(repo):
    """Si la API de GitHub no responde, la última versión se saca de la página de releases (otro servidor)."""
    h = {"User-Agent": "SalaSuplevet"}
    ultimo = None
    for k in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"https://github.com/{repo}/releases/latest", headers=h), timeout=15) as r:
                tag = r.geturl().rstrip("/").rsplit("/", 1)[-1]
            if tag.startswith("v"):
                return tag
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(1.5 * (k + 1))
    raise ultimo or RuntimeError("no se encontró la última versión")


def _num(v):
    return tuple(int(x) for x in v.lstrip("v").split(".")[:3] if x.isdigit())


def revisar(forzar=False):
    """{'actual', 'nueva', 'notas', 'completa'} o {'actual', 'error'}."""
    repo, _ = _cfg()
    out = {"actual": motor.VERSION, "portable": bool(LANZADOR)}
    if not repo:
        return {**out, "error": "Sin repositorio configurado"}
    if not forzar and _cache["datos"] and time.time() - _cache["t"] < 3600:
        return _cache["datos"]
    try:
        try:
            rel = json.loads(_pedir(f"https://api.github.com/repos/{repo}/releases/latest"))
        except urllib.error.HTTPError:
            raise
        except Exception:  # la API no respondió: se mira la página de releases y se baja el zip directo
            tag = _ultima_por_la_web(repo)
            rel = {"tag_name": tag, "body": "", "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
                   "assets": [{"name": f"app-{tag}.zip", "url": None,
                               "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/app-{tag}.zip"}]}
        nueva = rel["tag_name"].lstrip("v")
        app = next((a for a in rel["assets"] if a["name"].startswith("app-") and a["name"].endswith(".zip")), None)
        completa = next((a for a in rel["assets"] if "Instalador" in a["name"] and a["name"].endswith(".exe")), None)             or next((a for a in rel["assets"] if a["name"].startswith("SalaSuplevet-")), None)
        minimo = next((l.split(":", 1)[1].strip() for l in (rel.get("body") or "").splitlines()
                       if l.lower().startswith("lanzador minimo:")), "0")
        out.update({
            "nueva": nueva if _num(nueva) > _num(motor.VERSION) else None,
            "notas": (rel.get("body") or "").strip()[:1500],
            "url_app": (app.get("url") or app.get("browser_download_url")) if app else None,
            "url_app_directa": app.get("browser_download_url") if app else None,
            "url_completa": completa["browser_download_url"] if completa else rel.get("html_url"),
            "requiere_completa": bool(LANZADOR) and _num(minimo) > _num(LANZADOR or "0"),
        })
    except Exception as e:  # sin internet, repo privado sin token, etc.
        out["error"] = f"GitHub no respondió ({e}). La conexión con GitHub se está cortando: vuelve a intentar en un rato."
        return out  # el error no se guarda: el próximo intento vuelve a preguntar
    _cache.update(t=time.time(), datos=out)
    return out


def aplicar():
    """Baja el código nuevo y lo pone en lugar del actual. Devuelve la versión instalada."""
    info = revisar(forzar=True)
    if not info.get("nueva"):
        raise RuntimeError(info.get("error") or "Ya tienes la última versión")
    if info.get("requiere_completa"):
        raise RuntimeError("Esta versión necesita bajar la sala completa de nuevo: " + (info.get("url_completa") or ""))
    if not info.get("url_app"):
        raise RuntimeError("El release no trae app-*.zip")
    datos = None
    for url in dict.fromkeys(u for u in (info["url_app"], info.get("url_app_directa")) if u):  # API y, si falla, descarga directa
        try:
            datos = _pedir(url, binario=True)
            zipfile.ZipFile(io.BytesIO(datos)).testzip()  # si se cortó a medias, el zip no sirve: se prueba el otro camino
            break
        except Exception as e:  # noqa: BLE001
            datos, ultimo = None, e
    if datos is None:
        raise RuntimeError(f"No se pudo bajar la versión nueva: {ultimo}")
    destino = motor.RAIZ
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(io.BytesIO(datos)).extractall(tmp)
        nuevo = Path(tmp)
        if not (nuevo / "servidor.py").exists():  # zip con carpeta raíz
            hijos = [d for d in nuevo.iterdir() if d.is_dir()]
            nuevo = hijos[0] if len(hijos) == 1 else nuevo
        if not (nuevo / "servidor.py").exists() or not (nuevo / "VERSION").exists():
            raise RuntimeError("El paquete descargado no es válido")
        respaldo = destino.with_name(destino.name + "_anterior")
        shutil.rmtree(respaldo, ignore_errors=True)
        shutil.copytree(destino, respaldo, ignore=shutil.ignore_patterns("__pycache__"))
        try:
            for f in nuevo.rglob("*"):
                rel = f.relative_to(nuevo)
                if f.is_dir():
                    (destino / rel).mkdir(parents=True, exist_ok=True)
                else:
                    (destino / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, destino / rel)
        except Exception:
            shutil.copytree(respaldo, destino, dirs_exist_ok=True)  # vuelve a la versión anterior
            raise
    return info["nueva"]


def reiniciar_luego(segundos=1.0):
    """El lanzador vuelve a abrir la sala cuando el proceso sale con el código 42."""
    def salir():
        time.sleep(segundos)
        os._exit(CODIGO_REINICIO)
    import threading
    threading.Thread(target=salir, daemon=True).start()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(revisar(forzar=True), ensure_ascii=False, indent=1))
