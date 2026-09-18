# M1 HANDOFF — Loco Troubleshooting Verification Copilot

Read this **after** `BUILD_PLAN.md`. It records the decisions already locked in an
earlier working session so you don't re-derive or contradict them, and it scopes
Milestone 1.

---

## Status
- **Post-M6 additions (2026-09-15, all quote-first and user-confirmed):**
  - `kb/faults/qrsi2_drops_on_run.yaml` (§6.02.2 pp.86–87): mirror of QRSI-1 — truck 2, TMs 4/5/6,
    HMCS-2, HVSI-2/HVMT-2/HVSL-2, TFR terminals swapped (a3/a4 on 3900 kVA; a5/a6 on 5400 kVA);
    "particular position" folded into (c), truck isolation (d), TLC clause (e). Same INFERRED
    success-path provenance split as QRSI-1. `config_dependency: none` (both designations in text).
  - `kb/faults/fire_on_loco.yaml` (Ch.1 "Use of fire extinguishers" B.2–B.12 pp.43–44; Ch.4
    item 6 p.74): gate-free general fire response; defer `fire_uncontrollable` (B.9); completes
    on the log-book remark (B.12). Reachable by parser selection / aliases. **Held, not built:**
    an automatic cross-fault jump from an abnormality refusal to this procedure (test
    `test_no_automatic_jump_from_abnormality_refusal_to_fire_procedure` pins the current behaviour).
  - Engine: `Step.implies` — facts that CLAIMING a step establishes (the HMCS ladder is reached
    only via the frequent branch); found live when the parser claimed the HMCS step without
    setting `drops_frequently` and the engine asked clause (b) again. `Fault.precedence` —
    alias-match tie-break so a specific fault named with smoke/fire wording outranks the general
    fire procedure ("QLM locked, smoke coming from the CGR" → QLM_dropped). Clarify wording no
    longer relay-centric. Held-out live parse: 21/21. Scenario suite 13/13 offline + live.
  - **DVC remote configured (user choice: local folder)** — `.dvc/config` remote `localstore` →
    `E:/loco_troubleshooting_dvc_remote`; PDF pushed. A fresh clone on this machine can `dvc pull`.
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
**Built post-M6:** QRSI-2, `fire_on_loco`, DVC local remote.
**Open (all optional, all quote-first):** a genuine config-dependent fault (none found in the
TSD yet), `transformer_rating` axis (recorded), the abnormality→fire cross-fault jump (held),
video walkthrough, mobile client.

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

## Post-M6: out-of-scope exit (BUILD_PLAN §5.6)

Found in live use: a fault outside the KB (headlight, "no traction", QOP alone) looped on the
same clarification every turn. §5.6 says "This isn't in my procedure set — contact TLC."

- `ParseOutput.problem_outside_list` (yes/no/unknown): one bounded classification — is the
  stated problem clearly outside the listed faults? It never selects a procedure. `yes`, or a
  `fault_guess` naming a fault outside the list → `defer_to_TLC` with the §5.6 line plus the
  list of faults the copilot CAN verify (`llm.parse.out_of_scope_reason`); `stop_reason=out_of_scope`.
- Deterministic backstop (primary): `DiagnosisState.clarify_asked`. One clarify per unresolved
  stretch; the next unresolved turn defers. A resolved fault resets it. The same clarification
  is never asked twice.
- Fact keys are now validated against the RESOLVED fault's family only (a QRSI-1 key on a
  QLM-with-QOP message is dropped, not re-mapped), and the parser vocabulary tags each fact
  with its owning fault(s).
- Phrase guard: an out-of-scope defer that loses the "I can verify: ..." list falls back to
  the engine's words (`out_of_scope_dropped_coverage`).
- Held-out live parse set: 24 cases (headlight → out of scope; "qop dropped" and "no traction,
  no relays" pinned as never-acts: the model's scope call is borderline and the backstop
  defers on the next turn either way).

Also: `GET /` on the API points at the Streamlit UI; the client's Apply/Swap buttons show an
"API not reachable" error instead of a traceback.

## Post-M6: one reset booked twice (seen live 2026-09-16; approved before change)

"qlm dropped, i resetted, now working fine" parsed as BOTH `reset_decision` claimed AND
`was_QLM_reset_earlier_this_trip=yes` — one reset counted as the performed reset and as a
prior one — and refused under (f)(ii) with the relief-loco instruction. §6.1.1(e) after the
permitted first reset is log + TLC + continue; relief loco is (f)(ii) only.

