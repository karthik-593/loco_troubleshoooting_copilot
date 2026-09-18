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

Gate mechanisms encoded so far (101 fault files): **reset-limit** (QLM: once only; a stated prior reset *or*
a re-lock after the permitted reset — both refuse, and the recurrence is detected by the
engine itself, not the parser), **isolate-then-reset** (QLM with QOP/QRSI or QLA/QOA),
**hazard-exposure** (pantograph roof work gated on the OHE power block + earthing and
loco grounding; HT-compartment entry gated on grounding; **relay / contactor wedging** gated
on the TSD's stated preconditions — EM contactors open for Q118, TLC permission + GR
efficiency test for Q44, no Operation 'A' ending trouble for Q45, the concerned switch on 3
for C105/C106/C107), the gate-free **isolate-and-retest** and **isolate-by-elimination**
ladders (QRSI-1/2, Operations B-I / O / I), **hub-and-route intake** ("DJ tripped" → the
§5.01 precheck and observation drill → the tripping failure the sign names, confirmed in one
line before any guidance; "BP dropped suddenly" → §9.04.1–2 → the cause the pilot names),
the **continuity-test gate** on moving the train after a cattle run-over (§9.04.3 Note 3), the
Ch.9 **pneumatic ladders** (RS / MR / BP / FP pressure, loco brakes, BP not dropping / rising)
with their stated-only after-attaching / light-engine / banker branches, the **fuse-handling
gate** (a melted RSI tell-tale fuse, or any fuse in its socket, comes out only with DJ open,
panto down and HBA off — §10.08, §11.01, §13.12), the **relay-work gate** (pressing a relay by
hand, wedging it, or cleaning its interlocks — §12.01, §12.03, §13.13), and the general
**smoke/fire response** procedure. Chapters 11–13 are encoded as **reference procedures** —
the isolations, wedgings and special instructions the fault files have been citing all along
(isolate a TM / battery / RSI block, ground the loco, earth the OHE, renew a fuse, EEC, manual
GR, VCD) — carrying the same gates and the same intents as the faults that point at them, at a
lower precedence so a reported fault always outranks a how-to.

The knowledge base is **closed at Chapters 5–13** of the TSD (intake, safety relays, tripping
failures, traction failures, pneumatic failures, miscellaneous failures, and the isolation /
wedging / special-instruction reference procedures). The microprocessor and 3-phase locos, the
SIV's internal fault tree, MU / double-head / banker working and the circuit diagrams are out of
scope by design (BUILD_PLAN §15); Ch.19 (air brake train troubles) was offered and deliberately
left out.

## Evaluation

30 scripted-pilot scenarios (`eval/scenarios/`) — pilot did it right, pilot missed a
step, pilot's next move is unsafe, ambiguous intake, combination fault, config axis —
scored against a **flat-retrieval baseline that has the same KB content** and only
recites it. Live run (DeepSeek parse/decide, Claude phrase):

| Metric | Agent | Flat baseline |
|---|---|---|
| Unsafe-instruction rate (target 0) | **0.00** | 0.50 |
| Missed-gate rate (target 0) | **0.00** | 1.00 |
| Specific-miss detection | 1.00 | 0.00 |
| Correct-terminal rate | 1.00 | 0.00 |
| Distinct tool paths (proof of agency) | 15 | 1 |

The baseline's unsafe rate is not a strawman: it prints the correct procedure, which says
"reset QLM" / "climb on the roof" unconditionally and can never ask about the log book or
the power block. Where the agent merely ties it (the baseline recites the right step
somewhere, 17%), the report says so. Clean linear faults take a one-tool path; the agency
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
| DJ tripped on line (intake) | §5.01 / Ch.7 intro | hub: nine-point precheck, side-notes, observation drill; routes on the reported sign; dropped relay → its procedure |
| ICDJ + 7 branches | §7.01.1–7.01.8 | hub in Note-2 order (C118 closing first); branch chain Q118 → Q45 → Q44; manual energisation outcomes; **wedge Q118 / Q44 gates** |
| No tension · Op A beginning · Op A ending (+ part II) | §7.02 / 7.03 / 7.04 / 7.11 | BP-drop branches; relay reroute; relief-engine defer; the re-glow question (VCB 5-branch?) |
| Op B part 1 · Op O · Op I | §7.05 / 7.07 / 7.08 | isolate-by-elimination ladders (one switch back to 1, 15/30 s wait, the tripping switch names the defect) |
| Op B part II | §7.06 | single gated action: **wedge Q45** (contraindicated by Op A ending trouble) |
| Op II + 3 branches | §7.09.1–7.09.4 | hub on contactor state; wedge Q100 ordinary (no stated precondition); **contactor wedge gate** (switch on 3); minimum-contactor working |
| TWAC | §7.10 | 13-step ladder with two wedge gates in TSD order |
| Total loss of tractive effort (intake) + with LSB / without LSB / with GR progression | §8.01–8.03 | hub on the lamp / needle sign; Q50 wedge as an ordinary step with its after-precautions; Q52 / QRS relay branches with their before-checks asked as steps |
| Auto regression with LSP · 1st-notch auto regression without LSP | §8.04 (+8.04.1) / §8.05 | meter-branch question (A3 / A4 / U2 / U5 → HMCS position); stated reasons (slipped pinion, locked axle) served first; §8.04.1 symptoms folded in |
| Partial loss of tractive effort | §8.06 | HMCS positions → air leak → isolate TM-1 / TM-6 on U1 / U6 |
| CCPT melting | §8.07 | one file keyed on the occasion (15 values, KB fact phrases); the Ch.7 Q118 / Q44 wedge gates reused |
| Pneumatic failures: RS / MR / BP / FP pressure, sudden BP drop (intake + 4 causes), AFI overshoot, loco brakes (SA9 / A9 / not releasing), BP rising / not dropping | §9.01–9.10 | hub routing on the reported cause; **continuity-test gate** after a cattle run-over; stated-only after-attaching / dead-loco / light-engine / banker branches; "BP below 5" reroutes to §9.03 |
| MCPA not working · panto not raising · all pilot lamps · LSDJ / LSCHBA / LSGR / LSRSI · UA meter · head light · flasher · horns · auto regression during RB · wheel skidding · loco not moving on 1st notch | §10.01–10.16 | **fuse-removal gate** on §10.08; the HBA-'0' test picks CHBA vs QV61; lamp routes into §10.04; low battery routes into §11.06; locked axle defers |
| Reference procedures: isolation (RSI block, TM, MCP, battery charger, battery, RGCP, blower relays), wedging (EM contactors, relays), special instructions (EEC, manual GR, rear cab, without pilot lamps, grounding, OHE earthing, fire precautions, first aid, emergency telephone, power block, speedometer, fuse renewal, relay cleaning, SS2 dummying, BPEMS, VCD) | §11.01–11.08 / §12.01–12.03 / §13.01–13.16 | precedence −1 (a reported fault outranks a how-to); the same hazard gates and intents as the faults that cite them; §11.02's reverser-bit table on the lazily-asked RB axis |

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
| Tests — 305 offline (reflex, loop guards, recurrence, graph traces, API, eval harness) + 89 live parse cases | `tests/` |

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
