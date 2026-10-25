"""Lee fuentes TTF sin librerías externas: nombre y ancho de cada texto.

La sala lo usa para ubicar cada palabra del subtítulo y dibujar el resaltador (la caja de color)
justo detrás de la palabra clave. No aplica kerning: la caja lleva un margen que lo cubre.
"""
import struct
from functools import lru_cache
from pathlib import Path

import os

CARPETA = Path(__file__).resolve().parent / "fuentes"                     # las que trae la sala
PROPIAS = Path(os.environ.get("SALA_DATOS") or CARPETA.parent) / "fuentes_propias"  # las que agrega el equipo


def ruta(archivo):
    for d in (PROPIAS, CARPETA):
        if (d / archivo).exists():
            return d / archivo
    return CARPETA / archivo


class Fuente:
    def __init__(self, ruta):
        self.ruta = Path(ruta)
        d = self.ruta.read_bytes()
        n = struct.unpack(">H", d[4:6])[0]
        t = {d[12 + 16 * i:16 + 16 * i].decode("latin-1"): struct.unpack(">II", d[20 + 16 * i:28 + 16 * i])
             for i in range(n)}
        head, hhea, hmtx, cmap, name = (t[k][0] for k in ("head", "hhea", "hmtx", "cmap", "name"))
        self.upem = struct.unpack(">H", d[head + 18:head + 20])[0]
        self.ascender, self.descender = struct.unpack(">hh", d[hhea + 4:hhea + 8])
        nmet = struct.unpack(">H", d[hhea + 34:hhea + 36])[0]
        # libass (como VSFilter) dimensiona la letra con usWinAscent + usWinDescent de la tabla OS/2
        if "OS/2" in t:
            o = t["OS/2"][0]
            wa, wd = struct.unpack(">HH", d[o + 74:o + 78])
            if wa + wd:
                self.ascender, self.descender = wa, -wd
        self._adv = [struct.unpack(">H", d[hmtx + 4 * i:hmtx + 4 * i + 2])[0] for i in range(nmet)]
        self._cmap = self._leer_cmap(d, cmap)
        self.nombres = self._leer_nombres(d, name)
        self.nombre = self.nombres.get(4) or self.nombres.get(1) or self.ruta.stem  # nombre completo

    @staticmethod
    def _leer_cmap(d, base):
        n = struct.unpack(">H", d[base + 2:base + 4])[0]
        mejor = None
        for i in range(n):
            pid, eid, off = struct.unpack(">HHI", d[base + 4 + 8 * i:base + 12 + 8 * i])
            fmt = struct.unpack(">H", d[base + off:base + off + 2])[0]
            if (pid, eid) in ((3, 10), (0, 4)) and fmt == 12:
                mejor = (fmt, base + off)
                break
            if (pid, eid) in ((3, 1), (0, 3)) and fmt == 4 and not mejor:
                mejor = (fmt, base + off)
        out = {}
        if not mejor:
            return out
        fmt, o = mejor
        if fmt == 4:
            seg = struct.unpack(">H", d[o + 6:o + 8])[0] // 2
            ends = struct.unpack(f">{seg}H", d[o + 14:o + 14 + 2 * seg])
            p = o + 16 + 2 * seg
            starts = struct.unpack(f">{seg}H", d[p:p + 2 * seg])
            deltas = struct.unpack(f">{seg}h", d[p + 2 * seg:p + 4 * seg])
            pr = p + 4 * seg
            ranges = struct.unpack(f">{seg}H", d[pr:pr + 2 * seg])
            for k in range(seg):
                for c in range(starts[k], ends[k] + 1):
                    if c == 0xFFFF:
                        continue
                    if ranges[k] == 0:
                        g = (c + deltas[k]) & 0xFFFF
                    else:
                        q = pr + 2 * k + ranges[k] + 2 * (c - starts[k])
                        g = struct.unpack(">H", d[q:q + 2])[0]
                        g = (g + deltas[k]) & 0xFFFF if g else 0
                    out[c] = g
        else:
            ngr = struct.unpack(">I", d[o + 12:o + 16])[0]
            for k in range(ngr):
                a, b, g0 = struct.unpack(">III", d[o + 16 + 12 * k:o + 28 + 12 * k])
                for c in range(a, min(b, 0x2FFFF) + 1):
                    out[c] = g0 + c - a
        return out

    @staticmethod
    def _leer_nombres(d, base):
        cnt, sto = struct.unpack(">HH", d[base + 2:base + 6])
        out = {}
        for i in range(cnt):
            pid, eid, lid, nid, ln, off = struct.unpack(">HHHHHH", d[base + 6 + 12 * i:base + 18 + 12 * i])
            crudo = d[base + sto + off:base + sto + off + ln]
            if pid == 3 and lid in (0x409, 0x0C0A, 0x80A) or (pid == 0 and nid not in out):
                try:
                    out.setdefault(nid, crudo.decode("utf-16-be"))
                except UnicodeDecodeError:
                    pass
        return out

    def ancho(self, texto, tamano):
        """Ancho en píxeles de `texto` a `tamano` (tamaño de fuente en el sistema de 1080x1920)."""
        total = 0
        for ch in texto:
            g = self._cmap.get(ord(ch), 0)
            total += self._adv[min(g, len(self._adv) - 1)]
        # libass escala la fuente para que ascender+descender ocupen `tamano` píxeles
        alto_em = (self.ascender - self.descender) or self.upem
        return total * tamano / alto_em

    def alto(self, tamano):
        return tamano


@lru_cache(maxsize=None)
def cargar(archivo):
    return Fuente(ruta(archivo))


def disponibles():
    """[{archivo, nombre}] de las fuentes que trae la sala."""
    out = []
    for f in sorted([*CARPETA.glob("*.ttf"), *PROPIAS.glob("*.ttf"), *PROPIAS.glob("*.otf")], key=lambda x: x.name.lower()):
        try:
            # en la lista se ve sin el tamaño óptico de la fuente («DM Sans 9pt Black» → «DM Sans Black»); para armar
            # el video se sigue usando el nombre completo (cargar().nombre)
            nombre = " ".join(w for w in cargar(f.name).nombre.split() if not (w.lower().endswith("pt") and w[:-2].isdigit()))
            out.append({"archivo": f.name, "nombre": nombre, "propia": f.parent == PROPIAS})
        except Exception:
            pass
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for f in disponibles():
        fu = cargar(f["archivo"])
        print(f'{f["archivo"]:28} {f["nombre"]:24} "SUPLEVET"={fu.ancho("SUPLEVET", 100):.0f}px  ñÑáÉ={fu.ancho("ÑÁÉ", 100):.0f}px')
