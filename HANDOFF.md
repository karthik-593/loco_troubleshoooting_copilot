# M1 HANDOFF — Loco Troubleshooting Verification Copilot

Read this **after** `BUILD_PLAN.md`. It records the decisions already locked in an
earlier working session so you don't re-derive or contradict them, and it scopes
Milestone 1.

---

## Status
- **Milestone: M6 — demo + write-up (2026-09-15).** 182 offline tests; 18/18 live parse; suite
  13/13 offline and live (unsafe 0, missed-gate 0, 7 paths). `scripts/demo.py` regenerates
  `docs/walkthrough.md` (five showcase conversations, live, with per-turn engine trace) and
  `docs/walkthrough_offline.md`; README rewritten around BUILD_PLAN §17 with the metrics
  table, safety architecture, disclaimer and source URL. M6 fix: a confirm reached via
  `fault_resolved` on a reset-gated fault now carries the permitted reset's follow-up
  (10-min checks, log, TLC). **Optional / not done:** recorded video walkthrough (the
  transcript stands in), QRSI-2 (§6.02.2, quote-first), `fire_on_loco` (Ch.1 pp43–44 / Ch.4
  item 6, quote-first), a genuine SIV/ARNO-branching fault (none found in the TSD yet),
  `transformer_rating` axis, DVC remote for the PDF.
- **Milestone: M5 — evaluation (2026-09-15).** 181 offline tests; 18/18 live parse; scenario
  suite 13/13 offline AND live with unsafe = 0, missed-gate = 0, 7 distinct tool paths.
  Session loco context (loco_number/type/config, leading+trailing, swap; `type_dependency`
  axis) also landed 2026-09-15. M4b/M4a/M3: 2026-09-15.
- **M5 decisions:**
  - `eval/scenarios/*.yaml`: §12.1 fields + per-turn `structured` (the parse the M2 parser
    would produce) so the suite replays OFFLINE; `expected_turn_terminals` catches wrong
    intermediate turns (this exposed the instructed-reset bug below). Classes:
    did_it_right ×2, missed_step ×3, unsafe ×5, ambiguous, combination, config.
  - `eval/harness.py`: offline mode = ScriptedParse + PolicyDecider (diff, then none) +
    verbatim phrase; live mode = real providers. `--assert-safe` exits 1 unless unsafe = 0 and
    missed-gate = 0 — wired into CI (`.github/workflows/ci.yml`) with the report uploaded as
    an artifact. `must_not_do` vocabulary: instruct_reset, confirm, confirm_fault, refuse,
    ask_config, instruct_roof_work, any_procedure, confirm_after_recurrence.
  - `eval/baseline.py`: flat retrieval bot — alias-matches, recites the whole procedure;
    scored on the same gold (unsafe 0.54, missed-gate 1.0, specific-miss 0, recites the right
    step 0.31). `eval/report.py`: agent-vs-baseline table, tool-path divergence, per-scenario
    traces, honest notes; `--mlflow` logs to a local file store (`MLFLOW_ALLOW_FILE_STORE`
    set in code; mlflow-skinny). `dvc.yaml`: validate_kb → eval → report; `dvc metrics show`.
  - **Bug found by the live suite and fixed:** "reset done, resumed" was parsed as
    `was_QLM_reset_earlier_this_trip=yes` → refused as a second reset. Engine rule in
    `update_state`: while a first reset is instructed and the relay is not presenting again, a
    same-turn 'yes' on the prior-reset fact is taken as the INSTRUCTED reset
    (`reset_performed_this_session`), not a prior one; parser context line clarified. A
    genuine prior reset stated with a presenting fault still refuses (tests).
  - Live "expected tool path" is 0.85: the real agent chose `none` (no tool) on the clean
    confirm case and the engine confirmed anyway — valid, reported, not hidden.
