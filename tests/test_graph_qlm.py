"""End-to-end QLM through the LangGraph loop with scripted LLMs (BUILD_PLAN §10.2, §12.2
tool-path divergence, §5.5 confirmation)."""
from agent.graph import REFLEX_SHORT_CIRCUIT, Copilot
from engine.state import DiagnosisState
from engine.terminals import REASON_SECOND_RESET
from llm.interface import FakeProvider, Providers
from llm.schemas import DecideOutput, ParseOutput
from tests.conftest import ORDINARY, QLM, RESET_STEP


def _prov(parse_outs, decide_tools, phrases=()):
    return Providers(
        parse=FakeProvider(structured_queue=list(parse_outs)),
        decide=FakeProvider(structured_queue=[DecideOutput(tool=t, reason="t") for t in decide_tools]),
        phrase=FakeProvider(text_queue=list(phrases)),
    )


def test_build_plan_10_2_trace_through_the_graph():
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=list(ORDINARY[:2])),
         ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[ORDINARY[2]],
                     abnormality_found="no", was_reset_earlier_this_trip="yes", intended_action="reset_QLM")],
        ["diff_completed_steps"],
        ["Next, have you checked the CGR arc chutes, RGR/RPGR and the transformer terminals and bushings?",
         "Do not reset QLM again. Make the log-book remark, inform TLC and arrange a relief loco."]))

    # TURN 1
    t1 = cp.turn(d, "QLM locked. I checked the transformer and oil level.")
    assert t1.tool_path == ("diff_completed_steps",)
    assert t1.reflex_runs == 2 and t1.stop_reason == "reassess"
    assert t1.terminal.kind == "ask_step" and t1.terminal.step_id == ORDINARY[2]
    assert d.stuck_at == ORDINARY[2]
    assert not t1.phrase_fallback and t1.reply.endswith("?")

    # TURN 2 — reflex short-circuit; agent_decide never runs
    t2 = cp.turn(d, "Checked, all normal. I reset QLM once earlier this trip.", last_assistant=t1.reply)
    assert t2.tool_path == (REFLEX_SHORT_CIRCUIT,)
    assert t2.reflex_runs == 1 and t2.stop_reason == "gate"
    assert t2.terminal.kind == "refuse" and t2.terminal.reasons == (REASON_SECOND_RESET,)
    assert "not reset" in t2.reply.lower()
    assert len(cp.providers.decide.calls) == 1                       # only turn 1 consulted the agent


def test_confirm_path_pilot_did_it_right():
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[*ORDINARY, RESET_STEP],
                     abnormality_found="no", was_reset_earlier_this_trip="no")],
        ["diff_completed_steps"]))
    r = cp.turn(DiagnosisState(), "QLM acted first time, checked ht2, oil, arc chutes all ok, reset once, resumed")
    assert r.terminal.kind == "confirm" and r.tool_path == ("diff_completed_steps",)
    assert any("10 minutes" in g for g in r.terminal.guidance)


def test_tool_path_diverges_for_combination_fault():
    """§12.2 proof-of-agency: reported relays → a different tool path and a reroute into
    the KB's combination fault, versus the single-tool path for clean QLM."""
    clean = Copilot(_prov([ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=list(ORDINARY[:2]))],
                          ["diff_completed_steps"])).turn(DiagnosisState(), "QLM locked, checked tfp and oil")
    d = DiagnosisState()
    comb = Copilot(_prov([ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=list(ORDINARY[:2]),
                                      other_relays_acted=["QOP-1"])],
                         ["get_required_observations", "check_combination", "diff_completed_steps"])
                   ).turn(d, "QLM locked and QOP-1 also, checked tfp and oil")
    assert clean.tool_path == ("diff_completed_steps",)
    # identity is resolved deterministically at update time (before the reflex); the agent's
    # own tool choices then differ from the clean case too.
    assert comb.tool_path == ("(reroute→QLM_with_QOP_QRSI)", "get_required_observations",
                              "check_combination", "diff_completed_steps")
    assert d.matched_fault == "QLM_with_QOP_QRSI"
    assert d.steps_claimed_done == set(ORDINARY[:2])                # claims carried across the reroute
    assert comb.terminal.kind == "ask_step" and comb.terminal.step_id == ORDINARY[2]
    assert "§6.1.2(a)" in comb.terminal.source
    assert comb.reflex_runs == 4                                    # after update (rerouted) + 3 tools


