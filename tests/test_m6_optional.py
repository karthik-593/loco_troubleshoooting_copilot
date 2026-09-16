"""M6-optional KB additions: QRSI-2 (§6.02.2, mirror of QRSI-1) and fire_on_loco
(Ch.1 "Use of fire extinguishers" B.1–B.12 / Ch.4 item 6)."""
from agent.graph import Copilot
from engine.gates import evaluate_gates
from engine.run_turn import run_turn
from engine.state import DiagnosisState, StateUpdate
from llm.interface import FakeProvider, Providers
from llm.schemas import DecideOutput, ParseOutput

Q2 = "QRSI2_drops_on_run"
FIRE = "fire_on_loco"
Q2_STEPS = ("check_traction_circuit_2", "reset_and_accelerate_gradually",
            "recheck_and_reset_if_drops_after_long_interval", "try_hmcs2_positions_if_frequent",
            "isolate_tm_of_bad_hmcs2_position", "isolate_truck_2_if_all_positions")
FIRE_STEPS = ("open_dj_lower_panto_hba_off_flasher_stop", "take_extinguisher_to_equipment",
              "break_seal_remove_clip", "direct_jet_at_base_of_fire", "use_remaining_extinguishers_if_needed",
              "discharge_remaining_pressure", "isolate_equipment_inform_tlc_work_onwards", "logbook_remark")


def test_qrsi2_mirrors_qrsi1_shape_with_swapped_terminals(kb):
    f = kb.get(Q2); f1 = kb.get("QRSI1_drops_on_run")
    assert f.step_ids == list(Q2_STEPS) and f.gated_steps == []
    assert "a3, a4" in f.step(Q2_STEPS[0]).text and "3900" in f.step(Q2_STEPS[0]).text   # swapped vs QRSI-1
    assert "a5, a6" in f1.step("check_traction_circuit_1").text.split("5400")[0]
    assert "INFERRED" in f.step(Q2_STEPS[0]).isolation.source                              # same provenance split
    assert all("§6.02.2" in s.citation for s in f.steps)
    assert f.config_dependency == "none" and f.type_dependency == "none"
    assert kb.match_alias("qrsi 2 dropped").fault_id == Q2 and kb.match_alias("QRSI-1 dropped").fault_id != Q2


def test_qrsi2_ladder_branches_and_resolution(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=Q2, claimed_steps=Q2_STEPS[:2],
                                history={"drops_frequently": "yes", "drops_after_long_interval": "no"}), kb)
    assert t.terminal.step_id == "try_hmcs2_positions_if_frequent" and "HMCS-2" in t.terminal.message
    t = run_turn(d, StateUpdate(claimed_steps=(Q2_STEPS[3],),
                                history={"drops_in_particular_hmcs2_position": "no", "drops_in_all_hmcs2_positions": "yes"}), kb)
    assert t.terminal.step_id == "isolate_truck_2_if_all_positions" and "HVSI-2" in t.terminal.message
    t = run_turn(d, StateUpdate(claimed_steps=(Q2_STEPS[5],)), kb)
    assert t.terminal.kind == "confirm" and any("50%" in g for g in t.terminal.guidance)
    assert not evaluate_gates(d, kb.get(Q2)).fired


def test_qrsi2_isolation_and_defer(kb):
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=Q2, claimed_steps=Q2_STEPS[:1],
                                               history={"traction2_abnormality_found": "yes", "isolation_successful": "no"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "§6.02.2(a)" in t.terminal.source
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=Q2, history={"load_and_road_do_not_permit": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "§6.02.2(e)" in t.terminal.source


def test_fire_on_loco_shape_and_flow(kb):
    f = kb.get(FIRE)
    assert f.step_ids == list(FIRE_STEPS) and f.gated_steps == []
    assert "Ch.1 B.2" in f.step(FIRE_STEPS[0]).citation and "Ch.4" in f.step(FIRE_STEPS[0]).citation
    assert [d.fact for d in f.defer_conditions] == ["fire_uncontrollable"]
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=FIRE), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == FIRE_STEPS[0]
    assert "Open DJ" in t.terminal.message and "flasher" in t.terminal.message
    t = run_turn(d, StateUpdate(claimed_steps=FIRE_STEPS[:4]), kb)
    assert t.terminal.step_id == FIRE_STEPS[4]
    t = run_turn(d, StateUpdate(history={"fire_uncontrollable": "yes"}), kb)
    assert t.terminal.kind == "defer_to_TLC" and "fire engine" in t.terminal.message and "Ch.1 B.9" in t.terminal.source


def test_fire_on_loco_completes_on_logbook_remark(kb):
    t = run_turn(DiagnosisState(), StateUpdate(fault_id=FIRE, claimed_steps=FIRE_STEPS), kb)
    assert t.terminal.kind == "confirm" and any("TLC informed" in g for g in t.terminal.guidance)


def test_fire_on_loco_reachable_by_parser_no_confirmation_turn():
    """Gate-free: an LLM-guessed match goes straight to the first action (§5.5 is for
    hard-gated faults only)."""
    cp = Copilot(Providers(
        parse=FakeProvider(structured_queue=[ParseOutput(fault_guess=FIRE, fault_confidence=0.9, fault_presenting="yes")]),
        decide=FakeProvider(structured_queue=[DecideOutput(tool="diff_completed_steps", reason="t")]),
        phrase=FakeProvider()))
    r = cp.turn(DiagnosisState(), "there is smoke coming from the machine room")
    assert r.terminal.kind == "ask_step" and r.terminal.step_id == FIRE_STEPS[0]


def test_no_automatic_jump_from_abnormality_refusal_to_fire_procedure(kb):
    """Held decision: an abnormality refusal on QLM does NOT switch the fault to fire_on_loco."""
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id="QLM_dropped", claimed_steps=("check_ht2_compartment",),
                                history={"abnormality_found": "yes"}), kb)
    assert t.terminal.kind == "refuse" and d.matched_fault == "QLM_dropped"


