"""Batch 3 (Ch.8 traction failures, approved 2026-09-16): the total-loss-of-TE intake hub on
the lamp / needle sign, Q50 / QRS / Q52 / Q46 / QVCD wedges as ORDINARY steps (the TSD states
only after-precautions or none), §8.04's stated-only reason branches ahead of the meter
question, §8.07 CCPT melting as one file keyed on the occasion (fact phrases, `elicits`)."""
from engine.gates import evaluate_gates
from engine.run_turn import run_turn
from engine.state import DiagnosisState, StateUpdate
from llm.interface import FakeProvider
from llm.parse import parse_turn
from llm.schemas import ParseOutput

HUB = "Total_loss_TE"
TE = {"lsb_glowing": "Total_loss_TE_with_LSB", "lsgr_not_extinguished_no_lsb": "Total_loss_TE_without_LSB",
      "gr_progressing": "Total_loss_TE_with_GR_progression"}
AR, AR1, PL, CCPT = "Auto_regression_with_LSP", "First_notch_auto_regression_without_LSP", "Partial_loss_TE", "CCPT_melting"


def test_batch3_files_cite_chapter_8_and_wedges_are_ordinary(kb):
    for fid in [HUB, *TE.values(), AR, AR1, PL, CCPT]:
        f = kb.get(fid)
        assert all("§8." in s.citation or "§7.01" in s.citation for s in f.steps), fid
    assert {r.equals: r.route_to for r in kb.get(HUB).route_rules} == TE
    # 2026-09-18 ruling: §12.03's Q50 block states a BEFORE-check, so this wedge IS gated —
    # the other Ch.8 wedges keep batch-3 decision 2 (their before-checks are asked as steps).
    q50 = kb.get(TE["lsb_glowing"]).step("wedge_q50_energised")
    assert q50.gate.action == "wedge_relay"
    assert q50.gate.preconditions == ["loco_de_energised_for_wedging", "j1_j2_ctf_c145_set_for_q50"]
    assert "§12.03" in q50.gate.source
    assert "MPJ should not be operated" in q50.text
    for sid in ("wedge_q52_deenergised_if_still_energised", "wedge_qrs_energised_if_still_not"):
        assert kb.get(TE["lsgr_not_extinguished_no_lsb"]).step(sid).gate is None               # decision 2
    assert kb.get(AR1).step("wedge_q46_deenergised_and_eec_non_modified_zsms").gate is None
    # §8.07 keeps the Ch.7 wedge gates for Q118 and Q44; Q50 there is ordinary
    assert kb.get(CCPT).step("a_wedge_q118").gate.action == "wedge_Q118"
    assert kb.get(CCPT).step("c_wedge_q44").gate.preconditions == ["tlc_permission_for_wedging_q44", "gr_efficiency_test_done"]
    assert "wedge Q50" in kb.get(CCPT).step("d_j1_j2_ctf_q50_earth_fault").text
    assert len({fp.equals for fp in kb.get(CCPT).fact_phrases}) == 15


def test_te_hub_routes_on_the_lamp_sign_with_confirm(kb):
    for sign, target in TE.items():
        d = DiagnosisState()
        t = run_turn(d, StateUpdate(fault_id=HUB, history={"te_sign": sign}), kb)
        assert d.matched_fault == target and t.terminal.kind == "confirm_fault", sign
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=HUB), kb)
    assert t.terminal.step_id == "observe_lamps_and_needles_on_taking_notch"
    p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0)])
    r = parse_turn("no traction, LSB is glowing", DiagnosisState(), kb, p)
    assert r.update.fault_id == HUB and r.update.history.get("te_sign") == "lsb_glowing"


def test_q50_ladder_ends_in_a_gated_wedge_with_after_precautions(kb):
    """2026-09-18 ruling: the ladder's own items 5-8 ARE the gate's precondition, so the wedge
    is cautioned until the pilot states them, refused if they say no, and only then instructed —
    the after-precautions still ride with the step and the resolved terminal."""
    d = DiagnosisState()
    f = kb.get(TE["lsb_glowing"])
    run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True, claimed_steps=tuple(s.id for s in f.ordinary_steps)), kb)
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.kind == "caution" and t.terminal.step_id == "wedge_q50_energised"
    t = run_turn(d, StateUpdate(history={"j1_j2_ctf_c145_set_for_q50": "no"}, intended_action="wedge_relay"), kb)
    assert t.terminal.kind == "refuse" and "LSC145" in t.terminal.message
    t = run_turn(d, StateUpdate(history={"j1_j2_ctf_c145_set_for_q50": "yes",
                                         "loco_de_energised_for_wedging": "yes"}), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == "wedge_q50_energised"
    t = run_turn(d, StateUpdate(claimed_steps=("wedge_q50_energised",)), kb)
    assert t.terminal.kind == "confirm" and any("shunting" in g for g in t.terminal.guidance)


def test_q51_energised_branch_asks_the_before_checks_then_the_qrs_wedge(kb):
    d = DiagnosisState()
    f = kb.get(TE["lsgr_not_extinguished_no_lsb"])
    first = tuple(s.id for s in f.ordinary_steps if not s.applies_when)[:8]
    t = run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True, claimed_steps=first,
                                history={"q52_energised": "no", "q51_energised": "yes"}), kb)
    assert t.terminal.step_id == "check_qrs_ccls_bp_rgeb_if_q51_energised" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("check_qrs_ccls_bp_rgeb_if_q51_energised",), history={"qrs_energised": "no"}), kb)
    assert t.terminal.step_id == "wedge_qrs_energised_if_still_not"
    # Q51 de-energised: none of the 8a–8d branch is due
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True, claimed_steps=first,
                                history={"q52_energised": "no", "q51_energised": "no"}), kb)
    assert t.terminal.step_id == "clean_q50_q51_q52_interlocks"


