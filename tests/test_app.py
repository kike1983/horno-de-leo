"""Prueba automatizada v2: flujo completo con carta real, stock, medios de
pago, estadísticas, backup y generación ESC/POS."""
import os, sys, shutil, sqlite3, datetime

import tempfile
FAKEHOME = tempfile.mkdtemp(prefix="horno_test_")
shutil.rmtree(FAKEHOME, ignore_errors=True)
os.makedirs(FAKEHOME)
os.environ["HOME"] = FAKEHOME

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import restaurante as r
r.APP_DIR = os.path.join(FAKEHOME, ".restaurante_armenio")
r.DB_PATH = os.path.join(r.APP_DIR, "restaurante.db")
r.RECIBOS_DIR = os.path.join(r.APP_DIR, "recibos")
r.BACKUPS_DIR = os.path.join(r.APP_DIR, "backups")

r.init_db()

# --- carta real cargada
con = r.db()
n_prod = con.execute("SELECT COUNT(*) FROM productos").fetchone()[0]
shawarma = con.execute("SELECT precio, categoria FROM productos WHERE nombre='Shawarma de Pollo'").fetchone()
baklava = con.execute("SELECT precio, categoria FROM productos WHERE nombre='Baklava'").fetchone()
con.close()
assert n_prod == len(r.CARTA_HORNO_DE_LEO) == 40, n_prod
assert shawarma == (490, "Armenios") and baklava == (190, "Postre")
assert r.cfg_get("nombre") == "El Horno de Leo"
print(f"OK carta: {n_prod} productos reales cargados")

# --- backup automático
b = r.backup_auto()
assert b and os.path.exists(b)
print("OK backup automático:", os.path.basename(b))

app = r.App()
app.update()
r.messagebox.showinfo = lambda *a, **k: None
r.messagebox.showwarning = lambda *a, **k: None
r.messagebox.askyesno = lambda *a, **k: True

# --- stock: activar control en Baklava con stock 2, mínimo 1
con = r.db()
pid = con.execute("SELECT id FROM productos WHERE nombre='Baklava'").fetchone()[0]
con.execute("UPDATE productos SET usar_stock=1, stock=2, stock_min=1 WHERE id=?", (pid,))
con.commit(); con.close()

win = r.MesaWindow(app, 1)
win.var_mozo.set("Leo")
win.var_comensales.set(2)
win._comensales_cambiados()

def pedir(nombre_prod, comensal_idx, cant=1):
    for iid in win.tree_prod.get_children():
        if nombre_prod in win.tree_prod.item(iid, "text"):
            win.tree_prod.selection_set(iid)
            break
    win.var_cant.set(cant)
    win.cb_comensal.current(comensal_idx)
    win._agregar()

pedir("Shawarma de Pollo", 1, 1)   # 490 comensal 1
pedir("Baklava", 2, 2)             # 380 comensal 2 (agota stock)
pedir("Refresco 600 ml", 0, 2)     # 320 compartido

con = r.db()
assert con.execute("SELECT stock FROM productos WHERE id=?", (pid,)).fetchone()[0] == 0
con.close()
print("OK stock descontado al pedir (Baklava 2 -> 0)")

# pedir otra baklava debe bloquearse (sin stock)
errores = []
r.messagebox.showerror = lambda t, m, **k: errores.append(m)
pedir("Baklava", 2, 1)
assert errores and "stock" in errores[0].lower(), errores
con = r.db()
assert con.execute("SELECT COUNT(*) FROM pedidos WHERE mesa=1").fetchone()[0] == 3
con.close()
print("OK bloqueo por falta de stock:", errores[0])

# quitar la baklava devuelve stock
iid_baklava = [i for i in win.tree_pedido.get_children()
               if "Baklava" in win.tree_pedido.item(i, "values")[1]][0]
win.tree_pedido.selection_set(iid_baklava)
win._quitar()
con = r.db()
assert con.execute("SELECT stock FROM productos WHERE id=?", (pid,)).fetchone()[0] == 2
con.close()
print("OK stock devuelto al quitar ítem")
pedir("Baklava", 2, 2)  # la vuelvo a cargar

total_esperado = 490 + 380 + 320
assert f"Total: {r.fmt(total_esperado)}" == win.lbl_total.cget("text")

# --- faltantes detectados (stock 0 <= min 1)
assert app._faltantes() and app._faltantes()[0][0] == "Baklava"
print("OK aviso de faltantes:", app._faltantes())

