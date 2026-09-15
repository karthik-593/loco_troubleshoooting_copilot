"""M4a: combination faults (§6.1.2 / §6.1.3 isolate-then-reset) and sanders (§10.12 gate-free)."""
import pytest

from agent.graph import Copilot
from engine.gates import Outcome, evaluate_gates
from engine.run_turn import run_turn
from engine.state import (
    HF_ABNORMALITY,
    HF_RESET_EARLIER,
    HF_RESOLVED,
    DiagnosisState,
    StateUpdate,
    update_state,
)
from engine.terminals import REASON_ABNORMALITY, REASON_NOT_ISOLATED, REASON_SECOND_RESET
from llm.interface import FakeProvider, Providers
from llm.parse import kb_vocabulary, parse_turn
from llm.schemas import DecideOutput, ParseOutput
from tests.conftest import ORDINARY, RESET_STEP

QOP = "QLM_with_QOP_QRSI"
QLA = "QLM_with_QLA_QOA"
SANDERS = "sanders_not_working"
TRACTION = "check_traction_power_circuit"
AUX = "check_auxiliary_power_circuit"
SANDER_STEPS = ("check_psa_and_sander_cocs", "check_lamps_and_ccls", "operate_vesas_manually",
                "check_sand_in_sandboxes", "tap_sander_nozzle", "work_without_sanders",
                "progress_notches_gradually")


def _state(kb, fid, claimed=(), history=None):
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=fid, claimed_steps=tuple(claimed), history=history or {}), kb.get(fid))
    return s


# ---------------------------------------------------------------------------
# KB shape
# ---------------------------------------------------------------------------

def test_combination_faults_reuse_qlm_step_ids(kb):
    for fid, extra in ((QOP, TRACTION), (QLA, AUX)):
        f = kb.get(fid)
        assert [s.id for s in f.ordinary_steps] == [*ORDINARY, extra]
        assert [s.id for s in f.gated_steps] == [RESET_STEP]
        assert f.step(extra).isolation is not None and f.step(extra).abnormality_key
        assert "§6.1.1" in f.step(ORDINARY[0]).citation           # (a) "follow 6.1.1"
        assert f.combination_rules == []


def test_qlm_rules_route_to_encoded_faults(kb):
    routes = {r.route_to for r in kb.get("QLM_dropped").combination_rules}
    assert routes == {QOP, QLA} and routes <= set(kb.fault_ids)


def test_sanders_is_gate_free_and_linear(kb):
    f = kb.get(SANDERS)
    assert f.gated_steps == [] and [s.id for s in f.ordinary_steps] == list(SANDER_STEPS)
    assert all("§10.12" in s.citation for s in f.steps)
    assert f.history_keys == []


def test_vocabulary_lists_fault_specific_facts(kb):
    v = kb_vocabulary(kb)
    for k in ("traction_abnormality_found", "aux_abnormality_found", "isolation_successful"):
        assert k in v


# ---------------------------------------------------------------------------
# isolate-then-reset gate (§6.1.2(b)) — every branch
# ---------------------------------------------------------------------------

def test_traction_abnormality_isolation_unknown_asks(kb):
    s = _state(kb, QOP, claimed=[*ORDINARY, TRACTION],
               history={HF_ABNORMALITY: "no", "traction_abnormality_found": "yes"})
    v = evaluate_gates(s, kb.get(QOP))
    assert v.outcome is Outcome.ASK and v.rule == "2b"
    assert "isolate" in v.question.lower() and "§6.1.2(b)" in v.source


def test_traction_abnormality_isolated_permits_reset_caution(kb):
    s = _state(kb, QOP, claimed=[*ORDINARY, TRACTION],
               history={HF_ABNORMALITY: "no", "traction_abnormality_found": "yes",
                        "isolation_successful": "yes", HF_RESET_EARLIER: "no"})
    v = evaluate_gates(s, kb.get(QOP))
    assert v.outcome is Outcome.CAUTION and v.rule == "5-isolated"
    assert "reset the relay targets and resume traction" in v.message.lower()
    assert "§6.1.2(b)" in v.source and not v.conditional


def test_traction_abnormality_not_isolated_refuses_to_tlc(kb):
    s = _state(kb, QOP, claimed=[*ORDINARY, TRACTION],
               history={HF_ABNORMALITY: "no", "traction_abnormality_found": "yes",
                        "isolation_successful": "no", HF_RESET_EARLIER: "no"})
    v = evaluate_gates(s, kb.get(QOP))
    assert v.outcome is Outcome.REFUSE and v.reasons == (REASON_NOT_ISOLATED,)
    assert "tlc" in v.message.lower()


def test_isolated_but_history_unknown_still_asks_reset_history(kb):
    s = _state(kb, QOP, claimed=[*ORDINARY, TRACTION],
               history={HF_ABNORMALITY: "no", "traction_abnormality_found": "yes", "isolation_successful": "yes"})
    assert evaluate_gates(s, kb.get(QOP)).rule == "4"


