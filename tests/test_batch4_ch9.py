"""Batch 4 (Ch.9 pneumatic failures §9.01–§9.10, accepted 2026-09-17): the sudden-BP-drop hub
routing on the reported cause (confirm before guidance), the continuity-test hazard gate on
moving after a cattle run-over (intent `move_train`), stated-only after-attaching / MCPA /
banker / light-engine branches, the §9.08(b) → §9.03 route, AFI → ACP, and the §7.01.2(c) /
Ch.7-intro cross-references to §9.01."""
from engine.gates import Outcome, evaluate_gates
from engine.run_turn import run_turn
from engine.state import ACTION_MOVE_TRAIN, ACTIONS, DiagnosisState, StateUpdate
from llm.interface import FakeProvider
from llm.parse import out_of_scope_reason, parse_turn
from llm.schemas import ParseOutput

HUB = "Sudden_BP_drop_on_run"
CAUSES = {"cattle_run_over": "Cattle_run_over", "acp": "Alarm_chain_pulling",
          "a9_exhaust_leak": "A9_exhaust_port_leaking", "c2a_leak": "C2A_relay_valve_leaking"}
RS, MR, BP, FP = "RS_pressure_not_building", "MR_pressure_not_maintaining", "BP_pressure_not_charging", "FP_pressure_not_charging"
SA9, A9, REL = "Loco_brake_not_applying_SA9", "Loco_brake_not_applying_A9", "Loco_brakes_not_releasing"
RISES, NODROP, AFI = "BP_rises_beyond_5_after_A9", "BP_not_dropping_through_A9", "AFI_overshoots_on_run"
ALL = [HUB, *CAUSES.values(), RS, MR, BP, FP, SA9, A9, REL, RISES, NODROP, AFI]


def _ordinary_ids(fault, n=None):
    ids = [s.id for s in fault.ordinary_steps if not s.applies_when and not s.elicits]
    return tuple(ids if n is None else ids[:n])


# ---------------------------------------------------------------------------
# shape / provenance
# ---------------------------------------------------------------------------

def test_batch4_files_cite_chapter_9_and_collapse_in_the_coverage_list(kb):
    assert len(ALL) == 15
    for fid in ALL:
        f = kb.get(fid)
        assert all("§9." in s.citation for s in f.steps), fid
        assert f.listed_as and f.listed_as.startswith("pneumatic failures"), fid
    hub = kb.get(HUB)
    assert hub.precedence == -2 and {r.equals: r.route_to for r in hub.route_rules} == CAUSES
    assert all(kb.get(t).confirm_before_guidance for t in CAUSES.values())
    # decision 3: the ONLY gate in the batch is the continuity test on moving after a cattle run-over
    gated = [(fid, s) for fid in ALL for s in kb.get(fid).gated_steps]
    assert [(fid, s.id) for fid, s in gated] == [("Cattle_run_over", "move_train_after_continuity_test")]
    g = gated[0][1].gate
    assert g.type == "hazard_exposure" and g.action == ACTION_MOVE_TRAIN and g.preconditions == ["continuity_test_done"]
    assert ACTION_MOVE_TRAIN in ACTIONS
    # decision 6: "dummy SS1" / "dummy safety valve" are plain steps
    assert kb.get(RS).step("ensure_ss1_not_stuck_open_tap_or_dummy").gate is None
    assert kb.get(MR).step("tap_safety_valves_ensure_none_stuck_lifted").gate is None
    # decision 5: §9.06 / §9.07 have no TLC line → the engine default
    assert kb.get(SA9).terminal_actions["unresolved"].startswith("contact TLC")
    assert kb.get(A9).terminal_actions["unresolved"].startswith("contact TLC")
    msg = out_of_scope_reason(kb)
    assert msg.count("pneumatic failures") == 1 and "Cattle run over" not in msg