def test_auto_regression_meter_branches_and_stated_reasons(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=AR, fault_confirmed=True,
                            claimed_steps=("check_bp_brake_binding_sand_and_zqwc", "take_notches_and_observe_ammeters_voltmeters")), kb)
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.kind == "ask_history" and "A3 is not deviating" in t.terminal.message and "U5" in t.terminal.message
    t = run_turn(d, StateUpdate(history={"a3_deviating": "yes", "a4_deviating": "yes", "u2_deviating": "no"}), kb)
    assert t.terminal.step_id == "hmcs1_on_3_if_u2_not_deviating" and t.terminal.do_now
    # a stated reason is served before the meter question
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=AR, fault_confirmed=True, claimed_steps=("check_bp_brake_binding_sand_and_zqwc",),
                                history={"slipped_pinion_suspected": "yes"}), kb)
    assert t.terminal.step_id == "check_for_slipped_pinion"
    t = run_turn(d, StateUpdate(claimed_steps=("check_for_slipped_pinion",), history={"tm_pinion_rotating": "no"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "§8.04 D" in t.terminal.source
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=AR, fault_confirmed=True, claimed_steps=("check_bp_brake_binding_sand_and_zqwc",),
                            history={"locked_axle_suspected": "yes"}), kb)
    t = run_turn(d, StateUpdate(claimed_steps=("check_for_locked_axle",), history={"wheel_not_rotating": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "assistance" in t.terminal.message


def test_ccpt_melting_preamble_then_occasion_branch(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=CCPT, claimed_steps=("stop_zpt_0_mpj_0_hba_off_and_renew_ccpt",)), kb)
    assert t.terminal.step_id == "hoba_off_and_renew_if_melts_again"                  # asked as its "If it melts again" question
    t = run_turn(d, StateUpdate(claimed_steps=("hoba_off_and_renew_if_melts_again",),
                                history={"ccpt_melts_again": "yes", "ccpt_melts_with_hoba_off": "yes"}), kb)
    assert t.terminal.step_id == "identify_occasion_if_melts_with_hoba_off"
    t = run_turn(d, StateUpdate(history={"ccpt_melts_when": "closing_dj"}), kb)
    assert t.terminal.step_id == "c_q44_disconnect_cable_or_tlc"                      # elicits: the occasion step is done
    t = run_turn(d, StateUpdate(claimed_steps=("c_q44_disconnect_cable_or_tlc",)), kb)
    assert t.terminal.kind == "caution" and t.terminal.step_id == "c_wedge_q44"        # the Ch.7 Q44 gate
    # held after renewal → resolved, nothing more asked
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=CCPT, claimed_steps=("stop_zpt_0_mpj_0_hba_off_and_renew_ccpt",), history={"ccpt_melts_again": "no"}), kb)
    assert t.terminal.kind == "confirm"


def test_ccpt_occasion_phrases_are_deterministic(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=CCPT), kb)
    for text, value in (("it melts while raising panto", "raising_panto"), ("melts on taking 6th notch", "sixth_notch"),
                        ("goes when I press BPSW", "pressing_bpsw"), ("during quick regression", "quick_regression")):
        p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0)])
        r = parse_turn(text, d, kb, p)
        assert r.update.history.get("ccpt_melts_when") == value, text


def test_first_notch_ladder_alternatives_and_rear_cab_branch(kb):
    d = DiagnosisState()
    f = kb.get(AR1)
    run_turn(d, StateUpdate(fault_id=AR1, fault_confirmed=True, claimed_steps=tuple(s.id for s in f.ordinary_steps[:4]),
                            history={"notches_from_rear_cab": "no", "qvcd_energised": "no"}), kb)
    t = run_turn(d, StateUpdate(history={"zsms_modified": "yes"}), kb)
    assert t.terminal.step_id == "rear_cab_or_manual_gr_if_zsms_modified" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("rear_cab_or_manual_gr_if_zsms_modified",)), kb)
    assert t.terminal.step_id == "clean_q50_q51_interlocks_if_no_notches_from_rear_cab"
    assert not evaluate_gates(d, f).fired


def test_partial_loss_isolates_the_tm_whose_voltmeter_is_dead(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=PL, claimed_steps=("check_hmcs_positions_and_place_on_1", "check_air_leakage_at_line_contactor_valves", "observe_u1_u6"),
                                history={"u1_deviating": "yes", "u6_deviating": "no"}), kb)
    assert t.terminal.step_id == "isolate_tm6_if_u6_not_deviating" and t.terminal.do_now
    t = run_turn(d, StateUpdate(claimed_steps=("isolate_tm6_if_u6_not_deviating",)), kb)
    assert t.terminal.kind == "confirm" and any("5/6" in g for g in t.terminal.guidance)
