# Diagnóstico cultural asistido por IA · Caso Praxis

Maqueta (vertical slice) del ejercicio en aula del caso **Praxis (DP 26 C 02)** con el método de la nota técnica **DP 26 N 03**, conforme al PRD v0.2.
Business Data Scientists · Jorge Pérez Colín.

> **Estado:** maqueta funcional. La IA está **simulada** (`engine.mock_ai_analysis`). Los datos demo son **ficticios**. No se capturan datos personales.

## Qué demuestra

| Objetivo del PRD | Cómo lo resuelve la maqueta |
|---|---|
| Cierre → tablero ≤90 s | Cronómetro desde el congelamiento hasta la corrida; se registra en bitácora |
| Preservar el método | Motor de reglas determinístico: umbral, evidencia, fracturas por sección, señales débiles, reglas implícitas |
| Auditar la IA | Auditor de citas: cada hallazgo se verifica contra el corpus congelado (Verificado / Parcial / Rechazado) |
| Proteger el aprendizaje | Apuesta previa bloqueada antes de procesar; Contraste apuesta vs. resultado; criterio humano final |
| Simplificar operación | Una sola app para los cinco roles; sin copiar y pegar entre aplicaciones |
| Proteger privacidad | Sin login, sin nombres ni correos; purga automática a 30 días |
| Plan B | Botón **ACTIVAR RESPALDO**: carga la corrida validada del ensayo y registra la incidencia |

## Máquina de estados

```
PREPARADA → CAPTURANDO → CORPUS CONGELADO → PROCESANDO → SALA → INSTRUMENTO → CONTRASTE → CRITERIO → BITÁCORA
```

## Roles (selector en la barra lateral)

- **Participante** — formulario por trío (sección, supuesto "En Praxis se asume que…", evidencia, intensidad).
- **Operador** — abre captura, congela corpus, procesa, avanza fases, Plan B, umbral, datos demo.
- **Conductor** — apuesta previa, lectura para conducir, registro del criterio humano.
- **Proyección** — vista 16:9 que sigue la fase y se refresca cada 2 s (Muro, Sala, Instrumento, Contraste, Criterio, Bitácora).
- **Moderador remoto** — solo lectura.

## Ejecutar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
# varios dispositivos en la misma red (celulares de los participantes):
streamlit run app.py --server.address 0.0.0.0
```

### Demo en 2 minutos
1. Rol **Operador** → *Abrir captura* → *Configuración* → *Cargar 32 envíos demo*.
2. Rol **Conductor** → registrar una apuesta.
3. **Operador** → *Congelar corpus* → *Procesar*.
4. Rol **Proyección** (otra pestaña) mientras el Operador avanza: Sala → Instrumento → Contraste → Criterio → Bitácora.

## Publicar en Streamlit Community Cloud

1. Entrar a [share.streamlit.io](https://share.streamlit.io) con la cuenta de GitHub.
2. *Create app* → repositorio `praxis-cultural-diagnostic`, rama `main`, archivo `app.py`.
3. Nota: SQLite en Community Cloud es efímero (se reinicia con la app). Suficiente para ensayo; para la sesión real usar almacenamiento persistente.

## Arquitectura

```
app.py      UI por rol, orquestador de estados, SQLite, bitácora, Plan B
engine.py   Motor de reglas · Motor IA (simulado) · Auditor de citas · datos demo
```

```
Corpus congelado ──┬── Motor determinístico ──┐
                   └── LLM (simulado) ─────────┴─► Auditor de citas ─► Tablero
```

## Sustituir la IA simulada por un LLM real

`engine.mock_ai_analysis(reglas, corpus)` define el contrato: devolver una lista de hallazgos
`{"tipo", "texto", "citas": [ids]}`. Un LLM real (OpenAI, Azure OpenAI, Anthropic) debe recibir el corpus
congelado + el resultado de reglas y devolver JSON con ese esquema. El auditor no cambia.
Credenciales en `.streamlit/secrets.toml` (excluido del repo).

## Pendientes antes del Go/No-Go (9 oct 2026)

- Permiso del IPADE para procesar caso y nota con IA (condición del PRD).
- Conectar LLM real y medir latencia "primera salida IA" (<30 s).
- Prueba de carga: 40 tríos en 4 minutos.
- Validar el formulario contra la *Hoja del participante* y los textos contra el *Guion del conductor*.