def test_combination_pending_keeps_looping_until_checked():
    """Fresh diff alone is not enough when relays were reported: reassess loops back."""
    d = DiagnosisState()
    cp = Copilot(_prov([ParseOutput(fault_guess=None, fault_confidence=0.0, other_relays_acted=["QLA"])],
                       ["diff_completed_steps", "check_combination", "diff_completed_steps"]))
    r = cp.turn(d, "QLM locked with QLA")
    assert r.tool_path[0] == "(reroute→QLM_with_QLA_QOA)"
    assert d.matched_fault == "QLM_with_QLA_QOA" and r.terminal.kind == "ask_step"


def test_reflex_evaluates_the_rerouted_fault_not_the_original():
    """Seen live 2026-09-15: all three QLM checks claimed + QOP-1 reported. Before the fix
    the reflex fired QLM_dropped's history question and short-circuited, so the reroute
    never happened and the pilot was later told to reset without §6.1.2(b)'s traction
    check. Now identity resolves first and the engine asks for the traction check."""
    d = DiagnosisState()
    cp = Copilot(_prov([ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=list(ORDINARY),
                                    abnormality_found="no", other_relays_acted=["QOP-1"])],
                       ["diff_completed_steps"]))
    r = cp.turn(d, "QLM locked and QOP-1 also dropped. checked ht2, oil, arc chutes - all normal")
    assert d.matched_fault == "QLM_with_QOP_QRSI"
    assert r.terminal.kind == "ask_step" and r.terminal.step_id == "check_traction_power_circuit"
    assert r.tool_path == ("(reroute→QLM_with_QOP_QRSI)", "diff_completed_steps")


def test_llm_guessed_fault_gets_confirmation_not_tools():
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=QLM, fault_confidence=0.9, claimed_steps=[ORDINARY[0]]),
         ParseOutput(fault_guess=None, fault_confidence=0.0, confirms_fault="yes")],
        ["diff_completed_steps"]))
    t1 = cp.turn(d, "dj gone, big relay red, checked ht2")
    assert t1.terminal.kind == "confirm_fault" and t1.tool_path == () and t1.stop_reason == "reassess"
    assert cp.providers.decide.calls == []
    t2 = cp.turn(d, "yes", last_assistant=t1.reply)
    assert t2.terminal.kind == "ask_step" and t2.terminal.step_id == ORDINARY[1]
    assert t2.tool_path == ("diff_completed_steps",)


def test_low_confidence_clarifies_without_engine_or_agent():
    cp = Copilot(_prov([ParseOutput(fault_guess=QLM, fault_confidence=0.2)], ["diff_completed_steps"]))
    d = DiagnosisState()
    r = cp.turn(d, "something tripped")
    assert r.terminal.kind == "clarify" and r.stop_reason == "clarify"
    assert r.reflex_runs == 0 and r.tool_path == () and d.matched_fault is None


def test_unknown_fault_defers():
    cp = Copilot(_prov([ParseOutput(fault_guess="wipers_not_working", fault_confidence=0.95)], []))
    r = cp.turn(DiagnosisState(), "wipers dead")
    assert r.terminal.kind == "defer_to_TLC" and r.stop_reason == "out_of_scope"   # never a guessed procedure
    assert "isn't in my procedure set" in r.terminal.message and "QLM dropped" in r.terminal.message
    assert r.reflex_runs == 0 and r.tool_path == ()


def test_clarify_is_asked_once_then_out_of_scope_defers():
    """§5.6 backstop through the graph: two unresolved turns → clarify, then defer — never the
    same question twice. A later real fault resets the counter."""
    vague = ParseOutput(fault_guess=None, fault_confidence=0.0)
    cp = Copilot(_prov([vague, vague, ParseOutput(fault_guess=QLM, fault_confidence=0.9, fault_presenting="yes")],
                       ["diff_completed_steps"]))
    d = DiagnosisState()
    assert cp.turn(d, "loco stopped").terminal.kind == "clarify" and d.clarify_asked == 1
    r = cp.turn(d, "no relays dropped, just stopped")
    assert r.terminal.kind == "defer_to_TLC" and r.stop_reason == "out_of_scope" and d.matched_fault is None
    r = cp.turn(d, "QLM dropped")
    assert r.terminal.kind != "defer_to_TLC" and d.matched_fault == QLM and d.clarify_asked == 0


def test_state_persists_across_turns_and_iter_resets():
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[ORDINARY[0]]),
         ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[ORDINARY[1]])],
        ["diff_completed_steps", "diff_completed_steps"]))
    cp.turn(d, "QLM locked, checked ht2")
    r = cp.turn(d, "oil ok too")
    assert d.steps_claimed_done == {ORDINARY[0], ORDINARY[1]}
    assert r.terminal.step_id == ORDINARY[2] and r.iterations == 1
