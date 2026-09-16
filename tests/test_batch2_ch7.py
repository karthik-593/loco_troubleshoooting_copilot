"""Batch 2 (Ch.7 tripping failures + §5.01 / Ch.7-intro intake, approved 2026-09-16):
the DJ-tripped intake hub routing on the observed sign (confirm before guidance), the ICDJ
hub and its branch chain, wedging as hazard_exposure gates (Q118 / Q44 / Q45 / contactors),
gated steps inside untaken branches, the re-glow question hub (VCB 5-branch?), and the
collapsed out-of-scope coverage list."""
from agent.graph import Copilot
from engine.gates import Outcome, evaluate_gates
from engine.run_turn import run_turn
from engine.state import ACTION_WEDGE_Q118, ACTION_WEDGE_Q44, DiagnosisState, StateUpdate
from llm.interface import FakeProvider, Providers
from llm.parse import out_of_scope_reason, parse_turn
from llm.schemas import DecideOutput, ParseOutput

HUB, ICDJ = "DJ_tripped_on_line", "ICDJ"
Q118, Q45, Q44 = "ICDJ_Q118_branch", "ICDJ_Q45_branch", "ICDJ_Q44_branch"
TRIP_TARGETS = {"icdj": ICDJ, "no_tension": "No_tension", "op_a_beginning": "Op_A_beginning",
                "op_a_ending": "Op_A_ending", "reglows_on_release": "Reglows_on_release",
                "op_b_part1": "Op_B_part1", "op_o": "Op_O", "op_1": "Op_I", "op_2": "Op_II", "twac": "TWAC"}


def _cp(parses, tools):
    return Copilot(Providers(parse=FakeProvider(structured_queue=list(parses)),
                             decide=FakeProvider(structured_queue=[DecideOutput(tool=t, reason="t") for t in tools]),
                             phrase=FakeProvider()))


# ---------------------------------------------------------------------------
# shape / provenance
# ---------------------------------------------------------------------------

def test_batch2_files_cite_chapter_7_and_the_intake(kb):
    for fid in list(TRIP_TARGETS.values()) + [HUB, Q118, Q45, Q44, "ICDJ_C118_branch", "ICDJ_EFDJ_MTDJ_branch",
                                             "ICDJ_battery_voltage", "ICDJ_air_pressure", "Op_A_ending_part2",
                                             "Op_B_part2", "Op_II_none_closed", "Op_II_one_not_closed", "Op_II_interlock"]:
        f = kb.get(fid)
        assert all(("§7." in s.citation) or ("§5.01" in s.citation) or ("Ch.7" in s.citation) or ("ladder" in s.citation)
                   for s in f.steps), fid
    hub = kb.get(HUB)
    assert {r.equals: r.route_to for r in hub.route_rules} == TRIP_TARGETS
    assert hub.precedence == -2 and not hub.confirm_before_guidance
    assert all(kb.get(t).confirm_before_guidance for t in TRIP_TARGETS.values() if t != "Reglows_on_release")
    # wedging gates: every wedge step is a hazard_exposure gate with a TSD-stated precondition
    # (Ch.7 only: batch 3 encodes Q50 / Q52 / QRS / Q46 / QVCD wedges as ordinary steps — the TSD
    # states no precondition for them, approved 2026-09-16)
    ch7 = [fid for fid in kb.fault_ids if any("§7." in s.citation for s in kb.get(fid).steps)]
    wedges = [(fid, s) for fid in ch7 for s in kb.get(fid).steps if s.id.startswith("wedge_")]
    assert len(wedges) >= 9
    for fid, s in wedges:
        assert s.gate is not None and s.gate.type == "hazard_exposure" and s.gate.preconditions, (fid, s.id)
        assert s.gate.action.startswith("wedge_") and s.gate.precondition_question, (fid, s.id)
    assert kb.get(Q44).step("wedge_q44").gate.preconditions == ["tlc_permission_for_wedging_q44", "gr_efficiency_test_done"]
    assert kb.get("Op_B_part2").step("wedge_q45").gate.preconditions == ["no_operation_a_ending_trouble"]
    # §7.09.1(c) wedge Q100: the TSD states no precondition → ordinary step (decision 2)
    assert kb.get("Op_II_none_closed").step("check_q100_energised_wedge_if_not").gate is None


def test_intake_alias_precedence(kb):
    assert kb.match_alias("DJ tripped, QLM locked").fault_id == "QLM_dropped"
    assert kb.match_alias("DJ tripped, smoke on loco").fault_id == "fire_on_loco"
    assert kb.match_alias("dj tripped, dont know which relay").fault_id == HUB
    assert kb.match_alias("DJ tripped, LSDJ does not extinguish").fault_id == ICDJ