- Engine rule (primary, `update_state`, beside the M5 instructed-reset rule): gated reset
  claimed + prior-reset 'yes' + `fault_resolved=yes` in the SAME turn, no reset
  performed/instructed before this turn, no recurrence stated → ONE reset, just performed:
  `reset_performed=yes`, prior-reset fact left unstated → reflex rule 4 ASKS
  "Before the reset you just did, had QLM been reset earlier this trip?" (wording is
  reset-done-aware). 'no' → confirm with §6.1.1(d)(e) follow-up; 'yes' → refuse (second_reset).
  Without `fault_resolved` the prior-reset fact stands and the reflex refuses ("QLM locked.
  yes, reset it once already" — relay live now, reset before): eval gold, over-refuse bias.
  `fault_recurred` ("again", "second time") always refuses.
- Parser fast path: `was_reset_earlier_this_trip` is a reset BEFORE the current occurrence;
  the current drop's reset is the gated-step claim. "only once" / "first time" → no.
- Confirm after a done reset states "The one permitted reset has been done." (engine fact);
  the phrase payload carries an `already_done` cue so the KB guidance is rendered as follow-up
  (resume, 10-minute checks, log, TLC), not as "reset QLM".
- Tests: 4 engine tests (double-booked ask→confirm→recurrence refuse; prior answered yes →
  refuse; recurrence cue → refuse; prior stated without claim → refuse) + the no-resolution
  refusal; gate test rebuilt as two updates plus a direct state-fact test; 3 held-out live
  parse cases (27 total).

## Post-M6: "is this how a copilot behaves?" (live, 2026-09-16)

Seen: after "qlm dropped once. i checked and reset. now working fine. anything to keep
notice of?" the confirm replayed "Reset QLM once and resume..." verbatim; "anything else to
do?" replayed the identical text. Two causes, both fixed deterministically:

1. **Confirm after the permitted reset now answers "what to keep notice of" unasked.**
   `engine/reassess._confirm_after_reset` (both the resolved path 2c and the rule-6 path 4)
   states the engine fact "The one permitted reset has been done." (`terminals.RESET_DONE_NOTE`)
   and carries TWO KB texts: the gate's `after_first_reset` (§6.1.1(d)(e) follow-up worded for
   a reset already done — NEW YAML FIELD on the locked QLM file, a wording split of the approved
   `on_first_reset` sentence with the same citation, no new content) and the gate's `rule`
   (§6.1.1(f)(ii): reset only once; if it acts again, do not reset) as the forward warning.
   Guards: `confirm_missing_forward_rule` (the warning must survive phrasing),
   `confirm_added_relief` (relief loco is (f)(ii) text, not in this payload),
   `confirm_reinstructs_done_reset`. The verbatim confirm fallback now renders the engine
   message (with the note) + guidance, so a fallback reads correctly too.
2. **Repeat detection.** `StateUpdate.brings_news(state)` (no new claim/fact/intent/fault/
   config/presentation) and `terminals.signature(t)`; `DiagnosisState.last_terminal_sig`.
   No news + same terminal as last turn → `TurnResult.repeat=True`. A confirm is then rendered
   from the engine sentence "Nothing further is required by the procedure at this point." +
   the same guidance (`llm.phrase.for_repeat`); a pending caution / question / refusal is
   simply restated — it is still pending. The model never decides what a repeat is.

Not built (design boundary, recorded): free-form questions ("why?", "what is QLM?") are not
answered — the LLM has no answer-from-knowledge job (§8). If wanted, the safe shape is a fifth
bounded job / Class-A tool `explain_current_step` that returns the KB step text + TSD citation
verbatim, selected by the agent, never composed by the model. Needs a parse flag for
"asks a question about the current guidance" and a guard that the reply quotes KB text only.

## Post-M6: "this tool is useless" (live QRSI-2, 2026-09-16)

Seen: "qrsi2 dropped. i resetted" → asked (a); "no" → asked (a) again, verbatim; "no i didnt" →
again. Then, continuing the conversation: the branch step (c) was asked as "have you checked the
equipment listed above?"; step (d) was rendered by the phrase model as a question about its own
condition; a completed branch (e) did not end the checklist. All fixed deterministically:

1. **"No" is an answer.** `ParseOutput.denies_asked_step` (fast path) → `StateUpdate.denies_asked_step`
   → `DiagnosisState.steps_declined` (keyed on `stuck_at`). A declined step is returned as
   `ask_step(do_now=True)`: the TSD step text put as the next action ("Do this now: ... Then tell
   me what you found"), never asked again. Engine backstop: a repeated ask on the same step with
   no news → `do_now` (a pilot who had done it would have said so). Guard `do_now_asked_again`.
2. **Branch condition, not branch step.** When the next due steps are alternative branches whose
   conditions are all unstated ((c) long interval vs (d) frequently), `reassess` asks
   `T.ask_branch` — "Which applies now: <If-clause of (c)>; or <If-clause of (d)>? Or has it not
   recurred?" — assembled from the steps' own "If ...," heads (`terminals.branch_condition`);
   sub-branches (sharing a condition key with an earlier alternative) are excluded. Guard
   `branch_question_dropped_alternative`. The answer routes to the step directly, or "not
   recurred" → resolved confirm.
3. **`completes: true` ends the checklist** (`diff_steps`): later alternatives are not due.
4. **Phrase substance guard** `ask_step_lost_substance`: the rendering must keep the step's
   equipment identifiers (>2/3) and the distinctive words of its ACTION clause (>1/2). The payload
   splits a conditional step into `condition_already_met:` + `content:` so the model asks about
   the action. Fallback = KB text ("Next check: ... Done?").
5. **KB wording (QRSI-1/2 (b),(c)):** "the equipment listed above" → "the traction circuit-N
   equipment" — same meaning and citation, readable in a chat. Recorded, not a content change.

Known model slip not caught by a generic guard: "breakers" rendered for "breathers" once on
QLM (c). Candidate: a KB-declared equipment vocabulary check per step.

## Post-M6: spoken-style phrase prompt (user-supplied, 2026-09-16)

`llm/prompts/phrase.md` replaced with the user's prompt (colleague voice; long component lists
summarised as symptom + subsystem tag + an OFFER of the exact list). Three one-line rules were
added to it after live testing: alternatives in a branch question are kept; without `do_now`
an ask_step is always a question; "do not reset" in confirm guidance is kept literally.

