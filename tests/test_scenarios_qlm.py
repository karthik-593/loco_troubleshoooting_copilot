"""Structured-input QLM scenarios through the M1 driver (engine/run_turn.py). No NLP:
each 'pilot turn' is the StateUpdate the M2 parser would have produced.

Covers the M1 DoD: the §10.2 Turn-1/Turn-2 trace, and confirm / miss / refuse.
"""
from engine.gates import Outcome
from engine.matcher import match_alias, match_free_text
from engine.run_turn import run_turn
from engine.state import (
    ACTION_RESET_QLM,
    HF_ABNORMALITY,
    HF_RESET_EARLIER,
    DiagnosisState,
    StateUpdate,
)
from engine.terminals import REASON_ABNORMALITY, REASON_SECOND_RESET
from tests.conftest import ORDINARY, QLM, RESET_STEP
import pytest


def test_build_plan_10_2_trace(kb):
    """Reproduce BUILD_PLAN §10.2 exactly, in explicit-loop form."""
    state = DiagnosisState()

    # TURN 1: "QLM dropped. I checked the transformer and oil level."
    t1 = run_turn(state, StateUpdate(fault_id=QLM, claimed_steps=ORDINARY[:2]), kb)
    assert not t1.verdict_after_update.fired          # "no intended_action → no gate fires"
    assert not t1.short_circuit
    assert t1.tool_path == ("diff",)
    assert t1.decision.route == "need_pilot_input"
    assert t1.terminal.kind == "ask_step"
    assert t1.terminal.step_id == "check_arc_chutes_and_terminals"
    assert state.stuck_at == "check_arc_chutes_and_terminals"
    assert "CGR" in t1.terminal.message                # engine text is the KB's, from §6.1.1(c)
    assert "§6.1.1(c)" in t1.terminal.source

    # TURN 2: "Checked, all normal. I reset QLM once earlier this trip."
    t2 = run_turn(state, StateUpdate(
        claimed_steps=(ORDINARY[2],),
        history={HF_ABNORMALITY: "no", HF_RESET_EARLIER: "yes"},
        intended_action=ACTION_RESET_QLM,
    ), kb)
    assert t2.verdict_after_update.outcome is Outcome.REFUSE
    assert t2.short_circuit                            # agent_decide skipped entirely
    assert t2.tool_path == ("(reflex short-circuit)",)
    assert t2.decision.route == "gate_terminal"
    assert t2.terminal.kind == "refuse"
    assert t2.terminal.reasons == (REASON_SECOND_RESET,)
    assert "DO NOT reset" in t2.terminal.message
    assert "log book" in t2.terminal.message and "TLC" in t2.terminal.message
    assert "relief loco" in t2.terminal.message
    assert not t2.terminal.instructs_reset


def test_scenario_12_1_second_reset_refusal_no_intent(kb):
    """BUILD_PLAN §12.1 gold scenario: 'DJ tripped, QLM is locked.' / 'Yes, I reset it
    once already this trip.' → refuse; gate reset_limit fired; never instruct reset."""
    state = DiagnosisState()
    t1 = run_turn(state, StateUpdate(fault_id=QLM), kb)
    assert t1.terminal.kind == "ask_step" and not t1.terminal.instructs_reset
    t2 = run_turn(state, StateUpdate(history={HF_RESET_EARLIER: "yes"}), kb)
    assert t2.terminal.kind == "refuse"
    assert t2.terminal.gate_type == "reset_limit"
    assert t2.tool_path == ("(reflex short-circuit)",)
    assert not t2.terminal.instructs_reset


def test_confirm_pilot_did_it_right(kb):
    """'QLM dropped, first time this trip. Checked HT-2, oil, arc chutes — all normal.
    Reset once, resumed.' → CONFIRM, no nitpicking, plus the KB's monitoring guidance."""
    state = DiagnosisState()
    t = run_turn(state, StateUpdate(
        fault_id=QLM,
        claimed_steps=(*ORDINARY, RESET_STEP),
        history={HF_RESET_EARLIER: "no", HF_ABNORMALITY: "no"},
    ), kb)
    assert not t.short_circuit
    assert t.terminal.kind == "confirm"
    assert t.decision.route == "terminal"
    assert any("10 minutes" in g for g in t.terminal.guidance)   # §6.1.1(d)(e)
    assert state.stuck_at is None