# ---------------------------------------------------------------------------
# intake hub → sign → confirm → procedure
# ---------------------------------------------------------------------------

def test_intake_hub_prechecks_then_observation_drill_then_routes_with_confirm(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=HUB), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "prepare_loco_to_pick_up_abnormal_sign"
    assert "ZCPA" in t.terminal.message and "SA-9" in t.terminal.message              # §5.01 Notes c, d ride along
    t = run_turn(d, StateUpdate(claimed_steps=("prepare_loco_to_pick_up_abnormal_sign",)), kb)
    assert t.terminal.step_id == "observe_dj_closing_and_pick_up_abnormal_sign"
    t = run_turn(d, StateUpdate(claimed_steps=("observe_dj_closing_and_pick_up_abnormal_sign",),
                                history={"trip_sign": "op_a_beginning"}), kb)
    assert d.matched_fault == "Op_A_beginning" and not d.fault_confirmed
    assert t.terminal.kind == "confirm_fault" and "flickers" in t.terminal.message
    t = run_turn(d, StateUpdate(fault_confirmed=True), kb)
    assert t.terminal.step_id == "check_qla_qoa_targets"


def test_intake_side_notes_are_stated_only(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=HUB, claimed_steps=("prepare_loco_to_pick_up_abnormal_sign",)), kb)
    t = run_turn(d, StateUpdate(history={"battery_voltage_zero": "yes"}), kb)
    assert t.terminal.step_id == "check_hba_addl_ccba_and_batteries_if_ba_voltage_zero" and t.terminal.do_now


def test_intake_routes_a_dropped_relay_to_its_procedure(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=HUB, history={"other_relays_acted": ["QOP-2"]}), kb)
    assert d.matched_fault == "QOP2_dropped" and t.terminal.kind == "ask_step"


def test_every_sign_routes_to_its_section(kb):
    for sign, target in TRIP_TARGETS.items():
        d = DiagnosisState()
        run_turn(d, StateUpdate(fault_id=HUB, history={"trip_sign": sign}), kb)
        assert d.matched_fault == target, sign


def test_intake_alias_yields_to_a_specific_fault_the_parser_names(kb):
    """'DJ tripped, QLM is locked' — the alias hit is the generic hub; the model names QLM."""
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess="QLM_dropped", fault_confidence=0.9, fault_presenting="yes")])
    r = parse_turn("DJ tripped, QLM is locked.", DiagnosisState(), kb, p)
    assert r.update.fault_id == "QLM_dropped" and r.update.fault_confirmed is False
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0, problem_outside_list="no")])
    r = parse_turn("DJ tripped, no idea which relay", DiagnosisState(), kb, p)
    assert r.update.fault_id == HUB and r.update.fault_confirmed is True


def test_route_phrases_set_the_sign_deterministically(kb):
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0)])
    r = parse_turn("DJ tripped, it trips randomly on run", DiagnosisState(), kb, p)
    assert r.update.fault_id == HUB and r.update.history.get("trip_sign") == "twac"
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="Op_II"), kb)
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0)])
    r = parse_turn("none of them closed", d, kb, p)
    assert r.update.history.get("contactors_closed") == "none"


# ---------------------------------------------------------------------------
# ICDJ hub → branches → manual energisation → wedge gates
# ---------------------------------------------------------------------------

def _icdj_to_q118(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=ICDJ, fault_confirmed=True, claimed_steps=("stop_and_secure_train",
                            "check_battery_voltage_uba", "check_air_pressure_rs_mr", "check_whether_c118_closes"),
                            history={"battery_voltage_low_or_zero": "no", "air_pressure_low": "no", "c118_closing": "no"}), kb)
    return d


def test_icdj_hub_checks_in_note_2_order_and_routes(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=ICDJ, fault_confirmed=True), kb)
    assert t.terminal.step_id == "stop_and_secure_train"
    d = _icdj_to_q118(kb)
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.step_id == "check_q118_energising"            # C118 not closing → check the branches one by one
    t = run_turn(d, StateUpdate(claimed_steps=("check_q118_energising",), history={"q118_energised": "no"}), kb)
    assert d.matched_fault == Q118 and d.fault_confirmed and t.terminal.step_id == "ensure_battery_voltage_above_90"
    # C118 closing → straight to the EFDJ/MTDJ ladder (§7.01.6 Note 2), one file for both DJ types
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=ICDJ, fault_confirmed=True, history={"c118_closing": "yes"}), kb)
    assert d.matched_fault == "ICDJ_EFDJ_MTDJ_branch"