# --- cobro por comensal con MercadoPago
dlg = r.tk.Toplevel(win)
win._confirmar_cobro(dlg, "comensal", imprimir=False, medio="MercadoPago")
con = r.db()
venta = con.execute("SELECT mesa, mozo, total, modo, medio FROM ventas").fetchone()
assert venta == (1, "Leo", total_esperado, "comensal", "MercadoPago"), venta
items = con.execute("SELECT nombre, cantidad, subtotal FROM venta_items ORDER BY nombre").fetchall()
assert ("Baklava", 2, 380.0) in items and len(items) == 3, items
con.close()
print("OK venta con medio de pago y detalle de items:", items)

# --- recibos con medio de pago
recibos = sorted(os.listdir(r.RECIBOS_DIR))
texto = open(os.path.join(r.RECIBOS_DIR, recibos[0]), encoding="utf-8").read()
assert "MercadoPago" in texto and "Cocina Armenia" in texto
print("OK recibo con eslogan y medio de pago")

# --- ESC/POS: bytes correctos
data = r._escpos_bytes("Prueba áéíóú ñ")
assert data.startswith(b"\x1b\x40\x1b\x74\x02") and data.endswith(b"\x1d\x56\x42\x00")
assert "Prueba".encode() in data
assert b"\x1b\x21\x10" in data          # letra grande (doble alto) activada
r.cfg_set("imp_grande", "0")
assert b"\x1b\x21\x10" not in r._escpos_bytes("Prueba")
r.cfg_set("imp_grande", "1")
print("OK generación ESC/POS (init + CP850 + letra grande + corte)")

# --- estadísticas: datos y dibujo (tab 4 desde que existe Mostrador/Delivery)
app.nb.select(4)
app.update_idletasks(); app.update()
app._redibujar_graficos()
app.update()
assert len(app.cv_top.find_all()) > 5, "el gráfico de top productos no dibujó nada"
assert len(app.cv_dias.find_all()) > 5, "el gráfico por día no dibujó nada"
print("OK gráficos dibujados:", len(app.cv_dias.find_all()), "y",
      len(app.cv_top.find_all()), "elementos")

# --- reporte con medio de pago
app.var_fecha.set(datetime.date.today().isoformat())
app._cargar_ventas()
assert "MercadoPago" in app.lbl_por_mozo.cget("text")
print("OK reporte:", app.lbl_resumen.cget("text"))

# --- recargar carta
app._recargar_carta()
con = r.db()
assert con.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 40
con.close()
print("OK recargar carta")

# --- cancelar mesa: libera sin registrar venta y devuelve stock
con = r.db()
pid2 = con.execute("SELECT id FROM productos WHERE nombre='Baklava'").fetchone()[0]
con.execute("UPDATE productos SET usar_stock=1, stock=5, stock_min=1 WHERE id=?",
            (pid2,))
ventas_antes = con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
con.commit(); con.close()

# dejar la mesa 2 "pidiendo la cuenta": abrirla debe apagar el aviso
con = r.db()
con.execute("UPDATE mesas SET pide_cuenta=1 WHERE numero=2")
con.commit(); con.close()

win2 = r.MesaWindow(app, 2)
win2.var_mozo.set("Caro")
win2.update()
con = r.db()
assert con.execute("SELECT pide_cuenta FROM mesas WHERE numero=2").fetchone()[0] == 0
con.close()
print("OK abrir la mesa apaga el aviso de 'pide la cuenta'")
for iid in win2.tree_prod.get_children():
    if "Baklava" in win2.tree_prod.item(iid, "text"):
        win2.tree_prod.selection_set(iid)
        break
win2.var_cant.set(3)
win2._agregar()
con = r.db()
assert con.execute("SELECT abierta FROM mesas WHERE numero=2").fetchone()[0] == 1
assert con.execute("SELECT stock FROM productos WHERE id=?", (pid2,)).fetchone()[0] == 2
con.close()

win2._cancelar_mesa()   # askyesno está simulado en True
con = r.db()
assert con.execute("SELECT COUNT(*) FROM pedidos WHERE mesa=2").fetchone()[0] == 0
assert con.execute("SELECT abierta, comensales, mozo FROM mesas "
                   "WHERE numero=2").fetchone() == (0, 0, "")
