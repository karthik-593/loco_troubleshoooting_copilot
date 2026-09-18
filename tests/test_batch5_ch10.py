"""Batch 5 (Ch.10 miscellaneous failures, approved 2026-09-18): the tell-tale-fuse removal
gate (§10.08), the two cross-reference routes into §10.04 (all pilot lamps) and §11.06
(defective battery), §10.06's HBA-'0' discriminating test, §10.02's fuse blocks and §10.16's
locked-axle deferral. §10.03 (panto damaged) and §10.12 (sanders) were already encoded."""
from engine.run_turn import run_turn
from engine.state import DiagnosisState, StateUpdate
from llm.interface import FakeProvider
from llm.parse import parse_turn
from llm.schemas import ParseOutput

CH10 = ["MCPA_not_working", "Pantograph_not_raising", "All_pilot_lamps_not_glowing",
        "LSDJ_not_glowing_when_BLDJ_opened", "LSCHBA_glows_on_run", "LSGR_not_glowing_on_zero",
        "LSRSI_glows_on_run", "UA_meter_not_deviating", "Head_light_not_glowing",
        "Flasher_light_not_glowing", "Horns_not_sounding", "Auto_regression_with_LSB_during_RB",
        "Wheel_skidding", "Loco_not_moving_on_first_notch"]
LSRSI, LAMPS = "LSRSI_glows_on_run", "All_pilot_lamps_not_glowing"


def test_every_batch5_file_cites_chapter_10_and_only_the_fuse_removal_is_gated(kb):
    for fid in CH10:
        f = kb.get(fid)
        assert all("§10." in s.citation for s in f.steps), fid
        assert f.listed_as in ("miscellaneous failures (indication lamps, lights, horns, MCPA, panto)",
                               "auto regression (with LSP / 1st notch / during RB)"), fid
    # the QEMS and QRS wedges stay ORDINARY (the Q100 / Q50 rule)
    assert kb.get("Pantograph_not_raising").step("good_fuses_wedge_qems_de_energised").gate is None
    assert kb.get(LAMPS).step("work_without_pilot_lamps_if_ccls_melts_repeatedly").gate is None
    gated = {s.id for s in kb.get(LSRSI).gated_steps}
    assert gated == {"remove_fuse_if_one_melted_in_one_block",
                     "remove_fuses_and_isolate_truck_if_two_or_more_in_same_block",
                     "remove_fuses_from_both_blocks_if_one_in_each"}
    for sid in gated:
        g = kb.get(LSRSI).step(sid).gate
        assert g.action == "remove_fuse"
        assert g.preconditions == ["dj_opened", "panto_lowered", "hba_off_with_ip_coc_closed"]


def test_telltale_fuse_removal_cautions_then_refuses_on_a_live_loco(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=LSRSI, fault_confirmed=True,
                                claimed_steps=("note_meter_readings_and_notches",
                                               "coast_and_check_telltale_fuses_on_both_rsi_blocks"),
                                history={"telltale_fuses_melted": "one_in_one_block"}), kb)
    assert t.terminal.kind == "caution" and t.terminal.step_id == "remove_fuse_if_one_melted_in_one_block"
    t = run_turn(d, StateUpdate(history={"dj_opened": "no"}, intended_action="remove_fuse"), kb)
    assert t.terminal.kind == "refuse" and "open DJ" in t.terminal.message
    # the branch the pilot did NOT report is skipped, not asked
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=LSRSI, fault_confirmed=True,
                                claimed_steps=("note_meter_readings_and_notches",
                                               "coast_and_check_telltale_fuses_on_both_rsi_blocks"),
                                history={"telltale_fuses_melted": "none"}), kb)
    assert t.terminal.step_id == "check_telltale_positions_micro_switches_qv63_and_bpt"
    assert not t.verdict_after_update.fired                     # no gate in the 'none' branch


