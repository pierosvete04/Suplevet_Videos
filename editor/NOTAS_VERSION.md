Versión 1.23.0 (se actualiza desde la app con «Actualizar ahora»):
- Nueva pestaña **Carruseles**: arma carruseles de 1080×1350 dentro de la sala. Arrastra las fotos de Flow, tócalas para ponerlas en la lámina y con ✂ se les quita el fondo (sin internet, con el mismo modelo de rembg; la primera vez baja 170 MB). Encima van textos (con *palabra* resaltada), garabatos a mano (rayitas, ?!, flechas, corazón…), formas, la bolsa real y el logo. Se mueve, agranda y gira con el mouse.
- «Escribir con Claude» en Carruseles: escribe el texto de cada lámina, el prompt de Flow de cada foto y el copy.
- «Exportar a V.FINALES» deja L01.png, L02.png… y copy.txt en V.FINALES/<carrusel>: el Publicador ya lo toma como carrusel.
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
