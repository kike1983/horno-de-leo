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

# avisos para la interfaz de la PC (campana): los llena el hilo del
# servidor y los retira la app cada segundo
_eventos = []
_eventos_lock = threading.Lock()


def _avisar(tipo, mesa):
    with _eventos_lock:
        _eventos.append((tipo, mesa))


def eventos_pendientes():
    """Devuelve y limpia los avisos acumulados: [("pedido"|"cuenta", mesa)]."""
    with _eventos_lock:
        pendientes = _eventos[:]
        _eventos.clear()
    return pendientes


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
        "SELECT numero, mozo, comensales, abierta, pide_cuenta FROM mesas "
        "ORDER BY numero").fetchall()
    totales = dict(con.execute(
        "SELECT mesa, SUM(precio*cantidad) FROM pedidos GROUP BY mesa"))
    productos = con.execute(
        "SELECT id, nombre, precio, categoria, usar_stock, stock, "
        "promo_precio, promo_desde, promo_hasta "
        "FROM productos ORDER BY categoria, nombre").fetchall()
    con.close()
    pv = _d["precio_vigente"]
    lleva = _d["lleva_gustos"]
    incluidos = _d["gustos_incluidos"]
    return {
        "nombre": _d["cfg_get"]("nombre", "El Horno de Leo"),
        "categorias": _d["categorias"],
        "gustos_pizza": _d["gustos_pizza"],
        "gusto_extra": _d["precio_gusto_extra"](),
        "mesas": [{"numero": n, "mozo": mz or "", "comensales": c or 0,
                   "abierta": bool(a), "total": totales.get(n, 0) or 0,
                   "cuenta": bool(pc)}
                  for n, mz, c, a, pc in mesas],
        "productos": [{"id": pid, "nombre": nom,
                       "precio": pv(pre, pp, pd, ph),
                       # precio normal, solo si hay promo activa (se tacha)
                       "antes": pre if pv(pre, pp, pd, ph) != pre else None,
                       "categoria": cat,
                       "gustos": lleva(nom, cat),
                       "incluidos": incluidos(nom),
                       "agotado": bool(usar) and (stk or 0) <= 0,
                       "quedan": int(stk or 0) if usar else None}
                      for pid, nom, pre, cat, usar, stk, pp, pd, ph
                      in productos],
    }


