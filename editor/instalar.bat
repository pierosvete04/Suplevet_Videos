@echo off
chcp 65001 >nul
title Suplevet - Instalar sala de edicion
cd /d "%~dp0"
echo ============================================
echo   Sala de edicion Suplevet - instalacion
echo   (se hace una sola vez en esta PC)
echo ============================================
echo.

rem 1. Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && python --version >nul 2>nul && set "PY=python")
if not defined PY (
  echo [1/4] Instalando Python...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  set "PY=py -3"
) else (
  echo [1/4] Python ya esta instalado.
)

rem 2. ffmpeg
where ffmpeg >nul 2>nul
if errorlevel 1 (
  if exist "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*" (
    echo [2/4] ffmpeg ya esta instalado.
  ) else (
    echo [2/4] Instalando ffmpeg...
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  )
) else (
  echo [2/4] ffmpeg ya esta instalado.
)

rem 3. Librerias (transcripcion)
echo [3/4] Instalando librerias de Python (puede tardar unos minutos)...
%PY% -m pip install --upgrade pip >nul
%PY% -m pip install faster-whisper numpy
if errorlevel 1 (
  echo.
  echo No se pudieron instalar las librerias. Cierra esta ventana, abre una nueva y vuelve a correr instalar.bat
  pause
  exit /b 1
)
echo      Descargando el modelo de transcripcion (una sola vez)...
%PY% -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

rem 4. Carpeta de Google Drive
echo.
echo [4/4] Carpeta compartida de Google Drive
echo   Abre el Explorador, entra a la carpeta SUPLEVET_VIDEOS (en Mi unidad),
echo   haz clic en la barra de direccion, copia la ruta y pegala aqui.
echo   Ejemplo: G:\Mi unidad\SUPLEVET_VIDEOS
set /p "DRIVE=Ruta: "
%PY% preparar_drive.py "%DRIVE%"

echo.
echo ============================================
echo   Listo. Para trabajar: doble clic en abrir_sala.bat
echo ============================================
pause
