"""Prueba automatizada de la comandera web: estado, detalle de mesa,
pedido desde el celular (con stock y comanda), y rechazos por falta de
stock o pedido mal formado. No necesita entorno gráfico."""
import os, sys, json, glob, shutil, tempfile
import urllib.request
import urllib.error

FAKEHOME = tempfile.mkdtemp(prefix="horno_test_com_")
os.environ["HOME"] = FAKEHOME

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import restaurante as r
import comandera
r.APP_DIR = os.path.join(FAKEHOME, ".restaurante_armenio")
r.DB_PATH = os.path.join(r.APP_DIR, "restaurante.db")
r.RECIBOS_DIR = os.path.join(r.APP_DIR, "recibos")
r.BACKUPS_DIR = os.path.join(r.APP_DIR, "backups")

r.init_db()
r.cfg_set("imp_modo", "dispositivo")      # dispositivo inexistente:
r.cfg_set("imp_dev", "/no/existe/lp0")    # la comanda queda solo en archivo

PUERTO = 8799
srv, url = comandera.iniciar(r.deps_comandera(), PUERTO)
BASE = f"http://127.0.0.1:{PUERTO}"
print("OK servidor iniciado:", url)


def GET(ruta):
    with urllib.request.urlopen(BASE + ruta, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def POST(ruta, datos):
    req = urllib.request.Request(
        BASE + ruta, data=json.dumps(datos).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


# --- la página y el manifiesto responden
with urllib.request.urlopen(BASE + "/", timeout=5) as resp:
    assert resp.status == 200 and b"Comandera" in resp.read()
with urllib.request.urlopen(BASE + "/manifest.json", timeout=5) as resp:
    assert json.loads(resp.read())["short_name"] == "Comandera"
print("OK página móvil y manifiesto")

# --- estado inicial
estado = GET("/api/estado")
assert estado["nombre"] == "El Horno de Leo"
assert len(estado["mesas"]) == 8 and not estado["mesas"][0]["abierta"]
assert len(estado["productos"]) == len(r.CARTA_HORNO_DE_LEO)
print("OK /api/estado:", len(estado["productos"]), "productos")

# --- stock: Baklava con 2 unidades
con = r.db()
pid_baklava = con.execute(
    "SELECT id FROM productos WHERE nombre='Baklava'").fetchone()[0]
pid_shawarma = con.execute(
    "SELECT id, precio FROM productos WHERE nombre='Shawarma de Pollo'").fetchone()
con.execute("UPDATE productos SET usar_stock=1, stock=2, stock_min=1 "
            "WHERE id=?", (pid_baklava,))
con.commit(); con.close()
pid_sh, precio_sh = pid_shawarma

# --- pedido válido: 2 shawarmas (comensal 1) + 1 baklava (cuenta general)
code, resp = POST("/api/pedido", {
    "mesa": 3, "mozo": "Caro", "comensales": 2,
    "items": [{"id": pid_sh, "cantidad": 2, "comensal": 1},
              {"id": pid_baklava, "cantidad": 1, "comensal": 0}]})
assert code == 200 and resp["ok"], resp
assert resp["total"] == 2 * precio_sh + 190, resp
con = r.db()
assert con.execute("SELECT mozo, comensales, abierta FROM mesas "
                   "WHERE numero=3").fetchone() == ("Caro", 2, 1)
assert con.execute("SELECT COUNT(*) FROM pedidos WHERE mesa=3").fetchone()[0] == 2
assert con.execute("SELECT stock FROM productos WHERE id=?",
                   (pid_baklava,)).fetchone()[0] == 1
con.close()
print("OK pedido guardado: mesa 3 abierta, mozo Caro, stock descontado")

# --- la comanda de cocina quedó guardada (la impresora no existe)
comandas = glob.glob(os.path.join(r.RECIBOS_DIR, "comanda_*.txt"))
assert len(comandas) == 1
texto = open(comandas[0], encoding="utf-8").read()
assert "COMANDA COCINA" in texto and "Mesa 3" in texto
assert " 2 x Shawarma de Pollo  (comensal 1)" in texto
assert "(pedido desde el celular)" in texto
print("OK comanda de cocina generada")

# --- detalle de la mesa
detalle = GET("/api/mesa?n=3")
assert detalle["mozo"] == "Caro" and len(detalle["items"]) == 2
assert detalle["total"] == resp["total"]
print("OK /api/mesa")

# --- segundo envío a la misma mesa (un comensal agrega algo después):
#     se suma a la cuenta y la comanda nueva trae SOLO lo agregado
total_previo = resp["total"]
code, resp = POST("/api/pedido", {
    "mesa": 3, "mozo": "Caro", "comensales": 3,
    "items": [{"id": pid_baklava, "cantidad": 1, "comensal": 3}]})
assert code == 200 and resp["total"] == total_previo + 190, resp
con = r.db()
assert con.execute("SELECT COUNT(*) FROM pedidos WHERE mesa=3").fetchone()[0] == 3
assert con.execute("SELECT comensales FROM mesas WHERE numero=3").fetchone()[0] == 3
assert con.execute("SELECT stock FROM productos WHERE id=?",
                   (pid_baklava,)).fetchone()[0] == 0
con.close()
comandas = sorted(glob.glob(os.path.join(r.RECIBOS_DIR, "comanda_*.txt")))
assert len(comandas) == 2
texto = open(comandas[-1], encoding="utf-8").read()
assert " 1 x Baklava  (comensal 3)" in texto and "Shawarma" not in texto
print("OK agregar después: suma a la cuenta y comanda solo con lo nuevo")

# --- rechazo por falta de stock (no queda baklava, pido 5) y nada cambia
code, resp = POST("/api/pedido", {
    "mesa": 3, "mozo": "Caro",
    "items": [{"id": pid_baklava, "cantidad": 5, "comensal": 0}]})
assert code == 409 and "Baklava" in resp["error"], (code, resp)
con = r.db()
assert con.execute("SELECT stock FROM productos WHERE id=?",
                   (pid_baklava,)).fetchone()[0] == 0
assert con.execute("SELECT COUNT(*) FROM pedidos WHERE mesa=3").fetchone()[0] == 3
con.close()
print("OK rechazo por stock insuficiente (sin cambios en la base)")

# --- pedidos inválidos
assert POST("/api/pedido", {"mesa": 99, "items": [
    {"id": pid_sh, "cantidad": 1, "comensal": 0}]})[0] == 404
assert POST("/api/pedido", {"mesa": 1, "items": []})[0] == 400
assert POST("/api/pedido", {"mesa": 1, "items": [
    {"id": 999999, "cantidad": 1, "comensal": 0}]})[0] == 400
assert GET("/api/estado")["mesas"][0]["abierta"] is False
print("OK rechazos: mesa inexistente, sin ítems, producto inexistente")

# --- IP fija en Windows: máscara y contenido de los .bat
assert r.mascara_desde_prefijo(24) == "255.255.255.0"
assert r.mascara_desde_prefijo(25) == "255.255.255.128"
assert r.mascara_desde_prefijo(16) == "255.255.0.0"
bat = r.armar_bat_ip_fija("Wi-Fi", "192.168.1.50", 24, "192.168.1.1",
                          "192.168.1.1,8.8.8.8")
assert ('netsh interface ipv4 set address name="Wi-Fi" '
        'static 192.168.1.50 255.255.255.0 192.168.1.1') in bat
assert 'set dnsservers name="Wi-Fi" static 192.168.1.1 primary' in bat
assert 'add dnsservers name="Wi-Fi" 8.8.8.8 index=2' in bat
bat = r.armar_bat_ip_fija("Ethernet", "10.0.0.7", 24, "10.0.0.1", "")
assert 'static 10.0.0.1 primary' in bat  # sin DNS conocido usa el router
bat = r.armar_bat_ip_dhcp("Wi-Fi")
assert 'set address name="Wi-Fi" dhcp' in bat
assert 'set dnsservers name="Wi-Fi" dhcp' in bat
print("OK IP fija Windows: máscaras y .bat de netsh bien armados")

comandera.detener(srv)
shutil.rmtree(FAKEHOME, ignore_errors=True)
print("\nTODO OK — comandera funcionando")