def _mesa_detalle(numero):
    con = _d["db"]()
    fila = con.execute(
        "SELECT mozo, comensales, pide_cuenta FROM mesas WHERE numero=?",
        (numero,)).fetchone()
    if not fila:
        con.close()
        return None
    items = con.execute(
        "SELECT nombre, precio, cantidad, comensal FROM pedidos "
        "WHERE mesa=? ORDER BY comensal, id", (numero,)).fetchall()
    con.close()
    return {"numero": numero, "mozo": fila[0] or "",
            "comensales": fila[1] or 0, "cuenta": bool(fila[2]),
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
            gustos = it.get("gustos") or []
            assert 1 <= cant <= 99 and 0 <= comensal <= 30
            assert isinstance(gustos, list) and len(gustos) <= 10
            assert all(g in _d["gustos_pizza"] for g in gustos)
            pedido.append((pid, cant, comensal, gustos))
    except (KeyError, ValueError, TypeError, AssertionError):
        return 400, {"error": "Pedido mal formado."}

    con = _d["db"]()
    try:
        con.execute("BEGIN IMMEDIATE")
        fila = con.execute("SELECT mozo FROM mesas WHERE numero=?",
                           (mesa,)).fetchone()
        if not fila:
            con.rollback()
            return 404, {"error": f"La mesa {mesa} no existe."}
        mozo_actual = (fila[0] or "").strip()
        if not mozo:
            con.rollback()
            return 400, {"error": "Falta el nombre del mozo/a."}

        faltas, filas = [], []
        for pid, cant, comensal, gustos in pedido:
            prod = con.execute(
                "SELECT nombre, precio, categoria, usar_stock, stock, "
                "promo_precio, promo_desde, promo_hasta FROM productos "
                "WHERE id=?", (pid,)).fetchone()
            if not prod:
                con.rollback()
                return 400, {"error": "Hay un producto que ya no existe; "
                                      "actualizá la carta en el celular."}
            nombre, precio, cat, usar_stock, stock, pp, pdesde, phasta = prod
            precio = _d["precio_vigente"](precio, pp, pdesde, phasta)
            if gustos and _d["lleva_gustos"](nombre, cat):
                # los gustos que exceden lo incluido se cobran como extra
                de_mas = max(0, len(gustos) - _d["gustos_incluidos"](nombre))
                precio += de_mas * _d["precio_gusto_extra"]()
                nombre += " (" + ", ".join(gustos) + ")"
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
                               max(c for _, _, c, _ in pedido))
        if mozo_actual:
            # la mesa ya tiene mozo: otro puede agregar ítems, pero la
            # mesa sigue siendo de quien la abrió
            con.execute("UPDATE mesas SET comensales=?, abierta=1 "
                        "WHERE numero=?", (comensales_final, mesa))
        else:
            con.execute("UPDATE mesas SET mozo=?, comensales=?, abierta=1 "
                        "WHERE numero=?", (mozo, comensales_final, mesa))
        total = con.execute(
            "SELECT COALESCE(SUM(precio*cantidad),0) FROM pedidos "
            "WHERE mesa=?", (mesa,)).fetchone()[0]
        con.commit()
    finally:
        con.close()

    _imprimir_comanda(mesa, mozo_actual or mozo,
                      [(cant, nombre, comensal)
                       for _, nombre, _, cant, comensal, _ in filas])
    _avisar("pedido", mesa)
    return 200, {"ok": True, "total": total}