def test_feeding_circuit_abnormality_still_refuses_even_if_isolated(kb):
    """§6.1.1(f) via §6.1.2(a): feeding-power-circuit abnormality is never an isolate case."""
    s = _state(kb, QOP, claimed=[*ORDINARY, TRACTION],
               history={HF_ABNORMALITY: "yes", "traction_abnormality_found": "yes",
                        "isolation_successful": "yes", HF_RESET_EARLIER: "no"})
    v = evaluate_gates(s, kb.get(QOP))
    assert v.outcome is Outcome.REFUSE and REASON_ABNORMALITY in v.reasons


def test_second_time_refuses_even_with_no_abnormality(kb):
    """§6.1.2(d): 'ask for relief loco even though there is no abnormality'."""
    s = _state(kb, QOP, history={HF_RESET_EARLIER: "yes", HF_ABNORMALITY: "no"})
    v = evaluate_gates(s, kb.get(QOP))
    assert v.outcome is Outcome.REFUSE and v.reasons == (REASON_SECOND_RESET,)
    assert "even though there is no abnormality" in v.message


def test_aux_branch_mirrors_traction_branch(kb):
    s = _state(kb, QLA, claimed=[*ORDINARY, AUX],
               history={HF_ABNORMALITY: "no", "aux_abnormality_found": "yes",
                        "isolation_successful": "no"})
    v = evaluate_gates(s, kb.get(QLA))
    assert v.outcome is Outcome.REFUSE and v.reasons == (REASON_NOT_ISOLATED,)
    assert "§6.1.3" in kb.get(QLA).step(AUX).isolation.source


def test_clean_combination_run_asks_traction_check_then_history(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=QOP, claimed_steps=ORDINARY, history={HF_ABNORMALITY: "no"}), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == TRACTION
    t = run_turn(d, StateUpdate(claimed_steps=(TRACTION,), history={"traction_abnormality_found": "no"}), kb)
    assert t.terminal.kind == "ask_history"
    t = run_turn(d, StateUpdate(history={HF_RESET_EARLIER: "no"}), kb)
    assert t.terminal.kind == "caution" and "other relay targets" in t.terminal.message


# ---------------------------------------------------------------------------
# parse: KB-declared facts are accepted, unknown keys dropped
# ---------------------------------------------------------------------------

def test_parse_maps_kb_declared_facts_only(kb):
    p = FakeProvider(structured_queue=[ParseOutput(
        fault_guess=None, fault_confidence=0.0,
        facts={"traction_abnormality_found": "yes", "isolation_successful": "yes", "made_up_fact": "yes"})])
    r = parse_turn("QLM with QOP, RSI-1 was sparking, isolated it", DiagnosisState(), kb, p)
    assert r.update.fault_id == QOP and r.update.fault_confirmed is True
    assert r.update.history == {"traction_abnormality_found": "yes", "isolation_successful": "yes"}


# ---------------------------------------------------------------------------
# sanders — benign, no confirmation turn, resolves at any step
# ---------------------------------------------------------------------------

def _sanders_copilot(parse_outs, decide_tools):
    return Copilot(Providers(
        parse=FakeProvider(structured_queue=list(parse_outs)),
        decide=FakeProvider(structured_queue=[DecideOutput(tool=t, reason="t") for t in decide_tools]),
        phrase=FakeProvider()))


def test_sanders_no_confirmation_turn_even_when_llm_guessed(kb):
    """§5.5 applies to hard-gated faults only: a gate-free fault goes straight to guidance."""
    cp = _sanders_copilot([ParseOutput(fault_guess=SANDERS, fault_confidence=0.8)], ["diff_completed_steps"])
    r = cp.turn(DiagnosisState(), "sand not coming when i press the pedal")
    assert r.terminal.kind == "ask_step" and r.terminal.step_id == SANDER_STEPS[0]
    assert r.reflex_runs == 2 and r.tool_path == ("diff_completed_steps",)


def test_sanders_resolved_at_first_step_confirms(kb):
    d = DiagnosisState()
    cp = _sanders_copilot(
        [ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[SANDER_STEPS[0]], fault_resolved="yes")],
        ["diff_completed_steps"])
    r = cp.turn(d, "sanders not working — COCs were closed, opened them, working now")
    assert r.terminal.kind == "confirm"
    assert r.terminal.guidance == ("resume traction",)
    assert d.history_facts[HF_RESOLVED] == "yes"


def test_sanders_all_steps_tried_gives_unresolved_terminal(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=SANDERS, claimed_steps=SANDER_STEPS), kb)
    assert t.terminal.kind == "confirm"
    assert any("work without sanders" in g for g in t.terminal.guidance)
    assert any("TLC" in g for g in t.terminal.guidance)


def test_sanders_specific_miss(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=SANDERS, claimed_steps=(SANDER_STEPS[0], SANDER_STEPS[2])), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == SANDER_STEPS[1]
    assert not t.verdict_after_update.fired


def test_reflex_never_fires_on_a_gate_free_fault(kb):
    s = _state(kb, SANDERS, claimed=SANDER_STEPS,
               history={HF_RESET_EARLIER: "yes", HF_ABNORMALITY: "yes"})   # irrelevant facts
    assert not evaluate_gates(s, kb.get(SANDERS)).fired
