Versión 1.40.0 (se actualiza desde la app con «Actualizar ahora»):
- **Editor más claro**: los botones van en grupos (Reproducir · Deshacer · Cortar · Imagen y zoom · Ver) y cada uno muestra su tecla. Botones nuevos de **Deshacer y Rehacer**, zoom de la línea de tiempo con −, + y «Ajustar», y la ventana **«Teclas»** (también con la tecla ?). El tiempo muestra el número de cuadro.
- Arriba del editor se ve siempre **«● Cambios sin guardar»** y el botón 💾 para guardar, sin bajar hasta el final.
- **Deshacer (Ctrl+Z) ahora deshace los arrastres** (bordes, fotogramas de zoom, imágenes, voz) y los cambios de subtítulos.
- **Motor de cuerpo**: lo que editas en el cuerpo vale para todos sus videos (C1 con H1, H2, H3…) y ahora el editor muestra cuáles son. Nuevo **«↻ Cambiar la toma»**: otra grabación para una frase, el hook o el CTA, y todos sus videos se rearman con los mismos hooks y CTA (las imágenes y sonidos puestos en palabras se quedan). Nuevo **«⑂ Nueva versión del cuerpo»**: otra versión con los mismos hooks y CTA, editada aparte (C1-H1-…-B) o con otra toma completa; los videos de siempre no cambian.
- **Exportar**: el cuadro para elegir carpeta sale delante de la sala y también se puede pegar la ruta. Revisa que la carpeta exista y deje guardar, y si ya exportaste hoy ahí pregunta si se crea otra carpeta o se reemplaza. Cancelar ya no deja un video cortado.
- **Lo que no guardaste ya no se borra solo**: en Guion (la revisión de cada 90 s), en Piezas y al terminar un rearmado (ya no se abre otro video encima del que estás editando).
- **Guardar con la sala ocupada ya no da error**: queda en cola y se rearma cuando termine. No se puede cambiar de sesión mientras la sala trabaja.
- **Subidas seguras**: si una subida se corta no se toca el archivo bueno; lo reemplazado queda en _REEMPLAZADOS (también en Drive, así no vuelve la versión vieja). Si una subida a Drive falla, sale un aviso con «Reintentar».
- **Guion**: subir o reimportar un Word ya no borra las marcas del guion de Claude ni lo corregido en la sala. Entiende mejor «C3.1 HASTA C3.4», «CTAs: …», listas y tablas de Word y los .txt con tildes del Bloc de notas.
- **Subtítulos**: al corregir una palabra se conservan los tiempos de las demás y las marcas siguen a su palabra. Cambiar la toma de una pieza ya no borra sus marcas.
- **Sonidos y plantillas**: editar un sonido deja «⟳ rearmar» a sus videos y suena entero; la vista previa suena igual de fuerte que en el video. Borrar una plantilla dice cuántos videos la usan y ofrece rearmarlos.
- Arreglos menores: doble clic ya no manda dos pedidos, la búsqueda de sesiones encuentra nombres con tildes, una sesión nueva ya no traba «Escribir guion con Claude», y más.

Versión 1.39.0 (se actualiza desde la app con «Actualizar ahora»):
- **Exportar pregunta antes de empezar**: primero «¿Quieres llevar estos videos al Drive?» (Sí / No) y luego «¿A qué carpeta de tu PC los exportas?», con el cuadro de Windows para elegirla (recuerda la última que usaste).
- **Siempre se exporta dentro de una carpeta** con la fecha y el nombre de la sesión (por ejemplo «23-10-2026 P05 VETERINARIOS»), aunque sea un solo video. Los días (DIA_1…) y «por partes» (POR_PARTES) van dentro de esa carpeta.
- Con **Sí al Drive**, la carpeta queda en tu PC y además se copia a V.FINALES de la sesión y se sube. Con **No**, queda solo en tu PC. Al terminar se abre sola en el explorador.
- «Subir finales existentes al panel» también encuentra los videos que están dentro de esas carpetas.

Versión 1.38.1 (se actualiza desde la app con «Actualizar ahora»):
- **El cabezal y los clics ya caen donde deben**: con pedazos cortos, el cabezal, la regla y los clics en la línea de tiempo se corrían respecto de lo dibujado (hasta unos 8 s con la línea de tiempo alejada). Tocar la regla te llevaba a otro segundo y el zoom con el touchpad movía lo que estaba bajo el cursor. Ahora todo usa la misma medida.
- Al **dividir (S) un pedazo duplicado o desagrupado**, la copia seguía con solo una mitad y solo una mitad quedaba desagrupada; ahora las dos mitades siguen igual.
- La **copia de un pedazo desagrupado** se ve y suena en la vista previa igual que en el video armado (con la voz corrida, quitada o la imagen congelada de su original).
- «Quitar pausas» tocado dos veces ya no deja cortes repetidos (con uno repetido, «Volver a poner» no parecía funcionar).

Versión 1.38.0 (se actualiza desde la app con «Actualizar ahora»):
- **Clic derecho en la línea de tiempo** (como CapCut): en un bloque de video, Dividir en el cabezal, Duplicar, Desagrupar o agrupar el audio, Borrar y Borrar la frase entera; en la voz, quitarla o correrla.
- **Audio y video agrupados o por separado con Ctrl+G**: desagrupado, la voz del pedazo se elige aparte en la pista Voz y se **arrastra para correrla** antes o después de su imagen (cortes en J y en L, hasta 3 s); Supr en la voz la quita (el pedazo se ve mudo) y Supr en el video quita la imagen (queda su primer cuadro congelado mientras suena la voz). Ctrl+G otra vez los vuelve a juntar.
- **Duplicar (Ctrl+D)**: el pedazo se repite justo después; la copia se borra con Supr.
- **Deshacer y rehacer (Ctrl+Z / Ctrl+Y)** en el editor; un arrastre entero cuenta como un solo paso.
- La vista previa ya muestra todo eso: la voz quitada, la imagen congelada y la voz corrida sonando encima de lo de al lado.
- Arreglos: el botón «🗑 Quitar pedazo» quitaba la frase entera (desde la 1.37); al pasar de un pedazo al siguiente sin nada quitado en medio ya no salta (menos trabas); si pausas mientras carga la pieza siguiente, ya no arranca sola.