def test_batch4_aliases_resolve_and_a_named_cause_outranks_the_hub(kb):
    assert kb.match_alias("BP dropped suddenly").fault_id == HUB
    assert kb.match_alias("BP dropped suddenly, we ran over cattle").fault_id == "Cattle_run_over"
    assert kb.match_alias("sudden drop of BP, chain pulled").fault_id == "Alarm_chain_pulling"
    assert kb.match_alias("AFI overshoots, someone pulled the chain").fault_id == "Alarm_chain_pulling"
    assert kb.match_alias("loco brake not applying through SA9").fault_id == SA9
    assert kb.match_alias("loco brake not applying with A9").fault_id == A9
    assert kb.match_alias("BP not charging").fault_id == BP and kb.match_alias("FP not charging").fault_id == FP
    assert kb.match_alias("BP not dropping through A9").fault_id == NODROP
    assert kb.match_alias("BP rises beyond 5").fault_id == RISES
    assert kb.match_alias("MR pressure dropping").fault_id == MR
    assert kb.match_alias("RS pressure not building").fault_id == RS


# ---------------------------------------------------------------------------
# §9.04 hub → cause → confirm → procedure
# ---------------------------------------------------------------------------

def test_bp_drop_hub_ladder_then_routes_on_the_cause_with_confirm(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=HUB), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "flasher_on_and_check_formation_for_derailment"
    t = run_turn(d, StateUpdate(claimed_steps=("flasher_on_and_check_formation_for_derailment",)), kb)
    assert t.terminal.step_id == "report_cause_of_bp_drop"
    t = run_turn(d, StateUpdate(history={"bp_drop_cause": "a9_exhaust_leak"}), kb)
    assert d.matched_fault == "A9_exhaust_port_leaking" and not d.fault_confirmed
    assert t.terminal.kind == "confirm_fault" and "A9 exhaust port" in t.terminal.message
    t = run_turn(d, StateUpdate(fault_confirmed=True), kb)
    assert t.terminal.step_id == "apply_a9_to_emergency_and_try"
    # every cause routes to its section
    for cause, target in CAUSES.items():
        d = DiagnosisState()
        run_turn(d, StateUpdate(fault_id=HUB, history={"bp_drop_cause": cause}), kb)
        assert d.matched_fault == target, cause


def test_bp_drop_hub_no_cause_known_walks_ip_valve_checks(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=HUB, claimed_steps=("flasher_on_and_check_formation_for_derailment", "report_cause_of_bp_drop")), kb)
    assert t.terminal.step_id == "report_cause_of_bp_drop"          # a claim on a question step without the answer is not completion
    t = run_turn(d, StateUpdate(history={"bp_drop_cause": "not_known"}), kb)
    assert t.terminal.step_id == "stop_and_check_loco_leakages_if_no_derailment"
    t = run_turn(d, StateUpdate(claimed_steps=("stop_and_check_loco_leakages_if_no_derailment",)), kb)
    assert t.terminal.step_id == "check_ip_valve_and_loco_bp_angle_cocks"
    t = run_turn(d, StateUpdate(claimed_steps=("check_ip_valve_and_loco_bp_angle_cocks",), history={"ip_valve_leaking": "yes"}), kb)
    assert t.terminal.step_id == "close_ip_valve_coc_if_leaking" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("close_ip_valve_coc_if_leaking",)), kb)
    assert t.terminal.kind == "confirm" and any("IP valve COC" in g for g in t.terminal.guidance)
    # FIBA (cause 7) has no procedure: the hub ladder runs to its end → engine-default TLC
    d = DiagnosisState()
    f = kb.get(HUB)
    t = run_turn(d, StateUpdate(fault_id=HUB, claimed_steps=_ordinary_ids(f), history={"bp_drop_cause": "fiba", "ip_valve_leaking": "no"}), kb)
    assert d.matched_fault == HUB and t.terminal.kind == "confirm" and any("TLC" in g for g in t.terminal.guidance)


def test_bp_drop_cause_phrases_are_deterministic(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=HUB), kb)
    for text, value in (("we hit a buffalo", "cattle_run_over"), ("passenger pulled the chain", "acp"),
                        ("air leaking through A9 exhaust", "a9_exhaust_leak"), ("C2A is leaking", "c2a_leak")):
        p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0)])
        r = parse_turn(text, d, kb, p)
        assert r.update.history.get("bp_drop_cause") == value, text


# ---------------------------------------------------------------------------
# §9.04.3 cattle run over: continuity-test gate on moving, defer conditions
# ---------------------------------------------------------------------------