Engine/guard support the prompt needs:
- `long_list:` payload line (≥6 identifiers in the step's action clause) with the identifiers as
  the sanctioned subsystem tags. Guard: a SHORT FORM is accepted only if it names the symptom,
  keeps at least one payload identifier, and offers the list; otherwise the substance rule
  applies. `ask_step_named_other_circuit:<id>` rejects the other truck's identifier (RSI-1 on an
  RSI-2 step).
- "Yes, give me the list": `ParseOutput.asks_for_detail` → `StateUpdate.wants_detail` → the graph
  re-emits `DiagnosisState.last_terminal` with `verbatim=True`; `phrase()` renders KB text with
  no model call (`stop_reason=detail`, no engine pass, no reflex). Only for a last `ask_step`.
- A branch step whose condition was stated THIS turn (`DiagnosisState.facts_this_turn`) is
  issued as `do_now` (the pilot reported the condition, not the action).
- Out-of-scope guard checks every covered fault name is present, not the literal "I can verify".

Model slips seen, not caught by a generic guard (recorded): "L-series reactors" for L4/L5/L6
(line contactors); "breakers" for "breathers". A KB-declared per-step equipment vocabulary
(identifier → canonical noun) would let the guard reject a wrong noun next to a right identifier.

Open from the encoding plan (batch 1, Ch.6 remainder): fact-keyed reroute (`route_rules`),
`rb` axis on the session bar, and reading Ch.11/13 for the HT-compartment / reverser-bit
safety measures — awaiting the user's answers.

## Batch 1 — Ch.6 remainder (§6.03–6.05), encoded 2026-09-16 (approved: route rules, rb axis lazy, Ch.11/13 read)

Seven fault files, all quote-first from the TSD text (pdftotext), confirmed by the user 2026-09-16 ("approve all"):

| Fault | TSD | Shape |
|---|---|---|
| `QOP1_dropped` / `QOP2_dropped` | §6.03.1 / §6.03.2 pp.89–90 | isolate-and-retest ladder (long interval / frequent → TM-by-TM isolation 5/6 load → HQOP OFF + TLC); Note 1 HOBA; Note 2 "no RB" in the resolved terminal |
| `QOP1_target_not_resetting` / `QOP2_target_not_resetting` | §6.03.3 / §6.03.4 pp.91–96 | banding-failure check (15 km/h) → isolate → HQOP OFF, clear section → **HT-compartment hazard gate** → J neutral reset localises to TMs → reverser-bit packing (rb-dependent table, §11.02) → TLC |
| `QOA_dropped` / `QOA_target_not_resetting` | §6.04.1 / §6.04.2 pp.97–99 | aux-circuit ladder; EM contactors; HQOA on 0; isolate aux equipment one switch at a time; truck isolation / 50% load / assisting loco |
| `QLA_dropped` | §6.05 pp.100–101 | **reset_limit gate** on `was_QLA_reset_earlier_this_trip`; (d) second act → TLC (NO relief loco in this section); Note 1 → QLM combination reroute |

Engine/schema added for the batch:
- `Fault.route_rules` (RouteRule: if_fact / equals / route_to / phrases): fact-keyed reroute applied
  deterministically before the reflex (`engine.state.resolve_combination` → `_switch_to`). `phrases`
  are KB-declared deterministic triggers ("not resetting", "cannot be reset"...) set in `parse` like an
  alias hit; the parser vocabulary also lists route facts. Claims naming the target's steps in the
  same message are adopted on the switch (`unrecognised_claims` → target step ids).
- rb axis: `LocoInfo.rb` (fitted / not_fitted / unknown), `Fault.rb_dependency`, `AXIS_FACT_RB=loco_rb`,
  `ask_config` "Is this an RB-fitted loco?" — asked by the diff only when a reached step branches on it
  (QOP-2 at the first bit, QOP-1 at the third). API `LocoModel.rb` optional; not on the Streamlit bar.
- HT-compartment `hazard_exposure` gate, precondition `loco_grounded`, texts from GI 7 p.77 and
  §13.05 items 5–9 pp.198–199; `ACTION_ENTER_HT`. Reverser-bit packing sits after the gated step in
  TSD order, so the ordered diff keeps it behind the gate (no second gate).
