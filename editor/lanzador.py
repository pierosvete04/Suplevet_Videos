"""SalaSuplevet.exe: abre la Sala de edición (videos) en su propia ventana (como un programa) y la reinicia cuando se actualiza.

Carpeta portable (lo que se instala):
  SalaSuplevet/
    SalaSuplevet.exe   este lanzador + Python + librerías (PyInstaller)
    app/               el código de la sala; es lo único que cambia al actualizar
    ffmpeg/            ffmpeg.exe y ffprobe.exe
    datos/             lo de esta PC: configuración, procesados, videos armados (nunca se toca)

La ventana es del propio programa (pywebview con WebView2, el motor que Windows usa para dibujar páginas
en sus apps): con el ícono de la sala en la barra de tareas, sin navegador de por medio. Lo que la ventana guarda
(tema, vista elegida) va a datos/ventana_app. Si a una PC le falta WebView2, se abre con Edge en modo aplicación.
Por dentro la sala sigue siendo un servidor solo de esta PC (127.0.0.1), que se apaga al cerrar la ventana
(si estaba armando o exportando, primero termina). El .exe es un programa de ventana (sin consola): no aparece
el cuadro negro al abrir. Lo que escribe la sala va a datos/sala.log, y ffmpeg, rclone y Claude corren sin ventana.

Si cambias este archivo o las librerías, sube LANZADOR_VERSION y el archivo LANZADOR_MINIMO:
la sala pedirá bajar la versión completa en vez de actualizar solo el código.
"""
LANZADOR_VERSION = "2.2.0"

# Imports explícitos para que PyInstaller empaquete lo que usa el código de app/ (que no analiza).
import argparse, ctypes, glob, hashlib, http.server, io, json, mimetypes, re, runpy, secrets, shutil, socket  # noqa: E401,F401
import socketserver, subprocess, sys, tempfile, threading, time, traceback, unicodedata, webbrowser  # noqa: E401,F401
import difflib, http.cookies, urllib.parse, urllib.request, zipfile  # noqa: E401,F401
import base64, datetime, functools, getpass, html, platform, struct, uuid  # noqa: E401,F401  (los usa el código de app/: si falta uno, la app no arranca)
import xml.dom.minidom, xml.dom.expatbuilder, xml.dom.xmlbuilder  # noqa: E401,F401  (pypdf, en vendor/, para leer guiones en PDF)
import os
from pathlib import Path


def _para_el_empaquetador():  # nunca se llama: así el lanzador abre rápido y PyInstaller igual las incluye
    import numpy  # noqa: F401
    import faster_whisper  # noqa: F401


try:
    import webview  # ventana propia; si falta, se usa Edge
except Exception:  # noqa: BLE001
    webview = None

PUERTO = int(os.environ.get("SALA_PUERTO") or 8765)  # otro puerto solo para probar con la sala abierta
URL = f"http://127.0.0.1:{PUERTO}"
TITULO = "Sala de edición"


def base():
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def preparar_entorno():
    b = base()
    app = b / "app" if (b / "app" / "servidor.py").exists() else b
    datos = b / "datos" if app != b else b
    datos.mkdir(exist_ok=True)
    os.environ["SALA_DATOS"] = str(datos)
    os.environ["SALA_VENTANA"] = "1"  # la sala se apaga sola cuando se cierra su ventana
    if getattr(sys, "frozen", False):  # solo el .exe se actualiza solo; en desarrollo se usa git pull
        os.environ["SALA_LANZADOR"] = LANZADOR_VERSION
    ff = b / "ffmpeg"
    if ff.exists():
        os.environ["PATH"] = str(ff) + os.pathsep + os.environ.get("PATH", "")
    return app, datos


def en_uso():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PUERTO)) == 0


def sin_ventanas_de_consola():
    """El .exe no tiene consola: sin esto, cada ffmpeg, ffprobe o rclone que lanza la sala abriría su propia
    ventana negra. Todo programa que se lance sale sin ventana, salvo que pida otra cosa."""
    if os.name != "nt":
        return
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        flags = kwargs.get("creationflags") or 0
        if not flags & 0x00000010:  # salvo que pida CREATE_NEW_CONSOLE, va sin ventana aunque pida otra cosa (prioridad)
            kwargs["creationflags"] = flags | 0x08000000  # CREATE_NO_WINDOW
        original(self, *args, **kwargs)
    subprocess.Popen.__init__ = init


def salida_a_log(datos):
    """Un programa de ventana no tiene dónde escribir: lo que imprima va a datos/sala.log."""
    if sys.stdout is None or sys.stderr is None:
        log = open(datos / "sala.log", "a", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def correr_servidor():
    app, datos = preparar_entorno()
    salida_a_log(datos)
    sin_ventanas_de_consola()
    sys.path.insert(0, str(app))
    os.chdir(app)
    runpy.run_path(str(app / "servidor.py"), run_name="__main__")


def consola_para_errores():
    """SalaSuplevet.exe --consola: abre una consola para ver lo que pasa (el .exe normal no tiene)."""
    if os.name == "nt" and ctypes.windll.kernel32.AllocConsole():
        sys.stdout = sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)


def mensaje(texto, error=True):
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(0, texto, TITULO, 0x10 if error else 0x40)
    else:
        print(texto)


