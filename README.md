# Loco Troubleshooting Verification Copilot

A cab-side verification copilot for **conventional AC locomotives** (WAG-5, WAG-7, WAP-1,
WAP-4, WAM-4) — locos that emit no fault codes. The pilot describes the fault and what
they have already done; the copilot verifies that against the railway's troubleshooting
directory, trusts completed steps, flags only what was missed, and **refuses unsafe
actions** (e.g. a forbidden second QLM reset) via a deterministic safety reflex that no
language model can skip or override.

> **Safety disclaimer.** This is a demonstrator, not a certified system. It is not
> approved for operational use by Indian Railways or anyone else. Real deployment would
> require IR approval, safety certification and liability review. Nothing here replaces
> the printed troubleshooting directory, the TLC, or the loco pilot's judgement.

## Knowledge source

All procedure content is encoded from a single public document and nothing else:

**SCR/ETTC Operating Manual & Trouble Shooting Directory (Rev-2, 2019)**
<https://scr.indianrailways.gov.in/cris//uploads/files/1566969531009-ETTC_TSD.pdf>

Procedures are encoded as *facts* (step logic, gates, terminals) in `kb/faults/*.yaml`;
every file and every step cites its TSD section. The schema (`kb/schema.py`) enforces
this in CI. The PDF itself is DVC-tracked (`1566969531009-ETTC_TSD.pdf.dvc`) and kept
out of git.

## Evaluation (Milestone 5)

13 scripted-pilot scenarios (`eval/scenarios/`) across BUILD_PLAN §12's classes — pilot did
it right, pilot missed a step, pilot's next move is unsafe, ambiguous intake, combination
fault, config axis — scored against a **flat-retrieval baseline that has the same KB
content** and only recites it. Live run (DeepSeek parse/decide, Claude phrase):

| Metric | Agent | Flat baseline |
|---|---|---|
| Unsafe-instruction rate (target 0) | **0.00** | 0.54 |
| Missed-gate rate (target 0) | **0.00** | 1.00 |
| Specific-miss detection | 1.00 | 0.00 |
| Correct-terminal rate | 1.00 | 0.08 |
| Distinct tool paths (proof of agency) | 7 | 1 |

The baseline's unsafe rate is not a strawman: it prints the correct procedure, which says
"reset QLM" / "climb on the roof" unconditionally. Where the agent merely ties it (the
baseline recites the right step somewhere, 31%), the report says so.

**Safety as a CI gate:** every push replays the suite offline (scripted parses, no
credentials) and the build is red unless unsafe-instruction rate = 0 and missed-gate
rate = 0 (`python -m eval.harness --mode offline --assert-safe`). `dvc repro` reproduces
validate_kb → eval → report; `python -m eval.report --mlflow` logs params (models, KB SHA),
metrics and artifacts to a local MLflow file store. Full report: `eval/out/report_live.md`.

## Status — Milestone 5 (six TSD faults, all mechanisms, agent loop, API, client, evaluation)

| Piece | Where |
|---|---|
| Fault knowledge base — QLM, QLM+QOP/QRSI, QLM+QLA/QOA, sanders, pantograph damaged (hazard gate), QRSI-1 (isolate-and-retest) — each cites its TSD section | `kb/faults/*.yaml` |
| KB schema + `validate_kb` CI check | `kb/schema.py` |
| KB loader + alias match (LLM match stubbed for M2) | `engine/matcher.py` |
| Conversation state (`DiagnosisState`) | `engine/state.py` |
| Claimed-vs-required delta | `engine/diff.py` |
| **Safety reflex** — deterministic, runs after every state update | `engine/gates.py` |
| Router + loop-guard helpers | `engine/reassess.py` |
| Terminals (confirm / ask / caution / refuse / defer) | `engine/terminals.py` |
| M1 single-pass driver | `engine/run_turn.py` |
| Class-A diagnostic tools (the bounded set the agent may pick) | `engine/tools.py` |
| Swappable LLM providers — DeepSeek (`deepseek-flash`, non-thinking) for parse/decide, Claude (`claude-haiku-4-5-20251001`) for phrase, plus a scripted fake | `llm/interface.py` |
| `parse` — free text → KB-validated update, low confidence → clarify | `llm/parse.py` |
| `agent_decide` — picks one registered tool or none; safety not selectable | `llm/decide.py` |
| `phrase` — renders the engine's terminal; guard + verbatim-KB fallback | `llm/phrase.py` |
| Prompt templates (versioned) | `llm/prompts/` |
| **LangGraph agent loop** — reflex after every state update, bounded `agent_decide → execute_tool` cycle | `agent/graph.py` |
| Session store (per-pilot state + trace) | `agent/session.py` |
| FastAPI `POST /diagnose` (+ `/session/{id}`, `/health`) | `api/server.py` |
| Streamlit chat client showing the engine trace | `client/streamlit_app.py` |
| Session loco context — loco number, class (WAG-7/WAG-5/WAP-4), config (SIV/ARNO) per loco, leading/trailing, swap | `engine/state.py`, API, client sidebar |
| Eval: scenario suite, harness (offline + live), flat-retrieval baseline, report + MLflow | `eval/` |
| Tests (181 offline incl. loop guards, graph traces, recurrence, API, eval harness; 18 live parse cases) | `tests/` |

The LLM has exactly three bounded jobs — parse, decide, phrase — and none of them can
originate, alter, or skip a safety verdict: the engine package imports nothing from
`llm/` (enforced by a test), and the reflex is a graph node on every path out of a state
update — after parse and after every tool — so `agent_decide` cannot reach the pilot
except through it. If a gate fires the loop short-circuits and the agent is never
consulted (`tests/test_loop_guards.py::test_skip_attempt_guard_agent_never_consulted_when_gate_fires`).
The loop is bounded: max-iteration cap, idempotent tools, a no-progress detector, and a
registry-only toolset; every guard degrades to the engine's deterministic terminal.

Design spec: `BUILD_PLAN.md`. Working notes and locked decisions: `HANDOFF.md`.

## Run

```bash
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -r requirements.txt
python -m kb.schema      # validate the knowledge base
python -m pytest         # engine + reflex + LLM-layer + loop + API tests (scripted model)
python -m eval.harness --mode offline --assert-safe   # CI safety gate (no credentials)
python -m eval.harness --mode live --out eval/out/results_live.json   # live suite
dvc repro                # validate_kb → eval → report; dvc metrics show
uvicorn api.server:app   # API on :8000 (needs the two credential env vars)
streamlit run client/streamlit_app.py   # chat client against the API
# with ANTHROPIC_AGENTIC_AI_PROJECT_KEY / DEEPSEEK_AGENTIC_AI_PROJECT_KEY set in the environment:
python -m pytest tests/test_parse_live.py -m live -s
dvc pull                 # (once a DVC remote is configured) fetch the TSD PDF
```