Versión 1.37.0 (se actualiza desde la app con «Actualizar ahora»):
- **Armar es unas 3 veces más rápido**: la sala comprime con la tarjeta de video si la PC tiene una (NVIDIA, Intel o AMD; si falla, sigue sola con el procesador) y arma 2 videos a la vez en PCs con 6 núcleos o más. En la prueba, 4 videos pasaron de 115 s a 33 s. El video armado pesa un poco más, pero al exportar se vuelve a comprimir con la calidad final.
- **El editor ya no se traba en cada corte**: las piezas tenían un cuadro clave cada 3–8 s y cada salto tenía que leer segundos de video; ahora tienen uno cada medio segundo (los saltos pasan de ~190 ms a ~15 ms). Las piezas ya preparadas se ajustan solas una vez la próxima vez que toques «Armar todos los videos» (un par de minutos; los subtítulos y los cortes no cambian).
- **Frase dicha dos veces**: cuando repites una frase seguida («vitaminas y minerales… vitaminas y minerales»), la transcripción solo escribe una. Ahora la sala lo nota (voz sin palabras, después de una pausa, justo antes de la frase) y corta sola la primera vez: queda la última. Sale como «Dicho dos veces» en Cortes por voz.
- **Quitar una frase entera** desde la línea de tiempo aunque esté partida en pedazos: «Incluir la frase entera» en el panel del pedazo, o **Shift + Supr**.
- En el editor, la barra de botones (reproducir, cortar, quitar, fotograma clave, imagen…) **se queda siempre arriba** al bajar a los cortes por voz y la línea de tiempo.
- **Copias de seguridad de tus cambios** (menú de tu cuenta → Copias de seguridad): cada pocos minutos, si algo cambió, la sala guarda una copia de los subtítulos, cortes, zooms, versiones, portadas, copies, guion y ajustes de la sesión, sin los videos (unos 10–50 KB cada una), también en Drive. «Guardar copia ahora» para ponerle nombre y «Volver a esta» para restaurar (antes guarda cómo estaba, por si te arrepientes). No guarda llaves ni contraseñas.

Versión 1.36.1 (se actualiza desde la app con «Actualizar ahora»):
- **Cabecera nueva**: marca, sesión y botones en una sola fila que se acomoda al ancho de la ventana (en laptop pasa a dos filas ordenadas; en pantallas chicas los botones se acortan). Nada se sale de la pantalla.
- El menú de la cuenta **cuelga de tu cuenta** (con un piquito que la señala), ya no flota en la esquina, y es sólido: ya no se ve la página de atrás. Ahí también están «Abrir el teleprompter» y el cambio a modo claro u oscuro.
- **Plantillas** tiene dos páginas: «Estilo de edición» y «Subtítulos». El estilo de edición ahora es una lista vertical ordenada en Cámara, Imagen, Cortes y Sonido, con los ajustes de cada opción debajo de ella.
- Arreglos en Plantillas: los cambios sin guardar de una plantilla ya no se borran al cambiar de pestaña o subir una fuente; al elegir otra plantilla te pregunta antes de perderlos; el título avisa «Sin guardar»; «Duplicar» copia lo que estás viendo y ya no pisa otra plantilla con el mismo nombre; un arrastre interrumpido en el lienzo ya no se queda pegado; la animación de muestra se para al salir de la página.

Versión 1.36.0 (se actualiza desde la app con «Actualizar ahora»):
- **Retoques como en CapCut**, en Plantillas → Estilo de edición (se aplican al armar; cada edición B, C… puede tener los suyos):
  - **Voz limpia** con 3 niveles (Suave, Media, Fuerte): quita el ruido de fondo, suaviza las «s» fuertes y deja la voz pareja. Viene prendida en Suave.
  - **Seguir la cara**: el zoom se centra en la cara aunque la persona se mueva. La sala busca la cara en cada pieza una sola vez y lo recuerda. Viene prendido.
  - **Color (LUT)**: sube un LUT .cube o .3dl y elige su intensidad (Suave, Media, Completa). Pinta lo grabado y la portada; las imágenes de Flow y los subtítulos no se tocan.
  - **Efecto de imagen**: viñeta, grano de película o los dos.
  - **Transiciones**: destello, zoom, sacudida, glitch o desenfoque; solo al entrar el CTA (como antes), entre hook, cuerpo y CTA, o también entre frases.
- Los videos ya armados no cambian solos: tócalos con «Armar todos los videos» cuando quieras verlos con lo nuevo.

Versión 1.35.2 (se actualiza desde la app con «Actualizar ahora»):
- Al abrir Notificaciones, Procesos o cualquier ventana, la sala ya no salta: la barra de arriba se queda en su sitio y nada se corre de lado.
- La tuerca ⚙ se fue: ahora tocas **tu cuenta** (arriba a la derecha) y sale un menú con Cuenta, Conexiones, Google Drive, Teleprompter, Actualizaciones y Notas. Cada una es **su propia página**, ya no una página larga con índice.
- En el detalle de un video, si cierras con ✕ o Esc y hay algo sin guardar (cortes del editor, copy, portada o sonidos sin rearmar), la sala te pregunta antes de perderlo.
- Si abrías un video y enseguida otro, el editor podía quedarse con las piezas del primero. Ya no.
- «Ver portada actual» ahora sí deja el formulario como está guardado (antes cambiaba la imagen pero los cambios seguían ahí y se podían guardar sin querer).
- «Copiar copy de esta red» avisa si no pudo copiar.

Versión 1.35.1 (se actualiza desde la app con «Actualizar ahora»):
- En 16:9, la imagen «En ventana» ahora entra a la derecha y no al centro: quien habla se para en el tercio izquierdo (como en YouTube) y la ventana va del lado libre, sin taparle la cara. Se puede seguir arrastrando a donde quieras.

