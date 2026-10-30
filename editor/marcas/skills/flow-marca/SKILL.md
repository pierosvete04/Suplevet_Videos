---
name: flow-marca
description: Genera con Claude in Chrome las imágenes y clips de Google Flow y las pistas de Google Flow Music que pide el guion de una sesión, en la cuenta del equipo y sin APIs. Úsala cuando la sala pida generar lo que falta y el equipo no tenga su propia skill de Flow. No escribe guiones (eso es guionista-marca).
---

# Google Flow y Flow Music con Claude in Chrome

El equipo genera las imágenes, clips y música en su propia cuenta de Google, en Chrome, sin APIs de pago.

## Antes de empezar

1. Los prompts salen del pedido de la sala (o del `produccion.json` de la sesión): por cada asset `id`, `tipo` (imagen o clip), `flow.prompt`, `flow.plan_b`, `flow.formato`, `flow.evitar`; por cada pista `id`, `prompt.prompt`, `prompt.evitar`, `prompt.duracion`.
2. Solo lo que falta: no vuelvas a generar lo que la sala ya tiene.
3. Revisa cada prompt contra «Prompts limpios» de la skill `guionista-marca` y contra las reglas de imágenes de `perfil_marca.md`. Si uno no cumple, usa su `plan_b` o reescríbelo, y dilo al final.
4. Carga las herramientas de Chrome en una sola llamada (tabs, navigate, computer, read_page, find) y trabaja en una pestaña nueva.

## Google Flow (imágenes y clips)

1. Abre **labs.google/flow**. Si pide iniciar sesión, elegir cuenta, aceptar términos o un captcha: **para y pídele a la persona que lo haga**. No escribas contraseñas ni aceptes condiciones.
2. Entra al proyecto con el nombre de la sesión; si no existe, créalo con ese nombre.
3. Formato **vertical 9:16** (o el que diga `formato`). Imagen para `tipo: imagen`; video (Veo) para `tipo: clip`.
4. Se pueden pedir varias de una vez en un solo texto, **cada una con su código** («Name each image starting exactly with its code… C1_IMG1: …»).
5. Elige la más fiel a `que_muestra` (sin texto, sin logos, sin deformaciones), descárgala y renómbrala con el `id` exacto (`C1_H2_IMG1.png`, `C1_CLIP1.mp4`).
6. Ubica los botones leyendo la página cada vez (read_page / find): Google cambia el diseño seguido.

## Google Flow Music

1. Desde flow.google.com, botón «Flow Music» (se abre aparte). Misma regla: si pide sesión o términos, lo hace la persona.
2. Por cada pista: el prompt empieza con «Instrumental only, no vocals, no lyrics.» y sigue con `prompt.prompt`.
3. Elige la versión sin voces ni cortes bruscos, descárgala en MP3 y renómbrala con el `id` (`MUS_A.mp3`).

## Si Google rechaza un prompt

1. Reenvía una vez el mismo prompt.
2. Si vuelve a rechazarlo, usa `plan_b`.
3. Si también lo rechaza, anótalo con el mensaje de Google y sigue con el siguiente.
Nunca intentes engañar al filtro (faltas a propósito, palabras partidas, sinónimos para colar algo).

## Límites

- Usa la cuenta y el plan tal como están: no compres créditos ni cambies ajustes de la cuenta.
- No generes nada que no esté en el pedido. Si aparece un límite de uso, para y avisa cuántos faltan.

## Al terminar

Deja todo en Descargas con su `id` como nombre (la sala lo busca y lo pone sola) y entrega un resumen corto: cuántos se generaron, cuáles con `plan_b` y cuáles quedaron pendientes y por qué.
