#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comandera web para los mozos — El Horno de Leo
==============================================
Servidor HTTP liviano (solo librería estándar) que corre dentro del programa
principal. Los mozos entran desde el celular —conectado a la misma red WiFi
que la PC— a  http://IP-de-la-PC:8750 , eligen la mesa y cargan el pedido.
El pedido queda en la misma base de datos (la mesa se ve ocupada al instante
en la PC, descuenta stock) y, si está activado, la comanda de cocina se
imprime sola.

En el celular se puede usar "Agregar a pantalla de inicio" para que quede
como una app con ícono propio, sin instalar nada.

Este módulo no importa `restaurante` (evita el import circular): el programa
principal le presta las funciones que necesita al llamar a `iniciar(deps)`.
"""

import json
import socket
import datetime
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PUERTO_DEFECTO = 8750

# funciones prestadas por restaurante.py: db, cfg_get, centrar,
# imprimir_texto, categorias, ancho
_d = {}


def ip_local():
    """IP de esta PC en la red local (la que hay que abrir en el celular)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no envía nada; solo elige la interfaz
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def iniciar(deps, puerto=PUERTO_DEFECTO):
    """Arranca el servidor en un hilo demonio.
    Devuelve (servidor, url). Lanza OSError si el puerto está ocupado."""
    _d.update(deps)
    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), _Handler)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor, f"http://{ip_local()}:{puerto}"


def detener(servidor):
    """Apaga el servidor y libera el puerto (espera menos de medio segundo)."""
    servidor.shutdown()
    servidor.server_close()


# ---------------------------------------------------------------- API

def _estado():
    con = _d["db"]()
    mesas = con.execute(
        "SELECT numero, mozo, comensales, abierta FROM mesas "
        "ORDER BY numero").fetchall()
    totales = dict(con.execute(
        "SELECT mesa, SUM(precio*cantidad) FROM pedidos GROUP BY mesa"))
    productos = con.execute(
        "SELECT id, nombre, precio, categoria, usar_stock, stock "
        "FROM productos ORDER BY categoria, nombre").fetchall()
    con.close()
    return {
        "nombre": _d["cfg_get"]("nombre", "El Horno de Leo"),
        "categorias": _d["categorias"],
        "mesas": [{"numero": n, "mozo": mz or "", "comensales": c or 0,
                   "abierta": bool(a), "total": totales.get(n, 0) or 0}
                  for n, mz, c, a in mesas],
        "productos": [{"id": pid, "nombre": nom, "precio": pre,
                       "categoria": cat,
                       "agotado": bool(usar) and (stk or 0) <= 0,
                       "quedan": int(stk or 0) if usar else None}
                      for pid, nom, pre, cat, usar, stk in productos],
    }


def _mesa_detalle(numero):
    con = _d["db"]()
    fila = con.execute(
        "SELECT mozo, comensales FROM mesas WHERE numero=?",
        (numero,)).fetchone()
    if not fila:
        con.close()
        return None
    items = con.execute(
        "SELECT nombre, precio, cantidad, comensal FROM pedidos "
        "WHERE mesa=? ORDER BY comensal, id", (numero,)).fetchall()
    con.close()
    return {"numero": numero, "mozo": fila[0] or "",
            "comensales": fila[1] or 0,
            "items": [{"nombre": n, "precio": p, "cantidad": c, "comensal": co}
                      for n, p, c, co in items],
            "total": sum(p * c for _, p, c, _ in items)}


