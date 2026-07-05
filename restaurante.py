#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
El Horno de Leo — Gestión del restaurante
==========================================
Sistema de administración: productos por categoría con control de stock,
mesas con mozo asignado, cuentas por mesa o por comensal, medios de pago,
impresión de recibos (impresora del sistema o térmica ESC/POS), comandas
de cocina, estadísticas con gráficos y backup automático diario.

Funciona en Linux y Windows con Python 3.8+ (solo usa la librería estándar).

Ejecutar:  python3 restaurante.py   (Linux)
           python restaurante.py    (Windows)
"""

import os
import sys
import csv
import glob
import socket
import shutil
import sqlite3
import datetime
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ---------------------------------------------------------------- rutas / constantes

APP_DIR = os.path.join(os.path.expanduser("~"), ".restaurante_armenio")
DB_PATH = os.path.join(APP_DIR, "restaurante.db")
RECIBOS_DIR = os.path.join(APP_DIR, "recibos")
BACKUPS_DIR = os.path.join(APP_DIR, "backups")
BACKUPS_A_CONSERVAR = 30

CATEGORIAS = ["Entrada", "Menú", "Bebida", "Postre"]
MEDIOS_PAGO = ["Efectivo", "MercadoPago", "Transferencia"]
ANCHO_TICKET = 42  # caracteres de ancho del recibo

# Paleta (bordó / naranja de la marca)
COL_BG = "#f7f2ea"
COL_PANEL = "#ffffff"
COL_ACCENT = "#8c2f39"
COL_ACCENT2 = "#c96f2c"
COL_LIBRE = "#7fb069"
COL_OCUPADA = "#d94f4f"
COL_TEXT = "#2e2a26"
COL_MUTED = "#8a8178"
COL_GRID = "#e8e0d3"
COL_BAJO = "#b3261e"

FONT = "Segoe UI" if sys.platform.startswith("win") else "DejaVu Sans"


def fmt(x):
    """Formatea moneda estilo $ 1.234,56"""
    s = f"{x:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return f"$ {s}"


def fmt_corto(x):
    """Monto sin decimales para etiquetas de gráficos: 12.400"""
    return f"{x:,.0f}".replace(",", ".")


# ---------------------------------------------------------------- base de datos

def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    return con


# Carta real: "El Horno de Leo — Cocina Armenia"
CARTA_HORNO_DE_LEO = [
    # Entradas
    ("Tabla Armenia", 390, "Entrada"),
    ("Pan Lavash", 120, "Entrada"),
    ("Hummus de Garbanzo", 280, "Entrada"),
    ("Salsa de Yogurt", 280, "Entrada"),
    # Comida armenia
    ("Lehemeyun Clásico", 125, "Menú"),
    ("Lehemeyun Especial", 155, "Menú"),
    ("Lehemeyun con Muzza", 155, "Menú"),
    ("Shawarma Clásico", 460, "Menú"),
    ("Shawarma de Pollo", 490, "Menú"),
    ("Shawarma Vegetariano", 460, "Menú"),
    ("Shawarma Vegano", 460, "Menú"),
    ("Shawarma de Falafel", 460, "Menú"),
    ("Shawarma Clásico + Fritas", 590, "Menú"),
    ("Shawarma de Pollo + Fritas", 630, "Menú"),
    # Especialidades
    ("Falafel con Guarnición", 480, "Menú"),
    ("Borek de Queso con Guarnición", 430, "Menú"),
    # Individual
    ("Milanesa de Carne c/Guarnición", 520, "Menú"),
    ("Milanesa Armenia c/Guarnición", 650, "Menú"),
    ("Milanesa al Pan c/Fritas", 650, "Menú"),
    ("Papas Fritas", 220, "Menú"),
    ("Papas Fritas c/Cheddar", 350, "Menú"),
    ("Papas Rústicas", 260, "Menú"),
    ("Nuggets c/Fritas", 390, "Menú"),
    ("Bastones de Muzarella", 390, "Menú"),
    # Pizzetas
    ("Pizzeta c/Muzza", 450, "Menú"),
    ("Pizzeta 1 Gusto", 500, "Menú"),
    ("Tere c/Muzza", 550, "Menú"),
    ("Gusto Extra (pizzeta)", 90, "Menú"),
    # Bebidas
    ("Refresco 600 ml", 160, "Bebida"),
    ("Refresco 1.5 L", 280, "Bebida"),
    ("Agua 600 ml", 110, "Bebida"),
    ("Agua 1 L", 180, "Bebida"),
    ("Salus Saborizada", 150, "Bebida"),
    ("Jugo Dayrico 180 ml", 180, "Bebida"),
    ("Cerveza Miller 355 ml", 190, "Bebida"),
    ("Cerveza Scheider 1 L", 310, "Bebida"),
    ("Cerveza Heineken 1 L", 330, "Bebida"),
    ("Cerveza Artesanal de la Casa 500 ml", 280, "Bebida"),
    ("Heineken Sin Alcohol 330 ml", 190, "Bebida"),
    ("Vino Catamayor 375 ml", 310, "Bebida"),
    # Postres
    ("Baklava", 190, "Postre"),
    ("Helado", 160, "Postre"),
]


def _agregar_columna(cur, tabla, columna, definicion):
    """Migración: agrega la columna si no existe todavía."""
    existentes = [r[1] for r in cur.execute(f"PRAGMA table_info({tabla})")]
    if columna not in existentes:
        cur.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")


def seed_carta(cur):
    cur.execute("DELETE FROM productos")
    cur.executemany(
        "INSERT INTO productos(nombre, precio, categoria) VALUES (?,?,?)",
        CARTA_HORNO_DE_LEO)


def init_db():
    os.makedirs(APP_DIR, exist_ok=True)
    os.makedirs(RECIBOS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    con = db()
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS productos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            precio REAL NOT NULL,
            categoria TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mesas(
            numero INTEGER PRIMARY KEY,
            mozo TEXT DEFAULT '',
            comensales INTEGER DEFAULT 0,
            abierta INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pedidos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mesa INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            precio REAL NOT NULL,
            cantidad INTEGER NOT NULL,
            comensal INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ventas(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            mesa INTEGER,
            mozo TEXT,
            total REAL,
            modo TEXT
        );
        CREATE TABLE IF NOT EXISTS venta_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venta_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            cantidad INTEGER NOT NULL,
            subtotal REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS config(
            clave TEXT PRIMARY KEY,
            valor TEXT
        );
    """)
    # migraciones de versiones anteriores
    _agregar_columna(cur, "productos", "usar_stock", "INTEGER DEFAULT 0")
    _agregar_columna(cur, "productos", "stock", "INTEGER DEFAULT 0")
    _agregar_columna(cur, "productos", "stock_min", "INTEGER DEFAULT 0")
    _agregar_columna(cur, "ventas", "medio", "TEXT DEFAULT 'Efectivo'")

    if cur.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 0:
        seed_carta(cur)
    if cur.execute("SELECT COUNT(*) FROM mesas").fetchone()[0] == 0:
        cur.executemany("INSERT INTO mesas(numero) VALUES (?)",
                        [(i,) for i in range(1, 9)])
    for clave, valor in [
            ("nombre", "El Horno de Leo"),
            ("eslogan", "Cocina Armenia • Sabores con historia"),
            ("direccion", ""), ("telefono", ""),
            ("imp_modo", "sistema"),
            ("imp_red", "192.168.1.100:9100"),
            ("imp_dev", "/dev/usb/lp0"),
            ("imp_corte", "1")]:
        cur.execute("INSERT OR IGNORE INTO config(clave, valor) VALUES (?,?)",
                    (clave, valor))
    con.commit()
    con.close()


def cfg_get(clave, default=""):
    con = db()
    row = con.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
    con.close()
    return row[0] if row else default


def cfg_set(clave, valor):
    con = db()
    con.execute("INSERT OR REPLACE INTO config(clave, valor) VALUES (?,?)",
                (clave, valor))
    con.commit()
    con.close()


# ---------------------------------------------------------------- backup

def backup_auto():
    """Copia diaria de la base de datos; conserva las últimas N."""
    if not os.path.exists(DB_PATH):
        return None
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    destino = os.path.join(
        BACKUPS_DIR, f"restaurante_{datetime.date.today():%Y%m%d}.db")
    if not os.path.exists(destino):
        shutil.copy2(DB_PATH, destino)
    viejos = sorted(glob.glob(os.path.join(BACKUPS_DIR, "restaurante_*.db")))
    for f in viejos[:-BACKUPS_A_CONSERVAR]:
        try:
            os.remove(f)
        except OSError:
            pass
    return destino


