@echo off
rem ============================================================
rem  Instalador para Windows de "El Horno de Leo":
rem   1) crea el ejecutable HornoDeLeo.exe con el logo del local
rem      (queda en la carpeta "dist"), y
rem   2) deja un acceso directo "El Horno de Leo" en el escritorio
rem      con ese mismo icono.
rem  Correr en una PC Windows con Python instalado.
rem ============================================================
cd /d "%~dp0"
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name HornoDeLeo ^
    --icon icono.ico --add-data "icono.png;." restaurante.py
if not exist "dist\HornoDeLeo.exe" (
    echo.
    echo No se pudo crear el ejecutable. Revisa los mensajes de arriba.
    pause
    exit /b 1
)
copy /y icono.ico dist\ >nul

rem --- acceso directo en el escritorio con el logo
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$esc = [Environment]::GetFolderPath('Desktop');" ^
  "$lnk = $ws.CreateShortcut($esc + '\El Horno de Leo.lnk');" ^
  "$lnk.TargetPath = '%~dp0dist\HornoDeLeo.exe';" ^
  "$lnk.WorkingDirectory = '%~dp0dist';" ^
  "$lnk.IconLocation = '%~dp0dist\icono.ico';" ^
  "$lnk.Description = 'El Horno de Leo - Gestion del restaurante';" ^
  "$lnk.Save()"

echo.
echo Listo: HornoDeLeo.exe quedo en la carpeta dist y el acceso
echo directo "El Horno de Leo" quedo creado en el escritorio.
pause
