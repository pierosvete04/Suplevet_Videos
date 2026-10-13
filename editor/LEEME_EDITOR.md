# Sala de edición Suplevet: guía para el editor

## Instalar (una sola vez)

1. Copia la carpeta `editor` a tu PC, por ejemplo en `C:\SalaSuplevet`. **No la pongas dentro de Drive**: crea archivos pesados que no hace falta sincronizar.
2. Instala **Google Drive para escritorio** y asegúrate de ver la carpeta compartida `SUPLEVET_VIDEOS`.
3. Doble clic en `instalar.bat`. Instala Python, ffmpeg y el transcriptor, y al final te pide la ruta de `SUPLEVET_VIDEOS` (cópiala de la barra del Explorador).

## Cada semana

1. Doble clic en `abrir_sala.bat`. Se abre la sala en el navegador.
2. Arriba, elige la **semana**.
3. Pestaña **Faltantes**: ahí ves qué piezas faltan grabar y qué imágenes de Google Flow faltan. Arrastra cada archivo a su cuadro; se guarda con el nombre correcto.
4. Botón **Procesar y armar semana**. Une hook + cuerpo + CTA, pone subtítulos, imágenes, sonidos, música y la portada de cada video.
5. Pestaña **Videos**: toca una portada para ver el video, cómo se ve en Instagram, TikTok o Shorts, y corregir el copy.
6. Pestaña **Piezas y subtítulos**:
   - **Subtítulos**: corrige palabras mal escritas.
   - **Marcas**: cambia en qué palabra entra una imagen, el estilo del subtítulo (centro, palabra sola, resaltado…) o el sonido.
   - La vista previa muestra los cambios al instante. Cuando te guste, **Guardar y re-armar sus videos**.
7. Pestaña **Publicar**: **Exportar por día** copia cada video con su portada y su copy a `SUPLEVET_VIDEOS/SEMANA_XX/listos/DIA_X`.
8. Lo que no se arregla desde la sala (música, volumen de un efecto, otro estilo) va en **Notas para Claude**.

## Sonidos y música

- Efectos: `biblioteca/sfx/<categoría>/`. Pon varias versiones en cada carpeta y la sala las va rotando. Los que dicen `PROVISIONAL_` son de prueba; reemplázalos por los buenos.
- Música: `biblioteca/musica/`.