def backup_manual():
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    destino = os.path.join(
        BACKUPS_DIR,
        f"restaurante_manual_{datetime.datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(DB_PATH, destino)
    return destino


# ---------------------------------------------------------------- impresión

def centrar(texto):
    return texto.center(ANCHO_TICKET).rstrip()


def linea_item(cantidad, nombre, subtotal):
    izq = f"{cantidad:>2} x {nombre}"
    der = fmt(subtotal)
    if len(izq) + len(der) + 1 > ANCHO_TICKET:
        izq = izq[:ANCHO_TICKET - len(der) - 2] + "…"
    return izq + " " * (ANCHO_TICKET - len(izq) - len(der)) + der


def armar_recibo(titulo, mozo, items, total, nota="", medio=""):
    """items: lista de (cantidad, nombre, subtotal). Devuelve el texto del ticket."""
    ahora = datetime.datetime.now()
    lineas = [centrar(cfg_get("nombre", "El Horno de Leo"))]
    if cfg_get("eslogan"):
        lineas.append(centrar(cfg_get("eslogan")))
    if cfg_get("direccion"):
        lineas.append(centrar(cfg_get("direccion")))
    if cfg_get("telefono"):
        lineas.append(centrar("Tel: " + cfg_get("telefono")))
    lineas += [
        "=" * ANCHO_TICKET,
        f"Fecha: {ahora:%d/%m/%Y %H:%M}",
        titulo,
        f"Mozo/a: {mozo or '-'}",
        "-" * ANCHO_TICKET,
    ]
    for cantidad, nombre, subtotal in items:
        lineas.append(linea_item(cantidad, nombre, subtotal))
    lineas.append("-" * ANCHO_TICKET)
    total_txt = fmt(total)
    lineas.append("TOTAL" + " " * (ANCHO_TICKET - 5 - len(total_txt)) + total_txt)
    if medio:
        lineas.append(f"Medio de pago: {medio}")
    lineas.append("=" * ANCHO_TICKET)
    if nota:
        lineas.append(centrar(nota))
    lineas.append(centrar("Gracias por preferirnos!"))
    lineas.append("")
    return "\n".join(lineas)


def _escpos_bytes(texto):
    """Convierte el ticket a comandos ESC/POS (init, texto CP850, avance y corte)."""
    data = b"\x1b\x40"          # ESC @  inicializar
    data += b"\x1b\x74\x02"     # ESC t 2  página de códigos CP850 (acentos)
    data += texto.encode("cp850", errors="replace")
    data += b"\n\n\n\n"
    if cfg_get("imp_corte", "1") == "1":
        data += b"\x1d\x56\x42\x00"  # GS V B 0  corte parcial
    return data


def _imprimir_sistema(ruta):
    if sys.platform.startswith("win"):
        os.startfile(ruta, "print")  # impresora predeterminada de Windows
        return None
    for cmd in (["lp", ruta], ["lpr", ruta]):  # CUPS en Linux
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15)
            return None
        except (FileNotFoundError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired):
            continue
    return "No se encontró una impresora (comandos 'lp'/'lpr')."


def imprimir_texto(texto, prefijo="recibo"):
    """Guarda una copia del ticket y lo imprime según el modo configurado.
    Devuelve (ruta_del_archivo, error o None)."""
    os.makedirs(RECIBOS_DIR, exist_ok=True)
    nombre = f"{prefijo}_{datetime.datetime.now():%Y%m%d_%H%M%S_%f}.txt"
    ruta = os.path.join(RECIBOS_DIR, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto)
    modo = cfg_get("imp_modo", "sistema")
    try:
        if modo == "red":
            direccion = cfg_get("imp_red", "")
            host, _, puerto = direccion.partition(":")
            with socket.create_connection((host.strip(),
                                           int(puerto or 9100)), timeout=5) as s:
                s.sendall(_escpos_bytes(texto))
            return ruta, None
        if modo == "dispositivo":
            with open(cfg_get("imp_dev", "/dev/usb/lp0"), "wb") as dev:
                dev.write(_escpos_bytes(texto))
            return ruta, None
        error = _imprimir_sistema(ruta)
        return ruta, error
    except Exception as e:
        return ruta, str(e)


