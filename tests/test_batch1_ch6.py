"""Batch 1 (Ch.6 remainder, approved 2026-09-16): QOP-1/QOP-2 (target can / cannot be reset,
§6.03.1–6.03.4), QOA (§6.04.1/6.04.2), QLA (§6.05). Engine mechanisms proved here:
fact-keyed route rules, the lazily-asked rb axis, the HT-compartment hazard gate, the QLA
reset-limit gate on its own history key, DeferCondition.equals."""
from agent.graph import Copilot
from engine.gates import Outcome, evaluate_gates
from engine.run_turn import run_turn
from engine.state import ACTION_ENTER_HT, DiagnosisState, LocoInfo, StateUpdate
from engine.terminals import REASON_RECURRED, REASON_SECOND_RESET
from llm.interface import FakeProvider, Providers
from llm.schemas import DecideOutput, ParseOutput

QOP1, QOP1N = "QOP1_dropped", "QOP1_target_not_resetting"
QOP2, QOP2N = "QOP2_dropped", "QOP2_target_not_resetting"
QOA, QOAN, QLA = "QOA_dropped", "QOA_target_not_resetting", "QLA_dropped"


def _cp(parses, tools):
    return Copilot(Providers(parse=FakeProvider(structured_queue=list(parses)),
                             decide=FakeProvider(structured_queue=[DecideOutput(tool=t, reason="t") for t in tools]),
                             phrase=FakeProvider()))


# ---------------------------------------------------------------------------
# shape / provenance
# ---------------------------------------------------------------------------

def test_batch1_files_shape(kb):
    for fid in (QOP1, QOP2, QOA, QOAN, QLA, QOP1N, QOP2N):
        f = kb.get(fid)
        assert all("§6.0" in s.citation or "p77" in s.citation for s in f.steps), fid
    assert kb.get(QOP1).route_rules[0].route_to == QOP1N and kb.get(QOP2).route_rules[0].route_to == QOP2N
    assert kb.get(QOA).route_rules[0].route_to == QOAN
    assert kb.get(QOP1N).rb_dependency and kb.get(QOP2N).rb_dependency and not kb.get(QOP1).rb_dependency
    assert kb.get(QLA).gated_steps[0].gate.needs_history == "was_QLA_reset_earlier_this_trip"
    assert "INFERRED" in kb.get(QOP1).step("check_traction_circuit_1").isolation.source
    # the QLM family outranks the standalone relays when both are named
    assert kb.match_alias("QLM locked, QOP-1 dropped").fault_id == "QLM_dropped"
    assert kb.match_alias("QOP-1 dropped").fault_id == QOP1 and kb.match_alias("QOP-2 target not resetting").fault_id == QOP2N


# ---------------------------------------------------------------------------
# route rule: "target cannot be reset" is the other procedure for the same relay
# ---------------------------------------------------------------------------

def test_route_rule_switches_to_the_not_resetting_procedure_and_keeps_shared_claims(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=QOP1, claimed_steps=("check_traction_circuit_1",),
                                history={"traction1_abnormality_found": "no"}), kb)
    assert d.matched_fault == QOP1 and t.terminal.step_id == "reset_target_and_resume"
    t = run_turn(d, StateUpdate(history={"target_resets": "no"}), kb)
    assert d.matched_fault == QOP1N
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "stop_and_check_traction_circuit_1_and_banding"


def test_qop1_can_reset_ladder_branches(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=QOP1, claimed_steps=("check_traction_circuit_1", "reset_target_and_resume"),
                            history={"traction1_abnormality_found": "no"}), kb)
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.kind == "ask_history" and "long interval" in t.terminal.message and "frequently" in t.terminal.message
    t = run_turn(d, StateUpdate(history={"drops_frequently": "yes"}), kb)
    assert t.terminal.step_id == "isolate_tms_one_by_one_if_frequent" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("isolate_tms_one_by_one_if_frequent",),
                                history={"stops_after_isolating_particular_tm": "yes"}), kb)
    assert t.terminal.step_id == "work_with_5_6_load_if_particular_tm_isolated"
    t = run_turn(d, StateUpdate(claimed_steps=("work_with_5_6_load_if_particular_tm_isolated",)), kb)
    assert t.terminal.kind == "confirm" and any("5/6" in g for g in t.terminal.guidance)


# ---------------------------------------------------------------------------
# HT-compartment hazard gate + lazily asked rb axis (QOP-2 needs it at the FIRST bit)
# ---------------------------------------------------------------------------