Versión 1.35.0 (se actualiza desde la app con «Actualizar ahora»):
- **Videos horizontales 16:9**: arriba, junto a la sesión, ahora eliges «Vertical 9:16» u «Horizontal 16:9». En 16:9 la sesión arma videos de 1920×1080 para la web, YouTube o los correos: las piezas, las imágenes de Flow, el zoom, la portada y la exportación salen horizontales, y las vistas previas de la sala también.
- Las plantillas de subtítulos sirven para los dos formatos: en 16:9 se adaptan solas (letra un poco más chica y el subtítulo más cerca del borde de abajo, porque no hay botones de TikTok que esquivar).
- Al cambiar el formato, la sala vuelve a preparar las piezas con el tamaño nuevo y los videos quedan «por re-armar». Los subtítulos corregidos no se pierden. El guion también puede pedirlo con «formato»: "horizontal".
- En 16:9 no salen los avisos de «más de 60 s» ni de «subtítulo muy abajo», que son de Reels y TikTok.

Versión 1.34.0 (se actualiza desde la app con «Actualizar ahora»):
- **Dos ángulos grabados por pasadas**: si grabas un guion completo en un ángulo y después completo en otro, al «Analizar y renombrar todo» la sala lo nota por el orden de grabación (la fecha que guarda el celular, aunque sean dos celulares): la primera pasada es la de frente y la segunda el «Ángulo 2». Antes se quedaba con una sola toma y perdía el otro ángulo.
- Cada pasada arma sus propios videos (C1-H1-CTA1 y C1-H1-CTA1-A2). Si en el ángulo 2 falta un hook, ese video usa el hook de frente. Las tomas del ángulo 2 quedan en Clips/C1/Ángulo 2/.
- Una frase que repites enseguida, o al final para corregirla, sigue siendo otra toma, no otro ángulo.
- Arreglo: los clips dentro de subcarpetas de Clips/ (por ejemplo, «Clips/Clips iphone de mi papa/») no se veían. Ahora se leen como los demás.

Versión 1.33.1 (se actualiza desde la app con «Actualizar ahora»):
- **Altura del subtítulo en el hook y el CTA**: en «Subtítulos de este video» ahora hay tres alturas: cuerpo, hook y CTA. El hook y el CTA vienen «igual que el cuerpo»; quita esa casilla (o arrastra el subtítulo en el editor mientras se ve el hook o el CTA) para darles su propia altura. Solo cambia ese video (o los del cuerpo, si marcas «Igual en todos los videos»).
- Arreglo: en los hooks con imagen a pantalla completa, el subtítulo se quedaba al centro (encima de la cara) toda la frase, aunque la imagen ya se hubiera ido, y cambiar la plantilla no lo movía. Ahora va donde dice la plantilla y solo sube al centro mientras se ve la imagen, igual que en la vista previa.
- Arreglo: la música que rota entre videos ya no cambia según armes un video solo, todos juntos o al exportar: cada video tiene siempre la misma.
- Arreglo: al guardar una plantilla, la sala ofrece rearmar también los videos de las ediciones B, C… que la usan; y elegir otra predeterminada ahora gana a la que fija el guion.

Versión 1.33.0 (se actualiza desde la app con «Actualizar ahora»):
- **Generar en Flow con Chrome**: en Faltantes, «✦ Generar lo que falta». Claude abre tu Chrome (con la extensión de Claude), genera en Google Flow las imágenes y en Flow Music la música que faltan, y las guarda en su cuadro con su nombre. Después solo rearmas los videos.
- Si el guion no tiene música (por ejemplo, uno armado con lo grabado), Claude primero escribe sus prompts: de 2 a 4 pistas instrumentales según el tono de cada cuerpo, y qué pista usa cada uno.
- Si Chrome está cerrado, la sala lo abre. Si Google pide entrar, un captcha o se acabó el cupo, se detiene y te dice qué falta. Usa los créditos de tu plan de Flow, sin APIs de pago.

Versión 1.32.0 (se actualiza desde la app con «Actualizar ahora»):
- **Rehacer imagen con una nota**: en Faltantes, debajo de cada imagen o clip de Flow, «✎ Rehacer imagen con una nota». Escribes qué está mal («tiene arándanos, que se vea el polvo encima de las croquetas») y Claude escribe el prompt nuevo con tu nota y las reglas de la marca. Queda guardado en el guion; lo copias, generas en Flow y arrastras la imagen nueva a su cuadro.
- La nota queda guardada debajo de la imagen, con la fecha. Si la rehaces otra vez, Claude respeta también las notas anteriores.
- **La comida es la real**: si una imagen muestra un plato, son croquetas o comida simple con el polvo encima, nunca frutas, salmón ni verduras.

Versión 1.31.0 (se actualiza desde la app con «Actualizar ahora»):
- **Crear prompts de Flow**: si el guion no tiene prompts de imágenes (por ejemplo, uno armado con lo grabado), la pestaña Guion lo avisa y Claude los escribe a partir del texto de cada cuerpo y hook, sin cambiar el texto. Después se generan en Flow y aparecen en Faltantes.
- En cada imagen sale una mascota mostrando lo que dice la frase: decaída si habla de una mascota mal, contenta si habla del resultado. Nunca células ni órganos.

Versión 1.30.0 (se actualiza desde la app con «Actualizar ahora»):
- La **Configuración** tiene el mismo diseño en las tres apps (menú a la izquierda, secciones a la derecha), con **Cuenta y dispositivos** y **Buscar actualizaciones**.

Versión 1.29.1 (se actualiza desde la app con «Actualizar ahora»):
- Arreglo: «Iniciar sesión en Claude» no encontraba la app de escritorio de Claude (movió su Claude Code a otra carpeta).

Versión 1.29.0 (se actualiza desde la app con «Actualizar ahora»):
- **Entrar con Google**: al abrir, si no hay sesión, aparece la pantalla «Entrar con Google» (con el link de la carpeta del proyecto). La sesión queda abierta hasta que alguien toque «Cerrar sesión», y es una sola para las tres apps de la PC: si entras en una, ya entraste en todas.
- Arriba a la derecha está tu cuenta (foto y nombre). Ahí: **Dispositivos y seguridad**: las PCs donde se abrió tu cuenta, con qué apps, la primera y la última vez, las nuevas marcadas, y «Cerrar sesión ahí» o «Cerrar sesión en todos los demás» (esa PC la cierra sola en máximo 10 minutos).
- El candado de verdad sigue siendo Drive: solo entra quien tenga compartida la carpeta del proyecto.