def test_cattle_move_intent_gets_the_continuity_caution_and_refuses_on_no(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="Cattle_run_over", intended_action=ACTION_MOVE_TRAIN), kb)
    assert t.short_circuit and t.terminal.kind == "caution" and "continuity test" in t.terminal.message
    assert t.terminal.step_id == "move_train_after_continuity_test"
    t = run_turn(d, StateUpdate(history={"continuity_test_done": "no"}), kb)
    assert t.terminal.kind == "refuse" and t.verdict_after_update.outcome is Outcome.REFUSE
    assert t.terminal.reasons == ("continuity_test_done",)


def test_cattle_ladder_then_gate_then_resolved_with_sa9_and_dj_guidance(kb):
    d = DiagnosisState()
    f = kb.get("Cattle_run_over")
    plain = _ordinary_ids(f)
    t = run_turn(d, StateUpdate(fault_id="Cattle_run_over", claimed_steps=plain[:1]), kb)
    assert t.terminal.step_id == "check_leading_bp_and_fp_angle_cocks"
    t = run_turn(d, StateUpdate(claimed_steps=("check_leading_bp_and_fp_angle_cocks",),
                                history={"leading_bp_angle_cock_damaged": "yes"}), kb)
    assert t.terminal.step_id == "close_additional_angle_cock_or_dummy_if_bp_angle_cock_damaged" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("close_additional_angle_cock_or_dummy_if_bp_angle_cock_damaged",) + plain[2:],
                                history={"cattle_passed_below_train": "no"}), kb)
    # everything before the gated step is done → the gate is in play → proactive caution
    assert t.terminal.kind == "caution" and "continuity" in t.terminal.message
    t = run_turn(d, StateUpdate(history={"continuity_test_done": "yes"}), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "move_train_after_continuity_test"
    assert t.terminal.preconditions_met == ("continuity_test_done",)
    t = run_turn(d, StateUpdate(claimed_steps=("move_train_after_continuity_test",)), kb)
    assert t.terminal.kind == "confirm"
    assert any("SA9" in g and "A9" in g for g in t.terminal.guidance) and any("battery voltage" in g for g in t.terminal.guidance)


def test_cattle_move_claimed_without_the_test_stated_is_asked_not_confirmed(kb):
    d = DiagnosisState()
    f = kb.get("Cattle_run_over")
    t = run_turn(d, StateUpdate(fault_id="Cattle_run_over", claimed_steps=_ordinary_ids(f) + ("move_train_after_continuity_test",),
                                history={"cattle_passed_below_train": "no"}), kb)
    assert t.terminal.kind == "ask_history" and "continuity test" in t.terminal.message


def test_cattle_defer_conditions(kb):
    # Note 1: brake gear damaged → TLC, at any point
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="Cattle_run_over", history={"brake_gear_damaged": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "Do not move the train" in t.terminal.message and "Note 1" in t.terminal.source
    # (c)(ii): relief engine only once the angle-cock step is reached
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="Cattle_run_over", history={"bp_angle_cock_leak_not_arrested": "yes"}), kb)
    assert t.terminal.kind == "ask_step"
    t = run_turn(d, StateUpdate(claimed_steps=("note_km_flasher_on_stop_with_a9_do_not_trip_dj", "check_leading_bp_and_fp_angle_cocks",
                                               "close_additional_angle_cock_or_dummy_if_bp_angle_cock_damaged"),
                                history={"leading_bp_angle_cock_damaged": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "relief engine" in t.terminal.message


# ---------------------------------------------------------------------------
# §9.01 / §9.02 / §9.03 stated-only branches and ladders
# ---------------------------------------------------------------------------

def test_rs_pressure_mcpa_branch_is_stated_only_and_ladder_ends_in_tlc(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=RS), kb)
    assert t.terminal.step_id == "ensure_mcpa_working"
    t = run_turn(d, StateUpdate(history={"mcpa_working": "no"}), kb)
    assert t.terminal.step_id == "check_ccba_zcpa_terminals_if_mcpa_not_working" and t.terminal.do_now
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=RS, history={"mcpa_working": "yes"}), kb)
    assert t.terminal.step_id == "ensure_ral_cock_open"                 # elicits: MCPA step is done
    f = kb.get(RS)
    t = run_turn(d, StateUpdate(claimed_steps=_ordinary_ids(f)), kb)
    assert t.terminal.kind == "confirm" and t.terminal.guidance == ("contact TLC",)