def test_q118_branch_manual_energisation_outcomes(kb):
    f = kb.get(Q118)
    ladder = [s.id for s in f.ordinary_steps if not s.applies_when]
    # closed and held → resume, energising Q118 manually each time (completes)
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=Q118, claimed_steps=tuple(ladder), history={"dj_closes_with_manual_q118": "yes"}), kb)
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.step_id == "resume_traction_energising_q118_manually_each_time"
    # did not close → the Q45 branch (§7.01.3 manual (e))
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=Q118, claimed_steps=tuple(ladder), history={"dj_closes_with_manual_q118": "no"}), kb)
    assert d.matched_fault == Q45
    # closed but tripped on release → wedge Q118, gated on the EM contactors being open
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=Q118, claimed_steps=tuple(ladder), history={"trips_after_releasing_q118": "yes"}), kb)
    assert t.terminal.kind == "caution" and t.terminal.gate_type == "hazard_exposure" and "EM contactors" in t.terminal.message
    t = run_turn(d, StateUpdate(history={"all_em_contactors_open": "no"}), kb)
    assert t.terminal.kind == "refuse"
    d.history_facts["all_em_contactors_open"] = "yes"
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "wedge_q118"
    t = run_turn(d, StateUpdate(claimed_steps=("wedge_q118",)), kb)
    assert t.terminal.kind == "confirm" and any("C118" in g for g in t.terminal.guidance)


def test_wedge_claimed_without_precondition_asks_the_kb_question(kb):
    d = DiagnosisState()
    f = kb.get(Q118)
    run_turn(d, StateUpdate(fault_id=Q118, history={"trips_after_releasing_q118": "yes"}, claimed_steps=("wedge_q118",)), kb)
    v = evaluate_gates(d, f)
    assert v.outcome is Outcome.ASK and v.rule == "H-ask" and v.question == f.step("wedge_q118").gate.precondition_question


def test_q44_wedge_needs_tlc_permission_and_gr_test(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=Q44), kb)
    t = run_turn(d, StateUpdate(intended_action=ACTION_WEDGE_Q44), kb)
    assert t.terminal.kind == "caution" and "permission from TLC" in t.terminal.message and "GR efficiency" in t.terminal.message
    t = run_turn(d, StateUpdate(history={"tlc_permission_for_wedging_q44": "no"}), kb)
    assert t.terminal.kind == "refuse" and "tlc_permission_for_wedging_q44" in t.terminal.reasons
    d.history_facts.update({"tlc_permission_for_wedging_q44": "yes", "gr_efficiency_test_done": "yes",
                            "dj_closes_with_manual_q44": "no"})
    d.steps_claimed_done.update(s.id for s in kb.get(Q44).ordinary_steps if not s.applies_when)
    t = run_turn(d, StateUpdate(clear_intended_action=True), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "wedge_q44"


def test_branch_chain_q118_q45_q44_and_reglow_back_to_intake(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=Q45, history={"dj_closes_with_manual_q45": "no"}), kb)
    assert d.matched_fault == Q44
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=Q45, history={"trips_with_manual_q45": "yes"}), kb)
    assert d.matched_fault == HUB                                     # "pick up the correct abnormal sign"


# ---------------------------------------------------------------------------
# gated steps inside branches; two gates in one ladder
# ---------------------------------------------------------------------------

def test_gated_step_in_an_untaken_branch_neither_gates_nor_blocks(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="Op_O", fault_confirmed=True,
                            claimed_steps=("check_mvmt_mvrh_for_smell_smoke_fire", "hvrh_hvmt_on_3_close_dj_blvmt_wait_30s"),
                            history={"mvrh_working": "yes", "mvmt1_working": "yes", "mvmt2_working": "yes"}), kb)
    assert not evaluate_gates(d, kb.get("Op_O")).fired
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.step_id == "clear_section_with_all_blowers_working"
    # the stated intent still puts the wedge gate in play (§2.3)
    t = run_turn(d, StateUpdate(intended_action=ACTION_WEDGE_Q118), kb)
    assert t.terminal.kind == "caution" and t.terminal.step_id == "wedge_q118_to_clear_section_with_mvrh_isolated"


def test_op_o_mvrh_not_working_branch_offers_the_wedge_behind_its_gate(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="Op_O", fault_confirmed=True,
                                claimed_steps=("check_mvmt_mvrh_for_smell_smoke_fire", "hvrh_hvmt_on_3_close_dj_blvmt_wait_30s"),
                                history={"mvrh_working": "no"}), kb)
    assert t.terminal.step_id == "mvrh_not_working_hvrh_on_0_restricted_current" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("mvrh_not_working_hvrh_on_0_restricted_current",), intended_action=ACTION_WEDGE_Q118), kb)
    assert t.terminal.kind == "caution" and "EM contactors" in t.terminal.message


