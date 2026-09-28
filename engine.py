"""
Motor del diagnóstico cultural · Caso Praxis (maqueta)

Tres capas separadas, por diseño del PRD v0.2:
  1. Motor de reglas  -> lógica determinística (umbral, evidencia, fracturas, señales débiles)
  2. Motor IA         -> interpretación y síntesis (aquí SIMULADO: mock_ai_analysis)
  3. Auditor          -> verifica que cada hallazgo de la IA sea trazable a evidencia real

Ningún componente recibe ni produce datos personales.
"""

from __future__ import annotations

import random
import re
import unicodedata
from collections import Counter

SECTIONS = ["A", "B", "C", "D"]

STOPWORDS = {
    "en", "praxis", "se", "asume", "que", "la", "el", "los", "las", "un", "una",
    "de", "del", "y", "o", "para", "por", "con", "es", "al", "a", "su", "sus",
    "como", "mas", "menos", "lo", "no", "si", "ya", "hay", "son", "ser", "esta",
    "este", "eso", "esto", "muy", "tan", "cuando", "donde", "pero", "sin", "sobre",
    "tiene", "tienen", "aqui", "porque", "aunque", "entre", "cada", "le", "les",
}

RULE_WORDS = [
    "siempre", "nunca", "nadie", "todos", "requiere", "debe",
    "obligatorio", "solo", "unicamente", "jamas",
]

PREFIX_RE = re.compile(r"^\s*en praxis se asume que\s*", re.IGNORECASE)


