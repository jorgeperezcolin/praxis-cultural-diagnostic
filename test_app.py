"""Prueba end-to-end de la maqueta con Streamlit AppTest (sin navegador).

Correr: python test_app.py   (usa el PIN de demo 1234; borra praxis_demo.db local)
"""
import os, sqlite3, sys
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
os.chdir(os.path.dirname(APP))
if os.path.exists("praxis_demo.db"):
    os.remove("praxis_demo.db")


def ok(at, where):
    assert not at.exception, (where, [e.value for e in at.exception])


def console(role, pin=True):
    at = AppTest.from_file(APP, default_timeout=30)
    at.run(); ok(at, "load")
    at.sidebar.radio[0].set_value(role).run(); ok(at, role)
    if pin and role in ("Operador", "Conductor", "Moderador remoto"):
        pw = [t for t in at.text_input if t.label.startswith("PIN")][0]
        pw.set_value("1234").run()
        [b for b in at.button if b.label == "Entrar"][0].click().run(); ok(at, "pin")
    return at


def btn(at, label):
    b = [x for x in at.button if x.label.startswith(label)][0]
    b.click().run(); ok(at, label)


def participant():
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["rol"] = "participante"
    at.query_params["s"] = "PRAXIS-LEON"
    at.run(); ok(at, "participante")
    return at


# 1. Proyección es la vista por defecto y no pide PIN
at = AppTest.from_file(APP, default_timeout=30); at.run(); ok(at, "default")
assert at.sidebar.radio[0].value == "Proyección"
assert not [t for t in at.text_input if t.label.startswith("PIN")]
print("1 ok · Proyección por defecto, sin PIN")

# 2. Operador sin PIN correcto no ve la consola
at = console("Operador", pin=False)
assert not [b for b in at.button if b.label == "Abrir captura"]
[t for t in at.text_input if t.label.startswith("PIN")][0].set_value("0000").run()
[b for b in at.button if b.label == "Entrar"][0].click().run(); ok(at, "bad pin")
assert [e for e in at.error if "PIN incorrecto" in e.value]
assert not [b for b in at.button if b.label == "Abrir captura"]
print("2 ok · PIN incorrecto bloquea la consola")

# 3. Participante antes de abrir: sin formulario, sin barra lateral
at = participant()
assert not at.sidebar.radio and not at.number_input, "no debe haber formulario ni selector de rol"
print("3 ok · liga de participante: sin formulario antes de abrir, sin selector de rol")

# 4. Operador abre captura; QR aparece en la proyección
at = console("Operador"); btn(at, "Abrir captura")
at = AppTest.from_file(APP, default_timeout=30); at.run(); ok(at, "proy")
assert any("<svg" in m.value for m in at.markdown), "QR no encontrado en el muro"
assert any("rol=participante" in m.value for m in at.markdown)
print("4 ok · QR con liga de participante en el muro")

# 5. Participante envía; recibo; duplicado de mesa rechazado desde otro celular
at = participant()
at.number_input[0].set_value(7)
at.text_area[0].set_value("En Praxis se asume que el socio fundador siempre tiene la última palabra")
at.text_area[1].set_value("El caso narra que ningún proyecto avanza sin su firma.")
at.button[0].click().run(); ok(at, "envío")
assert [s for s in at.success if "Recibido" in s.value and "mesa 7" in s.value], [s.value for s in at.success]
assert not at.number_input, "tras enviar ya no debe mostrarse el formulario"
at2 = participant()
at2.number_input[0].set_value(7)
at2.text_area[0].set_value("En Praxis se asume que otra cosa distinta pasa aquí")
at2.button[0].click().run(); ok(at2, "dup")
assert [e for e in at2.error if "ya envió" in e.value], [e.value for e in at2.error]
print("5 ok · recibo con #envío y mesa; mesa duplicada rechazada")

# 6. XSS: el HTML de un participante se escapa en el muro
at3 = participant()
at3.number_input[0].set_value(8)
at3.text_area[0].set_value("En Praxis se asume que <img src=x onerror=alert(1)> manda")
at3.button[0].click().run(); ok(at3, "xss")
at = AppTest.from_file(APP, default_timeout=30); at.run(); ok(at, "proy2")
wall = " ".join(m.value for m in at.markdown)
assert "<img src=x" not in wall and "&lt;img" in wall
print("6 ok · HTML de participantes escapado en la proyección")

# 7. Operador anula el envío #2, carga demo, congela, procesa y avanza hasta bitácora
at = console("Operador")
at.number_input(key="anular_id").set_value(2).run(); ok(at, "anular set")
btn(at, "Anular envío")
btn(at, "Cargar 32")
con = sqlite3.connect("praxis_demo.db")
mesas = [r[0] for r in con.execute("select trio from envios order by id")]
assert len(mesas) == len(set(mesas)) == 33, mesas
at = console("Conductor")
at.text_input[0].set_value("Aquí todo lo decide el director general").run()
btn(at, "Registrar apuesta")
at = console("Operador"); btn(at, "Congelar"); btn(at, "Procesar")
for _ in range(4):
    btn(at, "Avanzar")
print("7 ok · anular, demo sin mesas duplicadas, flujo completo")

# 8. Participante tras el cierre y Plan B
at = participant()
assert [i for i in at.info if "cerró" in i.value]
at = console("Operador"); btn(at, "🛟")
at = console("Moderador remoto")
ev = [r[0] for r in con.execute("select evento from bitacora")]
assert "Envío anulado" in ev and "INCIDENCIA · Respaldo activado" in ev
print("8 ok · participante ve captura cerrada; Plan B; bitácora con anulación e incidencia")
print("ALL OK")