# la mesa 1 se cobró antes: también tiene que haber quedado sin mozo
assert con.execute("SELECT mozo FROM mesas WHERE numero=1").fetchone()[0] == ""
assert con.execute("SELECT stock FROM productos WHERE id=?", (pid2,)).fetchone()[0] == 5
assert con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == ventas_antes
con.close()
assert not win2.winfo_exists()
print("OK cancelar mesa: liberada sin venta y con stock devuelto")

# ================================================= v1.5: promociones

hoy = datetime.date.today()
ayer = (hoy - datetime.timedelta(days=1)).isoformat()
manana = (hoy + datetime.timedelta(days=1)).isoformat()

assert r.promo_vigente(100, "", "") is True
assert r.promo_vigente(100, ayer, manana) is True
assert r.promo_vigente(100, manana, "") is False        # todavía no empezó
assert r.promo_vigente(100, "", ayer) is False          # ya venció
assert r.promo_vigente(0, ayer, manana) is False        # sin precio promo
assert r.precio_vigente(490, 350, ayer, manana) == 350
assert r.precio_vigente(490, 350, "", ayer) == 490
print("OK helpers de promoción (vigencia por fechas)")

# promo activa en Shawarma de Pollo: la mesa tiene que cobrar 350
con = r.db()
con.execute("UPDATE productos SET promo_precio=350, promo_desde=?, "
            "promo_hasta=? WHERE nombre='Shawarma de Pollo'", (ayer, manana))
# promo vencida en Baklava: se sigue cobrando el precio normal
con.execute("UPDATE productos SET promo_precio=50, promo_desde='', "
            "promo_hasta=? WHERE nombre='Baklava'", (ayer,))
con.commit(); con.close()

win3 = r.MesaWindow(app, 2)
win3.var_mozo.set("Leo")
for nombre_prod in ("Shawarma de Pollo", "Baklava"):
    for iid in win3.tree_prod.get_children():
        if nombre_prod in win3.tree_prod.item(iid, "text"):
            win3.tree_prod.selection_set(iid)
            break
    win3.var_cant.set(1)
    win3._agregar()
con = r.db()
precios = dict(con.execute(
    "SELECT nombre, precio FROM pedidos WHERE mesa=2").fetchall())
con.close()
assert precios["Shawarma de Pollo"] == 350, precios
assert precios["Baklava"] == 190, precios
# en la lista, el producto en promo se marca
marcados = [win3.tree_prod.item(i, "text") for i in win3.tree_prod.get_children()
            if "PROMO" in win3.tree_prod.item(i, "text")]
assert any("Shawarma de Pollo" in t for t in marcados), marcados
assert not any("Baklava" in t for t in marcados), marcados
win3._cancelar_mesa()
print("OK promo: vigente cobra el precio promocional, vencida el normal")

# validación del formulario: promo mayor al precio se rechaza
app.var_p_nombre.set("Prueba")
app.var_p_precio.set("100")
app.var_p_stock.set("0"); app.var_p_stockmin.set("0")
app.var_p_promo.set("150")
app.var_p_pdesde.set(""); app.var_p_phasta.set("")
errores.clear()
assert app._leer_form_producto() is None and errores
app.var_p_promo.set("80")
app.var_p_phasta.set("31/12/2026")   # formato inválido
errores.clear()
assert app._leer_form_producto() is None and errores
app.var_p_phasta.set(manana)
datos = app._leer_form_producto()
assert datos and datos[6] == 80 and datos[8] == manana, datos
print("OK validación del formulario de promoción")

# ================================================= v1.5: mostrador y delivery

con = r.db()
con.execute("UPDATE productos SET usar_stock=1, stock=4 WHERE nombre='Baklava'")
ventas_antes = con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
con.commit(); con.close()

vd = r.VentaDirectaWindow(app, "mostrador")
vd.var_cliente.set("Anush")
for iid in vd.tree_prod.get_children():
    if "Baklava" in vd.tree_prod.item(iid, "text"):
        vd.tree_prod.selection_set(iid)
        break
vd.var_cant.set(2)
vd._agregar()
# el stock no se toca hasta cobrar
con = r.db()
assert con.execute("SELECT stock FROM productos WHERE nombre='Baklava'")\
    .fetchone()[0] == 4
