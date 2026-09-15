"""M4b: pantograph_damaged (§10.03/§11.04 hazard-exposure gate) and QRSI1_drops_on_run
(§6.02.1 isolate-and-retest ladder, gate-free)."""
from agent.graph import REFLEX_SHORT_CIRCUIT, Copilot
from engine.gates import Outcome, evaluate_gates
from engine.run_turn import run_turn
from engine.state import ACTION_WORK_ON_ROOF, HF_RESOLVED, DiagnosisState, StateUpdate, update_state
from llm.interface import FakeProvider, Providers
from llm.schemas import DecideOutput, ParseOutput

PANTO = "pantograph_damaged"
QRSI = "QRSI1_drops_on_run"
P_STEPS = ("lower_pantograph_immediately", "check_bp_and_protect_train", "obtain_emergency_power_block",
           "secure_damaged_pantograph_on_roof", "earth_damaged_pantograph_hpt",
           "clear_roof_unground_and_raise_good_panto", "resume_traction_and_report")
ROOF = "secure_damaged_pantograph_on_roof"
PRE = {"ohe_power_block_obtained_and_earthed": "yes", "loco_grounded": "yes"}
Q_STEPS = ("check_traction_circuit_1", "reset_and_accelerate_gradually",
           "recheck_and_reset_if_drops_after_long_interval", "try_hmcs1_positions_if_frequent",
           "isolate_tm_of_bad_hmcs1_position", "isolate_truck_1_if_all_positions")


def _st(kb, fid, claimed=(), history=None, intent=None):
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=fid, claimed_steps=tuple(claimed), history=history or {},
                                intended_action=intent), kb.get(fid))
    return s


def _prov(parse_outs, decide_tools):
    return Providers(parse=FakeProvider(structured_queue=list(parse_outs)),
                     decide=FakeProvider(structured_queue=[DecideOutput(tool=t, reason="t") for t in decide_tools]),
                     phrase=FakeProvider())


# ---------------------------------------------------------------------------
# KB shape
# ---------------------------------------------------------------------------

def test_panto_shape(kb):
    f = kb.get(PANTO)
    assert f.step_ids == list(P_STEPS)
    assert [s.id for s in f.gated_steps] == [ROOF]
    g = f.step(ROOF).gate
    assert g.type == "hazard_exposure" and g.action == ACTION_WORK_ON_ROOF
    assert set(g.preconditions) == set(PRE)
    assert "§11.04" in g.source and "§10.03" in g.source
    assert {d.fact for d in f.defer_conditions} == {"pantograph_not_lowered", "both_pantographs_damaged"}


def test_qrsi_shape(kb):
    f = kb.get(QRSI)
    assert f.step_ids == list(Q_STEPS) and f.gated_steps == []          # no reset-once rule
    assert f.step(Q_STEPS[0]).isolation is not None
    assert [d.fact for d in f.defer_conditions] == ["load_and_road_do_not_permit"]
    assert all("§6.02.1" in s.citation for s in f.steps)


# ---------------------------------------------------------------------------
# hazard-exposure gate — every branch
# ---------------------------------------------------------------------------

def test_hazard_not_in_play_before_prior_steps(kb):
    s = _st(kb, PANTO, claimed=P_STEPS[:1])
    assert not evaluate_gates(s, kb.get(PANTO)).fired
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=PANTO, claimed_steps=P_STEPS[:1]), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == P_STEPS[1]


def test_hazard_caution_is_proactive_when_action_is_next(kb):
    """Prior steps done, preconditions unstated → CAUTION stating them, before the action."""
    s = _st(kb, PANTO, claimed=P_STEPS[:3])
    v = evaluate_gates(s, kb.get(PANTO))
    assert v.outcome is Outcome.CAUTION and v.rule == "H-caution"
    assert "do not climb" in v.message.lower() and "power block" in v.message.lower() and "grounded" in v.message.lower()
    assert set(v.reasons) == set(PRE)