def _recibir_pedido(datos):
    """Valida y guarda un pedido enviado desde el celular.
    Devuelve (código_http, respuesta_json)."""
    try:
        mesa = int(datos["mesa"])
        mozo = str(datos.get("mozo", "")).strip()[:40]
        comensales = max(0, min(int(datos.get("comensales", 0) or 0), 30))
        items = datos["items"]
        assert isinstance(items, list) and items
        pedido = []
        for it in items:
            pid = int(it["id"])
            cant = int(it["cantidad"])
            comensal = int(it.get("comensal", 0))
            assert 1 <= cant <= 99 and 0 <= comensal <= 30
            pedido.append((pid, cant, comensal))
    except (KeyError, ValueError, TypeError, AssertionError):
        return 400, {"error": "Pedido mal formado."}

    con = _d["db"]()
    try:
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("SELECT 1 FROM mesas WHERE numero=?",
                           (mesa,)).fetchone():
            con.rollback()
            return 404, {"error": f"La mesa {mesa} no existe."}

        faltas, filas = [], []
        for pid, cant, comensal in pedido:
            prod = con.execute(
                "SELECT nombre, precio, usar_stock, stock FROM productos "
                "WHERE id=?", (pid,)).fetchone()
            if not prod:
                con.rollback()
                return 400, {"error": "Hay un producto que ya no existe; "
                                      "actualizá la carta en el celular."}
            nombre, precio, usar_stock, stock = prod
            if usar_stock and (stock or 0) < cant:
                faltas.append(f"{nombre} (quedan {int(stock or 0)})")
            filas.append((pid, nombre, precio, cant, comensal, usar_stock))
        if faltas:
            con.rollback()
            return 409, {"error": "Sin stock suficiente de:\n"
                                  + "\n".join(faltas)}

        for pid, nombre, precio, cant, comensal, usar_stock in filas:
            con.execute(
                "INSERT INTO pedidos(mesa, nombre, precio, cantidad, comensal)"
                " VALUES (?,?,?,?,?)", (mesa, nombre, precio, cant, comensal))
            if usar_stock:
                con.execute("UPDATE productos SET stock=stock-? WHERE id=?",
                            (cant, pid))

        actual = con.execute(
            "SELECT comensales FROM mesas WHERE numero=?", (mesa,)).fetchone()
        comensales_final = max(actual[0] or 0, comensales,
                               max(c for _, _, c in pedido))
        if mozo:
            con.execute("UPDATE mesas SET mozo=?, comensales=?, abierta=1 "
                        "WHERE numero=?", (mozo, comensales_final, mesa))
        else:
            con.execute("UPDATE mesas SET comensales=?, abierta=1 "
                        "WHERE numero=?", (comensales_final, mesa))
        total = con.execute(
            "SELECT COALESCE(SUM(precio*cantidad),0) FROM pedidos "
            "WHERE mesa=?", (mesa,)).fetchone()[0]
        con.commit()
    finally:
        con.close()

    _imprimir_comanda(mesa, mozo,
                      [(cant, nombre, comensal)
                       for _, nombre, _, cant, comensal, _ in filas])
    return 200, {"ok": True, "total": total}


def _imprimir_comanda(mesa, mozo, items):
    """Comanda de cocina solo con lo recién pedido (si está activada)."""
    if _d["cfg_get"]("mozos_comanda", "1") != "1":
        return
    centrar = _d["centrar"]
    ancho = _d.get("ancho", 42)
    ahora = datetime.datetime.now()
    lineas = [centrar("*** COMANDA COCINA ***"),
              f"Mesa {mesa}  -  {ahora:%H:%M}",
              f"Mozo/a: {mozo or '-'}",
              centrar("(pedido desde el celular)"),
              "-" * ancho]
    for cant, nombre, comensal in items:
        quien = "" if comensal == 0 else f"  (comensal {comensal})"
        lineas.append(f"{cant:>2} x {nombre}{quien}")
    lineas.append("")
    try:
        _d["imprimir_texto"]("\n".join(lineas), "comanda")
    except Exception:
        pass  # el hilo del servidor nunca debe caerse por la impresora


# ---------------------------------------------------------------- handler

class _Handler(BaseHTTPRequestHandler):
    server_version = "Comandera/1.0"

    def log_message(self, *args):
        pass  # sin ruido en la consola

    def _responder(self, cuerpo, tipo, codigo=200):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, obj, codigo=200):
        self._responder(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", codigo)

    def do_GET(self):
        try:
            url = urlparse(self.path)
            if url.path == "/":
                self._responder(PAGINA.encode("utf-8"),
                                "text/html; charset=utf-8")
            elif url.path == "/manifest.json":
                self._responder(MANIFIESTO.encode("utf-8"),
                                "application/manifest+json; charset=utf-8")
            elif url.path == "/api/estado":
                self._json(_estado())
            elif url.path == "/api/mesa":
                try:
                    numero = int(parse_qs(url.query).get("n", [""])[0])
                except ValueError:
                    self._json({"error": "Mesa inválida."}, 400)
                    return
                detalle = _mesa_detalle(numero)
                if detalle is None:
                    self._json({"error": f"La mesa {numero} no existe."}, 404)
                else:
                    self._json(detalle)
            elif url.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self._json({"error": "No existe."}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            if urlparse(self.path).path != "/api/pedido":
                self._json({"error": "No existe."}, 404)
                return
            try:
                largo = int(self.headers.get("Content-Length", 0))
                datos = json.loads(self.rfile.read(largo).decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "Pedido mal formado."}, 400)
                return
            codigo, respuesta = _recibir_pedido(datos)
            self._json(respuesta, codigo)
        except (BrokenPipeError, ConnectionResetError):
            pass


# ---------------------------------------------------------------- app móvil

_ICONO = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
          "viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='22' "
          "fill='%238c2f39'/%3E%3Ctext x='50' y='64' font-size='40' "
          "text-anchor='middle' fill='%23f7f2ea' font-family='sans-serif' "
          "font-weight='bold'%3EHL%3C/text%3E%3C/svg%3E")

