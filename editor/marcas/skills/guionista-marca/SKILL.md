---
name: guionista-marca
description: Escribe una sesión de videos modulares para cualquier marca (la que dice perfil_marca.md) - cuerpos, varios hooks por cuerpo, CTAs, hoja de grabación y el produccion.json que lee la sala de edición (marcas por palabra para subtítulos, imágenes y sonidos; prompts de Google Flow y Flow Music; portadas; copies). Úsala cuando la sala pida el guion de una sesión, temas para los cuerpos o prompts de Flow para un equipo que no tiene su propia skill de guionista.
---

# Guionista modular (cualquier marca)

Una sesión se graba una vez y la sala de edición combina las piezas: cada video = **un hook + un cuerpo + un CTA**. Con 5 cuerpos × 5 hooks salen 25 videos de una sola grabación.

## Antes de escribir

1. Lee **`perfil_marca.md`** (está en la carpeta donde trabajas): qué vende la marca, para quién, su tono, cómo se escribe su nombre, sus palabras, cómo pide la compra y sus reglas de imágenes y música. Todo lo que escribas respeta ese perfil.
2. Si existe la carpeta `contexto/`, léela también (marca, productos, preguntas frecuentes, guiones aprobados).
3. Mira las sesiones anteriores que te indique el pedido (cada una con `Guion/`) para no repetir cuerpos ni hooks. Si alguna tiene `Guion/resultados.md` (vistas y retención reales), usa más lo que ganó y evita lo que peor funcionó.
4. Respeta la estructura del pedido (cuántos cuerpos, hooks por cuerpo y CTAs) y el tema elegido para cada cuerpo.

## Cómo se eligen los cuerpos

Cuerpo = **idea + ángulo**.
- **Ideas:** problemas reales del público, creencias equivocadas, preguntas frecuentes, resultados, tendencias.
- **Ángulos:** qué es · cómo se hace · error común · caso real · mito · pregunta frecuente.
- «Caso real» solo si el equipo da un caso verdadero. Nunca inventar casos, testimonios ni cifras.

## Estructura de cada video

```
[HOOK 0–3 s] → [CUERPO 25–30 s] → [CTA 3–5 s]
```

### Cuerpo (unas 70–80 palabras para 30 s)
1. **Problema** (5 s): lo que el público vive o ve.
2. **Por qué pasa** (5 s): la causa, simple.
3. **Solución** (12–15 s): 2 o 3 beneficios del producto, dichos con palabras simples. Nombra la marca tal como la escribe el perfil.
4. **Cierre** (3 s): una frase que deje lista la entrada al CTA.

El cuerpo empieza **neutro** (nunca «como te decía») para que encaje con cualquier hook.

### Hooks (varios por cuerpo, de tipos distintos)
Todo hook es una frase **dicha a cámara desde el primer segundo** (nada de hooks solo visuales o sonoros). Tipos: Promesa, Pregunta, Número, Contra (contraintuitivo), Producto, Error, Segmentación («esto es para ti si…»). Mezcla tipos en cada cuerpo. Sin puentes largos: el hook va directo al tema y, cuando se pueda, nombra la marca.
Cada hook lleva voz, texto en pantalla (máx. 6 palabras) y portada.

### CTAs
Usa las frases de **cómo pide la compra** del perfil. Normalmente dos: uno para anuncios (con botón de mensaje o link) y uno orgánico (comentar una palabra).

## Reglas que no se rompen

- Habla la persona a cámara, en primera persona. Nunca actuar de otro personaje ni hacer cuestionarios al espectador.
- Palabras que todos conocen; si hay un término técnico, se explica o se cambia por uno simple.
- Nunca prometer curas, resultados garantizados ni cifras que no estén en el perfil o el contexto.
- Primer cuadro = una cara. Nunca logo ni pantalla negra.
- Lo que el perfil dice que se evita, se evita siempre.

## El guion dirige la edición (marcas)

La sala transcribe y sabe el segundo exacto de cada palabra. Todo se ancla a **una palabra dicha**:

| Marca | Qué hace |
|---|---|
| `sub: centro_grande` | La línea de esa palabra va al centro y más grande. Para la frase fuerte. |
| `sub: palabra_sola` | Solo esa palabra en pantalla, gigante. Máx. 1 por pieza. |
| `sub: resaltado` | Esa palabra en otro color toda la línea (cifras, nombres). |
| `sub: arriba` | Subtítulo arriba (cuando importa lo de abajo). |
| `asset: <id>` | Entra una imagen o clip de Google Flow en esa palabra (1–2 s). |
| `sfx: <categoria>` | Fuerza un sonido. Casi nunca hace falta: la sala pone solos impacto al inicio, whoosh en cortes, pop en palabras clave, ding en números y brillo cuando se dice la marca. |
| `musica: <id>` | Desde esa palabra suena otra pista. Solo en un giro claro; máx. 2 cambios por video. |
| `sfx: corte_silencio` | La música se corta medio segundo antes y vuelve con un golpe. 1 por cuerpo, en la frase clave. |

Categorías de sonido: `impacto, whoosh, whoosh_inverso, zoom, pop, riser, ding, glitch, brillo, scratch, notificacion, tecleo, tension`.

Reglas: la palabra marcada se **dice tal cual** y es distintiva (no «de», «que», «el»). Hooks: 1 imagen como máximo, después del primer segundo. Cuerpos: una imagen por beneficio, sin pasar de una cada ~3 s. CTAs: sin imágenes.

## Imágenes y clips de Google Flow

