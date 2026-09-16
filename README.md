# Loco Troubleshooting Verification Copilot

A cab-side copilot for **conventional AC electric locomotives** (WAG-5, WAG-7, WAP-1,
WAP-4, WAM-4) — locos that emit **no fault codes**. The pilot describes the fault and what
they have already done; a LangGraph agent verifies that against the railway's
troubleshooting directory, trusts completed steps, flags only what was missed, and
**refuses unsafe actions** — such as a forbidden second relay reset — through a
deterministic safety reflex that no language model can skip or override.

> **Safety disclaimer.** This is a demonstrator, not a certified system. It is not
> approved for operational use by Indian Railways or anyone else. Real deployment would
> need IR approval, safety certification and liability review. Nothing here replaces the
> printed troubleshooting directory, the TLC, or the loco pilot's judgement.

## Why this is a copilot and not a lookup

Modern locos print a fault code, so an assistant would be a lookup. Conventional locos
are diagnosed by physical test and equipment isolation, from a paper directory, under
time pressure, mid-section. This copilot **diffs the pilot's account against the standard
procedure and speaks only for the delta**: a confirmation, one targeted question, a
caution, or a refusal. A competent pilot is never made to re-answer for steps they
already stated.

```
Pilot:   QLM dropped. I checked the transformer and oil level.
Copilot: Have you checked the CGR arc chutes, RGR and RPGR for red-hot condition, and the
         TFR terminals, bushings, HT cable, TFILM, TFSPM, and breathers for smell, smoke,
         fire, or oil leakage?
Pilot:   Checked, all normal. I reset QLM once earlier this trip.
Copilot: Do not reset that relay again — it was already reset once earlier this trip.
         Mark it clearly in the loco log book, inform TLC, and request a relief loco.
         [refuse · reset_limit · path: (reflex short-circuit) · §6.1.1(f)]
```

The second turn is the point of the build: the refusal came from the engine's safety
reflex **before any tool was chosen**; the agent was never consulted. Full transcripts of
five showcase conversations, run live, are in [docs/walkthrough.md](docs/walkthrough.md).

## The division of labour

> **The agent decides WHICH tool to invoke. The deterministic engine decides WHAT the
> safety and procedure result is.**

- **Safety is a reflex, not a tool.** `evaluate_gates` runs after *every* state update —
  after parse and after every tool — as a graph node on every path. If a gate fires the
  loop short-circuits to a terminal; the agent cannot reach the pilot except through it.
  The guarantee is therefore independent of the agent's choices *and* of model quality.