def test_mr_formation_steps_asked_only_when_attached_and_notes_are_stated_only(kb):
    f = kb.get(MR)
    upto_k = tuple(s.id for s in f.ordinary_steps if not s.applies_when)[:12]
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=MR, claimed_steps=upto_k), kb)
    assert t.terminal.step_id == "check_bp_fp_leakage_on_formation_isolate_fp_single_pipe"    # asked as its "If attached…" question
    t = run_turn(d, StateUpdate(history={"attached_to_formation": "no"}), kb)
    assert t.terminal.step_id == "deenergise_drain_all_pressure_energise_and_try"             # l, m skipped
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=MR, claimed_steps=upto_k, history={"attached_to_formation": "yes"}), kb)
    assert t.terminal.step_id == "check_bp_fp_leakage_on_formation_isolate_fp_single_pipe"
    t = run_turn(d, StateUpdate(claimed_steps=("check_bp_fp_leakage_on_formation_isolate_fp_single_pipe",)), kb)
    assert t.terminal.step_id == "isolate_bp_after_fp_and_locate_defective_auxiliary_reservoir"
    # Note 2: burst air spring → completes with the 60 kmph + TLC/SCOR guidance
    t = run_turn(d, StateUpdate(history={"air_spring_burst": "yes"}), kb)
    assert t.terminal.step_id == "isolate_burst_air_spring_and_work_60_kmph" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("isolate_burst_air_spring_and_work_60_kmph",)), kb)
    assert t.terminal.kind == "confirm" and any("60 kmph" in g and "TLC/SCOR" in g for g in t.terminal.guidance)
    # Note 1: air dryer leaking served with (e)
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=MR, claimed_steps=upto_k[:6], history={"air_dryer_leaking": "yes"}), kb)
    assert t.terminal.step_id == "air_dryer_bypass_if_leaking"


def test_bp_not_charging_after_attaching_note_is_stated_only_and_first(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=BP), kb)
    assert t.terminal.step_id == "check_mr_pressure_8_to_9_5"           # the note is never asked
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=BP, history={"bp_not_charging_after_attaching": "yes"}), kb)
    assert t.terminal.step_id == "charge_bp_with_all_compressors_and_isolate_formation_if_after_attaching" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("charge_bp_with_all_compressors_and_isolate_formation_if_after_attaching",),
                                history={"dead_loco_attached": "yes"}), kb)
    assert t.terminal.step_id == "dead_loco_checks_if_dead_loco_attached" and "piped vehicle" in t.terminal.message
    f = kb.get(BP)
    t = run_turn(d, StateUpdate(claimed_steps=("dead_loco_checks_if_dead_loco_attached",) + _ordinary_ids(f)), kb)
    assert t.terminal.kind == "confirm" and t.terminal.guidance == ("contact TLC and act as per his instructions",)


# ---------------------------------------------------------------------------
# §9.05–§9.10
# ---------------------------------------------------------------------------

def test_fp_ladder_completes_on_single_pipe_and_after_attaching_note(kb):
    d = DiagnosisState()
    f = kb.get(FP)
    t = run_turn(d, StateUpdate(fault_id=FP, claimed_steps=_ordinary_ids(f)), kb)
    assert t.terminal.kind == "confirm" and any("60 kmph" in g for g in t.terminal.guidance)
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=FP, history={"fp_not_charging_after_attaching": "yes"}), kb)
    assert t.terminal.step_id == "check_formation_fp_leakage_if_not_charging_after_attaching" and t.terminal.do_now


