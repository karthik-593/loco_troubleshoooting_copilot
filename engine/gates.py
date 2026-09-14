"""THE SAFETY REFLEX — deterministic, mandatory, agent-independent (BUILD_PLAN §5.1).

``evaluate_gates(state, fault)`` is run after EVERY state update by the engine. It is
not a tool; nothing in the (future) agent loop can choose to skip it, and nothing an
LLM says can alter its verdict. No LLM code is imported here and none ever will be.

Verdicts: NO_FIRE (normal flow continues) | ASK | CAUTION | REFUSE.

Reset-limit gate — precedence, each rule grounded in TSD §6.1.1 (Rev-2 2019, p.83–84).
Reviewed and agreed 2026-09-14 (fixes A–D to the HANDOFF proposal):

  1. history[was_QLM_reset_earlier_this_trip] == yes  → REFUSE  §6.1.1(f) "QLM acts second time"
  2. history[abnormality_found] == yes               → REFUSE  §6.1.1(c)(f) "any abnormality"
     Rules 1–2 fire on the fact alone — regardless of stated intent or check completion
     (fix A; BUILD_PLAN §12.1 gold scenario states no intent). If both hold, the REFUSE
     carries BOTH reasons so the fire-precaution guidance is never dropped (fix B).
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

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from engine.state import (
    HF_ABNORMALITY,
    HF_RESET_EARLIER,
    DiagnosisState,
    checks_complete,
)
from engine.terminals import REASON_ABNORMALITY, REASON_SECOND_RESET
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

def _history_question(gate_key: str) -> str:
    # Derived from the gate's needs_history key, endorsed by BUILD_PLAN §2.3 wording.
    if gate_key == HF_RESET_EARLIER:
        return "Was QLM reset earlier this trip? Check the loco log book."
    return f"Please state: {gate_key.replace('_', ' ')}?"


def _reset_in_play(state: DiagnosisState, fault: Fault, step: Step) -> bool:
    return step.id in state.steps_claimed_done or checks_complete(state, fault)


def evaluate_reset_limit(state: DiagnosisState, fault: Fault, step: Step) -> GateVerdict:
    gate = step.gate
    assert gate is not None and gate.type == "reset_limit"
    reset_earlier = state.history(gate.needs_history)      # 'yes' | 'no' | None
    abnormality = state.history(HF_ABNORMALITY)             # 'yes' | 'no' | None

    # Rules 1–2: REFUSE on fact, regardless of intent / check completion. Collect all.
    reasons: list[str] = []
    if reset_earlier == "yes":
        reasons.append(REASON_SECOND_RESET)
    if abnormality == "yes":
        reasons.append(REASON_ABNORMALITY)
    if reasons:
        parts = [gate.on_already_reset] if REASON_SECOND_RESET in reasons else []
        if REASON_ABNORMALITY in reasons:
            parts.append(fault.terminal_actions.get("abnormality_found", ""))
            for s in fault.ordinary_steps:
                if s.on_abnormality:
                    parts.append(s.on_abnormality)
        return GateVerdict(
            outcome=Outcome.REFUSE,
            gate_type=gate.type,
            step_id=step.id,
            rule="1" if reasons[0] == REASON_SECOND_RESET else "2",
            reasons=tuple(reasons),
            message=" ".join(p for p in parts if p),
            source=gate.source,
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
            question=_history_question(gate.needs_history),
            message=gate.rule,
            source=gate.source,
        )

    # history == 'no' from here on.
    # Rule 6: the (first) reset is already done → nothing to gate; reassess confirms.
    if step.id in state.steps_claimed_done:
        return NO_FIRE

    # Rule 5: the permitted first reset (checks are complete — rule 3 guaranteed it).
    return GateVerdict(
        outcome=Outcome.CAUTION,
        gate_type=gate.type,
        step_id=step.id,
        rule="5",
        conditional=(abnormality is None),
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
    "hazard_exposure": _not_implemented("hazard_exposure"),           # pantograph / HT — M4
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