def _pedir_cuenta(datos):
    """El mozo avisa desde el celular que la mesa quiere la cuenta
    (o anula el aviso). Devuelve (código_http, respuesta_json)."""
    try:
        mesa = int(datos["mesa"])
        pedir = 1 if datos.get("pedir", True) else 0
    except (KeyError, ValueError, TypeError):
        return 400, {"error": "Pedido mal formado."}
    con = _d["db"]()
    try:
        if not con.execute("SELECT 1 FROM mesas WHERE numero=?",
                           (mesa,)).fetchone():
            return 404, {"error": f"La mesa {mesa} no existe."}
        if pedir and not con.execute(
                "SELECT 1 FROM pedidos WHERE mesa=? LIMIT 1",
                (mesa,)).fetchone():
            return 400, {"error": "La mesa no tiene pedidos."}
        con.execute("UPDATE mesas SET pide_cuenta=? WHERE numero=?",
                    (pedir, mesa))
        con.commit()
    finally:
        con.close()
    if pedir:
        _avisar("cuenta", mesa)
    return 200, {"ok": True, "cuenta": bool(pedir)}


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
            ruta = urlparse(self.path).path
            if ruta not in ("/api/pedido", "/api/cuenta"):
                self._json({"error": "No existe."}, 404)
                return
            try:
                largo = int(self.headers.get("Content-Length", 0))
                datos = json.loads(self.rfile.read(largo).decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "Pedido mal formado."}, 400)
                return
            if ruta == "/api/pedido":
                codigo, respuesta = _recibir_pedido(datos)
            else:
                codigo, respuesta = _pedir_cuenta(datos)
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
  #btnMozo{background:rgba(255,255,255,.15); border:0; color:#fff;
           font-size:.85rem; padding:7px 12px; border-radius:999px;
           font-family:inherit; white-space:nowrap}
  #cajaLogin{margin-top:12vh; text-align:center}
  #cajaLogin p{color:var(--suave); font-size:.85rem; margin-top:10px}
  #btnEntrar{width:100%; margin-top:12px; border:0; background:var(--bordo);
             color:#fff; border-radius:10px; padding:14px; font-size:1.05rem;
             font-weight:700; font-family:inherit}
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
  #btnCuenta{width:100%; margin-top:10px; border:1px solid var(--naranja);
             background:#fff; color:var(--naranja); border-radius:10px;
             padding:12px; font-size:.95rem; font-weight:700;
             font-family:inherit}
  #btnCuenta.pedida{background:var(--verde); border-color:var(--verde);
                    color:#fff}
  .mesa .cuenta{font-size:.78rem; font-weight:700; background:#fff;
                color:var(--rojo); border-radius:999px; padding:2px 8px;
                margin-top:4px; display:inline-block}
  /* --- catálogo --- */
  #chips,#chipsPara{display:flex; gap:8px; overflow-x:auto; padding:2px 0 10px}
  #chipsPara{padding-bottom:2px}
  #chipsPara .activa{background:var(--naranja); border-color:var(--naranja)}
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
  .prod .antes{color:var(--suave); font-size:.78rem;
               text-decoration:line-through}
  .prod .promo{background:var(--naranja); color:#fff; border-radius:6px;
               font-size:.68rem; font-weight:700; padding:2px 6px}
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
  /* --- panel de gustos (pizzetas) --- */
  #velo{position:fixed; inset:0; background:rgba(0,0,0,.45); display:none;
        z-index:40; align-items:flex-end; justify-content:center}
  #velo .modal{background:var(--panel); border-radius:16px 16px 0 0;
               padding:16px 16px calc(16px + env(safe-area-inset-bottom));
               width:100%; max-width:480px; max-height:80vh; overflow-y:auto}
  #gTitulo{color:var(--bordo); font-size:1.05rem; margin-bottom:2px}
  #gNota{color:var(--suave); font-size:.85rem; margin-bottom:6px}
  .gusto{display:flex; align-items:center; gap:12px; padding:11px 4px;
         border-bottom:1px dashed var(--borde); font-size:1rem}
  .gusto:last-child{border-bottom:0}
  .gusto input{width:22px; height:22px; margin:0; accent-color:var(--bordo)}
  #gBotones{display:flex; gap:8px; margin-top:12px}
  #gBotones button{flex:1; padding:13px; border-radius:10px; border:0;
                   font-weight:700; font-family:inherit; font-size:1rem}
  #gCancelar{background:var(--crema); color:var(--texto)}
  #gAgregar{background:var(--bordo); color:#fff}
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
  <button id="btnMozo" hidden></button>
</header>
<div id="banner">Sin conexión con la PC del restaurante…</div>

<main id="vLogin" hidden>
  <div class="tarjeta" id="cajaLogin">
    <h2 style="margin-top:0">¿Quién atiende?</h2>
    <input id="inNombre" placeholder="Tu nombre" autocomplete="off"
           maxlength="40">
    <button id="btnEntrar">ENTRAR</button>
    <p>Tu nombre queda en las mesas que abras y en las comandas de cocina.</p>
  </div>
</main>

<main id="vMesas" hidden>
  <div id="grillaMesas"></div>
</main>

<main id="vMesa" hidden>
  <div class="tarjeta">
    <div id="lblMozoMesa" style="margin-bottom:8px"></div>
    <div class="filaCampos">
      <label style="flex:0 0 130px">Comensales
        <input id="inCom" type="number" min="1" max="30" inputmode="numeric">
      </label>
    </div>
  </div>
  <div class="tarjeta" id="tYaPedido" hidden>
    <h2 style="margin-top:0">Ya en la mesa</h2>
    <div id="yaPedido"></div>
    <div class="totalMesa" id="totalMesa"></div>
    <button id="btnCuenta"></button>
  </div>
  <div class="tarjeta">
    <h2 style="margin-top:0">Cargar el pedido para</h2>
    <div id="chipsPara"></div>
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
<div id="velo">
  <div class="modal">
    <h3 id="gTitulo"></h3>
    <div id="gNota"></div>
    <div id="gLista"></div>
    <div id="gBotones">
      <button id="gCancelar">Cancelar</button>
      <button id="gAgregar">AGREGAR</button>
    </div>
  </div>
