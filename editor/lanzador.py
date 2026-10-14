"""SalaSuplevet.exe: abre la sala de edición en su propia ventana (como un programa) y la reinicia cuando se actualiza.

Carpeta portable (lo que se instala):
  SalaSuplevet/
    SalaSuplevet.exe   este lanzador + Python + librerías (PyInstaller)
    app/               el código de la sala; es lo único que cambia al actualizar
    ffmpeg/            ffmpeg.exe y ffprobe.exe
    datos/             lo de esta PC: configuración, procesados, videos armados (nunca se toca)

La ventana es Microsoft Edge en modo aplicación (viene con Windows): sin barra de direcciones ni pestañas,
con su propio perfil en datos/ventana. Por dentro la sala sigue siendo un servidor solo de esta PC (127.0.0.1),
que se apaga al cerrar la ventana. La consola queda oculta; lo que escribe va a datos/sala.log.

Si cambias este archivo o las librerías, sube LANZADOR_VERSION y el archivo LANZADOR_MINIMO:
la sala pedirá bajar la versión completa en vez de actualizar solo el código.
"""
LANZADOR_VERSION = "1.1.0"

# Imports explícitos para que PyInstaller empaquete lo que usa el código de app/ (que no analiza).
import argparse, ctypes, glob, hashlib, http.server, io, json, mimetypes, re, runpy, secrets, shutil, socket  # noqa: E401,F401
import socketserver, subprocess, sys, tempfile, threading, time, traceback, unicodedata, webbrowser  # noqa: E401,F401
import difflib, http.cookies, urllib.parse, urllib.request, zipfile  # noqa: E401,F401
import os
from pathlib import Path

import numpy  # noqa: F401
import faster_whisper  # noqa: F401

PUERTO = 8765
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


def correr_servidor():
    app, _ = preparar_entorno()
    sys.path.insert(0, str(app))
    os.chdir(app)
    runpy.run_path(str(app / "servidor.py"), run_name="__main__")


def ocultar_consola():
    if os.name == "nt":
        ventana = ctypes.windll.kernel32.GetConsoleWindow()
        if ventana:
            ctypes.windll.user32.ShowWindow(ventana, 0)


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
    if "--consola" not in sys.argv:  # para ver errores: SalaSuplevet.exe --consola
        ocultar_consola()
    if en_uso():  # ya estaba abierta: solo otra ventana
        abrir_ventana(datos)
        return
    log = open(datos / "sala.log", "a", encoding="utf-8", errors="replace")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} · lanzador {LANZADOR_VERSION} =====\n")
    log.flush()
    primera = True
    while True:
        cmd = [sys.executable, "--servidor"] if getattr(sys, "frozen", False) else [sys.executable, __file__, "--servidor"]
        proc = subprocess.Popen(cmd, stdout=log, stderr=log)
        if primera:
            for _ in range(120):
                if en_uso() or proc.poll() is not None:
                    break
                time.sleep(0.25)
            if proc.poll() is not None:
                mensaje(f"La sala no pudo arrancar. Mándale a Claude el archivo:\n{datos / 'sala.log'}")
                return
            abrir_ventana(datos)
            primera = False
        # Edge pasa la ventana a su propio proceso, así que no se la puede esperar: la página avisa con un latido
        # y la sala se apaga sola (código 0) cuando se cierra la ventana. Aquí solo se espera al servidor.
        codigo = proc.wait()
        if codigo == 42:  # actualización instalada: la ventana sigue abierta y se recarga sola
            continue
        if codigo != 0:
            mensaje(f"La sala se detuvo (código {codigo}). Mándale a Claude el archivo:\n{datos / 'sala.log'}")
        return


if __name__ == "__main__":
    main()