def test_sa9_light_engine_stated_only_and_resume_with_a9_completes(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=SA9), kb)
    assert t.terminal.step_id == "ensure_sa9_inlet_outlet_cocs_open_working_cab_closed_non_working"
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=SA9, history={"light_engine": "yes"}), kb)
    assert t.terminal.step_id == "stop_immediately_with_a9_emergency_if_light_engine" and t.terminal.do_now
    f = kb.get(SA9)
    t = run_turn(d, StateUpdate(claimed_steps=("stop_immediately_with_a9_emergency_if_light_engine",) + _ordinary_ids(f),
                                history={"loco_brakes_apply_with_a9": "yes"}), kb)
    assert t.terminal.step_id == "resume_with_a9_loco_brakes_cautious_speed_if_applying"
    t = run_turn(d, StateUpdate(claimed_steps=("resume_with_a9_loco_brakes_cautious_speed_if_applying",)), kb)
    assert t.terminal.kind == "confirm" and any("cautious speed" in g for g in t.terminal.guidance)
    # not applying with A9 either: no TSD line → engine default TLC
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=SA9, claimed_steps=_ordinary_ids(f), history={"loco_brakes_apply_with_a9": "no"}), kb)
    assert t.terminal.kind == "confirm" and t.terminal.guidance == ("contact TLC for advice",)


def test_a9_three_steps_then_engine_default_tlc(kb):
    d = DiagnosisState()
    f = kb.get(A9)
    assert len(f.steps) == 3
    t = run_turn(d, StateUpdate(fault_id=A9, claimed_steps=_ordinary_ids(f)), kb)
    assert t.terminal.kind == "confirm" and t.terminal.guidance == ("contact TLC for advice",)


def test_brakes_not_releasing_routes_to_bp_not_charging_below_5_and_asks_the_pvef_branch(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=REL, claimed_steps=("ensure_a9_sa9_in_release_tap_c2b",)), kb)
    assert t.terminal.step_id == "check_bp_pressure_5"
    t = run_turn(d, StateUpdate(history={"bp_below_5": "yes"}), kb)
    assert d.matched_fault == BP and t.terminal.step_id == "check_mr_pressure_8_to_9_5"
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0)])
    r = parse_turn("handles in release, tapped C2B. BP is below 5", DiagnosisState(matched_fault=REL, fault_confirmed=True), kb, p)
    assert r.update.history.get("bp_below_5") == "yes"
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=REL, claimed_steps=("ensure_a9_sa9_in_release_tap_c2b", "press_pvef_and_observe_release"),
                                history={"bp_below_5": "no"}), kb)
    assert t.terminal.step_id == "operate_c3w_release_handle_if_brakes_reapply_after_pvef"       # asked as its "If …" question
    t = run_turn(d, StateUpdate(history={"brakes_release_with_pvef_then_reapply": "no"}), kb)
    assert t.terminal.step_id == "isolate_c3w_and_operate_qrv"
    f = kb.get(REL)
    t = run_turn(d, StateUpdate(claimed_steps=_ordinary_ids(f)), kb)
    assert t.terminal.kind == "confirm" and t.terminal.guidance == ("contact TLC",)


def test_afi_routes_acp_and_branches_on_leakage(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=AFI, history={"afi_cause": "acp"}), kb)
    assert d.matched_fault == "Alarm_chain_pulling" and t.terminal.kind == "confirm_fault"
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=AFI, claimed_steps=("flasher_on_and_ensure_no_abnormality",)), kb)
    t = run_turn(d, StateUpdate(claimed_steps=("stop_and_check_bp_leakage_in_loco_and_formation",), history={"bp_leakage_found": "no"}), kb)
    assert t.terminal.step_id == "ensure_no_brake_binding_if_no_leakage" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("ensure_no_brake_binding_if_no_leakage",), history={"brake_binding_found": "no"}), kb)
    assert t.terminal.step_id == "ignore_afi_and_work_watching_bp_gauge_if_no_brake_binding"
    t = run_turn(d, StateUpdate(claimed_steps=("ignore_afi_and_work_watching_bp_gauge_if_no_brake_binding",)), kb)
    assert t.terminal.kind == "confirm" and any("BP gauge" in g for g in t.terminal.guidance)
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=AFI, claimed_steps=("flasher_on_and_ensure_no_abnormality", "stop_and_check_bp_leakage_in_loco_and_formation"),
                                history={"bp_leakage_found": "yes"}), kb)
    assert t.terminal.step_id == "arrest_bp_leakage_if_found" and t.terminal.do_now