def test_hazard_fires_on_stated_intent_even_with_prior_steps_incomplete(kb):
    """§2.3: 'fires proactively before the action' — intent alone puts it in play."""
    s = _st(kb, PANTO, claimed=P_STEPS[:1], intent=ACTION_WORK_ON_ROOF)
    v = evaluate_gates(s, kb.get(PANTO))
    assert v.outcome is Outcome.CAUTION


def test_hazard_refuses_when_a_precondition_is_no(kb):
    s = _st(kb, PANTO, claimed=P_STEPS[:3], history={**PRE, "loco_grounded": "no"})
    v = evaluate_gates(s, kb.get(PANTO))
    assert v.outcome is Outcome.REFUSE and v.rule == "H-no" and v.reasons == ("loco_grounded",)


def test_hazard_no_fire_when_preconditions_met_then_engine_asks_the_step(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=PANTO, claimed_steps=P_STEPS[:3], history=PRE), kb)
    assert not t.verdict_after_update.fired
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == ROOF     # 3b: the gated step itself
    t = run_turn(d, StateUpdate(claimed_steps=(ROOF,)), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == P_STEPS[4]  # steps after the gate now due


def test_hazard_step_claimed_without_preconditions_asks_not_confirms(kb):
    """Never confirm a hazardous step on an unstated precondition (§12.2: false confirmation)."""
    s = _st(kb, PANTO, claimed=P_STEPS[:4])
    v = evaluate_gates(s, kb.get(PANTO))
    assert v.outcome is Outcome.ASK and v.rule == "H-ask" and "?" in v.question


def test_steps_after_unclaimed_gate_are_not_due(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=PANTO, claimed_steps=(*P_STEPS[:3], P_STEPS[4])), kb)   # skipped the roof step
    assert t.terminal.kind == "caution" and t.terminal.step_id == ROOF