</div>
<div id="toast"></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
let estado = null, mesa = null, detalle = null;
let carrito = [], cat = "Todas", filtro = "", vista = "mesas";
let paraQuien = 0;  // 0 = toda la mesa, 1..N = comensal elegido
/* cada vez que se abre la comandera hay que poner el nombre
   (sessionStorage dura solo mientras la app está abierta) */
let mozo = sessionStorage.mozo || "";

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
  else if (vista === "login") $("titulo").textContent = estado.nombre;
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
    if (m.cuenta) b.appendChild(el("div", "cuenta", "🧾 pidió la cuenta"));
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
  mesa = n; carrito = []; cat = "Todas"; filtro = ""; paraQuien = 0;
  $("inBuscar").value = "";
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
  $("vLogin").hidden = vista !== "login";
  $("vMesas").hidden = vista !== "mesas";
  $("vMesa").hidden = vista !== "mesa";
  $("btnVolver").hidden = vista !== "mesa";
  $("btnMozo").hidden = vista === "login" || !mozo;
  $("btnMozo").textContent = "👤 " + mozo;
  if (vista === "login") {
    $("titulo").textContent = estado ? estado.nombre : "Comandera";
    $("barra").style.display = "none";
    return;
  }
  if (vista === "mesas") {
    if (estado) renderMesas();
    $("barra").style.display = "none";
    return;
  }
  $("titulo").textContent = "Mesa " + mesa;
  $("lblMozoMesa").textContent = detalle.mozo
    ? "Mozo/a de la mesa: " + detalle.mozo
    : "Mesa sin mozo — la abrís vos: " + mozo;
  renderYaPedido();
  renderPara();
  renderChips();
  renderProductos();
  renderBarra();
}

async function entrar() {
  const nombre = $("inNombre").value.trim();
  if (!nombre) { $("inNombre").focus(); return; }
  mozo = nombre;
  sessionStorage.mozo = nombre;
  localStorage.mozo = nombre;  // solo para sugerirlo la próxima vez
  vista = "mesas";
  render();
  try {
    await cargarEstado();
    if (mesaInicial) await abrirMesa(mesaInicial);
  } catch (e) {}
}

function salir() {
  mozo = ""; mesa = null; carrito = [];
  sessionStorage.removeItem("mozo");
  $("inNombre").value = localStorage.mozo || "";
  vista = "login";
  render();
}

function renderPara() {
  const caja = $("chipsPara");
  caja.replaceChildren();
  const nCom = Math.max(1, parseInt($("inCom").value) || 1);
  if (paraQuien > nCom) paraQuien = 0;
  const opciones = [["Toda la mesa", 0]];
  for (let c = 1; c <= nCom; c++) opciones.push(["Comensal " + c, c]);
  for (const [texto, valor] of opciones) {
    const b = el("button", "chip" + (valor === paraQuien ? " activa" : ""),
                 texto);
    b.onclick = () => { paraQuien = valor; renderPara(); };
    caja.appendChild(b);
  }
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
  const btn = $("btnCuenta");
  btn.className = detalle.cuenta ? "pedida" : "";
  btn.textContent = detalle.cuenta
    ? "✔ Cuenta pedida — tocá para anular el aviso"
    : "🧾 Pedir la cuenta";
}

async function pedirCuenta() {
  try {
    const r = await api("/api/cuenta", {mesa: mesa, pedir: !detalle.cuenta});
    detalle.cuenta = r.cuenta;
    toast(detalle.cuenta ? "Le avisamos a la caja 🧾" : "Aviso anulado");
    renderYaPedido();
  } catch (e) { alert(e.message); }
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
    if (p.antes) {
      b.appendChild(el("span", "promo", "PROMO"));
      b.appendChild(el("span", "antes", fmt(p.antes)));
    }
    b.appendChild(el("span", "precio", fmt(p.precio)));
    b.appendChild(el("span", "mas", "+"));
    if (!p.agotado) b.onclick = () => agregar(p);
    caja.appendChild(b);
  }
  if (!hay) caja.appendChild(el("div", "vacio", "No hay productos que coincidan."));
}