def test_specific_fault_alias_outranks_the_general_fire_procedure(kb):
    """'QLM locked, smoke coming from the CGR' names a specific fault; the general fire
    procedure (precedence -1) must not make the match ambiguous."""
    assert kb.get(FIRE).precedence == -1
    assert kb.match_alias("QLM locked, smoke coming from the CGR").fault_id == "QLM_dropped"
    assert kb.match_alias("panto damaged and smoke coming from roof").fault_id == "pantograph_damaged"
    assert kb.match_alias("smoke coming from the machine room").fault_id == FIRE
    assert kb.match_alias("ht2 checked, no smoke, oil ok") is None      # a negative finding is not the fire procedure


# ---------------------------------------------------------------------------
# Seen live 2026-09-16 on QRSI-2: "no" to "have you checked ...?" was asked again verbatim;
# a branch step was asked as "have you done (c)?"; step (d) lost its substance in phrasing.
# ---------------------------------------------------------------------------

def _cp(parses, tools):
    return Copilot(Providers(parse=FakeProvider(structured_queue=list(parses)),
                             decide=FakeProvider(structured_queue=[DecideOutput(tool=t, reason="t") for t in tools]),
                             phrase=FakeProvider()))


def test_declined_check_is_put_as_the_next_action_not_asked_again():
    d = DiagnosisState()
    cp = _cp([ParseOutput(fault_guess=Q2, fault_confidence=0.9, fault_presenting="yes", claimed_steps=[Q2_STEPS[1]]),
              ParseOutput(fault_guess=None, fault_confidence=0.0, denies_asked_step="yes"),
              ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[Q2_STEPS[0]],
                          facts={"traction2_abnormality_found": "no"})],
             ["diff_completed_steps"] * 3)
    t1 = cp.turn(d, "qrsi2 dropped. i resetted")
    assert t1.terminal.kind == "ask_step" and t1.terminal.step_id == Q2_STEPS[0] and not t1.terminal.do_now
    t2 = cp.turn(d, "no", last_assistant=t1.reply)
    assert t2.terminal.step_id == Q2_STEPS[0] and t2.terminal.do_now and not t2.repeat
    assert t2.reply.startswith("Do this now:") and "RSI-2" in t2.reply
    t3 = cp.turn(d, "checked all, nothing abnormal", last_assistant=t2.reply)
    assert Q2_STEPS[0] in d.steps_claimed_done and Q2_STEPS[0] not in d.steps_declined
    assert t3.terminal.step_id != Q2_STEPS[0]


def test_backstop_a_repeated_ask_on_the_same_step_becomes_do_now_without_any_parser_flag():
    d = DiagnosisState()
    blind = ParseOutput(fault_guess=None, fault_confidence=0.0)          # parser saw nothing at all
    cp = _cp([ParseOutput(fault_guess=Q2, fault_confidence=0.9, fault_presenting="yes"), blind, blind],
             ["diff_completed_steps"] * 3)
    t1 = cp.turn(d, "qrsi2 dropped")
    t2 = cp.turn(d, "no i didnt", last_assistant=t1.reply)
    assert t2.repeat and t2.terminal.do_now and Q2_STEPS[0] in d.steps_declined
    t3 = cp.turn(d, "hmm", last_assistant=t2.reply)
    assert t3.terminal.do_now                                            # stays an instruction


