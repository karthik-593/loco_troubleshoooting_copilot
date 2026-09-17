"""KB schema — typed models for a fault file + the ``validate_kb`` CI check.

Every fault file under ``kb/faults/`` must validate against ``Fault``. The schema
mechanically enforces the "cite the TSD" rule (BUILD_PLAN §5.3, §11; HANDOFF):

* file-level ``source`` and ``source_url`` are mandatory;
* every step cites a TSD section in its own ``source``;
* every gate is of a known type and cites its section;
* every combination rule cites its section.

Run ``python -m kb.schema`` to validate the whole KB (exit 1 on any failure).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KB_ROOT = Path(__file__).resolve().parent
FAULTS_DIR = KB_ROOT / "faults"

# Gate types the deterministic reflex knows how to evaluate (BUILD_PLAN §7 class B).
# Adding a type here without a matching evaluator in engine/gates.py is a CI failure.
GATE_TYPES = ("reset_limit", "hazard_exposure", "isolation_before_contact")
GateType = Literal["reset_limit", "hazard_exposure", "isolation_before_contact"]

ConfigDependency = Literal["none", "siv", "arno", "branch-specific"]
# Loco CLASS axis — independent of config. Type drives physical layout / equipment set;
# config (SIV/ARNO) drives the auxiliary build. Both are carried per loco in session state
# from the start; a fault consults ONLY the axes it declares.
LocoType = Literal["wag7", "wag5", "wap4"]
LOCO_TYPES = ("wag7", "wag5", "wap4")
# Pseudo-facts a step's applies_when may reference — resolved from the ACTIVE loco, and only
# if the fault declares the matching dependency.
AXIS_FACT_CONFIG = "loco_config"
AXIS_FACT_TYPE = "loco_type"
AXIS_FACT_RB = "loco_rb"          # rheostatic-braking equipment fitted: "fitted" | "not_fitted"
# An intake hub ("DJ tripped") carries this precedence: its aliases are generic entry phrases,
# so a specific fault the parser names in the same message outranks the alias hit (llm/parse).
INTAKE_PRECEDENCE = -2


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Gate(_Strict):
    type: GateType
    rule: str
    source: str = Field(min_length=1)
    # reset_limit fields (BUILD_PLAN §9)
    needs_history: Optional[str] = None          # prior reset stated by the pilot — (f)(ii) via history
    recurrence_history: Optional[str] = None     # relay acted AGAIN after the first reset — (f)(ii) recurrence
    on_first_reset: Optional[str] = None
    after_first_reset: Optional[str] = None      # the same (d)(e) follow-up, worded for AFTER the reset is done
    on_already_reset: Optional[str] = None       # (f) text; covers both (f)(ii) triggers
    # hazard_exposure fields (BUILD_PLAN §2.3): the action is dangerous unless every
    # precondition fact is 'yes'. Unknown → proactive CAUTION stating the preconditions;
    # 'no' → REFUSE with the same text. ``action`` is the intended_action that puts the
    # gate in play early (before the prior steps are complete).
    preconditions: list[str] = Field(default_factory=list)
    on_precondition_unmet: Optional[str] = None
    action: Optional[str] = None
    # The ONE question asked when the gated step is claimed done with a precondition still
    # unstated (never confirm a hazardous step on an unstated precondition). KB text.
    precondition_question: Optional[str] = None

    @model_validator(mode="after")
    def _type_fields(self) -> "Gate":
        if self.type == "reset_limit":
            missing = [f for f in ("needs_history", "on_first_reset", "on_already_reset")
                       if getattr(self, f) is None]
            if missing:
                raise ValueError(f"reset_limit gate missing {missing}")
        if self.type == "hazard_exposure":
            if not self.preconditions or not self.on_precondition_unmet:
                raise ValueError("hazard_exposure gate needs preconditions + on_precondition_unmet")
        return self


class Isolation(_Strict):
    """TSD pattern "if any abnormality … try to isolate; if successful, reset and resume;
    otherwise contact TLC" (e.g. §6.1.2(b), §6.1.3(b)). Declared on the step whose
    abnormality it qualifies; the reset_limit evaluator consults it."""
    needs_history: str = Field(min_length=1)          # e.g. isolation_successful
    on_isolated: str = Field(min_length=1)
    on_not_isolated: str = Field(min_length=1)
    source: str = Field(min_length=1)


class Step(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    text: str = Field(min_length=1)
    gate: Optional[Gate] = None
    on_abnormality: Optional[str] = None
    # History-fact key holding the pilot's abnormality verdict for THIS step's checks.
    # Default (None) = the fault-wide "abnormality_found". A step may name its own key when
    # the TSD gives its abnormality a different consequence (e.g. isolate-then-reset).
    abnormality_key: Optional[str] = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    # Fact that says the abnormality was found in THIS step's equipment. When set, this
    # step's ``on_abnormality`` text attaches only if that fact is 'yes' — the refuse
    # TRIGGER stays the fault-wide abnormality fact. (§6.1.1(c)'s inline "use fire
    # extinguisher and ask for Relief Engine" is tied to (c)'s findings only.)
    finding_key: Optional[str] = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    isolation: Optional[Isolation] = None
    # Conditional branch (TSD "If <situation>, ..."): fact → required value. The step is due
    # only while no stated fact contradicts it; unstated facts leave the step in play, so the
    # step's own "If …" text asks the question. Lets alternative clauses ((b) long interval
    # vs (c) frequent) coexist in one ordered checklist without asking the wrong branch.
    applies_when: dict[str, str] = Field(default_factory=dict)
    # Batch 1 (Ch.6 remainder): a side-note / consequence step ("If not resetting even with
    # HQOP-1 in OFF, place HOBA OFF"; "if the target resets after isolating, resume") is due
    # ONLY when every applies_when fact is STATED with the given value — never asked on its
    # own. Route alternatives ((d) long interval vs (e) frequently) keep the default.
    requires_stated: bool = False
    # Facts that CLAIMING this step establishes, when the TSD reaches the step only through a
    # branch (e.g. the HMCS ladder is reached only in the "dropping frequently" branch). Set by
    # the engine on the claim if the fact is not already stated — declarative, cited, and
    # independent of whether the parser extracted the branch fact.
    implies: dict[str, str] = Field(default_factory=dict)
    # An observation step exists to elicit ONE fact ("report the occasion", "report which
    # meter is not deviating"): it counts as done once that fact is stated, claimed or not
    # (§8.07: the pilot who opens with "CCPT melts on the 6th notch" is not asked the occasion)
    # — and ONLY then: a claim on the step without the fact is not completion (batch 4).
    elicits: Optional[str] = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    # Completing this step ends the procedure on its 'resolved' terminal (e.g. "isolate that
    # TM and work with 5/6 load" — a sanctioned way onward, not a failure).
    completes: bool = False
    # TSD section this step is taken from. A gated step may carry its citation on the
    # gate instead (that is how the locked qlm_dropped.yaml encodes reset_decision).
    source: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _must_cite(self) -> "Step":
        if self.source is None and self.gate is None:
            raise ValueError(f"step '{self.id}' has no TSD source citation")
        return self

    @model_validator(mode="after")
    def _isolation_needs_key(self) -> "Step":
        if self.isolation is not None and self.abnormality_key is None:
            raise ValueError(f"step '{self.id}': isolation requires its own abnormality_key")
        return self

    @property
    def is_gated(self) -> bool:
        return self.gate is not None

    @property
    def citation(self) -> str:
        return self.source or self.gate.source  # type: ignore[union-attr]


class CombinationRule(_Strict):
    if_also: list[str] = Field(min_length=1)
    route_to: str = Field(min_length=1)
    source: str = Field(min_length=1)


class RouteRule(_Strict):
    """Fact-keyed reroute (approved 2026-09-16): the SAME relay has a different TSD procedure
    depending on a fact the pilot reports ("target cannot be reset" → §6.03.3). Applied
    deterministically before the reflex, like combination_rules; claimed steps carry over
    where the step ids match."""
    if_fact: str = Field(min_length=1)
    equals: str = Field(min_length=1)
    route_to: str = Field(min_length=1)
    source: str = Field(min_length=1)
    # Deterministic triggers: if one of these phrases appears in the pilot's message (whole
    # phrase, case-insensitive) the fact is set to `equals` without the model. Declared in
    # the KB, like aliases; the parser is only the fallback.
    phrases: list[str] = Field(default_factory=list)
    # KB sentence prepended to the target's confirm line when this rule fired (§7.06/§7.11:
    # "DJ type not known: the signs indicate Operation 'B' part II").
    note: Optional[str] = None


class FactPhrase(_Strict):
    """KB-declared phrases that set a fact deterministically (like RouteRule.phrases, without a
    reroute): "while raising panto" → ccpt_melts_when = raising_panto. The parser is the fallback."""
    fact: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    equals: str = Field(min_length=1)
    phrases: list[str] = Field(min_length=1)
    source: str = Field(min_length=1)


class DeferCondition(_Strict):
    """A TSD clause of the form "if <situation>, contact TLC" — a terminal, not a step.
    When the fact is 'yes' the engine defers with the clause's own text."""
    fact: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    equals: str = "yes"                 # the value that triggers the deferral ("no" for "if it does not reset")
    # The deferral applies only once one of these steps is claimed done (§6.03.3(j) 4: "if
    # unsuccessful, contact TLC" comes after ALL prescribed bits, not after the first two).
    after_any: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)


