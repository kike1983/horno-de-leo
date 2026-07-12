#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
El Horno de Leo — Gestión del restaurante
==========================================
Sistema de administración: productos por categoría con control de stock y
promociones por tiempo limitado, mesas con mozo asignado, cuentas por mesa
o por comensal, ventas de mostrador y delivery con registro por canal,
medios de pago, impresión de recibos (impresora del sistema o térmica
ESC/POS), comandas de cocina, estadísticas con gráficos, backup automático
diario y comandera web para que los mozos tomen pedidos desde el celular
(misma red WiFi).

Funciona en Linux y Windows con Python 3.8+ (solo usa la librería estándar).

Ejecutar:  python3 restaurante.py   (Linux)
           python restaurante.py    (Windows)
"""

import os
import sys
import csv
import glob
import json
import base64
import socket
import shutil
import sqlite3
import datetime
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import font as tkfont

import comandera  # servidor web para que los mozos pidan desde el celular

# ---------------------------------------------------------------- rutas / constantes

VERSION = "2.0.1"

APP_DIR = os.path.join(os.path.expanduser("~"), ".restaurante_armenio")
DB_PATH = os.path.join(APP_DIR, "restaurante.db")
RECIBOS_DIR = os.path.join(APP_DIR, "recibos")
BACKUPS_DIR = os.path.join(APP_DIR, "backups")
BACKUPS_A_CONSERVAR = 30

CATEGORIAS = ["Entrada", "Armenios", "Minutas", "Pizzería", "Bebida", "Postre"]
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


def ruta_recurso(nombre):
    """Archivo que acompaña al programa (icono, etc.). Funciona igual
    ejecutando el .py suelto o dentro del .exe de PyInstaller."""
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nombre)


def fmt(x):
    """Formatea moneda estilo $ 1.234,56"""
    s = f"{x:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return f"$ {s}"


def fmt_corto(x):
    """Monto sin decimales para etiquetas de gráficos: 12.400"""
    return f"{x:,.0f}".replace(",", ".")


# ---------------------------------------------------------------- promociones

def promo_vigente(promo_precio, desde, hasta, hoy=None):
    """True si el producto tiene un precio de promoción activo hoy.
    `desde`/`hasta` son fechas AAAA-MM-DD (vacío = sin límite)."""
    if not promo_precio or promo_precio <= 0:
        return False
    hoy = (hoy or datetime.date.today()).isoformat()
    return (not desde or desde <= hoy) and (not hasta or hoy <= hasta)


def precio_vigente(precio, promo_precio, desde, hasta):
    """Precio a cobrar hoy: el de promoción si está activa, si no el normal."""
    if promo_vigente(promo_precio, desde, hasta):
        return promo_precio
    return precio


CANAL_NOMBRE = {"salon": "Salón", "mostrador": "Mostrador",
                "delivery": "Delivery"}


# ---------------------------------------------------------------- base de datos

def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    # la comandera escribe desde otro hilo: esperar si la base está ocupada
    con.execute("PRAGMA busy_timeout = 4000")
    return con


# Carta real: "El Horno de Leo — Cocina Armenia"
CARTA_HORNO_DE_LEO = [
    # Entradas
    ("Tabla Armenia", 390, "Entrada"),
    ("Pan Lavash", 120, "Entrada"),
    ("Hummus de Garbanzo", 280, "Entrada"),
    ("Salsa de Yogurt", 280, "Entrada"),
    # Platos armenios
    ("Lehemeyun Clásico", 125, "Armenios"),
    ("Lehemeyun Especial", 155, "Armenios"),
    ("Lehemeyun con Muzza", 155, "Armenios"),
    ("Shawarma Clásico", 460, "Armenios"),
    ("Shawarma de Pollo", 490, "Armenios"),
    ("Shawarma Vegetariano", 460, "Armenios"),
    ("Shawarma Vegano", 460, "Armenios"),
    ("Shawarma de Falafel", 460, "Armenios"),
    ("Shawarma Clásico + Fritas", 590, "Armenios"),
    ("Shawarma de Pollo + Fritas", 630, "Armenios"),
    ("Falafel con Guarnición", 480, "Armenios"),
    ("Borek de Queso con Guarnición", 430, "Armenios"),
    # Minutas
    ("Milanesa de Carne c/Guarnición", 520, "Minutas"),
    ("Milanesa Armenia c/Guarnición", 650, "Minutas"),
    ("Milanesa al Pan c/Fritas", 650, "Minutas"),
    ("Papas Fritas", 220, "Minutas"),
    ("Papas Fritas c/Cheddar", 350, "Minutas"),
    ("Papas Rústicas", 260, "Minutas"),
    ("Nuggets c/Fritas", 390, "Minutas"),
    ("Bastones de Muzarella", 390, "Minutas"),
    # Pizzería (los gustos se marcan al pedir y se cobran por gusto)
    ("Pizzeta c/Muzza", 450, "Pizzería"),
    ("Tere c/Muzza", 550, "Pizzería"),
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
        CREATE TABLE IF NOT EXISTS clientes(
            telefono TEXT PRIMARY KEY,
            nombre TEXT DEFAULT '',
            direccion TEXT DEFAULT '',
            pedidos INTEGER DEFAULT 0,
            ultimo TEXT DEFAULT ''
        );
    """)
    # migraciones de versiones anteriores
    _agregar_columna(cur, "productos", "usar_stock", "INTEGER DEFAULT 0")
    _agregar_columna(cur, "productos", "stock", "INTEGER DEFAULT 0")
    _agregar_columna(cur, "productos", "stock_min", "INTEGER DEFAULT 0")
    _agregar_columna(cur, "ventas", "medio", "TEXT DEFAULT 'Efectivo'")
    _agregar_columna(cur, "mesas", "pide_cuenta", "INTEGER DEFAULT 0")
    # v1.5: promociones por tiempo y ventas de mostrador / delivery
    _agregar_columna(cur, "productos", "promo_precio", "REAL DEFAULT 0")
    _agregar_columna(cur, "productos", "promo_desde", "TEXT DEFAULT ''")
    _agregar_columna(cur, "productos", "promo_hasta", "TEXT DEFAULT ''")
    _agregar_columna(cur, "ventas", "canal", "TEXT DEFAULT 'salon'")
    _agregar_columna(cur, "ventas", "cliente", "TEXT DEFAULT ''")
    # v1.9: la vieja categoría "Menú" se separa en Armenios/Minutas/Pizzería
    for categoria, patrones in [
            ("Pizzería", ("Pizzeta%", "Tere %", "Gusto Extra%")),
            ("Armenios", ("Lehemeyun%", "Shawarma%", "Falafel%", "Borek%")),
            ("Minutas", ("Milanesa%", "Papas%", "Nuggets%", "Bastones%"))]:
        for patron in patrones:
            cur.execute("UPDATE productos SET categoria=? "
                        "WHERE categoria='Menú' AND nombre LIKE ?",
                        (categoria, patron))
    # lo que quede en "Menú" (productos agregados a mano) pasa a Minutas
    cur.execute("UPDATE productos SET categoria='Minutas' "
                "WHERE categoria='Menú'")
    # v1.9.1: "Pizzeta 1 Gusto" y "Gusto Extra" salen de la carta: los
    # gustos se marcan sobre la pizzeta c/muzza o el tere, y cada gusto
    # se cobra según el precio de configuración (precio_gusto)
    if not cur.execute("SELECT 1 FROM config "
                       "WHERE clave='mig_pizzeta'").fetchone():
        viejo = cur.execute("SELECT precio FROM productos WHERE nombre "
                            "LIKE 'Gusto Extra%' ORDER BY id LIMIT 1")\
            .fetchone()
        if viejo:  # conservar el precio que tenía el Gusto Extra
            cur.execute("INSERT OR REPLACE INTO config(clave, valor) "
                        "VALUES ('precio_gusto', ?)", (str(viejo[0]),))
        cur.execute("DELETE FROM productos WHERE nombre='Pizzeta 1 Gusto' "
                    "OR nombre LIKE 'Gusto Extra%'")
        cur.execute("INSERT OR REPLACE INTO config(clave, valor) "
                    "VALUES ('mig_pizzeta', '1')")
    # WAL: la interfaz y la comandera pueden leer/escribir a la vez
    con.commit()  # cerrar la transacción de las migraciones (WAL lo exige)
    cur.execute("PRAGMA journal_mode = WAL")

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
            ("imp_corte", "1"),
            ("imp_grande", "1"),
            ("precio_gusto", "90"),
            ("mozos_activo", "1"),
            ("mozos_puerto", str(comandera.PUERTO_DEFECTO)),
            ("mozos_comanda", "1"),
            ("update_auto", "1"),
            ("update_url", URL_ACTUALIZACIONES)]:
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


# ---------------------------------------------------------------- pizzería (gustos)
# Las pizzetas llevan selección de gustos. "Pizzeta 1 Gusto" incluye uno;
# cada gusto que exceda lo incluido se cobra como "Gusto Extra (pizzeta)".
# Los gustos elegidos quedan en el nombre del ítem (los ve la cocina).

GUSTOS_PIZZA = ["Aceitunas", "Tomate", "Panceta", "Roquefort",
                "Albahaca", "Cheddar", "Rúcula"]


def lleva_gustos(nombre, categoria):
    """True si el producto abre la selección de gustos (pizzetas y teres;
    no el "Gusto Extra", que es el adicional que se cobra)."""
    return categoria == "Pizzería" and not nombre.startswith("Gusto Extra")


def gustos_incluidos(nombre):
    """Cuántos gustos incluye el precio base ("Pizzeta 1 Gusto" -> 1)."""
    return 1 if "1 gusto" in nombre.lower() else 0


def precio_gusto_extra():
    """Lo que suma cada gusto de pizzería (configurable en Productos)."""
    try:
        return float(cfg_get("precio_gusto", "90") or 0)
    except ValueError:
        return 90.0


def aplicar_gustos(nombre, precio, gustos):
    """Devuelve (nombre_final, precio_final) con los gustos marcados."""
    gustos = [g for g in gustos if g in GUSTOS_PIZZA]
    if not gustos:
        return nombre, precio
    extras = max(0, len(gustos) - gustos_incluidos(nombre))
    if extras:
        precio = precio + extras * precio_gusto_extra()
    return f"{nombre} ({', '.join(gustos)})", precio


# ---------------------------------------------------------------- código QR
# Generador de QR mínimo, solo librería estándar (byte mode, corrección M,
# versiones 1-3 = hasta 42 caracteres, máscara 0). Verificado contra segno
# y el lector de OpenCV.

_QR_EXP = [0] * 512
_QR_LOG = [0] * 256
_x = 1
for _i in range(255):
    _QR_EXP[_i] = _x
    _QR_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11d
for _i in range(255, 512):
    _QR_EXP[_i] = _QR_EXP[_i - 255]

# versión -> (bytes de datos, códigos de corrección) para nivel M
_QR_VERSIONES = {1: (16, 10), 2: (28, 16), 3: (44, 26)}
_QR_ALINEACION = {1: [], 2: [6, 18], 3: [6, 22]}


def _qr_rs(datos, n_ecc):
    """Códigos Reed-Solomon de los datos."""
    gen = [1]
    for i in range(n_ecc):
        nuevo = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nuevo[j] ^= c
            nuevo[j + 1] ^= _QR_EXP[(_QR_LOG[c] + i) % 255] if c else 0
        gen = nuevo
    resto = list(datos) + [0] * n_ecc
    for i in range(len(datos)):
        factor = resto[i]
        if factor:
            for j, c in enumerate(gen):
                if c:
                    resto[i + j] ^= _QR_EXP[(_QR_LOG[factor]
                                             + _QR_LOG[c]) % 255]
    return resto[len(datos):]


