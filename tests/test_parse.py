"""parse: model output is validated against the KB; low confidence → clarify, never act;
alias matches are deterministic and confirmed; LLM-guessed hard-gated faults are not."""
from engine.state import (
    ACTION_RESET_QLM,
    HF_ABNORMALITY,
    HF_RESET_EARLIER,
    DiagnosisState,
    StateUpdate,
    update_state,
)
from engine.tools import HF_OTHER_RELAYS
from llm.interface import FakeProvider
from llm.parse import CLARIFY_THRESHOLD, kb_vocabulary, parse_turn, system_prompt
from llm.schemas import ParseOutput
from tests.conftest import ORDINARY, QLM


def _fake(*outs):
    return FakeProvider(structured_queue=list(outs))


def test_vocabulary_is_built_from_kb_only(kb):
    v = kb_vocabulary(kb)
    assert QLM in v and "QLM locked" in v
    for sid in ORDINARY:
        assert f"{QLM}.{sid}" in v
    assert "reset_QLM" in v
    assert "{kb_vocabulary}" not in system_prompt(kb)


def test_alias_match_is_deterministic_and_confirmed(kb):
    p = _fake(ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[ORDINARY[0]]))
    r = parse_turn("QLM locked", DiagnosisState(), kb, p)
    assert r.update.fault_id == QLM and r.update.fault_confirmed is True
    assert r.confidence == 1.0 and not r.needs_clarification
    assert r.update.claimed_steps == (ORDINARY[0],)


def test_llm_guess_of_gated_fault_is_not_confirmed(kb):
    p = _fake(ParseOutput(fault_guess=QLM, fault_confidence=0.85))
    r = parse_turn("dj gone, main relay red", DiagnosisState(), kb, p)
    assert r.update.fault_id == QLM and r.update.fault_confirmed is False   # §5.5


def test_low_confidence_clarifies_instead_of_acting(kb):
    p = _fake(ParseOutput(fault_guess=QLM, fault_confidence=CLARIFY_THRESHOLD - 0.1,
                          claimed_steps=[ORDINARY[0]]))
    r = parse_turn("the main relay thing is red", DiagnosisState(), kb, p)
    assert r.update is None and r.needs_clarification and "?" in r.clarification


def test_unknown_fault_guess_is_dropped(kb):
    """A fault named OUTSIDE the list is never acted on — and, per §5.6, it is out of scope
    (a graceful defer), not a clarification loop."""
    p = _fake(ParseOutput(fault_guess="wipers_not_working", fault_confidence=0.95))
    r = parse_turn("wipers dead", DiagnosisState(), kb, p)
    assert r.update is None and r.out_of_scope and not r.needs_clarification


def test_described_problem_with_no_listed_fault_is_out_of_scope(kb):
    p = _fake(ParseOutput(fault_guess=None, fault_confidence=0.0, problem_outside_list="yes"))
    r = parse_turn("headlight not working", DiagnosisState(), kb, p)
    assert r.update is None and r.out_of_scope


def test_in_scope_but_underspecified_clarifies_not_defers(kb):
    """'DJ tripped, relay unknown' is inside the procedure set: since batch 2 it is the intake
    hub (§5.01 / Ch.7 intro precheck), matched deterministically — never §5.6, never a clarify loop."""
    p = _fake(ParseOutput(fault_guess=None, fault_confidence=0.0, problem_outside_list="no"))
    r = parse_turn("dj tripped, dont know which relay", DiagnosisState(), kb, p)
    assert r.update is not None and r.update.fault_id == "DJ_tripped_on_line" and not r.out_of_scope


def test_fact_key_of_another_fault_family_is_dropped(kb):
    """Facts are KB-validated per family: a QRSI-1 key on a QLM message is dropped, not re-mapped."""
    p = _fake(ParseOutput(fault_guess="QLM_dropped", fault_confidence=0.9,
                          facts={"traction1_abnormality_found": "yes", "traction_abnormality_found": "yes"}))
    r = parse_turn("QLM locked with QOP-1, RSI-1 smoking", DiagnosisState(), kb, p)
    assert r.update.history.get("traction_abnormality_found") == "yes"      # QLM_with_QOP_QRSI is family
    assert "traction1_abnormality_found" not in r.update.history


def test_vague_message_clarifies_once_then_defers(kb):
    """Deterministic backstop: the same clarification is never asked twice in a session."""
    vague = ParseOutput(fault_guess=None, fault_confidence=0.0, problem_outside_list="unknown")
    d = DiagnosisState()
    r = parse_turn("something wrong", d, kb, _fake(vague))
    assert r.needs_clarification and not r.out_of_scope
    d.clarify_asked = 1                                  # the graph records the clarify
    r = parse_turn("dunno", d, kb, _fake(vague))
    assert r.out_of_scope and not r.needs_clarification


def test_claimed_step_not_in_kb_is_rejected_and_surfaced(kb):
    p = _fake(ParseOutput(fault_guess=None, fault_confidence=0.0,
                          claimed_steps=[ORDINARY[1], "check_pantograph"],
                          unmapped_claims=["checked the horn"]))
    s = DiagnosisState()
    r = parse_turn("QLM acted", s, kb, p)
    assert r.rejected_steps == ("check_pantograph", "checked the horn")
    update_state(s, r.update, kb.get(QLM))
    assert s.steps_claimed_done == {ORDINARY[1]}                   # never silently accepted
    assert s.tool_results["unrecognised_claims"] == ["check_pantograph"]


def test_prefixed_step_ids_are_accepted(kb):
    p = _fake(ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[f"{QLM}.{ORDINARY[0]}"]))
    r = parse_turn("QLM red", DiagnosisState(), kb, p)
    assert r.update.claimed_steps == (ORDINARY[0],)


def test_history_and_intent_mapping(kb):
    p = _fake(ParseOutput(fault_guess=None, fault_confidence=0.0,
                          abnormality_found="no", was_reset_earlier_this_trip="yes",
                          other_relays_acted=["qop-1"], intended_action="reset_QLM"))
    r = parse_turn("QLM locked, all normal, reset once already, qop-1 also down, resetting now",
                   DiagnosisState(), kb, p)
    assert r.update.history == {HF_ABNORMALITY: "no", HF_RESET_EARLIER: "yes", HF_OTHER_RELAYS: ["QOP-1"]}
    assert r.update.intended_action == ACTION_RESET_QLM


def test_unknown_tristate_is_not_written(kb):
    p = _fake(ParseOutput(fault_guess=None, fault_confidence=0.0))
    r = parse_turn("QLM locked", DiagnosisState(), kb, p)
    assert r.update.history == {}


def test_pilot_confirms_fault(kb):
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=QLM, fault_confirmed=False), kb.get(QLM))
    p = _fake(ParseOutput(fault_guess=None, fault_confidence=0.0, confirms_fault="yes"))
    r = parse_turn("yes", s, kb, p, last_assistant="Sounds like QLM dropped?")
    assert r.update.fault_id is None and r.update.fault_confirmed is True
    assert "Sounds like QLM dropped?" in p.calls[0]["user"]


def test_pilot_denies_fault_with_no_alternative_clarifies(kb):
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=QLM, fault_confirmed=False), kb.get(QLM))
    p = _fake(ParseOutput(fault_guess=None, fault_confidence=0.0, confirms_fault="no"))
    r = parse_turn("no, not that", s, kb, p)
    assert r.update is None and r.needs_clarification
