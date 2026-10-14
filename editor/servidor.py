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
import asistente
import drive
import motor
import panel

PUERTO = int(sys.argv[sys.argv.index("--puerto") + 1]) if "--puerto" in sys.argv else 8765  # --puerto para probar sin chocar con la sala abierta
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
LATIDO = {"ultimo": 0.0, "adios": 0.0}  # la ventana avisa que está abierta; al cerrarla, la sala se apaga (solo con el .exe)


def al_cerrar_ventana():
    """La ventana dijo adiós: si en unos segundos no vuelve (no era una recarga), se apaga la sala.
    Si hay un trabajo en curso (armar, exportar, subir), primero lo termina."""
    import os
    if not os.environ.get("SALA_VENTANA"):
        return
    def esperar():
        marca = LATIDO["adios"]
        time.sleep(6)
        if LATIDO["ultimo"] > marca:
            return
        while TRABAJO["activo"]:
            time.sleep(2)
            if LATIDO["ultimo"] > marca:
                return
        os._exit(0)
    threading.Thread(target=esperar, daemon=True).start()


def vigilar_ventana():
    """Por si la ventana se cierra sin despedirse (se colgó, se apagó la PC a medias): sin latido por 3 minutos
    y sin trabajo en curso, la sala se apaga. Minimizada, la página igual late una vez por minuto."""
    import os
    if not os.environ.get("SALA_VENTANA"):
        return
    def revisar():
        while True:
            time.sleep(30)
            if LATIDO["ultimo"] and time.time() - LATIDO["ultimo"] > 180 and not TRABAJO["activo"]:
                os._exit(0)
    threading.Thread(target=revisar, daemon=True).start()