def test_other_pilot_lamps_not_glowing_routes_to_section_10_04(kb):
    for fid in ("LSDJ_not_glowing_when_BLDJ_opened", "LSGR_not_glowing_on_zero"):
        d = DiagnosisState()
        t = run_turn(d, StateUpdate(fault_id=fid, fault_confirmed=True,
                                    history={"other_pilot_lamps_glowing": "no"}), kb)
        assert d.matched_fault == LAMPS and t.terminal.kind == "ask_step", fid


def test_low_battery_voltage_routes_to_the_section_11_06_procedure(kb):
    for fid in ("MCPA_not_working", "Pantograph_not_raising"):
        d = DiagnosisState()
        run_turn(d, StateUpdate(fault_id=fid, fault_confirmed=True,
                                history={"battery_voltage_too_low": "yes"}), kb)
        assert d.matched_fault == "Isolation_of_defective_battery", fid


def test_lschba_hba_zero_test_picks_the_branch_deterministically(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="LSCHBA_glows_on_run"), kb)
    for text, value, first_step in (("with HBA on 0 the DJ trips", "chba_defective", "chba_ensure_hchba_on_1"),
                                    ("DJ does not trip", "qv61_defective", "qv61_ignore_lschba")):
        p = FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0)])
        r = parse_turn(text, d, kb, p)
        assert r.update.history.get("lschba_cause") == value, text
        e = DiagnosisState()
        t = run_turn(e, StateUpdate(fault_id="LSCHBA_glows_on_run", fault_confirmed=True,
                                    history={"lschba_cause": value}), kb)
        assert t.terminal.step_id == first_step


def test_panto_not_raising_splits_on_the_fuse_finding(kb):
    f = kb.get("Pantograph_not_raising")
    head = tuple(s.id for s in f.ordinary_steps if not s.applies_when)
    for value, sid in (("yes", "fuse_block_change_addl_ccba_and_check_ltba_mov"),
                       ("no", "good_fuses_check_vept_and_recycle_zpt")):
        d = DiagnosisState()
        t = run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True, claimed_steps=head,
                                    history={"ccba_ccpt_fuse_melted": value,
                                             "battery_voltage_too_low": "no"}), kb)
        assert t.terminal.step_id == sid, value


def test_loco_not_moving_on_first_notch_defers_on_a_locked_axle(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="Loco_not_moving_on_first_notch", fault_confirmed=True,
                            claimed_steps=("watch_ammeter_and_check_bc_gauge", "check_hand_brake_and_skids"),
                            history={"hand_brake_applied": "no"}), kb)
    t = run_turn(d, StateUpdate(claimed_steps=("check_for_locked_axle_if_hand_brake_released",),
                                history={"locked_axle_noticed": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "do not move the loco" in t.terminal.message
    # hand brake applied → the release branch, not the locked-axle check
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="Loco_not_moving_on_first_notch", fault_confirmed=True,
                                claimed_steps=("watch_ammeter_and_check_bc_gauge", "check_hand_brake_and_skids"),
                                history={"hand_brake_applied": "yes"}), kb)
    assert t.terminal.step_id == "release_hand_brake_if_applied"


def test_auto_regression_during_rb_allows_the_second_reset_the_tsd_prescribes(kb):
    """§10.14 is NOT a Ch.6 relay: (c) prescribes resetting the target again and dropping RB,
    so the reset carries no reset_limit gate."""
    f = kb.get("Auto_regression_with_LSB_during_RB")
    assert f.gated_steps == []
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True,
                                claimed_steps=("check_atfex_c145_tms_and_rfs_for_smoke_or_smell",),
                                history={"relay_target_dropped_again": "yes"}), kb)
    assert t.terminal.step_id == "recheck_equipment_and_stop_using_rb_if_target_drops_again"
    t = run_turn(d, StateUpdate(claimed_steps=("recheck_equipment_and_stop_using_rb_if_target_drops_again",)), kb)
    assert t.terminal.kind == "confirm"
    # no recurrence stated → the ordinary reset step is the one offered
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True,
                                claimed_steps=("check_atfex_c145_tms_and_rfs_for_smoke_or_smell",)), kb)
    assert t.terminal.step_id == "reset_relay_target_and_resume_within_current_rating"
