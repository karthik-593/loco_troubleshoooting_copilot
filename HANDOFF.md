# M1 HANDOFF — Loco Troubleshooting Verification Copilot

Read this **after** `BUILD_PLAN.md`. It records the decisions already locked in an
earlier working session so you don't re-derive or contradict them, and it scopes
Milestone 1.

---

## Status
- **Milestone: M3 — LangGraph agent loop + FastAPI + Streamlit built (2026-09-15).**
  114 offline tests green (10 live parse tests pending DeepSeek Flash). M2: 2026-09-14. M1: 2026-09-14.
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
  - **Open issue (2026-09-15):** `deepseek-flash` requests from this key hang with no HTTP
    response (>150 s, even streaming); `deepseek-v4-pro` on the same key answers in ~1 s;
    DeepSeek status page clean. Key confirmed correct by the user. Live held-out parse run is
    therefore still pending; re-try with `pytest tests/test_parse_live.py -m live -s`.
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
**Next (M4):** encode isolate-and-retest, pantograph (hazard gate), a config-dependent fault,
and the combination fault(s) `QLM_with_QOP_QRSI` / `QLM_with_QLA_QOA` — each from the TSD
only, each with its own scenario. Gate evaluators for `hazard_exposure` /
`isolation_before_contact` currently fail closed (`NotImplementedError`).

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