def test_unstated_branch_asks_the_condition_not_a_branch_step(kb):
    """After (a)+(b) with nothing said about recurrence, (c) 'after a long interval' and (d)
    'frequently' are alternatives: ask which applies, assembled from the KB 'If ...' heads."""
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=Q2, claimed_steps=Q2_STEPS[:2],
                                history={"traction2_abnormality_found": "no"}), kb)
    assert t.terminal.kind == "ask_history" and t.terminal.message.startswith("Which applies now")
    assert "after a long interval" in t.terminal.message and "dropping frequently" in t.terminal.message
    assert "§6.02.2(b)" in t.terminal.source and "§6.02.2(c)" in t.terminal.source
    # the answer routes to (d) directly
    t = run_turn(d, StateUpdate(history={"drops_frequently": "yes", "drops_after_long_interval": "no"}), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == Q2_STEPS[3] and "HMCS-2" in t.terminal.message
    # ... or to the resolved confirm
    d2 = DiagnosisState()
    run_turn(d2, StateUpdate(fault_id=Q2, claimed_steps=Q2_STEPS[:2], history={"traction2_abnormality_found": "no"}), kb)
    t = run_turn(d2, StateUpdate(history={"fault_resolved": "yes"}), kb)
    assert t.terminal.kind == "confirm"


def test_branch_question_only_when_no_condition_is_stated(kb):
    d = DiagnosisState()
    t = run_turn(d, StateUpdate(fault_id=Q2, claimed_steps=Q2_STEPS[:2],
                                history={"traction2_abnormality_found": "no", "drops_after_long_interval": "yes"}), kb)
    assert t.terminal.kind == "ask_step" and t.terminal.step_id == Q2_STEPS[2]


def test_phrase_guard_rejects_an_ask_step_that_lost_the_equipment(kb):
    from llm.phrase import guard
    from engine import terminals as T
    f = kb.get(Q2); t = T.ask_step(f, f.step(Q2_STEPS[3]))
    assert "ask_step_lost_substance" in guard(t, "Have you checked the equipment listed above for abnormality?")
    assert not guard(t, "If it is dropping frequently and the traction circuit-2 equipment is normal, have you tried HMCS-2 in positions 2, 3 and 4 one by one?")


# ---------------------------------------------------------------------------
# Spoken short form (phrase prompt 2026-09-16): long component lists are summarised and the
# exact list is OFFERED; "yes, the list" re-renders the last ask verbatim with no model.
# ---------------------------------------------------------------------------

def test_detail_request_re_renders_the_last_ask_verbatim_without_engine_or_model():
    d = DiagnosisState()
    cp = _cp([ParseOutput(fault_guess=Q2, fault_confidence=0.9, fault_presenting="yes"),
              ParseOutput(fault_guess=None, fault_confidence=0.0, asks_for_detail="yes")],
             ["diff_completed_steps"] * 2)
    t1 = cp.turn(d, "qrsi2 dropped")
    t2 = cp.turn(d, "yes give me the list", last_assistant=t1.reply)
    assert t2.stop_reason == "detail" and t2.terminal.verbatim and t2.terminal.step_id == t1.terminal.step_id
    assert t2.reply.startswith("Next check:") and "RU5, RU6, QD-2 and SJ 4, 5, 6" in t2.reply
    assert not t2.phrase_fallback and t2.tool_path == () and t2.reflex_runs == 0


def test_branch_step_conditioned_this_turn_is_issued_as_do_now(kb):
    d = DiagnosisState()
    run_turn(d, StateUpdate(fault_id=Q2, claimed_steps=Q2_STEPS[:2], history={"traction2_abnormality_found": "no"}), kb)
    t = run_turn(d, StateUpdate(history={"drops_frequently": "yes", "drops_after_long_interval": "no"}), kb)
    assert t.terminal.step_id == Q2_STEPS[3] and t.terminal.do_now          # pilot just reported the condition
    t = run_turn(d, StateUpdate(), kb)
    assert t.terminal.step_id == Q2_STEPS[3] and not t.terminal.do_now      # next turn: back to verifying


def test_phrase_short_form_is_accepted_and_other_truck_is_not(kb):
    from llm.phrase import guard, terminal_payload
    from engine import terminals as T
    f = kb.get(Q2); t = T.ask_step(f, f.step(Q2_STEPS[0]))
    assert "long_list: yes" in terminal_payload(t)
    ok = "Look for smoke or burning smell on the RSI-2 side and the truck-2 traction motors — want the exact component list?"
    assert guard(t, ok) == ()
    assert "ask_step_lost_substance" in guard(t, "Have you checked the equipment for smoke?")          # no tag, no offer
    assert any(v.startswith("ask_step_named_other_circuit:RSI-1") for v in guard(t, ok.replace("RSI-2", "RSI-1")))


def test_out_of_scope_guard_checks_the_list_not_the_phrase(kb):
    from llm.phrase import guard
    from llm.parse import out_of_scope_reason
    from engine import terminals as T
    t = T.defer_to_TLC(out_of_scope_reason(kb))
    names = out_of_scope_reason(kb).split("verify: ")[1].split(". Refer")[0]      # the listed_as entries
    assert guard(t, f"This one's outside the procedure set. You can verify {names}. Contact TLC.") == ()
    assert "out_of_scope_dropped_coverage" in guard(t, "Outside the procedure set. I can verify QLM dropped and a few others. Contact TLC.")
