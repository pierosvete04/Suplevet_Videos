---
name: director-marca
description: Dirige la edición de una sesión de cualquier marca (la de perfil_marca.md) después de grabar - compara lo que realmente se dijo con el guion, informa qué cambió y replanifica sobre las palabras reales dónde van los sonidos, la música, las imágenes de Flow y los estilos de subtítulo. Úsala cuando la sala pida revisar lo grabado de una sesión y el equipo no tenga su propia skill de director.
---

# Director de edición (cualquier marca)

Después de grabar, lo dicho nunca es igual al guion: palabras cambiadas, frases cortadas, tomas extra. Tu trabajo es leer la transcripción y **rehacer el plan de edición sobre lo que realmente se dijo**. Lee antes `perfil_marca.md` (qué vende la marca, cómo se escribe su nombre, lo que se evita).

La sala ya procesó los clips y te deja un **dossier** (`dossier.json`) con, por pieza: el guion, lo dicho, los cambios, las `palabras` con su segundo exacto, la duración, las marcas actuales, la música, las palabras clave y las categorías de sonido. No corras comandos: escribe los archivos que te pide el pedido.

## 1. Informe «qué cambió» (corto, para el equipo) → `informe.md`

Por pieza, solo lo que importa:
- Frases que **no se dijeron** o se dijeron distinto, si cambian el sentido o rompen una regla del perfil (promesas que no se pueden hacer, palabras que la marca evita).
- Palabras que el transcriptor oyó mal (no las cuentes como cambio; si es el nombre de la marca o de un componente, dilo para corregir el subtítulo).
- Duración: hooks de más de 6 s o cuerpos de más de 35 s → sugiere recorte.
- Tomas que conviene repetir, con el texto exacto.
Al final, las decisiones (una línea cada una) y los prompts de Flow y de música que falten.

## 2. Plan de edición sobre las palabras reales → `plan.json`

Usa **solo palabras que están en `palabras`** (tal cual se dijeron; si se repiten, `"n"`).
- **Sonidos:** la sala ya pone solos impacto al inicio, riser antes del cuerpo, whoosh en cortes y al entrar imágenes, pop en palabras clave, ding en números, brillo cuando se dice la marca y notificación en el CTA. Agrega solo donde suma: `corte_silencio` 1 vez por video en la frase clave del cuerpo; `glitch` o `scratch` en un «no», «error», «deja de». Nunca más de un efecto cada ~1.5 s.
- **Subtítulo:** `palabra_sola` 1 por pieza como máximo; `centro_grande` en la frase que resume el beneficio; `resaltado` en cifras y nombres.
- **Música:** una pista por cuerpo; cambia solo en un giro real o en el CTA, máx. 2 cambios. Si no hay música, crea las pistas con su prompt siguiendo las reglas de música del perfil.
- **Imágenes de Flow:** en la palabra concreta que se puede mostrar, 1–2 s, nunca en el primer segundo del hook, siguiendo las reglas de imágenes del perfil y «Prompts limpios» de la skill `guionista-marca`.

```json
{
  "piezas": {
    "C1": {"musica": "MUS_A",
           "marcas": [{"palabra": "energía", "sub": "palabra_sola"},
                      {"palabra": "resultado", "sfx": "corte_silencio"}]},
    "C1_H3": {"marcas": [{"palabra": "cansado", "asset": "C1_H3_IMG1", "dur": 1.5, "modo": "completa"}]},
    "CTA": {"marcas": [{"palabra": "WhatsApp", "sub": "centro_grande"}]}
  },
  "musica": [{"id": "MUS_A", "uso": "C1", "desde": 0,
              "prompt": {"herramienta": "Google Flow Music", "prompt": "…", "evitar": "…", "duracion": "60 s"}}],
  "videos": {"C1-H1-CTA": {"portada": "Le *falta* algo"}}
}
```

Las marcas de una pieza **reemplazan** a las anteriores de esa pieza: incluye las que quieras conservar. La sala aplica el plan sola cuando terminas.

## No hagas

- No inventes palabras que no se dijeron para anclar marcas.
- No cambies el texto de los subtítulos salvo nombres mal transcritos.
- No borres ni renombres archivos.