Versión 1.28.2 (se actualiza desde la app con «Actualizar ahora»):
- La sala abre más rápido: la ventana aparece al instante con «Abriendo…» y la app carga apenas está lista (antes no se veía nada hasta que todo arrancaba). Para tenerlo hay que pasar una vez el instalador completo de esta versión; con «Actualizar ahora» todo sigue funcionando igual que antes.

Versión 1.28.1 (se actualiza desde la app con «Actualizar ahora»):
- Ícono nuevo: una **E** azul, de la misma familia que la Sala de diseño (D) y el Publicador (P), sin el logo de Suplevet. Para ver el ícono nuevo en el escritorio hay que pasar una vez el instalador completo (SalaSuplevet-Instalador-v1.28.1.exe); «Actualizar ahora» cambia solo la letra de adentro.

Versión 1.28.0 (se actualiza desde la app con «Actualizar ahora»):
- Vuelve a llamarse **Sala de edición** y es solo para videos.
- Los carruseles y posts pasan a la **Sala de diseño**, un programa aparte con su propio instalador (github.com/pierosvete04/Suplevet_Diseno). Se publican igual con el Publicador.

Versión 1.27.1 (se actualiza desde la app con «Actualizar ahora»):
- Actualizaciones más tercas: si la conexión con GitHub se corta (pasa a ratos con algunas redes), la sala reintenta sola y, si la ruta normal no responde, prueba por la página de descargas de GitHub. Si aun así no puede, el aviso dice «Sin conexión con GitHub» (antes decía «Sin internet» aunque hubiera internet) y el próximo «Buscar actualizaciones» vuelve a preguntar de verdad (antes el error quedaba guardado una hora).

Versión 1.27.0 (se actualiza desde la app con «Actualizar ahora»):
- Ángulos: el mismo guion grabado desde otro lado arma sus propios videos. Lo de frente va como siempre (C1/C1.1, C1/C1-H1); el otro ángulo, en una carpeta con su nombre: C1/IZQUIERDA/C1.1 (o IZQUIERDA/C1/C1.1). Se reconocen IZQUIERDA, DERECHA, LADO, ARRIBA, ABAJO, PRIMER PLANO y CAM 2, ÁNGULO 3…
- Cada ángulo es su propia pieza (C1 · Izquierda) y sus videos llevan el ángulo al final: C1-H1-CTA1-IZQ. Si en ese ángulo grabaste los hooks o el CTA, usa esos; si no, los de frente (el mismo número de hook).
- Antes, una toma en C1/IZQUIERDA/C1.1 reemplazaba en silencio a la C1.1 de frente. Ya no.
- Videos: filtro por ángulo (Todos · Frente · Izquierda…) y etiqueta «↔ Izquierda» en cada video. En el Plan de la sesión, cada ángulo es una fila: puedes armarlo también en la edición B.
- El ángulo usa el guion, el copy y el tipo de hook de la toma de frente.

Versión 1.26.0 (se actualiza desde la app con «Actualizar ahora»):
- Ediciones (A, B, C…): el mismo material editado de otra forma, sin grabar nada más. Videos → «🗂 Plan de la sesión» → «＋ Nueva edición». Cada edición tiene su zoom (en las frases, en el hook, tipo, cada cuánto e intensidad), su música (otra distinta a la A, la misma, o una pista elegida), su plantilla de subtítulos y su ritmo.
- En la tabla del plan marcas en qué ediciones sale cada cuerpo. La A siempre se arma y sus videos conservan el código de siempre (C1-H1-CTA1); los de la B llevan la letra al final (C1-H1-CTA1-B). «Guardar y armar lo que falta» arma solo lo nuevo.
- En el editor, arriba se ve en qué edición estás. Los cortes, el orden de las frases y los sonidos que cambies en la B quedan solo en la B; mientras no toques una pieza en la B, usa lo de la A.
- Videos: filtro por edición (Todas · A · B…) y una etiqueta «Ed. B» en cada video de otra edición.

Versión 1.25.0 (se actualiza desde la app con «Actualizar ahora»):
- La sala ahora se llama **Estudio Suplevet**: ya no es solo para videos. Los datos, la carpeta y las actualizaciones siguen igual.
- Nueva pestaña **Carruseles**: arma carruseles de 1080×1350 dentro del Estudio. Arrastra las fotos de Flow, tócalas para ponerlas en la lámina y con ✂ se les quita el fondo (sin internet, con el mismo modelo de rembg; la primera vez baja 170 MB). Encima van textos (con *palabra* resaltada), garabatos a mano (rayitas, ?!, flechas, corazón…), formas, la bolsa real y el logo. Se mueve, agranda y gira con el mouse.
- «Escribir con Claude» en Carruseles: escribe el texto de cada lámina, el prompt de Flow de cada foto y el copy.
- «Exportar a V.FINALES» deja L01.png, L02.png… y copy.txt en V.FINALES/<carrusel>: el Publicador ya lo toma como carrusel.
- Exportar espera a que carguen todas las fotos: ninguna lámina sale vacía.

Versión 1.24.0 (se actualiza desde la app con «Actualizar ahora»):
- CTA repetido: si un CTA grabado en partes trae en cada parte un llamado completo («Pídelo hoy, escríbenos por WhatsApp…» y «Comenta Suplevet…»), la sala ya no los pega uno detrás del otro: son dos CTA (CTA1 y CTA1B) y cada video lleva uno solo, turnándose entre los hooks.
- El sonido del CTA sale de lo que se dice: notificación si pide escribir por WhatsApp, tecleo si pide comentar. Antes, con el guion sacado de lo grabado, el CTA se quedaba sin sonido.
- Arranque del hook con un whoosh rápido. En los cortes solo entran whoosh cortos (que suenan menos de 0,7 s): la biblioteca también trae whoosh en cámara lenta de varios segundos que arrastraban el corte. El riser ya no se pone solo (va donde lo pidas).
- Ritmo: los silencios se cortan desde 0,3 s (antes 0,5 s) y las frases que se dicen lento (menos de 6 sílabas por segundo) se aceleran un poco, hasta 12 %, sin cambiar el tono. Se mide frase por frase. Se apaga en «Cortes por voz» → «Acelerar un poco las frases lentas»; donde pongas una velocidad a mano, manda la tuya.
- Editor: «⏮ Desde el inicio» (tecla Inicio) vuelve al segundo 0 y reproduce.
- Editor: silenciar pistas. El parlante en «Voz» y «Sonidos» y el botón «🎵 Música» las apagan en este video: se oye así en la vista previa y el video se arma así.
- Sonidos con los 5 usos que recomienda @comoeditarvideos (TikTok):
  - «Fahhh» para llamar la atención: arranca los hooks «contra lo común» («Deja de…», «…no es la solución»). Si el guion no dice el tipo de hook, la sala lo deduce de lo que se dice.
  - Magic reveal para revelar: en el «esto» con que termina el hook («…sería esto») y hace de paso al cuerpo.
  - Whoosh corto en cada zoom (zoom alternado o fotogramas clave que acercan o alejan).
  - Click para remarcar: en las palabras clave que se resaltan en el subtítulo (antes era un pop).
  - Obturador de cámara con un destello blanco en la transición al CTA.