con.close()
# pero la ventana descuenta lo ya cargado: pedir 3 más debe fallar (quedan 2)
errores.clear()
vd.var_cant.set(3)
for iid in vd.tree_prod.get_children():
    if "Baklava" in vd.tree_prod.item(iid, "text"):
        vd.tree_prod.selection_set(iid)
        break
vd._agregar()
assert errores and "stock" in errores[0].lower(), errores
assert vd._total() == 380, vd._total()

dlg = r.tk.Toplevel(vd)
vd._confirmar_cobro(dlg, imprimir=False, medio="Efectivo")
con = r.db()
venta = con.execute(
    "SELECT mesa, total, canal, cliente, medio FROM ventas "
    "WHERE canal='mostrador'").fetchone()
assert venta == (None, 380.0, "mostrador", "Anush", "Efectivo"), venta
assert con.execute("SELECT stock FROM productos WHERE nombre='Baklava'")\
    .fetchone()[0] == 2
con.close()
assert not vd.winfo_exists()
print("OK venta mostrador: registrada con canal propio y stock descontado")

vd2 = r.VentaDirectaWindow(app, "delivery")
vd2.var_cliente.set("Karen")
vd2.var_tel.set("099123456")
vd2.var_dir.set("Av. Italia 1234")
for iid in vd2.tree_prod.get_children():
    if "Shawarma de Pollo" in vd2.tree_prod.item(iid, "text"):
        vd2.tree_prod.selection_set(iid)
        break
vd2.var_cant.set(1)
vd2._agregar()
assert vd2._total() == 350  # promo vigente también en delivery
dlg = r.tk.Toplevel(vd2)
vd2._confirmar_cobro(dlg, imprimir=False, medio="MercadoPago")
con = r.db()
venta = con.execute("SELECT total, canal, cliente FROM ventas "
                    "WHERE canal='delivery'").fetchone()
assert venta == (350.0, "delivery", "Karen · 099123456 · Av. Italia 1234"), venta
con.close()
# el ticket guarda los datos de entrega
recibo_dv = sorted(f for f in os.listdir(r.RECIBOS_DIR)
                   if f.startswith("recibo_delivery"))[-1]
texto = open(os.path.join(r.RECIBOS_DIR, recibo_dv), encoding="utf-8").read()
assert "*** DELIVERY ***" in texto, texto
assert "Cliente: Karen" in texto, texto          # renglón propio, no en el título
assert "Entregar en: Av. Italia 1234" in texto, texto
assert "Celular: 099123456" in texto, texto
assert "DELIVERY — Karen" not in texto and "Mozo/a" not in texto, texto
print("OK venta delivery: cliente/tel/dirección en el registro y el ticket")

# la pestaña Mostrador/Delivery lista las ventas de hoy
app.refrescar_directas()
assert len(app.tree_directas.get_children()) == 2
assert "Mostrador: 1" in app.lbl_dir_resumen.cget("text")
assert "Delivery: 1" in app.lbl_dir_resumen.cget("text")
print("OK pestaña Mostrador/Delivery:", app.lbl_dir_resumen.cget("text"))

# reporte con filtro por canal
app.var_fecha.set(datetime.date.today().isoformat())
app.var_canal_rep.set("Todos")
app._cargar_ventas()
assert "Por canal:" in app.lbl_por_mozo.cget("text")
todas = len(app.tree_ventas.get_children())
app.var_canal_rep.set("Delivery")
app._cargar_ventas()
solo_delivery = app.tree_ventas.get_children()
assert len(solo_delivery) == 1 and todas > 1
fila = app.tree_ventas.item(solo_delivery[0], "values")
assert fila[1] == "Delivery" and "Karen" in fila[2], fila
app.var_canal_rep.set("Todos")
print("OK reporte de ventas con filtro por canal")

# ================================================= v1.7: agenda de clientes

assert r.tel_normalizado("099 123-456") == "099123456"
assert r.cliente_buscar("12345") is None          # muy corto
assert r.cliente_buscar("099123456") is not None  # lo creó la venta delivery
nombre_g, dir_g, pedidos_g, ultimo_g = r.cliente_buscar("099 123 456")
assert nombre_g == "Karen" and dir_g == "Av. Italia 1234" and pedidos_g == 1
assert ultimo_g[:4].isdigit()
print("OK agenda: el delivery cobrado guardó al cliente solo")