- **M4b KB additions — PENDING HUMAN CONFIRMATION:**
  - `kb/faults/pantograph_damaged.yaml` (§10.03 pp.164–165 incl. "Obtaining emergency power
    block"; §11.04 p.178). Ordinary: lower panto → BP/protect → contact TPC; **gated**
    `secure_damaged_pantograph_on_roof` = `hazard_exposure` with preconditions
    `ohe_power_block_obtained_and_earthed` + `loco_grounded` (EPB (c); §11.04 1–2), in play on
    intent `work_on_roof` or when it is the next step; then HPT earthing clip → clear roof /
    un-ground / raise good panto → resume + TLC + log (completes). `defer_conditions`:
    `pantograph_not_lowered` (§10.03(a) stop/protect/TLC), `both_pantographs_damaged` (h).
  - `kb/faults/qrsi1_drops_on_run.yaml` (§6.02 / §6.02.1 pp.86–87). Gate-free (no reset-once
    rule: (b) permits a further reset after a long interval). (a) circuit check with
    `abnormality_key` + isolation block; ladder (b)|(c)→(d)|(e) as `applies_when` branches on
    facts `drops_after_long_interval` / `drops_frequently` / `drops_in_particular_hmcs1_position`
    / `drops_in_all_hmcs1_positions`; (d),(e) `completes: true`; defer `load_and_road_do_not_permit` (f).
    **Interpretation flagged in the file:** post-isolation action on (a) is not stated by the TSD
    ("try to isolate the same, otherwise contact TLC"); encoded as returning to (a)'s
    no-abnormality path (reset, accelerate gradually), by analogy with §6.1.2(b). Confirm or trim.
    **Config axis — RECORDED, not built (user decision 2026-09-15):** `transformer_rating`
    (3900 | 5400 kVA) is a known future config axis, distinct from SIV/ARNO. TFR terminal
    designations depend on it: 3900 kVA → a5/a6 on QRSI-1, a3/a4 on QRSI-2; 5400 kVA → reversed
    (§6.02.1(a), §6.02.2(a)). Applies to QRSI-1/2 and any TFR-terminal-referencing fault. Capture
    per loco (session bar) when equipment-position faults land; for V1 both designations stay in
    the step text and every YAML keeps `config_dependency: none` (confirmed).
    **QRSI-2 (§6.02.2, pp.86–87) — flagged M6-optional, NOT encoded:** mirror of QRSI-1 — truck 2,
    TMs 4/5/6, HMCS-2, HVSI-2/HVMT-2/HVSL-2, TFR terminals swapped (a3/a4 on 3900 kVA; a5/a6 on
    5400 kVA), no (f)-style separate TLC clause ((e) carries it). Trivial from the QRSI-1 template;
    encode only after per-step §6.02.2(a)–(e) provenance is quoted and confirmed.
- **M4b engine decisions:** `hazard_exposure` evaluator (`H-caution` unstated → proactive
  precondition statement; `H-no` → REFUSE; all yes → NO_FIRE and reassess asks the gated step
  itself, step 3b; claimed without preconditions → `H-ask`, never a confirmation). Diff is now
  ordered: steps after an unclaimed gated step are not due; `applies_when` skips a branch only
  when a STATED fact contradicts it. Generic `Fault.defer_conditions` ("if <situation>, contact
  TLC" → defer with the clause text). Gate-free isolate-then-reset handled in reassess 2e.
  `Step.completes` → 'resolved' terminal when the last claimed step completes the procedure.
  Actions vocabulary: `reset_QLM`, `work_on_roof`. `isolation_before_contact` still fails closed
  (no TSD section so far needed it: in §6.02.1 isolation is the remedy, not a precondition).
  Live: panto intent → proactive caution, agent never consulted; preconditions → ask the roof
  step. QRSI frequent/position-2 → skips (b), isolates TM → resolved (5/6 load); SL-1 burning,
  not isolable → TLC.
- **M4a KB additions — PENDING HUMAN CONFIRMATION (review against the TSD before treating as
  locked):** `kb/faults/qlm_with_qop_qrsi.yaml` (§6.1.2), `kb/faults/qlm_with_qla_qoa.yaml`
  (§6.1.3), `kb/faults/sanders_not_working.yaml` (§10.12). The combination faults reuse QLM's
  three step ids for "(a) follow 6.1.1" (claims carry across a reroute), add the circuit-(b)
  step with its own `abnormality_key` + `isolation:` block, and a `reset_decision` gate for
  (c)/(d). **Confirmed by the user 2026-09-15:** `on_first_reset` inherits §6.1.1(d)(e)
  (10-min feeding-circuit checks, log book, TLC) via §6.1.2(a)/§6.1.3(a) — keep, no trim.
- **§6.1.1(f) precision pass (user-directed; both fixes approved and implemented):**
  - **(f)(ii) recurrence.** Previously the gate knew one fact (`was_QLM_reset_earlier_this_trip`)
    and could not tell "reset twice" from "reset once, then QLM re-locked" — a re-trip after a
    valid first reset could be met with a second "reset once" caution. Now: `fault_recurred`
    fact; rule 1 fires on EITHER a stated prior reset (`second_reset`) OR recurrence
    (`recurred_after_reset`), same (f) text, engine framing sentence prepended for recurrence.
    **PRIMARY mechanism = engine backstop in `update_state`:** once a reset was performed
    (`reset_performed_this_session`, set when the gated step is claimed) or instructed
    (`reset_instructed_this_session`, set by `reassess` when it emits the first-reset caution),
    any later message that PRESENTS the fault (`StateUpdate.fault_presenting` — deterministic on
    an alias hit; else a confident model guess that says so) sets `fault_recurred=yes` in the
    engine regardless of the parser (parser flag = fast path). Biased to over-refuse.
    `llm.parse.same_family()` keeps a re-presentation of "QLM" after a reroute on the RESOLVED
    fault. Phrase guard enforces re-trip framing for the recurrence reason. YAML: gate `rule`
    reworded, `recurrence_history: fault_recurred`, source cites (f)(ii); mirrored to both
    combination faults' (d).
  - **(f)(i) / (c) inline hazard.** (c)'s "use fire extinguisher and ask for Relief Engine" was
    on step (c) but the engine appended it for ANY abnormality. Verified against the TSD text:
    (a) and (b) are pure check clauses with no inline consequence of their own → generic (f)(i).
    New `Step.finding_key` (`arc_chute_terminal_abnormality` on (c)): refuse TRIGGER stays the
    fault-wide `abnormality_found`; (c)'s text attaches only when its finding fact is yes.
    `terminal_actions.abnormality_found` trimmed to "do_not_reset; log; inform TLC; relief loco".
  - Tests: `tests/test_recurrence.py` (9) incl. the four required: recurrence e2e; (a)-vs-(c)
    text; parser-blind recurrence; recurrence across a reroute. Live verified.
- **M4a engine decisions:** schema `Step.abnormality_key` / `Step.isolation` / `Fault.history_keys`;
  reset_limit evaluator with per-step abnormality verdicts and rule 2b isolate-then-reset
  (isolated → CAUTION "5-isolated"; not isolated → REFUSE `abnormality_not_isolated`; unstated
  → ASK); `fault_resolved` → confirm with `terminal_actions.resolved`; **deviation from
  BUILD_PLAN §7 (flagged, awaiting explicit OK):** combination rerouting is deterministic in
  `engine.state.resolve_combination`, after every state update and before the reflex —
  otherwise the reflex can fire under the wrong procedure (seen live). `check_combination`
  remains a Class-A query tool. Parser: `facts{}` for KB-declared keys, per-step
  `_CLAIM_HINTS`, any-fault prefix stripping, `abnormality_found` scoped to the feeding circuit.
- **M3 decisions:**
  - `agent/graph.py`: nodes parse → update_state → reflex → agent_decide → execute_tool →
    reflex_after_tool → reassess → (loop | terminal) → phrase. The reflex is a node on every
    edge out of a state update; a combination reroute (a state change made by reassess) is
    routed back through `reflex_after_tool` before anything else.
  - Loop exit reasons (`stop_reason`): gate | none | max_iter | no_progress | rejected |
    reassess | clarify | reroute. Every non-gate exit lands on the ENGINE's `reassess` terminal.
  - No-progress = the tool's result was a cache hit AND the diagnostic snapshot is unchanged.
  - `reassess_node` loops back to the agent only when there is no fresh diff, or relays were
    reported and `check_combination` has not run on the current state (this is where the
    tool path diverges for combination faults — `tests/test_graph_qlm.py`).
  - Combination reroute to a fault not in the KB → `defer_to_TLC` citing the rule's section.
  - `tool_path` trace per turn is returned by the API and shown by the client (§12.2 evidence).
  - Live check: the Claude phrase path was verified on refuse / conditional caution /
    ask_step-with-hold — all passed the guard unmodified (~1.3 s each).
- **M2 decisions (recorded here so M3 does not re-derive them):**
  - `llm/interface.py`: `LLMProvider` protocol with two ops (`structured`, `text`).
    **Model pins (user decision 2026-09-15, explicit on every call, never an SDK default;
    do not substitute stronger models):**
    `parse` + `agent_decide` → `DeepSeekProvider`, **`deepseek-flash`** (DeepSeek-V4.1-Flash),
    temperature 0, **thinking mode explicitly disabled** (`extra_body={"thinking":{"type":"disabled"}}`,
    verified on the wire by `tests/test_provider.py`), JSON mode with the pydantic schema embedded.
    `phrase` → `AnthropicProvider`, **`claude-haiku-4-5-20251001`**, temperature 0.5, no
    thinking/effort params. `providers_from_env()` is the only vendor binding.
    Credentials: env vars `ANTHROPIC_AGENTIC_AI_PROJECT_KEY` / `DEEPSEEK_AGENTIC_AI_PROJECT_KEY`,
    read only inside `llm/interface.py` (process env, then Windows User-scope registry),
    never logged or written anywhere. `FakeProvider` for tests/offline eval.
  - **Resolved (2026-09-15, later):** the `deepseek-flash` stalls were DeepSeek-side queueing
    (an 8h43m background run eventually got answers); by evening it answers in ~2 s. Also
    seen live: a 200 with `choices: null` → provider now raises a clean RuntimeError.
  - **M2 DoD held-out parse: 10/10 on `deepseek-flash` (non-thinking), twice.** Prompt gained
    one mapping rule (a loosely named equipment check counts for the step that inspects it).
    The `smoke from HT2` case no longer asserts the claimed step (ambiguous; engine REFUSES
    on abnormality either way).
  - **Live §10.2 trace reproduced end-to-end** (DeepSeek parse/decide + Claude phrase):
    Turn 1 ask_step(arc chutes) via diff (4.5 s); Turn 2 refuse via reflex short-circuit,
    agent never consulted (1.9 s); no phrase fallbacks. `engine/matcher.py` now treats the
    fault_id's own words ("QLM dropped") as a deterministic alias (underscore ≡ space).
  - `llm/parse.py`: alias match (exact OR verbatim phrase containment, `engine/matcher.py`)
    is deterministic → fault confirmed, no confirm turn. Model-guessed fault → `fault_confirmed=False`;
    a hard-gated fault then gets the §5.5 one-line `confirm_fault` terminal from `reassess`
    (step 2b) before any step guidance. A REFUSE from the reflex still fires first.
    Confidence < 0.6 → `clarify`, state untouched. Claimed steps validated against the KB;
    unknown ones are surfaced via `unrecognised_claims`, never accepted.
  - `llm/decide.py`: only `engine/tools.py` REGISTRY names or "none"; anything else is
    rejected (recorded in `ToolChoice.rejected`) and treated as "none". The reflex is not
    listed and not selectable.
  - `llm/phrase.py`: deterministic `guard` per terminal kind (refusal must stay negated and
    keep TLC/relief/fire text; caution keeps once/10 min/log/TLC and the conditional; questions
    must ask; hold_action must survive). Any violation → `render_verbatim` (KB text). Provider
    failure → same fallback.
  - `engine/tools.py` (Class A) built early because `decide` needs the registry: diff,
    lookup_procedure, check_combination, get_required_observations, resolve_config,
    read_siv_screen. Idempotent via state-snapshot caching.
  - Prompts are versioned files in `llm/prompts/*.md` (git), built from the KB at runtime.
  - **Pending M2 DoD item:** the held-out free-text set (`tests/data/parse_heldout.yaml`,
    10 messy messages) has not been run against the live model — no credentials on the build
    machine. Run `pytest tests/test_parse_live.py -m live -s` once `ANTHROPIC_API_KEY` is set.
- Layout note: the TSD PDF lives at the repo root under its cited filename and is DVC-tracked
  (`1566969531009-ETTC_TSD.pdf.dvc`); QLM YAML is at `kb/faults/qlm_dropped.yaml`.
- **Scope: QLM only.** Depth-first: prove QLM end-to-end before encoding any other
  fault. Do **not** encode sanders or anything else yet (the BUILD_PLAN §14 M1 DoD
  mentions sanders; it is deliberately deferred — QLM only for now).
- **Procedure source of truth: `1566969531009-ETTC_TSD.pdf`** (the exact file cited
  in BUILD_PLAN §11). It has a real embedded text layer — read it with `pdftotext`.
  Do **not** use the four scanned volumes (image-only, OCR-error-prone); they are retired.

## Locked decisions (human-confirmed — treat as fixed)
1. **QLM is encoded at `kb/faults/qlm_dropped.yaml` and confirmed correct.** Do not
   re-encode. Change it only if a direct TSD re-check contradicts it, and flag first.
   - Both combination rules encoded: §6.1.2 (QLM+QOP/QRSI → `QLM_with_QOP_QRSI`) and
     §6.1.3 (QLM+QLA/QOA → `QLM_with_QLA_QOA`).
   - Schema additions vs the BUILD_PLAN §9 example (approved): per-step `source:`
     field; file-level `source_url:`; `on_abnormality:` on the arc-chute step.
   - `config_dependency: none` — §6.1.1 checks are identical across loco types; only
     the relay set-point amps differ and the pilot never acts on that number.
   - Citation: SCR/ETTC TSD Rev-2 (2019), Ch.6 "DJ Tripping through Safety Relays",
     §6.01 / 6.1.1–6.1.3, printed pp.83–86.

---

## Build now (M1, no LLM)
Per BUILD_PLAN §13 layout:
- `engine/matcher.py` — KB loader + typed `Fault`/`Step`/`Gate`/`CombinationRule`
  objects + deterministic **alias** match. **Free-text / LLM matching is M2 — stub it,
  do not implement.**
- `engine/state.py` — `DiagnosisState` (BUILD_PLAN §10.1 exactly) + `update_state` +
  a `checks_complete(state, fault)` helper.
- `engine/diff.py` — claimed-vs-required delta over the **ordinary (gate-null)** steps:
  `next_unmet`, `missing`, `complete`, `unrecognised`.
- `engine/gates.py` — the **deterministic safety reflex** (§5.1).
  `evaluate_gates(state, fault) -> GateVerdict`. Reset-limit gate only (QLM). Leave
  clear extension points for hazard-exposure / isolation-before-contact (later faults).
- `engine/reassess.py` — deterministic router: reflex short-circuit → diff routing →
  confirm. Include loop-guard constants/helpers (`max_iter`, no-progress, idempotency);
  they are only exercised in M3.
- `engine/terminals.py` — terminal constructors: confirm / ask_step / ask_history /
  caution / refuse / defer_to_TLC.
- `tests/test_gates.py`, `tests/test_diff.py`, `tests/test_scenarios_qlm.py`.

**Built in M2:** `engine/tools.py`, `llm/` (interface, schemas, parse, decide, phrase, prompts).
**Built in M3:** `agent/graph.py`, `agent/session.py`, `api/server.py`, `client/streamlit_app.py`,
`tests/test_loop_guards.py`, `tests/test_graph_qlm.py`, `tests/test_api.py`.
**Built in M4a:** `QLM_with_QOP_QRSI`, `QLM_with_QLA_QOA`, `sanders_not_working` + engine above.
**Built in M4b:** `pantograph_damaged`, `QRSI1_drops_on_run` + engine above.
**Built in M5:** `eval/` (scenarios, harness, baseline, report), `dvc.yaml`/`dvc.lock`, CI safety gate.
**Built in M6:** `scripts/demo.py`, `docs/walkthrough.md` (+ offline), README write-up.
**Open (all optional, all quote-first):** QRSI-2 (§6.02.2), `fire_on_loco`, a genuine
config-dependent fault, `transformer_rating` axis, DVC remote, video walkthrough.

---

## Engine design — REVIEWED 2026-09-14 against TSD §6.1.1 (pp.83–84) and implemented
The proposal below was checked clause-by-clause against the TSD text layer. Rules 1–5 are
each confirmed by §6.1.1(c)–(f). Four amendments were agreed and are what `engine/gates.py`
implements (its module docstring is the authoritative statement):

- **A (behavioural):** REFUSE rules fire on the history *fact* alone — regardless of
  `intended_action` or check completion. §6.1.1(f) refuses on "QLM acts second time", and
  BUILD_PLAN §12.1's gold scenario states no intent. The narrower "only when
  `intended_action == reset_QLM`" would also have let `abnormality_found=yes` with no intent
  fall through to a reset-once confirmation — an unsafe instruction.
  ASK / CAUTION fire when reset is *in play* = ordinary checks complete (reset is then the
  procedure's next step) or the reset step is claimed done. Intent alone never unlocks the gate.
- **B:** if second-reset AND abnormality both hold, the single REFUSE carries both `reasons`
  so the §6.1.1(c) fire-precaution text is never dropped.
- **C:** `abnormality_found` is tri-state; REFUSE only on `yes`. On `unknown` with checks
  complete the CAUTION is `conditional` — worded as §6.1.1(d) words it ("if no abnormality").
- **D:** reset intent with checks incomplete → NO_FIRE (TSD order a→d), but the `ask_step`
  terminal carries `hold_action=reset_QLM` so the phrase layer can say "before resetting…".
- Confirm terminal: all checks + `reset_decision` claimed, history=no → `confirm` +
  `on_first_reset` guidance. Reset claimed but history=yes → REFUSE (f) still applies.

Original proposal, kept for the record:

**Safety reflex = mandatory deterministic node run after every state update (§5.1).**
It is NOT a tool the agent may choose. In M1 (no agent) the single pass is:
`update_state → evaluate_gates → (if fired) terminal ; else diff → reassess → terminal`.

**GateVerdict outcomes:** `NO_FIRE` (defer to normal flow), `ASK`, `CAUTION`, `REFUSE`.

**Reset-limit gate precedence** — applies only when `intended_action == reset_QLM`;
evaluate in this order, all grounded in §6.1.1:
1. `history[was_QLM_reset_earlier_this_trip] == yes` → **REFUSE** (second reset
   forbidden). §6.1.1(f). Dominates everything else.
2. `history[abnormality_found] == yes` → **REFUSE** (do not reset; fire precautions;
   log; TLC; relief). §6.1.1(c)(f).
3. ordinary checks incomplete → **NO_FIRE** (defer; diff asks the next unmet check —
   reset stays gated behind the checks). §6.1.1(d) "if no abnormality [from checks], reset".
4. reset history not stated → **ASK** "Was QLM reset earlier this trip? Check the log
   book." (question derived from the gate's `needs_history`, not TSD prose; endorsed by §2.3).
5. else (history=no, checks complete, no abnormality) → **CAUTION**: reset once +
   resume + check feeding-power items every 10 min + log + inform TLC. §6.1.1(d)(e).

**Reassess routing:**
- gate fired → emit its terminal (refuse / ask / caution).
- else checks incomplete → `ask_step(next_unmet)` (the specific missed check).
- else checks complete, no reset intent → `confirm` + surface the reset-once guidance
  with the one-reset caveat.

---

## MLOps, data versioning & reproducibility
**Honest framing:** no model training here, so "MLOps" means reproducible eval,
versioned data/artifacts, tracked experiments, and CI that enforces the safety bar —
not a train/serve pipeline. Apply the right tool per artifact; do not cargo-cult.

**Artifact → tool:**
| Artifact | Tool | Why |
|---|---|---|
| TSD source PDF (~64 MB binary) | **DVC** | large binary; keep out of git; track hash + provenance (remote = local dir / S3 / GDrive) |
| `kb/faults/*.yaml` | **git** | small, reviewable, wants PR diffs; DVC would hide them. Validated in CI |
| `eval/scenarios/*.yaml` | **git** | small gold text |
| prompt templates (M2) `llm/prompts/*` | **git** | reviewable; log active version to MLflow per run |
| eval runs: metrics, params, tool-traces, report | **MLflow** | experiment tracking (BUILD_PLAN §3, §12) |
| large/derived eval outputs, if they grow | **DVC** (optional) | version alongside the KB/scenario version that produced them |

**Reproducible pipeline (`dvc.yaml`) — no training stages:**
1. `validate_kb` — schema-lint every fault (pydantic/jsonschema): must have
   `source`+`source_url`; every step cites a section; every gate is a known type.
   Fails the build otherwise → mechanically enforces the "cite the TSD" rule.
2. `eval` — run the scripted-pilot harness (agent under test + flat-retrieval baseline)
   over the scenario suite.
3. `report` — emit metrics table + per-scenario tool traces; log to MLflow.
`dvc repro` reproduces eval end-to-end from the pinned KB + scenario versions.

**MLflow (M5, per §12) logs per run:**
- params: model id, prompt version, KB git SHA / DVC hash, scenario-suite version,
  `max_iter`, parse-confidence threshold.
- metrics (§12.2): unsafe-instruction rate (target 0), missed-gate rate (0),
  specific-miss detection, correct-terminal rate, turns-to-clear, tool-path divergence.
- artifacts: per-scenario tool-path traces, agent-vs-baseline table.

**CI (GitHub Actions) — every push:**
- `pytest` (gate tests incl. the §5.1 reflex-fires test, loop guards, diff, matcher).
- `validate_kb` schema check.
- **Safety as a CI gate (from M5):** the unsafe-instruction-rate = 0 assertion on the
  scenario suite must pass or the build is red. For a safety-critical system this is the
  headline maturity signal — state it in the README.

**Set up WHEN (don't derail depth-first M1):**
- **Now (M1):** `git` + `.gitignore` (venv, `__pycache__`, `mlruns/`, DVC-tracked paths);
  pinned `requirements.txt`; `dvc init` + `dvc add 1566969531009-ETTC_TSD.pdf` (so the
  64 MB binary never enters git); local `pytest`; a minimal GitHub Actions workflow
  running pytest + `validate_kb`. `pre-commit` (ruff/black + yaml-lint + kb-schema)
  optional but cheap.
- **M5 (Evaluation):** `dvc.yaml` pipeline, MLflow logging, baseline, full metrics +
  tool-path-divergence report.
- **Do NOT** stand up MLflow or the `dvc.yaml` pipeline now — there is nothing to track
  until the eval exists. DVC-for-the-PDF + CI-for-tests + KB schema validation is the
  only MLOps that pays off at M1.

**Repo additions (layout):**
```
.dvc/                                   # dvc init
1566969531009-ETTC_TSD.pdf.dvc          # pointer; the PDF itself is DVC-tracked + git-ignored
dvc.yaml / dvc.lock                     # M5: validate_kb -> eval -> report
.github/workflows/ci.yml                # pytest + KB schema validation (+ safety-gate assertion at M5)
kb/schema.py (pydantic) or kb/schema.json  # KB validation, enforced in CI
requirements.txt                        # pinned
.gitignore
.pre-commit-config.yaml                 # optional
```

---

## M1 Definition of Done (BUILD_PLAN §14)
- Unit tests pass.
- Structured-input QLM scenarios (no NLP) yield correct **confirm / miss / refuse**.
- `test_gates.py` covers **first-reset (caution)** and **second-reset (refuse)**.
- `test_gates.py` includes a test that **the reflex fires even when no tool requested
  it** (§5.1): build the state directly, call `evaluate_gates`, assert REFUSE.
- Reproduce the §10.2 QLM trace: Turn 1 (miss → ask arc-chute check), Turn 2
  (already-reset + intend reset → REFUSE, with no agent/tool involved).
- M1 MLOps: repo builds green in CI (pytest + `validate_kb`); the TSD PDF is DVC-tracked,
  not in git.

## Working rules
- **Ask before deviating from `BUILD_PLAN.md` on anything.**
- Every encoded fact cites its TSD section. **No procedure content from model knowledge.**
- Safety gates are deterministic and always fire. The (future) LLM never decides a
  safety verdict and never invents procedure content (§5).
- Show the YAML/code at each checkpoint and stop for review before moving on.