- Editor: cambiar el orden de las frases del cuerpo. Toca una frase y usa «◀ Mover frase antes / Mover frase después ▶» (o Alt + ← / →). Sus subtítulos, imágenes y encuadre van con ella; vale para todos los videos de ese cuerpo.

Versión 1.23.0 (se actualiza desde la app con «Actualizar ahora»):
- Cortes más finos: la sala mira la onda de la voz (no solo la transcripción, que estira la primera palabra sobre el silencio) y recorta el silencio del inicio y del final de cada parte del cuerpo, no solo de la primera y la última. Así no queda un hueco mudo donde se pega una toma con la siguiente (por ejemplo, el arranque de C1-1).
- Las pausas largas en medio también se cortan aunque la transcripción diga que ahí había una palabra.
- Frase repetida con otras palabras («Panza tranquila, mejor digestión y mejores popos. Panza tranquila y mejor digestión»): se corta la primera y queda la última toma, como con las demás repeticiones. Toca lo tachado para volver a ponerla.
- Tomas largas como «C3.1 HASTA C3.4»: la sala escucha la toma y la suelta; si la larga no dice la frase 1 (empieza en la 2), la toma suelta C3.1 entra y la larga pasa a ser la parte 2. Antes se perdía «Se llama Suplevet y su panza te lo agradecerá».
- Sonidos con más criterio: un solo efecto para arrancar el hook (antes impacto + brillo a la vez); el riser va solo en hooks de pregunta y solo en su último tramo (antes iba siempre, 2 segundos debajo de la voz); si un corte quita el silencio donde caía el golpe, el whoosh o el sonido del CTA, el efecto se corre a donde sigue el video en vez de perderse.
- Transcripción con el modelo más fino («medium») y más vocabulario de la marca: entiende «una cucharada», «te lo llevamos», «escupe», «mascota», «opaco». La primera vez baja el modelo (unos 1,5 GB) y transcribir tarda un poco más.
- Los subtítulos que salieron solos con el modelo anterior se rehacen solos al armar; los que alguien corrigió a mano no se tocan. Correcciones fijas en config (transcripcion.correcciones): «Suplebet», «su plebet» → Suplevet.
- Si el guion salió de lo grabado, se pone al día con las nuevas transcripciones (lo que corregiste a mano en el guion se queda).

Versión 1.22.0 (se actualiza desde la app con «Actualizar ahora»):
- Cortes por voz, como «editar por texto» de CapCut: la sala lee cada palabra que se dijo (con su tiempo) y la onda de la voz, y corta sola lo que sobra: lo repetido (queda la última vez que se dijo bien), los tartamudeos («pe- perro»), los «eh» y «mmm», las tomas que se cortaron con «perdón, otra vez» y los silencios largos.
- En el editor de cada video, el panel «🎙 Cortes por voz» muestra todo el texto: lo tachado en rojo se corta, lo ondulado es una sugerencia dudosa (una palabra que no está en el guion, o algo que suena sin palabras). Toca una palabra para ir ahí; con Shift eliges varias; «✂ Cortar» (o Supr) la quita y «↩ Volver a poner» la devuelve. Mientras suena, la palabra que se dice se marca.
- «Sin cortes / Con cortes» (tecla C): compara antes y después. Sin cortes se ve y se escucha todo lo grabado, con lo que se corta rayado en rojo en la línea de tiempo (y un aviso «✂ Esto se corta» en el video). Arrastra los bordes rojos para correr un corte.
- Lo seguro se corta solo en las piezas que nadie revisó todavía; al guardar desde el editor, manda lo que dejaste. Abajo del panel se elige desde cuántos segundos un silencio se corta (0,5 s recomendado) y se puede apagar el corte automático.
- La cuenta «3, 2, 1» que ya se quitaba sola ahora también se ve como corte en la línea de tiempo.
- Guion → «Subir guion»: sube el produccion.json de Claude o el guion en Word, .md o .txt (reemplaza al que hay y deja una copia). El guion que el equipo deja en la carpeta Guion o Guiones de Drive se lee solo.
- Sin guion: «Armar el guion con lo grabado» transcribe los clips y arma el guion con lo que se dijo.
- Al bajar de Drive se saltan las carpetas Bloopers (tomas apartadas).

Versión 1.21.8 (se actualiza desde la app con «Actualizar ahora»):
- Plantillas → Estilo de edición, con más opciones:
  - Zoom alternado configurable: tipo (salto, acercar, alejar o mezcla), intensidad (suave, media o fuerte) y cada cuánto (frase sí, frase no, o todas las frases).
  - Zoom en el hook (nuevo): se acerca despacio durante todo el hook, con intensidad suave, media o fuerte.
  - Fundido suave: ahora eliges el largo (rápido, normal o lento).
- Todo se guarda al tocarlo, y la sala avisa cuántos videos quedan por rearmar. Donde pongas un zoom a mano, manda el tuyo.

Versión 1.21.7 (se actualiza desde la app con «Actualizar ahora»):
- Plantillas → Estilo de edición: «Zoom alternado» y «Fundido suave» ahora son dos tarjetas con interruptor, una al lado de la otra. Se guardan al tocarlas (ya no hay botón «Guardar estilo») y la sala avisa qué videos quedan por rearmar.
- Exportar: la carpeta donde quedan los videos ya no se sale del recuadro. Se muestra corta («Drive › sesión › V.FINALES», la ruta completa al pasar el mouse) y hay un botón «Abrir carpeta».

