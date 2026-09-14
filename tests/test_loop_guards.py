"""§6 loop guards on the real LangGraph loop: max-iter, idempotency/no-progress, bounded
toolset, and the §5.1 skip-attempt guard (the reflex cannot be bypassed by tool choice)."""
from agent.graph import REFLEX_SHORT_CIRCUIT, Copilot
from engine.state import DiagnosisState, HF_RESET_EARLIER
from llm.interface import FakeProvider, Providers
from llm.phrase import render_verbatim
from llm.schemas import DecideOutput, ParseOutput
from tests.conftest import ORDINARY, QLM


def _providers(parse_outs, decide_tools):
    fp = FakeProvider(structured_queue=list(parse_outs))
    fd = FakeProvider(structured_queue=[DecideOutput(tool=t, reason="test") for t in decide_tools])
    return Providers(parse=fp, decide=fd, phrase=FakeProvider()), fp, fd   # phrase → verbatim fallback


def _turn1_parse():
    # alias-matched text is used, so fault_confirmed=True without a confirm turn
    return ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=list(ORDINARY[:2]))


TEXT = "QLM locked. checked transformer and oil"


def test_max_iter_cap_forces_engine_terminal():
    prov, fp, fd = _providers([_turn1_parse()], ["lookup_procedure", "resolve_config", "read_siv_screen", "lookup_procedure"])
    cp = Copilot(prov, max_iter=2)
    d = DiagnosisState()
    r = cp.turn(d, TEXT)
    assert r.stop_reason == "max_iter"
    assert r.iterations == 2
    assert r.tool_path == ("lookup_procedure", "resolve_config")      # third choice never made
    assert len(fd.calls) == 2
    assert r.terminal.kind == "ask_step" and r.terminal.step_id == ORDINARY[2]   # safe engine fallback
    assert r.reply == render_verbatim(r.terminal) and r.phrase_fallback


def test_idempotency_and_no_progress_detector():
    prov, fp, fd = _providers([_turn1_parse()], ["lookup_procedure", "lookup_procedure", "lookup_procedure"])
    r = Copilot(prov).turn(DiagnosisState(), TEXT)
    assert r.stop_reason == "no_progress"
    assert r.tool_path == ("lookup_procedure", "lookup_procedure")   # 2nd run was a cache hit → stop
    assert len(fd.calls) == 2                                        # never asked a 3rd time
    assert r.terminal.kind == "ask_step"


def test_unregistered_tool_is_rejected_and_never_run():
    prov, fp, fd = _providers([_turn1_parse()], ["check_safety_gate"])
    r = Copilot(prov).turn(DiagnosisState(), TEXT)
    assert r.stop_reason == "rejected"
    assert r.rejected_tools == ("check_safety_gate",)
    assert r.tool_path == ()                                         # not attempted
    assert r.terminal.kind == "ask_step"                             # engine still answers


def test_none_from_agent_yields_engine_terminal():
    prov, fp, fd = _providers([_turn1_parse()], ["none"])
    r = Copilot(prov).turn(DiagnosisState(), TEXT)
    assert r.stop_reason == "none" and r.tool_path == ()
    assert r.terminal.kind == "ask_step" and r.terminal.step_id == ORDINARY[2]


def test_reflex_runs_after_every_tool():
    prov, fp, fd = _providers([_turn1_parse()], ["lookup_procedure", "resolve_config", "diff_completed_steps"])
    r = Copilot(prov).turn(DiagnosisState(), TEXT)
    assert r.tool_path == ("lookup_procedure", "resolve_config", "diff_completed_steps")
    assert r.reflex_runs == 1 + 3                                    # after parse + after each tool
    assert r.stop_reason == "reassess"


def test_skip_attempt_guard_agent_never_consulted_when_gate_fires():
    """§5.1: with a firing state, the agent is not even asked — no tool choice can suppress
    the gate. The decide provider has tools queued; none are consumed."""
    prov, fp, fd = _providers(
        [ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=list(ORDINARY),
                     was_reset_earlier_this_trip="yes", intended_action="reset_QLM")],
        ["lookup_procedure", "diff_completed_steps"])
    r = Copilot(prov).turn(DiagnosisState(), "QLM locked, all checked, reset once already, resetting")
    assert r.terminal.kind == "refuse"
    assert r.tool_path == (REFLEX_SHORT_CIRCUIT,)
    assert r.reflex_runs == 1
    assert fd.calls == []                                            # agent_decide never entered
    assert r.iterations == 0


def test_gate_fires_after_a_tool_too():
    """A tool cannot change history facts, but the reflex still evaluates after it; here the
    firing fact arrives with the parse, so the post-parse reflex catches it first."""
    d = DiagnosisState()
    prov, fp, fd = _providers(
        [ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=list(ORDINARY[:1]),
                     abnormality_found="yes")], ["diff_completed_steps"])
    r = Copilot(prov).turn(d, "QLM locked, smoke from HT2")
    assert r.terminal.kind == "refuse" and r.tool_path == (REFLEX_SHORT_CIRCUIT,)
    assert fd.calls == []