def abrir_carpeta(ruta):
    """Abre una carpeta en el explorador de archivos del sistema."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(ruta)
        else:
            subprocess.Popen(["xdg-open", ruta],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ---------------------------------------------------------------- gráficos (Canvas)

def barras_verticales(cv, datos, titulo, fmt_valor=fmt_corto):
    """Barras verticales de una serie. datos: [(etiqueta, valor)]."""
    cv.delete("all")
    w, h = cv.winfo_width(), cv.winfo_height()
    if w < 80 or h < 80:
        return
    cv.create_text(12, 14, text=titulo, anchor="w",
                   font=(FONT, 10, "bold"), fill=COL_TEXT)
    if not datos or all(v == 0 for _, v in datos):
        cv.create_text(w / 2, h / 2, text="Sin ventas en el período",
                       fill=COL_MUTED, font=(FONT, 10))
        return
    ml, mr, mt, mb = 18, 14, 42, 32
    pw, ph = w - ml - mr, h - mt - mb
    vmax = max(v for _, v in datos) * 1.18
    base = mt + ph
    for i in range(1, 4):  # grilla discreta, sin rótulos
        y = base - ph * i / 4
        cv.create_line(ml, y, w - mr, y, fill=COL_GRID)
    n = len(datos)
    paso = pw / n
    bw = max(6, min(paso * 0.62, 58))
    for i, (etiqueta, v) in enumerate(datos):
        x0 = ml + paso * i + (paso - bw) / 2
        y0 = base - (v / vmax) * ph if vmax else base
        cv.create_rectangle(x0, y0, x0 + bw, base, fill=COL_ACCENT, width=0)
        if v:
            cv.create_text(x0 + bw / 2, y0 - 9, text=fmt_valor(v),
                           font=(FONT, 8), fill=COL_TEXT)
        cv.create_text(x0 + bw / 2, base + 12, text=etiqueta,
                       font=(FONT, 8), fill=COL_MUTED)
    cv.create_line(ml, base, w - mr, base, fill="#c9beac")


def barras_horizontales(cv, datos, titulo):
    """Ranking horizontal. datos: [(nombre, cantidad, plata)]."""
    cv.delete("all")
    w, h = cv.winfo_width(), cv.winfo_height()
    if w < 80 or h < 80:
        return
    cv.create_text(12, 14, text=titulo, anchor="w",
                   font=(FONT, 10, "bold"), fill=COL_TEXT)
    if not datos:
        cv.create_text(w / 2, h / 2, text="Sin ventas en el período",
                       fill=COL_MUTED, font=(FONT, 10))
        return
    mt, mb, ml, mr = 40, 12, 12, 12
    ph = h - mt - mb
    n = len(datos)
    fila = min(46, ph / n)
    vmax = max(c for _, c, _ in datos)
    ancho_max = w - ml - mr - 120  # deja lugar para la cifra al final
    for i, (nombre, cant, plata) in enumerate(datos):
        y = mt + fila * i
        nombre_c = nombre if len(nombre) <= 30 else nombre[:29] + "…"
        cv.create_text(ml, y + 9, text=nombre_c, anchor="w",
                       font=(FONT, 8), fill=COL_MUTED)
        bw = max(4, (cant / vmax) * ancho_max) if vmax else 4
        y0 = y + 17
        cv.create_rectangle(ml, y0, ml + bw, y0 + min(14, fila - 22),
                            fill=COL_ACCENT, width=0)
        cv.create_text(ml + bw + 8, y0 + min(14, fila - 22) / 2,
                       text=f"{cant} u · $ {fmt_corto(plata)}", anchor="w",
                       font=(FONT, 8, "bold"), fill=COL_TEXT)


# ---------------------------------------------------------------- ventana de mesa

class MesaWindow(tk.Toplevel):
    def __init__(self, app, numero):
        super().__init__(app)
        self.app = app
        self.numero = numero
        self.title(f"Mesa {numero}")
        self.geometry("1000x640")
        self.configure(bg=COL_BG)
        self.transient(app)

        con = db()
        row = con.execute(
            "SELECT mozo, comensales FROM mesas WHERE numero=?",
            (numero,)).fetchone()
        con.close()
        mozo, comensales = row if row else ("", 0)

        # --- encabezado -------------------------------------------------
        top = ttk.Frame(self, style="Panel.TFrame", padding=10)
        top.pack(fill="x")
        ttk.Label(top, text=f"Mesa {numero}", style="Titulo.TLabel").pack(side="left")

        ttk.Label(top, text="Mozo/a:", style="Panel.TLabel").pack(side="left", padx=(30, 4))
        self.var_mozo = tk.StringVar(value=mozo)
        ttk.Entry(top, textvariable=self.var_mozo, width=20).pack(side="left")

        ttk.Label(top, text="Comensales:", style="Panel.TLabel").pack(side="left", padx=(20, 4))
        self.var_comensales = tk.IntVar(value=max(comensales, 1))
        ttk.Spinbox(top, from_=1, to=30, width=4,
                    textvariable=self.var_comensales,
                    command=self._comensales_cambiados).pack(side="left")

        # --- cuerpo: productos a la izquierda, pedido a la derecha ------
        cuerpo = ttk.Frame(self, style="Panel.TFrame", padding=10)
        cuerpo.pack(fill="both", expand=True)
        cuerpo.columnconfigure(0, weight=2)
        cuerpo.columnconfigure(1, weight=3)
        cuerpo.rowconfigure(0, weight=1)

        # panel productos
        izq = ttk.Labelframe(cuerpo, text=" Agregar producto ", padding=8)
        izq.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        izq.columnconfigure(0, weight=1)
        izq.rowconfigure(1, weight=1)

        self.var_cat = tk.StringVar(value="Todas")
        cb_cat = ttk.Combobox(izq, textvariable=self.var_cat, state="readonly",
                              values=["Todas"] + CATEGORIAS)
        cb_cat.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        cb_cat.bind("<<ComboboxSelected>>", lambda e: self._cargar_productos())

        self.tree_prod = ttk.Treeview(izq, columns=("precio",), height=12)
        self.tree_prod.heading("#0", text="Producto")
        self.tree_prod.heading("precio", text="Precio")
        self.tree_prod.column("#0", width=230)
        self.tree_prod.column("precio", width=90, anchor="e")
        self.tree_prod.tag_configure("bajo", foreground=COL_BAJO)
        self.tree_prod.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.tree_prod.bind("<Double-1>", lambda e: self._agregar())

        fila = ttk.Frame(izq)
        fila.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(fila, text="Cant.:").pack(side="left")
        self.var_cant = tk.IntVar(value=1)
        ttk.Spinbox(fila, from_=1, to=99, width=4,
                    textvariable=self.var_cant).pack(side="left", padx=(2, 12))
        ttk.Label(fila, text="Para:").pack(side="left")
        self.var_comensal = tk.StringVar()
        self.cb_comensal = ttk.Combobox(fila, textvariable=self.var_comensal,
                                        state="readonly", width=16)
        self.cb_comensal.pack(side="left", padx=2)
        ttk.Button(izq, text="Agregar al pedido  ➜", style="Accent.TButton",
                   command=self._agregar).grid(row=3, column=0, columnspan=2,
                                               sticky="ew", pady=(8, 0))

        # panel pedido
        der = ttk.Labelframe(cuerpo, text=" Pedido de la mesa ", padding=8)
        der.grid(row=0, column=1, sticky="nsew")
        der.columnconfigure(0, weight=1)
        der.rowconfigure(0, weight=1)

        cols = ("comensal", "producto", "cant", "precio", "subtotal")
        self.tree_pedido = ttk.Treeview(der, columns=cols, show="headings", height=12)
        for col, txt, w, anchor in [
                ("comensal", "Cuenta", 110, "w"),
                ("producto", "Producto", 190, "w"),
                ("cant", "Cant.", 50, "center"),
                ("precio", "Precio", 90, "e"),
                ("subtotal", "Subtotal", 100, "e")]:
            self.tree_pedido.heading(col, text=txt)
            self.tree_pedido.column(col, width=w, anchor=anchor)
        self.tree_pedido.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(der, orient="vertical", command=self.tree_pedido.yview)
        self.tree_pedido.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        fila2 = ttk.Frame(der)
        fila2.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(fila2, text="Quitar ítem",
                   command=self._quitar).pack(side="left")
        self.lbl_total = ttk.Label(fila2, text="Total: $ 0,00",
                                   font=(FONT, 13, "bold"), foreground=COL_ACCENT)
        self.lbl_total.pack(side="right")

        # --- acciones ----------------------------------------------------
        pie = ttk.Frame(self, style="Panel.TFrame", padding=10)
        pie.pack(fill="x")
        ttk.Button(pie, text="🖨  Comanda cocina",
                   command=self._imprimir_comanda).pack(side="left")
        ttk.Button(pie, text="🖨  Pre-cuenta",
                   command=self._imprimir_precuenta).pack(side="left", padx=8)
        ttk.Button(pie, text="Cerrar ventana",
                   command=self._cerrar).pack(side="right")
        ttk.Button(pie, text="💵  COBRAR MESA", style="Accent.TButton",
                   command=self._cobrar).pack(side="right", padx=8)

        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self._cargar_productos()
        self._comensales_cambiados()
        self._refrescar_pedido()

    # ------------------------------------------------ helpers de datos

    def _pedidos(self):
        con = db()
        rows = con.execute(
            "SELECT id, nombre, precio, cantidad, comensal FROM pedidos "
            "WHERE mesa=? ORDER BY comensal, id", (self.numero,)).fetchall()
        con.close()
        return rows

    def _guardar_mesa(self, abierta=None):
        con = db()
        if abierta is None:
            con.execute("UPDATE mesas SET mozo=?, comensales=? WHERE numero=?",
                        (self.var_mozo.get().strip(),
                         self.var_comensales.get(), self.numero))
        else:
            con.execute(
                "UPDATE mesas SET mozo=?, comensales=?, abierta=? WHERE numero=?",
                (self.var_mozo.get().strip(), self.var_comensales.get(),
                 abierta, self.numero))
        con.commit()
        con.close()

    # ------------------------------------------------ acciones UI

    def _cargar_productos(self):
        self.tree_prod.delete(*self.tree_prod.get_children())
        con = db()
        sql = ("SELECT id, nombre, precio, categoria, usar_stock, stock, stock_min "
               "FROM productos ")
        if self.var_cat.get() == "Todas":
            rows = con.execute(sql + "ORDER BY categoria, nombre").fetchall()
        else:
            rows = con.execute(sql + "WHERE categoria=? ORDER BY nombre",
                               (self.var_cat.get(),)).fetchall()
        con.close()
        for pid, nombre, precio, cat, usar, stock, smin in rows:
            texto = nombre if self.var_cat.get() != "Todas" else f"[{cat}] {nombre}"
            tags = ()
            if usar:
                if (stock or 0) <= 0:
                    texto += "  — SIN STOCK"
                    tags = ("bajo",)
                elif (stock or 0) <= (smin or 0):
                    texto += f"  — quedan {int(stock)}"
                    tags = ("bajo",)
            self.tree_prod.insert("", "end", iid=str(pid), text=texto,
                                  values=(fmt(precio),), tags=tags)

    def _comensales_cambiados(self):
        n = self.var_comensales.get()
        maximo = max((c for _, _, _, _, c in self._pedidos()), default=0)
        if n < maximo:
            self.var_comensales.set(maximo)
            n = maximo
            messagebox.showwarning(
                "Comensales", f"Hay consumos cargados al comensal {maximo}; "
                "no se puede reducir por debajo de eso.", parent=self)
        valores = ["Cuenta general"] + [f"Comensal {i}" for i in range(1, n + 1)]
        self.cb_comensal["values"] = valores
        if self.var_comensal.get() not in valores:
            self.cb_comensal.current(0)
        self._guardar_mesa()

    def _agregar(self):
        sel = self.tree_prod.selection()
        if not sel:
            messagebox.showinfo("Agregar", "Seleccioná un producto de la lista.",
                                parent=self)
            return
        pid = int(sel[0])
        cant = max(self.var_cant.get(), 1)
        con = db()
        row = con.execute(
            "SELECT nombre, precio, usar_stock, stock FROM productos WHERE id=?",
            (pid,)).fetchone()
        if not row:
            con.close()
            return
        nombre, precio, usar_stock, stock = row
        if usar_stock and (stock or 0) < cant:
            con.close()
            messagebox.showerror(
                "Sin stock",
                f"No hay stock suficiente de \"{nombre}\" "
                f"(quedan {int(stock or 0)}).", parent=self)
            return
        comensal = self.cb_comensal.current()  # 0 = cuenta general
        con.execute(
            "INSERT INTO pedidos(mesa, nombre, precio, cantidad, comensal) "
            "VALUES (?,?,?,?,?)", (self.numero, nombre, precio, cant, comensal))
        if usar_stock:
            con.execute("UPDATE productos SET stock=stock-? WHERE id=?",
                        (cant, pid))
        con.commit()
        con.close()
        self._guardar_mesa(abierta=1)
        self.var_cant.set(1)
        self._cargar_productos()
        self._refrescar_pedido()
        self.app.refrescar_mesas()

    def _quitar(self):
        sel = self.tree_pedido.selection()
        if not sel:
            return
        con = db()
        for iid in sel:
            row = con.execute("SELECT nombre, cantidad FROM pedidos WHERE id=?",
                              (int(iid),)).fetchone()
            if row:
                # devolver el stock si ese producto lo controla
                con.execute("UPDATE productos SET stock=stock+? "
                            "WHERE nombre=? AND usar_stock=1", (row[1], row[0]))
            con.execute("DELETE FROM pedidos WHERE id=?", (int(iid),))
        abierta = 1 if con.execute(
            "SELECT COUNT(*) FROM pedidos WHERE mesa=?",
            (self.numero,)).fetchone()[0] else 0
        con.execute("UPDATE mesas SET abierta=? WHERE numero=?",
                    (abierta, self.numero))
        con.commit()
        con.close()
        self._cargar_productos()
        self._refrescar_pedido()
        self.app.refrescar_mesas()

    def _refrescar_pedido(self):
        self.tree_pedido.delete(*self.tree_pedido.get_children())
        total = 0.0
        for pid, nombre, precio, cant, comensal in self._pedidos():
            quien = "Cuenta general" if comensal == 0 else f"Comensal {comensal}"
            sub = precio * cant
            total += sub
            self.tree_pedido.insert("", "end", iid=str(pid),
                                    values=(quien, nombre, cant,
                                            fmt(precio), fmt(sub)))
        self.lbl_total.config(text=f"Total: {fmt(total)}")

    # ------------------------------------------------ impresión

    def _items_todos(self):
        return [(cant, nombre, precio * cant)
                for _, nombre, precio, cant, _ in self._pedidos()]

    def _imprimir_comanda(self):
        pedidos = self._pedidos()
        if not pedidos:
            messagebox.showinfo("Comanda", "La mesa no tiene pedidos.", parent=self)
            return
        ahora = datetime.datetime.now()
        lineas = [centrar("*** COMANDA COCINA ***"),
                  f"Mesa {self.numero}  -  {ahora:%H:%M}",
                  f"Mozo/a: {self.var_mozo.get() or '-'}",
                  "-" * ANCHO_TICKET]
        for _, nombre, _, cant, comensal in pedidos:
            quien = "" if comensal == 0 else f"  (comensal {comensal})"
            lineas.append(f"{cant:>2} x {nombre}{quien}")
        lineas.append("")
        self._despachar("\n".join(lineas), "comanda")

    def _imprimir_precuenta(self):
        items = self._items_todos()
        if not items:
            messagebox.showinfo("Pre-cuenta", "La mesa no tiene pedidos.", parent=self)
            return
        total = sum(s for _, _, s in items)
        texto = armar_recibo(f"Mesa {self.numero}  -  PRE-CUENTA",
                             self.var_mozo.get(), items, total,
                             nota="* Pre-cuenta: no válido como factura *")
        self._despachar(texto, "precuenta")

    def _despachar(self, texto, prefijo):
        ruta, error = imprimir_texto(texto, prefijo)
        if error:
            if messagebox.askyesno(
                    "Impresión",
                    f"No se pudo imprimir: {error}\n\n"
                    f"El ticket quedó guardado como archivo de texto.\n"
                    f"¿Abrir la carpeta de recibos para verlo?",
                    parent=self):
                abrir_carpeta(RECIBOS_DIR)
        else:
            messagebox.showinfo("Impresión",
                                f"Enviado a la impresora.\nCopia: {ruta}",
                                parent=self)

    # ------------------------------------------------ cobro

    def _cobrar(self):
        pedidos = self._pedidos()
        if not pedidos:
            messagebox.showinfo("Cobrar", "La mesa no tiene pedidos.", parent=self)
            return
        self._guardar_mesa()

        dlg = tk.Toplevel(self)
        dlg.title(f"Cobrar mesa {self.numero}")
        dlg.configure(bg=COL_BG)
        dlg.transient(self)
        dlg.grab_set()

        total = sum(p * c for _, _, p, c, _ in pedidos)
        ttk.Label(dlg, text=f"Total de la mesa: {fmt(total)}",
                  style="Titulo.TLabel").pack(padx=20, pady=(15, 10))

        var_modo = tk.StringVar(value="una")
        var_imprimir = tk.BooleanVar(value=True)
        for valor, texto in [
                ("una", "Una sola cuenta (todo junto)"),
                ("comensal", "Por comensal (cada uno paga lo suyo; lo de la "
                             "cuenta general se divide en partes iguales)"),
                ("iguales", "Dividir el total en partes iguales")]:
            ttk.Radiobutton(dlg, text=texto, value=valor,
                            variable=var_modo).pack(anchor="w", padx=25, pady=3)

        fila_medio = ttk.Frame(dlg)
        fila_medio.pack(anchor="w", padx=25, pady=(10, 0))
        ttk.Label(fila_medio, text="Medio de pago:").pack(side="left")
        var_medio = tk.StringVar(value=MEDIOS_PAGO[0])
        ttk.Combobox(fila_medio, textvariable=var_medio, state="readonly",
                     values=MEDIOS_PAGO, width=16).pack(side="left", padx=8)

        ttk.Checkbutton(dlg, text="Imprimir recibo(s)",
                        variable=var_imprimir).pack(anchor="w", padx=25, pady=(10, 5))

        botones = ttk.Frame(dlg)
        botones.pack(pady=15)
        ttk.Button(botones, text="Cancelar",
                   command=dlg.destroy).pack(side="left", padx=8)
        ttk.Button(botones, text="Confirmar cobro", style="Accent.TButton",
                   command=lambda: self._confirmar_cobro(
                       dlg, var_modo.get(), var_imprimir.get(),
                       var_medio.get())).pack(side="left")

    def _confirmar_cobro(self, dlg, modo, imprimir, medio):
        pedidos = self._pedidos()
        mozo = self.var_mozo.get().strip()
        n = self.var_comensales.get()
        total = sum(p * c for _, _, p, c, _ in pedidos)
        recibos = []

        if modo == "una":
            recibos.append(armar_recibo(f"Mesa {self.numero}", mozo,
                                        self._items_todos(), total, medio=medio))
        elif modo == "iguales":
            por_persona = total / n
            texto = armar_recibo(
                f"Mesa {self.numero}  ({n} comensales)", mozo,
                self._items_todos(), total, medio=medio,
                nota=f"Por persona ({n}): {fmt(por_persona)}")
            recibos.append(texto)
        else:  # por comensal
            hay_individual = any(c != 0 for _, _, _, _, c in pedidos)
            if not hay_individual:
                if not messagebox.askyesno(
                        "Por comensal",
                        "Todos los consumos están en la cuenta general.\n"
                        "Se dividirá todo en partes iguales. ¿Continuar?",
                        parent=dlg):
                    return
            compartido = sum(p * c for _, _, p, c, com in pedidos if com == 0)
            parte_compartida = compartido / n if n else 0
            for i in range(1, n + 1):
                items = [(cant, nombre, precio * cant)
                         for _, nombre, precio, cant, com in pedidos if com == i]
                sub = sum(s for _, _, s in items)
                if parte_compartida > 0:
                    items.append((1, "Compartido (proporcional)", parte_compartida))
                    sub += parte_compartida
                if not items:
                    continue
                recibos.append(armar_recibo(
                    f"Mesa {self.numero}  -  Comensal {i}", mozo, items, sub,
                    medio=medio))

        # registrar venta (con detalle de ítems para estadísticas) y liberar mesa
        con = db()
        cur = con.cursor()
        cur.execute("INSERT INTO ventas(fecha, mesa, mozo, total, modo, medio) "
                    "VALUES (?,?,?,?,?,?)",
                    (datetime.datetime.now().isoformat(timespec="seconds"),
                     self.numero, mozo, total, modo, medio))
        venta_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO venta_items(venta_id, nombre, cantidad, subtotal) "
            "VALUES (?,?,?,?)",
            [(venta_id, nombre, cant, precio * cant)
             for _, nombre, precio, cant, _ in pedidos])
        cur.execute("DELETE FROM pedidos WHERE mesa=?", (self.numero,))
        cur.execute("UPDATE mesas SET abierta=0, comensales=0 WHERE numero=?",
                    (self.numero,))
        con.commit()
        con.close()

        problemas = []
        for texto in recibos:
            if imprimir:
                _, error = imprimir_texto(texto, f"recibo_mesa{self.numero}")
                if error:
                    problemas.append(error)
            else:
                # solo guardar copia sin imprimir
                nombre = (f"recibo_mesa{self.numero}_"
                          f"{datetime.datetime.now():%Y%m%d_%H%M%S_%f}.txt")
                with open(os.path.join(RECIBOS_DIR, nombre), "w",
                          encoding="utf-8") as f:
                    f.write(texto)

        dlg.destroy()
        msg = f"Mesa {self.numero} cobrada: {fmt(total)} ({medio})."
        if problemas:
            if messagebox.askyesno(
                    "Cobro registrado",
                    msg + f"\n\nNo se pudo imprimir ({problemas[0]}).\n"
                    "Los recibos quedaron guardados como archivos.\n"
                    "¿Abrir la carpeta de recibos?", parent=self.app):
                abrir_carpeta(RECIBOS_DIR)
        else:
            messagebox.showinfo("Cobro registrado", msg, parent=self.app)
        self.app.refrescar_mesas()
        self.destroy()

    def _cerrar(self):
        self._guardar_mesa()
        self.app.refrescar_mesas()
        self.destroy()


# ---------------------------------------------------------------- aplicación

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gestión — " + cfg_get("nombre", "El Horno de Leo"))
        self.geometry("1180x720")
        self.minsize(980, 620)
        self.configure(bg=COL_BG)
        self._estilos()
        self._ventanas_mesa = {}

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_mesas = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        self.tab_prod = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        self.tab_rep = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        self.tab_stats = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        self.tab_cfg = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        nb.add(self.tab_mesas, text="  🍽  Mesas  ")
        nb.add(self.tab_prod, text="  📋  Productos  ")
        nb.add(self.tab_rep, text="  🧾  Ventas  ")
        nb.add(self.tab_stats, text="  📊  Estadísticas  ")
        nb.add(self.tab_cfg, text="  ⚙  Configuración  ")
        nb.bind("<<NotebookTabChanged>>", self._al_cambiar_tab)
        self.nb = nb

        self._armar_tab_mesas()
        self._armar_tab_productos()
        self._armar_tab_reportes()
        self._armar_tab_stats()
        self._armar_tab_config()

        self.after(600, self._avisar_faltantes)

    # ------------------------------------------------ estilos

    def _estilos(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=COL_BG, foreground=COL_TEXT,
                     font=(FONT, 10))
        st.configure("TNotebook", background=COL_BG, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(14, 8), font=(FONT, 10, "bold"))
        st.map("TNotebook.Tab",
               background=[("selected", COL_ACCENT)],
               foreground=[("selected", "white")])
        st.configure("Panel.TFrame", background=COL_BG)
        st.configure("Panel.TLabel", background=COL_BG)
        st.configure("Titulo.TLabel", background=COL_BG,
                     font=(FONT, 15, "bold"), foreground=COL_ACCENT)
        st.configure("TLabelframe", background=COL_BG)
        st.configure("TLabelframe.Label", background=COL_BG,
                     foreground=COL_ACCENT, font=(FONT, 10, "bold"))
        st.configure("Treeview", rowheight=26, fieldbackground=COL_PANEL,
                     background=COL_PANEL)
        st.configure("Treeview.Heading", font=(FONT, 10, "bold"),
                     background=COL_ACCENT, foreground="white")
        st.map("Treeview.Heading", background=[("active", COL_ACCENT2)])
        st.configure("Accent.TButton", background=COL_ACCENT,
                     foreground="white", font=(FONT, 10, "bold"), padding=8)
        st.map("Accent.TButton",
               background=[("active", COL_ACCENT2), ("disabled", "#b9a7a9")])
        st.configure("TButton", padding=6)

    def _al_cambiar_tab(self, _evento=None):
        idx = self.nb.index(self.nb.select())
        if idx == 0:
            self.refrescar_mesas()
        elif idx == 1:
            self._cargar_productos()
        elif idx == 2:
            self._cargar_ventas()
        elif idx == 3:
            self._redibujar_graficos()
        elif idx == 4:
            self._cargar_mesas_cfg()

    # ------------------------------------------------ stock bajo

    def _faltantes(self):
        con = db()
        rows = con.execute(
            "SELECT nombre, stock, stock_min FROM productos "
            "WHERE usar_stock=1 AND stock<=stock_min "
            "ORDER BY stock").fetchall()
        con.close()
        return rows

    def _avisar_faltantes(self):
        rows = self._faltantes()
        if rows:
            detalle = "\n".join(
                f"  • {n} — quedan {int(s)} (mínimo {int(m)})"
                for n, s, m in rows[:15])
            messagebox.showwarning(
                "Stock bajo", "Productos para reponer:\n\n" + detalle,
                parent=self)

    # ================================================= TAB MESAS

    def _armar_tab_mesas(self):
        ttk.Label(self.tab_mesas, text="Salón — tocá una mesa para atenderla",
                  style="Titulo.TLabel").pack(anchor="w", pady=(0, 10))
        self.frame_grilla = ttk.Frame(self.tab_mesas, style="Panel.TFrame")
        self.frame_grilla.pack(fill="both", expand=True)
        leyenda = ttk.Frame(self.tab_mesas, style="Panel.TFrame")
        leyenda.pack(anchor="w", pady=(10, 0))
        for color, texto in [(COL_LIBRE, "Libre"), (COL_OCUPADA, "Ocupada")]:
            tk.Label(leyenda, text="  ", bg=color).pack(side="left", padx=(10, 3))
            ttk.Label(leyenda, text=texto, style="Panel.TLabel").pack(side="left")
        self.refrescar_mesas()

    def refrescar_mesas(self):
        for w in self.frame_grilla.winfo_children():
            w.destroy()
        con = db()
        mesas = con.execute(
            "SELECT numero, mozo, abierta FROM mesas ORDER BY numero").fetchall()
        totales = dict(con.execute(
            "SELECT mesa, SUM(precio*cantidad) FROM pedidos GROUP BY mesa").fetchall())
        con.close()
        columnas = 5
        for i in range(columnas):
            self.frame_grilla.columnconfigure(i, weight=1)
        for idx, (numero, mozo, abierta) in enumerate(mesas):
            total = totales.get(numero, 0) or 0
            estado = fmt(total) if abierta else "Libre"
            texto = f"Mesa {numero}\n{mozo or '(sin mozo)'}\n{estado}"
            color = COL_OCUPADA if abierta else COL_LIBRE
            btn = tk.Button(self.frame_grilla, text=texto, bg=color, fg="white",
                            font=(FONT, 11, "bold"), relief="flat", cursor="hand2",
                            activebackground=COL_ACCENT2, activeforeground="white",
                            command=lambda n=numero: self.abrir_mesa(n))
            btn.grid(row=idx // columnas, column=idx % columnas,
                     sticky="nsew", padx=6, pady=6, ipadx=10, ipady=18)

    def abrir_mesa(self, numero):
        win = self._ventanas_mesa.get(numero)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        self._ventanas_mesa[numero] = MesaWindow(self, numero)

    # ================================================= TAB PRODUCTOS

    def _armar_tab_productos(self):
        f = self.tab_prod
        f.columnconfigure(0, weight=3)
        f.columnconfigure(1, weight=2)
        f.rowconfigure(1, weight=1)

        ttk.Label(f, text="Productos, precios y stock", style="Titulo.TLabel")\
            .grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        cols = ("categoria", "nombre", "precio", "stock")
        self.tree_productos = ttk.Treeview(f, columns=cols, show="headings")
        for col, txt, w, anchor in [("categoria", "Categoría", 90, "w"),
                                    ("nombre", "Producto", 250, "w"),
                                    ("precio", "Precio", 100, "e"),
                                    ("stock", "Stock", 110, "center")]:
            self.tree_productos.heading(col, text=txt)
            self.tree_productos.column(col, width=w, anchor=anchor)
        self.tree_productos.tag_configure("bajo", foreground=COL_BAJO)
        self.tree_productos.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.tree_productos.bind("<<TreeviewSelect>>", self._producto_seleccionado)

        form = ttk.Labelframe(f, text=" Ficha del producto ", padding=12)
        form.grid(row=1, column=1, sticky="new")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Nombre:").grid(row=0, column=0, sticky="w", pady=4)
        self.var_p_nombre = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_p_nombre)\
            .grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Precio:").grid(row=1, column=0, sticky="w", pady=4)
        self.var_p_precio = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_p_precio)\
            .grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Categoría:").grid(row=2, column=0, sticky="w", pady=4)
        self.var_p_cat = tk.StringVar(value=CATEGORIAS[0])
        ttk.Combobox(form, textvariable=self.var_p_cat, state="readonly",
                     values=CATEGORIAS).grid(row=2, column=1, sticky="ew", pady=4)

        self.var_p_usar = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="Controlar stock de este producto",
                        variable=self.var_p_usar)\
            .grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Label(form, text="Stock actual:").grid(row=4, column=0, sticky="w", pady=4)
        self.var_p_stock = tk.StringVar(value="0")
        ttk.Entry(form, textvariable=self.var_p_stock, width=8)\
            .grid(row=4, column=1, sticky="w", pady=4)
        ttk.Label(form, text="Avisar si baja de:").grid(row=5, column=0,
                                                        sticky="w", pady=4)
        self.var_p_stockmin = tk.StringVar(value="0")
        ttk.Entry(form, textvariable=self.var_p_stockmin, width=8)\
            .grid(row=5, column=1, sticky="w", pady=4)

        ttk.Button(form, text="➕  Agregar nuevo", style="Accent.TButton",
                   command=self._producto_agregar)\
            .grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        ttk.Button(form, text="💾  Guardar cambios del seleccionado",
                   command=self._producto_editar)\
            .grid(row=7, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(form, text="🗑  Eliminar seleccionado",
                   command=self._producto_eliminar)\
            .grid(row=8, column=0, columnspan=2, sticky="ew", pady=4)

        self.lbl_faltantes = ttk.Label(f, text="", style="Panel.TLabel",
                                       foreground=COL_BAJO)
        self.lbl_faltantes.grid(row=2, column=0, columnspan=2,
                                sticky="w", pady=(8, 0))
        self._cargar_productos()

    def _cargar_productos(self):
        self.tree_productos.delete(*self.tree_productos.get_children())
        con = db()
        for pid, nombre, precio, cat, usar, stock, smin in con.execute(
                "SELECT id, nombre, precio, categoria, usar_stock, stock, "
                "stock_min FROM productos ORDER BY categoria, nombre"):
            if usar:
                stock_txt = f"{int(stock or 0)} (avisa ≤ {int(smin or 0)})"
                tags = ("bajo",) if (stock or 0) <= (smin or 0) else ()
            else:
                stock_txt, tags = "—", ()
            self.tree_productos.insert("", "end", iid=str(pid), tags=tags,
                                       values=(cat, nombre, fmt(precio), stock_txt))
        con.close()
        faltan = self._faltantes()
        if faltan:
            self.lbl_faltantes.config(
                text="⚠  Reponer: " + ", ".join(
                    f"{n} ({int(s)})" for n, s, _ in faltan[:8])
                + ("…" if len(faltan) > 8 else ""))
        else:
            self.lbl_faltantes.config(text="")

    def _producto_seleccionado(self, _evento=None):
        sel = self.tree_productos.selection()
        if not sel:
            return
        con = db()
        row = con.execute(
            "SELECT nombre, precio, categoria, usar_stock, stock, stock_min "
            "FROM productos WHERE id=?", (int(sel[0]),)).fetchone()
        con.close()
        if row:
            self.var_p_nombre.set(row[0])
            self.var_p_precio.set(f"{row[1]:g}")
            self.var_p_cat.set(row[2])
            self.var_p_usar.set(bool(row[3]))
            self.var_p_stock.set(str(int(row[4] or 0)))
            self.var_p_stockmin.set(str(int(row[5] or 0)))

    def _leer_form_producto(self):
        nombre = self.var_p_nombre.get().strip()
        try:
            precio = float(self.var_p_precio.get().replace(",", "."))
            if precio < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Producto", "El precio no es válido.", parent=self)
            return None
        if not nombre:
            messagebox.showerror("Producto", "Falta el nombre.", parent=self)
            return None
        try:
            stock = int(self.var_p_stock.get() or 0)
            stock_min = int(self.var_p_stockmin.get() or 0)
        except ValueError:
            messagebox.showerror("Producto", "Stock y mínimo deben ser números "
                                 "enteros.", parent=self)
            return None
        return (nombre, precio, self.var_p_cat.get(),
                1 if self.var_p_usar.get() else 0, stock, stock_min)

    def _producto_agregar(self):
        datos = self._leer_form_producto()
        if not datos:
            return
        con = db()
        con.execute("INSERT INTO productos(nombre, precio, categoria, "
                    "usar_stock, stock, stock_min) VALUES (?,?,?,?,?,?)", datos)
        con.commit()
        con.close()
        self.var_p_nombre.set("")
        self.var_p_precio.set("")
        self._cargar_productos()

    def _producto_editar(self):
        sel = self.tree_productos.selection()
        if not sel:
            messagebox.showinfo("Editar", "Seleccioná un producto de la lista.",
                                parent=self)
            return
        datos = self._leer_form_producto()
        if not datos:
            return
        con = db()
        con.execute("UPDATE productos SET nombre=?, precio=?, categoria=?, "
                    "usar_stock=?, stock=?, stock_min=? WHERE id=?",
                    (*datos, int(sel[0])))
        con.commit()
        con.close()
        self._cargar_productos()

    def _producto_eliminar(self):
        sel = self.tree_productos.selection()
        if not sel:
            return
        if not messagebox.askyesno("Eliminar", "¿Eliminar el producto seleccionado?",
                                   parent=self):
            return
        con = db()
        con.execute("DELETE FROM productos WHERE id=?", (int(sel[0]),))
        con.commit()
        con.close()
        self._cargar_productos()

    # ================================================= TAB VENTAS

    def _armar_tab_reportes(self):
        f = self.tab_rep
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)

        ttk.Label(f, text="Reporte de ventas", style="Titulo.TLabel")\
            .grid(row=0, column=0, sticky="w", pady=(0, 10))

        barra = ttk.Frame(f, style="Panel.TFrame")
        barra.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(barra, text="Día (AAAA-MM-DD):", style="Panel.TLabel")\
            .pack(side="left")
        self.var_fecha = tk.StringVar(
            value=datetime.date.today().isoformat())
        ttk.Entry(barra, textvariable=self.var_fecha, width=12)\
            .pack(side="left", padx=6)
        ttk.Button(barra, text="Actualizar",
                   command=self._cargar_ventas).pack(side="left")
        ttk.Button(barra, text="Exportar CSV",
                   command=self._exportar_csv).pack(side="left", padx=8)
        self.lbl_resumen = ttk.Label(barra, text="", style="Panel.TLabel",
                                     font=(FONT, 11, "bold"))
        self.lbl_resumen.pack(side="right")

        cols = ("hora", "mesa", "mozo", "modo", "medio", "total")
        self.tree_ventas = ttk.Treeview(f, columns=cols, show="headings")
        for col, txt, w, anchor in [("hora", "Hora", 80, "center"),
                                    ("mesa", "Mesa", 55, "center"),
                                    ("mozo", "Mozo/a", 140, "w"),
                                    ("modo", "Tipo de cobro", 130, "w"),
                                    ("medio", "Medio de pago", 130, "w"),
                                    ("total", "Total", 110, "e")]:
            self.tree_ventas.heading(col, text=txt)
            self.tree_ventas.column(col, width=w, anchor=anchor)
        self.tree_ventas.grid(row=2, column=0, sticky="nsew")

        self.lbl_por_mozo = ttk.Label(f, text="", style="Panel.TLabel",
                                      justify="left")
        self.lbl_por_mozo.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self._cargar_ventas()

    def _ventas_del_dia(self):
        fecha = self.var_fecha.get().strip()
        con = db()
        rows = con.execute(
            "SELECT fecha, mesa, mozo, modo, medio, total FROM ventas "
            "WHERE fecha LIKE ? ORDER BY fecha", (fecha + "%",)).fetchall()
        con.close()
        return rows

    def _cargar_ventas(self):
        self.tree_ventas.delete(*self.tree_ventas.get_children())
        modos = {"una": "Una cuenta", "comensal": "Por comensal",
                 "iguales": "Partes iguales"}
        total_dia = 0.0
        por_mozo, por_medio = {}, {}
        rows = self._ventas_del_dia()
        for fecha, mesa, mozo, modo, medio, total in rows:
            hora = fecha[11:16] if len(fecha) >= 16 else fecha
            self.tree_ventas.insert("", "end", values=(
                hora, mesa, mozo or "-", modos.get(modo, modo),
                medio or "-", fmt(total)))
            total_dia += total
            por_mozo[mozo or "(sin mozo)"] = \
                por_mozo.get(mozo or "(sin mozo)", 0) + total
            por_medio[medio or "-"] = por_medio.get(medio or "-", 0) + total
        self.lbl_resumen.config(
            text=f"{len(rows)} mesas cobradas — Total del día: {fmt(total_dia)}")
        if rows:
            linea1 = "Por mozo/a:   " + "   |   ".join(
                f"{m}: {fmt(t)}" for m, t in
                sorted(por_mozo.items(), key=lambda kv: -kv[1]))
            linea2 = "Por medio de pago:   " + "   |   ".join(
                f"{m}: {fmt(t)}" for m, t in
                sorted(por_medio.items(), key=lambda kv: -kv[1]))
            self.lbl_por_mozo.config(text=linea1 + "\n" + linea2)
        else:
            self.lbl_por_mozo.config(text="Sin ventas registradas para ese día.")

    def _exportar_csv(self):
        rows = self._ventas_del_dia()
        if not rows:
            messagebox.showinfo("Exportar", "No hay ventas para exportar.",
                                parent=self)
            return
        ruta = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv",
            initialfile=f"ventas_{self.var_fecha.get()}.csv",
            filetypes=[("CSV", "*.csv")])
        if not ruta:
            return
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["fecha", "mesa", "mozo", "modo", "medio", "total"])
            w.writerows(rows)
        messagebox.showinfo("Exportar", f"Exportado a:\n{ruta}", parent=self)

    # ================================================= TAB ESTADÍSTICAS

    def _armar_tab_stats(self):
        f = self.tab_stats
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(2, weight=1)

        ttk.Label(f, text="Estadísticas del negocio", style="Titulo.TLabel")\
            .grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        barra = ttk.Frame(f, style="Panel.TFrame")
        barra.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(barra, text="Período:", style="Panel.TLabel").pack(side="left")
        self.var_rango = tk.StringVar(value="Últimos 7 días")
        cb = ttk.Combobox(barra, textvariable=self.var_rango, state="readonly",
                          width=16, values=["Hoy", "Últimos 7 días",
                                            "Últimos 30 días"])
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._redibujar_graficos())
        ttk.Button(barra, text="Actualizar",
                   command=self._redibujar_graficos).pack(side="left")

        self.cv_dias = tk.Canvas(f, bg=COL_PANEL, highlightthickness=0)
        self.cv_dias.grid(row=2, column=0, sticky="nsew", padx=(0, 6))
        self.cv_top = tk.Canvas(f, bg=COL_PANEL, highlightthickness=0)
        self.cv_top.grid(row=2, column=1, sticky="nsew", padx=(6, 0))
        for cv in (self.cv_dias, self.cv_top):
            cv.bind("<Configure>", lambda e: self._redibujar_graficos())

    def _fecha_desde(self):
        hoy = datetime.date.today()
        rango = self.var_rango.get()
        if rango == "Hoy":
            return hoy
        if rango == "Últimos 30 días":
            return hoy - datetime.timedelta(days=29)
        return hoy - datetime.timedelta(days=6)

    def _redibujar_graficos(self):
        desde = self._fecha_desde()
        hoy = datetime.date.today()
        con = db()
        por_dia = dict(con.execute(
            "SELECT substr(fecha,1,10), SUM(total) FROM ventas "
            "WHERE substr(fecha,1,10)>=? GROUP BY 1", (desde.isoformat(),)))
        top = con.execute(
            "SELECT vi.nombre, SUM(vi.cantidad), SUM(vi.subtotal) "
            "FROM venta_items vi JOIN ventas v ON v.id=vi.venta_id "
            "WHERE substr(v.fecha,1,10)>=? "
            "GROUP BY vi.nombre ORDER BY SUM(vi.cantidad) DESC LIMIT 10",
            (desde.isoformat(),)).fetchall()
        con.close()

        dias = []
        d = desde
        while d <= hoy:
            dias.append((f"{d:%d/%m}", por_dia.get(d.isoformat(), 0) or 0))
            d += datetime.timedelta(days=1)
        # con 30 días las etiquetas no entran una por una: rotular cada 5
        if len(dias) > 12:
            dias = [(et if i % 5 == 0 else "", v)
                    for i, (et, v) in enumerate(dias)]

        barras_verticales(self.cv_dias, dias,
                          f"Facturación por día — {self.var_rango.get().lower()}")
        barras_horizontales(self.cv_top, top,
                            f"Productos más vendidos — "
                            f"{self.var_rango.get().lower()}")

    # ================================================= TAB CONFIGURACIÓN

    def _armar_tab_config(self):
        f = self.tab_cfg
        f.columnconfigure(0, weight=0, minsize=430)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(2, weight=1)

        ttk.Label(f, text="Configuración", style="Titulo.TLabel")\
            .grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        # --- datos del local (salen en el recibo)
        datos = ttk.Labelframe(f, text=" Datos del local (encabezado del recibo) ",
                               padding=12)
        datos.grid(row=1, column=0, sticky="new", padx=(0, 10))
        datos.columnconfigure(1, weight=1)
        self.var_c_nombre = tk.StringVar(value=cfg_get("nombre"))
        self.var_c_eslogan = tk.StringVar(value=cfg_get("eslogan"))
        self.var_c_dir = tk.StringVar(value=cfg_get("direccion"))
        self.var_c_tel = tk.StringVar(value=cfg_get("telefono"))
        for i, (txt, var) in enumerate([("Nombre:", self.var_c_nombre),
                                        ("Eslogan:", self.var_c_eslogan),
                                        ("Dirección:", self.var_c_dir),
                                        ("Teléfono:", self.var_c_tel)]):
            ttk.Label(datos, text=txt).grid(row=i, column=0, sticky="w", pady=4)
            ttk.Entry(datos, textvariable=var).grid(row=i, column=1,
                                                    sticky="ew", pady=4)
        ttk.Button(datos, text="💾  Guardar datos", style="Accent.TButton",
                   command=self._guardar_config)\
            .grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        # --- impresora
        imp = ttk.Labelframe(f, text=" Impresora de tickets ", padding=12)
        imp.grid(row=2, column=0, sticky="new", padx=(0, 10), pady=(10, 0))
        imp.columnconfigure(1, weight=1)
        self.var_imp_modo = tk.StringVar(value=cfg_get("imp_modo", "sistema"))
        ttk.Radiobutton(imp, text="Impresora del sistema (predeterminada)",
                        value="sistema", variable=self.var_imp_modo)\
            .grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Radiobutton(imp, text="Térmica ESC/POS por red — IP:puerto",
                        value="red", variable=self.var_imp_modo)\
            .grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.var_imp_red = tk.StringVar(value=cfg_get("imp_red"))
        ttk.Entry(imp, textvariable=self.var_imp_red, width=24)\
            .grid(row=2, column=0, columnspan=2, sticky="w", padx=(24, 0),
                  pady=(0, 4))
        ttk.Radiobutton(imp, text="Térmica ESC/POS por USB — dispositivo",
                        value="dispositivo", variable=self.var_imp_modo)\
            .grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.var_imp_dev = tk.StringVar(value=cfg_get("imp_dev"))
        ttk.Entry(imp, textvariable=self.var_imp_dev, width=24)\
            .grid(row=4, column=0, columnspan=2, sticky="w", padx=(24, 0),
                  pady=(0, 4))
        self.var_imp_corte = tk.BooleanVar(value=cfg_get("imp_corte", "1") == "1")
        ttk.Checkbutton(imp, text="Cortar el papel al final (térmicas)",
                        variable=self.var_imp_corte)\
            .grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 2))
        fila_imp = ttk.Frame(imp)
        fila_imp.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(fila_imp, text="💾  Guardar impresora",
                   command=self._guardar_impresora).pack(side="left")
        ttk.Button(fila_imp, text="🖨  Ticket de prueba",
                   command=self._ticket_prueba).pack(side="left", padx=8)

        # --- mesas y mozos
        mesas = ttk.Labelframe(f, text=" Mesas y mozos ", padding=12)
        mesas.grid(row=1, column=1, rowspan=2, sticky="nsew")
        mesas.columnconfigure(0, weight=1)
        mesas.rowconfigure(2, weight=1)

        fila = ttk.Frame(mesas)
        fila.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(fila, text="Cantidad de mesas:").pack(side="left")
        self.var_cant_mesas = tk.IntVar(value=self._contar_mesas())
        ttk.Spinbox(fila, from_=1, to=99, width=5,
                    textvariable=self.var_cant_mesas).pack(side="left", padx=6)
        ttk.Button(fila, text="Aplicar",
                   command=self._aplicar_cant_mesas).pack(side="left")

        self.tree_mesas_cfg = ttk.Treeview(
            mesas, columns=("mozo", "estado"), height=8)
        self.tree_mesas_cfg.heading("#0", text="Mesa")
        self.tree_mesas_cfg.heading("mozo", text="Mozo/a asignado")
        self.tree_mesas_cfg.heading("estado", text="Estado")
        self.tree_mesas_cfg.column("#0", width=80)
        self.tree_mesas_cfg.column("mozo", width=180)
        self.tree_mesas_cfg.column("estado", width=90, anchor="center")
        self.tree_mesas_cfg.grid(row=2, column=0, columnspan=2, sticky="nsew")

        fila2 = ttk.Frame(mesas)
        fila2.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(fila2, text="Mozo/a:").pack(side="left")
        self.var_mozo_cfg = tk.StringVar()
        ttk.Entry(fila2, textvariable=self.var_mozo_cfg,
                  width=20).pack(side="left", padx=6)
        ttk.Button(fila2, text="Asignar a la(s) mesa(s) seleccionada(s)",
                   command=self._asignar_mozo).pack(side="left")

        # --- mantenimiento
        mant = ttk.Frame(mesas)
        mant.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(mant, text="📂  Abrir carpeta de recibos",
                   command=lambda: abrir_carpeta(RECIBOS_DIR)).pack(side="left")
        ttk.Button(mant, text="🗄  Hacer backup ahora",
                   command=self._backup_ahora).pack(side="left", padx=8)
        ttk.Button(mant, text="🔄  Recargar carta El Horno de Leo",
                   command=self._recargar_carta).pack(side="left")
        ttk.Label(mesas,
                  text=f"Backup automático diario (se conservan los últimos "
                       f"{BACKUPS_A_CONSERVAR}) en:\n{BACKUPS_DIR}",
                  style="Panel.TLabel", foreground=COL_MUTED, justify="left",
                  wraplength=520)\
            .grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self._cargar_mesas_cfg()

    def _contar_mesas(self):
        con = db()
        n = con.execute("SELECT COUNT(*) FROM mesas").fetchone()[0]
        con.close()
        return n

    def _guardar_config(self):
        cfg_set("nombre", self.var_c_nombre.get().strip())
        cfg_set("eslogan", self.var_c_eslogan.get().strip())
        cfg_set("direccion", self.var_c_dir.get().strip())
        cfg_set("telefono", self.var_c_tel.get().strip())
        self.title("Gestión — " + cfg_get("nombre", "Restaurante"))
        messagebox.showinfo("Configuración", "Datos guardados.", parent=self)

    def _guardar_impresora(self):
        cfg_set("imp_modo", self.var_imp_modo.get())
        cfg_set("imp_red", self.var_imp_red.get().strip())
        cfg_set("imp_dev", self.var_imp_dev.get().strip())
        cfg_set("imp_corte", "1" if self.var_imp_corte.get() else "0")
        messagebox.showinfo("Impresora", "Configuración de impresora guardada.",
                            parent=self)

    def _ticket_prueba(self):
        cfg_set("imp_modo", self.var_imp_modo.get())
        cfg_set("imp_red", self.var_imp_red.get().strip())
        cfg_set("imp_dev", self.var_imp_dev.get().strip())
        cfg_set("imp_corte", "1" if self.var_imp_corte.get() else "0")
        texto = armar_recibo("TICKET DE PRUEBA", "-",
                             [(1, "Prueba de impresión", 0)], 0,
                             nota="Si leés esto, la impresora funciona")
        ruta, error = imprimir_texto(texto, "prueba")
        if error:
            messagebox.showwarning(
                "Impresión", f"No se pudo imprimir: {error}\n\n"
                f"Copia guardada en:\n{ruta}", parent=self)
        else:
            messagebox.showinfo("Impresión", "Ticket de prueba enviado.",
                                parent=self)

    def _backup_ahora(self):
        ruta = backup_manual()
        messagebox.showinfo("Backup", f"Backup guardado en:\n{ruta}", parent=self)

    def _recargar_carta(self):
        if not messagebox.askyesno(
                "Recargar carta",
                "Esto BORRA todos los productos actuales y vuelve a cargar la "
                "carta original de El Horno de Leo.\n¿Continuar?", parent=self):
            return
        con = db()
        seed_carta(con.cursor())
        con.commit()
        con.close()
        self._cargar_productos()
        messagebox.showinfo("Carta", "Carta recargada.", parent=self)

    def _cargar_mesas_cfg(self):
        self.tree_mesas_cfg.delete(*self.tree_mesas_cfg.get_children())
        con = db()
        for numero, mozo, abierta in con.execute(
                "SELECT numero, mozo, abierta FROM mesas ORDER BY numero"):
            self.tree_mesas_cfg.insert(
                "", "end", iid=str(numero), text=f"Mesa {numero}",
                values=(mozo or "-", "Ocupada" if abierta else "Libre"))
        con.close()
        self.var_cant_mesas.set(self._contar_mesas())

    def _aplicar_cant_mesas(self):
        deseadas = self.var_cant_mesas.get()
        con = db()
        actuales = [n for (n,) in con.execute(
            "SELECT numero FROM mesas ORDER BY numero")]
        if deseadas > len(actuales):
            siguiente = (max(actuales) + 1) if actuales else 1
            con.executemany(
                "INSERT INTO mesas(numero) VALUES (?)",
                [(n,) for n in range(siguiente,
                                     siguiente + deseadas - len(actuales))])
        elif deseadas < len(actuales):
            a_borrar = actuales[deseadas:]
            ocupadas = [n for (n,) in con.execute(
                f"SELECT numero FROM mesas WHERE abierta=1 AND numero IN "
                f"({','.join('?' * len(a_borrar))})", a_borrar)]
            if ocupadas:
                con.close()
                messagebox.showerror(
                    "Mesas", "No se pueden quitar mesas ocupadas: "
                    + ", ".join(map(str, ocupadas)), parent=self)
                return
            con.executemany("DELETE FROM mesas WHERE numero=?",
                            [(n,) for n in a_borrar])
        con.commit()
        con.close()
        self._cargar_mesas_cfg()
        self.refrescar_mesas()

    def _asignar_mozo(self):
        sel = self.tree_mesas_cfg.selection()
        if not sel:
            messagebox.showinfo("Mozos", "Seleccioná una o más mesas en la lista.",
                                parent=self)
            return
        mozo = self.var_mozo_cfg.get().strip()
        con = db()
        con.executemany("UPDATE mesas SET mozo=? WHERE numero=?",
                        [(mozo, int(iid)) for iid in sel])
        con.commit()
        con.close()
        self._cargar_mesas_cfg()
        self.refrescar_mesas()


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    init_db()
    backup_auto()
    app = App()
    app.mainloop()