Versión 1.21.6 (se actualiza desde la app con «Actualizar ahora»):
- Plantillas: tarjetas nuevas. La que estás editando lleva borde de color y un check; la que llevan los videos dice «En uso» sobre la vista previa (y «Pide el guion» si la sesión exige otra). Vista previa más grande, descripción en dos líneas, se elevan al pasar el mouse y se ven con el teclado.
- Textos de Plantillas más claros: «Guardar y usar en los videos» en vez de «predeterminada», y el estilo de edición explicado mejor.

Versión 1.21.5 (se actualiza desde la app con «Actualizar ahora»):
- Sonidos: sin el espacio vacío sobre el título; los avisos de la sala aparecen flotando abajo y ya no empujan la página.
- Ecualizador: los puntos de corte (LC y HC) se ven enteros, ya no quedan medio cortados en los bordes.
- La vista previa ahora sí suena más fuerte con volumen sobre 100 % (150 %, 200 %), en Sonidos y en el editor del video.
- Al cambiar el tamaño de la ventana, las ondas y la curva del ecualizador se redibujan bien.
- Las versiones de prueba de sonidos editados que nadie usa en una semana se borran solas, para no llenar el disco.

Versión 1.21.4 (se actualiza desde la app con «Actualizar ahora»):
- Editar sonido → «Sin silencios»: quita todos los silencios del sonido (al inicio, en medio y al final) y deja solo lo que suena. Compáralo con «▶ Original» y quédate con el que prefieras. La onda «Resultado» lo muestra más corto.
- Ecualizador: graves y agudos pueden ir en «Estante» (suben o bajan todo hacia ese extremo) o en «Campana» (solo alrededor de su frecuencia). La rueda del mouse sobre una campana la hace más ancha o más fina. La ayuda ya no tapa la curva.
- Textos de toda la sala revisados: etiquetas y estados con la primera letra en mayúscula («Guion listo», «Sin armar», «Aprobado»), «Claude listo» en vez de «conectado», «Drive conectado», «rearmar» sin guion, plurales correctos («1 cuerpo») y tipos de sonido sin mayúscula en cada palabra.

Versión 1.21.3 (se actualiza desde la app con «Actualizar ahora»):
- Teleprompter, «Seguir mi voz»: ya no salta de la nada. Sigue lo que vas leyendo en orden, solo hacia adelante, no se va a una frase repetida más abajo ni reacciona a palabras sueltas o ruido, y desliza suave. Si mueves tú la pantalla, no se pelea contigo. (También en el teleprompter del panel, cuando el panel se publique.)
- Sonidos: la página queda fija; solo se desplazan la lista, los tipos y el panel, cada uno por dentro.
- Sonidos → editar: ecualizador paramétrico en vez de «Color». Una curva con el espectro del sonido detrás y puntos que se arrastran: corte de graves, graves, medios (con la rueda del mouse, más ancho o más fino), agudos y corte de agudos. Doble clic en un punto lo apaga. El video se arma con esos mismos filtros.
- Al escuchar, una línea roja (el cabezal) avanza sobre la onda que está sonando.
- «▶ Original» y «▶ Con los cambios» suenan al instante: el sonido se precarga al elegirlo y la versión con cambios se va preparando mientras mueves los controles.
- Al tocar un sonido de la lista, suena tal como queda en los videos. Si lo marcas con «Usar en un video», arriba sale cómo ponerlo y un botón para quitarlo.

Versión 1.21.2 (se actualiza desde la app con «Actualizar ahora»):
- Página Sonidos nueva, como el navegador de Ableton: a la izquierda los tipos con su cantidad, al centro los sonidos en una línea cada uno (▶ para escuchar, un punto si está editado) y a la derecha el sonido elegido con su editor siempre a la vista. El nivel general pasó a un botón arriba.
- «Usar en un video» desde el panel del sonido (antes «Elegir»).
- El volumen de un sonido editado ahora sí se nota en el video: el nivelado automático se mide sobre el sonido ya editado (si lo recortabas o invertías, antes casi se perdía el cambio de volumen).

Versión 1.21.1 (se actualiza desde la app con «Actualizar ahora»):
- Guion: se resalta el paso que toca. Si la sesión todavía no tiene clips, el resaltado es «1 · Analizar clips» y «Ver cambios contra el guion» queda apagado hasta que haya clips.
- «Analizar clips» ahora pregunta antes de empezar: muestra de qué carpeta de Drive se traen los clips, dónde quedan en esta PC, cuántas piezas del guion ya tienen clip y qué va a hacer. Solo arranca con «Sí, analizar».
- Editar sonido: nueva onda «Resultado» que se estira o se encoge en vivo según la velocidad (y muestra el recorte, al revés, los fundidos y el eco). Se puede arrastrar como un clip: los bordes de la onda de arriba recortan, el centro mueve el recorte y el borde del resultado cambia la velocidad.

Versión 1.21.0 (se actualiza desde la app con «Actualizar ahora»):
- Los efectos de sonido entran justo en su marca: la sala salta sola el silencio (o el soplido bajito) que traen muchos archivos al inicio. Antes algunos sonaban hasta medio segundo tarde.
- Sonidos → «✎ Editar» en cada sonido: recortar dónde empieza y termina, velocidad, tono, eco, graves, agudos, entrada y salida suave, volumen y «al revés». Se escucha antes de guardar y queda como predeterminado: todos los videos que usan ese sonido lo ponen así (ofrece re-armar los que ya lo usan).
- En el editor del video, cada efecto tiene ahora su velocidad (más rápido o más lento sin cambiar el tono), solo para ese lugar. También se puede abrir «✎ Editar el sonido» desde ahí.
- Corrige el error al mover, recortar o cambiar el volumen de un sonido en el editor que traía la 1.20.0.

