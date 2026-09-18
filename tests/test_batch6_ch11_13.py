"""Batch 6 (Ch.11 isolation of equipment, Ch.12 wedging, Ch.13 special instructions, encoded
2026-09-18): the REFERENCE PROCEDURES the fault files have been citing all along — isolating a
TM / battery / RSI block, wedging a contactor or relay, grounding the loco, earthing the OHE,
renewing a fuse, EEC and manual GR, VCD. They carry the SAME hazard gates and the same intents
as the fault files that point at them, so a claim made on a live loco is refused here too.
Also the KB-wide integrity checks that 101 files make worth having."""
from engine.matcher import load_kb
from engine.run_turn import run_turn
from engine.state import ACTIONS, DiagnosisState, StateUpdate

CH11 = ["Isolation_of_RSI_block", "Isolation_of_traction_motors", "Isolation_of_MCP",
        "Isolation_of_battery_charger", "Isolation_of_defective_battery", "Isolation_of_RGCP",
        "Isolation_of_blower_motor_relays"]
CH12 = ["Manual_operation_of_relays", "Wedging_of_EM_contactors", "Wedging_of_relays"]
CH13 = ["EEC_operation", "Manual_operation_of_GR", "Working_from_rear_cab",
        "Working_without_pilot_lamps", "Grounding_the_loco", "Earthing_OHE_for_roof_work",
        "Precautions_to_prevent_fire", "First_aid_electrical_shock", "Emergency_telephone",
        "Emergency_power_block", "Speedometer_instructions", "Renewing_a_fuse",
        "Cleaning_relay_interlocks", "Dummying_safety_valve_SS2", "BPEMS_operation_and_reset",
        "VCD_operation_and_reset"]


def test_reference_procedures_cite_their_chapter_and_yield_to_a_named_fault(kb):
    for fids, marks in ((CH11, ("§11.", "§6.03", "§8.04")), (CH12, ("§12.",)),
                        (CH13, ("§13.", "General Instructions"))):
        for fid in fids:
            f = kb.get(fid)
            assert all(any(m in s.citation for m in marks) for s in f.steps), fid
            assert f.precedence == -1, fid                       # a reported fault outranks a how-to
            assert f.listed_as == "isolation, wedging and special instructions", fid


def test_every_gate_action_is_a_registered_intent(kb):
    """A gate whose action is not in ACTIONS can never be put in play early by the parser."""
    for fid in kb.fault_ids:
        for s in kb.get(fid).gated_steps:
            if s.gate.action is not None:
                assert s.gate.action in ACTIONS, (fid, s.id, s.gate.action)


def test_every_route_and_combination_target_resolves(kb):
    for fid in kb.fault_ids:
        f = kb.get(fid)
        for r in list(f.route_rules) + list(f.combination_rules):
            assert r.route_to in kb.fault_ids, (fid, r.route_to)


def test_every_alias_resolves_to_its_own_fault(kb):
    """101 files: an alias that now matches two faults would silently become ambiguous."""
    for fid in kb.fault_ids:
        for alias in kb.get(fid).aliases:
            hit = kb.match_alias(alias)
            assert hit is not None and hit.fault_id == fid, (fid, alias, hit and hit.fault_id)


def test_grounding_procedure_keeps_the_ht_compartment_gate(kb):
    g = kb.get("Grounding_the_loco").step("attend_the_repairs_in_ht_compartment").gate
    assert g.action == "enter_HT_compartment" and g.preconditions == ["loco_grounded"]
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="Grounding_the_loco", fault_confirmed=True), kb)
    t = run_turn(d, StateUpdate(intended_action="enter_HT_compartment", history={"loco_grounded": "no"}), kb)
    assert t.terminal.kind == "refuse" and "grounded" in t.terminal.message


def test_roof_work_procedures_keep_the_power_block_gate(kb):
    for fid, sid in (("Earthing_OHE_for_roof_work", "climb_on_the_roof_with_ohe_staff"),
                     ("Emergency_power_block", "do_the_roof_work_with_the_ladder_fixed")):
        g = kb.get(fid).step(sid).gate
        assert g.action == "work_on_roof"
        assert g.preconditions == ["ohe_power_block_obtained_and_earthed", "loco_grounded"]
        d = DiagnosisState()
        run_turn(d, StateUpdate(fault_id=fid, fault_confirmed=True), kb)
        t = run_turn(d, StateUpdate(intended_action="work_on_roof",
                                    history={"ohe_power_block_obtained_and_earthed": "no"}), kb)
        assert t.terminal.kind == "refuse", fid


