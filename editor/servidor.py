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
import drive
import motor
import panel

PUERTO = 8765
# Desde otra PC o la tablet (por WiFi) solo se puede abrir el teleprompter, y con PIN.
ABIERTO_A_LA_RED = {"/teleprompter", "/api/teleprompter", "/entrar"}


def ip_local():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def red():
    r = motor.CONFIG.get("red_local", {})
    return {"activa": bool(r.get("activa")), "pin": r.get("pin", ""),
            "url": f"http://{ip_local()}:{PUERTO}/teleprompter" if r.get("activa") else None}
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
    for m in checklist["musica"]:
        f = motor.buscar_musica(m["id"])
        m["url"] = url_media(f) if f else None
    musica_ids = [m["id"] for m in checklist["musica"]]
    import fuentes
    fondo = S.proc / "_fondo.jpg"  # un cuadro limpio (sin portada) para previsualizar plantillas
    if not fondo.exists():
        base = next((S.proc / f"{p['nombre']}.mp4" for p in motor.piezas() if (S.proc / f"{p['nombre']}.mp4").exists()), None)
        if base:
            try:
                motor.run([motor.FFMPEG, "-hide_banner", "-y", "-ss", "1", "-i", str(base), "-frames:v", "1",
                           "-vf", "scale=540:-2", "-q:v", "4", str(fondo)])
            except Exception:
                pass
    dr = drive.estado()
    return {"version": motor.VERSION, "red": red(), "drive": dr,
            "sesion_bajada": (not dr["activo"]) or drive.bajada(S.semana), "fondo": url_media(fondo) if fondo.exists() else None, "plantillas": motor.plantillas(), "plantilla": motor.id_plantilla(),
            "plantilla_predeterminada": motor.CONFIG.get("plantilla_activa") or motor.CONFIG["subtitulos"].get("plantilla"),
            "plantilla_del_guion": prod.get("plantilla_sub"), "fuentes": fuentes.disponibles(),
            "propias": list(motor.CONFIG.get("plantillas_propias", {})),
            "claves": [motor.norm(x) for x in motor.CONFIG["palabras_clave"]],
            "semana": S.semana, "prefijo": S.prefijo, "semanas": motor.semanas_disponibles(),
            "carpeta": str(S.grab), "hay_produccion": bool(prod),
            "piezas": ps, "salidas": salidas, "assets": assets, "checklist": checklist, "musica_ids": musica_ids, "piezas_guion": {n: i.get("texto", "") for n, i in prod.get("piezas", {}).items()},
            "calendario": motor.calendario(), "sfx": sfx, "estilos": motor.ESTILOS_SUB,
            "listos": str(S.listos), "panel": bool(motor.CONFIG.get("panel", {}).get("url")),
            "notas": notas.read_text(encoding="utf-8") if notas.exists() else "", "trabajo": TRABAJO}


