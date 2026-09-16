"""THE SAFETY REFLEX — deterministic, mandatory, agent-independent (BUILD_PLAN §5.1).

``evaluate_gates(state, fault)`` is run after EVERY state update by the engine. It is
not a tool; nothing in the (future) agent loop can choose to skip it, and nothing an
LLM says can alter its verdict. No LLM code is imported here and none ever will be.

Verdicts: NO_FIRE (normal flow continues) | ASK | CAUTION | REFUSE.

Reset-limit gate — precedence, each rule grounded in TSD §6.1.1 (Rev-2 2019, p.83–84).
Reviewed and agreed 2026-09-14 (fixes A–D to the HANDOFF proposal):

  1. §6.1.1(f)(ii) "QLM acts second time" — TWO triggers, same verdict, distinct reasons:
       history[was_QLM_reset_earlier_this_trip] == yes → REFUSE (second_reset: pilot states
         a prior reset this trip);
       history[fault_recurred] == yes                  → REFUSE (recurred_after_reset: the
         relay re-locked after the first reset performed/instructed in this session — set
         by the ENGINE backstop in update_state, parser flag is a fast path only).
  2. abnormality == yes on any ordinary step         → REFUSE  §6.1.1(f)(i) "any abnormality";
     a step's own ``on_abnormality`` text (e.g. (c)'s fire extinguisher + relief engine)
     attaches only when its ``finding_key`` fact is 'yes' (or it declares no finding_key).
     Rules 1–2 fire on the fact alone — regardless of stated intent or check completion
     (fix A; BUILD_PLAN §12.1 gold scenario states no intent). If both hold, the REFUSE
     carries BOTH reasons so the fire-precaution guidance is never dropped (fix B).
     2b. (M4) a step may carry its own ``abnormality_key`` and an ``isolation`` block —
     the TSD's "try to isolate; if successful, reset and resume; otherwise contact TLC"
     (§6.1.2(b), §6.1.3(b)). For such a step, abnormality == yes means:
        isolation_successful == yes → NOT a refusal; the reset proceeds under rules 3–5
                                       with the isolation's own on_isolated text;
        isolation_successful == no  → REFUSE (reason abnormality_not_isolated, TLC text);
        not stated                  → ASK the one isolation question.
  3. reset not "in play" (ordinary checks incomplete)  → NO_FIRE — reset stays
     behind the checks; the diff asks the next unmet one. §6.1.1(d) "if no abnormality".
     A stated reset intent with checks incomplete is also NO_FIRE (TSD order a→d), but
     reassess marks the ask_step with hold_action=reset_QLM (fix D).
  4. in play, reset history not stated              → ASK the one history question (§2.3)
  5. in play, history == no, reset not yet done     → CAUTION: reset once + monitor +
     log + TLC. §6.1.1(d)(e). If abnormality_found is unknown (checks claimed, no
     verdict stated) the caution is CONDITIONAL — "if no abnormality" — exactly as
     §6.1.1(d) words it (fix C).
  6. in play, history == no, reset already claimed done → NO_FIRE (reassess → confirm).

"In play" = all ordinary checks are complete OR the reset step is itself claimed done.
A stated intent alone does NOT put the reset in play (rule 3): the reflex is stricter
than the pilot's intent, and it does not depend on the parser extracting intent at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from engine.state import (
    HF_ABNORMALITY,
    HF_RECURRED,
    HF_RESET_EARLIER,
    DiagnosisState,
    checks_complete,
)
from engine.terminals import (
    REASON_ABNORMALITY,
    REASON_NOT_ISOLATED,
    REASON_RECURRED,
    REASON_SECOND_RESET,
)
from kb.schema import GATE_TYPES, Fault, Step


class Outcome(str, Enum):
    NO_FIRE = "NO_FIRE"
    ASK = "ASK"
    CAUTION = "CAUTION"
    REFUSE = "REFUSE"


@dataclass(frozen=True)
class GateVerdict:
    outcome: Outcome
    gate_type: Optional[str] = None
    step_id: Optional[str] = None
    rule: Optional[str] = None            # which precedence rule decided (for traces)
    reasons: tuple[str, ...] = ()         # REFUSE: every applicable reason
    conditional: bool = False             # CAUTION: conditional on "no abnormality"
    message: str = ""                     # KB text to phrase (never model-generated)
    question: Optional[str] = None        # ASK: the single history question
    source: str = ""

    @property
    def fired(self) -> bool:
        return self.outcome is not Outcome.NO_FIRE


NO_FIRE = GateVerdict(Outcome.NO_FIRE)


# ---------------------------------------------------------------------------
# reset_limit
# ---------------------------------------------------------------------------

def _history_question(gate_key: str, reset_done: bool = False) -> str:
    # Derived from the gate's needs_history key, endorsed by BUILD_PLAN §2.3 wording.
    m = re.fullmatch(r"was_([A-Za-z0-9-]+)_reset_earlier_this_trip", gate_key)
    if m:
        relay = m.group(1)
        if reset_done:      # the pilot has just reset it: ask about BEFORE that, unambiguously
            return (f"Before the reset you just did, had {relay} been reset earlier this trip? "
                    "Check the loco log book.")
        return f"Was {relay} reset earlier this trip? Check the loco log book."
    return f"Please state: {gate_key.replace('_', ' ')}?"


def _render_action(text: str) -> str:
    """KB ``terminal_actions`` values are compact notation ("do_not_reset; log; relief loco").
    Render mechanically into sentences — a transform of KB text, never new content."""
    parts = [p.strip().replace("_", " ") for p in text.split(";") if p.strip()]
    return " ".join(p[0].upper() + p[1:] + ("" if p.endswith(".") else ".") for p in parts)


def _relay_name(fault: Fault) -> str:
    """The relay the reset gate protects, from the fault_id (QLM_dropped → 'QLM')."""
    return fault.fault_id.split("_", 1)[0]


def _reset_in_play(state: DiagnosisState, fault: Fault, step: Step) -> bool:
    return step.id in state.steps_claimed_done or checks_complete(state, fault)


def prior_ordinary_complete(state: DiagnosisState, fault: Fault, step: Step) -> bool:
    """Every ordinary step BEFORE ``step`` (in TSD order) that is DUE is claimed done. A
    conditional step that is contradicted, a side-note (requires_stated) whose condition is
    not stated, or an unchosen route alternative is not pending (engine.diff decides)."""
    from engine.diff import diff_steps, step_skipped   # local import: diff imports state, not gates
    delta = diff_steps(state, fault)
    if delta.completed:
        return False                            # the procedure ended on a completing step: not reached
    before = []
    for s in fault.steps:
        if s.id == step.id:
            break
        if s.gate is not None and s.id not in state.steps_claimed_done and not step_skipped(state, fault, s):
            return False                        # an earlier gated step is still pending (§7.10: Q118 before Q44)
        before.append(s.id)
    return not (set(delta.missing) & set(before))


def isolation_status(state: DiagnosisState, fault: Fault):
    """For steps with an ``isolation`` block: (pending_step, failed_step, isolated_step) —
    abnormality found and isolation unstated / failed / succeeded. Used by the reset gate
    and, for gate-free faults, by reassess."""
    pending = failed = isolated = None
    for s in fault.ordinary_steps:
        if s.isolation is None or state.history(s.abnormality_key or HF_ABNORMALITY) != "yes":
            continue
        iso = state.history(s.isolation.needs_history)
        if iso == "yes":
            isolated = s
        elif iso == "no":
            failed = s
        else:
            pending = s
    return pending, failed, isolated


# ---------------------------------------------------------------------------
# hazard_exposure  (BUILD_PLAN §2.3 — "a precondition, not a question")
# ---------------------------------------------------------------------------

def evaluate_hazard_exposure(state: DiagnosisState, fault: Fault, step: Step) -> GateVerdict:
    """The gated action is dangerous unless every precondition fact is 'yes'.

    In play when: the pilot states the intent (gate.action), or every ordinary step before
    the gated step is claimed (the action is next), or the step is claimed done.
      any precondition == 'no'      → REFUSE (do not do it; the precondition text)
      all preconditions == 'yes'    → NO_FIRE (proceed; reassess asks/confirms the step)
      otherwise (unstated), not yet claimed → CAUTION: proactive precondition statement
      otherwise, claimed done       → ASK whether the preconditions were met (never
                                      confirm a hazardous step on an unstated precondition)
    """
    gate = step.gate
    assert gate is not None and gate.type == "hazard_exposure"
    from engine.diff import step_skipped        # local import: diff imports state, not gates
    claimed = step.id in state.steps_claimed_done
    in_play = (claimed or state.intended_action == gate.action
               or (not step_skipped(state, fault, step) and prior_ordinary_complete(state, fault, step)))
    if not in_play:
        return NO_FIRE
    values = {p: state.history(p) for p in gate.preconditions}
    if any(v == "no" for v in values.values()):
        return GateVerdict(Outcome.REFUSE, gate.type, step.id, rule="H-no",
                           reasons=tuple(p for p, v in values.items() if v == "no"),
                           message=gate.on_precondition_unmet or gate.rule, source=gate.source)
    if all(v == "yes" for v in values.values()):
        return NO_FIRE
    unstated = tuple(p for p, v in values.items() if v is None)
    if claimed:
        return GateVerdict(Outcome.ASK, gate.type, step.id, rule="H-ask", reasons=unstated,
                           question=gate.precondition_question or
                                    "Before the roof work, was the OHE power block obtained and the "
                                    "contact wire earthed by OHE staff, and was the loco grounded?",
                           message=gate.on_precondition_unmet or gate.rule, source=gate.source)
    return GateVerdict(Outcome.CAUTION, gate.type, step.id, rule="H-caution", reasons=unstated,
                       message=gate.on_precondition_unmet or gate.rule, source=gate.source)


def evaluate_reset_limit(state: DiagnosisState, fault: Fault, step: Step) -> GateVerdict:
    gate = step.gate
    assert gate is not None and gate.type == "reset_limit"
    reset_earlier = state.history(gate.needs_history)      # 'yes' | 'no' | None

    # Per-step abnormality verdicts. Steps without their own key share HF_ABNORMALITY.
    reasons: list[str] = []
    parts: list[str] = []
    isolation_pending: Optional[Step] = None      # abnormality found, isolation not yet stated
    isolated_step: Optional[Step] = None          # abnormality found and isolated → reset allowed
    any_unknown = False
    for s in fault.ordinary_steps:
        key = s.abnormality_key or HF_ABNORMALITY
        v = state.history(key)
        if v is None:
            any_unknown = True
            continue
        if v != "yes":
            continue
        if s.isolation is None:
            if REASON_ABNORMALITY not in reasons:
                reasons.append(REASON_ABNORMALITY)
                parts.append(_render_action(fault.terminal_actions.get("abnormality_found", "")))
            # step-specific inline consequence only for THIS step's finding (§6.1.1(c))
            if s.on_abnormality and (s.finding_key is None or state.history(s.finding_key) == "yes"):
                parts.append(s.on_abnormality)
            continue
        iso = state.history(s.isolation.needs_history)
        if iso == "yes":
            isolated_step = s
        elif iso == "no":
            reasons.append(REASON_NOT_ISOLATED)
            parts.append(s.on_abnormality or "")
            parts.append(s.isolation.on_not_isolated)
        else:
            isolation_pending = s

    # Rule 1 — (f)(ii), both triggers. Recurrence gets an engine framing sentence so the
    # phrased reply can reflect the real situation; the verdict text itself is (f)'s.
    recurred = state.history(gate.recurrence_history or HF_RECURRED)
    if recurred == "yes":
        reasons.insert(0, REASON_RECURRED)
        parts.insert(0, f"{_relay_name(fault)} has acted a second time, after the first reset. "
                        f"{gate.on_already_reset}")
    if reset_earlier == "yes":
        reasons.insert(0, REASON_SECOND_RESET)
        if recurred != "yes":
            parts.insert(0, gate.on_already_reset)

    # Rules 1–2 (+2b not-isolated): REFUSE on fact, regardless of intent / check completion.
    if reasons:
        return GateVerdict(
            outcome=Outcome.REFUSE,
            gate_type=gate.type,
            step_id=step.id,
            rule="1" if reasons[0] in (REASON_SECOND_RESET, REASON_RECURRED) else "2",
            reasons=tuple(reasons),
            message=" ".join(p for p in parts if p),
            source=gate.source,
        )

    # Rule 2b: abnormality found on an isolate-then-reset step, isolation not yet stated.
    if isolation_pending is not None:
        return GateVerdict(
            outcome=Outcome.ASK,
            gate_type=gate.type,
            step_id=step.id,
            rule="2b",
            question=f"Were you able to isolate the abnormal equipment found in the "
                     f"{isolation_pending.id.replace('_', ' ').replace('check ', '')} check?",
            message=isolation_pending.on_abnormality or "",
            source=isolation_pending.isolation.source,
        )

    # Rule 3: reset not yet in play → normal flow (the diff asks the next check).
    if not _reset_in_play(state, fault, step):
        return NO_FIRE

    # Rule 4: in play but history unknown → ask the single history question.
    if reset_earlier is None:
        return GateVerdict(
            outcome=Outcome.ASK,
            gate_type=gate.type,
            step_id=step.id,
            rule="4",
            question=_history_question(gate.needs_history, reset_done=step.id in state.steps_claimed_done),
            message=gate.rule,
            source=gate.source,
        )

    # history == 'no' from here on.
    # Rule 6: the (first) reset is already done → nothing to gate; reassess confirms.
    if step.id in state.steps_claimed_done:
        return NO_FIRE

    # Rule 5: the permitted first reset (checks are complete — rule 3 guaranteed it).
    if isolated_step is not None:
        return GateVerdict(
            outcome=Outcome.CAUTION, gate_type=gate.type, step_id=step.id, rule="5-isolated",
            conditional=False,
            message=isolated_step.isolation.on_isolated,
            source=f"{isolated_step.isolation.source}; {gate.source}",
        )
    return GateVerdict(
        outcome=Outcome.CAUTION,
        gate_type=gate.type,
        step_id=step.id,
        rule="5",
        conditional=any_unknown,
        message=gate.on_first_reset,
        source=gate.source,
    )


# ---------------------------------------------------------------------------
# extension points — later faults (BUILD_PLAN §9 encoding order 2 and 4)
# ---------------------------------------------------------------------------

def _not_implemented(gate_type: str) -> Callable[[DiagnosisState, Fault, Step], GateVerdict]:
    def _raise(state: DiagnosisState, fault: Fault, step: Step) -> GateVerdict:
        # Fail closed: an unevaluable gate must never silently NO_FIRE.
        raise NotImplementedError(f"gate type {gate_type!r} has no evaluator yet (step {step.id})")
    return _raise


GATE_EVALUATORS: dict[str, Callable[[DiagnosisState, Fault, Step], GateVerdict]] = {
    "reset_limit": evaluate_reset_limit,
    "hazard_exposure": evaluate_hazard_exposure,                      # pantograph roof work — M4b
    "isolation_before_contact": _not_implemented("isolation_before_contact"),  # TM / RSI — M4
}
assert set(GATE_EVALUATORS) == set(GATE_TYPES), "every KB gate type needs an evaluator"


# ---------------------------------------------------------------------------
# the reflex
# ---------------------------------------------------------------------------

_SEVERITY = {Outcome.REFUSE: 3, Outcome.CAUTION: 2, Outcome.ASK: 1, Outcome.NO_FIRE: 0}


def evaluate_gates(state: DiagnosisState, fault: Optional[Fault]) -> GateVerdict:
    """Evaluate ALL gates of the matched fault against the current state.

    Pure function of (state, fault). Returns the most severe verdict. Call it after
    every state update — that is the §5.1 contract, and it is the caller's obligation,
    not the agent's choice.
    """
    if fault is None or state.matched_fault != fault.fault_id:
        return NO_FIRE
    worst = NO_FIRE
    for step in fault.gated_steps:
        assert step.gate is not None
        verdict = GATE_EVALUATORS[step.gate.type](state, fault, step)
        if _SEVERITY[verdict.outcome] > _SEVERITY[worst.outcome]:
            worst = verdict
    return worst