# segunda venta del mismo celular: suma pedidos y actualiza datos no vacíos
r.cliente_guardar("099123456", "", "Av. Italia 1234 apto 2")
nombre_g, dir_g, pedidos_g, _ = r.cliente_buscar("099123456")
assert nombre_g == "Karen"                       # el vacío no pisa
assert dir_g == "Av. Italia 1234 apto 2"
assert pedidos_g == 2
print("OK agenda: suma pedidos y un dato vacío no pisa el guardado")

# autocompletado: al escribir el celular en una venta delivery nueva
vd3 = r.VentaDirectaWindow(app, "delivery")
vd3.var_tel.set("099123456")   # dispara el trace
vd3.update()
assert vd3.var_cliente.get() == "Karen"
assert vd3.var_dir.get() == "Av. Italia 1234 apto 2"
assert "2 pedido" in vd3.lbl_cli_info.cget("text")
# lo escrito a mano no se pisa al re-autocompletar
vd3.var_cliente.set("Karen Sarkissian")
vd3.var_tel.set("099123456 ")  # re-dispara con el mismo cliente
assert vd3.var_cliente.get() == "Karen Sarkissian"
vd3.destroy()
print("OK agenda: autocompleta nombre y dirección al escribir el celular")

# ventana de agenda: buscar, guardar cambios y eliminar
ag = r.AgendaClientesWindow(app)
ag.update()
assert len(ag.tree.get_children()) == 1
ag.var_buscar.set("karen")
assert len(ag.tree.get_children()) == 1
ag.var_buscar.set("noexiste")
assert len(ag.tree.get_children()) == 0
ag.var_buscar.set("")
ag.var_tel.set("098 765 432")
ag.var_nombre.set("Vartan")
ag.var_dir.set("Rivera 456")
ag._guardar()
assert r.cliente_buscar("098765432")[0] == "Vartan"
ag.tree.selection_set("098765432")
ag._eliminar()   # askyesno simulado en True
assert r.cliente_buscar("098765432") is None
ag.destroy()
print("OK agenda: ventana con búsqueda, alta manual y eliminación")

# ================================================= v1.9: categorías y gustos

# migración: productos que quedaron en la vieja categoría "Menú"
con = r.db()
con.execute("UPDATE productos SET categoria='Menú' WHERE nombre LIKE 'Pizzeta%'")
con.execute("UPDATE productos SET categoria='Menú' WHERE nombre LIKE 'Milanesa%'")
con.execute("UPDATE productos SET categoria='Menú' WHERE nombre LIKE 'Shawarma%'")
con.execute("INSERT INTO productos(nombre, precio, categoria) "
            "VALUES ('Tarta casera', 300, 'Menú')")  # agregado a mano
con.commit(); con.close()
r.init_db()  # la migración corre al abrir el programa
con = r.db()
cats = dict(con.execute("SELECT nombre, categoria FROM productos WHERE nombre "
                        "IN ('Pizzeta c/Muzza','Milanesa al Pan c/Fritas',"
                        "'Shawarma Vegano','Tarta casera')"))
n_menu = con.execute("SELECT COUNT(*) FROM productos "
                     "WHERE categoria='Menú'").fetchone()[0]
con.execute("DELETE FROM productos WHERE nombre='Tarta casera'")
con.commit(); con.close()
assert cats == {"Pizzeta c/Muzza": "Pizzería",
                "Milanesa al Pan c/Fritas": "Minutas",
                "Shawarma Vegano": "Armenios",
                "Tarta casera": "Minutas"}, cats
assert n_menu == 0
print("OK migración: Menú separado en Armenios / Minutas / Pizzería")

# gustos: reglas y cobro
assert r.lleva_gustos("Pizzeta 1 Gusto", "Pizzería") is True
assert r.lleva_gustos("Tere c/Muzza", "Pizzería") is True
assert r.lleva_gustos("Gusto Extra (pizzeta)", "Pizzería") is False
assert r.lleva_gustos("Papas Fritas", "Minutas") is False
assert r.gustos_incluidos("Pizzeta 1 Gusto") == 1
assert r.gustos_incluidos("Pizzeta c/Muzza") == 0
assert r.precio_gusto_extra() == 90
nom, pre = r.aplicar_gustos("Pizzeta 1 Gusto", 500, ["Roquefort"])
assert (nom, pre) == ("Pizzeta 1 Gusto (Roquefort)", 500)   # 1 incluido
nom, pre = r.aplicar_gustos("Pizzeta 1 Gusto", 500,
                            ["Roquefort", "Panceta", "Rúcula"])
