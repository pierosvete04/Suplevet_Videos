"""Sube a Drive (_SALA/claude) lo que Claude necesita dentro de la sala de las otras PCs: las skills del guionista
y del director, y el contexto de la marca. Se corre en la PC del dueño cada vez que cambian las skills:

    python preparar_claude.py

Va a Drive (privado) y no al repositorio, que es público.
"""
import shutil
import sys
from pathlib import Path

import drive
import motor

SKILLS = Path.home() / ".claude" / "skills"
TRABAJOS = Path(r"F:\TRABAJOS CLAUDE")
CONTEXTO = [*sorted((TRABAJOS / "PÁGINA WEB - SUPLEVET" / "_context").glob("*.md")),
            TRABAJOS / "PÁGINA WEB - SUPLEVET" / "Estudios y suplevet" / "BULLETS-BENEFICIOS-AMPLIADOS.md",
            TRABAJOS / "GUIONES - SUPLEVET" / "01_Guia_de_Hooks_Suplevet.md",
            TRABAJOS / "GUIONES - SUPLEVET" / "02_Guiones_B2B_Veterinarios_6x5.md",
            motor.RAIZ / "GUIA_SONIDOS.md"]
USAR = ["suplevet-guionista-modular", "suplevet-director", "suplevet-flow-chrome"]

CLAUDE_MD = """# Claude dentro de la sala de edición

Te llama la sala de edición de otra PC (no la del dueño), sin nadie mirando: no hagas preguntas ni pidas confirmación;
decide con criterio y deja todo escrito en las carpetas que te indica el pedido.

- Las rutas `F:\\TRABAJOS CLAUDE\\…` de las skills no existen aquí. Los archivos de contexto están en `contexto/`
  con el mismo nombre (marca, voz, productos, FAQ, beneficios, guía de hooks, guiones aprobados, guía de sonidos).
- No corras comandos (python, drive.py, motor.py): la sala procesa, aplica el plan y sube a Drive sola.
- Las sesiones anteriores están en la carpeta que dice el pedido; cada una tiene `Guion/guiones.md`.
- Escribe siempre en español, con palabras simples. Los prompts de Flow (imágenes y clips) y de música van siempre.
"""


def armar(destino):
    shutil.rmtree(destino, ignore_errors=True)
    (destino / "contexto").mkdir(parents=True, exist_ok=True)
    for f in CONTEXTO:
        if f.exists():
            shutil.copy2(f, destino / "contexto" / f.name)
        else:
            print("Falta:", f)
    for nombre in USAR:
        texto = (SKILLS / nombre / "SKILL.md").read_text(encoding="utf-8")
        for viejo in (r"F:\TRABAJOS CLAUDE\PÁGINA WEB - SUPLEVET\_context" + "\\",
                      r"F:\TRABAJOS CLAUDE\PÁGINA WEB - SUPLEVET\Estudios y suplevet" + "\\",
                      r"F:\TRABAJOS CLAUDE\GUIONES - SUPLEVET" + "\\0", "editor\\GUIA_SONIDOS.md"):
            nuevo = "contexto\\0" if viejo.endswith("\\0") else "contexto\\GUIA_SONIDOS.md" if "GUIA" in viejo else "contexto\\"
            texto = texto.replace(viejo, nuevo)
        (destino / ".claude" / "skills" / nombre).mkdir(parents=True, exist_ok=True)
        (destino / ".claude" / "skills" / nombre / "SKILL.md").write_text(texto, encoding="utf-8")
    (destino / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    local = motor.DATOS / "claude"
    armar(local)
    print("Armado en", local)
    if drive.estado()["activo"]:
        drive._rc("sync", local, f"{drive.REMOTO}:_SALA/claude", timeout=600)
        print("Subido a Drive: _SALA/claude")
    else:
        print("Drive no está conectado en esta PC: no se subió.")
