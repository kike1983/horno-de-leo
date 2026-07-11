#!/bin/bash
# Arma "Horno de Leo <version>.zip" en el Escritorio, listo para mandar
# por WhatsApp. Adentro va: INSTALAR.bat (instala, borra la versión
# anterior y crea el acceso directo con el logo), LEEME.txt y la carpeta
# programa/ con los .py, los íconos y el Python portable de Windows.
#
# El python-portable se extrae del zip de una versión anterior la primera
# vez y queda cacheado en ~/.cache/horno-python-portable.
set -e
cd "$(dirname "$0")"

VER=$(grep -oP 'VERSION = "\K[^"]+' restaurante.py)
CACHE="$HOME/.cache/horno-python-portable"

if [ ! -d "$CACHE/python-portable" ]; then
    VIEJO=$(ls -t "$HOME/Escritorio"/Horno\ de\ Leo\ *.zip 2>/dev/null | head -1)
    if [ -z "$VIEJO" ]; then
        echo "ERROR: no encuentro un zip anterior en el Escritorio para" >&2
        echo "sacar el python-portable de Windows." >&2
        exit 1
    fi
    echo "Extrayendo python-portable de: $VIEJO"
    mkdir -p "$CACHE"
    TMP=$(mktemp -d)
    unzip -q "$VIEJO" -d "$TMP"
    mv "$TMP"/*/python-portable "$CACHE/"
    rm -rf "$TMP"
fi

DEST_DIR=$(mktemp -d)
CARPETA="$DEST_DIR/Horno de Leo $VER"
mkdir -p "$CARPETA/programa"
cp restaurante.py comandera.py icono.png icono.ico "$CARPETA/programa/"
cp -r "$CACHE/python-portable" "$CARPETA/programa/"

# lanzador dentro de la carpeta instalada (por si borran el acceso directo)
printf '@echo off\r\nrem  El Horno de Leo - doble clic para abrir el programa.\r\ncd /d "%%~dp0"\r\nstart "" "%%~dp0python-portable\\python\\pythonw.exe" "%%~dp0restaurante.py"\r\n' \
    > "$CARPETA/programa/HornoDeLeo.bat"

# instalador y notas, con fin de línea Windows (CRLF)
for f in INSTALAR.bat LEEME.txt INSTRUCCIONES-COMANDERA.txt; do
    sed 's/\r$//; s/$/\r/' "instalador/$f" > "$CARPETA/$f"
done

ZIP="$HOME/Escritorio/Horno de Leo $VER.zip"
rm -f "$ZIP"
(cd "$DEST_DIR" && zip -qr "$ZIP" "Horno de Leo $VER")
rm -rf "$DEST_DIR"
echo "Listo: $ZIP ($(du -h "$ZIP" | cut -f1))"