def test_wedging_reference_carries_each_relays_own_before_condition(kb):
    f = kb.get("Wedging_of_relays")
    assert f.step("q118_wedge").gate.preconditions == ["loco_de_energised_for_wedging", "all_em_contactors_open"]
    assert f.step("q45_wedge").gate.preconditions == ["loco_de_energised_for_wedging", "no_operation_a_ending_trouble"]
    # Q45 with Operation 'A' ending trouble on the loco is refused, as §12.03 heads that block
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True, history={"relay_to_wedge": "q45"}), kb)
    t = run_turn(d, StateUpdate(intended_action="wedge_Q45",
                                history={"no_operation_a_ending_trouble": "no"}), kb)
    assert t.terminal.kind == "refuse" and "Operation 'A' ending" in t.terminal.message


def test_fuse_renewal_and_relay_cleaning_are_gated_on_de_energising(kb):
    g = kb.get("Renewing_a_fuse").step("place_the_fuse_in_the_cap_and_insert_it").gate
    assert g.action == "remove_fuse" and g.preconditions == ["loco_de_energised_for_fuse_work"]
    for sid in ("clean_a_normally_open_interlock", "clean_a_normally_closed_interlock"):
        g = kb.get("Cleaning_relay_interlocks").step(sid).gate
        assert g.action == "work_on_relay" and g.preconditions == ["loco_de_energised_for_relay_work"]
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id="Renewing_a_fuse", fault_confirmed=True), kb)
    t = run_turn(d, StateUpdate(intended_action="remove_fuse",
                                history={"loco_de_energised_for_fuse_work": "no"}), kb)
    assert t.terminal.kind == "refuse" and "open DJ" in t.terminal.message


def test_traction_motor_isolation_asks_the_rb_axis_lazily(kb):
    """§11.02's negative-side bit table differs for WAP4 without RB — the axis is asked only
    when the negative-side step is reached, never up front (the batch-1 lazy-axis rule)."""
    f = kb.get("Isolation_of_traction_motors")
    assert f.rb_dependency
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True,
                                history={"tm_to_isolate": "tm1"}), kb)
    assert t.terminal.step_id == "isolate_positive_side_by_hmcs_position"      # no axis needed yet
    t = run_turn(d, StateUpdate(claimed_steps=("isolate_positive_side_by_hmcs_position",)), kb)
    assert t.terminal.kind == "ask_config" and "RB" in t.terminal.message
    t = run_turn(d, StateUpdate(loco_rb="not_fitted"), kb)
    assert t.terminal.step_id == "isolate_negative_side_by_reverser_bit_no_rb"


def test_manual_relay_operation_gates_q118_and_q44_presses(kb):
    f = kb.get("Manual_operation_of_relays")
    assert f.step("q118_press_relay_then_bp2dj").gate.preconditions == ["c118_fully_opened"]
    assert f.step("q44_press_bp2dj_then_q44_rod").gate.preconditions == ["q118_energised_for_manual_q44"]
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True, history={"manual_relay": "q44"}), kb)
    t = run_turn(d, StateUpdate(intended_action="work_on_relay",
                                history={"q118_energised_for_manual_q44": "no"}), kb)
    assert t.terminal.kind == "refuse"
    # DJ still not closing after the manual operation → back to the §5.01 intake, per §12.01
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=f.fault_id, fault_confirmed=True, history={"manual_relay": "q45"}), kb)
    run_turn(d, StateUpdate(history={"dj_closes_with_manual_relay": "no"}), kb)
    assert d.matched_fault == "DJ_tripped_on_line"


def test_kb_is_loadable_and_every_step_cites_a_source():
    kb = load_kb()
    assert len(kb.fault_ids) == 101
    for fid in kb.fault_ids:
        for s in kb.get(fid).steps:
            assert s.citation and s.citation.strip(), (fid, s.id)
