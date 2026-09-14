"""M2 end-to-end with a scripted model: free text → parse → engine → phrase.
Also the architectural invariant that the engine has no LLM dependency."""
import ast
from pathlib import Path

from engine.run_turn import run_turn
from engine.state import DiagnosisState, StateUpdate, update_state
from engine.terminals import REASON_SECOND_RESET
from llm.interface import FakeProvider
from llm.parse import parse_turn
from llm.phrase import phrase
from llm.schemas import ParseOutput
from tests.conftest import ORDINARY, QLM

ROOT = Path(__file__).resolve().parents[1]


def test_engine_package_never_imports_llm_or_sdk():
    """§5.1 / §3: safety and procedure logic must not depend on model code."""
    for py in (ROOT / "engine").glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not n.startswith(("llm", "anthropic", "langgraph", "openai")), f"{py.name} imports {n}"


def test_llm_guessed_qlm_is_confirmed_before_any_step_guidance(kb):
    """§5.5: model guesses QLM at 0.9 → one-line confirmation, not the first check."""
    s = DiagnosisState()
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess=QLM, fault_confidence=0.9,
                                                   claimed_steps=[ORDINARY[0]])])
    r = parse_turn("dj gone, big relay showing red", s, kb, p)
    t = run_turn(s, r.update, kb)
    assert t.terminal.kind == "confirm_fault"
    assert "QLM" in t.terminal.message and "?" in t.terminal.message
    assert s.steps_claimed_done == {ORDINARY[0]}      # the claim is kept, just not acted on yet

    # pilot: "yes" → confirmed → engine proceeds to the specific missed step
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0, confirms_fault="yes")])
    r = parse_turn("yes", s, kb, p, last_assistant=t.terminal.message)
    t = run_turn(s, r.update, kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == ORDINARY[1]


def test_refusal_fires_even_while_fault_unconfirmed(kb):
    """Refusing is always safe: history=yes on an unconfirmed guess still REFUSES."""
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=QLM, fault_confirmed=False, history={"was_QLM_reset_earlier_this_trip": "yes"}), kb.get(QLM))
    t = run_turn(s, StateUpdate(), kb)
    assert t.terminal.kind == "refuse" and t.short_circuit


def test_10_2_trace_free_text_end_to_end(kb):
    """§10.2 with the LLM jobs in the loop (scripted): alias-matched, so no confirm turn."""
    s = DiagnosisState()
    # Turn 1 — "QLM locked. I checked the transformer and oil level."
    p = FakeProvider(
        structured_queue=[ParseOutput(fault_guess=QLM, fault_confidence=0.95, claimed_steps=list(ORDINARY[:2]))],
        text_queue=["Next, have you checked the CGR arc chutes, RGR/RPGR and the transformer terminals and bushings?"])
    r = parse_turn("QLM locked. I checked the transformer and oil level.", s, kb, p)
    t1 = run_turn(s, r.update, kb)
    assert t1.terminal.kind == "ask_step" and t1.terminal.step_id == ORDINARY[2]
    ph1 = phrase(t1.terminal, p)
    assert not ph1.used_fallback and ph1.text.endswith("?")

    # Turn 2 — "Checked, all normal. I reset QLM once earlier this trip."
    p = FakeProvider(
        structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[ORDINARY[2]],
                                      abnormality_found="no", was_reset_earlier_this_trip="yes",
                                      intended_action="reset_QLM")],
        text_queue=["Do not reset QLM again. Make the log-book remark, inform TLC and arrange a relief loco."])
    r = parse_turn("Checked, all normal. I reset QLM once earlier this trip.", s, kb, p, last_assistant=ph1.text)
    t2 = run_turn(s, r.update, kb)
    assert t2.short_circuit and t2.terminal.kind == "refuse"
    assert t2.terminal.reasons == (REASON_SECOND_RESET,)
    ph2 = phrase(t2.terminal, p)
    assert not ph2.used_fallback and "not reset" in ph2.text.lower()


def test_low_confidence_turn_never_touches_state(kb):
    s = DiagnosisState()
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess=QLM, fault_confidence=0.3, claimed_steps=list(ORDINARY))])
    r = parse_turn("something tripped", s, kb, p)
    assert r.needs_clarification and r.update is None
    assert s.matched_fault is None and not s.steps_claimed_done
