"""Sala de edición Suplevet: revisar videos, portadas y copies; corregir subtítulos y marcas; subir assets.

Abrir:  python servidor.py   →   http://localhost:8765   (o doble clic en abrir_sala.bat)
"""
import json
import mimetypes
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import actualizador
import motor
import panel

PUERTO = 8765
TRABAJO = {"activo": False, "accion": None, "mensaje": "", "error": None, "fin": None, "log": []}
candado = threading.Lock()


def url_media(ruta):
    """Ruta local -> URL servible. Lo de la semana (Drive) va por /grab, lo del editor por /media."""
    ruta = Path(ruta)
    try:
        return "/grab/" + quote(ruta.relative_to(motor.S.grab).as_posix()) + f"?v={int(ruta.stat().st_mtime)}"
    except (ValueError, FileNotFoundError):
        pass
    try:
        return "/media/" + quote(ruta.relative_to(motor.DATOS).as_posix()) + (
            f"?v={int(ruta.stat().st_mtime)}" if ruta.exists() else "")
    except ValueError:
        return None


def estado():
    S = motor.S
    prod = motor.produccion()
    ps = []
    for p in motor.piezas():
        d = motor.leer_subs(p["nombre"])
        proc = S.proc / f"{p['nombre']}.mp4"
        ps.append({"nombre": p["nombre"], "tipo": p["tipo"], "procesada": proc.exists(),
                   "video": url_media(proc if proc.exists() else p["original"]),
                   "lineas": d["lineas"], "marcas": motor.marcas_de(p["nombre"]),
                   "marcas_editadas": "marcas" in d, "info": motor.info_pieza(p["nombre"])})
    salidas = []
    for c in motor.combinaciones():
        f = S.salida / f"{c['archivo']}.mp4"
        por = S.portadas / f"{c['archivo']}.jpg"
        r = S.recetas / f"{c['codigo']}.json"
        salidas.append({**c, "existe": f.exists(), "video": url_media(f) if f.exists() else None,
                        "portada": url_media(por) if por.exists() else None,
                        "titulo_portada": motor.texto_portada(c) if (S.proc / f"{c['hook']}.mp4").exists() or prod else "",
                        "receta": json.loads(r.read_text(encoding="utf-8")) if r.exists() else None,
                        "copy": motor.copy_de(c["codigo"])})
    assets = []
    if S.assets.exists():
        assets = [{"id": f.stem, "archivo": f.name, "url": url_media(f),
                   "es_video": f.suffix.lower() in motor.EXT_VID}
                  for f in sorted(S.assets.iterdir()) if f.suffix.lower() in motor.EXT_IMG | motor.EXT_VID]
    notas = motor.DATOS / "correcciones.md"
    sfx = sorted(set(motor.CONFIG.get("sfx_mapa", {})) | {d.name for d in (motor.BIBLIO_BASE / "sfx").iterdir() if d.is_dir()})
    checklist = motor.checklist()
    for c in checklist["sin_asignar"]:
        c["video"] = url_media(motor.S.grab / c["rel"])
    return {"version": motor.VERSION, "semana": S.semana, "prefijo": S.prefijo, "semanas": motor.semanas_disponibles(),
            "carpeta": str(S.grab), "hay_produccion": bool(prod),
            "piezas": ps, "salidas": salidas, "assets": assets, "checklist": checklist, "piezas_guion": {n: i.get("texto", "") for n, i in prod.get("piezas", {}).items()},
            "calendario": motor.calendario(), "sfx": sfx, "estilos": motor.ESTILOS_SUB,
            "listos": str(S.listos), "panel": bool(motor.CONFIG.get("panel", {}).get("url")),
            "notas": notas.read_text(encoding="utf-8") if notas.exists() else "", "trabajo": TRABAJO}


def lanzar(accion, pieza=None):
    """Corre procesar/armar/exportar en segundo plano para que la app no se congele."""
    if TRABAJO["activo"]:
        raise RuntimeError("Ya hay un trabajo en curso. Espera a que termine.")

    def avance(msg):
        TRABAJO["mensaje"] = msg
        TRABAJO["log"] = (TRABAJO["log"] + [msg])[-200:]

    def correr():
        try:
            with candado:
                if accion in ("todo", "procesar"):
                    motor.procesar(avance=avance)
                if accion in ("todo", "armar"):
                    motor.armar(pieza, avance=avance)
                if accion == "escuchar":
                    motor.escuchar_clips(avance=avance)
                if accion == "uno":
                    combo = next(c for c in motor.combinaciones() if c["codigo"] == pieza)
                    avance(f"Armando {combo['archivo']}")
                    motor.armar_uno(combo)
                if accion == "portadas":
                    motor.rehacer_portadas(avance=avance)
                if accion in ("todo", "exportar"):
                    hechos = motor.exportar(avance=avance)
                    avance(f"Exportados {len(hechos)} videos a {motor.S.listos}")
                if accion == "panel":
                    n = panel.subir_semana(avance=avance)
                    avance(f"Subidos {n} videos al panel")
            TRABAJO["mensaje"] = "Listo"
        except Exception as e:
            TRABAJO["error"] = str(e)
            avance("Error: " + str(e).splitlines()[0])
            traceback.print_exc()
        finally:
            TRABAJO["activo"] = False
            TRABAJO["fin"] = time.time()

    TRABAJO.update({"activo": True, "accion": accion, "mensaje": "Empezando…", "error": None, "log": []})
    threading.Thread(target=correr, daemon=True).start()