MANIFIESTO = json.dumps({
    "name": "El Horno de Leo — Comandera",
    "short_name": "Comandera",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#f7f2ea",
    "theme_color": "#8c2f39",
    "icons": [{"src": _ICONO, "sizes": "any", "type": "image/svg+xml"}],
}, ensure_ascii=False)

PAGINA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#8c2f39">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<link rel="manifest" href="/manifest.json">
<title>Comandera</title>
<style>
  :root{
    --bordo:#8c2f39; --naranja:#c96f2c; --crema:#f7f2ea; --panel:#ffffff;
    --verde:#7fb069; --rojo:#d94f4f; --texto:#2e2a26; --suave:#8a8178;
    --borde:#e8e0d3;
  }
  *{box-sizing:border-box; margin:0; -webkit-tap-highlight-color:transparent}
  html,body{height:100%}
  body{font-family:system-ui,sans-serif; background:var(--crema);
       color:var(--texto); display:flex; flex-direction:column}
  header{background:var(--bordo); color:#fff; padding:12px 14px;
         display:flex; align-items:center; gap:10px; flex:none;
         position:sticky; top:0; z-index:10}
  header h1{font-size:1.05rem; flex:1; white-space:nowrap;
            overflow:hidden; text-overflow:ellipsis}
  #btnVolver{background:rgba(255,255,255,.15); border:0; color:#fff;
             font-size:1.3rem; line-height:1; padding:6px 14px;
             border-radius:8px}
  #banner{display:none; background:var(--rojo); color:#fff; text-align:center;
          padding:6px; font-size:.85rem; flex:none}
  main{flex:1; overflow-y:auto; padding:12px;
       padding-bottom:calc(120px + env(safe-area-inset-bottom))}
  /* --- grilla de mesas --- */
  #grillaMesas{display:grid; gap:10px;
               grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}
  .mesa{border:0; border-radius:14px; color:#fff; padding:14px 10px;
        text-align:center; font-family:inherit; box-shadow:0 1px 3px rgba(0,0,0,.15)}
  .mesa .num{font-size:1.35rem; font-weight:700}
  .mesa .sub{font-size:.8rem; opacity:.92; margin-top:2px}
  .libre{background:var(--verde)} .ocupada{background:var(--rojo)}
  /* --- vista mesa --- */
  .tarjeta{background:var(--panel); border:1px solid var(--borde);
           border-radius:12px; padding:12px; margin-bottom:12px}
  .filaCampos{display:flex; gap:10px}
  .filaCampos label{flex:1; font-size:.8rem; color:var(--suave)}
  .filaCampos label:last-child{flex:0 0 110px}
  input,select{width:100%; padding:10px; margin-top:3px; font-size:1rem;
        border:1px solid var(--borde); border-radius:8px; background:#fff;
        font-family:inherit; color:var(--texto)}
  h2{font-size:.85rem; color:var(--bordo); text-transform:uppercase;
     letter-spacing:.04em; margin:14px 0 8px}
  .itemPedido{display:flex; justify-content:space-between; gap:8px;
              padding:5px 0; font-size:.92rem; border-bottom:1px dashed var(--borde)}
  .itemPedido:last-child{border-bottom:0}
  .itemPedido .quien{color:var(--suave); font-size:.78rem}
  .totalMesa{text-align:right; font-weight:700; color:var(--bordo);
             padding-top:8px}
  /* --- catálogo --- */
  #chips{display:flex; gap:8px; overflow-x:auto; padding:2px 0 10px}
  .chip{border:1px solid var(--bordo); color:var(--bordo); background:#fff;
        border-radius:999px; padding:7px 14px; font-size:.85rem;
        white-space:nowrap; font-family:inherit}
  .chip.activa{background:var(--bordo); color:#fff}
  .prod{display:flex; align-items:center; gap:10px; width:100%;
        text-align:left; background:var(--panel); border:1px solid var(--borde);
        border-radius:10px; padding:11px 12px; margin-bottom:8px;
        font-family:inherit; font-size:.95rem; color:var(--texto)}
  .prod:active{background:#f1e7d8}
  .prod .nom{flex:1}
  .prod .aviso{display:block; color:var(--rojo); font-size:.75rem}
  .prod .precio{font-weight:700; color:var(--bordo)}
  .prod .mas{background:var(--naranja); color:#fff; border-radius:8px;
             padding:4px 10px; font-weight:700}
  .prod.agotado{opacity:.45}
  .prod .cuantos{background:var(--bordo); color:#fff; border-radius:999px;
                 font-size:.78rem; padding:2px 8px; font-weight:700}
  /* --- barra carrito --- */
  #barra{position:fixed; left:0; right:0; bottom:0; z-index:20;
         background:var(--panel); border-top:2px solid var(--bordo);
         padding:10px 12px calc(10px + env(safe-area-inset-bottom));
         display:none; flex-direction:column; gap:8px;
         box-shadow:0 -3px 12px rgba(0,0,0,.12)}
  #carrito{max-height:38vh; overflow-y:auto; display:none}
  .cItem{display:flex; align-items:center; gap:6px; padding:6px 0;
         border-bottom:1px dashed var(--borde); font-size:.9rem}
  .cItem .nom{flex:1; min-width:0}
  .cItem select{width:auto; padding:6px; margin:0; font-size:.8rem}
  .cItem button{border:0; background:var(--crema); border-radius:8px;
                width:34px; height:34px; font-size:1.05rem; font-family:inherit;
                color:var(--texto)}
  .cItem .borrar{color:var(--rojo)}
  .cItem .cant{min-width:20px; text-align:center; font-weight:700}
  #filaEnviar{display:flex; gap:8px}
  #btnCarrito{flex:1; border:1px solid var(--bordo); background:#fff;
              color:var(--bordo); border-radius:10px; padding:12px;
              font-size:.95rem; font-weight:700; font-family:inherit}
  #btnEnviar{flex:1.4; border:0; background:var(--bordo); color:#fff;
             border-radius:10px; padding:12px; font-size:1rem;
             font-weight:700; font-family:inherit}
  #btnEnviar:disabled{opacity:.6}
  /* --- toast --- */
  #toast{position:fixed; top:64px; left:50%; transform:translateX(-50%);
         background:var(--verde); color:#fff; padding:10px 22px;
         border-radius:999px; font-weight:700; display:none; z-index:30;
         box-shadow:0 2px 10px rgba(0,0,0,.25)}
  .vacio{color:var(--suave); text-align:center; padding:18px; font-size:.9rem}