def test_panto_defer_conditions(kb):
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=PANTO, history={"pantograph_not_lowered": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "stop the train immediately" in t.terminal.message
    assert "§10.03(a)" in t.terminal.source
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=PANTO, claimed_steps=P_STEPS[:2],
                                               history={"both_pantographs_damaged": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "Both pantographs" in t.terminal.message


def test_panto_full_run_through_graph_with_reflex_short_circuit():
    """Pilot at the roof step without stating the power block → the reflex short-circuits
    with the caution; agent never consulted. Then preconditions stated → asks the step."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=list(P_STEPS[:3]),
                     intended_action="work_on_roof"),
         ParseOutput(fault_guess=None, fault_confidence=0.0, facts=PRE)],
        ["diff_completed_steps", "diff_completed_steps"]))
    t1 = cp.turn(d, "panto damaged, lowered it, BP ok, called TPC. going up on the roof now")
    assert t1.terminal.kind == "caution" and t1.tool_path == (REFLEX_SHORT_CIRCUIT,)
    assert cp.providers.decide.calls == []
    t2 = cp.turn(d, "power block given, OHE earthed both sides, loco grounded with HOM", last_assistant=t1.reply)
    assert t2.terminal.kind == "ask_step" and t2.terminal.step_id == ROOF
    assert t2.tool_path == ("diff_completed_steps",)


# ---------------------------------------------------------------------------
# QRSI-1 — isolate-and-retest + resume-from-stuck, gate-free
# ---------------------------------------------------------------------------

def test_qrsi_never_fires_the_reflex(kb):
    s = _st(kb, QRSI, claimed=Q_STEPS[:2], history={"was_QLM_reset_earlier_this_trip": "yes"})
    assert not evaluate_gates(s, kb.get(QRSI)).fired                     # no reset-once rule here


def test_qrsi_resume_from_stuck_in_the_ladder(kb):
    """Pilot tried HMCS positions and it drops in position 3 → next is 'isolate that TM'."""
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=QRSI, claimed_steps=Q_STEPS[:4]), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "isolate_tm_of_bad_hmcs1_position"
    assert "5/6" in t.terminal.message and "§6.02.1(d)" in t.terminal.source
    t = run_turn(d, StateUpdate(claimed_steps=(Q_STEPS[4],), history={HF_RESOLVED: "yes"}), kb)
    assert t.terminal.kind == "confirm" and any("5/6" in g for g in t.terminal.guidance)


def test_qrsi_isolation_branches_without_a_gate(kb):
    f = kb.get(QRSI)
    # abnormality, isolation unstated → ask
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=QRSI, claimed_steps=Q_STEPS[:1],
                                               history={"traction1_abnormality_found": "yes"}), kb)
    assert t.terminal.kind == "ask_history" and "isolate" in t.terminal.message.lower()
    # isolation failed → TLC
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=QRSI, claimed_steps=Q_STEPS[:1],
                                               history={"traction1_abnormality_found": "yes", "isolation_successful": "no"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "§6.02.1(a)" in t.terminal.source
    # isolated → continue the ladder (next step = reset and accelerate)
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=QRSI, claimed_steps=Q_STEPS[:1],
                                               history={"traction1_abnormality_found": "yes", "isolation_successful": "yes"}), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == Q_STEPS[1]


def test_qrsi_load_and_road_defer(kb):
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=QRSI, claimed_steps=Q_STEPS,
                                               history={"load_and_road_do_not_permit": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "§6.02.1(f)" in t.terminal.source


def test_qrsi_no_confirmation_turn_for_gate_free_fault():
    cp = Copilot(_prov([ParseOutput(fault_guess=QRSI, fault_confidence=0.8)], ["diff_completed_steps"]))
    r = cp.turn(DiagnosisState(), "qrsi one target down on run")
    assert r.terminal.kind == "ask_step" and r.terminal.step_id == Q_STEPS[0]


def test_qrsi_alternative_branches_are_skipped_when_contradicted(kb):
    """(b) 'after a long interval' vs (c) 'frequently' are alternatives: a frequent dropper
    is never asked about (b); 'drops only in position 2' skips the all-positions step."""
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=QRSI, claimed_steps=Q_STEPS[:2],
                                history={"drops_frequently": "yes", "drops_after_long_interval": "no"}), kb)
    assert t.terminal.step_id == "try_hmcs1_positions_if_frequent"              # (b) skipped
    t = run_turn(d, StateUpdate(claimed_steps=(Q_STEPS[3],),
                                history={"drops_in_particular_hmcs1_position": "yes",
                                         "drops_in_all_hmcs1_positions": "no"}), kb)
    assert t.terminal.step_id == "isolate_tm_of_bad_hmcs1_position"
    t = run_turn(d, StateUpdate(claimed_steps=(Q_STEPS[4],)), kb)
    assert t.terminal.kind == "confirm"                                          # (e) skipped → done


def test_qrsi_unstated_branch_facts_leave_steps_in_play(kb):
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=QRSI, claimed_steps=Q_STEPS[:2]), kb)
    assert t.terminal.step_id == "recheck_and_reset_if_drops_after_long_interval"   # asks (b)'s "If …"


def test_qrsi_completing_branch_ends_on_resolved_terminal(kb):
    """Isolating the TM of the bad HMCS position is a sanctioned way onward (§6.02.1(d)):
    the terminal is 'resolved' with the load restriction, not 'contact TLC'."""
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=QRSI, claimed_steps=Q_STEPS[:2],
                            history={"drops_frequently": "yes", "drops_after_long_interval": "no"}), kb)
    t = run_turn(d, StateUpdate(claimed_steps=(Q_STEPS[3], Q_STEPS[4]),
                                history={"drops_in_particular_hmcs1_position": "yes",
                                         "drops_in_all_hmcs1_positions": "no"}), kb)
    assert t.terminal.kind == "confirm"
    assert any("5/6" in g for g in t.terminal.guidance) and not any("TLC" in g for g in t.terminal.guidance)