def lanzar(accion, pieza=None, extra=None):
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
                if accion == "conectar":
                    drive.conectar(extra["link"], extra.get("client_id", ""), extra.get("client_secret", ""), avance=avance)
                if accion == "bajar":
                    drive.bajar_sesion(motor.S.semana, avance=avance)
                if accion == "subir_drive":
                    drive.subir(motor.S.semana, avance=avance)
                if accion == "lista_drive":
                    avance(f"{len(drive.actualizar_lista())} sesiones en Drive")
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
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.semana, [motor.S.listos.name], avance=avance)
                        avance(f"Exportados y subidos a Drive ({motor.S.listos.name}): {len(hechos)} videos")
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

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _pin_ok(self):
        from http.cookies import SimpleCookie
        c = SimpleCookie(self.headers.get("Cookie") or "")
        pin = red()["pin"]
        return bool(pin) and "sala_pin" in c and c["sala_pin"].value == pin

    def _permitido(self, ruta):
        if self._local():
            return True
        if ruta not in ABIERTO_A_LA_RED:
            self.send_error(403)
            return False
        if ruta == "/api/teleprompter" and not self._pin_ok():
            self._json({"error": "PIN"}, 401)
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        if not self._permitido(u.path):
            return
        if u.path == "/teleprompter":
            b = (motor.RAIZ / "teleprompter.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/teleprompter":
            q = parse_qs(u.query)
            sesion = (q.get("sesion") or [motor.S.semana])[0]
            if sesion not in motor.semanas_disponibles():
                sesion = motor.S.semana
            self._json({"sesion": sesion, "sesiones": motor.semanas_disponibles(), "piezas": motor.guion_para_grabar(sesion)})
        elif u.path == "/":
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
        elif u.path.startswith("/fuentes/"):
            import fuentes
            self._archivo(fuentes.ruta(unquote(u.path[len("/fuentes/"):])).parent, unquote(u.path[len("/fuentes/"):]))
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
        carpetas = {"assets": motor.S.assets, "musica": motor.S.musica, "hooks": motor.S.carpeta("hook"),
                    "cuerpos": motor.S.carpeta("cuerpo"), "ctas": motor.S.carpeta("cta")}
        permitidas = motor.EXT_AUDIO if destino == "musica" else motor.EXT_IMG | motor.EXT_VID
        if destino not in carpetas or not NOMBRE_OK.match(nombre) or ext not in permitidas:
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
        if not self._permitido(u.path):
            return
        try:
            if u.path == "/entrar":
                pin = str(self._cuerpo().get("pin", ""))
                if not red()["pin"] or pin != red()["pin"]:
                    time.sleep(1)  # frena intentos a ciegas
                    return self._json({"ok": False}, 403)
                b = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"sala_pin={pin}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            if u.path == "/api/red":
                activa = bool(self._cuerpo().get("activa"))
                import secrets
                actual = motor.CONFIG.get("red_local", {})
                motor.guardar_config({"red_local": {"activa": activa, "pin": actual.get("pin") or f"{secrets.randbelow(9000) + 1000}"}})
                return self._json({"ok": True, **red(), "reiniciar": True})
            if u.path == "/api/subir":
                archivo = self._subir(q)
                carpeta = {"assets": motor.S.assets, "musica": motor.S.musica, "hooks": motor.S.carpeta("hook"),
                           "cuerpos": motor.S.carpeta("cuerpo"), "ctas": motor.S.carpeta("cta")}[q["destino"][0]]
                drive.subir_en_segundo_plano(motor.S.semana, [carpeta.relative_to(motor.S.grab).as_posix()])
                self._json({"ok": True, "archivo": archivo})
            elif u.path == "/api/semana":
                sem = " ".join(self._cuerpo()["semana"].split())
                sem = sem.replace("/", "／")  # Drive permite "11/10 SESIÓN"; en disco se guarda con la barra ancha
                if not re.fullmatch(r"[\w .／-]{1,60}", sem) or sem.startswith("."):
                    raise ValueError("Nombre de semana no válido")
                nueva = motor.S.base / sem
                if drive.estado()["activo"] and sem not in drive.sesiones_en_drive():
                    drive.crear_sesion(sem)
                if not nueva.exists() and not (drive.estado()["activo"] and sem in drive.sesiones_en_drive()):
                    for sub in ("Guion", "Clips/Hooks", "Clips/Cuerpo", "Clips/CTA", "Flow", "V.FINALES"):
                        (nueva / sub).mkdir(parents=True, exist_ok=True)
                motor.usar_semana(sem)
                motor.guardar_config({"semana": sem})
                if drive.estado()["activo"] and not drive.bajada(sem):
                    lanzar("bajar")  # se baja sola la primera vez que se abre
                self._json({"ok": True})
            elif u.path == "/api/subtitulos":
                motor.guardar_lineas_editadas(q["pieza"][0], self._cuerpo()["lineas"])
                self._json({"ok": True})
            elif u.path == "/api/plantilla":
                b = self._cuerpo()
                pid = b["id"]
                if not re.fullmatch(r"[a-z0-9_]{1,40}", pid):
                    raise ValueError("Id de plantilla no válido")
                if b.get("borrar"):
                    propias = {k: v for k, v in motor.CONFIG.get("plantillas_propias", {}).items() if k != pid}
                    motor.guardar_config({"plantillas_propias": propias})
                if b.get("datos"):
                    if pid in motor.CONFIG.get("plantillas_sub", {}):
                        raise ValueError("Las plantillas de fábrica no se editan: duplícala y edita la copia")
                    motor.guardar_config({"plantillas_propias": {**motor.CONFIG.get("plantillas_propias", {}), pid: b["datos"]}})
                if b.get("predeterminada"):
                    motor.guardar_config({"plantilla_activa": pid})
                self._json({"ok": True})
            elif u.path == "/api/fuente":
                import fuentes
                nombre = re.sub(r"[^\w.-]", "_", q["nombre"][0])
                if not nombre.lower().endswith((".ttf", ".otf")):
                    raise ValueError("Sube un archivo .ttf u .otf")
                fuentes.PROPIAS.mkdir(parents=True, exist_ok=True)
                destino = fuentes.PROPIAS / nombre
                destino.write_bytes(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                try:
                    fuentes.Fuente(destino)
                except Exception:
                    destino.unlink()
                    raise ValueError("Ese archivo no es una fuente que la sala pueda leer")
                fuentes.cargar.cache_clear()
                self._json({"ok": True, "archivo": nombre})
            elif u.path == "/api/asignar":
                b = self._cuerpo()
                motor.guardar_asignacion(b["rel"], b.get("pieza") or "")
                drive.subir_en_segundo_plano(motor.S.semana, ["asignaciones.json"])
                self._json({"ok": True})
            elif u.path == "/api/drive":
                b = self._cuerpo()
                if b.get("accion") == "conectar":
                    drive.carpeta_id(b.get("link", ""))  # valida el link antes de abrir Google
                    lanzar("conectar", extra=b)
                elif b.get("accion") == "desconectar":
                    drive.desconectar()
                else:
                    lanzar({"bajar": "bajar", "subir": "subir_drive", "lista": "lista_drive"}[b["accion"]])
                self._json({"ok": True})
            elif u.path == "/api/numerar_hooks":
                n = motor.numerar_hooks(self._cuerpo().get("cuerpo"))
                drive.subir_en_segundo_plano(motor.S.semana, ["asignaciones.json"])
                self._json({"ok": True, "asignadas": n})
            elif u.path == "/api/aceptar_sugerencias":
                n = motor.aceptar_sugerencias()
                drive.subir_en_segundo_plano(motor.S.semana, ["asignaciones.json"])
                self._json({"ok": True, "asignadas": n})
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
    host = "0.0.0.0" if red()["activa"] else "127.0.0.1"
    print(f"Sala de edición en http://localhost:{PUERTO}  (sesión {motor.S.semana})")
    if red()["activa"]:
        print(f"Teleprompter para la tablet: {red()['url']}  ·  PIN {red()['pin']}")
    ThreadingHTTPServer((host, PUERTO), H).serve_forever()
