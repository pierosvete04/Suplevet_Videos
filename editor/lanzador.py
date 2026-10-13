"""SalaSuplevet.exe: abre la sala de edición y la reinicia cuando se actualiza.

Carpeta portable (lo que se descomprime):
  SalaSuplevet/
    SalaSuplevet.exe   este lanzador + Python + librerías (PyInstaller)
    app/               el código de la sala; es lo único que cambia al actualizar
    ffmpeg/            ffmpeg.exe y ffprobe.exe
    datos/             lo de esta PC: configuración, procesados, videos armados (nunca se toca)

Si cambias este archivo o las librerías, sube LANZADOR_VERSION y el archivo LANZADOR_MINIMO:
la sala pedirá bajar la versión completa en vez de actualizar solo el código.
"""
LANZADOR_VERSION = "1.0.0"

# Imports explícitos para que PyInstaller empaquete lo que usa el código de app/ (que no analiza).
import argparse, glob, hashlib, http.server, io, json, mimetypes, re, runpy, secrets, shutil, socket  # noqa: E401,F401
import socketserver, subprocess, sys, tempfile, threading, time, traceback, unicodedata, webbrowser  # noqa: E401,F401
import urllib.parse, urllib.request, zipfile  # noqa: E401,F401
import os
from pathlib import Path

import numpy  # noqa: F401
import faster_whisper  # noqa: F401

PUERTO = 8765
URL = f"http://localhost:{PUERTO}"


def base():
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def preparar_entorno():
    b = base()
    app = b / "app" if (b / "app" / "servidor.py").exists() else b
    datos = b / "datos" if app != b else b
    datos.mkdir(exist_ok=True)
    os.environ["SALA_DATOS"] = str(datos)
    if getattr(sys, "frozen", False):  # solo el .exe se actualiza solo; en desarrollo se usa git pull
        os.environ["SALA_LANZADOR"] = LANZADOR_VERSION
    ff = b / "ffmpeg"
    if ff.exists():
        os.environ["PATH"] = str(ff) + os.pathsep + os.environ.get("PATH", "")
    return app


def en_uso():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PUERTO)) == 0


def correr_servidor():
    app = preparar_entorno()
    sys.path.insert(0, str(app))
    os.chdir(app)
    runpy.run_path(str(app / "servidor.py"), run_name="__main__")


def main():
    if "--servidor" in sys.argv:
        return correr_servidor()
    os.system("title Sala de edicion Suplevet (no cierres esta ventana)")
    print("Sala de edición Suplevet")
    print("Para apagarla, cierra esta ventana.\n")
    if en_uso():
        print("La sala ya estaba abierta. Abriendo el navegador…")
        webbrowser.open(URL)
        time.sleep(2)
        return
    primera = True
    while True:
        cmd = [sys.executable, "--servidor"] if getattr(sys, "frozen", False) else [sys.executable, __file__, "--servidor"]
        proc = subprocess.Popen(cmd)
        if primera:
            for _ in range(60):
                if en_uso():
                    break
                time.sleep(0.5)
            webbrowser.open(URL)
            primera = False
        codigo = proc.wait()
        if codigo == 42:
            print("\nActualización instalada. Reiniciando la sala…\n")
            continue
        print(f"\nLa sala se detuvo (código {codigo}). Si ves un error arriba, mándale una captura a Claude.")
        input("Presiona Enter para cerrar.")
        return


if __name__ == "__main__":
    main()