def test_twac_two_wedge_gates_fire_in_tsd_order(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="TWAC", fault_confirmed=True,
                                claimed_steps=("check_ccpt_ccdj_cca_fuses_slack_or_hot", "zpt_on_2_work_with_front_panto")), kb)
    assert t.terminal.kind == "caution" and t.terminal.step_id == "wedge_q118_and_resume_with_precautions"
    t = run_turn(d, StateUpdate(history={"all_em_contactors_open": "yes"}), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "wedge_q118_and_resume_with_precautions"   # not yet the Q44 gate
    t = run_turn(d, StateUpdate(claimed_steps=("wedge_q118_and_resume_with_precautions",)), kb)
    assert t.terminal.step_id == "hvsi_on_3_and_try"
    t = run_turn(d, StateUpdate(claimed_steps=("hvsi_on_3_and_try",)), kb)
    assert t.terminal.kind == "caution" and t.terminal.step_id == "wedge_q44_with_tlc_permission"
    t = run_turn(d, StateUpdate(history={"tlc_permission_for_wedging_q44": "yes", "gr_efficiency_test_done": "yes"}), kb)
    assert t.terminal.step_id == "wedge_q44_with_tlc_permission"
    t = run_turn(d, StateUpdate(claimed_steps=("wedge_q44_with_tlc_permission",)), kb)
    assert t.terminal.step_id == "check_relay_fixations_and_tap_safety_relays"
    t = run_turn(d, StateUpdate(claimed_steps=("check_relay_fixations_and_tap_safety_relays",),
                                history={"other_relays_acted": ["QRSI-1"]}), kb)
    assert d.matched_fault == "QRSI1_drops_on_run"


# ---------------------------------------------------------------------------
# the re-glow question (decision 4): VCB 5-branch? yes / no / don't know
# ---------------------------------------------------------------------------

def _reglow(kb, answer):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=HUB, history={"trip_sign": "reglows_on_release"}), kb)
    t = run_turn(d, StateUpdate(), kb)
    assert d.matched_fault == "Reglows_on_release" and t.terminal.step_id == "ask_vcb_5_branch_dj_type"
    t = run_turn(d, StateUpdate(history={"vcb_5_branch_loco": answer}), kb)
    return d, t


def test_reglow_vcb_5_branch_is_op_a_ending_part2_tlc(kb):
    d, t = _reglow(kb, "yes")
    assert d.matched_fault == "Op_A_ending_part2" and t.terminal.kind == "confirm_fault"
    t = run_turn(d, StateUpdate(fault_confirmed=True), kb)
    assert t.terminal.kind == "defer_to_TLC" and "DJ N/O I/L on MTDJ branch" in t.terminal.message


def test_reglow_not_known_states_the_signs_indicate_op_b_part2(kb):
    d, t = _reglow(kb, "not_known")
    assert d.matched_fault == "Op_B_part2" and t.terminal.kind == "confirm_fault"
    assert t.terminal.message.startswith("The DJ type is not known: the signs indicate Operation 'B' part II")
    assert "§7.11" in t.terminal.message                            # the VCB 5-branch alternative is stated too
    d2, t2 = _reglow(kb, "no")
    assert d2.matched_fault == "Op_B_part2" and not t2.terminal.message.startswith("The DJ type")


def test_reglow_answers_are_route_phrases(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="Reglows_on_release"), kb)
    for text, value in (("dont know", "not_known"), ("it is a 6 branch VCB", "no"), ("yes 5-branch", "yes")):
        p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0)])
        r = parse_turn(text, d, kb, p)
        assert r.update.history.get("vcb_5_branch_loco") == value, text


def test_caution_on_an_unconfirmed_routed_fault_waits_behind_the_confirm_line(kb):
    """Op_B_part2 is one gated step: the Q45 caution would fire on arrival; §5.5 confirms first.
    A REFUSE never waits."""
    d, t = _reglow(kb, "no")
    assert t.terminal.kind == "confirm_fault" and t.verdict_after_update.fired
    t = run_turn(d, StateUpdate(fault_confirmed=True), kb)
    assert t.terminal.kind == "caution" and t.terminal.step_id == "wedge_q45"
    d, _ = _reglow(kb, "no")
    t = run_turn(d, StateUpdate(history={"no_operation_a_ending_trouble": "no"}), kb)
    assert t.terminal.kind == "refuse"


# ---------------------------------------------------------------------------
# elimination ladders (§7.05) and the Op-II hub
# ---------------------------------------------------------------------------