def _to_ht_entry(kb, fid, n):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=fid, claimed_steps=(f"stop_and_check_traction_circuit_{n}_and_banding",
                                                          f"hqop{n}_off_and_clear_section"),
                            history={f"traction{n}_abnormality_found": "no", "banding_failure_seen": "no"}), kb)
    return d


def test_ht_compartment_entry_is_gated_on_loco_grounded(kb):
    d = _to_ht_entry(kb, QOP2N, 2)
    f = kb.get(QOP2N)
    v = evaluate_gates(d, f)
    assert v.fired and v.outcome is Outcome.CAUTION and "loco_grounded" in v.reasons     # proactive caution
    t = run_turn(d, StateUpdate(intended_action=ACTION_ENTER_HT), kb)
    assert t.terminal.kind == "caution" and "HOM" in t.terminal.message
    t = run_turn(d, StateUpdate(history={"loco_grounded": "no"}), kb)
    assert t.terminal.kind == "refuse" and "Do not enter the HT compartment" in t.terminal.message
    d2 = _to_ht_entry(kb, QOP2N, 2)
    t = run_turn(d2, StateUpdate(history={"loco_grounded": "yes"}), kb)
    assert not evaluate_gates(d2, f).fired
    assert t.terminal.step_id == "ground_loco_and_enter_ht_compartment"                  # now the step itself is asked


def test_rb_axis_is_asked_only_when_the_bit_table_is_reached(kb):
    d = _to_ht_entry(kb, QOP2N, 2)
    run_turn(d, StateUpdate(claimed_steps=("ground_loco_and_enter_ht_compartment", "normalise_hqop2_and_j2_neutral",
                                           "try_reset_with_j2_neutral"),
                            history={"loco_grounded": "yes", "target_resets_with_j2_neutral": "yes"}), kb)
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.kind == "ask_config" and t.terminal.reasons == ("loco_rb",) and "RB" in t.terminal.message
    t = run_turn(d, StateUpdate(loco_rb="not_fitted"), kb)
    assert t.terminal.step_id == "pack_j2_bit_6_no_rb"                                     # WAP4 without RB: J2-6 first
    d.locos = [LocoInfo(rb="fitted")]
    d.steps_claimed_done.discard("pack_j2_bit_6_no_rb")
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.step_id == "pack_j2_bit_8_rb_fitted"


def test_qop1_bit_table_differs_only_at_the_third_bit(kb):
    d = _to_ht_entry(kb, QOP1N, 1)
    run_turn(d, StateUpdate(claimed_steps=("ground_loco_and_enter_ht_compartment", "normalise_hqop1_and_j1_neutral",
                                           "try_reset_with_j1_neutral"),
                            history={"loco_grounded": "yes", "target_resets_with_j1_neutral": "yes"}), kb)
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.step_id == "pack_j1_bit_8"                    # same for every class: no rb question yet
    t = run_turn(d, StateUpdate(claimed_steps=("pack_j1_bit_8", "pack_j1_bit_10")), kb)
    assert t.terminal.kind == "ask_config" and t.terminal.reasons == ("loco_rb",)


def test_defer_conditions_with_equals_no(kb):
    d = _to_ht_entry(kb, QOP1N, 1)
    t = run_turn(d, StateUpdate(claimed_steps=("ground_loco_and_enter_ht_compartment", "normalise_hqop1_and_j1_neutral",
                                               "try_reset_with_j1_neutral"),
                                history={"loco_grounded": "yes", "target_resets_with_j1_neutral": "no"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "not with the traction motors" in t.terminal.message
    assert "§6.03.3(h)" in t.terminal.source


def test_banding_failure_completes_with_speed_restriction(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=QOP1N, claimed_steps=("stop_and_check_traction_circuit_1_and_banding",),
                                history={"banding_failure_seen": "yes"}), kb)
    assert t.terminal.step_id == "isolate_tm_with_banding_failure" and "15 km/h" in t.terminal.message and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("isolate_tm_with_banding_failure",)), kb)
    assert t.terminal.kind == "confirm"


# ---------------------------------------------------------------------------
# QOA
# ---------------------------------------------------------------------------