TRABAJO = {"activo": False, "accion": None, "mensaje": "", "error": None, "fin": None, "log": [], "historial": [], "inicio": None}
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
        info_hook = motor.info_pieza(c["hook"])
        v = c.get("version") or {}
        etiqueta = v.get("nombre") or f"Hook {c['hook'].split('_')[-1].lstrip('H')}" + (f" · {info_hook['tipo']}" if info_hook.get("tipo") else "")
        ap = motor.ajustes_portada(c["codigo"])
        av = motor.ajustes_video(c["codigo"])
        # desactualizado: se cambió algo de sus piezas (subtítulos, recortes, zoom) o sus ajustes después de armarlo
        viejo = f.exists() and motor.editado_en(c) > f.stat().st_mtime + 1
        salidas.append({**c, "etiqueta": etiqueta, "es_version": bool(v), "existe": f.exists(), "desactualizado": viejo,
                        "estado": av.get("estado") or "pendiente",
                        "ajustes_portada": {"segundo": ap.get("segundo"), "titulo": ap.get("titulo"),
                                            "posicion": ap.get("posicion", "centro"), "logo": ap.get("logo", True)},
                        "ajustes_video": av, "video": url_media(f) if f.exists() else None,
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
    ajustes_p = motor.ajustes_piezas()
    for p in ps:
        p["editada"] = bool({k for k in ajustes_p.get(p["nombre"], {}) if k != "editado"})
    return {"version": motor.VERSION, "red": red(), "drive": dr, "bajando": TRABAJO["activo"] and TRABAJO["accion"] == "bajar",
            "sesion_bajada": (not dr["activo"]) or all(drive.bajada(m) for m in S.miembros),
            "grupo": {"sesiones": S.miembros, "guion": S.drive} if S.es_grupo else None, "grupos": motor.grupos(), "fondo": url_media(fondo) if fondo.exists() else None, "plantillas": motor.plantillas(), "plantilla": motor.id_plantilla(),
            "plantilla_predeterminada": motor.CONFIG.get("plantilla_activa") or motor.CONFIG["subtitulos"].get("plantilla"),
            "plantilla_del_guion": prod.get("plantilla_sub"), "fuentes": fuentes.disponibles(),
            "propias": list(motor.CONFIG.get("plantillas_propias", {})), "fabrica": list(motor.CONFIG.get("plantillas_sub", {})),
            "elementos": elementos(),
            "claves": [motor.norm(x) for x in motor.CONFIG["palabras_clave"]],
            "semana": S.semana, "prefijo": S.prefijo, "semanas": motor.semanas_disponibles(),
            "carpeta": str(S.grab), "hay_produccion": bool(prod),
            "piezas": ps, "salidas": salidas, "assets": assets, "checklist": checklist, "musica_ids": musica_ids, "piezas_guion": {n: i.get("texto", "") for n, i in prod.get("piezas", {}).items()},
            "calendario": motor.calendario(), "exportar": motor.ajustes_exportar(), "sfx": sfx, "estilos": motor.ESTILOS_SUB,
            "listos": str(S.listos), "panel": bool(motor.CONFIG.get("panel", {}).get("url")),
            "notas": notas.read_text(encoding="utf-8") if notas.exists() else "", "trabajo": TRABAJO}


def subir_asignaciones():
    for m in motor.S.miembros:
        drive.subir_en_segundo_plano(m, ["asignaciones.json"])


def elementos():
    """Imágenes, logos y GIF para las plantillas: los que subió el equipo y los de la marca."""
    out = []
    for base, propio in ((motor.ELEMENTOS, True), (motor.BIBLIO_BASE / "marca", False)):
        if base.is_dir():
            for f in sorted(base.iterdir()):
                if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    out.append({"archivo": f.name, "propio": propio, "url": url_media(f) if propio else "/marca/" + quote(f.name)})
    return out


def lanzar(accion, pieza=None, extra=None):
    """Corre procesar/armar/exportar en segundo plano para que la app no se congele."""
    if TRABAJO["activo"]:
        raise RuntimeError("Ya hay un trabajo en curso. Espera a que termine.")

    def avance(msg):
        motor.revisar_cancelado()
        TRABAJO["mensaje"] = msg
        TRABAJO["log"] = (TRABAJO["log"] + [msg])[-200:]

    def correr():
        try:
            with candado:
                if accion in ("todo", "procesar"):
                    motor.procesar(avance=avance)
                if accion in ("todo", "armar"):
                    motor.armar(pieza, avance=avance)
                if accion == "armar_sel":
                    motor.armar(avance=avance, codigos=extra["codigos"])
                if accion == "exportar_sel":
                    faltan = [c for c in motor.combinaciones() if c["codigo"] in extra["codigos"]
                              and not (motor.S.salida / f"{c['archivo']}.mp4").exists()]
                    if faltan:
                        motor.armar(avance=avance, codigos=[c["codigo"] for c in faltan])
                    hechos = motor.exportar(avance=avance, codigos=extra["codigos"], ajustes=extra.get("ajustes"))
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, [motor.S.listos.name], avance=avance)
                    avance(f"Exportados {len(hechos)} videos a {motor.S.listos.name}")
                if accion == "conectar":
                    drive.conectar(extra["link"], extra.get("client_id", ""), extra.get("client_secret", ""), avance=avance)
                if accion == "bajar":
                    for m in motor.S.miembros:  # en un grupo, cada una de sus sesiones
                        drive.bajar_sesion(m, avance=avance)
                if accion == "subir_drive":
                    for m in motor.S.miembros:
                        drive.subir(m, avance=avance)
                if accion == "lista_drive":
                    avance(f"{len(drive.actualizar_lista())} sesiones en Drive")
                if accion == "escuchar":
                    motor.escuchar_clips(avance=avance)
                if accion == "analizar":
                    # Paso 1: traer de Drive todos los clips, pasar su audio a texto (en esta PC, gratis), compararlo con
                    # el guion, identificar solos los clips que se parecen claramente a una pieza y procesarlos.
                    if drive.estado()["activo"]:
                        for m in motor.S.miembros:
                            drive.bajar_sesion(m, avance=avance)
                    motor.escuchar_clips(avance=avance)
                    n = motor.aceptar_sugerencias()
                    if n and drive.estado()["activo"]:
                        for m in motor.S.miembros:
                            drive.subir(m, ["asignaciones.json"], avance=avance)
                    motor.procesar(avance=avance)
                    falta = len(motor.clips_sin_asignar())
                    avance(f"Clips analizados y comparados con el guion: {n} identificados solos"
                           + (f", {falta} por revisar a mano" if falta else "") + ".")
                if accion == "guion":
                    sesion = extra["sesion"]
                    texto = asistente.generar_guion(sesion, extra.get("pedido", ""), avance=avance)
                    motor._prod_cache = None
                    if sesion == motor.S.semana:
                        motor.guardar_documentos_guion()
                    if drive.estado()["activo"]:
                        drive.subir(sesion, ["Guion"], avance=avance)
                    TRABAJO["resultado"] = texto
                    avance("Guion listo: " + (texto.strip().splitlines() or [""])[0][:120])
                if accion == "temas":
                    TRABAJO["resultado"] = asistente.proponer_temas(extra["cuerpos"], extra["publico"], extra.get("idea", ""), avance=avance)
                    avance("Temas propuestos: elige uno por cuerpo.")
                if accion == "revisar":
                    r = asistente.revisar_grabacion(motor.S.semana, avance=avance)
                    if drive.estado()["activo"]:
                        drive.subir(motor.S.drive, ["Guion"], avance=avance)
                    TRABAJO["resultado"] = r["texto"]
                    if r["aplicado"]:
                        avance("Ojo: estas palabras del plan no se dijeron: " + ", ".join(r["aplicado"][:5]))
                    motor.armar(avance=avance)
                    avance("Revisión de Claude aplicada y videos re-armados")
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
                        drive.subir(motor.S.drive, [motor.S.listos.name], avance=avance)
                        avance(f"Exportados y subidos a Drive ({motor.S.listos.name}): {len(hechos)} videos")
                if accion == "importar":
                    n = panel.importar_finales(avance=avance)
                    avance(f"Importados {n} videos finales al panel")
                if accion == "panel":
                    n = panel.subir_semana(avance=avance)
                    avance(f"Subidos {n} videos al panel")
            TRABAJO["mensaje"] = "Listo"
        except motor.Cancelado:
            TRABAJO["error"] = None
            TRABAJO["mensaje"] = "Cancelado"
            TRABAJO["log"] = (TRABAJO["log"] + ["Cancelado por ti."])[-200:]
            TRABAJO["cancelado"] = True
        except Exception as e:
            TRABAJO["error"] = str(e)
            avance("Error: " + str(e).splitlines()[0])
            traceback.print_exc()
        finally:
            TRABAJO["activo"] = False
            TRABAJO["fin"] = time.time()
            TRABAJO["historial"] = ([{"accion": accion, "inicio": TRABAJO["inicio"], "fin": TRABAJO["fin"],
                                      "resultado": "cancelado" if TRABAJO.get("cancelado") else "error" if TRABAJO["error"] else "listo",
                                      "mensaje": (TRABAJO["error"] or (TRABAJO["log"] or [""])[-1])[:200]}]
                                    + TRABAJO.get("historial", []))[:15]

    motor.CANCELAR.clear()
    TRABAJO.update({"activo": True, "accion": accion, "mensaje": "Empezando…", "error": None, "log": [], "resultado": None,
                    "cancelado": False, "inicio": time.time()})
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
        elif u.path == "/api/cuadro":
            q2 = parse_qs(u.query)
            c = next((x for x in motor.combinaciones() if x["codigo"] == q2["codigo"][0]), None)
            if not c:
                return self.send_error(404)
            destino = motor.carpeta_trabajo() / "cuadro.jpg"
            motor.cuadro(c, float(q2.get("t", ["0.5"])[0]), destino)
            b = destino.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/portada_vista":  # la portada con los ajustes que se están eligiendo, sin guardarla
            q2 = parse_qs(u.query)
            c = next((x for x in motor.combinaciones() if x["codigo"] == q2["codigo"][0]), None)
            if not c:
                return self.send_error(404)
            destino = motor.carpeta_trabajo() / "portada_vista.jpg"
            motor.hacer_portada(c, prueba={"segundo": float(q2.get("segundo", ["0.5"])[0]), "titulo": q2.get("titulo", [""])[0],
                                           "posicion": q2.get("posicion", ["centro"])[0], "logo": q2.get("logo", ["1"])[0] == "1"},
                                destino=destino)
            b = destino.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/guion":
            self._json(motor.revision_guion())
        elif u.path == "/api/estructura":
            try:
                e = motor.estructura(parse_qs(u.query)["codigo"][0])
                for k in ("hook", "cuerpo", "cta"):
                    e[k]["url"] = url_media(e[k].pop("archivo")) if e[k].get("archivo") else None
                self._json(e)
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif u.path == "/api/version":
            self._json(actualizador.revisar(forzar="forzar" in u.query))
        elif u.path == "/api/claude":
            self._json(asistente.estado(forzar="forzar" in u.query))
        elif u.path == "/api/latido":
            if "ping" in u.query:
                LATIDO["ultimo"] = time.time()
            self._json({"viva": time.time() - LATIDO["ultimo"] < 20})
        elif u.path == "/api/onda":
            self._json(motor.onda(parse_qs(u.query)["pieza"][0]))
        elif u.path == "/api/guion_doc":
            version = (parse_qs(u.query).get("version") or ["host"])[0]
            vista = (parse_qs(u.query).get("vista") or ["combinacion"])[0]
            texto = motor.documento_guion("produccion" if version == "produccion" else "host", vista)
            if texto and "guardar" in u.query:  # quedan también en Drive (carpeta Guion), para abrirlas desde cualquier lado
                motor.guardar_documentos_guion()
                drive.subir_en_segundo_plano(motor.S.drive, ["Guion"])
            self._json({"texto": texto})
        elif u.path == "/api/informe":
            f = motor.S.guion / "director" / "informe.md"
            self._json({"texto": f.read_text(encoding="utf-8") if f.exists() else ""})
        elif u.path.startswith("/media/"):
            self._archivo(motor.DATOS, unquote(u.path[len("/media/"):]))
        elif u.path.startswith("/fuentes/"):
            import fuentes
            self._archivo(fuentes.ruta(unquote(u.path[len("/fuentes/"):])).parent, unquote(u.path[len("/fuentes/"):]))
        elif u.path.startswith("/marca/"):
            self._archivo(motor.BIBLIO_BASE / "marca", unquote(u.path[len("/marca/"):]))
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
            if u.path == "/api/cancelar":
                if TRABAJO["activo"]:
                    motor.cancelar()
                return self._json({"ok": True})
            if u.path == "/api/adios":
                LATIDO["adios"] = time.time()
                al_cerrar_ventana()
                return self._json({"ok": True})
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
                drive.subir_en_segundo_plano(motor.S.drive, [carpeta.relative_to(motor.S.principal).as_posix()])
                self._json({"ok": True, "archivo": archivo})
            elif u.path == "/api/semana":
                sem = " ".join(self._cuerpo()["semana"].split())
                sem = sem.replace("/", "／")  # Drive permite "11/10 SESIÓN"; en disco se guarda con la barra ancha
                if not re.fullmatch(r"[\w .／-]{1,60}", sem) or sem.startswith("."):
                    raise ValueError("Nombre de semana no válido")
                nueva = motor.S.base / sem
                es_grupo = sem in motor.grupos()
                if not es_grupo and drive.estado()["activo"] and sem not in drive.sesiones_en_drive():
                    drive.crear_sesion(sem)
                if not es_grupo and not nueva.exists() and not (drive.estado()["activo"] and sem in drive.sesiones_en_drive()):
                    for sub in ("Guion", "Clips/Hooks", "Clips/Cuerpo", "Clips/CTA", "Flow", "V.FINALES"):
                        (nueva / sub).mkdir(parents=True, exist_ok=True)
                motor.usar_semana(sem)
                motor.guardar_config({"semana": sem})
                if drive.estado()["activo"] and not all(drive.bajada_completa(m) for m in motor.S.miembros) and not TRABAJO["activo"]:
                    lanzar("bajar")  # se baja sola la primera vez que se abre
                self._json({"ok": True})
            elif u.path == "/api/grupo":
                b = self._cuerpo()
                nombre = " ".join(str(b.get("nombre", "")).split()).upper()[:60]
                gs = motor.grupos()
                if b.get("borrar"):
                    gs.pop(nombre, None)
                else:
                    sesiones = [x for x in b.get("sesiones", []) if x in motor.semanas_disponibles() and x not in gs]
                    if not re.fullmatch(r"[\w .／+-]{2,60}", nombre) or len(sesiones) < 2:
                        raise ValueError("Ponle un nombre al grupo y elige al menos dos sesiones")
                    if nombre in sesiones:
                        raise ValueError("El grupo no puede llamarse igual que una sesión")
                    guion = b.get("guion") if b.get("guion") in sesiones else sesiones[0]
                    gs[nombre] = {"sesiones": sesiones, "guion": guion}
                motor.guardar_grupos(gs)
                drive.subir_grupos()
                if not b.get("borrar"):
                    motor.usar_semana(nombre)
                    motor.guardar_config({"semana": nombre})
                elif motor.S.semana == nombre:
                    motor.usar_semana(motor.semanas_disponibles()[-1])
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
                if b.get("datos"):  # una de fábrica editada se guarda encima (con «Restaurar» vuelve a la original)
                    motor.guardar_config({"plantillas_propias": {**motor.CONFIG.get("plantillas_propias", {}), pid: b["datos"]}})
                if b.get("predeterminada"):
                    motor.guardar_config({"plantilla_activa": pid})
                self._json({"ok": True})
            elif u.path == "/api/elemento":
                nombre = re.sub(r"[^\w.-]", "_", q["nombre"][0])[-80:]
                if not nombre.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    raise ValueError("Sube una imagen PNG, JPG, WEBP o un GIF")
                motor.ELEMENTOS.mkdir(parents=True, exist_ok=True)
                (motor.ELEMENTOS / nombre).write_bytes(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                self._json({"ok": True, "archivo": nombre})
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
            elif u.path == "/api/portada":
                b = self._cuerpo()
                c = next(x for x in motor.combinaciones() if x["codigo"] == b["codigo"])
                motor.guardar_ajustes_portada(b["codigo"], b)
                motor.hacer_portada(c)  # un segundo: no hace falta re-armar el video
                self._json({"ok": True, "portada": url_media(motor.S.portadas / f"{c['archivo']}.jpg")})
            elif u.path == "/api/ajustes_video":
                b = self._cuerpo()
                if b.get("codigos"):  # varios videos elegidos: solo cambia su plantilla (la altura de cada uno se queda)
                    for cod in b["codigos"]:
                        motor.guardar_ajustes_video(cod, {"plantilla": b.get("plantilla") or None})
                    if b.get("armar"):
                        lanzar("armar_sel", extra={"codigos": b["codigos"]})
                    return self._json({"ok": True, "codigos": b["codigos"]})
                codigos = [b["codigo"]]
                if b.get("todo_el_cuerpo"):  # la misma plantilla y altura para todos los videos de ese cuerpo
                    cuerpo = next(c["cuerpo"] for c in motor.combinaciones() if c["codigo"] == b["codigo"])
                    codigos = [c["codigo"] for c in motor.combinaciones() if c["cuerpo"] == cuerpo]
                for cod in codigos:
                    motor.guardar_ajustes_video(cod, {"plantilla": b.get("plantilla") or None,
                                                      "ajustes_sub": b.get("ajustes_sub") or None})
                if b.get("armar"):
                    lanzar("armar_sel", extra={"codigos": codigos})
                self._json({"ok": True, "codigos": codigos})
            elif u.path == "/api/estado_video":
                b = self._cuerpo()
                if b.get("estado") not in ("pendiente", "editando", "listo"):
                    raise ValueError("Estado no válido")
                for cod in b.get("codigos") or [b["codigo"]]:
                    motor.guardar_ajustes_video(cod, {"estado": b["estado"]})
                self._json({"ok": True})
            elif u.path == "/api/edicion":
                b = self._cuerpo()
                codigos = motor.guardar_edicion_piezas(b)
                if b.get("armar") and codigos:
                    lanzar("armar_sel", extra={"codigos": codigos})
                self._json({"ok": True, "codigos": codigos})
            elif u.path == "/api/claude":
                b = self._cuerpo()
                if b.get("accion") == "login":
                    asistente.iniciar_sesion()
                elif b.get("accion") == "guion":
                    sesion = b.get("sesion") or motor.S.semana
                    if sesion not in motor.semanas_disponibles():
                        raise ValueError("Primero crea la sesión")
                    lanzar("guion", extra={"sesion": sesion, "pedido": str(b.get("pedido", ""))[:4000]})
                elif b.get("accion") == "revisar":
                    lanzar("revisar")
                elif b.get("accion") == "temas":
                    lanzar("temas", extra={"cuerpos": max(1, min(10, int(b.get("cuerpos") or 5))),
                                           "publico": "dueños de mascotas" if b.get("publico") == "duenos" else "médicos veterinarios",
                                           "idea": str(b.get("idea", ""))[:1000]})
                else:
                    raise ValueError("Acción no válida")
                self._json({"ok": True})
            elif u.path == "/api/version":
                b = self._cuerpo()
                if b.get("borrar"):
                    motor.borrar_version(b["borrar"])
                    return self._json({"ok": True})
                vid = motor.guardar_version(b, b.get("id") or None)
                if b.get("armar"):
                    lanzar("uno", vid)
                self._json({"ok": True, "codigo": vid})
            elif u.path == "/api/trabajo_sel":
                b = self._cuerpo()
                if b.get("ajustes"):  # los ajustes de exportación quedan guardados para la próxima vez
                    aj = {k: v for k, v in b["ajustes"].items() if k in motor.AJUSTES_EXPORTAR}
                    motor.guardar_config({"exportar": aj})
                    b["ajustes"] = aj
                lanzar(b["accion"], extra={"codigos": b["codigos"], "ajustes": b.get("ajustes")})
                self._json({"ok": True})
            elif u.path == "/api/asignar":
                b = self._cuerpo()
                sesion = motor.guardar_asignacion(b["rel"], b.get("pieza") or "")
                drive.subir_en_segundo_plano(sesion, ["asignaciones.json"])
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
                subir_asignaciones()
                self._json({"ok": True, "asignadas": n})
            elif u.path == "/api/aceptar_sugerencias":
                n = motor.aceptar_sugerencias()
                subir_asignaciones()
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
    print(f"Sala de edición en http://localhost:{PUERTO}  (sesión {motor.S.semana})", flush=True)
    vigilar_ventana()
    if red()["activa"]:
        print(f"Teleprompter para la tablet: {red()['url']}  ·  PIN {red()['pin']}")
    ThreadingHTTPServer((host, PUERTO), H).serve_forever()