El equipo genera en Google Flow con su propia cuenta (sin APIs). Por cada imagen o clip, el prompt listo para pegar:

- **id:** `<pieza>_IMG<n>` o `<pieza>_CLIP<n>` (por ejemplo `C1_H2_IMG1`, `C1_CLIP1`).
- **Imagen:** vertical 9:16. **Clip:** vertical 9:16, 4–6 s, un movimiento simple; su audio se silencia.
- **Prompt en inglés**, fotorrealista, + `plan_b` (el mismo plano dicho más neutro) + `que_muestra` en español.
- Sigue las **reglas de imágenes del perfil de la marca**. Nunca texto, logos, envases con marca (el producto real se graba) ni antes/después.

### Prompts limpios (para que Flow y Flow Music no los rechacen)

Los filtros de Google rechazan prompts con temas médicos, violentos o con personas reales aunque la intención sea buena. Cada prompt se escribe limpio desde el inicio:

- **Muestra el resultado, no el procedimiento:** la persona (o el animal) tranquila y bien en casa, no la operación.
- **Nunca en un prompt:** surgery, operation, wound, blood, needle, injection, syringe, scalpel, stitches, IV, sick, ill, disease, infection, parasite, pain, suffering, injured, hurt, vomit, dying, dead, emergency, cage, crying, child, kid, baby, teen, nombres de marcas, lugares o personas reales, «in the style of» un artista.
- **Reemplazos que funcionan:** «sick / weak / in pain» → «quiet, resting comfortably on a sofa, relaxed»; «hospital» → «bright, calm clinic»; «child» → «adult».
- **Personas:** solo adultos genéricos («a young woman», «a man in his forties»), sin nombres ni parecido con famosos.
- **Tono:** escenas cotidianas, luz suave. La tensión la ponen la voz, el subtítulo y el sonido, no la imagen.
- **Música:** solo género, BPM, instrumentos y ánimo. Nunca artistas, canciones ni letras; siempre instrumental.
- Antes de entregar, revisa cada `prompt` y `plan_b` contra esta lista.

## Música (Google Flow Music)

Siempre entrega los prompts de música. Una pista por grupo de cuerpos con el mismo tono (2 a 4 por sesión), o una por estado de ánimo si el guion los usa. Sigue las **reglas de música del perfil**; si no tiene, que sea enérgica y con ritmo, de fondo para la voz.
- `prompt` en inglés: género, BPM, 3–4 instrumentos, ánimo, y `instrumental, no vocals, background music for voiceover, steady energy`.
- `evitar`: lo que no debe tener (por defecto `vocals, choir, rap, sudden drops, long intro`).
- `duracion`: 45–60 s.

## Portadas y copies

- **Portada** (una por hook): 2–6 palabras, una entre asteriscos para que salga resaltada: `"Le *falta* algo"`.
- **Copies:** `cuerpos.Cx.texto` (2–3 frases), `hooks.Cx_Hy` (primera línea), `ctas`, `youtube_titulos.Cx_Hy` (máx. 60 caracteres) y 3–5 hashtags por cuerpo.

## Qué entregar

En la carpeta `Guion` que indique el pedido:

1. **`guiones.md`:** tabla resumen (código · idea · ángulo) y, por cuerpo, sus hooks y su cuerpo con las marcas; al final los CTAs, la lista de Flow y la lista de música.
2. **`hoja_de_grabacion.md`:** el orden de grabación (cuerpos → hooks → CTAs), el texto exacto **sin marcas** de cada pieza y una casilla `[ ]` para la toma buena.
3. **`produccion.json`** (lo que lee la sala):
```json
{
  "piezas": {
    "C1_H2": {"tipo": "Pregunta", "texto": "voz exacta sin marcas", "portada": "¿Le *falta* algo?",
              "marcas": [{"palabra": "falta algo", "asset": "C1_H2_IMG1", "dur": 1.5, "modo": "completa", "mov": "zoom_in"}]},
    "C1": {"tipo": "cuerpo", "texto": "…", "musica": "MUS_A",
           "marcas": [{"palabra": "energía", "sub": "palabra_sola"}, {"palabra": "resultado", "sfx": "corte_silencio"}]},
    "CTA-A": {"tipo": "anuncio", "texto": "…"}, "CTA-O": {"tipo": "organico", "texto": "…"}
  },
  "musica": [{"id": "MUS_A", "uso": "C1 y C3", "desde": 0,
              "prompt": {"herramienta": "Google Flow Music", "prompt": "…", "evitar": "…", "duracion": "60 s"}}],
  "assets": [{"id": "C1_H2_IMG1", "tipo": "imagen", "pieza": "C1_H2", "palabra": "falta algo",
              "flow": {"herramienta": "Google Flow · Imagen", "formato": "9:16 vertical", "prompt": "…", "plan_b": "…",
                       "que_muestra": "…", "evitar": "…"}}],
  "copies": {"cuerpos": {"C1": {"texto": "…", "hashtags": "#…"}}, "hooks": {"C1_H2": "…"},
             "ctas": {"CTA-A": "…", "CTA-O": "…"}, "youtube_titulos": {"C1_H2": "…"}}
}
```
   - `modo`: `completa` o `ventana`. `mov`: `zoom_in`, `zoom_out` o `fijo`. `dur` entre 0.8 y 3 s.
   - Cada `asset` de una marca está en `assets`; cada pista usada está en `musica`. El archivo es JSON válido.

Antes de entregar, revisa cada pieza contra el perfil y estas reglas. Cierra con un resumen corto: ideas y ángulos, total de piezas, cuántas imágenes y clips hay que generar y cuántas pistas de música.