- `Step.requires_stated`: side-note / consequence steps due only when their condition is STATED
  (never asked on their own). Route alternatives keep the default; choosing one route (condition
  stated true) excludes its unstated siblings (`engine.diff._chosen_route_keys`).
- `DeferCondition.equals` ("no" for "if it does not reset") and `after_any` (§6.03.3(j) 4 applies only
  after the LAST prescribed bit — seen live: "packed 8th and 10th, no reset" was read as unsuccessful).
- Hazard-gate in-play and reassess 3b now use the diff's DUE steps (non-due conditionals are skipped;
  a claimed `completes` step means the gate is not reached).
- reset_limit generalised to the gate's own `needs_history` key (`reset_history_key`); intended
  actions `reset_QLA`, `enter_HT_compartment`; a spent intent (gated step claimed) is cleared.
- Locked-file touch (flagged): `qlm_dropped.yaml` gets `precedence: 2` (with the two QLM combination
  files) so "QLM locked, QOP-1 dropped" resolves to the QLM procedure (§6.1.2/6.1.3), not QOP-1.
- Phrase: caution guard now rejects ADDED conditions (the model rendered QLM's 10-minute checks and
  log-book remark on to the QLA caution); an ask_step may be an imperative with a report-back closer.
- Eval: 4 scenarios (HT entry caution / refusal, QLA second reset, QOP-1 reroute + lazy rb); 7 held-out
  parse cases. Live: 17/17, unsafe 0, missed-gate 0, 9 paths.

Judgement calls (confirmed 2026-09-16): (1) §6.03.1(b)/§6.03.2(b)/§6.04.2(a)/§6.05(b) state no "otherwise TLC"
branch — `on_not_isolated` is INFERRED from the analogous sections (marked in each isolation.source);
(2) §6.03.3(b) negative-side TM isolation is by reverser bit (§11.02) — no separate gate on (b);
GI 7 applies through the ordered diff only from (e) onwards; (3) QOA (e) i–iii focus hints and Note 2
folded into step (a)'s text rather than steps; (4) QOA "very frequently" mapped to `drops_frequently`.

## Batch 2 — Ch.7 tripping failures + §5.01 / Ch.7-intro intake, encoded 2026-09-16

Approved decisions: (1) hub-and-route with a one-line confirm before an ungated target;
(2) wedging = hazard_exposure gate wherever the TSD states a precondition, Q100 ordinary;
(3) no DJ-type axis — one EFDJ/MTDJ file; (4) re-glow sign → ask "VCB 5-branch loco?", don't
know → "the signs indicate Operation 'B' part II", VCB-5 → §7.11 TLC; (5) cross-references
verbatim; (6) post-wedge precautions in the step text / resolved terminal; (7) elimination
ladders as applies_when chains.

23 fault files (`dj_tripped_on_line`, `icdj` + 7 branches, `no_tension`, `op_a_beginning`,
`op_a_ending`, `reglows_on_release`, `op_a_ending_part2`, `op_b_part1`, `op_b_part2`, `op_o`,
`op_i`, `op_ii` + 3 branches, `twac`). Every step cites §7.x / §5.01 / Ch.7 intro; the
observation-drill route rules cite each section's "Abnormal sign" line.

Engine / schema added:
- `Fault.confirm_before_guidance` — a procedure reached by a parser-classified route fact is
  confirmed (reassess 2b) before guidance; `_switch_to` clears `fault_confirmed` for such a
  target. A CAUTION / ASK on an unconfirmed fault now waits behind the confirm line (a REFUSE
  never does) — `run_turn` / graph accept `confirm_fault` after a fired verdict.
- `RouteRule.note` → `DiagnosisState.route_note`, prepended to the confirm line (decision 4).
  `confirm_fault` is rendered VERBATIM (the phrased line asked a different question live) and
  names the fault by its first alias.
- `Fault.listed_as` + `INTAKE_PRECEDENCE` (= -2): the out-of-scope coverage list collapses
  families; an intake hub's alias ("DJ tripped") yields to a specific fault the parser names
  in the same message, and the hub's own drill steps are then dropped from the claims.
- `Gate.precondition_question` (KB text for the H-ask branch; the roof question was hard-coded).
- Wedge intents `wedge_Q118` / `wedge_Q44` / `wedge_Q45` / `wedge_contactor`; `Terminal.preconditions_met`
  tells the phrasing a gated step's preconditions are already stated.
- `engine.diff.step_skipped`: a gated step in an untaken branch (contradicted / unstated
  side-note) neither gates nor blocks; `prior_ordinary_complete` also requires every EARLIER
  gated step to be done (TWAC: Q118 before Q44).
- Reassess step 4 attaches the `resolved` terminal on a claimed `completes` step of a
  hazard-gated fault too (wedge + precautions).
