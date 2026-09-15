"""Reflex tests — exhaustive over the reset_limit precedence (engine/gates.py docstring),
every rule traced to TSD §6.1.1. Structured input only; no LLM anywhere.
"""
import pytest

from engine.gates import GATE_EVALUATORS, Outcome, evaluate_gates, evaluate_reset_limit
from engine.state import (
    ACTION_RESET_QLM,
    HF_ABNORMALITY,
    HF_RESET_EARLIER,
    DiagnosisState,
    StateUpdate,
    update_state,
)
from engine.terminals import REASON_ABNORMALITY, REASON_SECOND_RESET
from kb.schema import GATE_TYPES
from tests.conftest import ORDINARY, QLM, RESET_STEP


def _state(qlm, claimed=(), history=None, intent=None):
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=QLM, claimed_steps=tuple(claimed),
                                history=history or {}, intended_action=intent), qlm)
    return s


# ---------------------------------------------------------------------------
# §5.1 — the reflex fires with NO tool requested and NO agent involved.
# ---------------------------------------------------------------------------

def test_reflex_fires_with_no_tool_requested(qlm):
    """Build the state directly and call evaluate_gates. Nothing 'chose' to check safety;
    the verdict is a pure function of state. (BUILD_PLAN §5.1, M1 DoD)"""
    s = DiagnosisState(
        matched_fault=QLM,
        steps_required=list(qlm.step_ids),
        steps_claimed_done=set(ORDINARY),
        history_facts={HF_RESET_EARLIER: "yes"},
        intended_action=ACTION_RESET_QLM,
        tool_results={},          # no tool ever ran
        iter_count=0,             # no agent iteration ever happened
    )
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.REFUSE
    assert v.gate_type == "reset_limit"
    assert REASON_SECOND_RESET in v.reasons
    assert "§6.1.1(f)" in v.source


def test_reflex_is_pure_and_deterministic(qlm):
    s = _state(qlm, claimed=ORDINARY, history={HF_RESET_EARLIER: "yes"})
    verdicts = {evaluate_gates(s, qlm) for _ in range(5)}
    assert len(verdicts) == 1


# ---------------------------------------------------------------------------
# Rule 1 — second reset → REFUSE (§6.1.1(f))
# ---------------------------------------------------------------------------

def test_second_reset_refused_with_intent(qlm):
    s = _state(qlm, claimed=ORDINARY, history={HF_RESET_EARLIER: "yes"}, intent=ACTION_RESET_QLM)
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.REFUSE and v.rule == "1"
    assert "DO NOT reset" in v.message and "relief loco" in v.message


def test_second_reset_refused_without_intent(qlm):
    """Fix A / BUILD_PLAN §12.1 gold scenario: 'Yes, I reset it once already this trip.'
    states no intent — the refusal must still fire on the fact alone."""
    s = _state(qlm, history={HF_RESET_EARLIER: "yes"})
    assert evaluate_gates(s, qlm).outcome is Outcome.REFUSE


def test_second_reset_refused_even_with_checks_incomplete(qlm):
    s = _state(qlm, claimed=ORDINARY[:1], history={HF_RESET_EARLIER: "yes"}, intent=ACTION_RESET_QLM)
    assert evaluate_gates(s, qlm).outcome is Outcome.REFUSE


def test_second_reset_refused_after_the_fact(qlm):
    """Pilot claims the reset is done but history says it was already reset earlier:
    (f) still applies — log, TLC, relief loco. (The prior reset is known BEFORE the reset
    claim arrives; a claim and a 'yes' in one first-turn update is one reset booked twice —
    see engine.state.update_state and test_recurrence.)"""
    s = _state(qlm, claimed=ORDINARY, history={HF_RESET_EARLIER: "yes"})
    update_state(s, StateUpdate(claimed_steps=(RESET_STEP,)), qlm)
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.REFUSE and REASON_SECOND_RESET in v.reasons


def test_gate_still_refuses_when_state_carries_both_facts(qlm):
    """The gate is fact-driven: if the state itself holds reset-done + prior-reset, REFUSE."""
    s = _state(qlm, claimed=(*ORDINARY, RESET_STEP))
    s.history_facts[HF_RESET_EARLIER] = "yes"
    assert evaluate_gates(s, qlm).outcome is Outcome.REFUSE


# ---------------------------------------------------------------------------
# Rule 2 — abnormality → REFUSE (§6.1.1(c)(f))
# ---------------------------------------------------------------------------

def test_abnormality_refused_without_intent(qlm):
    """(a)/(b) finding → generic (f)(i): do not reset, log, TLC, relief loco — and NOT (c)'s
    extinguisher text, which is tied to (c)'s own findings."""
    s = _state(qlm, claimed=ORDINARY[:1], history={HF_ABNORMALITY: "yes"})
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.REFUSE and v.rule == "2"
    assert v.reasons == (REASON_ABNORMALITY,)
    assert "do not reset" in v.message.lower() and "relief loco" in v.message.lower()
    assert "tlc" in v.message.lower() and "log" in v.message.lower()
    assert "fire extinguisher" not in v.message.lower()


def test_arc_chute_finding_adds_c_inline_consequence(qlm):
    """(c) finding → generic (f)(i) PLUS (c)'s 'use fire extinguisher; ask for relief engine'."""
    s = _state(qlm, claimed=ORDINARY, history={HF_ABNORMALITY: "yes", "arc_chute_terminal_abnormality": "yes"})
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.REFUSE and v.reasons == (REASON_ABNORMALITY,)
    assert "fire extinguisher" in v.message.lower() and "relief engine" in v.message.lower()
    assert "do not reset" in v.message.lower()


