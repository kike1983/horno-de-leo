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
powershell -NoProfile -Command "Get-Process pythonw,python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ($env:LOCALAPPDATA + '\HornoDeLeo*') } | Stop-Process -Force" >nul 2>&1

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
set "VBS=%TEMP%\horno_atajo.vbs"
> "%VBS%" echo Set ws = CreateObject("WScript.Shell")
>>"%VBS%" echo ruta = ws.SpecialFolders("Desktop") ^& "\El Horno de Leo.lnk"
>>"%VBS%" echo Set lnk = ws.CreateShortcut(ruta)
>>"%VBS%" echo lnk.TargetPath = "%DEST%\python-portable\python\pythonw.exe"
>>"%VBS%" echo lnk.Arguments = """%DEST%\restaurante.py"""
>>"%VBS%" echo lnk.WorkingDirectory = "%DEST%"
>>"%VBS%" echo lnk.IconLocation = "%DEST%\icono.ico"
>>"%VBS%" echo lnk.Description = "El Horno de Leo - Gestion del restaurante"
>>"%VBS%" echo lnk.Save
cscript //nologo "%VBS%" >nul 2>&1
del "%VBS%" >nul 2>&1

echo.
echo  Listo! El programa quedo instalado.
echo  (Si el icono no aparecio en el escritorio, se crea solo
echo   la proxima vez que se abra el programa.)
echo.
echo  Abriendo el programa...
start "" "%DEST%\python-portable\python\pythonw.exe" "%DEST%\restaurante.py"
pause