def test_qoa_ladder_and_reroute_to_not_resetting(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=QOA, claimed_steps=("check_aux_circuit_equipment",),
                                history={"aux_abnormality_found": "no"}), kb)
    assert t.terminal.step_id == "ensure_em_contactors_open"
    t = run_turn(d, StateUpdate(claimed_steps=("ensure_em_contactors_open",), history={"target_resets": "no"}), kb)
    assert d.matched_fault == QOAN and "check_aux_circuit_equipment" in d.steps_claimed_done   # shared id carried over
    assert t.terminal.step_id == "check_em_contactors_welded"
    t = run_turn(d, StateUpdate(claimed_steps=("check_em_contactors_welded", "hqoa_0_and_clear_section",
                                               "isolate_aux_equipment_one_by_one_and_try_reset"),
                                history={"target_resets_after_isolating": "no"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "§6.04.2(f)" in t.terminal.source


def test_qoa_abnormality_isolate_then_resume(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=QOA, claimed_steps=("check_aux_circuit_equipment",),
                                history={"aux_abnormality_found": "yes", "isolation_successful": "no"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "§6.04.1" in t.terminal.source


# ---------------------------------------------------------------------------
# QLA: reset-limit gate on its own key; QLM+QLA reroutes to the combination procedure
# ---------------------------------------------------------------------------

def test_qla_reset_once_then_second_act_refused(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=QLA, claimed_steps=("check_aux_circuit_for_smoke_smell_fire",),
                                history={"aux_abnormality_found": "no"}), kb)
    assert t.terminal.kind == "ask_history" and "QLA" in t.terminal.message
    t = run_turn(d, StateUpdate(history={"was_QLA_reset_earlier_this_trip": "no"}), kb)
    assert t.terminal.kind == "caution" and "Reset QLA" in t.terminal.message
    t = run_turn(d, StateUpdate(claimed_steps=("reset_qla",), history={"fault_resolved": "yes"}), kb)
    assert t.terminal.kind == "confirm" and any("DO NOT reset" in g for g in t.terminal.guidance)
    t = run_turn(d, StateUpdate(fault_presenting=True), kb)                      # engine backstop, parser-blind
    assert t.terminal.kind == "refuse" and REASON_RECURRED in t.terminal.reasons
    assert "relief" not in t.terminal.message.lower() and "TLC" in t.terminal.message   # §6.05(d): TLC, no relief loco


def test_qla_prior_reset_stated_refuses(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=QLA, history={"was_QLA_reset_earlier_this_trip": "yes"}), kb)
    assert t.terminal.kind == "refuse" and REASON_SECOND_RESET in t.terminal.reasons


def test_qla_with_qlm_reroutes_to_the_combination_procedure(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=QLA, history={"other_relays_acted": ["QLM"]}), kb)
    assert d.matched_fault == "QLM_with_QLA_QOA"


def test_qla_parser_writes_the_prior_reset_fact_under_the_qla_key():
    d = DiagnosisState()
    cp = _cp([ParseOutput(fault_guess=QLA, fault_confidence=0.9, fault_presenting="yes",
                          was_reset_earlier_this_trip="yes")], ["diff_completed_steps"])
    r = cp.turn(d, "QLA dropped, already reset once this trip")
    assert d.history_facts.get("was_QLA_reset_earlier_this_trip") == "yes"
    assert r.terminal.kind in ("refuse", "confirm_fault")                       # §5.5 confirm first if LLM-guessed


def test_bit_packing_unsuccessful_defers_only_after_the_last_bit(kb):
    """Seen live: 'packed 8th and 10th bits, no reset either time' parsed as
    bit_packing_unsuccessful=yes. \u00a76.03.3(j) 4 is after ALL bits: the engine holds the
    deferral until the last prescribed bit step is claimed."""
    d = _to_ht_entry(kb, QOP1N, 1)
    t = run_turn(d, StateUpdate(claimed_steps=("ground_loco_and_enter_ht_compartment", "normalise_hqop1_and_j1_neutral",
                                               "try_reset_with_j1_neutral", "pack_j1_bit_8", "pack_j1_bit_10"),
                                history={"loco_grounded": "yes", "target_resets_with_j1_neutral": "yes",
                                         "bit_packing_unsuccessful": "yes"}), kb)
    assert t.terminal.kind == "ask_config"                                    # the third bit needs the rb axis
    t = run_turn(d, StateUpdate(loco_rb="fitted"), kb)
    assert t.terminal.step_id == "pack_j1_bit_12_rb_fitted"
    t = run_turn(d, StateUpdate(claimed_steps=("pack_j1_bit_12_rb_fitted",)), kb)
    assert t.terminal.kind == "defer_to_TLC" and "§6.03.3(j) item 4" in t.terminal.source
