# El Horno de Leo — Gestión del restaurante 🍽

Sistema de administración para el restaurante: carta real cargada, mesas,
mozos, cuentas por comensal, medios de pago, control de stock, impresión de
recibos (sistema o térmica ESC/POS), estadísticas con gráficos y backup
automático. Un solo archivo (`restaurante.py`), sin dependencias externas.

## Cómo ejecutarlo

**Linux (Debian/Kali/Ubuntu):**
```bash
sudo apt install python3-tk   # solo si tkinter no está instalado
python3 restaurante.py
```

**Windows:**
1. Instalar Python desde https://www.python.org/downloads/ (marcar "Add Python to PATH").
2. Doble clic en `restaurante.py`, o en una terminal: `python restaurante.py`

**Ejecutable .exe (sin instalar Python en cada PC):** copiar la carpeta a una
PC con Windows y Python, y hacer doble clic en `crear_exe_windows.bat`.
El ejecutable queda en `dist\HornoDeLeo.exe`.

Los datos se guardan en `~/.restaurante_armenio/`:
- `restaurante.db` — base de datos (SQLite)
- `recibos/` — copia de cada ticket emitido
- `backups/` — respaldo automático diario de la base (se conservan 30)

## Funciones

- **Mesas**: grilla del salón (verde libre / rojo ocupada con el total a la vista).
  Dentro de cada mesa: mozo/a, comensales, pedidos a la cuenta general o a un
  comensal específico, comanda de cocina y pre-cuenta.
- **Cobro**: una sola cuenta, por comensal (lo compartido se divide
  proporcionalmente, un recibo por persona) o partes iguales. Se registra el
  **medio de pago**: Efectivo, MercadoPago o Transferencia.
- **Productos**: la carta completa de El Horno de Leo ya está cargada
  (entradas, lehemeyuns, shawarmas, milanesas, pizzetas, bebidas, cervezas,
  vino y postres) con las categorías Entrada / Menú / Bebida / Postre.
  Alta, edición y baja de productos.
- **Control de stock** (opcional por producto): se descuenta al pedir, se
  devuelve si se quita el ítem, bloquea la venta sin stock y avisa al abrir el
  programa qué hay que reponer.
- **Ventas**: reporte por día con desglose por mozo/a y por medio de pago,
  exportación a CSV.
- **Estadísticas**: facturación por día y ranking de productos más vendidos
  (hoy / últimos 7 días / últimos 30 días).
- **Configuración**: datos del local para el ticket, cantidad de mesas,
  mozo por mesa, impresora, backup manual y recarga de la carta original.

## Impresión

Tres modos (pestaña Configuración → Impresora de tickets):

1. **Impresora del sistema** (predeterminada): CUPS en Linux (`lpoptions -d
   nombre_impresora` para elegirla) o la predeterminada de Windows. Para una
   térmica USB instalada como impresora, en Windows usar el driver
   "Generic / Text Only".
2. **Térmica ESC/POS por red**: poné la IP y puerto (casi siempre `:9100`).
   Envía comandos ESC/POS directos, con acentos (CP850) y corte de papel.
3. **Térmica ESC/POS por USB**: ruta del dispositivo en Linux (típicamente
   `/dev/usb/lp0`; puede requerir agregar tu usuario al grupo `lp`).

El botón **"Ticket de prueba"** permite verificar la conexión. Si la
impresión falla, el ticket siempre queda guardado en `recibos/` (botón
"Abrir carpeta de recibos" en Configuración).

## Desarrollo

El proyecto es un repositorio git. Después de cualquier cambio, correr la
suite de pruebas (usa un HOME temporal, no toca los datos reales):

```bash
xvfb-run -a python3 tests/test_app.py   # necesita: sudo apt install xvfb
```

Y guardar el cambio: `git add -A && git commit -m "descripción del cambio"`.
Para distribuir a Windows, rearmar el ZIP portable (WinPython + restaurante.py
+ HornoDeLeo.bat) o usar `crear_exe_windows.bat` en una PC con Windows.
