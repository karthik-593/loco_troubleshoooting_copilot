"""agent_decide: bounded to the registered Class-A set; anything else is rejected."""
from engine.state import StateUpdate, update_state
from engine.tools import TOOL_NAMES, run_tool
from llm.decide import agent_decide, state_summary, system_prompt
from llm.interface import FakeProvider
from llm.schemas import DecideOutput
from tests.conftest import ORDINARY


def _fake(tool, reason="need it"):
    return FakeProvider(structured_queue=[DecideOutput(tool=tool, reason=reason)])


def test_selects_registered_tool(qlm_state, qlm):
    c = agent_decide(qlm_state, qlm, _fake("diff_completed_steps"))
    assert c.tool == "diff_completed_steps" and c.rejected is None


def test_none_stops_looping(qlm_state, qlm):
    for name in ("none", "NONE", ""):
        c = agent_decide(qlm_state, qlm, _fake(name))
        assert c.tool is None and c.rejected is None


def test_unregistered_tool_is_rejected(qlm_state, qlm):
    """§6 bounded toolset / §5.1: asking for the safety check as a tool is refused."""
    for name in ("check_safety_gate", "evaluate_gates", "reset_QLM", "google_it"):
        c = agent_decide(qlm_state, qlm, _fake(name))
        assert c.tool is None and c.rejected == name


def test_prompt_lists_only_registered_tools_and_no_safety_option(qlm_state, qlm):
    sp = system_prompt()
    for name in TOOL_NAMES:
        assert name in sp
    assert "check_safety_gate" not in sp and "evaluate_gates" not in sp
    assert "{tool_registry}" not in sp


def test_state_summary_marks_fresh_and_stale_results(qlm_state, qlm):
    update_state(qlm_state, StateUpdate(claimed_steps=ORDINARY[:1]), qlm)
    run_tool("diff_completed_steps", qlm_state, qlm)
    assert "fresh for the current state: ['diff_completed_steps']" in state_summary(qlm_state, qlm)
    update_state(qlm_state, StateUpdate(claimed_steps=ORDINARY[1:2]), qlm)
    s = state_summary(qlm_state, qlm)
    assert "stale (state changed since): ['diff_completed_steps']" in s
