# Sala de edición: guía para el editor

Es la sala de los videos. Los carruseles y posts se hacen en la **Sala de diseño** (otro programa); se publican con el **Publicador**.

## Instalar (una sola vez)

1. Entra a https://github.com/pierosvete04/Suplevet_Videos/releases/latest y descarga **`SalaSuplevet-vX.Y.Z.zip`**.
2. Descomprímelo en `C:\SalaSuplevet`. **No lo pongas dentro de Google Drive**: la sala crea archivos pesados que no hace falta sincronizar.
3. Abre **`SalaSuplevet.exe`**. Si Windows muestra "Windows protegió su PC", toca *Más información → Ejecutar de todas formas* (el programa no está firmado, es normal).
4. La primera vez descarga el transcriptor (unos 500 MB). Las siguientes veces abre al toque.
5. Instala **Google Drive para escritorio** y agrega a "Mi unidad" el acceso directo de la carpeta compartida `SUPLEVET_VIDEOS`. Luego avísale a Piero o a Claude para conectar la sala a esa carpeta.

## Actualizaciones

No tienes que hacer nada. Cuando hay una versión nueva, arriba aparece **"Hay una versión nueva de la sala"**. Toca **Actualizar ahora**: se descarga, la sala se reinicia sola y tus videos y ajustes se quedan donde estaban (carpeta `datos`).

## Cada semana

1. Abre `SalaSuplevet.exe`. La sala se abre en su propia ventana, como cualquier programa. Al cerrarla, la sala se apaga sola (si estaba armando o exportando, primero termina).
2. Arriba, elige la **semana**.
3. Pestaña **Faltantes**: qué piezas faltan grabar y qué imágenes de Google Flow faltan. Arrastra cada archivo a su cuadro; se guarda con el nombre correcto.
4. Botón **Procesar y armar semana**: une hook + cuerpo + CTA y pone subtítulos, imágenes, sonidos, música y la portada de cada video.
5. Pestaña **Videos**: toca una portada para ver el video, cómo se ve en Instagram, TikTok o Shorts, y corregir el copy.
6. Pestaña **Piezas y subtítulos**:
   - **Subtítulos**: corrige palabras mal escritas.
   - **Marcas**: cambia en qué palabra entra una imagen, el estilo del subtítulo o el sonido.
   - La vista previa muestra los cambios al instante. Cuando te guste: **Guardar y re-armar sus videos**.
7. Pestaña **Publicar**: **Exportar por día** deja cada video con su portada y su copy en `SUPLEVET_VIDEOS/SEMANA_XX/listos/DIA_X`. **Subir al panel** los manda a edicion.suplevet.pe para aprobarlos.
8. Lo que no se arregla desde la sala va en **Notas para Claude**.

## Sonidos y música

Están en Drive: `SUPLEVET_VIDEOS/_BIBLIOTECA/sfx/<categoría>/` y `_BIBLIOTECA/musica/`. Pon varias versiones en cada carpeta y la sala las va rotando.