def test_abnormality_refused_with_intent_and_history_no(qlm):
    s = _state(qlm, claimed=ORDINARY, history={HF_ABNORMALITY: "yes", HF_RESET_EARLIER: "no"},
               intent=ACTION_RESET_QLM)
    assert evaluate_gates(s, qlm).outcome is Outcome.REFUSE


def test_both_reasons_are_kept(qlm):
    """Fix B: second reset AND abnormality → one REFUSE carrying both, and the
    fire-precaution text is not dropped."""
    s = _state(qlm, claimed=ORDINARY, history={HF_ABNORMALITY: "yes", HF_RESET_EARLIER: "yes",
                                               "arc_chute_terminal_abnormality": "yes"})
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.REFUSE
    assert set(v.reasons) == {REASON_SECOND_RESET, REASON_ABNORMALITY}
    assert "DO NOT reset" in v.message and "fire extinguisher" in v.message.lower()


# ---------------------------------------------------------------------------
# Rule 3 — checks incomplete → NO_FIRE (reset stays behind the checks, §6.1.1(d))
# ---------------------------------------------------------------------------

def test_no_fire_when_nothing_claimed_and_no_intent(qlm):
    assert not evaluate_gates(_state(qlm), qlm).fired          # §10.2 Turn 1 position


def test_no_fire_when_checks_incomplete_even_with_reset_intent(qlm):
    s = _state(qlm, claimed=ORDINARY[:2], intent=ACTION_RESET_QLM)
    assert not evaluate_gates(s, qlm).fired


def test_no_fire_when_checks_incomplete_history_no(qlm):
    s = _state(qlm, claimed=ORDINARY[:2], history={HF_RESET_EARLIER: "no"}, intent=ACTION_RESET_QLM)
    assert not evaluate_gates(s, qlm).fired


# ---------------------------------------------------------------------------
# Rule 4 — in play, history unknown → ASK (§2.3)
# ---------------------------------------------------------------------------

def test_ask_history_when_checks_complete_and_history_unknown(qlm):
    s = _state(qlm, claimed=ORDINARY, history={HF_ABNORMALITY: "no"})
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.ASK and v.rule == "4"
    assert "log book" in v.question


def test_ask_history_fires_without_reset_intent(qlm):
    """Checks complete means reset is the procedure's next step; the one history
    question is asked whether or not the pilot said they intend to reset."""
    s = _state(qlm, claimed=ORDINARY)
    assert evaluate_gates(s, qlm).outcome is Outcome.ASK


def test_history_not_reasked_when_volunteered(qlm):
    """§2.3: volunteered 'not reset earlier' → trust it, never ASK."""
    s = _state(qlm, claimed=ORDINARY, history={HF_RESET_EARLIER: "no", HF_ABNORMALITY: "no"})
    assert evaluate_gates(s, qlm).outcome is not Outcome.ASK


# ---------------------------------------------------------------------------
# Rule 5 — first reset → CAUTION (§6.1.1(d)(e))
# ---------------------------------------------------------------------------

def test_first_reset_caution(qlm):
    s = _state(qlm, claimed=ORDINARY, history={HF_RESET_EARLIER: "no", HF_ABNORMALITY: "no"},
               intent=ACTION_RESET_QLM)
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.CAUTION and v.rule == "5"
    assert v.conditional is False
    assert "once" in v.message and "10 minutes" in v.message
    assert "log book" in v.message and "TLC" in v.message
    assert "§6.1.1(d)" in v.source and "§6.1.1(e)" in v.source


def test_first_reset_caution_is_conditional_when_abnormality_unknown(qlm):
    """Fix C: checks claimed but no verdict stated → the caution is worded as §6.1.1(d)
    words it: 'if there is no abnormality'."""
    s = _state(qlm, claimed=ORDINARY, history={HF_RESET_EARLIER: "no"})
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.CAUTION and v.conditional is True


# ---------------------------------------------------------------------------
# Rule 6 — first reset already done, history no → NO_FIRE (reassess confirms)
# ---------------------------------------------------------------------------

def test_no_fire_after_permitted_first_reset(qlm):
    s = _state(qlm, claimed=(*ORDINARY, RESET_STEP), history={HF_RESET_EARLIER: "no", HF_ABNORMALITY: "no"})
    assert not evaluate_gates(s, qlm).fired


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

def test_no_fault_no_fire():
    assert not evaluate_gates(DiagnosisState(), None).fired


def test_every_kb_gate_type_has_an_evaluator():
    assert set(GATE_EVALUATORS) == set(GATE_TYPES)


def test_unimplemented_gate_types_fail_closed(qlm):
    """A gate type without an evaluator must raise, never silently NO_FIRE."""
    s = _state(qlm)
    step = qlm.step(RESET_STEP)
    for t in ("isolation_before_contact",):          # hazard_exposure implemented in M4b
        with pytest.raises(NotImplementedError):
            GATE_EVALUATORS[t](s, qlm, step)


def test_evaluator_rejects_wrong_gate_type(qlm):
    s = _state(qlm)
    with pytest.raises(AssertionError):
        evaluate_reset_limit(s, qlm, qlm.step(ORDINARY[0]))