# --------------------------------------------------------------------------
# Normalización
# --------------------------------------------------------------------------
def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def normalize(text: str) -> str:
    text = strip_accents((text or "").lower())
    text = PREFIX_RE.sub("", text)
    text = re.sub(r"[^a-zñ0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def stem(word: str) -> str:
    """Stemming ligero para español (suficiente para una maqueta)."""
    for suf in ("aciones", "acion", "mente", "ciones", "cion", "es", "s"):
        if len(word) > 5 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def tokens(text: str) -> set[str]:
    return {stem(w) for w in normalize(text).split() if len(w) > 2 and w not in STOPWORDS}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------
# 1. Motor de reglas (determinístico)
# --------------------------------------------------------------------------
def cluster_assumptions(subs: list[dict], sim_threshold: float = 0.22) -> list[dict]:
    """Agrupamiento codicioso por similitud de tokens contra el perfil del grupo."""
    clusters: list[dict] = []
    for s in sorted(subs, key=lambda x: x["id"]):
        tk = tokens(s["supuesto"])
        best, best_sim = None, 0.0
        for c in clusters:
            profile = {t for t, n in c["tokens"].items() if n >= max(1, len(c["ids"]) * 0.34)}
            sim = jaccard(tk, profile or set(c["tokens"]))
            if sim > best_sim:
                best, best_sim = c, sim
        if best is not None and best_sim >= sim_threshold:
            best["ids"].append(s["id"])
            best["tokens"].update(tk)
        else:
            clusters.append({"ids": [s["id"]], "tokens": Counter(tk)})
    return clusters


def label_cluster(c: dict, by_id: dict) -> str:
    """Etiqueta = supuesto más representativo (mayor solapamiento con el grupo)."""
    top = {t for t, _ in c["tokens"].most_common(6)}
    rep = max(c["ids"], key=lambda i: (len(tokens(by_id[i]["supuesto"]) & top), -len(by_id[i]["supuesto"]), -i))
    text = PREFIX_RE.sub("", by_id[rep]["supuesto"]).strip()
    return text[:1].upper() + text[1:]


def apply_rules(subs: list[dict], umbral: float = 0.30, min_evidencia: float = 0.5) -> dict:
    """
    Reglas del método (maqueta):
      - Patrón        : cuota de tríos >= umbral Y evidencia >= min_evidencia
      - Candidato     : cuota >= umbral pero evidencia insuficiente (no se proyecta como patrón)
      - Señal débil   : <= 2 tríos, intensidad media >= 4
      - Fractura      : sección cuyo supuesto dominante difiere del dominante global
      - Regla implícita: presencia de palabras absolutas (siempre, nunca, nadie...)
    """
    n = len(subs)
    by_id = {s["id"]: s for s in subs}
    if n == 0:
        return {"n": 0, "clusters": [], "fracturas": [], "senales_debiles": [], "umbral": umbral}

    clusters = cluster_assumptions(subs)
    out = []
    for k, c in enumerate(clusters, start=1):
        items = [by_id[i] for i in c["ids"]]
        share = len(items) / n
        ev_ratio = sum(1 for s in items if len(normalize(s.get("evidencia", ""))) >= 12) / len(items)
        intensidad = sum(int(s.get("intensidad", 3)) for s in items) / len(items)
        rule_hits = sorted({w for s in items for w in RULE_WORDS if w in normalize(s["supuesto"]).split()})
        secciones = Counter(s["seccion"] for s in items)
        if share >= umbral and ev_ratio >= min_evidencia:
            estado = "Patrón"
        elif share >= umbral:
            estado = "Candidato sin evidencia"
        elif len(items) <= 2 and intensidad >= 4:
            estado = "Señal débil"
        else:
            estado = "Minoritario"
        out.append({
            "cid": f"S{k}",
            "etiqueta": label_cluster(c, by_id),
            "ids": c["ids"],
            "n": len(items),
            "cuota": round(share, 3),
            "evidencia": round(ev_ratio, 2),
            "intensidad": round(intensidad, 2),
            "reglas_implicitas": rule_hits,
            "secciones": dict(secciones),
            "estado": estado,
        })
    out.sort(key=lambda x: (-x["n"], x["cid"]))

    # Fracturas: dominante por sección vs dominante global
    global_dom = out[0]["cid"]
    fracturas = []
    for sec in SECTIONS:
        counts = [(c["secciones"].get(sec, 0), c["cid"], c["etiqueta"]) for c in out]
        total_sec = sum(x[0] for x in counts)
        if total_sec == 0:
            continue
        top_n, top_cid, top_lbl = max(counts, key=lambda x: (x[0], x[1] == global_dom))
        if top_cid != global_dom:
            fracturas.append({
                "seccion": sec, "cid": top_cid, "etiqueta": top_lbl,
                "cuota_en_seccion": round(top_n / total_sec, 2),
            })

    senales = [c for c in out if c["estado"] == "Señal débil"]
    return {
        "n": n, "umbral": umbral, "clusters": out, "dominante": global_dom,
        "fracturas": fracturas, "senales_debiles": senales,
    }


# --------------------------------------------------------------------------
# 2. Motor IA (SIMULADO) — punto de sustitución por un LLM real
# --------------------------------------------------------------------------
def mock_ai_analysis(rules: dict, subs: list[dict], inject_error: bool = False) -> list[dict]:
    """
    Simula la lectura interpretativa de un LLM. Contrato de salida (el mismo que
    debe respetar el LLM real): lista de hallazgos con 'texto', 'tipo' y 'citas'
    (IDs de envíos del corpus congelado).

    Para sustituir por IA real: enviar corpus + resultado de reglas al proveedor
    (OpenAI, Azure OpenAI, Anthropic...) con instrucción de devolver JSON con este
    mismo esquema. El auditor no cambia.
    """
    hallazgos = []
    patrones = [c for c in rules["clusters"] if c["estado"] == "Patrón"]
    for c in patrones[:3]:
        hallazgos.append({
            "tipo": "Patrón",
            "texto": f"La sala converge en que {c['etiqueta'].lower()} "
                     f"({c['n']} de {rules['n']} tríos, {int(c['cuota']*100)}%).",
            "citas": c["ids"][:3],
        })
    for f in rules["fracturas"]:
        cl = next(c for c in rules["clusters"] if c["cid"] == f["cid"])
        ids_sec = [i for i in cl["ids"] if any(s["id"] == i and s["seccion"] == f["seccion"] for s in subs)]
        hallazgos.append({
            "tipo": "Fractura",
            "texto": f"La sección {f['seccion']} lee otra cultura: {f['etiqueta'].lower()}.",
            "citas": ids_sec[:2],
        })
    for c in rules["senales_debiles"][:2]:
        hallazgos.append({
            "tipo": "Señal débil",
            "texto": f"Pocos tríos, alta intensidad: {c['etiqueta'].lower()}.",
            "citas": c["ids"][:2],
        })
    if inject_error:
        # Afirmación sin sustento para demostrar que el auditor la detiene.
        hallazgos.append({
            "tipo": "Patrón",
            "texto": "La mayoría de los tríos afirma que la estrategia digital es la prioridad cultural.",
            "citas": [9999],
        })
    return hallazgos


# --------------------------------------------------------------------------
# 3. Auditor de citas
# --------------------------------------------------------------------------
def audit(hallazgos: list[dict], subs: list[dict]) -> list[dict]:
    by_id = {s["id"]: s for s in subs}
    audited = []
    for h in hallazgos:
        citas = h.get("citas", [])
        existentes = [i for i in citas if i in by_id]
        con_evidencia = [i for i in existentes if len(normalize(by_id[i].get("evidencia", ""))) >= 12]
        h_tok = tokens(h["texto"])
        soporte = [i for i in existentes if h_tok & tokens(by_id[i]["supuesto"])]
        if not citas or not existentes:
            estado, motivo = "Rechazado", "Cita inexistente en el corpus congelado"
        elif len(soporte) < len(existentes):
            estado, motivo = "Parcial", "Alguna cita no respalda el texto del hallazgo"
        elif len(con_evidencia) < len(existentes):
            estado, motivo = "Parcial", "Alguna cita carece de evidencia del caso"
        else:
            estado, motivo = "Verificado", "Todas las citas existen y respaldan el hallazgo"
        audited.append({**h, "estado_auditoria": estado, "motivo": motivo,
                        "citas_validas": existentes})
    return audited


def bet_similarity(apuesta: str, etiqueta: str) -> float:
    return round(jaccard(tokens(apuesta), tokens(etiqueta)), 2)


# --------------------------------------------------------------------------
# Datos demo (ficticios, sin datos personales)
# --------------------------------------------------------------------------
DEMO_THEMES = [
    ("En Praxis se asume que las decisiones importantes solo las toma el director general",
     "El caso describe que ningún gerente aprueba un proyecto sin visto bueno de la dirección.", 4, 0.50),
    ("En Praxis se asume que equivocarse se castiga y por eso nadie propone ideas nuevas",
     "Tras el proyecto fallido, el responsable fue removido y el equipo dejó de proponer.", 4, 0.18),
    ("En Praxis se asume que el cliente siempre tiene la razón aunque afecte la rentabilidad",
     "Se aceptan cambios de alcance sin cobro para no perder la cuenta.", 3, 0.14),
    ("En Praxis se asume que la antigüedad pesa más que el desempeño para ascender",
     "Las promociones del último año recayeron en los socios con más años.", 3, 0.08),
    ("En Praxis se asume que trabajar horas extra demuestra compromiso",
     "Quien sale a su hora es visto como poco comprometido.", 3, 0.10),
]
DEMO_WEAK = [
    ("En Praxis se asume que los datos se usan para justificar decisiones ya tomadas",
     "Los reportes se preparan después de que la dirección ya decidió.", 5),
    ("En Praxis se asume que los datos solo sirven para justificar lo ya decidido",
     "", 5),
]
VARIANTS = ["", " en la práctica", " de forma tácita", " aunque nadie lo diga"]


def demo_submissions(n: int = 32, seed: int = 12) -> list[dict]:
    rnd = random.Random(seed)
    subs = []
    weights = [t[3] for t in DEMO_THEMES]
    for i in range(n - len(DEMO_WEAK)):
        sec = SECTIONS[i % 4]
        if sec == "C" and rnd.random() < 0.75:
            theme = DEMO_THEMES[3]  # fractura deliberada en la sección C
        else:
            theme = rnd.choices(DEMO_THEMES, weights=weights)[0]
        evid = theme[1] if rnd.random() > 0.12 else ""
        subs.append({
            "seccion": sec,
            "supuesto": theme[0] + rnd.choice(VARIANTS),
            "evidencia": evid,
            "intensidad": max(1, min(5, theme[2] + rnd.choice([-1, 0, 0, 1]))),
        })
    for w in DEMO_WEAK:
        subs.append({"seccion": rnd.choice(SECTIONS), "supuesto": w[0], "evidencia": w[1], "intensidad": w[2]})
    return subs
