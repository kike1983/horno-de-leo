@echo off
rem ============================================================
rem  Crea el ejecutable HornoDeLeo.exe (correr en una PC Windows
rem  con Python instalado). El .exe queda en la carpeta "dist".
rem ============================================================
pip install pyinstaller
pyinstaller --onefile --windowed --name HornoDeLeo restaurante.py
echo.
echo Listo: el ejecutable esta en la carpeta dist\HornoDeLeo.exe
pause