- Matcher: an alias immediately preceded by a negation is not a hit ("no operation A ending
  trouble" while confirming the Q45 precondition).
- Parse: `facts` values may be enumerated (`trip_sign`, `contactors_closed`, `vcb_5_branch_loco`);
  route phrases for every sign / contactor state / DJ-type answer; "reporting the RESULT of a
  listed check is a claim of that check".
- Phrase guards: substance guard stems words and ignores instruction meta-words; the hold
  guard ignores the step's own "wait 15 s" / "held"; "If unsuccessful," ladder connectors are
  stripped from the payload (seen live: "the safety relays are already showing unsuccessful");
  a reply may be as long as 1.3× its payload; a defer must keep "relief" when the payload has it.
- Eval: `ambiguous_dj_tripped` gold is now the intake precheck (no longer a clarify); 4 new
  scenarios (TWAC Q118 caution→refuse, Q44 refused without TLC, intake→Op A beginning with
  confirm, re-glow not known → Op B II caution). 21/21 offline and live, 11 paths. 15 new
  held-out parse cases (55 live).

Confirmed by the user 2026-09-16: (1) §7.08(d) stays verbatim (HVSI is the RSI-block switch);
(2) the ICDJ hub's Q118 / Q45 / Q44 steps ASK whether the relay energises and, as an
instruction, tell the pilot to check for energisation (step texts reworded accordingly).
Judgement calls (3)–(6) confirmed 2026-09-16: (3) the Q118→Q45→Q44
chain follows each section's "if DJ does not close, check … branches"; a Q45 manual close
that trips routes back to the intake hub ("pick up the correct abnormal sign") and re-asks
the precheck; (4) the contactor wedge gate's precondition (switch on 3) is a companion
requirement rather than a strict "before" in the TSD; (5) §7.09.4 "wedge any two compressor
contactors" left as text (no gate — not C105/C106/C107); (6) `terminal_actions.unresolved:
contact TLC` on ladders that end without a TLC line (§7.01.1, §7.01.2) is the engine's
default, marked in the file comments.

Known phrase slips not caught by guards (recorded): "motor contactors" for the MVMT/MVSL
blowers; "wedging while energised causes chatter" paraphrase of precaution 7. A per-step
equipment vocabulary guard remains the candidate fix.

## Batch 3 — Ch.8 traction failures, encoded 2026-09-16

Approved decisions: (1) Q50 wedge is an ORDINARY step — the TSD states only after-precautions
(in the step text and the resolved terminal); (2) QRS wedge: its before-checks (CCLS fuse, BP,
RGEB COC) are asked as ladder steps, no gate; Q52 / Q46 / QVCD / QWC wedges ordinary (no stated
precondition — QVCD / HVCD on 0 flagged: it disables the vigilance device); (3) §8.07 as ONE
file keyed on `ccpt_melts_when` (15 values); (4) §8.04 as one file; (5) "remove '+'ve wire"
steps verbatim, ungated; (6) manual GR control verbatim; (7) reverser photographs as text.

8 fault files: `total_loss_te` (intake hub on `te_sign`, the batch-2 pattern — added so "no
traction" has an entry), `total_loss_te_with_lsb`, `total_loss_te_without_lsb`,
`total_loss_te_with_gr_progression`, `auto_regression_with_lsp`,
`first_notch_auto_regression_without_lsp`, `partial_loss_te`, `ccpt_melting`.

Engine / schema added:
- `Fault.fact_phrases` (FactPhrase: fact / equals / phrases / source): deterministic fact
  triggers without a reroute (the CCPT occasion), applied in `parse` like route phrases.
- `Step.elicits`: an observation step counts as done once the fact it asks for is stated
  (the CCPT occasion, the DJ-trip / TE signs, the Op-II contactor state, the VCB question).
- §8.04: the stated-only reason branches (MPS / slipped pinion / locked axle) are placed
  BEFORE the meter question so a stated reason is served first.
- Phrase substance guard: generic instruction words added to the stop list; word-loss
  threshold 0.6 (identifier loss unchanged at 0.34).
- Eval: 3 scenarios (TE intake → Q50 wedge as a plain step; CCPT on closing DJ → Q44 gate
  refused without TLC; slipped pinion → TLC). 24/24 offline and live, 12–13 paths. 10 new
  held-out parse cases (65 live); "no traction suddenly…" is now the TE intake, not a clarify.

Judgement calls (confirmed 2026-09-16): (1) the TE intake hub (8th file) — the three §8.01–8.03 sections
are told apart only by the LSB / LSGR / NR / ammeter sign; (2) §8.02 items 8a–8d encoded as
stated-only steps under "Q51 energised", 8d completes; (3) §8.05 items 5/6 (ZSMS non-modified /
modified) as alternatives, 8–12 under "notches not from rear cab" (item 7's header), 13–14
unconditional; (4) §8.06 3(c) (rear-cab meters / HVSI) as a stated-only note; (5) §8.07
preamble: "melts again?" and "still melts with HOBA off?" asked as questions, the occasion
branches stated-only.

## Batch 4 — Ch.9 pneumatic failures §9.01–§9.10, encoded 2026-09-17

Accepted decisions (user, 2026-09-17, on the batch-4 proposal): (1) §9.04 as a hub + routed
cause files (the batch-2 pattern); (2) §9.04.7 AFI overshoot as its own file; (3) a
continuity-test hazard gate on moving the train after a cattle run-over; (4) the §9.03
after-attaching Note as a stated-only branch, no question; (5) §9.06 / §9.07 end without a
TLC line → the engine-default `unresolved: contact TLC`; (6) "dummy SS1" / "dummy the safety
valve" as plain steps.

15 fault files (the proposal said 14 — hub + 4 routed causes + AFI = 6, plus the nine
standalone sections): `rs_pressure_not_building` (§9.01), `mr_pressure_not_maintaining`
(§9.02), `bp_pressure_not_charging` (§9.03), `sudden_bp_drop_on_run` (hub, §9.04 + §9.04.1–2),
`cattle_run_over` (§9.04.3), `alarm_chain_pulling` (§9.04.4), `a9_exhaust_port_leaking`
(§9.04.5), `c2a_relay_valve_leaking` (§9.04.6), `afi_overshoots_on_run` (§9.04.7),
`fp_pressure_not_charging` (§9.05), `loco_brake_not_applying_sa9` (§9.06),
`loco_brake_not_applying_a9` (§9.07), `loco_brakes_not_releasing` (§9.08),
`bp_rises_beyond_5_after_a9` (§9.09), `bp_not_dropping_through_a9` (§9.10). Every step cites
§9.x; all 15 share `listed_as: "pneumatic failures (…)"` so the coverage sentence stays under
the batch-2 bound.

Shape notes:
- Hub `bp_drop_cause` (train_parting / ip_valve / cattle_run_over / acp / a9_exhaust_leak /
  c2a_leak / fiba) is elicited by a "report the cause if known" step after the §9.04.1 flasher
  step; causes 3–6 route (confirm line on the target); 1–2 are the hub's own ladder; FIBA has no
  procedure (presenting-sign text only). Hub precedence -2; cattle / ACP / A9 / C2A carry direct
  aliases. AFI routes `afi_cause: acp` → §9.04.4; leak / no-leak / brake-binding as branches.
- Cattle: (c)(ii) "ask for relief engine" = defer condition after the angle-cock step; Note 1 =
  defer condition `brake_gear_damaged`; Note 3 = `hazard_exposure` on `continuity_test_done`,
  intent `move_train` (`ACTION_MOVE_TRAIN`, parser schema + prompt); Notes 2 and 4 = resolved
  guidance; "Do not trip DJ in section" in step (a).
- §9.08(b) "BP < 5 → trouble shoot for the same" = route rule to §9.03 on `bp_below_5` (phrases
  "BP below 5", "BP less than 5" …), (b) `elicits` the fact.
- Stated-only steps: §9.01 step 1 (`mcpa_working: no`, step 1 `elicits` it); §9.02 Note 1
  (`air_dryer_leaking`, placed with (e)) and Note 2 (`air_spring_burst`, completes, placed
  BEFORE l–m so a stated burst air spring is served first); §9.03 Note (`bp_not_charging_after_attaching`)
  + dead-loco sub-branch (`dead_loco_attached`), placed FIRST; §9.05 Note 1; §9.06(a) light
  engine and (h) resume-with-A9 (`loco_brakes_apply_with_a9`, completes); §9.10(c) banker.
  §9.02 l–m are asked as (l)'s own "If loco is attached on formation" question.

Engine / eval touched (beyond the "no engine changes expected" estimate; accepted by the user 2026-09-17):
- `engine/reassess.py` step 4: the resolved terminal keys on ANY claimed `completes` step, not
  the last-claimed step by file order (a stated-only aside placed ahead of already-claimed
  ladder steps — §9.02 Note 2 — was falling through to the unresolved guidance). Mirrors the
  diff's `completed` rule.
- `llm/parse.py`: a model guess that is the current fault's OWN route / combination target with
  `confirms_fault: no` is not a denial (seen live with the larger vocabulary: "only C107 is not
  closing" on Op_II → guess Op_II_one_not_closed, confirms_fault no); the KB route rule makes
  the switch on the fact. Batch-4 `_FACT_HINTS`; `move_train` in the action list.
- `eval/baseline.py`: the flat bot is now also scored unsafe on `instruct_gated_step` (batch 2's
  rule; the harness already did this at the agent side) — baseline unsafe 0.48 on 27 scenarios.
- `engine/diff.py` (**safety fix found by the live suite**): an `elicits` step is done when —
  and ONLY when — its fact is stated; a claim on the step without the answer is not
  completion. Seen live on the batch-2 reglow scenario: DeepSeek (temperature 0, yet varying
  between identical calls) claimed Reglows_on_release's VCB-type QUESTION step from the first
  message, and the engine confirmed the one-step procedure on that fake claim (a `confirm`
  the gold forbids → UNSAFE 1/27). The hub's "report the cause" step now accepts
  `bp_drop_cause: not_known` as the answer, like `vcb_5_branch_loco`.
- Tests: `tests/test_batch4_ch9.py` (22). 286 offline. Eval: 3 scenarios (cattle → move without
  continuity test refused; BP-drop hub → A9 exhaust with confirm; brakes-not-releasing → §9.03
  reroute, specific missed check). 27/27 offline AND live, unsafe 0, missed-gate 0, 15 paths
  offline / 14 live. Held-out parse: 10 new cases (75); live 75/75 after the parse fix (one
  earlier run missed "all normal" → `traction1_abnormality_found: no` on the QOP-1 recurrence
  case — model variance; the engine consequence is an unstated abnormality, safe). Also seen
  live: "air is leaking through the A9 exhaust port" over-claimed
  `apply_a9_to_emergency_and_try` (a sign report taken as a step result) — recorded, not fixed.

Judgement calls (7)–(12) confirmed by the user 2026-09-17 ("all accepted"): (7) the two cross-references the batch-3 HANDOFF
earmarked for this batch are wired as route rules to §9.01 — `ICDJ_air_pressure` (c) "as
explained in §9.1" on `rs_still_not_building` (step (c) `elicits` it; phrases "still not
building" …), and the DJ intake hub's Ch.7-intro "If RS pressure is less, trouble shoot
accordingly (Chapter 9)" on `rs_pressure_low`, listed AFTER the sign rules so a stated sign
routes first (both are confirmed batch-2 files; the batch-2 route-map test now scopes to
`trip_sign`). §7.02(e) "panto not rising (Chapter 9)" is NOT in §9.01–§9.10 (it is a Ch.10
section) — left as text. (8) The hub's IP-valve step (c) completes with a resolved line that
restates the step ("work onwards with the IP valve COC closed") — the TSD has no resume line
there. (9) §9.04.4(d) RS-5 register (at destination) is the resolved guidance; (c) BPC entry
completes. (10) §9.05: (d) and Note 1 complete; Notes 2–3 (60 kmph, whole formation single
pipe) ride as a conditional resolved line. (11) §9.06(h) doubled as a stated-only completing
step and the resolved text. (12) Coverage list: the 15 files collapse to one
"pneumatic failures" entry.

## Batch 5 — Ch.10 miscellaneous failures, encoded 2026-09-18

Approved decisions (user, 2026-09-18, "all approved"): (1) §10.08's tell-tale fuse removal is a
`hazard_exposure` gate on DJ open / panto lowered / HBA off with the IP(M) coc closed — §10.08
states them as a BEFORE condition of the removal; (2) §10.02 is ONE file with the two fuse
blocks as branches on `ccba_ccpt_fuse_melted`; (3) §10.06's HBA-'0' test is the discriminating
question (`lschba_cause`, KB fact phrases), the modified-HCHBA table a stated-only aside;
(4) §10.16(c) hand-brake wheel numbers stay as TSD text, not a `loco_type` branch; (5) §10.16(g)
locked axle is a defer-to-TLC condition; (6) QEMS (§10.02) and QRS (§10.04(c)) wedges ordinary;
(7) aliases keep §10.16 apart from §8.05 and the TE hub.

14 files (§10.03 panto damaged and §10.12 sanders were already encoded): `mcpa_not_working`,
`pantograph_not_raising`, `all_pilot_lamps_not_glowing`, `lsdj_not_glowing_when_bldj_opened`,
`lschba_glows_on_run`, `lsgr_not_glowing_on_zero`, `lsrsi_glows_on_run`, `ua_meter_not_deviating`,
`head_light_not_glowing`, `flasher_light_not_glowing`, `horns_not_sounding`,
`auto_regression_with_lsb_during_rb`, `wheel_skidding`, `loco_not_moving_on_first_notch`.

Cross-references closed: §10.05(b) and §10.07(c) route to §10.04 on `other_pilot_lamps_glowing`;
§10.01(b) and §10.02(f) route to §11.06 on `battery_voltage_too_low`. §7.02(e) "panto not
rising" is §10.02 (NOT Ch.9 — the batch-2 file's "(Chapter 9)" gloss was mine, corrected).

Judgement calls: (a) §10.14 (auto regression during RB) carries NO reset_limit gate — §10.14(c)
itself prescribes resetting the target a second time and dropping RB, unlike the Ch.6 relays;
its (c) is placed BEFORE (b) so a stated recurrence is served first ((b) completes).
(b) §10.04's MU / LSAF notes are stated-only asides.

## Batch 6 — Ch.11 isolation, Ch.12 wedging, Ch.13 special instructions, encoded 2026-09-18

27 REFERENCE PROCEDURE files (precedence −1, `listed_as: isolation, wedging and special
instructions`): Ch.11 §11.01–11.08 except §11.04, which `pantograph_damaged` already carries;
Ch.12 §12.01 (one file, relay chosen by `manual_relay`), §12.02, §12.03 (one file, relay chosen
by `relay_to_wedge`, each relay keeping its own before-conditions); Ch.13 all sixteen sections.

They carry the SAME gates and intents as the fault files that cite them — `enter_HT_compartment`
on §13.05's repair step, `work_on_roof` on §13.06 / §13.10, `wedge_Q118` / `wedge_Q45` with the
Ch.7 facts (`all_em_contactors_open`, `no_operation_a_ending_trouble`) in §12.03 — so asking for
the how-to never routes around a refusal. §11.02's reverser-bit table is the one already written
out in words in §6.03.3(j) / §6.03.4(i) (batch 1, user-confirmed) and corroborated by §8.04 B;
the rb axis is asked lazily. §13.11's ready-reckoner speed table is NOT encoded (the
"facts, not verbatim tables" rule) — the step names it and points at the TSD.

Judgement calls, ALL CONFIRMED by the user 2026-09-18: (1) §12.01's Q118 press gated on C-118
fully opened and the Q44 press on Q118 energised (both stated BEFORE-checks); Q45's conditions
left as ordinary steps. (2) §12.03's Q50 BEFORE-check ("ensure LSC145 is extinguished before
wedging Q50") — RULED: §8.01's Q50 wedge is gated too, on `loco_de_energised_for_wedging` +
`j1_j2_ctf_c145_set_for_q50`, citing §12.03; this supersedes batch-3 decision 1, which read
§8.01 alone. The ladder's own items 5–8 are exactly what the precondition asks about, so no new
procedure content was introduced. The §8.02 QRS and §8.05 Q46 / QVCD wedges KEEP batch-3
decision 2 (their before-checks are asked as ordinary steps) — the user ruled on Q50 only.
(3) §13.05 / §13.06 / §13.10 keep their hazard gates, so a pilot who opens with "going into the
HT compartment" is cautioned on every turn until they state the loco is grounded, rather than
being walked through steps 1–9 first — ACCEPTED as-is (safe; the caution repeats).
(4) §13.08 (first aid) and §13.07 (fire precautions) are encoded as ordered procedures even
though they are not fault ladders.

New engine / LLM work in batches 5–6:
- Three intents registered: `remove_fuse`, `work_on_relay`, `wedge_relay` (state, schema, prompt).
- `llm/parse.py`: an intake hub no longer hijacks an ANSWER given inside another procedure —
  if the current fault's own KB phrases match the message, the hub alias is dropped (§10.06's
  "if DJ trips, CHBA is defective" was being taken over by the §5.01 hub alias "DJ trips").
  The KB-phrase scan is now the shared helper `kb_phrase_facts`.
- `llm/phrase.py`: NUMERIC-VALUE loss guard — a rendering that drops more than a quarter of a
  step's standalone numbers falls back to verbatim (seen live: §11.02's reverser-bit numbers
  rendered as "the bit positions ... as laid out").
- `llm/schemas.py`: `ParseOutput` is `extra="allow"` + `fold_stray_facts()` — DeepSeek sometimes
  emits an enumerated fact as a TOP-LEVEL key, and `extra="forbid"` turned a good parse into a
  hard failure. Folded into `facts`, where KB-key validation still applies.
- Fact hints sharpened for the negative wording ("DJ is still closed" → `dj_opened: no`, and the
  same for panto / HBA / the de-energised facts) and for "all normal" → abnormality `no`.
- Coverage list collapsed again (12 entries, 570 chars): the seven relay files became
  "safety relay trips", the Ch.10 files "miscellaneous failures", Ch.11–13 one entry.
- Tests: `tests/test_batch5_ch10.py` (8), `tests/test_batch6_ch11_13.py` (11, including
  KB-wide integrity: every gate action is a registered intent, every route target resolves,
  every alias resolves to its own fault). 305 offline. Held-out parse: 14 new cases (89), all
  passing live; two stale cases fixed (a headlight message is no longer out of scope now that
  §10.10 is encoded; "qop dropped" is ambiguous between QOP-1 and QOP-2, not out of scope).
- Eval: 3 scenarios (§10.08 fuse removal on a live loco; §10.05 → §10.04 route with a specific
  miss; §12.03 Q45 wedge refused on Operation 'A' ending trouble). 30/30 offline AND live,
  unsafe 0, missed-gate 0, 16 paths offline / 15 live.

## KB SCOPE CLOSED at Ch.5–13 (user, 2026-09-18: "lets keep the scope till this")

The knowledge base is complete as a deliverable: Ch.5 intake, Ch.6 safety relays, Ch.7 tripping
failures, Ch.8 traction failures, Ch.9 pneumatic failures, Ch.10 miscellaneous failures and the
Ch.11–13 reference procedures — 101 fault files, every step citing its TSD section.

Deliberately NOT encoded, and not to be added without a new decision:
- Ch.14 SIV internal fault tree, Ch.15–16 microprocessor / 3-phase and MU locos, Ch.17–18
  double-head and banker, Ch.20 circuit diagrams — all OUT by BUILD_PLAN §15.
- Ch.19 troubles on air brake trains — in the TSD's conventional-loco territory and offered as
  a batch-7 candidate; the user closed scope instead. If it is ever picked up, it is a clean
  standalone batch (§19.01–19.10: BP / FP on the formation, brake binding, LHB bogie isolation,
  BP pipe / hose / angle cock, ACP on the train, air spring failure).
- §13.11's ready-reckoner speed table and §11.02's photographs stay in the TSD by the
  "facts, not verbatim tables" rule.

Any further work is engine / product work on a frozen KB, not more encoding.