/* ---------------- carrito ---------------- */

function agregar(p) {
  if (p.gustos) { abrirGustos(p); return; }  // pizzetas: elegir gustos
  alCarrito(p, []);
}

function alCarrito(p, gustos) {
  const clave = gustos.join(",");
  const existente = carrito.find(
    i => i.id === p.id && i.comensal === paraQuien &&
         (i.gustos || []).join(",") === clave);
  if (existente) existente.cantidad = Math.min(existente.cantidad + 1, 99);
  else {
    const extras = Math.max(0, gustos.length - (p.incluidos || 0));
    carrito.push({
      id: p.id,
      nombre: p.nombre + (gustos.length ? " (" + gustos.join(", ") + ")" : ""),
      precio: p.precio + extras * (estado.gusto_extra || 0),
      cantidad: 1, comensal: paraQuien, gustos: gustos});
  }
  renderProductos();
  renderBarra();
}

/* ---------------- gustos de pizzetas ---------------- */

let gustoProd = null;

function abrirGustos(p) {
  gustoProd = p;
  $("gTitulo").textContent = p.nombre;
  $("gNota").textContent = p.incluidos
    ? "Incluye " + p.incluidos + " gusto; cada gusto de más suma "
      + fmt(estado.gusto_extra || 0) + "."
    : "Cada gusto suma " + fmt(estado.gusto_extra || 0) + ".";
  const caja = $("gLista");
  caja.replaceChildren();
  for (const g of estado.gustos_pizza || []) {
    const fila = el("label", "gusto");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = g;
    fila.appendChild(cb);
    fila.appendChild(el("span", "", g));
    caja.appendChild(fila);
  }
  $("velo").style.display = "flex";
}

$("gCancelar").onclick = () => { $("velo").style.display = "none"; };
$("gAgregar").onclick = () => {
  const gustos = [...$("gLista").querySelectorAll("input:checked")]
    .map(c => c.value);
  $("velo").style.display = "none";
  if (gustoProd) alCarrito(gustoProd, gustos);
};

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
      mozo: mozo,
      comensales: parseInt($("inCom").value) || 1,
      items: carrito.map(i => ({id: i.id, cantidad: i.cantidad,
                                comensal: i.comensal,
                                gustos: i.gustos || []}))
    });
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
$("btnMozo").onclick = salir;
$("btnEntrar").onclick = entrar;
$("inNombre").onkeydown = e => { if (e.key === "Enter") entrar(); };
$("btnEnviar").onclick = enviar;
$("btnCuenta").onclick = pedirCuenta;
$("btnCarrito").onclick = () => {
  const c = $("carrito");
  c.style.display = c.style.display === "block" ? "none" : "block";
};
$("inBuscar").oninput = e => { filtro = e.target.value; renderProductos(); };
$("inCom").onchange = () => { renderPara(); renderCarrito(); };

setInterval(() => {
  if (vista === "mesas") cargarEstado().catch(() => {});
}, 8000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && vista === "mesas") cargarEstado().catch(() => {});
});

/* si la dirección trae ?mesa=N se abre esa mesa directo apenas
   el mozo pone su nombre (sirve para pegar un QR en cada mesa) */
const mesaInicial = parseInt(
  new URLSearchParams(location.search).get("mesa"));
$("inNombre").value = localStorage.mozo || "";
if (!mozo) {
  vista = "login";
  render();
  cargarEstado().catch(e => { $("banner").style.display = "block"; });
} else {
  render();
  cargarEstado()
    .then(() => { if (mesaInicial) return abrirMesa(mesaInicial); })
    .catch(e => { $("banner").style.display = "block"; });
}
</script>
</body>
</html>
"""