- **The LLM has exactly three bounded jobs** — `parse` (free text → KB-validated update),
  `agent_decide` (pick one registered diagnostic tool, or none), `phrase` (render the
  engine's chosen output). It never originates or alters a verdict and never invents
  procedure content: `engine/` imports nothing from `llm/` (enforced by a test), and the
  phrase output passes a deterministic guard that falls back to the KB's own words on any
  drift — a refusal rewritten as an instruction never reaches the pilot.
- **The loop is bounded** — max-iteration cap, idempotent tools, a no-progress detector,
  and a registry-only toolset; every guard degrades to the engine's deterministic terminal.
- **Out-of-scope is a refusal, not a guess.** A problem outside the procedure set (headlight,
  brakes, a relay not encoded) gets "this isn't in my procedure set — contact TLC" plus the
  list of faults it *can* verify; a vague message is clarified once, and the engine defers
  on the next unresolved turn rather than repeat the question (BUILD_PLAN §5.6).

Gate mechanisms encoded so far (15 faults): **reset-limit** (QLM: once only; a stated prior reset *or*
a re-lock after the permitted reset — both refuse, and the recurrence is detected by the
engine itself, not the parser), **isolate-then-reset** (QLM with QOP/QRSI or QLA/QOA),
**hazard-exposure** (pantograph roof work gated on the OHE power block + earthing and
loco grounding), the gate-free **isolate-and-retest** ladders (QRSI-1/QRSI-2, with alternative
branches), and the general **smoke/fire response** procedure.

## Evaluation

17 scripted-pilot scenarios (`eval/scenarios/`) — pilot did it right, pilot missed a
step, pilot's next move is unsafe, ambiguous intake, combination fault, config axis —
scored against a **flat-retrieval baseline that has the same KB content** and only
recites it. Live run (DeepSeek parse/decide, Claude phrase):

| Metric | Agent | Flat baseline |
|---|---|---|
| Unsafe-instruction rate (target 0) | **0.00** | 0.54 |
| Missed-gate rate (target 0) | **0.00** | 1.00 |
| Specific-miss detection | 1.00 | 0.00 |
| Correct-terminal rate | 1.00 | 0.08 |
| Distinct tool paths (proof of agency) | 9 | 1 |

The baseline's unsafe rate is not a strawman: it prints the correct procedure, which says
"reset QLM" / "climb on the roof" unconditionally and can never ask about the log book or
the power block. Where the agent merely ties it (the baseline recites the right step
somewhere, 31%), the report says so. Clean linear faults take a one-tool path; the agency
is load-bearing in the branching cases, as the distinct-paths count shows.

**Safety as a CI gate:** every push replays the suite offline (scripted parses, no
credentials) and the build is red unless unsafe-instruction rate = 0 and missed-gate
rate = 0. `dvc repro` reproduces validate_kb → eval → report; `python -m eval.report
--mlflow` logs params (models, KB SHA), metrics and artifacts to a local MLflow store.
Report: [eval/out/report_live.md](eval/out/report_live.md).

## Knowledge source

All procedure content comes from one public document and nothing else:

**SCR/ETTC Operating Manual & Trouble Shooting Directory (Rev-2, 2019)**
<https://scr.indianrailways.gov.in/cris//uploads/files/1566969531009-ETTC_TSD.pdf>

Procedures are encoded as *facts* (step logic, gates, terminals) in `kb/faults/*.yaml`;
every file, step, gate and clause cites its TSD section, and where a consequence had to be
inferred by analogy the provenance line says so. The schema (`kb/schema.py`) enforces the
citations in CI. The PDF is DVC-tracked and kept out of git.

| Fault | TSD | Mechanism |
|---|---|---|
| QLM dropped | §6.01 / 6.1.1 | reset-limit gate; recurrence; (c)-specific fire action |
| QLM with QOP/QRSI · QLM with QLA/QOA | §6.1.2 / 6.1.3 | combination reroute; isolate-then-reset |
| Sanders not working | §10.12 | benign, gate-free, no confirmation turn |
| Pantograph damaged | §10.03 / 11.04 | hazard-exposure gate |
| QRSI-1 / QRSI-2 drop on run | §6.02.1 / §6.02.2 | isolate-and-retest ladder, resume-from-stuck; alternative branches |
| Smoke or fire on any equipment | Ch.1 B.1–B.12 / Ch.4 item 6 | general fire response, gate-free |
| QOP-1 / QOP-2 drop (target resets) | §6.03.1 / 6.03.2 | isolate-and-retest ladder; fact-keyed reroute when the target will not reset |
| QOP-1 / QOP-2 target not resetting | §6.03.3 / 6.03.4 + GI 7, §13.05, §11.02 | **HT-compartment hazard gate** (loco grounded); reverser-bit table on the lazily-asked RB axis |
| QOA drops (resets / not resetting) | §6.04.1 / 6.04.2 | aux-circuit ladder; one-switch-at-a-time isolation |
| QLA drops on run | §6.05 | reset-limit gate on its own key; second act → TLC |

## Layout

| Piece | Where |
|---|---|
| Knowledge base + schema / `validate_kb` | `kb/` |
| Deterministic engine: state, diff, **safety reflex**, router, terminals, Class-A tools | `engine/` |
| LLM layer: swappable providers, parse / decide / phrase, prompts, output guard | `llm/` |
| LangGraph loop + session store | `agent/` |
| FastAPI `POST /diagnose` (+ session bar: loco number, class, SIV/ARNO, leading/trailing) | `api/` |
| Streamlit chat client showing the engine trace | `client/` |
| Scenario suite, harness, baseline, report | `eval/` |
| Showcase walkthrough generator | `scripts/demo.py` |
| Tests — 230 offline (reflex, loop guards, recurrence, graph traces, API, eval harness) + 40 live parse cases | `tests/` |

Design spec: `BUILD_PLAN.md`. Decisions, provenance notes and open items: `HANDOFF.md`.

## Run

```bash
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -r requirements.txt
python -m kb.schema                                   # validate the knowledge base
python -m pytest                                      # offline suite (no credentials)
python -m eval.harness --mode offline --assert-safe   # the CI safety gate
# with ANTHROPIC_AGENTIC_AI_PROJECT_KEY / DEEPSEEK_AGENTIC_AI_PROJECT_KEY in the environment:
python -m pytest tests/test_parse_live.py -m live     # live parse set
python -m eval.harness --mode live --out eval/out/results_live.json
python -m scripts.demo                                # regenerate docs/walkthrough.md
uvicorn api.server:app                                # API on :8000 (terminal 1; GET / points to the UI)
streamlit run client/streamlit_app.py                 # chat client on :8501 (terminal 2; COPILOT_API_URL to override)
dvc pull                                              # TSD PDF from the configured DVC remote (local folder)
```

Models are pinned explicitly on every call: `deepseek-flash` (non-thinking, temperature 0)
for parse and agent_decide, `claude-haiku-4-5-20251001` for phrase.