class Fault(_Strict):
    fault_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    aliases: list[str] = Field(min_length=1)
    config_dependency: ConfigDependency
    # "none" | one class | a list of classes the procedure branches on. Parallel to
    # config_dependency; both default to none unless the TSD text branches on the axis.
    type_dependency: Union[Literal["none"], LocoType, list[LocoType]] = "none"
    # Third axis (approved 2026-09-16): rheostatic-braking equipment fitted. Never asked up
    # front — the diff asks "Is this an RB-fitted loco?" only when a reached step branches
    # on loco_rb (§6.03.3/6.03.4 reverser-bit tables: "WAP4 locos without RB" differ).
    rb_dependency: bool = False
    # Alias-match precedence when ONE message matches several faults: the higher wins; a tie
    # is ambiguous (no deterministic match). General procedures (fire_on_loco) declare -1 so
    # a specific fault named in the same message ("QLM locked, arc chute burning") wins.
    precedence: int = 0
    # Short name under which the fault appears in the out-of-scope coverage list (§5.6); faults
    # sharing a `listed_as` collapse to one entry (a hub and its branches, a relay's two
    # procedures). Default: the fault_id with underscores as spaces.
    listed_as: Optional[str] = None
    # Batch 2 (approved 2026-09-16, decision 1): a procedure reached by a fact-keyed route whose
    # fact the PARSER classified (the DJ-trip abnormal sign) is confirmed in one line before any
    # guidance — like a hard-gated fault under §5.5 — so a misread sign never routes silently.
    confirm_before_guidance: bool = False
    presenting_signs: list[str] = Field(default_factory=list)
    combination_rules: list[CombinationRule] = Field(default_factory=list)
    route_rules: list[RouteRule] = Field(default_factory=list)
    fact_phrases: list[FactPhrase] = Field(default_factory=list)
    defer_conditions: list[DeferCondition] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    terminal_actions: dict[str, str] = Field(default_factory=dict)
    source: str = Field(min_length=1)
    source_url: str = Field(pattern=r"^https?://")

    @field_validator("steps")
    @classmethod
    def _unique_step_ids(cls, steps: list[Step]) -> list[Step]:
        ids = [s.id for s in steps]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate step ids: {sorted(dupes)}")
        return steps

    @model_validator(mode="after")
    def _branches_only_on_declared_axes(self) -> "Fault":
        """A step may branch on loco_config / loco_type ONLY if the fault declares that
        dependency — the engine must never consult an axis it was not told to check."""
        for s in self.steps:
            if AXIS_FACT_CONFIG in s.applies_when and self.config_dependency == "none":
                raise ValueError(f"step '{s.id}' branches on {AXIS_FACT_CONFIG} but config_dependency is none")
            if AXIS_FACT_TYPE in s.applies_when and self.type_axes == ():
                raise ValueError(f"step '{s.id}' branches on {AXIS_FACT_TYPE} but type_dependency is none")
            if AXIS_FACT_RB in s.applies_when and not self.rb_dependency:
                raise ValueError(f"step '{s.id}' branches on {AXIS_FACT_RB} but rb_dependency is false")
        return self

    @property
    def type_axes(self) -> tuple[str, ...]:
        """Normalised type_dependency: () for none, else the classes the fault branches on."""
        td = self.type_dependency
        if td == "none":
            return ()
        return (td,) if isinstance(td, str) else tuple(td)

    @property
    def depends_on_config(self) -> bool:
        return self.config_dependency != "none"

    @property
    def depends_on_type(self) -> bool:
        return self.type_axes != ()

    @property
    def depends_on_rb(self) -> bool:
        return self.rb_dependency

    # Convenience views used by the engine -------------------------------------
    @property
    def ordinary_steps(self) -> list[Step]:
        """Steps with no gate — the checklist the diff runs over (HANDOFF)."""
        return [s for s in self.steps if s.gate is None]

    @property
    def gated_steps(self) -> list[Step]:
        return [s for s in self.steps if s.gate is not None]

    @property
    def step_ids(self) -> list[str]:
        return [s.id for s in self.steps]

    @property
    def history_keys(self) -> list[str]:
        """Every history-fact key this fault's gates / steps consult (for the parser's vocabulary)."""
        keys: list[str] = []
        for s in self.steps:
            if s.abnormality_key:
                keys.append(s.abnormality_key)
            if s.finding_key:
                keys.append(s.finding_key)
            keys.extend(s.applies_when.keys())
            keys.extend(s.implies.keys())
            if s.elicits:
                keys.append(s.elicits)
            if s.isolation:
                keys.append(s.isolation.needs_history)
            if s.gate and s.gate.needs_history:
                keys.append(s.gate.needs_history)
            if s.gate and s.gate.recurrence_history:
                keys.append(s.gate.recurrence_history)
            if s.gate:
                keys.extend(s.gate.preconditions)
        keys.extend(d.fact for d in self.defer_conditions)
        keys.extend(r.if_fact for r in self.route_rules)
        keys.extend(fp.fact for fp in self.fact_phrases)
        return list(dict.fromkeys(keys))

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)


def load_fault_file(path: Path) -> Fault:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Fault.model_validate(data)


def validate_kb(faults_dir: Path = FAULTS_DIR) -> list[str]:
    """Validate every ``*.yaml`` in ``faults_dir``; return a list of error strings."""
    errors: list[str] = []
    files = sorted(faults_dir.glob("*.yaml"))
    if not files:
        errors.append(f"no fault files found in {faults_dir}")
    seen: dict[str, Path] = {}
    for path in files:
        try:
            fault = load_fault_file(path)
        except Exception as exc:  # pydantic / yaml errors, reported per file
            errors.append(f"{path.name}: {exc}")
            continue
        if fault.fault_id in seen:
            errors.append(f"{path.name}: duplicate fault_id {fault.fault_id} (also in {seen[fault.fault_id].name})")
        seen[fault.fault_id] = path
    return errors


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    faults_dir = Path(args[0]) if args else FAULTS_DIR
    errors = validate_kb(faults_dir)
    if errors:
        print("validate_kb: FAILED")
        for e in errors:
            print(f"  - {e}")
        return 1
    n = len(list(faults_dir.glob("*.yaml")))
    print(f"validate_kb: OK ({n} fault file(s) in {faults_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
