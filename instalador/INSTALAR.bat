@echo off
rem ============================================================
rem  Instalador de "El Horno de Leo"
rem  - Cierra el programa si esta abierto
rem  - Borra la version anterior instalada
rem  - Copia la version nueva a esta PC
rem  - Crea el acceso directo en el escritorio con el logo
rem  - Abre el programa
rem  Las ventas, productos y configuracion NO se tocan
rem  (se guardan en otra carpeta y sobreviven a la instalacion).
rem ============================================================
setlocal
set "DEST=%LOCALAPPDATA%\HornoDeLeo"

echo.
echo  ===== Instalador de El Horno de Leo =====
echo.

echo [1/4] Cerrando el programa si esta abierto...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | Where-Object { $_.ExecutablePath -like ($env:LOCALAPPDATA + '\HornoDeLeo*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

echo [2/4] Borrando la version anterior...
if exist "%DEST%" rmdir /s /q "%DEST%"

echo [3/4] Copiando la version nueva (puede demorar un minuto)...
robocopy "%~dp0programa" "%DEST%" /e /nfl /ndl /njh /njs /np >nul
if not exist "%DEST%\restaurante.py" (
    echo.
    echo  ERROR: no se pudo copiar el programa.
    echo  Proba extraer todo el zip primero y correr INSTALAR.bat
    echo  desde la carpeta extraida.
    pause
    exit /b 1
)

echo [4/4] Creando el acceso directo en el escritorio...
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\El Horno de Leo.lnk'); $lnk.TargetPath = $env:LOCALAPPDATA + '\HornoDeLeo\python-portable\python\pythonw.exe'; $lnk.Arguments = '\"' + $env:LOCALAPPDATA + '\HornoDeLeo\restaurante.py\"'; $lnk.WorkingDirectory = $env:LOCALAPPDATA + '\HornoDeLeo'; $lnk.IconLocation = $env:LOCALAPPDATA + '\HornoDeLeo\icono.ico'; $lnk.Description = 'El Horno de Leo - Gestion del restaurante'; $lnk.Save()"

echo.
echo  Listo! El programa quedo instalado con su icono
echo  "El Horno de Leo" en el escritorio.
echo.
echo  Abriendo el programa...
start "" "%DEST%\python-portable\python\pythonw.exe" "%DEST%\restaurante.py"
pause
