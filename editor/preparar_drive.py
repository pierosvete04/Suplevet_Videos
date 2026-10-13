r"""Crea la carpeta SUPLEVET_VIDEOS en Google Drive y apunta la sala a ella.

Uso:  python preparar_drive.py "G:\Mi unidad"   (o la ruta de SUPLEVET_VIDEOS)
Copia los sonidos y la música de prueba a _BIBLIOTECA (sin pisar los que ya estén) y
guarda las rutas en config.local.json.
"""
import json
import os
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DATOS = Path(os.environ.get("SALA_DATOS") or RAIZ)
LEEME = """SUPLEVET_VIDEOS - carpeta de trabajo compartida (tú + editor + Claude)

_BIBLIOTECA/sfx/<categoría>/   efectos de sonido (varios por carpeta; la sala los rota)
_BIBLIOTECA/musica/            música de fondo
SEMANA_XX/1_guion/             guiones, hoja de grabación y produccion.json (los escribe Claude)
SEMANA_XX/hooks|cuerpos|ctas/  grabaciones (C1_H1.mp4, C1.mp4, CTA-A.mp4)
SEMANA_XX/assets/              imágenes y clips de Google Flow (se arrastran en la sala, pestaña Faltantes)
SEMANA_XX/listos/DIA_X/        videos terminados con portada y copy

Categorías de sonido: impacto, whoosh, whoosh_inverso, zoom, pop, riser, ding, glitch, brillo,
scratch, notificacion, tecleo, tension. Los archivos PROVISIONAL_ son de prueba: reemplázalos.
"""


def main(drive):
    drive = Path(drive.strip().strip('"'))
    base = drive if drive.name.upper() == "SUPLEVET_VIDEOS" else drive / "SUPLEVET_VIDEOS"
    if not base.parent.exists():
        raise SystemExit(f"No encuentro {drive}. Abre Google Drive para escritorio y vuelve a intentar.")
    bib = base / "_BIBLIOTECA"
    for origen, destino in ((RAIZ / "biblioteca" / "sfx", bib / "sfx"), (RAIZ / "biblioteca" / "musica", bib / "musica")):
        for f in origen.rglob("*"):
            d = destino / f.relative_to(origen)
            if f.is_dir():
                d.mkdir(parents=True, exist_ok=True)
            elif not d.exists():
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, d)
    (base / "LEEME.txt").write_text(LEEME, encoding="utf-8")
    local = DATOS / "config.local.json"
    cfg = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
    cfg.update({"carpeta_trabajo": str(base), "biblioteca": str(bib)})
    local.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Listo: {base}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"G:\Mi unidad")