Versión 1.20.0 (esta pide instalar la versión completa una vez, porque cambia el programa que abre la sala):
- Al abrir la sala ya no aparece ni se cierra el cuadro negro: se abre directo la ventana de la sala.
- Procesos (☰) ahora es un panel como el de Notificaciones: se abre bajo su botón, con «Recientes» y «Todo el historial», cada proceso con su ícono (listo, falló, cancelado), lo que tardó y agrupado por día. Lo que está corriendo sale arriba, con su avance y el botón Cancelar.

Versión 1.19.0 (se actualiza desde la app con «Actualizar ahora»):
- Producciones como carpetas: cada producción es una carpeta en Drive «P02 TEMA», y adentro van sus días de grabación («11／10 SESIÓN», «13／10 SESIÓN», cada uno con su Clips/). Así un mismo guion sirve para varios días. La sala la encuentra sola y la muestra como una sola producción con sus días. El guion, Flow, la música y V.FINALES pueden ir en la carpeta de la producción o en uno de los días.
- Si un día estaba suelto y lo mueves dentro de su producción, la sala mueve también su copia en esta PC: no vuelve a bajar ni a procesar nada, y se mantiene lo identificado y lo armado.
- El nombre de una producción de carpeta se cambia en Drive (no en la sala).

Versión 1.18.6 (se actualiza desde la app con «Actualizar ahora»):
- Teleprompter: ahora entra entero en la pantalla. Ya no hay que bajar con la barra lateral para ver los botones de abajo (Seguir mi voz, Desplazar solo, Anterior, Siguiente, Toma buena); siempre están a la vista, con cualquier tamaño de letra.

Versión 1.18.5 (se actualiza desde la app con «Actualizar ahora»):
- El guion se actualiza solo: si cambia en Drive (lo actualizó Claude, el panel u otra PC), la sala lo trae al abrir la sesión, cada 90 segundos y al volver a la ventana, y avisa «El guion se actualizó desde Drive». Lo que editaste en esta sala y aún no se subió no se pisa.

Versión 1.18.4 (se actualiza desde la app con «Actualizar ahora»):
- Armar es más del doble de rápido: cada video se codificaba dos veces (una con los subtítulos y otra solo para aplicar los cortes). Ahora es una sola pasada. En la prueba, un video de 39 s bajó de 39 s a 17 s.
- Al guardar una plantilla, la pregunta «¿Re-armar ahora…?» sale al instante (antes esperaba a que la sala se recargara entera).
- Se quitó la pestaña Resultados.
- Configuración (⚙) nueva: menú a la izquierda (Conexiones, Google Drive, Teleprompter, Actualizaciones, Notas) con un punto verde o naranja según el estado de cada una. Las conexiones salen en tarjetas, primero las que faltan, con una barra «3 de 6 listas» y los pasos plegados.
- Notificaciones nuevas: se abren como panel bajo la campana, agrupadas por día (Hoy, Ayer…), con filtro Todas / Errores / Listas, hora relativa («hace 5 min»), botón para quitar una sola y «Marcar leídas».

Versión 1.18.3 (se actualiza desde la app con «Actualizar ahora»):
- Si hay dos salas abiertas a la vez en la misma PC, cada una arma en su propia carpeta y ya no se pisan los archivos (antes un video podía fallar con «Invalid data found»).

Versión 1.18.2 (se actualiza desde la app con «Actualizar ahora»):
- «Armar todos los videos» ahora solo arma (antes también exportaba y se juntaban copias en V.FINALES). Exportar se hace aparte, en Exportar, cuando decidas publicar.
- «Producir todo» viene con «Exportar» apagado; lo prendes solo si quieres exportar en el mismo paso.

Versión 1.18.1 (se actualiza desde la app con «Actualizar ahora»):
- Claqueta fuera: la sala detecta sola el «3, 2, 1, va» del inicio de una toma (y lo que se dijo antes, como el nombre de la toma) y los reinicios cortos en medio («una, pa»), y los quita del video. En P01 salían en los 10 videos de C5 y C6.
- Sonidos normalizados: la sala mide qué tan fuerte suena cada efecto y los lleva a todos al mismo nivel. En Sonidos eliges si suenan suaves, normales o fuertes, o lo apagas.
- Portadas: la sala elige sola el mejor cuadro del hook (nítido, quieto, sin parpadeo ni mano movida). El que elijas a mano se respeta.
- La portada mantiene su propio estilo (letra gruesa en mayúsculas), aunque el subtítulo sea más sobrio.

Versión 1.18.0 (se actualiza desde la app con «Actualizar ahora»):
- ▶ Producir todo (arriba): de los clips subidos a los videos exportados en un paso. Identifica, renombra, prepara, arma, revisa la calidad, exporta (y sube al panel, si quieres). Al final muestra un resumen. Con los 30 videos de P01 tardó 1 h 2 min.
- Exportar → Revisión de calidad: videos muy largos, sin música, con imágenes que faltan, audio saturado o muy bajo, silencios largos, subtítulo donde lo tapan los botones de TikTok o cambios sin armar.
- Resultados (pestaña nueva): carga las vistas y la retención de cada video (o pégalas de Excel). La sala te dice qué tipo de hook, qué hook y qué cuerpo ganan, y Claude lo usa al escribir los próximos guiones.
- Editor: un solo botón «💾 Guardar cambios», que guarda y re-arma solo los videos que cambian (lo del cuerpo va a todos sus videos).
- Editor, pista «Sonidos»: cada sonido con su largo real y su onda. Se arrastra y se mueve de verdad (ya no queda una copia tachada), se recorta desde sus bordes y tiene su propio volumen. En la vista previa suena recortado.
- Editor, pista «Imágenes»: las imágenes del guion con su miniatura. Se pueden arrastrar (pasan a ser tuyas) y se ven en la vista previa.
- Arreglo: «◆ Fotograma clave» e «Imagen o escena en el cabezal» habían dejado de funcionar en la 1.14.0.
- Arreglo: el riser automático no se podía mover ni quitar (quedaba duplicado).
- Piezas y subtítulos → Marcas, rediseñada: el texto dicho palabra por palabra con su segundo. Tocas una palabra y le pones imagen, sonido, estilo o música. Filtros por tipo, lista en orden y, si una marca del guion no encuentra su palabra, te sugiere la más parecida («✨ Arreglar solas»).
- Las marcas del guion se ubican solas en la palabra más parecida si la persona dijo otra («veterinaria» por «veterinario»).
- Plantillas: «Guardar y usar como predeterminada» ahora guarda tus cambios (antes se perdían y el cuadro volvía al medio).
- Estilo del editor del equipo: plantilla «Editor (limpio)» (la frase completa en 1 o 2 líneas, blanca, sin cajas) y, en Plantillas → Estilo de edición, «zoom alternado» por frase y «fundido suave» en imágenes y clips.
- Audio con más margen (sin llegar al tope).