NOMBRE_OK = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            b = (motor.RAIZ / "app.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/estado":
            self._json(estado())
        elif u.path == "/api/trabajo":
            self._json(TRABAJO)
        elif u.path == "/api/version":
            self._json(actualizador.revisar(forzar="forzar" in u.query))
        elif u.path.startswith("/media/"):
            self._archivo(motor.DATOS, unquote(u.path[len("/media/"):]))
        elif u.path.startswith("/grab/"):
            self._archivo(motor.S.grab, unquote(u.path[len("/grab/"):]))
        else:
            self.send_error(404)

    def _archivo(self, base, rel):
        base = base.resolve()
        ruta = (base / rel).resolve()
        if base not in ruta.parents or not ruta.is_file():
            return self.send_error(404)
        tam = ruta.stat().st_size
        ini, fin = 0, tam - 1
        rango = self.headers.get("Range")
        if rango and rango.startswith("bytes="):
            a, _, b = rango[6:].partition("-")
            ini = int(a) if a else 0
            fin = min(int(b), tam - 1) if b else tam - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {ini}-{fin}/{tam}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(ruta.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fin - ini + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(ruta, "rb") as f:
            f.seek(ini)
            restante = fin - ini + 1
            try:
                while restante > 0:
                    trozo = f.read(min(1 << 16, restante))
                    if not trozo:
                        break
                    self.wfile.write(trozo)
                    restante -= len(trozo)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass

    def _subir(self, q):
        """Guarda el archivo arrastrado con el nombre correcto (assets de Flow o piezas grabadas)."""
        destino = q["destino"][0]
        nombre = q["nombre"][0]
        ext = "." + q["ext"][0].lower().lstrip(".")
        carpetas = {"assets": motor.S.assets, "hooks": motor.S.carpeta("hook"),
                    "cuerpos": motor.S.carpeta("cuerpo"), "ctas": motor.S.carpeta("cta")}
        if destino not in carpetas or not NOMBRE_OK.match(nombre) or ext not in motor.EXT_IMG | motor.EXT_VID:
            raise ValueError("Archivo o nombre no válido")
        carpeta = carpetas[destino]
        carpeta.mkdir(parents=True, exist_ok=True)
        for viejo in carpeta.glob(f"{nombre}.*"):  # reemplaza la versión anterior aunque tenga otra extensión
            viejo.unlink()
        ruta = carpeta / f"{nombre}{ext}"
        restante = int(self.headers.get("Content-Length") or 0)
        with open(ruta, "wb") as f:
            while restante > 0:
                trozo = self.rfile.read(min(1 << 20, restante))
                if not trozo:
                    break
                f.write(trozo)
                restante -= len(trozo)
        return ruta.name

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/subir":
                self._json({"ok": True, "archivo": self._subir(q)})
            elif u.path == "/api/semana":
                sem = " ".join(self._cuerpo()["semana"].split())
                if not re.fullmatch(r"[\w .-]{1,60}", sem) or sem.startswith("."):
                    raise ValueError("Nombre de semana no válido")
                nueva = motor.S.base / sem
                if not nueva.exists():  # sesión nueva con la convención del equipo
                    for sub in ("Guion", "Clips/Hooks", "Clips/Cuerpo", "Clips/CTA", "Flow", "V.FINALES"):
                        (nueva / sub).mkdir(parents=True, exist_ok=True)
                motor.usar_semana(sem)
                motor.guardar_config({"semana": sem})
                self._json({"ok": True})
            elif u.path == "/api/subtitulos":
                motor.guardar_lineas_editadas(q["pieza"][0], self._cuerpo()["lineas"])
                self._json({"ok": True})
            elif u.path == "/api/asignar":
                b = self._cuerpo()
                motor.guardar_asignacion(b["rel"], b.get("pieza") or "")
                self._json({"ok": True})
            elif u.path == "/api/numerar_hooks":
                self._json({"ok": True, "asignadas": motor.numerar_hooks(self._cuerpo().get("cuerpo"))})
            elif u.path == "/api/aceptar_sugerencias":
                self._json({"ok": True, "asignadas": motor.aceptar_sugerencias()})
            elif u.path == "/api/marcas":
                motor.guardar_marcas(q["pieza"][0], self._cuerpo()["marcas"])
                self._json({"ok": True})
            elif u.path == "/api/copy":
                motor.guardar_copy(q["codigo"][0], self._cuerpo())
                self._json({"ok": True})
            elif u.path == "/api/trabajo":
                b = self._cuerpo()
                lanzar(b["accion"], b.get("pieza"))
                self._json({"ok": True})
            elif u.path == "/api/actualizar":
                if not actualizador.LANZADOR:
                    raise RuntimeError("En desarrollo se actualiza con git pull, no desde la app")
                if TRABAJO["activo"]:
                    raise RuntimeError("Espera a que termine el trabajo en curso")
                version = actualizador.aplicar()
                self._json({"ok": True, "version": version})
                actualizador.reiniciar_luego()
            elif u.path == "/api/notas":
                (motor.DATOS / "correcciones.md").write_text(self._cuerpo()["texto"], encoding="utf-8")
                self._json({"ok": True})
            else:
                self.send_error(404)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"Sala de edición en http://localhost:{PUERTO}  (semana {motor.S.semana})")
    ThreadingHTTPServer(("127.0.0.1", PUERTO), H).serve_forever()