def test_acp_ladder_completes_on_the_bpc_entry(kb):
    d = DiagnosisState()
    f = kb.get("Alarm_chain_pulling")
    t = run_turn(d, StateUpdate(fault_id="Alarm_chain_pulling", claimed_steps=_ordinary_ids(f)[:-1]), kb)
    assert t.terminal.step_id == "make_entry_in_bpc"
    t = run_turn(d, StateUpdate(claimed_steps=("make_entry_in_bpc",)), kb)
    assert t.terminal.kind == "confirm" and any("RS-5" in g for g in t.terminal.guidance)


def test_bp_not_dropping_banker_step_is_stated_only(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=NODROP, claimed_steps=("stop_by_bringing_a9_to_emergency", "check_rear_cab_a9_cocs_close_if_open")), kb)
    assert t.terminal.step_id == "clear_section_with_rear_cab_if_all_normal"
    t = run_turn(d, StateUpdate(history={"banker_loco": "yes"}), kb)
    assert t.terminal.step_id == "ensure_a8_coc_closed_if_banker_loco" and t.terminal.do_now
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=RISES, claimed_steps=_ordinary_ids(kb.get(RISES))), kb)
    assert t.terminal.kind == "confirm" and t.terminal.guidance == ("contact TLC",)


# ---------------------------------------------------------------------------
# cross-references landing in this batch (judgement call 7)
# ---------------------------------------------------------------------------

def test_cross_references_to_rs_pressure_from_icdj_air_pressure_and_the_dj_intake(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="ICDJ_air_pressure", claimed_steps=("build_up_pressure_with_auxiliary_compressor",
                                                                          "check_drain_cocks_closed_if_pressure_not_building"),
                            history={"mcpa_working_but_pressure_not_building": "yes"}), kb)
    t = run_turn(d, StateUpdate(history={"rs_still_not_building": "yes"}), kb)
    assert d.matched_fault == RS and t.terminal.step_id == "ensure_mcpa_working"
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="DJ_tripped_on_line", claimed_steps=("prepare_loco_to_pick_up_abnormal_sign",),
                                history={"rs_pressure_low": "yes"}), kb)
    assert d.matched_fault == RS and t.terminal.kind == "ask_step"
    # a stated sign still routes first
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="DJ_tripped_on_line", history={"rs_pressure_low": "yes", "trip_sign": "twac"}), kb)
    assert d.matched_fault == "TWAC"


# ---------------------------------------------------------------------------
# parse hardening seen live with the larger vocabulary (batch 4)
# ---------------------------------------------------------------------------

def test_model_naming_the_current_faults_own_route_target_is_not_a_denial(kb):
    """Seen live 2026-09-17: on Op_II, "only C107 is not closing" was returned as fault_guess
    Op_II_one_not_closed with confirms_fault=no. The route target of the current fault is the
    same family — keep the fault and let the route rule switch on the fact."""
    d = DiagnosisState(matched_fault="Op_II", fault_confirmed=True)
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess="Op_II_one_not_closed", fault_confidence=0.9, confirms_fault="no",
                                                   claimed_steps=["Op_II.check_c105_c106_c107_closing_on_blvmt"],
                                                   facts={"contactors_closed": "some"})])
    r = parse_turn("only C107 is not closing, other two closed", d, kb, p)
    assert r.update.fault_id is None and r.update.fault_confirmed is None
    assert r.update.history.get("contactors_closed") == "some" and "check_c105_c106_c107_closing_on_blvmt" in r.update.claimed_steps
    # a genuine denial naming an unrelated fault still switches (unconfirmed)
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess="QLA_dropped", fault_confidence=0.9, confirms_fault="no")])
    r = parse_turn("no, it is QLA that dropped", DiagnosisState(matched_fault="Op_II", fault_confirmed=True), kb, p)
    assert r.update.fault_id == "QLA_dropped" and r.update.fault_confirmed is False


def test_a_claimed_question_step_without_its_fact_is_not_done(kb):
    """Seen live 2026-09-17 (reglow scenario): the parser claimed Reglows_on_release's VCB-type
    question step from the first message; the engine must keep asking it, never confirm."""
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="Reglows_on_release", claimed_steps=("ask_vcb_5_branch_dj_type",)), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "ask_vcb_5_branch_dj_type"
    t = run_turn(d, StateUpdate(history={"vcb_5_branch_loco": "not_known"}), kb)
    assert d.matched_fault == "Op_B_part2" and t.terminal.kind == "confirm_fault"