def navegador_app():
    """Edge (viene con Windows 10/11) o Chrome, para abrir la sala como ventana de programa."""
    pf = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for raiz in pf:
            ruta = os.path.join(raiz, rel)
            if raiz and os.path.isfile(ruta):
                return ruta
    return None


ABRIENDO = """<!doctype html><meta charset="utf-8"><body style="margin:0;height:100vh;display:grid;place-items:center;
background:#04060b;color:#8494b4;font:15px 'Segoe UI',system-ui,sans-serif"><div style="text-align:center">
<div style="font:700 20px 'Segoe UI',system-ui,sans-serif;color:#edf3ff;margin-bottom:10px">{titulo}</div>
<div>Abriendo…</div></div></body>"""
NO_ARRANCO = """<!doctype html><meta charset="utf-8"><body style="margin:0;height:100vh;display:grid;place-items:center;
background:#04060b;color:#edf3ff;font:15px 'Segoe UI',system-ui,sans-serif"><div style="max-width:520px;text-align:center">
<div style="font:700 20px 'Segoe UI';margin-bottom:10px">{titulo} no pudo arrancar</div>
<div style="color:#8494b4">Mándale a Claude el archivo<br><code>{log}</code></div></div></body>"""


def cargar(ventana, listo, log_ruta):
    """Con la ventana ya abierta («Abriendo…»), espera al servidor y entonces muestra la app."""
    if listo():
        ventana.load_url(URL)
    else:
        ventana.load_html(NO_ARRANCO.format(titulo=TITULO, log=log_ruta))


def ventana_propia(datos, log, listo=None, log_ruta=""):
    """Abre la sala en su propia ventana y espera a que se cierre. False si en esta PC no se pudo (falta WebView2)."""
    if webview is None:
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # teleprompter, Drive, Google: en el navegador
        ventana = webview.create_window(TITULO, None if listo else URL, html=ABRIENDO.format(titulo=TITULO) if listo else None, width=1440, height=920, min_size=(900, 600), text_select=True)
        webview.start(cargar if listo else None, (ventana, listo, log_ruta) if listo else None, gui="edgechromium", private_mode=False, storage_path=str(datos / "ventana_app"))
        return True
    except Exception:  # noqa: BLE001
        log.write("No se pudo abrir la ventana propia; se usa Edge.\n" + traceback.format_exc())
        log.flush()
        return False


def avisar_cierre():
    """La ventana se cerró: la sala termina lo que esté haciendo y se apaga sola."""
    try:
        urllib.request.urlopen(urllib.request.Request(URL + "/api/adios", data=b"", method="POST"), timeout=5).read()
    except Exception:  # noqa: BLE001
        pass


def abrir_ventana(datos):
    exe = navegador_app()
    if not exe:
        webbrowser.open(URL)
        return None
    perfil = datos / "ventana"
    subprocess.Popen([exe, f"--app={URL}", f"--user-data-dir={perfil}", "--no-first-run", "--no-default-browser-check",
                             "--disable-features=Translate,msEdgeSidebarV2,msHubApps", "--window-size=1440,920",
                             "--disable-background-mode"])


def main():
    if "--servidor" in sys.argv:
        return correr_servidor()
    _, datos = preparar_entorno()
    if "--consola" in sys.argv:  # para ver errores: SalaSuplevet.exe --consola
        consola_para_errores()
    salida_a_log(datos)
    if en_uso():  # ya estaba abierta: solo otra ventana
        if not ventana_propia(datos, sys.stderr):
            abrir_ventana(datos)
        return
    log = open(datos / "sala.log", "a", encoding="utf-8", errors="replace")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} · lanzador {LANZADOR_VERSION} =====\n")
    log.flush()
    cmd = [sys.executable, "--servidor"] if getattr(sys, "frozen", False) else [sys.executable, __file__, "--servidor"]
    estado = {"proc": subprocess.Popen(cmd, stdout=log, stderr=log), "codigo": None}
    def listo():  # True cuando el servidor responde; False si se cerró o no arrancó en 90 s
        for _ in range(360):
            if en_uso():
                return True
            if estado["proc"].poll() is not None:
                return False
            time.sleep(0.25)
        return False
    if webview is None and not listo():  # sin ventana propia: se espera aquí, como antes
        mensaje(f"La sala no pudo arrancar. Mándale a Claude el archivo:\n{datos / 'sala.log'}")
        return

    def servidor():  # el servidor se reinicia solo cuando se instala una actualización (código 42)
        while True:
            codigo = estado["proc"].wait()
            if codigo != 42:
                estado["codigo"] = codigo
                return
            estado["proc"] = subprocess.Popen(cmd, stdout=log, stderr=log)  # la ventana sigue abierta y se recarga sola
    hilo = threading.Thread(target=servidor, daemon=True)
    hilo.start()
    if ventana_propia(datos, log, listo, str(datos / 'sala.log')):  # espera aquí hasta que se cierre la ventana
        avisar_cierre()
    else:
        # Edge pasa la ventana a su propio proceso, así que no se la puede esperar: la página avisa con un latido
        # y la sala se apaga sola (código 0) cuando se cierra la ventana.
        listo()  # Edge no tiene «Abriendo…»: se espera a que el servidor responda
        abrir_ventana(datos)
    hilo.join()
    if estado["codigo"] not in (0, None):
        mensaje(f"La sala se detuvo (código {estado['codigo']}). Mándale a Claude el archivo:\n{datos / 'sala.log'}")


if __name__ == "__main__":
    main()
