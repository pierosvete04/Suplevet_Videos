# Sound guide: which sound goes in each situation

The sala takes these sounds from your **SOUND FX** folder in Drive (mostly from the *Efectos de Sonido_Edwin Arenas* pack). It picks a different variant each time so they don't repeat, and cuts long sounds down so they don't cover the voice. It never puts two optional sounds less than 1.5 s apart.

## 1. Video structure (always automatic)

| Moment | Sound | Your folder |
|---|---|---|
| Start of a **Promesa**, **Pregunta** or **Número** hook | One fast whoosh (sounds under 0.7 s) | 36-WHOOSH · 32-SWOSH |
| Start of a **Producto** hook | Shine | 03-ANIMACION LOGO |
| Start of a **Contra lo común** hook ("deja de…", "…no es la solución") | "Fahhh" to grab attention | FX SUPLEVET/ATENCION |
| Riser | Only when you ask for it (a mark in the script or in the editor); never automatic | 31-RISER · 11-CORPORATE RISER |
| Hook that ends showing the product ("…sería esto") | Magic reveal on "esto" (it is also the step into the body) | 35-TRANSICION/Magic Spell · 03-ANIMACION LOGO |
| Hook → body cut (other hooks) | Fast whoosh (under 0.7 s; the slow-motion ones are skipped) | 36-WHOOSH · 32-SWOSH |
| Body → CTA cut | Camera shutter + short white flash | 07-CAMARA |
| Each zoom (punch or keyframes that zoom in/out) | Short whoosh | 32-SWOSH |
| CTA that asks to write on WhatsApp (detected from what is said) | Notification | 19-EXTRAS/Notification… |
| CTA that asks to comment (detected from what is said) | Typing | 10-CLICK/Keyboard |
| "Suplevet" is said for the first time | Logo shine | 03-ANIMACION LOGO |
| A Flow image comes in | Short whoosh | 32-SWOSH |
| Large subtitle / single word | Hit | 05-BOOM |
| **Silence cut** (`corte_silencio`) | Music stops for half a second and comes back with a hit | 05-BOOM |
| Key word highlighted in the subtitle (calostro, defensas…) | Click | sala click (built in) · falls back to pop |
| Number said aloud | Ding | 14-DING |

Based on the 5 uses recommended by @comoeditarvideos (TikTok): Fahhh to grab attention, magic reveal to reveal, whoosh for zoom, click to highlight text, camera shutter for transitions.

## 2. Everyday situations (automatic when that word is said)

| When this is said… | Sound | Folder |
|---|---|---|
| ingresos, venta, ganancia, precio, negocio, rentable | Cash register | 13-DINERO · 27-MONEDA |
| estudio, porcentaje, comprobado, ciencia, dato | Data / processing | 12-DATA |
| error, mito, falso, nunca | Soft "wrong" | 16-ERROR · FX CARTOON 2/Cartoon Fail |
| ficha, ficha técnica, receta, información | Page turning | 28-PAPEL |
| cucharada, comida, agua, mezcla, plato | Liquid / pour | 24-LIQUIDO |
| tiempo, tarde, años, edad, días | Clock | 02-ALARMA/Clock Ticking |
| energía, vitalidad, fuerza, activo | Electric | 15-ELECTRICO |
| mira, fíjate, foto | Camera | 07-CAMARA |
| *(owners only)* salta, juega, corre, feliz | Boing | FX CARTOON 2/Boing · Toing |
| *(owners only)* resultados, lo logramos | Applause | 19-EXTRAS/Aplausos · Crowd Cheer |

The word lists can be edited in `config.json` → `sfx_palabras`.

## 3. On request (a mark in the script or in the sala's editor)

| Use | Category | Folder |
|---|---|---|
| Big text coming in hard | `golpe_texto` | 26-METAL SLICE |
| Big reveal (long riser) | `riser_largo` | 09-CINEMATICA RISER |
| Drama / serious problem | `drama` | 34-TERROS Y DRAMA |
| Glitch on a "no" | `glitch` | 22-GLITCH |

## 4. What we don't use (it doesn't fit the brand)

- **25-MEMES, 23-GRITOS, 18-EXPRESIONES**: insults, shouting and memes; they could offend a vet or a pet owner. (There are files in the trash, `.trashed-…`, inside those folders.)
- **33-TERROR, 17-EXPLOSION, 20-FUEGO, 01-AGUA (LLUVIA)**: they don't fit a nutrition product.
- **FX SERIES (Volume 1–10)**: good for specific Foley (animals, home), only when the script asks for it.

## Rules so it doesn't get tiring

0. One sound to open the hook. No riser under a hook that is spoken straight through: it covers the voice and piles on top of the hit and the whoosh. If a cut removes the silence where a structure sound (hit, whoosh, CTA) was, the sound moves to where the video continues instead of being lost.

1. Maximum one optional sound every 1.5 s.
2. No effect on top of the spoken key word: it goes just before it, or on the cut.
3. Effects stay under the voice; the music drops on its own when someone speaks.
4. Several versions per category, rotated across the week's videos.