assert (nom, pre) == ("Pizzeta 1 Gusto (Roquefort, Panceta, Rúcula)", 680)
nom, pre = r.aplicar_gustos("Pizzeta c/Muzza", 450, ["Cheddar"])
assert (nom, pre) == ("Pizzeta c/Muzza (Cheddar)", 540)     # sin incluidos
nom, pre = r.aplicar_gustos("Tere c/Muzza", 550, [])
assert (nom, pre) == ("Tere c/Muzza", 550)
nom, pre = r.aplicar_gustos("Pizzeta c/Muzza", 450, ["Inventado"])
assert (nom, pre) == ("Pizzeta c/Muzza", 450)               # gusto inválido
print("OK gustos: incluidos, extras cobrados y validación")

# en una venta de mostrador, la pizzeta abre el diálogo de gustos
# (simulado: el operador marca Panceta y Cheddar)
elegir_original = r.elegir_gustos
r.elegir_gustos = lambda parent, nombre: ["Panceta", "Cheddar"]
vg = r.VentaDirectaWindow(app, "mostrador")
for iid in vg.tree_prod.get_children():
    if "Pizzeta c/Muzza" in vg.tree_prod.item(iid, "text"):
        vg.tree_prod.selection_set(iid)
        break
vg.var_cant.set(1)
vg._agregar()
assert vg.items[0][1] == "Pizzeta c/Muzza (Panceta, Cheddar)", vg.items
assert vg.items[0][2] == 630, vg.items   # 450 + 2 gustos de 90
# cancelar el diálogo no agrega nada
r.elegir_gustos = lambda parent, nombre: None
vg._agregar()
assert len(vg.items) == 1
r.elegir_gustos = elegir_original
vg.destroy()
print("OK gustos en venta de mostrador: nombre anotado y gustos cobrados")

# migración v1.9.1: "Pizzeta 1 Gusto" y "Gusto Extra" desaparecen de la
# carta y el precio que tenía el Gusto Extra queda como precio del gusto
con = r.db()
assert con.execute("SELECT COUNT(*) FROM productos WHERE nombre IN "
                   "('Pizzeta 1 Gusto','Gusto Extra (pizzeta)')")\
    .fetchone()[0] == 0
con.execute("INSERT INTO productos(nombre, precio, categoria) VALUES "
            "('Pizzeta 1 Gusto', 500, 'Pizzería'), "
            "('Gusto Extra (pizzeta)', 95, 'Pizzería')")
con.execute("DELETE FROM config WHERE clave='mig_pizzeta'")
con.commit(); con.close()
r.init_db()
con = r.db()
assert con.execute("SELECT COUNT(*) FROM productos WHERE nombre IN "
                   "('Pizzeta 1 Gusto','Gusto Extra (pizzeta)')")\
    .fetchone()[0] == 0
con.close()
assert r.precio_gusto_extra() == 95   # conservó el precio del producto viejo
r.cfg_set("precio_gusto", "90")
print("OK migración pizzería: productos viejos afuera, precio conservado")

# ================================================= v2.0: carta digital y QR

# QR: idéntico a la matriz verificada con segno y el lector de OpenCV
esperado = open(os.path.join(os.path.dirname(__file__),
                             "qr_hola_esperado.txt")).read().split()
m = r.matriz_qr("hola")
assert ["".join(str(b) for b in fila) for fila in m] == esperado
assert len(r.matriz_qr("x" * 42)) == 29     # versión 3, justo el límite
try:
    r.matriz_qr("x" * 43)
    assert False, "debió rechazar 43 caracteres"
except ValueError:
    pass
assert "<svg" in r.svg_qr("hola") and "<rect" in r.svg_qr("hola")
assert len(r.URL_CARTA) <= 42               # el QR de las mesas tiene que entrar
print("OK QR propio: matriz verificada, límites y SVG")

# carta digital: datos vivos, promos y secciones
con = r.db()
con.execute("UPDATE productos SET promo_precio=350, promo_desde='', "
            "promo_hasta='' WHERE nombre='Shawarma de Pollo'")
