# Sound guide: which sound goes in each situation

The sala takes these sounds from your **SOUND FX** folder in Drive (mostly from the *Efectos de Sonido_Edwin Arenas* pack). It picks a different variant each time so they don't repeat, and cuts long sounds down so they don't cover the voice. It never puts two optional sounds less than 1.5 s apart.

## 1. Video structure (always automatic)

| Moment | Sound | Your folder |
|---|---|---|
| Start of a **Promesa** or **Producto** hook | Bass hit + shine | 05-BOOM (IMPACTO) + 03-ANIMACION LOGO |
| Start of a **Pregunta** hook | Short tension | 08-CINEMATICA |
| Start of a **Contra lo común** hook ("deja de…") | Record scratch | 25-MEMES/Turntable Scratch · 19-EXTRAS/Scratch |
| Start of a **Número** hook | Hit + ding on the number | 05-BOOM · 14-DING |
| Just before the body | Riser that ends right at the cut | 31-RISER · 11-CORPORATE RISER |
| Hook → body cut | Whoosh | 36-WHOOSH · 32-SWOSH |
| Body → CTA cut | Transition | 35-TRANSICION |
| CTA "escríbenos por WhatsApp" | Notification | 19-EXTRAS/Notification… |
| CTA "comenta VET" | Typing | 10-CLICK/Keyboard |
| "Suplevet" is said for the first time | Logo shine | 03-ANIMACION LOGO |
| A Flow image comes in | Short whoosh | 32-SWOSH |
| Large subtitle / single word | Hit | 05-BOOM |
| **Silence cut** (`corte_silencio`) | Music stops for half a second and comes back with a hit | 05-BOOM |
| Key word (calostro, defensas…) | Pop | 29-POP · FX CARTOON 2/Pop Sounds |
| Number said aloud | Ding | 14-DING |

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

1. Maximum one optional sound every 1.5 s.
2. No effect on top of the spoken key word: it goes just before it, or on the cut.
3. Effects stay under the voice; the music drops on its own when someone speaks.
4. Several versions per category, rotated across the week's videos.