def test_op_b_part1_elimination_ladder(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="Op_B_part1", fault_confirmed=True,
                            claimed_steps=("check_mvsl_mph_for_smell_smoke_fire", "hvsl_on_3_hqcvar_hph_on_0_close_dj",
                                           "check_mvsl1_mvsl2_working", "clear_section_observing_transformer_oil"),
                            history={"one_mvsl_not_working": "no"}), kb)
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.step_id == "test_hvsl1_on_1"
    t = run_turn(d, StateUpdate(claimed_steps=("test_hvsl1_on_1",)), kb)
    assert t.terminal.step_id == "qvsl1_defective_hvsl1_on_3"        # asked as its "If DJ trips…" question
    t = run_turn(d, StateUpdate(history={"dj_trips_with_hvsl1_on_1": "no"}), kb)
    assert t.terminal.step_id == "test_hvsl2_on_1" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("test_hvsl2_on_1",), history={"dj_trips_with_hvsl2_on_1": "yes"}), kb)
    assert t.terminal.step_id == "qvsl2_defective_hvsl2_on_3" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("qvsl2_defective_hvsl2_on_3",)), kb)
    assert t.terminal.kind == "confirm"
    # Note 1: both MVSL not working → TLC
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="Op_B_part1", fault_confirmed=True, history={"both_mvsl_not_working": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "Note 1" in t.terminal.source


def test_op_ii_hub_routes_on_contactor_state_and_contactor_wedge_is_gated(kb):
    for value, target in (("none", "Op_II_none_closed"), ("some", "Op_II_one_not_closed"), ("all", "Op_II_interlock")):
        d = DiagnosisState()
        run_turn(d, StateUpdate(fault_id="Op_II", fault_confirmed=True, history={"contactors_closed": value}), kb)
        assert d.matched_fault == target, value
    d = DiagnosisState()
    f = kb.get("Op_II_one_not_closed")
    run_turn(d, StateUpdate(fault_id="Op_II_one_not_closed", claimed_steps=tuple(s.id for s in f.ordinary_steps if not s.applies_when)), kb)
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.kind == "caution" and "'3' position" in t.terminal.message
    t = run_turn(d, StateUpdate(history={"concerned_switch_on_3": "yes"}), kb)
    assert t.terminal.step_id == "wedge_contactor_and_work_with_precautions"


def test_op_ii_interlock_wedge_only_when_still_tripping(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="Op_II_interlock", claimed_steps=("hvmt1_hvmt2_in_3_and_close_dj",)), kb)
    assert t.terminal.step_id == "normalise_switches_one_by_one_to_find_trouble"    # the "If DJ does not trip…" question
    assert not t.verdict_after_update.fired
    t = run_turn(d, StateUpdate(history={"still_trips_with_hvmt_in_3": "yes"}), kb)
    assert t.terminal.kind == "caution" and t.terminal.step_id == "wedge_q118_c107_interlock_defective"


# ---------------------------------------------------------------------------
# out-of-scope coverage list collapses families (listed_as)
# ---------------------------------------------------------------------------

def test_out_of_scope_list_is_collapsed_by_listed_as(kb):
    msg = out_of_scope_reason(kb)
    assert msg.count(",") < 20 and len(msg) < 600 and "ICDJ (DJ not closing)" in msg and "QLM dropped" in msg
    assert "ICDJ Q118 branch" not in msg and "Op O" not in msg


def test_full_loop_intake_to_confirm_with_fake_providers(kb):
    cp = _cp([ParseOutput(fault_guess=None, fault_confidence=0.0, problem_outside_list="no"),
              ParseOutput(fault_guess=None, fault_confidence=0.0,
                          claimed_steps=["prepare_loco_to_pick_up_abnormal_sign", "observe_dj_closing_and_pick_up_abnormal_sign"],
                          facts={"trip_sign": "twac"}),
              ParseOutput(fault_guess=None, fault_confidence=0.0, confirms_fault="yes")],
             ["diff_completed_steps"] * 3)
    d = DiagnosisState()
    t1 = cp.turn(d, "dj tripped, dont know which relay")
    assert t1.terminal.step_id == "prepare_loco_to_pick_up_abnormal_sign"
    t2 = cp.turn(d, "prepared, closed DJ, it trips now and then with no fixed pattern", last_assistant=t1.reply)
    assert d.matched_fault == "TWAC" and t2.terminal.kind == "confirm_fault"
    t3 = cp.turn(d, "yes", last_assistant=t2.reply)
    assert t3.terminal.step_id == "check_ccpt_ccdj_cca_fuses_slack_or_hot"