con.commit(); con.close()
carta = r.generar_carta_html()
assert "Shawarma de Pollo" in carta and "Baklava" in carta
assert "PROMO" in carta and "350" in carta       # promo activa visible
assert "Platos Armenios" in carta and "Pizzería" in carta
assert "Cervezas" in carta and "Vinos" in carta  # subgrupos de bebidas
assert "Rúcula" in carta                          # gustos de pizzería
assert "data:image/png;base64" in carta           # logo incrustado
con = r.db()
con.execute("UPDATE productos SET promo_precio=0 "
            "WHERE nombre='Shawarma de Pollo'")
con.commit(); con.close()
assert "PROMO" not in r.generar_carta_html()      # sin promo, sin insignia
print("OK carta digital: productos, promos, secciones y logo")

# cartelitos: uno por mesa, con el QR a la dirección pública
hoja = r.generar_cartelitos_html(3)
assert hoja.count("MIRÁ NUESTRA CARTA") == 3
assert "MESA 3" in hoja and "<svg" in hoja
print("OK cartelitos QR imprimibles")

# publicar sin código configurado avisa en vez de explotar
r.cfg_set("gh_token", "")
assert "publicación" in r.publicar_carta()
print("OK publicar sin token devuelve aviso claro")

# acceso directo: en Linux no hace nada y no rompe
assert r.asegurar_acceso_directo() is None
print("OK asegurar_acceso_directo es inofensivo fuera de Windows")

# ================================================= v1.6: actualizador

import json, threading, functools
import http.server

assert r._numeros_version("1.5.1") == (1, 5, 1)
assert r._numeros_version("1.10") > r._numeros_version("1.9")
assert r._numeros_version("basura") == (0,)

SRV_DIR = tempfile.mkdtemp(prefix="horno_update_")
def publicar(version, cuerpo_py="VERSION = 'nuevo'\n"):
    with open(os.path.join(SRV_DIR, "version.json"), "w", encoding="utf-8") as f:
        json.dump({"version": version, "archivos": ["restaurante.py"],
                   "novedades": "prueba"}, f)
    with open(os.path.join(SRV_DIR, "restaurante.py"), "w", encoding="utf-8") as f:
        f.write(cuerpo_py)

handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                            directory=SRV_DIR)
httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
r.cfg_set("update_url", f"http://127.0.0.1:{httpd.server_address[1]}")

# misma versión (o más vieja): no ofrece nada
publicar(r.VERSION)
assert r.consultar_actualizacion() is None
publicar("0.1")
assert r.consultar_actualizacion() is None

# versión más nueva: se descarga y reemplaza (con respaldo .anterior)
publicar("99.0")
info = r.consultar_actualizacion()
assert info and info["version"] == "99.0" and info["novedades"] == "prueba"
destino = tempfile.mkdtemp(prefix="horno_destino_")
with open(os.path.join(destino, "restaurante.py"), "w") as f:
    f.write("viejo")
r.descargar_actualizacion(info, carpeta=destino)
assert open(os.path.join(destino, "restaurante.py")).read() == "VERSION = 'nuevo'\n"
assert open(os.path.join(destino, "restaurante.py.anterior")).read() == "viejo"
print("OK actualizador: detecta versión nueva, descarga y respalda")

# un .py que no compila se rechaza y no toca nada
publicar("100.0", cuerpo_py="def roto(:\n")
info = r.consultar_actualizacion()
fallo = False
try:
    r.descargar_actualizacion(info, carpeta=destino)
except SyntaxError:
    fallo = True
assert fallo
assert open(os.path.join(destino, "restaurante.py")).read() == "VERSION = 'nuevo'\n"
print("OK actualizador: una descarga rota no pisa el programa")

# nombres con ruta se ignoran (seguridad) => sin archivos válidos, error
fallo = False
try:
    r.descargar_actualizacion({"version": "101", "archivos": ["../pwn.py"]},
                              carpeta=destino)
except ValueError:
    fallo = True
assert fallo and not os.path.exists(os.path.join(destino, "pwn.py"))
print("OK actualizador: rechaza rutas fuera de la carpeta del programa")

httpd.shutdown()

# con la config vacía o rota, manda la dirección grabada en el código
# (así nadie puede dejar al local sin actualizaciones por accidente)
r.cfg_set("update_url", "")
assert r.url_actualizaciones() == r.URL_ACTUALIZACIONES
r.cfg_set("update_url", "   ")
assert r.url_actualizaciones() == r.URL_ACTUALIZACIONES
print("OK actualizador: la URL vacía cae a la dirección del código")

app.destroy()
print("\nTODAS LAS PRUEBAS PASARON")