def test_miss_catches_the_specific_step(kb):
    """Pilot skipped the oil-level check (b) but did (a) and (c) → ask about (b) only."""
    state = DiagnosisState()
    t = run_turn(state, StateUpdate(fault_id=QLM, claimed_steps=(ORDINARY[0], ORDINARY[2])), kb)
    assert t.terminal.kind == "ask_step"
    assert t.terminal.step_id == "check_oil_levels"
    assert t.terminal.hold_action is None


def test_miss_with_reset_intent_holds_the_reset(kb):
    """Fix D: 'I'm going to reset' before finishing the checks → ask the missed check and
    flag that the reset is on hold (§6.1.1(d): reset only after 'no abnormality')."""
    state = DiagnosisState()
    t = run_turn(state, StateUpdate(fault_id=QLM, claimed_steps=ORDINARY[:2],
                                    intended_action=ACTION_RESET_QLM), kb)
    assert not t.short_circuit
    assert t.terminal.kind == "ask_step"
    assert t.terminal.step_id == ORDINARY[2]
    assert t.terminal.hold_action == ACTION_RESET_QLM


def test_first_reset_path_ask_then_caution(kb):
    """Checks done, history not stated → ASK the one question; 'no' → CAUTION."""
    state = DiagnosisState()
    t1 = run_turn(state, StateUpdate(fault_id=QLM, claimed_steps=ORDINARY,
                                     history={HF_ABNORMALITY: "no"}), kb)
    assert t1.short_circuit and t1.terminal.kind == "ask_history"
    assert "log book" in t1.terminal.message

    t2 = run_turn(state, StateUpdate(history={HF_RESET_EARLIER: "no"}, intended_action=ACTION_RESET_QLM), kb)
    assert t2.short_circuit and t2.terminal.kind == "caution"
    assert t2.terminal.instructs_reset               # the permitted first reset
    assert not t2.terminal.conditional
    assert "once" in t2.terminal.message

    # and after the pilot reports the reset done → confirm with monitoring guidance
    t3 = run_turn(state, StateUpdate(claimed_steps=(RESET_STEP,), clear_intended_action=True), kb)
    assert t3.terminal.kind == "confirm"


def test_abnormality_found_refuses_regardless_of_progress(kb):
    """Smoke seen during (a) → do not reset, fire precautions, relief — no more checks asked."""
    state = DiagnosisState()
    t = run_turn(state, StateUpdate(fault_id=QLM, claimed_steps=ORDINARY[:1],
                                    history={HF_ABNORMALITY: "yes"}), kb)
    assert t.short_circuit and t.terminal.kind == "refuse"
    assert t.terminal.reasons == (REASON_ABNORMALITY,)
    assert "relief loco" in t.terminal.message.lower()             # (f)(i) generic for an (a) finding
    assert "fire extinguisher" not in t.terminal.message.lower()    # (c)'s text stays on (c)
    assert not t.terminal.instructs_reset


def test_unknown_fault_defers_to_tlc(kb):
    """§5.6: not in the procedure set → never guess."""
    state = DiagnosisState()
    t = run_turn(state, StateUpdate(fault_id="wipers_not_working"), kb)
    assert t.terminal.kind == "defer_to_TLC"
    assert not t.short_circuit


def test_alias_match_and_llm_stub(kb):
    assert match_alias("qlm locked", kb).fault_id == QLM
    assert match_alias("  QLM  Target Dropped ", kb).fault_id == QLM
    assert match_alias("QLM_dropped", kb).fault_id == QLM
    assert match_alias("wipers not working", kb) is None
    with pytest.raises(NotImplementedError):
        match_free_text("dj tripped and the main relay thing is red", kb)


def test_alias_phrase_containment(kb):
    assert match_alias("QLM locked, all normal, resetting now", kb).fault_id == QLM
    assert match_alias("DJ tripped and qlm red target", kb).fault_id == QLM
    assert match_alias("QLM is locked", kb) is None          # not a verbatim alias phrase
    assert match_alias("QLM not locked", kb) is None
    assert match_alias("dj tripped", kb).fault_id == "DJ_tripped_on_line"     # batch 2 intake hub


def test_fault_id_words_match_as_alias(kb):
    """'QLM dropped' (the fault_id's own words, as in the §10.2 trace) is deterministic."""
    assert match_alias("QLM dropped. I checked the transformer and oil level.", kb).fault_id == QLM