</style>
</head>
<body>
<header>
  <button id="btnVolver" hidden>&#8249;</button>
  <h1 id="titulo">Comandera</h1>
</header>
<div id="banner">Sin conexión con la PC del restaurante…</div>

<main id="vMesas">
  <div id="grillaMesas"></div>
</main>

<main id="vMesa" hidden>
  <div class="tarjeta">
    <div class="filaCampos">
      <label>Mozo/a
        <input id="inMozo" placeholder="Tu nombre" autocomplete="off">
      </label>
      <label>Comensales
        <input id="inCom" type="number" min="1" max="30" inputmode="numeric">
      </label>
    </div>
  </div>
  <div class="tarjeta" id="tYaPedido" hidden>
    <h2 style="margin-top:0">Ya en la mesa</h2>
    <div id="yaPedido"></div>
    <div class="totalMesa" id="totalMesa"></div>
  </div>
  <div id="chips"></div>
  <input id="inBuscar" placeholder="&#128269; Buscar producto…" autocomplete="off">
  <div id="listaProd" style="margin-top:10px"></div>
</main>

<div id="barra">
  <div id="carrito"></div>
  <div id="filaEnviar">
    <button id="btnCarrito"></button>
    <button id="btnEnviar">ENVIAR PEDIDO</button>
  </div>
</div>
<div id="toast"></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
let estado = null, mesa = null, detalle = null;
let carrito = [], cat = "Todas", filtro = "", vista = "mesas";

const fmtNum = new Intl.NumberFormat("es-UY",
  {minimumFractionDigits: 0, maximumFractionDigits: 2});
const fmt = x => "$ " + fmtNum.format(x);

function el(tag, clase, texto) {
  const e = document.createElement(tag);
  if (clase) e.className = clase;
  if (texto !== undefined) e.textContent = texto;
  return e;
}

