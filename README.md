# Suplevet Videos

- **`editor/`**: sala de edición (Python + ffmpeg). Une hooks, cuerpos y CTAs; subtítulos por estilo, imágenes de Google Flow, sonidos, música, portadas y copies. Se distribuye como `SalaSuplevet.exe` portable con actualización automática.
- **`panel-edicion/`**: panel web `edicion.suplevet.pe/videos` (Next.js + Supabase SUPLEVET_VIDEOS + R2) para revisar, aprobar y medir los videos.

## Publicar una versión nueva de la sala

1. Cambiar el código en `editor/` y subir el número en `editor/VERSION` (por ejemplo `1.1.0`).
2. Escribir qué cambia en `editor/NOTAS_VERSION.md`.
3. Si cambió `lanzador.py` o las librerías, subir también `LANZADOR_VERSION` (en `lanzador.py`) y `editor/LANZADOR_MINIMO`.
4. Commit, tag y push:

```bash
git tag v1.1.0
git push origin main --tags
```

GitHub Actions arma el `.exe` y publica el release. La sala de cada PC muestra el aviso «Actualizar» en menos de una hora.

## Instalación para el editor

Descargar `SalaSuplevet-vX.Y.Z.zip` del último release, descomprimir en `C:\SalaSuplevet` y abrir `SalaSuplevet.exe`. Ver `editor/LEEME_EDITOR.md`.
