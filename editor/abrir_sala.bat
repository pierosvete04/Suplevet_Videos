@echo off
chcp 65001 >nul
title Suplevet - Sala de edicion (no cierres esta ventana)
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
echo Abriendo la sala de edicion en http://localhost:8765 ...
echo Para apagarla, cierra esta ventana.
start "" "http://localhost:8765"
%PY% servidor.py
echo.
echo La sala se detuvo. Si ves un error arriba, mandale una captura a Claude.
pause