async function api(ruta, cuerpo) {
  let r;
  try {
    r = await fetch(ruta, cuerpo === undefined ? {} : {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(cuerpo)
    });
  } catch (e) {
    $("banner").style.display = "block";
    throw new Error("No hay conexión con la PC. Revisá el WiFi.");
  }
  $("banner").style.display = "none";
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(datos.error || "Error del servidor.");
  return datos;
}

/* ---------------- vista: mesas ---------------- */

async function cargarEstado() {
  estado = await api("/api/estado");
  if (vista === "mesas") renderMesas();
}

function renderMesas() {
  $("titulo").textContent = estado ? estado.nombre : "Comandera";
  const g = $("grillaMesas");
  g.replaceChildren();
  for (const m of estado.mesas) {
    const b = el("button", "mesa " + (m.abierta ? "ocupada" : "libre"));
    b.appendChild(el("div", "num", "Mesa " + m.numero));
    b.appendChild(el("div", "sub", m.mozo || "(sin mozo)"));
    b.appendChild(el("div", "sub", m.abierta ? fmt(m.total) : "Libre"));
    b.onclick = () => abrirMesa(m.numero);
    g.appendChild(b);
  }
}

/* ---------------- vista: una mesa ---------------- */

async function abrirMesa(n) {
  try {
    detalle = await api("/api/mesa?n=" + n);
    if (!estado) estado = await api("/api/estado");
  } catch (e) { alert(e.message); return; }
  mesa = n; carrito = []; cat = "Todas"; filtro = "";
  $("inBuscar").value = "";
  $("inMozo").value = detalle.mozo || localStorage.mozo || "";
  $("inCom").value = detalle.comensales > 0 ? detalle.comensales : 1;
  vista = "mesa";
  render();
}

function volver() {
  vista = "mesas"; mesa = null; carrito = [];
  render();
  cargarEstado().catch(() => {});
}

function render() {
  $("vMesas").hidden = vista !== "mesas";
  $("vMesa").hidden = vista !== "mesa";
  $("btnVolver").hidden = vista !== "mesa";
  if (vista === "mesas") {
    if (estado) renderMesas();
    $("barra").style.display = "none";
    return;
  }
  $("titulo").textContent = "Mesa " + mesa;
  renderYaPedido();
  renderChips();
  renderProductos();
  renderBarra();
}

function renderYaPedido() {
  $("tYaPedido").hidden = !detalle.items.length;
  const caja = $("yaPedido");
  caja.replaceChildren();
  for (const it of detalle.items) {
    const fila = el("div", "itemPedido");
    const izq = el("div", "", it.cantidad + " × " + it.nombre);
    if (it.comensal > 0)
      izq.appendChild(el("span", "quien", "  · comensal " + it.comensal));
    fila.appendChild(izq);
    fila.appendChild(el("div", "", fmt(it.precio * it.cantidad)));
    caja.appendChild(fila);
  }
  $("totalMesa").textContent = "Total: " + fmt(detalle.total);
}

function renderChips() {
  const caja = $("chips");
  caja.replaceChildren();
  for (const c of ["Todas", ...estado.categorias]) {
    const b = el("button", "chip" + (c === cat ? " activa" : ""), c);
    b.onclick = () => { cat = c; renderChips(); renderProductos(); };
    caja.appendChild(b);
  }
}

function enCarrito(id) {
  let n = 0;
  for (const it of carrito) if (it.id === id) n += it.cantidad;
  return n;
}

function renderProductos() {
  const caja = $("listaProd");
  caja.replaceChildren();
  const busca = filtro.trim().toLowerCase();
  let hay = false;
  for (const p of estado.productos) {
    if (cat !== "Todas" && p.categoria !== cat) continue;
    if (busca && !p.nombre.toLowerCase().includes(busca)) continue;
    hay = true;
    const b = el("button", "prod" + (p.agotado ? " agotado" : ""));
    const nom = el("span", "nom", p.nombre);
    if (p.agotado) nom.appendChild(el("span", "aviso", "SIN STOCK"));
    else if (p.quedan !== null && p.quedan <= 5)
      nom.appendChild(el("span", "aviso", "quedan " + p.quedan));
    b.appendChild(nom);
    const n = enCarrito(p.id);
    if (n) b.appendChild(el("span", "cuantos", "×" + n));
    b.appendChild(el("span", "precio", fmt(p.precio)));
    b.appendChild(el("span", "mas", "+"));
    if (!p.agotado) b.onclick = () => agregar(p);
    caja.appendChild(b);
  }
  if (!hay) caja.appendChild(el("div", "vacio", "No hay productos que coincidan."));
}