Versión 1.17.0 (se actualiza desde la app con «Actualizar ahora»):
- 🔔 Notificaciones (arriba, junto a ☰): todos los avisos de la sala (listo, errores) quedan guardados con su hora. El número rojo dice cuántos no leíste.
- Los avisos ahora se ven aunque esté abierto el detalle del video o el editor. Antes quedaban tapados detrás de la ventana.
- Procesos (☰) → «Ver todo el historial y los tiempos»: cada trabajo con su fecha, cuánto demoró, cuántos videos hizo y cuánto tardó cada uno, y el promedio por video de armar y de exportar. Al terminar un trabajo, el aviso dice cuánto demoró.
- Detalle del video → «Qué hizo el editor automático»: los segundos ahora son los del video armado (antes eran los del video sin recortes). Toca uno para ir a ese momento.

Versión 1.16.2 (se actualiza desde la app con «Actualizar ahora»):
- Cada video dice qué usa. En la tarjeta: la plantilla de subtítulos. Al abrirlo: «Usa: Aa plantilla · ♪ música · ▣ imágenes · 🔊 sonidos» (y si le faltan imágenes o música). Los videos con ajustes solo suyos llevan la etiqueta «solo este».

Versión 1.16.1 (se actualiza desde la app con «Actualizar ahora»):
- Plantillas: «Mayúsculas: No» ya se ve en la vista previa (la muestra venía escrita en mayúsculas). En el video sí se aplicaba.
- La vista previa ahora muestra la sombra como sale en el video: en diagonal, con su color, su opacidad y el desenfoque. También muestra el espacio entre letras.
- Al guardar una plantilla (o elegir otra predeterminada), la sala ofrece re-armar los videos que la usan, y si no, los deja marcados «⟳ re-armar».
- Un número vacío (por ejemplo, borrar el tamaño) ya no rompe el armado: vuelve al valor por defecto.
- Abrir Plantillas antes de que la sala termine de cargar ya no da error.

Versión 1.16.0 (se actualiza desde la app con «Actualizar ahora»):
- Configuración (⚙) → «Conexiones para trabajar»: los pasos para dejar lista una PC nueva (Google Drive, Claude, Google Flow, Flow Music, Claude en Chrome, el panel y el teleprompter de la tablet), con botones para abrir cada uno. La sala marca sola lo que ya está conectado.
- Exportar: «Peso máximo por video» (15, 20, 25, 30 o 50 MB). Si un video sale más pesado, se vuelve a exportar con la calidad justa para no pasarse. La ficha muestra el peso aproximado de cada video.
- Las imágenes de Flow se mueven con un zoom lento y suave (ya no tiemblan).
- Sesiones: los días que forman un grupo ya no salen sueltos en la lista; se trabajan desde el grupo. Nuevo botón «Cambiar nombre del grupo».

Versión 1.15.1 (se actualiza desde la app con «Actualizar ahora»):
- Los CTA valen como se grabaron: un CTA del guion que no se grabó ya no sale como «falta grabar» si ese cuerpo ya tiene su CTA grabado.

Versión 1.15.0 (se actualiza desde la app con «Actualizar ahora»):
- Faltantes → «Analizar y renombrar todo»: la sala escucha los clips nuevos, los compara con el guion, asigna los que coinciden y renombra cada clip con el código de su pieza, en Drive y en esta PC (IMG_1058.MOV → Clips/Hooks/C2_H3.MOV). No se vuelve a procesar nada ni se pierde lo editado. Lo hace en esta PC, sin Claude.
- «Qué no se identificó»: la lista de los clips que no se pudieron asignar solos y por qué (otra toma, se parece poco al guion, casi no se habla, no se parece a nada, clip reemplazado) y las piezas del guion que todavía no tienen clip.
- Guion: botón «✎ Editar texto» en cada pieza (una caja por frase en los cuerpos). Se guarda en el guion, se sube a Drive y al panel, y se ve en el teleprompter.
- Teleprompter: opción «Líneas» (todas, 3 o 2). En la tablet tapa todo menos 2 o 3 líneas a la altura de la cámara; con «Tamaño» se agranda la letra.

Versión 1.14.2 (se actualiza desde la app con «Actualizar ahora»):
- Faltantes: cuando cambias el clip de una pieza, el clip anterior vuelve a «Clips por identificar» con un aviso «↺ Era C2_H3 · lo cambiaste por IMG_1058», para que no parezca que no se movió. Si ya no lo necesitas, descártalo.
- Si el motor de la sala se apaga con la ventana abierta, «Buscar actualizaciones» ya no dice «Failed to fetch»: explica que hay que cerrar y volver a abrir la sala.
- Silencios fuera: cada clip y cada frase empieza justo antes de que se hable y termina justo después. Se hace solo en todos los videos (los que recortaste a mano se respetan). En el editor, «✂ Quitar silencios» deja elegir el margen en milisegundos y también quitar las pausas largas de en medio.
- Sonidos en el editor: ahora se arrastran para moverlos. Tocarlos los abre para escucharlos, moverlos al cabezal, cambiarlos o quitarlos.
- Subtítulos en el editor: se arrastran para moverlos, se estiran de sus bordes y se pueden cortar en dos, unir con el siguiente o borrar.
- Editor más claro: una guía «¿Cómo se usa el editor?», pistas con nombre claro (Voz, Sonidos, Subtítulos) y las imágenes del guion en la pista «Imágenes».
- Página de Sonidos ordenada por tipo, con una explicación de cada uno, y bien vista en pantallas chicas.
- En Videos, una leyenda del punto de color: gris es «Por revisar», naranja es «Editando» (se pone solo al abrir el video en el editor) y verde es «Aprobado».
- Re-arma los videos para que salgan sin los silencios del inicio.
