"""
Diagnóstico cultural asistido por IA · Caso Praxis (DP 26 C 02 · método DP 26 N 03)
Maqueta Streamlit — Business Data Scientists

Ejecutar:
    pip install -r requirements.txt
    streamlit run app.py                         # local
    streamlit run app.py --server.address 0.0.0.0  # varios dispositivos en la misma red

Ligas:
    /?rol=participante&s=PRAXIS-LEON   -> solo el formulario del trío (sin barra lateral ni otros roles)
    /                                   -> consola; Operador, Conductor y Moderador piden PIN
"""

from __future__ import annotations

import hmac
import html
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import altair as alt
import pandas as pd
import segno
import streamlit as st

import engine

st.set_page_config(page_title="Diagnóstico cultural asistido por IA", page_icon="🧭", layout="wide")

DB_PATH = "praxis_demo.db"
RETENTION_DAYS = 30
TARGET_SECONDS = 90
MAX_TRIOS = 40
DEFAULT_SESSION = "PRAXIS-LEON"
DEFAULT_PIN = "1234"  # solo para demo local; en Streamlit Cloud se define STAFF_PIN en Secrets
PROTECTED_ROLES = {"Operador", "Conductor", "Moderador remoto"}


def secret(name: str, default: str | None = None) -> str | None:
    """Lee de st.secrets o de variables de entorno sin fallar si no hay secrets.toml."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name, default)

PHASES = [
    "PREPARADA", "CAPTURANDO", "CORPUS CONGELADO", "PROCESANDO",
    "SALA", "INSTRUMENTO", "CONTRASTE", "CRITERIO", "BITÁCORA",
]
ROLES = ["Participante", "Operador", "Conductor", "Proyección", "Moderador remoto"]

st.markdown(
    """
    <style>
      .big {font-size: 2.1rem; font-weight: 700; line-height: 1.2;}
      .mid {font-size: 1.35rem;}
      .phase {display:inline-block; padding:4px 10px; border-radius:6px; margin:2px;
              font-size:.8rem; border:1px solid #9993;}
      .on {background:#1f4e79; color:white; border-color:#1f4e79;}
      .card {padding:14px 18px; border-radius:10px; border:1px solid #9994; margin-bottom:10px;}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# Persistencia (SQLite) — sin datos personales
# --------------------------------------------------------------------------
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS sesiones (
              codigo TEXT PRIMARY KEY, fase TEXT, creada TEXT,
              congelada TEXT, apuesta TEXT, apuesta_ts TEXT,
              criterio TEXT, criterio_ts TEXT, umbral REAL DEFAULT 0.30,
              respaldo INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS envios (
              id INTEGER PRIMARY KEY AUTOINCREMENT, sesion TEXT, ts TEXT,
              seccion TEXT, supuesto TEXT, evidencia TEXT, intensidad INTEGER,
              congelado INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS corridas (
              id INTEGER PRIMARY KEY AUTOINCREMENT, sesion TEXT, ts TEXT,
              origen TEXT, reglas TEXT, hallazgos TEXT, tiempos TEXT);
            CREATE TABLE IF NOT EXISTS bitacora (
              id INTEGER PRIMARY KEY AUTOINCREMENT, sesion TEXT, ts TEXT,
              rol TEXT, evento TEXT, detalle TEXT);
            """
        )
        cols = {r["name"] for r in con.execute("PRAGMA table_info(envios)")}
        if "trio" not in cols:  # migración de bases creadas por la versión anterior
            con.execute("ALTER TABLE envios ADD COLUMN trio INTEGER")
        con.execute("CREATE INDEX IF NOT EXISTS ix_envios_sesion_trio ON envios(sesion, trio)")


def purge_old() -> None:
    """Retención del corpus anonimizado: 30 días."""
    limite = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()
    with db() as con:
        viejas = [r["codigo"] for r in con.execute("SELECT codigo FROM sesiones WHERE creada < ?", (limite,))]
        for c in viejas:
            for t in ("envios", "corridas", "bitacora"):
                con.execute(f"DELETE FROM {t} WHERE sesion = ?", (c,))
            con.execute("DELETE FROM sesiones WHERE codigo = ?", (c,))


def now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def log(sesion: str, rol: str, evento: str, detalle: str = "") -> None:
    with db() as con:
        con.execute("INSERT INTO bitacora (sesion, ts, rol, evento, detalle) VALUES (?,?,?,?,?)",
                    (sesion, now(), rol, evento, detalle))


def get_session(codigo: str) -> dict:
    with db() as con:
        r = con.execute("SELECT * FROM sesiones WHERE codigo = ?", (codigo,)).fetchone()
        if r is None:
            con.execute("INSERT INTO sesiones (codigo, fase, creada) VALUES (?,?,?)",
                        (codigo, "PREPARADA", now()))
            r = con.execute("SELECT * FROM sesiones WHERE codigo = ?", (codigo,)).fetchone()
    return dict(r)


def update_session(codigo: str, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db() as con:
        con.execute(f"UPDATE sesiones SET {sets} WHERE codigo = ?", (*fields.values(), codigo))


def set_phase(codigo: str, fase: str, rol: str) -> None:
    update_session(codigo, fase=fase)
    log(codigo, rol, "Cambio de fase", fase)


def trio_submission(codigo: str, trio: int) -> dict | None:
    with db() as con:
        r = con.execute("SELECT * FROM envios WHERE sesion = ? AND trio = ? ORDER BY id LIMIT 1",
                        (codigo, int(trio))).fetchone()
    return dict(r) if r else None


def add_submission(codigo: str, seccion: str, supuesto: str, evidencia: str, intensidad: int,
                   trio: int | None = None) -> int:
    """Inserta un envío y devuelve su id. Un trío (mesa) solo puede enviar una vez por sesión."""
    with db() as con:
        if trio is not None:
            dup = con.execute("SELECT id FROM envios WHERE sesion = ? AND trio = ?", (codigo, int(trio))).fetchone()
            if dup:
                raise ValueError(f"La mesa {trio} ya envió su diagnóstico (#{dup['id']}).")
        cur = con.execute(
            "INSERT INTO envios (sesion, ts, seccion, supuesto, evidencia, intensidad, trio) VALUES (?,?,?,?,?,?,?)",
            (codigo, now(), seccion, supuesto.strip(), evidencia.strip(), int(intensidad),
             int(trio) if trio is not None else None),
        )
        return cur.lastrowid


def void_submission(codigo: str, envio_id: int, rol: str) -> bool:
    """Anula un envío durante la captura (p. ej. una mesa se equivocó). Queda en bitácora."""
    with db() as con:
        cur = con.execute("DELETE FROM envios WHERE sesion = ? AND id = ? AND congelado = 0", (codigo, int(envio_id)))
        ok = cur.rowcount > 0
    if ok:
        log(codigo, rol, "Envío anulado", f"#{envio_id}")
    return ok


def submissions(codigo: str, solo_congelados: bool = False) -> list[dict]:
    q = "SELECT * FROM envios WHERE sesion = ?" + (" AND congelado = 1" if solo_congelados else "")
    with db() as con:
        return [dict(r) for r in con.execute(q + " ORDER BY id", (codigo,))]


def freeze(codigo: str, rol: str) -> int:
    t0 = time.perf_counter()
    with db() as con:
        con.execute("UPDATE envios SET congelado = 1 WHERE sesion = ?", (codigo,))
        n = con.execute("SELECT COUNT(*) FROM envios WHERE sesion = ?", (codigo,)).fetchone()[0]
    update_session(codigo, fase="CORPUS CONGELADO", congelada=now())
    log(codigo, rol, "Corpus congelado", f"{n} envíos · {time.perf_counter()-t0:.3f}s")
    return n


def latest_run(codigo: str) -> dict | None:
    with db() as con:
        r = con.execute("SELECT * FROM corridas WHERE sesion = ? ORDER BY id DESC LIMIT 1", (codigo,)).fetchone()
    if r is None:
        return None
    r = dict(r)
    for k in ("reglas", "hallazgos", "tiempos"):
        r[k] = json.loads(r[k])
    return r


def process(codigo: str, rol: str, inject_error: bool, origen: str = "en vivo") -> dict:
    s = get_session(codigo)
    set_phase(codigo, "PROCESANDO", rol)
    corpus = submissions(codigo, solo_congelados=True)
    t0 = time.perf_counter()
    reglas = engine.apply_rules(corpus, umbral=s["umbral"] or 0.30)
    t_rules = time.perf_counter() - t0
    hall = engine.mock_ai_analysis(reglas, corpus, inject_error=inject_error)
    t_ai = time.perf_counter() - t0 - t_rules
    aud = engine.audit(hall, corpus)
    t_total = time.perf_counter() - t0
    congelada = datetime.fromisoformat(s["congelada"]) if s["congelada"] else datetime.now()
    cierre_a_tablero = (datetime.now() - congelada).total_seconds()
    tiempos = {"reglas_s": round(t_rules, 3), "ia_s": round(t_ai, 3), "total_proc_s": round(t_total, 3),
               "cierre_a_tablero_s": round(cierre_a_tablero, 1)}
    with db() as con:
        con.execute("INSERT INTO corridas (sesion, ts, origen, reglas, hallazgos, tiempos) VALUES (?,?,?,?,?,?)",
                    (codigo, now(), origen, json.dumps(reglas, ensure_ascii=False),
                     json.dumps(aud, ensure_ascii=False), json.dumps(tiempos)))
    log(codigo, rol, "Corrida procesada", f"{origen} · {json.dumps(tiempos)}")
    set_phase(codigo, "SALA", rol)
    return tiempos


def load_demo(codigo: str, rol: str, n: int = 32) -> None:
    usados = {s["trio"] for s in submissions(codigo) if s.get("trio")}
    libres = (k for k in range(1, 10_000) if k not in usados)
    for s in engine.demo_submissions(n):
        add_submission(codigo, s["seccion"], s["supuesto"], s["evidencia"], s["intensidad"], trio=next(libres))
    log(codigo, rol, "Datos demo cargados", f"{n} envíos ficticios")


def activate_backup(codigo: str, rol: str) -> None:
    """Plan B: detiene la corrida activa y carga la corrida validada del ensayo."""
    with db() as con:
        con.execute("DELETE FROM envios WHERE sesion = ?", (codigo,))
    load_demo(codigo, rol, 32)
    with db() as con:
        con.execute("UPDATE envios SET congelado = 1 WHERE sesion = ?", (codigo,))
    update_session(codigo, respaldo=1, congelada=now())
    log(codigo, rol, "INCIDENCIA · Respaldo activado", "Se carga corrida validada del ensayo")
    process(codigo, rol, inject_error=False, origen="respaldo (ensayo validado)")
    set_phase(codigo, "INSTRUMENTO", rol)


def reset_session(codigo: str) -> None:
    with db() as con:
        for t in ("envios", "corridas", "bitacora"):
            con.execute(f"DELETE FROM {t} WHERE sesion = ?", (codigo,))
        con.execute("DELETE FROM sesiones WHERE codigo = ?", (codigo,))


def export_bitacora(codigo: str) -> str:
    with db() as con:
        ev = [dict(r) for r in con.execute("SELECT ts, rol, evento, detalle FROM bitacora WHERE sesion = ? ORDER BY id", (codigo,))]
    payload = {
        "sesion": get_session(codigo),
        "corpus_anonimizado": [{k: s[k] for k in ("id", "seccion", "supuesto", "evidencia", "intensidad")}
                               for s in submissions(codigo, solo_congelados=True)],
        "corrida": latest_run(codigo),
        "eventos": ev,
        "politica": f"Sin datos personales · retención {RETENTION_DAYS} días",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# Liga de participante, QR y acceso del staff
# --------------------------------------------------------------------------
def base_url() -> str:
    """URL pública de la app: APP_URL en Secrets o, si no, el host de la petición."""
    configured = secret("APP_URL")
    if configured:
        return configured.rstrip("/")
    try:
        host = st.context.headers.get("host") or "localhost:8501"
    except Exception:
        host = "localhost:8501"
    scheme = "http" if host.startswith(("localhost", "127.", "0.0.0.0", "192.168.", "10.")) else "https"
    return f"{scheme}://{host}"


def participant_url(codigo: str) -> str:
    return f"{base_url()}/?rol=participante&s={quote(codigo)}"


def qr_svg(url: str, scale: int = 8) -> str:
    return segno.make(url, error="m").svg_inline(scale=scale, dark="#111111", light="#ffffff", border=2)


def staff_pin() -> str:
    return secret("STAFF_PIN", DEFAULT_PIN) or DEFAULT_PIN


def require_pin(rol: str) -> bool:
    """Devuelve True si este navegador ya se autenticó como staff en esta sesión."""
    if st.session_state.get("staff_ok"):
        return True
    st.subheader(f"{rol} · acceso con PIN")
    st.caption("Esta vista controla la sesión. Los participantes usan su propia liga o el QR proyectado.")
    with st.form("pin"):
        pin = st.text_input("PIN del equipo docente", type="password")
        ok = st.form_submit_button("Entrar", type="primary")
    if ok:
        if hmac.compare_digest(pin.strip(), staff_pin()):
            st.session_state["staff_ok"] = True
            st.rerun()
        else:
            st.error("PIN incorrecto. Pídelo al operador de la sesión.")
    return False


# --------------------------------------------------------------------------
# Componentes de UI
# --------------------------------------------------------------------------
def phase_bar(fase: str) -> None:
    html = "".join(f"<span class='phase {'on' if p == fase else ''}'>{p}</span>" for p in PHASES)
    st.markdown(html, unsafe_allow_html=True)


def bar_chart(df: pd.DataFrame, x: str, y: str, color: str | None = None, height: int = 320) -> None:
    """Barras horizontales ordenadas (nunca pastel/dona)."""
    enc = {
        "x": alt.X(f"{x}:Q", title=None),
        "y": alt.Y(f"{y}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=520)),
        "tooltip": list(df.columns),
    }
    if color:
        enc["color"] = alt.Color(f"{color}:N", legend=alt.Legend(orient="bottom", title=None))
    chart = alt.Chart(df).mark_bar().encode(**enc).properties(height=height)
    text = alt.Chart(df).mark_text(align="left", dx=4).encode(
        x=f"{x}:Q", y=alt.Y(f"{y}:N", sort="-x"), text=f"{x}:Q")
    st.altair_chart(chart + text, width="stretch")


def clusters_df(reglas: dict) -> pd.DataFrame:
    return pd.DataFrame([{
        "Supuesto": c["etiqueta"], "Tríos": c["n"], "Cuota": f"{int(c['cuota']*100)}%",
        "Evidencia": f"{int(c['evidencia']*100)}%", "Intensidad": c["intensidad"],
        "Estado": c["estado"], "Reglas implícitas": ", ".join(c["reglas_implicitas"]),
        "Envíos": ", ".join(map(str, c["ids"])),
    } for c in reglas["clusters"]])


def view_muro(codigo: str) -> None:
    subs = submissions(codigo)
    c1, c2 = st.columns([1, 3])
    with c1:
        if get_session(codigo)["fase"] == "CAPTURANDO":
            url = participant_url(codigo)
            st.markdown(f"<div style='background:#fff;padding:8px;border-radius:8px;display:inline-block'>{qr_svg(url, 6)}</div>",
                        unsafe_allow_html=True)
            st.markdown(f"<div class='mid'><b>Escanea para enviar</b></div><small>{html.escape(url)}</small>", unsafe_allow_html=True)
        st.metric("Envíos recibidos", f"{len(subs)} / {MAX_TRIOS}")
    por_sec = pd.Series([s["seccion"] for s in subs]).value_counts().reindex(engine.SECTIONS, fill_value=0)
    c1.dataframe(por_sec.rename("Envíos"), width="stretch")
    with c2:
        st.markdown("<div class='mid'><b>Muro de la sala</b> · últimos supuestos</div>", unsafe_allow_html=True)
        for s in subs[-8:][::-1]:
            st.markdown(f"<div class='card'>#{s['id']} · Sección {s['seccion']} — {html.escape(s['supuesto'])}</div>",
                        unsafe_allow_html=True)


def view_sala(run: dict) -> None:
    r = run["reglas"]
    st.markdown(f"<div class='big'>Lo que ve la sala</div><div class='mid'>{r['n']} tríos · umbral {int(r['umbral']*100)}%</div>",
                unsafe_allow_html=True)
    df = pd.DataFrame([{"Supuesto": c["etiqueta"][:90], "Tríos": c["n"], "Estado": c["estado"]}
                       for c in r["clusters"]])
    bar_chart(df, "Tríos", "Supuesto", color="Estado", height=60 + 42 * len(df))


def view_instrumento(run: dict) -> None:
    st.markdown("<div class='big'>Lo que lee el instrumento</div><div class='mid'>Lectura IA, auditada contra el corpus congelado</div>",
                unsafe_allow_html=True)
    icon = {"Verificado": "✅", "Parcial": "🟡", "Rechazado": "⛔"}
    for h in run["hallazgos"]:
        st.markdown(
            f"<div class='card'>{icon[h['estado_auditoria']]} <b>{h['tipo']}</b> — {html.escape(h['texto'])}<br>"
            f"<small>Citas: {h['citas']} · Auditoría: {h['estado_auditoria']} ({h['motivo']})</small></div>",
            unsafe_allow_html=True)


def view_contraste(run: dict, s: dict) -> None:
    r = run["reglas"]
    dom = next(c for c in r["clusters"] if c["cid"] == r["dominante"])
    st.markdown("<div class='big'>Contraste</div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<div class='mid'><b>Apuesta previa del grupo</b></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='card'>{html.escape(s['apuesta'] or '— sin apuesta registrada —')}<br>"
                    f"<small>Registrada: {s['apuesta_ts'] or 'n/d'}</small></div>", unsafe_allow_html=True)
    with c2:
        st.markdown("<div class='mid'><b>Supuesto dominante (reglas)</b></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='card'>{html.escape(dom['etiqueta'])}<br><small>{dom['n']} tríos · {int(dom['cuota']*100)}%</small></div>",
                    unsafe_allow_html=True)
    if s["apuesta"]:
        sim = engine.bet_similarity(s["apuesta"], dom["etiqueta"])
        veredicto = "coincide" if sim >= 0.25 else "NO coincide"
        st.info(f"Similitud apuesta–resultado: {sim:.2f} → la apuesta {veredicto} con lo que dice la sala.")
    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**Fracturas por sección**")
        if r["fracturas"]:
            for f in r["fracturas"]:
                st.markdown(f"- Sección **{f['seccion']}**: {f['etiqueta']} ({int(f['cuota_en_seccion']*100)}% de la sección)")
        else:
            st.markdown("- Sin fracturas: todas las secciones comparten el dominante.")
    with c4:
        st.markdown("**Señales débiles**")
        if r["senales_debiles"]:
            for c in r["senales_debiles"]:
                st.markdown(f"- {c['etiqueta']} ({c['n']} tríos · intensidad {c['intensidad']})")
        else:
            st.markdown("- No se detectaron señales débiles.")


def view_criterio(s: dict) -> None:
    st.markdown("<div class='big'>Criterio humano</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='card mid'>{html.escape(s['criterio'] or '— pendiente de registrar por el Conductor —')}</div>",
                unsafe_allow_html=True)


def view_bitacora(codigo: str, run: dict | None) -> None:
    st.markdown("<div class='big'>Bitácora</div>", unsafe_allow_html=True)
    if run:
        t = run["tiempos"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Cierre → tablero", f"{t['cierre_a_tablero_s']} s", delta=f"meta ≤{TARGET_SECONDS} s", delta_color="off")
        c2.metric("Reglas", f"{t['reglas_s']} s")
        c3.metric("IA (simulada)", f"{t['ia_s']} s")
        c4.metric("Origen", run["origen"])
    with db() as con:
        ev = pd.DataFrame([dict(r) for r in con.execute(
            "SELECT ts, rol, evento, detalle FROM bitacora WHERE sesion = ? ORDER BY id DESC", (codigo,))])
    st.dataframe(ev, width="stretch", hide_index=True)


def render_projection(codigo: str, fase: str, override: str | None = None) -> None:
    s = get_session(codigo)
    run = latest_run(codigo)
    view = override or fase
    if view in ("PREPARADA",):
        st.markdown(f"<div class='big'>Diagnóstico cultural · Caso Praxis</div>"
                    f"<div class='mid'>Sesión {html.escape(codigo)} · espera la apertura de la captura</div>", unsafe_allow_html=True)
    elif view in ("CAPTURANDO", "MURO"):
        view_muro(codigo)
    elif view == "CORPUS CONGELADO":
        st.markdown(f"<div class='big'>Corpus congelado</div><div class='mid'>{len(submissions(codigo, True))} diagnósticos · sin cambios a partir de aquí</div>",
                    unsafe_allow_html=True)
    elif view == "PROCESANDO":
        st.markdown("<div class='big'>Procesando…</div>", unsafe_allow_html=True)
    elif run is None:
        st.warning("Aún no hay una corrida procesada.")
    elif view == "SALA":
        view_sala(run)
    elif view == "INSTRUMENTO":
        view_instrumento(run)
    elif view == "CONTRASTE":
        view_contraste(run, s)
    elif view == "CRITERIO":
        view_criterio(s)
    elif view == "BITÁCORA":
        view_bitacora(codigo, run)


# --------------------------------------------------------------------------
# Vistas por rol
# --------------------------------------------------------------------------
def role_participante(codigo: str, s: dict) -> None:
    st.subheader("Diagnóstico cultural · Caso Praxis")
    st.caption("Un envío por trío. No se piden nombres, correos ni datos personales.")
    enviados = st.session_state.setdefault("enviados", {})

    # Confirmación persistente: si este celular ya envió en esta sesión, se muestra el recibo y no el formulario.
    if codigo in enviados:
        r = enviados[codigo]
        st.success(f"✅ Recibido · envío #{r['id']} · mesa {r['mesa']}")
        st.markdown(f"<div class='card'><b>Su supuesto:</b> {html.escape(r['supuesto'])}</div>", unsafe_allow_html=True)
        st.caption("Ya puede ver su envío en la pantalla. Si hay un error, avise al operador para que lo anule.")
        return

    if s["fase"] != "CAPTURANDO":
        st.info("La captura todavía no está abierta. Esta página se actualiza sola en cuanto el profesor la abra."
                if s["fase"] == "PREPARADA" else "La captura ya cerró. Gracias por participar.")

        @st.fragment(run_every="3s")
        def wait_for_open():
            if get_session(codigo)["fase"] == "CAPTURANDO":
                st.rerun()

        if s["fase"] == "PREPARADA":
            wait_for_open()
        return

    with st.form("envio"):
        mesa = st.number_input("Número de mesa del trío", min_value=1, max_value=MAX_TRIOS, step=1, value=None,
                               placeholder=f"1 a {MAX_TRIOS}", help="Lo indica la tarjeta de su mesa. Evita envíos duplicados.")
        seccion = st.selectbox("Sección del grupo", engine.SECTIONS)
        supuesto = st.text_area("Supuesto cultural", value="En Praxis se asume que ", max_chars=280,
                                help="Complete la frase con el supuesto que su trío identifica.")
        evidencia = st.text_area("Evidencia del caso", max_chars=400,
                                 help="Hecho o pasaje del caso que sostiene el supuesto.")
        intensidad = st.slider("Intensidad (qué tanto pesa en la conducta)", 1, 5, 3)
        ok = st.form_submit_button("Enviar diagnóstico", type="primary", width="stretch")
    if ok:
        if mesa is None:
            st.error("Indique el número de mesa antes de enviar.")
        elif len(engine.normalize(supuesto)) < 8:
            st.error("El supuesto está vacío o incompleto: complete la frase «En Praxis se asume que…».")
        elif get_session(codigo)["fase"] != "CAPTURANDO":
            st.error("La captura se cerró mientras escribían. Avisen al profesor.")
        else:
            try:
                t0 = time.perf_counter()
                envio_id = add_submission(codigo, seccion, supuesto, evidencia, intensidad, trio=int(mesa))
                enviados[codigo] = {"id": envio_id, "mesa": int(mesa), "supuesto": supuesto.strip(),
                                    "t": round(time.perf_counter() - t0, 2)}
                st.rerun()
            except ValueError as e:
                st.error(f"{e} Si es un error, avisen al operador para que lo anule.")


def role_operador(codigo: str, s: dict) -> None:
    st.subheader("Consola del operador")
    fase = s["fase"]
    idx = PHASES.index(fase)
    subs = submissions(codigo)
    c1, c2, c3 = st.columns(3)
    c1.metric("Fase", fase)
    c2.metric("Envíos", len(subs))
    c3.metric("Respaldo activo", "Sí" if s["respaldo"] else "No")

    st.markdown("#### Flujo")
    b1, b2, b3, b4 = st.columns(4)
    if b1.button("Abrir captura", disabled=fase != "PREPARADA", width="stretch"):
        set_phase(codigo, "CAPTURANDO", "Operador"); st.rerun()
    if b2.button("Congelar corpus", disabled=fase != "CAPTURANDO" or not subs, width="stretch"):
        freeze(codigo, "Operador"); st.rerun()
    inject = st.checkbox("Demostrar auditoría: inyectar una afirmación sin sustento en la IA", value=True)
    if b3.button("Procesar (reglas + IA + auditor)", type="primary",
                 disabled=fase != "CORPUS CONGELADO", width="stretch"):
        with st.spinner("Procesando…"):
            t = process(codigo, "Operador", inject_error=inject)
        st.toast(f"Cierre → tablero: {t['cierre_a_tablero_s']} s"); st.rerun()
    nxt = PHASES[idx + 1] if idx + 1 < len(PHASES) and idx >= PHASES.index("SALA") else None
    if b4.button(f"Avanzar a {nxt}" if nxt else "Avanzar", disabled=nxt is None, width="stretch"):
        set_phase(codigo, nxt, "Operador"); st.rerun()

    st.markdown("#### Plan B")
    if st.button("🛟 ACTIVAR RESPALDO", type="secondary"):
        activate_backup(codigo, "Operador"); st.rerun()
    st.caption("Detiene la corrida activa, carga la corrida validada del ensayo, conserva el cronómetro, "
               "habilita Instrumento → Contraste y registra la incidencia.")

    with st.expander("Configuración y utilidades"):
        umbral = st.slider("Umbral de patrón (cuota de tríos)", 0.10, 0.60, float(s["umbral"] or 0.30), 0.05)
        if umbral != s["umbral"]:
            update_session(codigo, umbral=umbral)
            log(codigo, "Operador", "Umbral ajustado", f"{umbral:.2f}")
        cA, cB, cC = st.columns(3)
        if cA.button("Cargar 32 envíos demo", disabled=fase != "CAPTURANDO"):
            load_demo(codigo, "Operador"); st.rerun()
        if cB.button("Regresar a SALA", disabled=latest_run(codigo) is None):
            set_phase(codigo, "SALA", "Operador"); st.rerun()
        if cC.button("⚠️ Reiniciar sesión"):
            reset_session(codigo); st.rerun()

    run = latest_run(codigo)
    if run:
        st.markdown("#### Resultado del motor de reglas")
        st.dataframe(clusters_df(run["reglas"]), width="stretch", hide_index=True)
        t = run["tiempos"]
        ok = t["cierre_a_tablero_s"] <= TARGET_SECONDS
        st.markdown(f"Cierre → tablero: **{t['cierre_a_tablero_s']} s** {'✅' if ok else '⛔'} (meta ≤{TARGET_SECONDS} s)")
    if subs:
        with st.expander(f"Corpus ({len(subs)} envíos)"):
            st.dataframe(pd.DataFrame(subs)[["id", "trio", "seccion", "supuesto", "evidencia", "intensidad", "congelado"]]
                         .rename(columns={"trio": "mesa"}), width="stretch", hide_index=True)
            if fase == "CAPTURANDO":
                cX, cY = st.columns([1, 2])
                anular = cX.number_input("Envío a anular (#)", min_value=1, step=1, value=None, key="anular_id")
                if cY.button("Anular envío", disabled=anular is None):
                    if void_submission(codigo, int(anular), "Operador"):
                        st.toast(f"Envío #{int(anular)} anulado"); st.rerun()
                    else:
                        st.error("No existe un envío abierto con ese número.")


def role_conductor(codigo: str, s: dict) -> None:
    st.subheader("Panel del conductor")
    phase_bar(s["fase"])
    st.markdown("#### 1 · Apuesta previa (antes del resultado)")
    bloqueada = s["fase"] in PHASES[PHASES.index("PROCESANDO"):]
    if s["apuesta"] and bloqueada:
        st.success(f"Apuesta registrada ({s['apuesta_ts']}): {s['apuesta']}")
    else:
        apuesta = st.text_input("¿Qué supuesto cree el grupo que dominará?", value=s["apuesta"] or "",
                                disabled=bloqueada)
        if st.button("Registrar apuesta", disabled=bloqueada or not apuesta.strip()):
            update_session(codigo, apuesta=apuesta.strip(), apuesta_ts=now())
            log(codigo, "Conductor", "Apuesta registrada", apuesta.strip()); st.rerun()
        if bloqueada and not s["apuesta"]:
            st.warning("El procesamiento ya inició sin apuesta previa: el contraste pierde fuerza pedagógica.")

    run = latest_run(codigo)
    if run:
        st.markdown("#### 2 · Lectura para conducir")
        r = run["reglas"]
        dom = next(c for c in r["clusters"] if c["cid"] == r["dominante"])
        st.markdown(f"- **Dominante:** {dom['etiqueta']} ({dom['n']} tríos)")
        st.markdown(f"- **Fracturas:** {len(r['fracturas'])} · **Señales débiles:** {len(r['senales_debiles'])}")
        rech = [h for h in run["hallazgos"] if h["estado_auditoria"] != "Verificado"]
        if rech:
            st.markdown(f"- **Auditoría:** {len(rech)} hallazgo(s) de la IA no verificados — úsalos para discutir los límites del instrumento.")

    st.markdown("#### 3 · Criterio humano")
    criterio = st.text_area("Síntesis y decisión del grupo (la última palabra es humana)", value=s["criterio"] or "")
    if st.button("Registrar criterio", disabled=not criterio.strip() or run is None):
        update_session(codigo, criterio=criterio.strip(), criterio_ts=now())
        log(codigo, "Conductor", "Criterio registrado", criterio.strip()); st.rerun()

    st.markdown("#### Vista proyectada actual")
    render_projection(codigo, s["fase"])


def role_proyeccion(codigo: str) -> None:
    override = st.sidebar.selectbox(
        "Vista (opcional)", ["Seguir la fase", "MURO", "SALA", "INSTRUMENTO", "CONTRASTE", "CRITERIO", "BITÁCORA"])

    @st.fragment(run_every="2s")
    def live():
        s = get_session(codigo)
        phase_bar(s["fase"])
        render_projection(codigo, s["fase"], None if override == "Seguir la fase" else override)

    live()


def role_moderador(codigo: str, s: dict) -> None:
    st.subheader("Moderador remoto · solo lectura")
    phase_bar(s["fase"])
    run = latest_run(codigo)
    if run:
        st.dataframe(clusters_df(run["reglas"]), width="stretch", hide_index=True)
    view_bitacora(codigo, run)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    init_db()
    purge_old()
    qp = st.query_params
    codigo_qp = (qp.get("s") or "").strip().upper()

    # Liga de participante: solo el formulario, sin barra lateral ni acceso a otros roles.
    if (qp.get("rol") or "").lower() == "participante":
        st.markdown("<style>[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl'],"
                    "[data-testid='collapsedControl']{display:none}</style>", unsafe_allow_html=True)
        codigo = codigo_qp or DEFAULT_SESSION
        role_participante(codigo, get_session(codigo))
        return

    st.sidebar.title("🧭 Diagnóstico cultural")
    st.sidebar.caption("Caso Praxis · DP 26 C 02 · método DP 26 N 03")
    codigo = st.sidebar.text_input("Código de sesión", value=codigo_qp or DEFAULT_SESSION).strip().upper() or DEFAULT_SESSION
    rol = st.sidebar.radio("Rol", ROLES, index=ROLES.index("Proyección"))
    s = get_session(codigo)

    st.sidebar.divider()
    if st.session_state.get("staff_ok"):
        st.sidebar.markdown("**Liga para los tríos**")
        st.sidebar.code(participant_url(codigo), language=None)
        st.sidebar.download_button("Descargar bitácora (JSON)", export_bitacora(codigo),
                                   file_name=f"bitacora_{codigo}.json", mime="application/json")
        if staff_pin() == DEFAULT_PIN:
            st.sidebar.warning("PIN de demostración activo. Define STAFF_PIN en Secrets antes de la sesión real.")
        if st.sidebar.button("Salir del modo docente"):
            st.session_state["staff_ok"] = False; st.rerun()
    st.sidebar.caption("Maqueta · IA simulada · sin datos personales · retención 30 días")

    if rol in PROTECTED_ROLES and not require_pin(rol):
        return
    if rol == "Participante":
        st.caption("Vista previa del formulario. Los tríos usan la liga o el QR, que muestran solo esta pantalla.")
        role_participante(codigo, s)
    elif rol == "Operador":
        role_operador(codigo, s)
    elif rol == "Conductor":
        role_conductor(codigo, s)
    elif rol == "Proyección":
        role_proyeccion(codigo)
    else:
        role_moderador(codigo, s)


if __name__ == "__main__":
    main()