def matriz_qr(texto):
    """Matriz de módulos (0/1) del QR para `texto`."""
    datos = texto.encode("utf-8")
    version = None
    for v, (cap, _) in _QR_VERSIONES.items():
        if len(datos) <= cap - 2:
            version = v
            break
    if version is None:
        raise ValueError("Texto demasiado largo para el QR (máx. 42).")
    cap, n_ecc = _QR_VERSIONES[version]
    lado = 17 + 4 * version

    bits = "0100" + format(len(datos), "08b")
    for b in datos:
        bits += format(b, "08b")
    bits += "0000"
    bits += "0" * ((8 - len(bits) % 8) % 8)
    cw = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    relleno = [0xEC, 0x11]
    i = 0
    while len(cw) < cap:
        cw.append(relleno[i % 2])
        i += 1
    cw += _qr_rs(cw, n_ecc)

    m = [[None] * lado for _ in range(lado)]

    def finder(fila, col):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = fila + r, col + c
                if 0 <= rr < lado and 0 <= cc < lado:
                    dentro = 0 <= r <= 6 and 0 <= c <= 6
                    borde = r in (0, 6) or c in (0, 6)
                    centro = 2 <= r <= 4 and 2 <= c <= 4
                    m[rr][cc] = 1 if dentro and (borde or centro) else 0

    finder(0, 0)
    finder(0, lado - 7)
    finder(lado - 7, 0)
    for k in range(8, lado - 8):
        m[6][k] = m[k][6] = (k + 1) % 2
    centros = _QR_ALINEACION[version]
    for fa in centros:
        for ca in centros:
            if m[fa][ca] is not None:
                continue  # pisa un buscador
            for r in range(-2, 3):
                for c in range(-2, 3):
                    m[fa + r][ca + c] = 1 if max(abs(r), abs(c)) != 1 else 0
    m[lado - 8][8] = 1  # módulo oscuro
    for k in range(9):
        if m[8][k] is None:
            m[8][k] = 0
        if m[k][8] is None:
            m[k][8] = 0
    for k in range(8):
        if m[8][lado - 1 - k] is None:
            m[8][lado - 1 - k] = 0
        if m[lado - 1 - k][8] is None:
            m[lado - 1 - k][8] = 0

    todos = "".join(format(b, "08b") for b in cw)
    idx = 0
    col = lado - 1
    subir = True
    while col > 0:
        if col == 6:
            col -= 1
        filas = range(lado - 1, -1, -1) if subir else range(lado)
        for fila in filas:
            for cc in (col, col - 1):
                if m[fila][cc] is None:
                    bit = int(todos[idx]) if idx < len(todos) else 0
                    idx += 1
                    if (fila + cc) % 2 == 0:
                        bit ^= 1
                    m[fila][cc] = bit
        subir = not subir
        col -= 2

    formato = 0b00000  # nivel M, máscara 0
    resto = formato << 10
    for k in range(4, -1, -1):
        if resto >> (k + 10):
            resto ^= 0x537 << k
    fmt = ((formato << 10) | resto) ^ 0x5412
    fbits = [(fmt >> (14 - k)) & 1 for k in range(15)]
    pos_a = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
             (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    pos_b = [(lado - 1, 8), (lado - 2, 8), (lado - 3, 8), (lado - 4, 8),
             (lado - 5, 8), (lado - 6, 8), (lado - 7, 8),
             (8, lado - 8), (8, lado - 7), (8, lado - 6), (8, lado - 5),
             (8, lado - 4), (8, lado - 3), (8, lado - 2), (8, lado - 1)]
    for (fa, ca), (fb, cb), bit in zip(pos_a, pos_b, fbits):
        m[fa][ca] = bit
        m[fb][cb] = bit
    return m


def svg_qr(texto, modulo=6, borde=2, color="#2b2118"):
    """El QR como imagen SVG (para incrustar en HTML e imprimir)."""
    m = matriz_qr(texto)
    lado = len(m)
    total = (lado + 2 * borde) * modulo
    partes = [f'<svg xmlns="http://www.w3.org/2000/svg" '
              f'viewBox="0 0 {total} {total}"><rect width="100%" '
              f'height="100%" fill="#ffffff"/>']
    for f in range(lado):
        for c in range(lado):
            if m[f][c]:
                partes.append(
                    f'<rect x="{(c + borde) * modulo}" '
                    f'y="{(f + borde) * modulo}" width="{modulo}" '
                    f'height="{modulo}" fill="{color}"/>')
    partes.append("</svg>")
    return "".join(partes)


# ---------------------------------------------------------------- carta digital
# Carta para los clientes: se publica en internet (GitHub Pages) así se
# abre desde el QR de la mesa sin necesidad del WiFi del local. El programa
# la vuelve a publicar solo cuando cambian productos, precios o promos.

URL_CARTA = "https://kike1983.github.io/horno-de-leo/"
_API_CARTA = ("https://api.github.com/repos/kike1983/horno-de-leo/"
              "contents/docs/index.html")

DESCRIPCIONES = {
    "Tabla Armenia": "Pan lavash acompañado de hummus, salsa de yogurt y tabule",
    "Hummus de Garbanzo": "Pote de 250 g",
    "Salsa de Yogurt": "Yogurt natural, pepino y ajo",
    "Lehemeyun Clásico": "Pan plano tradicional con carne picada, verduras y especias",
    "Lehemeyun Especial": "Clásico con picadillo de tomate, lechuga, cebolla y salsa de yogurt",
    "Lehemeyun con Muzza": "Pan, muzzarella, tomate y albahaca",
    "Shawarma Clásico": "Pan lavash, hummus, bondiola de cerdo, salsa de yogurt, tomate, cebolla, zanahoria, repollo y lechuga",
    "Shawarma de Pollo": "Clásico con picadillo de tomate, lechuga, cebolla y salsa de yogurt",
    "Shawarma Vegetariano": "Con muzzarella y tabule",
    "Falafel con Guarnición": "Croquetas de garbanzo fritas",
    "Borek de Queso con Guarnición": "Torta de ricota, parmesano y muzzarella",
    "Milanesa al Pan c/Fritas": "Pan ciabatta, tomate y huevo",
    "Bastones de Muzarella": "8 unidades con dip de salsa de yogurt y salsa de tomate",
    "Refresco 600 ml": "Línea Coca",
    "Vino Catamayor 375 ml": "Reserva tannat · sauvignon blanc",
    "Baklava": "Capas de masa filo con pistacho y nueces, bañada en almíbar",
}


def _plata_carta(x):
    return f"$ {x:,.0f}".replace(",", ".")


def _sub_bebida(nombre):
    n = nombre.lower()
    if any(p in n for p in ("cerveza", "miller", "scheider", "heineken")):
        return "Cervezas"
    if any(p in n for p in ("vino", "catamayor")):
        return "Vinos"
    return "Refrescos"


def _item_carta(nombre, precio, pp, pdesde, phasta):
    vigente = precio_vigente(precio, pp, pdesde, phasta)
    if vigente != precio:
        precio_html = (f'<span class="antes">{_plata_carta(precio)}</span>'
                       f'<span class="pill">PROMO</span> '
                       f'{_plata_carta(vigente)}')
    else:
        precio_html = _plata_carta(precio)
    desc = DESCRIPCIONES.get(nombre, "")
    d = f'<p class="desc">{desc}.</p>' if desc else ""
    return (f'<div class="item"><div class="fila"><span class="nom">'
            f'{nombre}</span><span class="puntos"></span>'
            f'<span class="precio">{precio_html}</span></div>{d}</div>')


def generar_carta_html():
    """Arma la carta completa (HTML autosuficiente) con los datos vivos."""
    con = db()
    rows = con.execute(
        "SELECT nombre, precio, categoria, promo_precio, promo_desde, "
        "promo_hasta FROM productos ORDER BY nombre").fetchall()
    con.close()
    por_cat = {}
    for nombre, precio, cat, pp, pd, ph in rows:
        por_cat.setdefault(cat, []).append((nombre, precio, pp, pd, ph))

    etiquetas = {"Entrada": "Entradas", "Armenios": "Platos Armenios",
                 "Minutas": "Minutas", "Pizzería": "Pizzería",
                 "Bebida": "Bebidas", "Postre": "Postres"}
    nav, secciones = [], []
    for cat in CATEGORIAS:
        items = por_cat.get(cat)
        if not items:
            continue
        sid = cat.lower().replace("í", "i")
        titulo = etiquetas.get(cat, cat)
        nav.append(f'<a href="#{sid}">{titulo}</a>')
        if cat == "Bebida":
            grupos = {}
            for it in items:
                grupos.setdefault(_sub_bebida(it[0]), []).append(it)
            cuerpo = ""
            for sub in ("Refrescos", "Cervezas", "Vinos"):
                if grupos.get(sub):
                    cuerpo += f'<h3 class="sub">{sub}</h3>' + "".join(
                        _item_carta(*it) for it in grupos[sub])
        else:
            cuerpo = "".join(_item_carta(*it) for it in items)
        if cat == "Pizzería":
            chips = "".join(f'<span class="chip">{g}</span>'
                            for g in GUSTOS_PIZZA)
            cuerpo += ('<div class="gustos"><p class="gtit">Elegí los '
                       f'gustos</p>{chips}<p class="al-pie">Cada gusto suma '
                       f'{_plata_carta(precio_gusto_extra())} · se marcan al '
                       'hacer el pedido</p></div>')
        if cat == "Armenios":
            cuerpo += ('<p class="al-pie">Guarniciones: fritas · ensalada · '
                       'rústicas</p>')
        secciones.append(
            f'<section id="{sid}"><h2><span class="raya"></span>'
            f'<span class="stit">{titulo}</span><span class="raya"></span>'
            f'</h2>{cuerpo}</section>')

    logo_html = marca_agua = ""
    try:
        with open(ruta_recurso("icono.png"), "rb") as f:
            logo64 = base64.b64encode(f.read()).decode()
        logo_html = (f'<img src="data:image/png;base64,{logo64}" '
                     'alt="El Horno de Leo">')
        marca_agua = ('<div class="marca-agua" style="background-image:url('
                      f'data:image/png;base64,{logo64})"></div>')
    except OSError:
        pass

    nombre_local = cfg_get("nombre", "El Horno de Leo")
    eslogan = cfg_get("eslogan", "")
    pie_local = " · ".join(x for x in [cfg_get("direccion"),
                                       cfg_get("telefono")] if x)
    ahora = datetime.datetime.now()
    return CARTA_PLANTILLA.format(
        titulo=nombre_local, logo=logo_html, marca_agua=marca_agua,
        nombre=nombre_local.upper(), eslogan=eslogan,
        nav="".join(nav), secciones="".join(secciones),
        pie_local=(f'<p class="pago">{pie_local}</p>' if pie_local else ""),
        actualizada=f"{ahora:%d/%m/%Y %H:%M}")


def publicar_carta():
    """Sube la carta a GitHub Pages. Devuelve None si salió bien o el
    mensaje de error."""
    token = cfg_get("gh_token", "").strip()
    if not token:
        return ("Falta el código de publicación (pestaña Configuración → "
                "Carta digital).")
    import urllib.request
    import urllib.error
    html = generar_carta_html()
    cab = {"Authorization": "Bearer " + token,
           "Accept": "application/vnd.github+json",
           "User-Agent": "HornoDeLeo"}
    sha = None
    try:
        with urllib.request.urlopen(urllib.request.Request(
                _API_CARTA, headers=cab), timeout=15) as r:
            sha = json.loads(r.read()).get("sha")
    except Exception:
        pass  # todavía no existe el archivo
    cuerpo = {"message": "Carta actualizada desde el programa",
              "content": base64.b64encode(html.encode("utf-8")).decode()}
    if sha:
        cuerpo["sha"] = sha
    try:
        with urllib.request.urlopen(urllib.request.Request(
                _API_CARTA, data=json.dumps(cuerpo).encode("utf-8"),
                headers=cab, method="PUT"), timeout=30):
            pass
        return None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ("GitHub rechazó el código de publicación (¿venció o "
                    "está mal pegado?).")
        return f"GitHub respondió con error {e.code}."
    except Exception as e:
        return f"Sin conexión con GitHub: {e}"


def generar_cartelitos_html(cant_mesas):
    """Hoja imprimible con un cartelito con QR por mesa."""
    qr = svg_qr(URL_CARTA, modulo=6, borde=2)
    logo = ""
    try:
        with open(ruta_recurso("icono.png"), "rb") as f:
            logo = ('<img src="data:image/png;base64,'
                    + base64.b64encode(f.read()).decode() + '">')
    except OSError:
        pass
    tarjetas = "".join(f"""
  <div class="tarjeta">{logo}
    <h3>MIRÁ NUESTRA CARTA</h3>
    <p class="peq">desde tu celular, al instante</p>
    {qr}
    <p class="mesa">Escaneá con la cámara · MESA {n}</p>
  </div>""" for n in range(1, cant_mesas + 1))
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Cartelitos QR — carta</title>
<style>
 body{{font-family:Georgia,serif;background:#eee;margin:0;padding:20px}}
 .hoja{{display:flex;flex-wrap:wrap;gap:14px;justify-content:center}}
 .tarjeta{{width:300px;background:#faf5e9;border:2px solid #cfc09a;
  outline:1px solid #cfc09a;outline-offset:5px;border-radius:4px;
  text-align:center;padding:24px 18px 18px;margin:6px;
  page-break-inside:avoid}}
 .tarjeta img{{width:58px;mix-blend-mode:multiply}}
 h3{{margin:6px 0 2px;letter-spacing:.12em;font-size:1rem;color:#2b2118}}
 .peq{{font-style:italic;color:#8c2f39;font-size:.82rem;margin:0 0 12px}}
 svg{{width:180px;height:180px;border:1px solid #cfc09a;border-radius:8px;
  background:#fff}}
 .mesa{{margin:10px 0 0;font-size:.8rem;color:#77674f;
  font-family:system-ui,sans-serif;letter-spacing:.06em}}
 @media print{{body{{background:#fff}}.tarjeta{{box-shadow:none}}}}
</style></head><body>
<p style="text-align:center;font-family:system-ui,sans-serif;font-size:.85rem;
color:#555">Imprimí esta hoja (Ctrl+P), recortá los cartelitos y pegá uno en
cada mesa. Todos llevan a la carta: {URL_CARTA}</p>
<div class="hoja">{tarjetas}</div></body></html>"""


# plantilla de la carta (las llaves de CSS van dobles por el .format)
CARTA_PLANTILLA = """<!doctype html><html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Carta — {titulo}</title>
<style>
:root{{--papel:#f5efe2;--papel2:#ede3cd;--tinta:#2b2118;--oro:#a0782c;
 --oro-suave:#cfc09a;--bordo:#8c2f39;--humo:#77674f;color-scheme:light}}
html{{background:var(--papel);scroll-behavior:smooth}}
body{{margin:0;color:var(--tinta);
 font-family:Georgia,"Palatino Linotype","Noto Serif",serif;
 background:radial-gradient(ellipse at 50% -10%,#fff9ec 0%,transparent 55%),
  linear-gradient(var(--papel),var(--papel2));min-height:100vh}}
.marca-agua{{position:fixed;inset:0;pointer-events:none;z-index:0;
 background-position:center 42%;background-size:min(80vw,420px);
 background-repeat:no-repeat;opacity:.05;mix-blend-mode:multiply}}
.hoja{{position:relative;z-index:1;max-width:560px;margin:0 auto;
 padding:0 20px 48px}}
header{{text-align:center;padding:34px 0 6px}}
header img{{width:104px;mix-blend-mode:multiply}}
h1{{font-size:1.9rem;letter-spacing:.13em;margin:.35em 0 .1em;font-weight:600}}
.eslogan{{font-style:italic;color:var(--bordo);margin:0;font-size:1.02rem}}
.orn{{color:var(--oro);letter-spacing:.5em;margin:14px 0 4px;font-size:.9rem}}
nav{{position:sticky;top:0;z-index:5;display:flex;gap:6px;overflow-x:auto;
 padding:10px 4px;margin:8px -4px 6px;
 background:linear-gradient(#f5efe2f2,#f5efe2e6);scrollbar-width:none}}
nav::-webkit-scrollbar{{display:none}}
nav a{{flex:none;text-decoration:none;color:var(--bordo);
 border:1px solid var(--oro-suave);border-radius:999px;padding:7px 15px;
 font-size:.86rem;letter-spacing:.06em}}
section{{padding-top:10px}}
h2{{display:flex;align-items:center;gap:14px;margin:26px 0 6px}}
h2 .stit{{flex:none;font-size:1.12rem;letter-spacing:.22em;
 text-transform:uppercase;font-weight:600}}
h2 .raya{{flex:1;height:1px;
 background:linear-gradient(90deg,transparent,var(--oro),transparent)}}
.sub{{font-size:.85rem;letter-spacing:.28em;text-transform:uppercase;
 color:var(--oro);margin:20px 0 2px;font-weight:600}}
.item{{padding:9px 0 2px}}
.fila{{display:flex;align-items:baseline;gap:8px}}
.nom{{font-variant:small-caps;letter-spacing:.04em;font-size:1.06rem}}
.puntos{{flex:1;border-bottom:2px dotted var(--oro-suave);
 transform:translateY(-4px);min-width:24px}}
.precio{{font-variant-numeric:tabular-nums;white-space:nowrap;
 font-size:1.02rem}}
.desc{{margin:2px 0 0;font-style:italic;color:var(--humo);font-size:.9rem;
 line-height:1.45;max-width:46ch}}
.antes{{text-decoration:line-through;color:var(--humo);font-size:.88rem;
 margin-right:6px}}
.pill{{background:var(--bordo);color:#fff;border-radius:4px;font-size:.62rem;
 letter-spacing:.12em;padding:2.5px 6px;vertical-align:2px;margin-right:4px;
 font-family:system-ui,sans-serif;font-weight:700}}
.al-pie{{font-style:italic;color:var(--humo);font-size:.88rem;
 text-align:center;margin:14px 0 0}}
.gustos{{margin-top:16px;border:1px solid var(--oro-suave);border-radius:10px;
 padding:14px 16px 16px;text-align:center;background:#fbf6ea66}}
.gtit{{margin:0 0 10px;letter-spacing:.24em;text-transform:uppercase;
 font-size:.8rem;color:var(--oro);font-weight:600}}
.chip{{display:inline-block;border:1px solid var(--oro-suave);
 border-radius:999px;padding:5px 13px;margin:3px 2px;font-size:.88rem;
 font-variant:small-caps;letter-spacing:.05em}}
footer{{margin-top:40px;text-align:center}}
.cita{{font-style:italic;color:var(--humo);font-size:.95rem;max-width:40ch;
 margin:0 auto;line-height:1.55}}
.pago{{font-variant:small-caps;letter-spacing:.14em;margin:18px 0 4px;
 font-size:.98rem}}
.vivo{{color:var(--humo);font-size:.78rem;margin-top:4px}}
</style></head><body>
{marca_agua}
<div class="hoja">
 <header>{logo}
  <h1>{nombre}</h1>
  <p class="eslogan">{eslogan}</p>
  <p class="orn">&#10087; &#10086; &#10087;</p>
 </header>
 <nav>{nav}</nav>
 {secciones}
 <footer>
  <p class="orn">&#10087; &#10086; &#10087;</p>
  <p class="cita">Cada plato refleja la tradición de la cocina armenia,
  elaborada con dedicación y sabores auténticos.</p>
  <p class="pago">Efectivo · MercadoPago · Transferencia</p>
  {pie_local}
  <p class="vivo">Carta actualizada el {actualizada}</p>
 </footer>
</div></body></html>"""


# ---------------------------------------------------------------- clientes (delivery)
# Agenda que se arma sola: cada delivery cobrado guarda o actualiza al
# cliente por su número de celular. Al volver a escribir ese número en una
# venta, el nombre y la dirección se completan automáticamente.

def tel_normalizado(telefono):
    """Deja solo los dígitos: '099 123-456' -> '099123456'."""
    return "".join(c for c in str(telefono) if c.isdigit())


def cliente_buscar(telefono):
    """Devuelve (nombre, direccion, pedidos, ultimo) o None."""
    t = tel_normalizado(telefono)
    if len(t) < 6:
        return None
    con = db()
    row = con.execute("SELECT nombre, direccion, pedidos, ultimo "
                      "FROM clientes WHERE telefono=?", (t,)).fetchone()
    con.close()
    return row


def cliente_guardar(telefono, nombre, direccion):
    """Alta o actualización automática al cobrar un delivery (suma 1 al
    contador de pedidos; un dato vacío no pisa al guardado)."""
    t = tel_normalizado(telefono)
    if len(t) < 6:
        return
    con = db()
    con.execute(
        "INSERT INTO clientes(telefono, nombre, direccion, pedidos, ultimo) "
        "VALUES (?,?,?,1,?) ON CONFLICT(telefono) DO UPDATE SET "
        "nombre=CASE WHEN excluded.nombre<>'' THEN excluded.nombre "
        "            ELSE nombre END, "
        "direccion=CASE WHEN excluded.direccion<>'' THEN excluded.direccion "
        "               ELSE direccion END, "
        "pedidos=pedidos+1, ultimo=excluded.ultimo",
        (t, (nombre or "").strip(), (direccion or "").strip(),
         datetime.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    con.close()


# ---------------------------------------------------------------- actualizaciones
# El programa se puede actualizar solo: consulta version.json en el sitio
# del proyecto y, si hay una versión más nueva, baja los archivos, deja
# copia .anterior de los viejos y se reinicia. Así se le hacen mejoras al
# local a la distancia. Los datos nunca se tocan (viven en ~/.restaurante_armenio).

URL_ACTUALIZACIONES = ("https://raw.githubusercontent.com/"
                       "kike1983/horno-de-leo/main/")


def _numeros_version(v):
    """'1.5.1' -> (1, 5, 1) para poder comparar versiones."""
    try:
        return tuple(int(p) for p in str(v).strip().split("."))
    except ValueError:
        return (0,)


def url_actualizaciones():
    # si la configuración guardada quedara vacía o rota, manda la
    # dirección grabada en el código: nunca se pierden las actualizaciones
    url = cfg_get("update_url", "").strip() or URL_ACTUALIZACIONES
    if url and not url.endswith("/"):
        url += "/"
    return url


def consultar_actualizacion():
    """Lee version.json del sitio de actualizaciones. Devuelve el dict si
    hay una versión más nueva que la instalada; si no, None."""
    import urllib.request
    base = url_actualizaciones()
    if not base:
        return None
    with urllib.request.urlopen(base + "version.json", timeout=10) as r:
        info = json.loads(r.read().decode("utf-8"))
    if _numeros_version(info.get("version")) > _numeros_version(VERSION):
        return info
    return None


def descargar_actualizacion(info, carpeta=None):
    """Baja los archivos de la versión nueva y reemplaza los del programa
    (deja copia .anterior de cada uno). Primero descarga y verifica TODO;
    si algo falla no se toca ningún archivo local."""
    import urllib.request
    base = url_actualizaciones()
    carpeta = carpeta or os.path.dirname(os.path.abspath(__file__))
    nombres = info.get("archivos") or ["restaurante.py", "comandera.py"]
    descargados = []
    for nombre in nombres:
        if os.path.basename(nombre) != nombre:
            continue  # por seguridad solo nombres de archivo, sin rutas
        with urllib.request.urlopen(base + nombre, timeout=60) as r:
            datos = r.read()
        if nombre.endswith(".py"):
            # que la actualización no rompa el programa: tiene que compilar
            compile(datos.decode("utf-8"), nombre, "exec")
        descargados.append((nombre, datos))
    if not descargados:
        raise ValueError("La actualización no trae archivos.")
    for nombre, datos in descargados:
        ruta = os.path.join(carpeta, nombre)
        if os.path.exists(ruta):
            shutil.copy2(ruta, ruta + ".anterior")
        temporal = ruta + ".nuevo"
        with open(temporal, "wb") as fh:
            fh.write(datos)
        os.replace(temporal, ruta)


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


def armar_recibo(titulo, mozo, items, total, nota="", medio="", extra=()):
    """items: lista de (cantidad, nombre, subtotal). `extra` son renglones
    que van debajo del título (datos del cliente en mostrador/delivery).
    Con mozo=None no se imprime el renglón del mozo. Devuelve el texto."""
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
    ]
    if mozo is not None:
        lineas.append(f"Mozo/a: {mozo or '-'}")
    lineas.extend(extra)
    lineas.append("-" * ANCHO_TICKET)
    for cantidad, nombre, subtotal in items:
        lineas.append(linea_item(cantidad, nombre, subtotal))
    lineas.append("-" * ANCHO_TICKET)
    total_txt = fmt(total)
    lineas.append("TOTAL" + " " * (ANCHO_TICKET - 5 - len(total_txt)) + total_txt)
    if medio:
        lineas.append(f"Medio de pago: {medio}")
    lineas.append("=" * ANCHO_TICKET)
    if nota:
        for renglon in nota.split("\n"):
            lineas.append(centrar(renglon))
    lineas.append(centrar("Gracias por preferirnos!"))
    lineas.append("")
    return "\n".join(lineas)


# Caracteres comunes que no existen en CP850, con su reemplazo imprimible
# (si no, la térmica los saca como "?").
_ESCPOS_EQUIV = str.maketrans({"•": "·", "–": "-", "—": "-", "‘": "'",
                               "’": "'", "“": '"', "”": '"', "…": "...",
                               "€": "EUR", "★": "*", "✓": "-"})


def _escpos_bytes(texto):
    """Convierte el ticket a comandos ESC/POS (init, texto CP850, avance y corte)."""
    data = b"\x1b\x40"          # ESC @  inicializar
    data += b"\x1b\x74\x02"     # ESC t 2  página de códigos CP850 (acentos)
    if cfg_get("imp_grande", "1") == "1":
        # ESC ! 16: letra doble alto (entran los mismos 42 caracteres
        # por renglón, pero el doble de altos: más fácil de leer)
        data += b"\x1b\x21\x10"
    data += texto.translate(_ESCPOS_EQUIV).encode("cp850", errors="replace")
    data += b"\n\n\n\n"
    if cfg_get("imp_corte", "1") == "1":
        data += b"\x1d\x56\x42\x00"  # GS V B 0  corte parcial
    return data


def _imprimir_raw_windows(nombre, data):
    """Manda bytes crudos (ESC/POS) a una impresora instalada en Windows.
    `nombre` es el nombre tal como figura en el panel de impresoras de
    Windows; vacío = la predeterminada. Con el tipo de dato RAW el spooler
    pasa los bytes directo al puerto, así que alcanza con cualquier driver
    (incluso "Generic / Text Only")."""
    import ctypes
    from ctypes import wintypes

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR),
                    ("pOutputFile", wintypes.LPWSTR),
                    ("pDatatype", wintypes.LPWSTR)]

    ws = ctypes.WinDLL("winspool.drv")
    ws.OpenPrinterW.argtypes = [wintypes.LPWSTR,
                                ctypes.POINTER(wintypes.HANDLE),
                                ctypes.c_void_p]
    ws.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                    ctypes.c_void_p]
    ws.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_char_p,
                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    for fn in (ws.StartPagePrinter, ws.EndPagePrinter,
               ws.EndDocPrinter, ws.ClosePrinter):
        fn.argtypes = [wintypes.HANDLE]

    if not nombre:
        n = wintypes.DWORD(0)
        ws.GetDefaultPrinterW(None, ctypes.byref(n))
        buf = ctypes.create_unicode_buffer(max(n.value, 1))
        if not ws.GetDefaultPrinterW(buf, ctypes.byref(n)):
            raise OSError("Windows no tiene una impresora predeterminada.")
        nombre = buf.value
    h = wintypes.HANDLE()
    if not ws.OpenPrinterW(nombre, ctypes.byref(h), None):
        raise OSError(f'No existe la impresora "{nombre}" en Windows '
                      "(el nombre tiene que ser igual al del panel "
                      "de impresoras).")
    try:
        doc = DOC_INFO_1("Ticket Horno de Leo", None, "RAW")
        if not ws.StartDocPrinterW(h, 1, ctypes.byref(doc)):
            raise OSError(f'La impresora "{nombre}" no aceptó el trabajo.')
        ws.StartPagePrinter(h)
        escrito = wintypes.DWORD(0)
        ws.WritePrinter(h, data, len(data), ctypes.byref(escrito))
        ws.EndPagePrinter(h)
        ws.EndDocPrinter(h)
    finally:
        ws.ClosePrinter(h)


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
            destino = cfg_get("imp_dev", "").strip()
            if sys.platform.startswith("win"):
                # En Windows el campo es el nombre de la impresora instalada
                # (vacío o una ruta /dev heredada = la predeterminada).
                nombre = "" if destino.startswith("/dev") else destino
                _imprimir_raw_windows(nombre, _escpos_bytes(texto))
            else:
                with open(destino or "/dev/usb/lp0", "wb") as dev:
                    dev.write(_escpos_bytes(texto))
            return ruta, None
        error = _imprimir_sistema(ruta)
        return ruta, error
    except Exception as e:
        return ruta, str(e)


def deps_comandera():
    """Funciones que el módulo comandera necesita (evita import circular)."""
    return {"db": db, "cfg_get": cfg_get, "centrar": centrar,
            "imprimir_texto": imprimir_texto, "categorias": CATEGORIAS,
            "ancho": ANCHO_TICKET, "precio_vigente": precio_vigente,
            "gustos_pizza": GUSTOS_PIZZA, "lleva_gustos": lleva_gustos,
            "gustos_incluidos": gustos_incluidos,
            "precio_gusto_extra": precio_gusto_extra,
            "carta_html": generar_carta_html}


# ---------------------------------------------------------------- IP fija (Windows)
# Para que la dirección de la comandera no cambie, el programa puede fijar
# la IP actual de la PC en Windows (netsh necesita permiso de administrador,
# así que se genera un .bat y se ejecuta con elevación/UAC).

_PS_RED = (
    "Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway} "
    "| Select-Object -First 1 InterfaceAlias,"
    "@{n='IP';e={@($_.IPv4Address)[0].IPAddress}},"
    "@{n='Prefijo';e={@($_.IPv4Address)[0].PrefixLength}},"
    "@{n='Puerta';e={@($_.IPv4DefaultGateway)[0].NextHop}},"
    "@{n='DNS';e={($_.DNSServer | Where-Object {$_.AddressFamily -eq 2})"
    ".ServerAddresses -join ','}} | ConvertTo-Json")


def datos_red_windows():
    """Conexión activa en Windows: alias, IP, prefijo, puerta de enlace, DNS."""
    salida = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _PS_RED],
        capture_output=True, text=True, timeout=25,
        creationflags=0x08000000).stdout  # sin ventana de consola
    info = json.loads(salida)
    if isinstance(info, list):
        info = info[0]
    return info


def mascara_desde_prefijo(prefijo):
    """24 -> 255.255.255.0"""
    bits = (0xffffffff << (32 - int(prefijo))) & 0xffffffff
    return ".".join(str((bits >> s) & 0xff) for s in (24, 16, 8, 0))


def armar_bat_ip_fija(alias, ip, prefijo, puerta, dns):
    """Arma el .bat que deja fija la configuración de red actual."""
    mascara = mascara_desde_prefijo(prefijo)
    lineas = [
        "@echo off", "chcp 65001 >nul",
        f"echo Fijando la IP {ip} en \"{alias}\" ...",
        f"netsh interface ipv4 set address name=\"{alias}\" "
        f"static {ip} {mascara} {puerta}",
    ]
    servidores = [s.strip() for s in (dns or "").split(",") if s.strip()]
    if not servidores:
        servidores = [puerta]  # sin DNS conocido: usar el router
    lineas.append(f"netsh interface ipv4 set dnsservers name=\"{alias}\" "
                  f"static {servidores[0]} primary")
    for i, servidor in enumerate(servidores[1:4], start=2):
        lineas.append(f"netsh interface ipv4 add dnsservers name=\"{alias}\" "
                      f"{servidor} index={i}")
    lineas += ["echo.",
               f"echo Listo: esta PC va a tener siempre la direccion {ip}",
               "pause"]
    return "\r\n".join(lineas) + "\r\n"


def armar_bat_ip_dhcp(alias):
    """Arma el .bat que vuelve a la configuración automática (DHCP)."""
    lineas = [
        "@echo off", "chcp 65001 >nul",
        f"echo Volviendo \"{alias}\" a IP automatica (DHCP) ...",
        f"netsh interface ipv4 set address name=\"{alias}\" dhcp",
        f"netsh interface ipv4 set dnsservers name=\"{alias}\" dhcp",
        "echo.", "echo Listo.", "pause"]
    return "\r\n".join(lineas) + "\r\n"


def ejecutar_bat_admin(texto_bat):
    """Guarda el .bat y lo ejecuta pidiendo permiso de administrador (UAC).
    Devuelve None si arrancó bien o un mensaje de error."""
    ruta = os.path.join(APP_DIR, "configurar_ip.bat")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto_bat)
    import ctypes
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", ruta, None, None, 1)
    if r <= 32:
        return ("No se pudo ejecutar (¿se canceló el permiso de "
                f"administrador?). El archivo quedó en:\n{ruta}")
    return None


# ---------------------------------------------------------------- acceso directo

def asegurar_acceso_directo():
    """En Windows: si falta el acceso directo "El Horno de Leo" en el
    escritorio, lo crea con el logo (apuntando a esta instalación). Corre
    al abrir el programa, así se repara solo aunque el instalador haya
    fallado en ese paso."""
    if not sys.platform.startswith("win"):
        return
    try:
        carpeta = os.path.dirname(os.path.abspath(__file__))
        piton = sys.executable
        pw = os.path.join(os.path.dirname(piton), "pythonw.exe")
        if os.path.basename(piton).lower() == "python.exe" \
                and os.path.exists(pw):
            piton = pw  # sin ventana de consola
        script = os.path.join(carpeta, "restaurante.py")
        icono = os.path.join(carpeta, "icono.ico")
        # PowerShell recibe el guion como UN argumento (sin pasar por las
        # comillas de cmd, que era lo que fallaba en el instalador)
        guion = (
            "$ws = New-Object -ComObject WScript.Shell; "
            "$ruta = [Environment]::GetFolderPath('Desktop') "
            "+ '\\El Horno de Leo.lnk'; "
            "if (-not (Test-Path $ruta)) { "
            "$lnk = $ws.CreateShortcut($ruta); "
            f"$lnk.TargetPath = '{piton}'; "
            "$lnk.Arguments = '\"" + script + "\"'; "
            f"$lnk.WorkingDirectory = '{carpeta}'; "
            + (f"$lnk.IconLocation = '{icono}'; "
               if os.path.exists(icono) else "")
            + "$lnk.Description = 'El Horno de Leo - Gestion del "
              "restaurante'; $lnk.Save() }")
        subprocess.run(["powershell", "-NoProfile", "-Command", guion],
                       timeout=30, creationflags=0x08000000,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass  # sin acceso directo el programa funciona igual


# ---------------------------------------------------------------- campana

def _generar_campana_wav():
    """Genera el sonido de campana una sola vez (WAV sintetizado propio,
    no depende de archivos del sistema)."""
    ruta = os.path.join(APP_DIR, "campana.wav")
    if os.path.exists(ruta):
        return ruta
    import wave
    import math
    import struct
    fs, dur = 44100, 0.9
    marcos = []
    for i in range(int(fs * dur)):
        t = i / fs
        # golpe de campana: fundamental y dos parciales con caída exponencial
        v = (math.sin(2 * math.pi * 880 * t)
             + 0.6 * math.sin(2 * math.pi * 1320 * t)
             + 0.4 * math.sin(2 * math.pi * 1760 * t))
        v *= math.exp(-4 * t)
        marcos.append(struct.pack("<h", int(v / 2.0 * 32767 * 0.85)))
    with wave.open(ruta, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(b"".join(marcos))
    return ruta


def sonar_campana():
    """Reproduce la campana sin bloquear. Devuelve False si no se pudo
    (el que llama puede usar el beep del sistema como último recurso)."""
    try:
        ruta = _generar_campana_wav()
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(ruta,
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True
        for reproductor in (["paplay", ruta], ["pw-play", ruta],
                            ["aplay", "-q", ruta]):
            try:
                subprocess.Popen(reproductor, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
            except FileNotFoundError:
                continue
    except Exception:
        pass
    return False


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
        # al abrir la mesa, el operador ya vio el aviso de "pide la cuenta"
        con.execute("UPDATE mesas SET pide_cuenta=0 WHERE numero=?", (numero,))
        con.commit()
        con.close()
        mozo, comensales = row if row else ("", 0)
        app.refrescar_mesas()

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

        # botones de categoría, como en la comandera de los mozos
        self.var_cat = tk.StringVar(value="Todas")
        fila_cat = ttk.Frame(izq)
        fila_cat.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._chips_cat = {}
        for c in ["Todas"] + CATEGORIAS:
            b = tk.Button(fila_cat, text=c, relief="flat", cursor="hand2",
                          font=(FONT, 10, "bold"), bd=0, padx=10, pady=4,
                          command=lambda c=c: self._elegir_categoria(c))
            b.pack(side="left", padx=(0, 5))
            self._chips_cat[c] = b
        self._pintar_chips()

        self.tree_prod = ttk.Treeview(izq, columns=("precio",), height=12)
        self.tree_prod.heading("#0", text="Producto")
        self.tree_prod.heading("precio", text="Precio")
        self.tree_prod.column("#0", width=230)
        self.tree_prod.column("precio", width=90, anchor="e")
        self.tree_prod.tag_configure("bajo", foreground=COL_BAJO)
        self.tree_prod.tag_configure("promo", foreground=COL_ACCENT2)
        self.tree_prod.grid(row=1, column=0, columnspan=2, sticky="nsew")
        # diferido: si el diálogo de gustos se abre en medio del doble
        # clic, el mouse queda "agarrado" y las casillas no responden
        self.tree_prod.bind("<Double-1>",
                            lambda e: self.after(120, self._agregar))

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
                                   font=(FONT, 14, "bold"), foreground=COL_ACCENT)
        self.lbl_total.pack(side="right")

        # --- acciones ----------------------------------------------------
        pie = ttk.Frame(self, style="Panel.TFrame", padding=10)
        pie.pack(fill="x")
        ttk.Button(pie, text="🖨  Comanda cocina",
                   command=self._imprimir_comanda).pack(side="left")
        ttk.Button(pie, text="🖨  Pre-cuenta",
                   command=self._imprimir_precuenta).pack(side="left", padx=8)
        ttk.Button(pie, text="✖  Cancelar mesa",
                   command=self._cancelar_mesa).pack(side="left")
        ttk.Button(pie, text="Cerrar ventana",
                   command=self._cerrar).pack(side="right")
        ttk.Button(pie, text="💵  COBRAR MESA", style="Accent.TButton",
                   command=self._cobrar).pack(side="right", padx=8)

        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self._snap_pedidos = None
        self._cargar_productos()
        self._comensales_cambiados()
        self._refrescar_pedido()
        self.after(3000, self._auto_refresco)

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

    def _elegir_categoria(self, categoria):
        self.var_cat.set(categoria)
        self._pintar_chips()
        self._cargar_productos()

    def _pintar_chips(self):
        for c, b in self._chips_cat.items():
            if c == self.var_cat.get():
                b.config(bg=COL_ACCENT, fg="white",
                         activebackground=COL_ACCENT2,
                         activeforeground="white")
            else:
                b.config(bg=COL_PANEL, fg=COL_ACCENT,
                         activebackground=COL_GRID,
                         activeforeground=COL_ACCENT)

    def _cargar_productos(self):
        self.tree_prod.delete(*self.tree_prod.get_children())
        con = db()
        sql = ("SELECT id, nombre, precio, categoria, usar_stock, stock, "
               "stock_min, promo_precio, promo_desde, promo_hasta "
               "FROM productos ")
        if self.var_cat.get() == "Todas":
            rows = con.execute(sql + "ORDER BY categoria, nombre").fetchall()
        else:
            rows = con.execute(sql + "WHERE categoria=? ORDER BY nombre",
                               (self.var_cat.get(),)).fetchall()
        con.close()
        for pid, nombre, precio, cat, usar, stock, smin, pp, pd, ph in rows:
            texto = nombre if self.var_cat.get() != "Todas" else f"[{cat}] {nombre}"
            tags = ()
            if promo_vigente(pp, pd, ph):
                texto += "  — PROMO"
                tags = ("promo",)
                precio = pp
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
            "SELECT nombre, precio, categoria, usar_stock, stock, "
            "promo_precio, promo_desde, promo_hasta FROM productos "
            "WHERE id=?", (pid,)).fetchone()
        con.close()
        if not row:
            return
        nombre, precio, categoria, usar_stock, stock, pp, pdesde, phasta = row
        precio = precio_vigente(precio, pp, pdesde, phasta)
        if usar_stock and (stock or 0) < cant:
            messagebox.showerror(
                "Sin stock",
                f"No hay stock suficiente de \"{nombre}\" "
                f"(quedan {int(stock or 0)}).", parent=self)
            return
        if lleva_gustos(nombre, categoria):
            gustos = elegir_gustos(self, nombre)
            if gustos is None:
                return  # canceló
            nombre, precio = aplicar_gustos(nombre, precio, gustos)
        comensal = self.cb_comensal.current()  # 0 = cuenta general
        con = db()
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
        rows = self._pedidos()
        for pid, nombre, precio, cant, comensal in rows:
            quien = "Cuenta general" if comensal == 0 else f"Comensal {comensal}"
            sub = precio * cant
            total += sub
            self.tree_pedido.insert("", "end", iid=str(pid),
                                    values=(quien, nombre, cant,
                                            fmt(precio), fmt(sub)))
        self.lbl_total.config(text=f"Total: {fmt(total)}")
        self._snap_pedidos = rows

    def _auto_refresco(self):
        """Refleja pedidos que entran desde la comandera de los mozos."""
        if not self.winfo_exists():
            return
        if self._pedidos() != self._snap_pedidos:
            self._cargar_productos()
            self._refrescar_pedido()
            self.app.refrescar_mesas()
        self.after(3000, self._auto_refresco)

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

    # ------------------------------------------------ cancelar mesa

    def _cancelar_mesa(self):
        """Libera la mesa sin cobrar: borra los pedidos, devuelve el stock
        y no registra ninguna venta (para mesas cargadas por error)."""
        pedidos = self._pedidos()
        if pedidos:
            total = sum(p * c for _, _, p, c, _ in pedidos)
            if not messagebox.askyesno(
                    "Cancelar mesa",
                    f"La mesa {self.numero} tiene {len(pedidos)} ítem(s) "
                    f"por {fmt(total)}.\n\nSe van a borrar SIN cobrar: no "
                    "queda registrada ninguna venta y el stock se devuelve."
                    "\n¿Cancelar la mesa y dejarla libre?",
                    icon="warning", default="no", parent=self):
                return
        con = db()
        for _, nombre, _, cant, _ in pedidos:
            con.execute("UPDATE productos SET stock=stock+? "
                        "WHERE nombre=? AND usar_stock=1", (cant, nombre))
        con.execute("DELETE FROM pedidos WHERE mesa=?", (self.numero,))
        con.execute("UPDATE mesas SET abierta=0, comensales=0, mozo='', "
                    "pide_cuenta=0 WHERE numero=?", (self.numero,))
        con.commit()
        con.close()
        self.app.refrescar_mesas()
        messagebox.showinfo("Cancelar mesa",
                            f"Mesa {self.numero} liberada (sin venta).",
                            parent=self.app)
        self.destroy()

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
        # la mesa queda libre y sin mozo hasta que alguien la vuelva a abrir
        cur.execute("UPDATE mesas SET abierta=0, comensales=0, mozo='', "
                    "pide_cuenta=0 WHERE numero=?", (self.numero,))
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


# ---------------------------------------------------------------- diálogo de gustos

def elegir_gustos(parent, nombre):
    """Diálogo para marcar los gustos de una pizzeta desde la PC.
    Devuelve la lista elegida (puede ser vacía) o None si se canceló."""
    dlg = tk.Toplevel(parent)
    dlg.title("Gustos de la pizzeta")
    dlg.configure(bg=COL_BG)
    dlg.transient(parent)
    ttk.Label(dlg, text=nombre, style="Titulo.TLabel")\
        .pack(padx=25, pady=(14, 2))
    incluidos = gustos_incluidos(nombre)
    pge = precio_gusto_extra()
    if incluidos:
        aviso = f"Incluye {incluidos} gusto; cada gusto de más suma {fmt(pge)}."
    else:
        aviso = f"Cada gusto suma {fmt(pge)}."
    ttk.Label(dlg, text=aviso, foreground=COL_MUTED).pack(padx=25)
    variables = {g: tk.BooleanVar(value=False) for g in GUSTOS_PIZZA}
    caja = ttk.Frame(dlg)
    caja.pack(padx=30, pady=10)
    for i, g in enumerate(GUSTOS_PIZZA):
        ttk.Checkbutton(caja, text=g, variable=variables[g])\
            .grid(row=i // 2, column=i % 2, sticky="w", padx=10, pady=4)
    resultado = {"ok": False}
    def aceptar():
        resultado["ok"] = True
        dlg.destroy()
    botones = ttk.Frame(dlg)
    botones.pack(pady=(4, 14))
    ttk.Button(botones, text="Cancelar",
               command=dlg.destroy).pack(side="left", padx=8)
    ttk.Button(botones, text="Agregar", style="Accent.TButton",
               command=aceptar).pack(side="left")
    # el grab recién cuando la ventana es visible: si se toma antes
    # (por ej. abierta con doble clic), las casillas quedan muertas
    try:
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.focus_set()
    except tk.TclError:
        pass
    dlg.wait_window()
    if not resultado["ok"]:
        return None
    return [g for g in GUSTOS_PIZZA if variables[g].get()]


# ---------------------------------------------------------------- marco con scroll

class MarcoScroll(ttk.Frame):
    """Marco con scroll vertical (barra + rueda del mouse) para que el
    contenido entre en pantallas chicas. Lo que se arma adentro va en
    `self.interior`. La barra aparece solo cuando hace falta."""

    def __init__(self, padre, estilo="Panel.TFrame"):
        super().__init__(padre, style=estilo)
        self._cv = tk.Canvas(self, bg=COL_BG, highlightthickness=0)
        self._sb = ttk.Scrollbar(self, orient="vertical",
                                 command=self._cv.yview)
        self._cv.configure(yscrollcommand=self._sb.set)
        self._cv.pack(side="left", fill="both", expand=True)
        self._sb.pack(side="right", fill="y")
        self.interior = ttk.Frame(self._cv, style=estilo)
        self._vent = self._cv.create_window((0, 0), window=self.interior,
                                            anchor="nw")
        self.interior.bind("<Configure>", self._ajustar)
        self._cv.bind("<Configure>", lambda e: self._cv.itemconfigure(
            self._vent, width=e.width))
        # la rueda del mouse funciona en cualquier parte del marco,
        # salvo sobre widgets que tienen scroll propio
        self.bind("<Enter>", self._activar_rueda)
        self.bind("<Leave>", self._desactivar_rueda)

    def _ajustar(self, _evento=None):
        self._cv.configure(scrollregion=self._cv.bbox("all") or (0, 0, 0, 0))

    def _sobra(self):
        return self.interior.winfo_height() > self._cv.winfo_height()

    def _rueda(self, evento):
        if evento.widget.winfo_class() in ("Treeview", "Listbox", "Text",
                                           "TCombobox"):
            return  # esos widgets scrollean solos
        if self._sobra():
            arriba = getattr(evento, "num", 0) == 4 or evento.delta > 0
            self._cv.yview_scroll(-1 if arriba else 1, "units")

    def _activar_rueda(self, _evento=None):
        self.bind_all("<MouseWheel>", self._rueda)   # Windows
        self.bind_all("<Button-4>", self._rueda)     # Linux
        self.bind_all("<Button-5>", self._rueda)

    def _desactivar_rueda(self, _evento=None):
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.unbind_all(ev)


# ---------------------------------------------------------------- agenda clientes

class AgendaClientesWindow(tk.Toplevel):
    """Agenda de clientes del delivery. Se arma sola con cada venta; acá
    se puede buscar, corregir una dirección, borrar un cliente o (si se
    abre desde una venta) elegirlo para esa venta."""

    def __init__(self, padre, elegir=None):
        super().__init__(padre)
        self.elegir = elegir  # callback(telefono, nombre, direccion) o None
        self.title("Agenda de clientes — Delivery")
        self.geometry("820x520")
        self.configure(bg=COL_BG)
        self.transient(padre)

        top = ttk.Frame(self, style="Panel.TFrame", padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="📒  Clientes del delivery",
                  style="Titulo.TLabel").pack(side="left")
        ttk.Label(top, text="Buscar:", style="Panel.TLabel")\
            .pack(side="left", padx=(30, 4))
        self.var_buscar = tk.StringVar()
        self.var_buscar.trace_add("write", lambda *a: self._cargar())
        ttk.Entry(top, textvariable=self.var_buscar, width=24).pack(side="left")

        cuerpo = ttk.Frame(self, style="Panel.TFrame", padding=10)
        cuerpo.pack(fill="both", expand=True)
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.rowconfigure(0, weight=1)

        cols = ("telefono", "nombre", "direccion", "pedidos", "ultimo")
        self.tree = ttk.Treeview(cuerpo, columns=cols, show="headings")
        for col, txt, w, anchor in [
                ("telefono", "Celular", 110, "w"),
                ("nombre", "Cliente", 160, "w"),
                ("direccion", "Dirección", 260, "w"),
                ("pedidos", "Pedidos", 70, "center"),
                ("ultimo", "Último", 90, "center")]:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor=anchor)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(cuerpo, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._seleccionado)
        if self.elegir:
            self.tree.bind("<Double-1>", lambda e: self._usar())

        form = ttk.Labelframe(cuerpo, text=" Ficha del cliente ", padding=10)
        form.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for i in (1, 3, 5):
            form.columnconfigure(i, weight=1)
        ttk.Label(form, text="Celular:").grid(row=0, column=0, sticky="w")
        self.var_tel = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_tel, width=14)\
            .grid(row=0, column=1, sticky="w", padx=(4, 12))
        ttk.Label(form, text="Nombre:").grid(row=0, column=2, sticky="w")
        self.var_nombre = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_nombre, width=18)\
            .grid(row=0, column=3, sticky="ew", padx=(4, 12))
        ttk.Label(form, text="Dirección:").grid(row=0, column=4, sticky="w")
        self.var_dir = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_dir)\
            .grid(row=0, column=5, sticky="ew", padx=(4, 0))

        pie = ttk.Frame(self, style="Panel.TFrame", padding=(10, 0, 10, 10))
        pie.pack(fill="x")
        ttk.Button(pie, text="💾  Guardar cliente",
                   command=self._guardar).pack(side="left")
        ttk.Button(pie, text="🗑  Eliminar",
                   command=self._eliminar).pack(side="left", padx=8)
        if self.elegir:
            ttk.Button(pie, text="✔  Usar en la venta", style="Accent.TButton",
                       command=self._usar).pack(side="right")
        ttk.Button(pie, text="Cerrar",
                   command=self.destroy).pack(side="right", padx=8)

        self._cargar()

    def _cargar(self):
        self.tree.delete(*self.tree.get_children())
        filtro = f"%{self.var_buscar.get().strip()}%"
        con = db()
        rows = con.execute(
            "SELECT telefono, nombre, direccion, pedidos, ultimo FROM clientes "
            "WHERE telefono LIKE ? OR nombre LIKE ? OR direccion LIKE ? "
            "ORDER BY nombre, telefono", (filtro, filtro, filtro)).fetchall()
        con.close()
        for tel, nombre, direccion, pedidos, ultimo in rows:
            fecha = f"{ultimo[8:10]}/{ultimo[5:7]}/{ultimo[2:4]}" \
                if len(ultimo) >= 10 else "-"
            self.tree.insert("", "end", iid=tel,
                             values=(tel, nombre or "-", direccion or "-",
                                     pedidos, fecha))

    def _seleccionado(self, _evento=None):
        sel = self.tree.selection()
        if not sel:
            return
        tel, nombre, direccion, _, _ = self.tree.item(sel[0], "values")
        self.var_tel.set(tel)
        self.var_nombre.set("" if nombre == "-" else nombre)
        self.var_dir.set("" if direccion == "-" else direccion)

    def _guardar(self):
        tel = tel_normalizado(self.var_tel.get())
        if len(tel) < 6:
            messagebox.showerror("Cliente", "El celular tiene que tener al "
                                 "menos 6 dígitos.", parent=self)
            return
        con = db()
        con.execute(
            "INSERT INTO clientes(telefono, nombre, direccion) VALUES (?,?,?) "
            "ON CONFLICT(telefono) DO UPDATE SET nombre=excluded.nombre, "
            "direccion=excluded.direccion",
            (tel, self.var_nombre.get().strip(), self.var_dir.get().strip()))
        con.commit()
        con.close()
        self._cargar()

    def _eliminar(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Eliminar", "Seleccioná un cliente de la "
                                "lista.", parent=self)
            return
        if not messagebox.askyesno("Eliminar",
                                   "¿Eliminar el cliente seleccionado de la "
                                   "agenda?", parent=self):
            return
        con = db()
        con.execute("DELETE FROM clientes WHERE telefono=?", (sel[0],))
        con.commit()
        con.close()
        self._cargar()

    def _usar(self):
        sel = self.tree.selection()
        if not sel or not self.elegir:
            return
        tel, nombre, direccion, _, _ = self.tree.item(sel[0], "values")
        self.elegir(tel, "" if nombre == "-" else nombre,
                    "" if direccion == "-" else direccion)
        self.destroy()


# ---------------------------------------------------------------- venta directa

class VentaDirectaWindow(tk.Toplevel):
    """Venta sin mesa: mostrador (retiro en el local) o delivery (envío).
    Los ítems viven en memoria y el stock se descuenta recién al cobrar,
    así cerrar la ventana sin cobrar no deja nada colgado."""

    def __init__(self, app, canal):
        super().__init__(app)
        self.app = app
        self.canal = canal  # "mostrador" | "delivery"
        etiqueta = CANAL_NOMBRE[canal]
        icono = "🛍" if canal == "mostrador" else "🛵"
        self.title(f"Venta {etiqueta}")
        self.geometry("1020x640")
        self.configure(bg=COL_BG)
        self.transient(app)
        self.items = []  # [pid, nombre, precio, cantidad]

        # --- encabezado: datos del cliente -------------------------------
        top = ttk.Frame(self, style="Panel.TFrame", padding=10)
        top.pack(fill="x")
        ttk.Label(top, text=f"{icono}  Venta {etiqueta}",
                  style="Titulo.TLabel").pack(side="left")
        self.var_cliente = tk.StringVar()
        self.var_tel = tk.StringVar()
        self.var_dir = tk.StringVar()
        # último dato autocompletado desde la agenda (para no pisar lo
        # que el operador escriba a mano)
        self._autocompletado = {"nombre": "", "direccion": ""}
        if canal == "delivery":
            ttk.Label(top, text="Celular:", style="Panel.TLabel")\
                .pack(side="left", padx=(24, 4))
            ttk.Entry(top, textvariable=self.var_tel, width=13).pack(side="left")
            self.var_tel.trace_add("write", lambda *a: self._tel_cambiado())
            ttk.Label(top, text="Cliente:", style="Panel.TLabel")\
                .pack(side="left", padx=(10, 4))
            ttk.Entry(top, textvariable=self.var_cliente, width=15)\
                .pack(side="left")
            ttk.Label(top, text="Dirección:", style="Panel.TLabel")\
                .pack(side="left", padx=(10, 4))
            ttk.Entry(top, textvariable=self.var_dir, width=24)\
                .pack(side="left", fill="x", expand=True)
            ttk.Button(top, text="📒", width=3,
                       command=self._abrir_agenda).pack(side="left", padx=(8, 0))
            self.lbl_cli_info = ttk.Label(top, text="", style="Panel.TLabel",
                                          foreground=COL_ACCENT2,
                                          font=(FONT, 10, "bold"))
            self.lbl_cli_info.pack(side="left", padx=(8, 0))
        else:
            ttk.Label(top, text="Cliente:", style="Panel.TLabel")\
                .pack(side="left", padx=(30, 4))
            ttk.Entry(top, textvariable=self.var_cliente, width=18)\
                .pack(side="left")

        # --- cuerpo: productos a la izquierda, pedido a la derecha -------
        cuerpo = ttk.Frame(self, style="Panel.TFrame", padding=10)
        cuerpo.pack(fill="both", expand=True)
        cuerpo.columnconfigure(0, weight=2)
        cuerpo.columnconfigure(1, weight=3)
        cuerpo.rowconfigure(0, weight=1)

        izq = ttk.Labelframe(cuerpo, text=" Agregar producto ", padding=8)
        izq.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        izq.columnconfigure(0, weight=1)
        izq.rowconfigure(1, weight=1)

        self.var_cat = tk.StringVar(value="Todas")
        fila_cat = ttk.Frame(izq)
        fila_cat.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._chips_cat = {}
        for c in ["Todas"] + CATEGORIAS:
            b = tk.Button(fila_cat, text=c, relief="flat", cursor="hand2",
                          font=(FONT, 10, "bold"), bd=0, padx=10, pady=4,
                          command=lambda c=c: self._elegir_categoria(c))
            b.pack(side="left", padx=(0, 5))
            self._chips_cat[c] = b
        self._pintar_chips()

        self.tree_prod = ttk.Treeview(izq, columns=("precio",), height=12)
        self.tree_prod.heading("#0", text="Producto")
        self.tree_prod.heading("precio", text="Precio")
        self.tree_prod.column("#0", width=230)
        self.tree_prod.column("precio", width=90, anchor="e")
        self.tree_prod.tag_configure("bajo", foreground=COL_BAJO)
        self.tree_prod.tag_configure("promo", foreground=COL_ACCENT2)
        self.tree_prod.grid(row=1, column=0, columnspan=2, sticky="nsew")
        # diferido: si el diálogo de gustos se abre en medio del doble
        # clic, el mouse queda "agarrado" y las casillas no responden
        self.tree_prod.bind("<Double-1>",
                            lambda e: self.after(120, self._agregar))

        fila = ttk.Frame(izq)
        fila.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(fila, text="Cant.:").pack(side="left")
        self.var_cant = tk.IntVar(value=1)
        ttk.Spinbox(fila, from_=1, to=99, width=4,
                    textvariable=self.var_cant).pack(side="left", padx=(2, 12))
        ttk.Button(izq, text="Agregar al pedido  ➜", style="Accent.TButton",
                   command=self._agregar).grid(row=3, column=0, columnspan=2,
                                               sticky="ew", pady=(8, 0))

        der = ttk.Labelframe(cuerpo, text=f" Pedido {etiqueta.lower()} ",
                             padding=8)
        der.grid(row=0, column=1, sticky="nsew")
        der.columnconfigure(0, weight=1)
        der.rowconfigure(0, weight=1)

        cols = ("producto", "cant", "precio", "subtotal")
        self.tree_pedido = ttk.Treeview(der, columns=cols, show="headings",
                                        height=12)
        for col, txt, w, anchor in [
                ("producto", "Producto", 240, "w"),
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
                                   font=(FONT, 14, "bold"),
                                   foreground=COL_ACCENT)
        self.lbl_total.pack(side="right")

        # --- acciones -----------------------------------------------------
        pie = ttk.Frame(self, style="Panel.TFrame", padding=10)
        pie.pack(fill="x")
        ttk.Button(pie, text="🖨  Comanda cocina",
                   command=self._imprimir_comanda).pack(side="left")
        ttk.Button(pie, text="Cerrar sin cobrar",
                   command=self.destroy).pack(side="right")
        ttk.Button(pie, text=f"💵  COBRAR {etiqueta.upper()}",
                   style="Accent.TButton",
                   command=self._cobrar).pack(side="right", padx=8)

        self._cargar_productos()

    # ------------------------------------------------ agenda de clientes

    def _tel_cambiado(self):
        """Al escribir el celular, si es un cliente conocido se completan
        solos el nombre y la dirección (sin pisar lo escrito a mano)."""
        fila = cliente_buscar(self.var_tel.get())
        if not fila:
            self.lbl_cli_info.config(text="")
            return
        nombre, direccion, pedidos, _ = fila
        if nombre and self.var_cliente.get().strip() in \
                ("", self._autocompletado["nombre"]):
            self.var_cliente.set(nombre)
            self._autocompletado["nombre"] = nombre
        if direccion and self.var_dir.get().strip() in \
                ("", self._autocompletado["direccion"]):
            self.var_dir.set(direccion)
            self._autocompletado["direccion"] = direccion
        self.lbl_cli_info.config(
            text=f"📒 {pedidos} pedido(s) anteriores")

    def _abrir_agenda(self):
        AgendaClientesWindow(self, elegir=self._usar_cliente)

    def _usar_cliente(self, telefono, nombre, direccion):
        self._autocompletado = {"nombre": nombre, "direccion": direccion}
        self.var_cliente.set(nombre)
        self.var_dir.set(direccion)
        self.var_tel.set(telefono)

    # ------------------------------------------------ catálogo

    def _elegir_categoria(self, categoria):
        self.var_cat.set(categoria)
        self._pintar_chips()
        self._cargar_productos()

    def _pintar_chips(self):
        for c, b in self._chips_cat.items():
            if c == self.var_cat.get():
                b.config(bg=COL_ACCENT, fg="white",
                         activebackground=COL_ACCENT2,
                         activeforeground="white")
            else:
                b.config(bg=COL_PANEL, fg=COL_ACCENT,
                         activebackground=COL_GRID,
                         activeforeground=COL_ACCENT)

    def _cargar_productos(self):
        self.tree_prod.delete(*self.tree_prod.get_children())
        con = db()
        sql = ("SELECT id, nombre, precio, categoria, usar_stock, stock, "
               "stock_min, promo_precio, promo_desde, promo_hasta "
               "FROM productos ")
        if self.var_cat.get() == "Todas":
            rows = con.execute(sql + "ORDER BY categoria, nombre").fetchall()
        else:
            rows = con.execute(sql + "WHERE categoria=? ORDER BY nombre",
                               (self.var_cat.get(),)).fetchall()
        con.close()
        for pid, nombre, precio, cat, usar, stock, smin, pp, pd, ph in rows:
            texto = nombre if self.var_cat.get() != "Todas" else f"[{cat}] {nombre}"
            tags = ()
            if promo_vigente(pp, pd, ph):
                texto += "  — PROMO"
                tags = ("promo",)
                precio = pp
            if usar:
                disponible = (stock or 0) - self._en_pedido(pid)
                if disponible <= 0:
                    texto += "  — SIN STOCK"
                    tags = ("bajo",)
                elif disponible <= (smin or 0):
                    texto += f"  — quedan {int(disponible)}"
                    tags = ("bajo",)
            self.tree_prod.insert("", "end", iid=str(pid), text=texto,
                                  values=(fmt(precio),), tags=tags)

    def _en_pedido(self, pid):
        """Unidades de ese producto ya cargadas en esta venta (el stock
        se descuenta al cobrar, así que hay que restarlas a mano)."""
        return sum(c for p, _, _, c in self.items if p == pid)

    # ------------------------------------------------ pedido

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
            "SELECT nombre, precio, categoria, usar_stock, stock, "
            "promo_precio, promo_desde, promo_hasta FROM productos "
            "WHERE id=?", (pid,)).fetchone()
        con.close()
        if not row:
            return
        nombre, precio, categoria, usar_stock, stock, pp, pdesde, phasta = row
        precio = precio_vigente(precio, pp, pdesde, phasta)
        if usar_stock and (stock or 0) - self._en_pedido(pid) < cant:
            disponible = int((stock or 0) - self._en_pedido(pid))
            messagebox.showerror(
                "Sin stock",
                f"No hay stock suficiente de \"{nombre}\" "
                f"(quedan {max(disponible, 0)}).", parent=self)
            return
        if lleva_gustos(nombre, categoria):
            gustos = elegir_gustos(self, nombre)
            if gustos is None:
                return  # canceló
            nombre, precio = aplicar_gustos(nombre, precio, gustos)
        for it in self.items:
            if it[0] == pid and it[1] == nombre and it[2] == precio:
                it[3] += cant
                break
        else:
            self.items.append([pid, nombre, precio, cant])
        self.var_cant.set(1)
        self._cargar_productos()
        self._refrescar_pedido()

    def _quitar(self):
        sel = self.tree_pedido.selection()
        if not sel:
            return
        for iid in sorted((int(i) for i in sel), reverse=True):
            del self.items[iid]
        self._cargar_productos()
        self._refrescar_pedido()

    def _refrescar_pedido(self):
        self.tree_pedido.delete(*self.tree_pedido.get_children())
        for i, (pid, nombre, precio, cant) in enumerate(self.items):
            self.tree_pedido.insert("", "end", iid=str(i),
                                    values=(nombre, cant, fmt(precio),
                                            fmt(precio * cant)))
        self.lbl_total.config(text=f"Total: {fmt(self._total())}")

    def _total(self):
        return sum(p * c for _, _, p, c in self.items)

    def _nota_delivery(self):
        """Renglones con los datos de entrega para el ticket y la comanda."""
        renglones = []
        if self.var_dir.get().strip():
            renglones.append("Enviar a: " + self.var_dir.get().strip())
        if self.var_tel.get().strip():
            renglones.append("Tel: " + self.var_tel.get().strip())
        return renglones

    # ------------------------------------------------ impresión

    def _imprimir_comanda(self):
        if not self.items:
            messagebox.showinfo("Comanda", "La venta no tiene productos.",
                                parent=self)
            return
        ahora = datetime.datetime.now()
        titulo = CANAL_NOMBRE[self.canal].upper()
        lineas = [centrar("*** COMANDA COCINA ***"),
                  f"{titulo}  -  {ahora:%H:%M}",
                  f"Cliente: {self.var_cliente.get().strip() or '-'}"]
        lineas += self._nota_delivery()
        lineas.append("-" * ANCHO_TICKET)
        for _, nombre, _, cant in self.items:
            lineas.append(f"{cant:>2} x {nombre}")
        lineas.append("")
        ruta, error = imprimir_texto("\n".join(lineas), "comanda")
        if error:
            messagebox.showwarning(
                "Impresión", f"No se pudo imprimir: {error}\n\n"
                f"Copia guardada en:\n{ruta}", parent=self)

    # ------------------------------------------------ cobro

    def _cobrar(self):
        if not self.items:
            messagebox.showinfo("Cobrar", "La venta no tiene productos.",
                                parent=self)
            return
        dlg = tk.Toplevel(self)
        dlg.title(f"Cobrar {CANAL_NOMBRE[self.canal].lower()}")
        dlg.configure(bg=COL_BG)
        dlg.transient(self)
        dlg.grab_set()

        ttk.Label(dlg, text=f"Total: {fmt(self._total())}",
                  style="Titulo.TLabel").pack(padx=25, pady=(15, 10))
        fila_medio = ttk.Frame(dlg)
        fila_medio.pack(anchor="w", padx=25)
        ttk.Label(fila_medio, text="Medio de pago:").pack(side="left")
        var_medio = tk.StringVar(value=MEDIOS_PAGO[0])
        ttk.Combobox(fila_medio, textvariable=var_medio, state="readonly",
                     values=MEDIOS_PAGO, width=16).pack(side="left", padx=8)
        var_imprimir = tk.BooleanVar(value=True)
        ttk.Checkbutton(dlg, text="Imprimir ticket",
                        variable=var_imprimir).pack(anchor="w", padx=25,
                                                    pady=(10, 5))
        botones = ttk.Frame(dlg)
        botones.pack(pady=15)
        ttk.Button(botones, text="Cancelar",
                   command=dlg.destroy).pack(side="left", padx=8)
        ttk.Button(botones, text="Confirmar cobro", style="Accent.TButton",
                   command=lambda: self._confirmar_cobro(
                       dlg, var_imprimir.get(), var_medio.get()))\
            .pack(side="left")

    def _confirmar_cobro(self, dlg, imprimir, medio):
        cliente = self.var_cliente.get().strip()
        if self.canal == "delivery" and not (cliente or
                                             self.var_dir.get().strip()):
            messagebox.showerror(
                "Delivery", "Cargá al menos el nombre del cliente o la "
                "dirección de entrega.", parent=dlg)
            return
        total = self._total()

        con = db()
        try:
            con.execute("BEGIN IMMEDIATE")
            # revalidar stock recién ahora (se descuenta al cobrar)
            for pid, nombre, _, cant in self.items:
                row = con.execute(
                    "SELECT usar_stock, stock FROM productos WHERE id=?",
                    (pid,)).fetchone()
                if row and row[0] and (row[1] or 0) < self._en_pedido(pid):
                    con.rollback()
                    messagebox.showerror(
                        "Sin stock",
                        f"Se quedó sin stock \"{nombre}\" (quedan "
                        f"{int(row[1] or 0)}). Ajustá la venta.", parent=dlg)
                    return
            for pid, _, _, cant in self.items:
                con.execute("UPDATE productos SET stock=stock-? "
                            "WHERE id=? AND usar_stock=1", (cant, pid))
            datos_cliente = " · ".join(
                [d for d in [cliente, self.var_tel.get().strip(),
                             self.var_dir.get().strip()] if d])
            cur = con.cursor()
            cur.execute(
                "INSERT INTO ventas(fecha, mesa, mozo, total, modo, medio, "
                "canal, cliente) VALUES (?,?,?,?,?,?,?,?)",
                (datetime.datetime.now().isoformat(timespec="seconds"),
                 None, "", total, "una", medio, self.canal, datos_cliente))
            venta_id = cur.lastrowid
            cur.executemany(
                "INSERT INTO venta_items(venta_id, nombre, cantidad, subtotal)"
                " VALUES (?,?,?,?)",
                [(venta_id, nombre, cant, precio * cant)
                 for _, nombre, precio, cant in self.items])
            con.commit()
        finally:
            con.close()

        if self.canal == "delivery":
            # agenda automática: el próximo pedido de este celular
            # completa solo el nombre y la dirección
            cliente_guardar(self.var_tel.get(), cliente,
                            self.var_dir.get().strip())

        etiqueta = CANAL_NOMBRE[self.canal].upper()
        extra = []
        if cliente:
            extra.append(f"Cliente: {cliente}")
        if self.canal == "delivery":
            if self.var_tel.get().strip():
                extra.append(f"Celular: {self.var_tel.get().strip()}")
            if self.var_dir.get().strip():
                extra.append(f"Entregar en: {self.var_dir.get().strip()}")
        texto = armar_recibo(f"*** {etiqueta} ***", None,
                             [(c, n, p * c) for _, n, p, c in self.items],
                             total, medio=medio, extra=extra)
        problema = None
        if imprimir:
            _, problema = imprimir_texto(texto, f"recibo_{self.canal}")
        else:
            nombre_arch = (f"recibo_{self.canal}_"
                           f"{datetime.datetime.now():%Y%m%d_%H%M%S_%f}.txt")
            with open(os.path.join(RECIBOS_DIR, nombre_arch), "w",
                      encoding="utf-8") as f:
                f.write(texto)

        dlg.destroy()
        msg = f"Venta {CANAL_NOMBRE[self.canal].lower()} cobrada: " \
              f"{fmt(total)} ({medio})."
        if problema:
            if messagebox.askyesno(
                    "Cobro registrado",
                    msg + f"\n\nNo se pudo imprimir ({problema}).\n"
                    "El ticket quedó guardado como archivo.\n"
                    "¿Abrir la carpeta de recibos?", parent=self.app):
                abrir_carpeta(RECIBOS_DIR)
        else:
            messagebox.showinfo("Cobro registrado", msg, parent=self.app)
        self.app.refrescar_directas()
        self.destroy()


# ---------------------------------------------------------------- aplicación

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gestión — " + cfg_get("nombre", "El Horno de Leo")
                   + f"  ·  v{VERSION}")
        self.geometry("1180x720")
        self.minsize(980, 620)
        self.configure(bg=COL_BG)
        try:
            # logo del local en la barra de título y la barra de tareas
            # (True = también en todas las ventanas de mesa y de venta)
            self._icono = tk.PhotoImage(file=ruta_recurso("icono.png"))
            self.iconphoto(True, self._icono)
        except Exception:
            pass  # sin el archivo del logo el programa funciona igual
        self._estilos()
        self._ventanas_mesa = {}
        self._snap_mesas = None

        # comandera web para los mozos (celulares en la misma red WiFi)
        self.comandera_srv = None
        self.comandera_url = ""
        if cfg_get("mozos_activo", "1") == "1":
            self._comandera_arrancar(silencioso=True)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_mesas = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        self.tab_dir = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        self.tab_prod = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        self.tab_rep = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        self.tab_stats = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        self.tab_cfg = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        nb.add(self.tab_mesas, text="  🍽  Mesas  ")
        nb.add(self.tab_dir, text="  🛵  Mostrador/Delivery  ")
        nb.add(self.tab_prod, text="  📋  Productos  ")
        nb.add(self.tab_rep, text="  🧾  Ventas  ")
        nb.add(self.tab_stats, text="  📊  Estadísticas  ")
        nb.add(self.tab_cfg, text="  ⚙  Configuración  ")
        nb.bind("<<NotebookTabChanged>>", self._al_cambiar_tab)
        self.nb = nb

        self._armar_tab_mesas()
        self._armar_tab_directas()
        self._armar_tab_productos()
        self._armar_tab_reportes()
        self._armar_tab_stats()
        self._armar_tab_config()

        self.after(600, self._avisar_faltantes)
        self.after(4000, self._auto_refresco)
        self.after(1000, self._escuchar_avisos)
        if cfg_get("update_auto", "1") == "1" \
                and not getattr(sys, "frozen", False):
            self.after(3000, self._buscar_actualizacion_fondo)
        # primera vez en Windows: ofrecer dejar fija la IP de la PC
        if sys.platform.startswith("win") \
                and cfg_get("ip_fija_ofrecida", "0") != "1":
            self.after(1500, self._ofrecer_ip_fija)
        # el acceso directo del escritorio se crea/repara solo (Windows)
        threading.Thread(target=asegurar_acceso_directo,
                         daemon=True).start()

    # ------------------------------------------------ comandera de mozos

    def _comandera_arrancar(self, silencioso=False):
        try:
            puerto = int(cfg_get("mozos_puerto", str(comandera.PUERTO_DEFECTO)))
        except ValueError:
            puerto = comandera.PUERTO_DEFECTO
        if self.comandera_srv:
            comandera.detener(self.comandera_srv)
            self.comandera_srv = None
        try:
            self.comandera_srv, self.comandera_url = comandera.iniciar(
                deps_comandera(), puerto)
        except OSError as e:
            self.comandera_url = ""
            if not silencioso:
                messagebox.showerror(
                    "Comandera",
                    f"No se pudo iniciar la comandera en el puerto {puerto}:\n"
                    f"{e}\n\nProbá con otro puerto.", parent=self)
        self._actualizar_url_comandera()

    def _comandera_apagar(self):
        if self.comandera_srv:
            comandera.detener(self.comandera_srv)
            self.comandera_srv = None
        self._actualizar_url_comandera()

    def _actualizar_url_comandera(self):
        if not hasattr(self, "var_mz_url"):
            return  # la pestaña de configuración todavía no se armó
        self.var_mz_url.set(self.comandera_url if self.comandera_srv
                            else "(comandera apagada)")

    def _auto_refresco(self):
        """Refleja en la grilla los pedidos que entran desde los celulares."""
        try:
            if self.nb.index(self.nb.select()) == 0 \
                    and self._datos_mesas() != self._snap_mesas:
                self.refrescar_mesas()
        except tk.TclError:
            return
        self.after(4000, self._auto_refresco)

    def _escuchar_avisos(self):
        """Campana al llegar algo desde una comandera:
        1 campanada = pedido nuevo, 3 campanadas = piden la cuenta."""
        try:
            for tipo, mesa in comandera.eventos_pendientes():
                hora = datetime.datetime.now().strftime("%H:%M")
                if tipo == "cuenta":
                    self._campana(3)
                    self.lbl_aviso.config(
                        text=f"🧾 La mesa {mesa} pide la cuenta  ({hora})")
                else:
                    self._campana(1)
                    self.lbl_aviso.config(
                        text=f"🛎 Pedido nuevo en la mesa {mesa}  ({hora})")
        except tk.TclError:
            return
        self.after(1000, self._escuchar_avisos)

    def _campana(self, veces):
        if not sonar_campana():
            self.bell()  # último recurso si no hay audio
        if veces > 1:
            self.after(400, lambda: self._campana(veces - 1))

    # ------------------------------------------------ actualizaciones

    def _buscar_actualizacion_fondo(self):
        """Consulta en un hilo si hay versión nueva; si hay, la ofrece."""
        def trabajo():
            try:
                info = consultar_actualizacion()
            except Exception:
                return  # sin internet o sitio caído: se prueba otro día
            if info:
                try:
                    self.after(0, lambda: self._ofrecer_actualizacion(info))
                except tk.TclError:
                    pass  # la app se cerró mientras tanto
        threading.Thread(target=trabajo, daemon=True).start()

    def _buscar_actualizacion_manual(self):
        self._guardar_actualizaciones(avisar=False)
        if getattr(sys, "frozen", False):
            messagebox.showinfo(
                "Actualización", "Esta copia es un .exe congelado y no se "
                "actualiza sola; usá la versión instalada con el zip.",
                parent=self)
            return
        if not url_actualizaciones():
            messagebox.showinfo(
                "Actualización", "No hay una dirección de actualizaciones "
                "configurada.", parent=self)
            return
        try:
            info = consultar_actualizacion()
        except Exception as e:
            messagebox.showerror(
                "Actualización", "No se pudo consultar si hay una versión "
                f"nueva:\n{e}\n\n¿Esta PC tiene internet?", parent=self)
            return
        if info:
            self._ofrecer_actualizacion(info)
        else:
            messagebox.showinfo(
                "Actualización",
                f"El programa ya está en la última versión ({VERSION}).",
                parent=self)

    def _ofrecer_actualizacion(self, info):
        novedades = str(info.get("novedades", "")).strip()
        mensaje = (f"Hay una versión nueva del programa: {info['version']} "
                   f"(esta PC tiene la {VERSION}).\n\n")
        if novedades:
            mensaje += f"Novedades:\n{novedades}\n\n"
        mensaje += ("¿Instalarla ahora? El programa se reinicia solo y "
                    "las ventas, productos y configuración no se tocan.")
        if not messagebox.askyesno("Actualización disponible", mensaje,
                                   parent=self):
            return
        try:
            descargar_actualizacion(info)
        except Exception as e:
            messagebox.showerror(
                "Actualización", f"No se pudo actualizar:\n{e}\n\n"
                "El programa sigue funcionando con la versión actual.",
                parent=self)
            return
        messagebox.showinfo(
            "Actualización",
            f"Listo: se instaló la versión {info['version']}.\n"
            "El programa se va a reiniciar.", parent=self)
        self._reiniciar()

    def _reiniciar(self):
        """Vuelve a lanzar el programa (tras una actualización)."""
        if self.comandera_srv:
            comandera.detener(self.comandera_srv)  # soltar el puerto
            self.comandera_srv = None
        script = os.path.abspath(__file__)
        subprocess.Popen([sys.executable, script],
                         cwd=os.path.dirname(script))
        self.destroy()

    # ------------------------------------------------ estilos

    def _estilos(self):
        # letra más grande en todo el programa (también en los campos de
        # texto y menús, que usan las fuentes con nombre de Tk)
        for nombre in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                       "TkHeadingFont"):
            try:
                tkfont.nametofont(nombre).configure(family=FONT, size=11)
            except tk.TclError:
                pass
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=COL_BG, foreground=COL_TEXT,
                     font=(FONT, 11))
        st.configure("TNotebook", background=COL_BG, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(14, 8), font=(FONT, 11, "bold"))
        st.map("TNotebook.Tab",
               background=[("selected", COL_ACCENT)],
               foreground=[("selected", "white")])
        st.configure("Panel.TFrame", background=COL_BG)
        st.configure("Panel.TLabel", background=COL_BG)
        st.configure("Titulo.TLabel", background=COL_BG,
                     font=(FONT, 16, "bold"), foreground=COL_ACCENT)
        st.configure("TLabelframe", background=COL_BG)
        st.configure("TLabelframe.Label", background=COL_BG,
                     foreground=COL_ACCENT, font=(FONT, 11, "bold"))
        st.configure("Treeview", rowheight=30, fieldbackground=COL_PANEL,
                     background=COL_PANEL, font=(FONT, 11))
        st.configure("Treeview.Heading", font=(FONT, 11, "bold"),
                     background=COL_ACCENT, foreground="white")
        st.map("Treeview.Heading", background=[("active", COL_ACCENT2)])
        st.configure("Accent.TButton", background=COL_ACCENT,
                     foreground="white", font=(FONT, 11, "bold"), padding=8)
        st.map("Accent.TButton",
               background=[("active", COL_ACCENT2), ("disabled", "#b9a7a9")])
        st.configure("TButton", padding=6)

    def _al_cambiar_tab(self, _evento=None):
        idx = self.nb.index(self.nb.select())
        if idx == 0:
            self.refrescar_mesas()
        elif idx == 1:
            self.refrescar_directas()
        elif idx == 2:
            self._cargar_productos()
        elif idx == 3:
            self._cargar_ventas()
        elif idx == 4:
            self._redibujar_graficos()
        elif idx == 5:
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
        sc = MarcoScroll(self.tab_mesas)
        sc.pack(fill="both", expand=True)
        self.frame_grilla = sc.interior
        leyenda = ttk.Frame(self.tab_mesas, style="Panel.TFrame")
        leyenda.pack(fill="x", pady=(10, 0))
        for color, texto in [(COL_LIBRE, "Libre"), (COL_OCUPADA, "Ocupada"),
                             (COL_ACCENT2, "Pide la cuenta")]:
            tk.Label(leyenda, text="  ", bg=color).pack(side="left", padx=(10, 3))
            ttk.Label(leyenda, text=texto, style="Panel.TLabel").pack(side="left")
        self.lbl_aviso = ttk.Label(leyenda, text="", style="Panel.TLabel",
                                   foreground=COL_ACCENT2,
                                   font=(FONT, 10, "bold"))
        self.lbl_aviso.pack(side="right", padx=(0, 10))
        self.refrescar_mesas()

    def _datos_mesas(self):
        con = db()
        mesas = con.execute(
            "SELECT numero, mozo, abierta, pide_cuenta FROM mesas "
            "ORDER BY numero").fetchall()
        totales = dict(con.execute(
            "SELECT mesa, SUM(precio*cantidad) FROM pedidos GROUP BY mesa").fetchall())
        con.close()
        return mesas, totales

    def refrescar_mesas(self):
        for w in self.frame_grilla.winfo_children():
            w.destroy()
        mesas, totales = self._datos_mesas()
        self._snap_mesas = (mesas, totales)
        columnas = 5
        for i in range(columnas):
            self.frame_grilla.columnconfigure(i, weight=1)
        for idx, (numero, mozo, abierta, pide_cuenta) in enumerate(mesas):
            total = totales.get(numero, 0) or 0
            estado = fmt(total) if abierta else "Libre"
            if abierta and pide_cuenta:
                estado += "  ·  🧾 CUENTA"
            texto = f"Mesa {numero}\n{mozo or '(sin mozo)'}\n{estado}"
            if abierta:
                color = COL_ACCENT2 if pide_cuenta else COL_OCUPADA
            else:
                color = COL_LIBRE
            btn = tk.Button(self.frame_grilla, text=texto, bg=color, fg="white",
                            font=(FONT, 12, "bold"), relief="flat", cursor="hand2",
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

    # ================================================= TAB MOSTRADOR/DELIVERY

    def _armar_tab_directas(self):
        f = self.tab_dir
        f.columnconfigure(0, weight=1)
        f.rowconfigure(3, weight=1)

        ttk.Label(f, text="Mostrador y delivery — ventas sin mesa",
                  style="Titulo.TLabel").grid(row=0, column=0, sticky="w",
                                              pady=(0, 10))
        botones = ttk.Frame(f, style="Panel.TFrame")
        botones.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        for canal, texto, color in [
                ("mostrador", "🛍  NUEVA VENTA MOSTRADOR", COL_ACCENT),
                ("delivery", "🛵  NUEVA VENTA DELIVERY", COL_ACCENT2)]:
            tk.Button(botones, text=texto, bg=color, fg="white",
                      font=(FONT, 12, "bold"), relief="flat", cursor="hand2",
                      activebackground=COL_OCUPADA, activeforeground="white",
                      padx=24, pady=14,
                      command=lambda c=canal: VentaDirectaWindow(self, c))\
                .pack(side="left", padx=(0, 12))
        ttk.Button(botones, text="📒  Agenda de clientes",
                   command=lambda: AgendaClientesWindow(self))\
            .pack(side="left", padx=(8, 0))

        ttk.Label(f, text="Ventas de hoy por mostrador y delivery:",
                  style="Panel.TLabel", font=(FONT, 10, "bold"))\
            .grid(row=2, column=0, sticky="w", pady=(0, 4))
        cols = ("hora", "canal", "cliente", "medio", "total")
        self.tree_directas = ttk.Treeview(f, columns=cols, show="headings")
        for col, txt, w, anchor in [("hora", "Hora", 70, "center"),
                                    ("canal", "Canal", 100, "w"),
                                    ("cliente", "Cliente / entrega", 340, "w"),
                                    ("medio", "Medio de pago", 130, "w"),
                                    ("total", "Total", 110, "e")]:
            self.tree_directas.heading(col, text=txt)
            self.tree_directas.column(col, width=w, anchor=anchor)
        self.tree_directas.grid(row=3, column=0, sticky="nsew")

        self.lbl_dir_resumen = ttk.Label(f, text="", style="Panel.TLabel",
                                         font=(FONT, 11, "bold"))
        self.lbl_dir_resumen.grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.refrescar_directas()

    def refrescar_directas(self):
        if not hasattr(self, "tree_directas"):
            return  # la pestaña todavía no se armó
        self.tree_directas.delete(*self.tree_directas.get_children())
        hoy = datetime.date.today().isoformat()
        con = db()
        rows = con.execute(
            "SELECT fecha, canal, cliente, medio, total FROM ventas "
            "WHERE fecha LIKE ? AND canal IN ('mostrador','delivery') "
            "ORDER BY fecha DESC", (hoy + "%",)).fetchall()
        con.close()
        resumen = {"mostrador": [0, 0.0], "delivery": [0, 0.0]}
        for fecha, canal, cliente, medio, total in rows:
            hora = fecha[11:16] if len(fecha) >= 16 else fecha
            self.tree_directas.insert("", "end", values=(
                hora, CANAL_NOMBRE.get(canal, canal), cliente or "-",
                medio or "-", fmt(total)))
            if canal in resumen:
                resumen[canal][0] += 1
                resumen[canal][1] += total
        self.lbl_dir_resumen.config(text=(
            f"Hoy —  Mostrador: {resumen['mostrador'][0]} venta(s), "
            f"{fmt(resumen['mostrador'][1])}   |   "
            f"Delivery: {resumen['delivery'][0]} venta(s), "
            f"{fmt(resumen['delivery'][1])}"))

    # ================================================= TAB PRODUCTOS

    def _armar_tab_productos(self):
        f = self.tab_prod
        f.columnconfigure(0, weight=3)
        f.columnconfigure(1, weight=2)
        f.rowconfigure(1, weight=1)

        ttk.Label(f, text="Productos, precios y stock", style="Titulo.TLabel")\
            .grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        cols = ("categoria", "nombre", "precio", "promo", "stock")
        self.tree_productos = ttk.Treeview(f, columns=cols, show="headings")
        for col, txt, w, anchor in [("categoria", "Categoría", 90, "w"),
                                    ("nombre", "Producto", 220, "w"),
                                    ("precio", "Precio", 90, "e"),
                                    ("promo", "Promoción", 150, "w"),
                                    ("stock", "Stock", 100, "center")]:
            self.tree_productos.heading(col, text=txt)
            self.tree_productos.column(col, width=w, anchor=anchor)
        self.tree_productos.tag_configure("bajo", foreground=COL_BAJO)
        self.tree_productos.tag_configure("promo", foreground=COL_ACCENT2)
        self.tree_productos.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.tree_productos.bind("<<TreeviewSelect>>", self._producto_seleccionado)

        sc_form = MarcoScroll(f)
        sc_form.grid(row=1, column=1, sticky="nsew")
        form = ttk.Labelframe(sc_form.interior, text=" Ficha del producto ",
                              padding=12)
        form.pack(fill="both", expand=True)
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

        ttk.Separator(form, orient="horizontal")\
            .grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        ttk.Label(form, text="Promoción (opcional)",
                  foreground=COL_ACCENT2, font=(FONT, 10, "bold"))\
            .grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Label(form, text="Precio promo:").grid(row=8, column=0,
                                                   sticky="w", pady=4)
        self.var_p_promo = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_p_promo, width=10)\
            .grid(row=8, column=1, sticky="w", pady=4)
        ttk.Label(form, text="Desde (AAAA-MM-DD):").grid(row=9, column=0,
                                                         sticky="w", pady=4)
        self.var_p_pdesde = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_p_pdesde, width=12)\
            .grid(row=9, column=1, sticky="w", pady=4)
        ttk.Label(form, text="Hasta (AAAA-MM-DD):").grid(row=10, column=0,
                                                         sticky="w", pady=4)
        self.var_p_phasta = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_p_phasta, width=12)\
            .grid(row=10, column=1, sticky="w", pady=4)
        ttk.Label(form, foreground=COL_MUTED, wraplength=300, justify="left",
                  text="Mientras la promo está vigente se cobra ese precio "
                       "en las mesas y en la comandera. Fechas vacías = sin "
                       "límite. Para sacarla, borrá el precio promo y guardá.")\
            .grid(row=11, column=0, columnspan=2, sticky="w", pady=(2, 0))

        ttk.Button(form, text="➕  Agregar nuevo", style="Accent.TButton",
                   command=self._producto_agregar)\
            .grid(row=12, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        ttk.Button(form, text="💾  Guardar cambios del seleccionado",
                   command=self._producto_editar)\
            .grid(row=13, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(form, text="🗑  Eliminar seleccionado",
                   command=self._producto_eliminar)\
            .grid(row=14, column=0, columnspan=2, sticky="ew", pady=4)

        self.lbl_faltantes = ttk.Label(f, text="", style="Panel.TLabel",
                                       foreground=COL_BAJO)
        self.lbl_faltantes.grid(row=2, column=0, columnspan=2,
                                sticky="w", pady=(8, 0))

        fila_gusto = ttk.Frame(f, style="Panel.TFrame")
        fila_gusto.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(fila_gusto, text="Pizzería — precio de cada gusto "
                  "(aceitunas, panceta, etc.): $").pack(side="left")
        self.var_precio_gusto = tk.StringVar(
            value=f"{precio_gusto_extra():g}")
        ttk.Entry(fila_gusto, textvariable=self.var_precio_gusto,
                  width=7).pack(side="left", padx=4)
        ttk.Button(fila_gusto, text="💾  Guardar",
                   command=self._guardar_precio_gusto).pack(side="left",
                                                            padx=6)
        self._cargar_productos()

    def _guardar_precio_gusto(self):
        try:
            precio = float(self.var_precio_gusto.get().replace(",", "."))
            if precio < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Gustos", "El precio del gusto no es válido.",
                                 parent=self)
            return
        cfg_set("precio_gusto", f"{precio:g}")
        self.publicar_carta_fondo()
        messagebox.showinfo(
            "Gustos", f"Listo: cada gusto de pizzería suma {fmt(precio)}.",
            parent=self)

    def _texto_promo(self, promo, desde, hasta):
        """Cómo se muestra la promoción en la lista de productos."""
        if not promo or promo <= 0:
            return "—"
        corta = lambda f: f"{f[8:10]}/{f[5:7]}" if len(f) >= 10 else f
        if promo_vigente(promo, desde, hasta):
            return fmt(promo) + (f" hasta {corta(hasta)}" if hasta else "")
        hoy = datetime.date.today().isoformat()
        if desde and desde > hoy:
            return f"{fmt(promo)} desde {corta(desde)}"
        return f"{fmt(promo)} (vencida)"

    def _cargar_productos(self):
        self.tree_productos.delete(*self.tree_productos.get_children())
        con = db()
        for pid, nombre, precio, cat, usar, stock, smin, pp, pd, ph in \
                con.execute(
                    "SELECT id, nombre, precio, categoria, usar_stock, stock, "
                    "stock_min, promo_precio, promo_desde, promo_hasta "
                    "FROM productos ORDER BY categoria, nombre"):
            if usar:
                stock_txt = f"{int(stock or 0)} (avisa ≤ {int(smin or 0)})"
                tags = ("bajo",) if (stock or 0) <= (smin or 0) else ()
            else:
                stock_txt, tags = "—", ()
            if not tags and promo_vigente(pp, pd, ph):
                tags = ("promo",)
            self.tree_productos.insert(
                "", "end", iid=str(pid), tags=tags,
                values=(cat, nombre, fmt(precio),
                        self._texto_promo(pp, pd, ph), stock_txt))
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
            "SELECT nombre, precio, categoria, usar_stock, stock, stock_min, "
            "promo_precio, promo_desde, promo_hasta "
            "FROM productos WHERE id=?", (int(sel[0]),)).fetchone()
        con.close()
        if row:
            self.var_p_nombre.set(row[0])
            self.var_p_precio.set(f"{row[1]:g}")
            self.var_p_cat.set(row[2])
            self.var_p_usar.set(bool(row[3]))
            self.var_p_stock.set(str(int(row[4] or 0)))
            self.var_p_stockmin.set(str(int(row[5] or 0)))
            self.var_p_promo.set(f"{row[6]:g}" if row[6] else "")
            self.var_p_pdesde.set(row[7] or "")
            self.var_p_phasta.set(row[8] or "")

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
        promo_txt = self.var_p_promo.get().strip().replace(",", ".")
        try:
            promo = float(promo_txt) if promo_txt else 0.0
            if promo < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Producto", "El precio de promoción no es "
                                 "válido.", parent=self)
            return None
        if promo and promo >= precio:
            messagebox.showerror(
                "Producto", "El precio de promoción tiene que ser menor "
                "al precio normal.", parent=self)
            return None
        desde = self.var_p_pdesde.get().strip()
        hasta = self.var_p_phasta.get().strip()
        for etiqueta, valor in (("desde", desde), ("hasta", hasta)):
            if valor:
                try:
                    datetime.date.fromisoformat(valor)
                except ValueError:
                    messagebox.showerror(
                        "Producto", f'La fecha "{etiqueta}" de la promoción '
                        "no es válida (formato AAAA-MM-DD).", parent=self)
                    return None
        if desde and hasta and desde > hasta:
            messagebox.showerror(
                "Producto", 'En la promoción, "desde" no puede ser posterior '
                'a "hasta".', parent=self)
            return None
        return (nombre, precio, self.var_p_cat.get(),
                1 if self.var_p_usar.get() else 0, stock, stock_min,
                promo, desde, hasta)

    def _producto_agregar(self):
        datos = self._leer_form_producto()
        if not datos:
            return
        con = db()
        con.execute("INSERT INTO productos(nombre, precio, categoria, "
                    "usar_stock, stock, stock_min, promo_precio, promo_desde, "
                    "promo_hasta) VALUES (?,?,?,?,?,?,?,?,?)", datos)
        con.commit()
        con.close()
        self.var_p_nombre.set("")
        self.var_p_precio.set("")
        self.var_p_promo.set("")
        self.var_p_pdesde.set("")
        self.var_p_phasta.set("")
        self._cargar_productos()
        self.publicar_carta_fondo()

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
                    "usar_stock=?, stock=?, stock_min=?, promo_precio=?, "
                    "promo_desde=?, promo_hasta=? WHERE id=?",
                    (*datos, int(sel[0])))
        con.commit()
        con.close()
        self._cargar_productos()
        self.publicar_carta_fondo()

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
        self.publicar_carta_fondo()

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
        ttk.Label(barra, text="Canal:", style="Panel.TLabel")\
            .pack(side="left", padx=(10, 0))
        self.var_canal_rep = tk.StringVar(value="Todos")
        cb_canal = ttk.Combobox(barra, textvariable=self.var_canal_rep,
                                state="readonly", width=11,
                                values=["Todos", "Salón", "Mostrador",
                                        "Delivery"])
        cb_canal.pack(side="left", padx=6)
        cb_canal.bind("<<ComboboxSelected>>", lambda e: self._cargar_ventas())
        ttk.Button(barra, text="Actualizar",
                   command=self._cargar_ventas).pack(side="left")
        ttk.Button(barra, text="Exportar CSV",
                   command=self._exportar_csv).pack(side="left", padx=8)
        self.lbl_resumen = ttk.Label(barra, text="", style="Panel.TLabel",
                                     font=(FONT, 11, "bold"))
        self.lbl_resumen.pack(side="right")

        cols = ("hora", "canal", "detalle", "mozo", "modo", "medio", "total")
        self.tree_ventas = ttk.Treeview(f, columns=cols, show="headings")
        for col, txt, w, anchor in [("hora", "Hora", 70, "center"),
                                    ("canal", "Canal", 90, "w"),
                                    ("detalle", "Mesa / cliente", 190, "w"),
                                    ("mozo", "Mozo/a", 120, "w"),
                                    ("modo", "Tipo de cobro", 120, "w"),
                                    ("medio", "Medio de pago", 120, "w"),
                                    ("total", "Total", 100, "e")]:
            self.tree_ventas.heading(col, text=txt)
            self.tree_ventas.column(col, width=w, anchor=anchor)
        self.tree_ventas.grid(row=2, column=0, sticky="nsew")

        self.lbl_por_mozo = ttk.Label(f, text="", style="Panel.TLabel",
                                      justify="left")
        self.lbl_por_mozo.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self._cargar_ventas()

    def _ventas_del_dia(self):
        fecha = self.var_fecha.get().strip()
        filtro = {"Salón": "salon", "Mostrador": "mostrador",
                  "Delivery": "delivery"}.get(self.var_canal_rep.get())
        sql = ("SELECT fecha, mesa, mozo, modo, medio, total, canal, cliente "
               "FROM ventas WHERE fecha LIKE ?")
        params = [fecha + "%"]
        if filtro:
            sql += " AND canal=?"
            params.append(filtro)
        con = db()
        rows = con.execute(sql + " ORDER BY fecha", params).fetchall()
        con.close()
        return rows

    def _cargar_ventas(self):
        self.tree_ventas.delete(*self.tree_ventas.get_children())
        modos = {"una": "Una cuenta", "comensal": "Por comensal",
                 "iguales": "Partes iguales"}
        total_dia = 0.0
        por_mozo, por_medio, por_canal = {}, {}, {}
        rows = self._ventas_del_dia()
        for fecha, mesa, mozo, modo, medio, total, canal, cliente in rows:
            hora = fecha[11:16] if len(fecha) >= 16 else fecha
            if canal == "salon" or mesa is not None:
                detalle = f"Mesa {mesa}"
            else:
                detalle = cliente or "-"
            self.tree_ventas.insert("", "end", values=(
                hora, CANAL_NOMBRE.get(canal, canal or "salon"), detalle,
                mozo or "-", modos.get(modo, modo), medio or "-", fmt(total)))
            total_dia += total
            if canal in (None, "", "salon"):
                por_mozo[mozo or "(sin mozo)"] = \
                    por_mozo.get(mozo or "(sin mozo)", 0) + total
            por_medio[medio or "-"] = por_medio.get(medio or "-", 0) + total
            nombre_canal = CANAL_NOMBRE.get(canal, canal or "salon")
            por_canal[nombre_canal] = por_canal.get(nombre_canal, 0) + total
        self.lbl_resumen.config(
            text=f"{len(rows)} ventas — Total del día: {fmt(total_dia)}")
        if rows:
            lineas = ["Por canal:   " + "   |   ".join(
                f"{c}: {fmt(t)}" for c, t in
                sorted(por_canal.items(), key=lambda kv: -kv[1]))]
            if por_mozo:
                lineas.append("Por mozo/a (salón):   " + "   |   ".join(
                    f"{m}: {fmt(t)}" for m, t in
                    sorted(por_mozo.items(), key=lambda kv: -kv[1])))
            lineas.append("Por medio de pago:   " + "   |   ".join(
                f"{m}: {fmt(t)}" for m, t in
                sorted(por_medio.items(), key=lambda kv: -kv[1])))
            self.lbl_por_mozo.config(text="\n".join(lineas))
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
            w.writerow(["fecha", "mesa", "mozo", "modo", "medio", "total",
                        "canal", "cliente"])
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
        sc = MarcoScroll(self.tab_cfg)
        sc.pack(fill="both", expand=True)
        f = sc.interior
        f.columnconfigure(0, weight=0, minsize=430)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(3, weight=1)

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
        ttk.Radiobutton(imp, text="Térmica ESC/POS por USB",
                        value="dispositivo", variable=self.var_imp_modo)\
            .grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))
        dev_guardado = cfg_get("imp_dev")
        if sys.platform.startswith("win") and dev_guardado.startswith("/dev"):
            dev_guardado = ""  # valor viejo de Linux: en Windows no sirve
        self.var_imp_dev = tk.StringVar(value=dev_guardado)
        ttk.Entry(imp, textvariable=self.var_imp_dev, width=24)\
            .grid(row=4, column=0, columnspan=2, sticky="w", padx=(24, 0),
                  pady=(0, 2))
        ttk.Label(imp, foreground="gray",
                  text=("En Windows: el nombre de la impresora tal como "
                        "figura en el panel\nde impresoras (vacío = la "
                        "predeterminada). En Linux: /dev/usb/lp0."))\
            .grid(row=5, column=0, columnspan=2, sticky="w", padx=(24, 0),
                  pady=(0, 4))
        self.var_imp_corte = tk.BooleanVar(value=cfg_get("imp_corte", "1") == "1")
        ttk.Checkbutton(imp, text="Cortar el papel al final (térmicas)",
                        variable=self.var_imp_corte)\
            .grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 2))
        self.var_imp_grande = tk.BooleanVar(
            value=cfg_get("imp_grande", "1") == "1")
        ttk.Checkbutton(imp, text="Letra grande en el ticket (doble alto, "
                        "térmicas)", variable=self.var_imp_grande)\
            .grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 2))
        fila_imp = ttk.Frame(imp)
        fila_imp.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(fila_imp, text="💾  Guardar impresora",
                   command=self._guardar_impresora).pack(side="left")
        ttk.Button(fila_imp, text="🖨  Ticket de prueba",
                   command=self._ticket_prueba).pack(side="left", padx=8)

        # --- comandera de mozos (celulares)
        com = ttk.Labelframe(
            f, text=" Comandera para mozos (celulares por WiFi) ", padding=12)
        com.grid(row=3, column=0, sticky="new", padx=(0, 10), pady=(10, 0))
        com.columnconfigure(1, weight=1)
        self.var_mz_activo = tk.BooleanVar(
            value=cfg_get("mozos_activo", "1") == "1")
        ttk.Checkbutton(com, text="Activar la comandera al abrir el programa",
                        variable=self.var_mz_activo)\
            .grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        self.var_mz_comanda = tk.BooleanVar(
            value=cfg_get("mozos_comanda", "1") == "1")
        ttk.Checkbutton(com,
                        text="Imprimir comanda de cocina al recibir un pedido",
                        variable=self.var_mz_comanda)\
            .grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        fila_pto = ttk.Frame(com)
        fila_pto.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 2))
        ttk.Label(fila_pto, text="Puerto:").pack(side="left")
        self.var_mz_puerto = tk.StringVar(
            value=cfg_get("mozos_puerto", str(comandera.PUERTO_DEFECTO)))
        ttk.Entry(fila_pto, textvariable=self.var_mz_puerto,
                  width=7).pack(side="left", padx=6)
        ttk.Button(fila_pto, text="💾  Guardar y aplicar",
                   command=self._guardar_comandera).pack(side="left", padx=8)
        ttk.Label(com, text="Los mozos abren esta dirección en el navegador "
                            "del celular (misma red WiFi que esta PC):",
                  wraplength=380, justify="left")\
            .grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.var_mz_url = tk.StringVar()
        ttk.Entry(com, textvariable=self.var_mz_url, state="readonly",
                  font=(FONT, 11, "bold"))\
            .grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        ttk.Label(com, text='Con "Agregar a pantalla de inicio" queda como '
                            "una app con su ícono.",
                  foreground=COL_MUTED, wraplength=380, justify="left")\
            .grid(row=5, column=0, columnspan=2, sticky="w")
        fila_ip = ttk.Frame(com)
        fila_ip.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        if sys.platform.startswith("win"):
            ttk.Button(fila_ip, text="🔒  Fijar la IP de esta PC",
                       command=self._fijar_ip_windows).pack(side="left")
            ttk.Button(fila_ip, text="↩  Volver a IP automática",
                       command=self._ip_automatica_windows)\
                .pack(side="left", padx=8)
        else:
            ttk.Label(fila_ip, text="Para que la dirección no cambie, fijá la "
                                    "IP de esta PC (en Windows hay un botón "
                                    "acá; en Linux, reservala en el router).",
                      foreground=COL_MUTED, wraplength=380,
                      justify="left").pack(side="left")
        self._actualizar_url_comandera()

        # --- actualizaciones del programa
        act = ttk.Labelframe(f, text=" Actualizaciones del programa ",
                             padding=12)
        act.grid(row=4, column=0, sticky="new", padx=(0, 10), pady=(10, 0))
        act.columnconfigure(1, weight=1)
        self.var_up_auto = tk.BooleanVar(
            value=cfg_get("update_auto", "1") == "1")
        ttk.Checkbutton(act, text="Buscar actualizaciones al abrir el "
                        "programa (necesita internet)",
                        variable=self.var_up_auto)\
            .grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(act, text="Dirección de descarga:")\
            .grid(row=1, column=0, sticky="w", pady=(4, 0))
        # solo lectura: si se pudiera editar y alguien la borrara por
        # accidente, el local se quedaría sin actualizaciones
        self.var_up_url = tk.StringVar(value=url_actualizaciones())
        ttk.Entry(act, textvariable=self.var_up_url, state="readonly")\
            .grid(row=1, column=1, sticky="ew", pady=(4, 0), padx=(6, 0))
        fila_up = ttk.Frame(act)
        fila_up.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(fila_up, text="💾  Guardar",
                   command=self._guardar_actualizaciones).pack(side="left")
        ttk.Button(fila_up, text="🔄  Buscar actualización ahora",
                   command=self._buscar_actualizacion_manual)\
            .pack(side="left", padx=8)
        ttk.Label(fila_up, text=f"Versión instalada: {VERSION}",
                  foreground=COL_MUTED).pack(side="right")

        # --- carta digital (QR para los clientes)
        car = ttk.Labelframe(f, text=" Carta digital para los clientes (QR) ",
                             padding=12)
        car.grid(row=5, column=0, sticky="new", padx=(0, 10), pady=(10, 0))
        car.columnconfigure(1, weight=1)
        ttk.Label(car, text="Dirección pública (el QR lleva acá):")\
            .grid(row=0, column=0, columnspan=2, sticky="w")
        ent_carta = ttk.Entry(car)
        ent_carta.insert(0, URL_CARTA)
        ent_carta.configure(state="readonly")
        ent_carta.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        ttk.Label(car, text="Código de publicación (token de GitHub):")\
            .grid(row=2, column=0, columnspan=2, sticky="w")
        self.var_gh_token = tk.StringVar(value=cfg_get("gh_token", ""))
        ttk.Entry(car, textvariable=self.var_gh_token, show="•")\
            .grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        fila_car = ttk.Frame(car)
        fila_car.grid(row=4, column=0, columnspan=2, sticky="ew")
        ttk.Button(fila_car, text="💾  Guardar",
                   command=self._guardar_carta_cfg).pack(side="left")
        ttk.Button(fila_car, text="🌐  Publicar carta ahora",
                   command=self._publicar_carta_manual)\
            .pack(side="left", padx=8)
        ttk.Button(fila_car, text="🖨  Cartelitos QR",
                   command=self._cartelitos_qr).pack(side="left")
        ttk.Label(car, foreground=COL_MUTED, wraplength=380, justify="left",
                  text="La carta se vuelve a publicar sola cada vez que "
                       "cambiás productos, precios o promociones. Los "
                       "clientes la abren con el QR desde cualquier lado, "
                       "sin el WiFi del local.")\
            .grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # --- mesas y mozos
        mesas = ttk.Labelframe(f, text=" Mesas y mozos ", padding=12)
        mesas.grid(row=1, column=1, rowspan=5, sticky="nsew")
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
        self.title("Gestión — " + cfg_get("nombre", "Restaurante")
                   + f"  ·  v{VERSION}")
        messagebox.showinfo("Configuración", "Datos guardados.", parent=self)

    def _guardar_carta_cfg(self):
        cfg_set("gh_token", self.var_gh_token.get().strip())
        messagebox.showinfo("Carta digital", "Guardado.", parent=self)

    def _publicar_carta_manual(self):
        cfg_set("gh_token", self.var_gh_token.get().strip())
        error = publicar_carta()
        if error:
            messagebox.showerror("Carta digital",
                                 f"No se pudo publicar:\n{error}", parent=self)
        else:
            messagebox.showinfo(
                "Carta digital",
                "Carta publicada. En 1 o 2 minutos se ve actualizada en:\n"
                + URL_CARTA, parent=self)

    def publicar_carta_fondo(self):
        """Re-publica la carta sin molestar (al cambiar productos/promos)."""
        if not cfg_get("gh_token", "").strip():
            return  # sin código de publicación no hay nada que hacer
        threading.Thread(target=publicar_carta, daemon=True).start()

    def _cartelitos_qr(self):
        cant = self._contar_mesas()
        ruta = os.path.join(APP_DIR, "cartelitos_qr.html")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(generar_cartelitos_html(cant))
        import webbrowser
        webbrowser.open("file://" + ruta)
        messagebox.showinfo(
            "Cartelitos QR",
            f"Se abrió en el navegador una hoja con {cant} cartelitos "
            "(uno por mesa) para imprimir y recortar.\n\n"
            f"Quedó guardada en:\n{ruta}", parent=self)

    def _guardar_actualizaciones(self, avisar=True):
        cfg_set("update_auto", "1" if self.var_up_auto.get() else "0")
        if avisar:
            messagebox.showinfo("Actualizaciones",
                                "Configuración de actualizaciones guardada.",
                                parent=self)

    def _guardar_impresora(self):
        cfg_set("imp_modo", self.var_imp_modo.get())
        cfg_set("imp_red", self.var_imp_red.get().strip())
        cfg_set("imp_dev", self.var_imp_dev.get().strip())
        cfg_set("imp_corte", "1" if self.var_imp_corte.get() else "0")
        cfg_set("imp_grande", "1" if self.var_imp_grande.get() else "0")
        messagebox.showinfo("Impresora", "Configuración de impresora guardada.",
                            parent=self)

    def _fijar_ip_windows(self):
        try:
            red = datos_red_windows()
            alias, ip = red["InterfaceAlias"], red["IP"]
            prefijo, puerta = red["Prefijo"], red["Puerta"]
            dns = red.get("DNS", "")
        except Exception:
            messagebox.showerror(
                "IP fija", "No se pudo leer la configuración de red de "
                "Windows.\n¿La PC está conectada a la red del local?",
                parent=self)
            return
        if not messagebox.askyesno(
                "Fijar IP de esta PC",
                "Se va a dejar fija la configuración de red actual:\n\n"
                f"    Conexión: {alias}\n"
                f"    IP fija: {ip}\n"
                f"    Máscara: {mascara_desde_prefijo(prefijo)}\n"
                f"    Puerta de enlace: {puerta}\n"
                f"    DNS: {dns or puerta}\n\n"
                "Así la dirección de la comandera no cambia nunca.\n"
                "Windows va a pedir permiso de administrador.\n¿Continuar?",
                parent=self):
            return
        error = ejecutar_bat_admin(
            armar_bat_ip_fija(alias, ip, prefijo, puerta, dns))
        if error:
            messagebox.showwarning("IP fija", error, parent=self)
        else:
            messagebox.showinfo(
                "IP fija",
                "Aceptá el permiso de administrador en la ventana que abre "
                "Windows.\n\nLa dirección para los mozos queda siempre en:\n"
                f"http://{ip}:{cfg_get('mozos_puerto', '8750')}",
                parent=self)

    def _ip_automatica_windows(self):
        try:
            alias = datos_red_windows()["InterfaceAlias"]
        except Exception:
            messagebox.showerror("IP fija", "No se pudo leer la configuración "
                                 "de red de Windows.", parent=self)
            return
        if not messagebox.askyesno(
                "IP automática",
                f'La conexión "{alias}" vuelve a IP automática (DHCP).\n'
                "La dirección de la comandera podría cambiar.\n¿Continuar?",
                parent=self):
            return
        error = ejecutar_bat_admin(armar_bat_ip_dhcp(alias))
        if error:
            messagebox.showwarning("IP fija", error, parent=self)

    def _ofrecer_ip_fija(self):
        cfg_set("ip_fija_ofrecida", "1")
        if messagebox.askyesno(
                "Comandera — IP fija",
                "Para que la dirección que usan los mozos no cambie nunca, "
                "conviene dejar fija la IP de esta PC.\n\n"
                "¿Configurarla ahora? (Windows va a pedir permiso de "
                "administrador; también se puede hacer después desde "
                "Configuración → Comandera)", parent=self):
            self._fijar_ip_windows()

    def _guardar_comandera(self):
        puerto = self.var_mz_puerto.get().strip()
        if not puerto.isdigit() or not 1 <= int(puerto) <= 65535:
            messagebox.showerror("Comandera", "El puerto no es válido "
                                 "(usá un número, por ej. 8750).", parent=self)
            return
        cfg_set("mozos_activo", "1" if self.var_mz_activo.get() else "0")
        cfg_set("mozos_comanda", "1" if self.var_mz_comanda.get() else "0")
        cfg_set("mozos_puerto", puerto)
        if self.var_mz_activo.get():
            self._comandera_arrancar()
            if self.comandera_srv:
                messagebox.showinfo(
                    "Comandera",
                    "Comandera activa. En el celular abrir:\n\n"
                    f"{self.comandera_url}", parent=self)
        else:
            self._comandera_apagar()
            messagebox.showinfo("Comandera", "Comandera apagada.", parent=self)

    def _ticket_prueba(self):
        cfg_set("imp_modo", self.var_imp_modo.get())
        cfg_set("imp_red", self.var_imp_red.get().strip())
        cfg_set("imp_dev", self.var_imp_dev.get().strip())
        cfg_set("imp_corte", "1" if self.var_imp_corte.get() else "0")
        cfg_set("imp_grande", "1" if self.var_imp_grande.get() else "0")
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
        self.publicar_carta_fondo()
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