/* ---------------- carrito ---------------- */

function agregar(p) {
  const existente = carrito.find(i => i.id === p.id && i.comensal === 0);
  if (existente) existente.cantidad = Math.min(existente.cantidad + 1, 99);
  else carrito.push({id: p.id, nombre: p.nombre, precio: p.precio,
                     cantidad: 1, comensal: 0});
  renderProductos();
  renderBarra();
}

function renderBarra() {
  const barra = $("barra");
  if (vista !== "mesa" || !carrito.length) {
    barra.style.display = "none";
    $("carrito").style.display = "none";
    return;
  }
  barra.style.display = "flex";
  let unidades = 0, total = 0;
  for (const it of carrito) { unidades += it.cantidad; total += it.cantidad * it.precio; }
  $("btnCarrito").textContent = "Pedido: " + unidades + " ítem" +
    (unidades === 1 ? "" : "s") + " · " + fmt(total);
  renderCarrito();
}

function renderCarrito() {
  const caja = $("carrito");
  caja.replaceChildren();
  const nCom = Math.max(1, parseInt($("inCom").value) || 1);
  carrito.forEach((it, i) => {
    const fila = el("div", "cItem");
    fila.appendChild(el("span", "nom", it.nombre));
    const sel = el("select");
    sel.appendChild(new Option("Mesa", "0"));
    for (let c = 1; c <= nCom; c++)
      sel.appendChild(new Option("Com. " + c, String(c)));
    sel.value = String(Math.min(it.comensal, nCom));
    sel.onchange = () => { it.comensal = parseInt(sel.value); };
    fila.appendChild(sel);
    const menos = el("button", "", "−");
    menos.onclick = () => {
      it.cantidad--;
      if (it.cantidad <= 0) carrito.splice(i, 1);
      renderProductos(); renderBarra();
    };
    fila.appendChild(menos);
    fila.appendChild(el("span", "cant", String(it.cantidad)));
    const mas = el("button", "", "+");
    mas.onclick = () => { it.cantidad = Math.min(it.cantidad + 1, 99);
                          renderProductos(); renderBarra(); };
    fila.appendChild(mas);
    const borrar = el("button", "borrar", "✕");
    borrar.onclick = () => { carrito.splice(i, 1);
                             renderProductos(); renderBarra(); };
    fila.appendChild(borrar);
    caja.appendChild(fila);
  });
}

async function enviar() {
  if (!carrito.length) return;
  const btn = $("btnEnviar");
  btn.disabled = true;
  try {
    await api("/api/pedido", {
      mesa: mesa,
      mozo: $("inMozo").value.trim(),
      comensales: parseInt($("inCom").value) || 1,
      items: carrito.map(i => ({id: i.id, cantidad: i.cantidad,
                                comensal: i.comensal}))
    });
    localStorage.mozo = $("inMozo").value.trim();
    carrito = [];
    toast("Pedido enviado ✔");
    detalle = await api("/api/mesa?n=" + mesa);
    estado = await api("/api/estado");
    render();
  } catch (e) {
    alert(e.message);
    try { estado = await api("/api/estado"); renderProductos(); }
    catch (e2) {}
  } finally {
    btn.disabled = false;
  }
}

let toastTimer = null;
function toast(msj) {
  const t = $("toast");
  t.textContent = msj;
  t.style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.style.display = "none"; }, 2200);
}

/* ---------------- eventos ---------------- */

$("btnVolver").onclick = volver;
$("btnEnviar").onclick = enviar;
$("btnCarrito").onclick = () => {
  const c = $("carrito");
  c.style.display = c.style.display === "block" ? "none" : "block";
};
$("inBuscar").oninput = e => { filtro = e.target.value; renderProductos(); };
$("inCom").onchange = renderCarrito;

setInterval(() => {
  if (vista === "mesas") cargarEstado().catch(() => {});
}, 8000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && vista === "mesas") cargarEstado().catch(() => {});
});

/* si la dirección trae ?mesa=N se abre esa mesa directo
   (sirve para pegar un QR distinto en cada mesa) */
const mesaInicial = parseInt(
  new URLSearchParams(location.search).get("mesa"));
cargarEstado()
  .then(() => { if (mesaInicial) return abrirMesa(mesaInicial); })
  .catch(e => { $("banner").style.display = "block"; });
</script>
</body>
</html>
"""
