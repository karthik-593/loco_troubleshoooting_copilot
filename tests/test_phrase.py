"""phrase: the model adds nothing; any drift from the terminal falls back to the KB text."""
from engine import terminals as T
from engine.state import ACTION_RESET_QLM
from llm.interface import FakeProvider
from llm.phrase import guard, phrase, render_verbatim, terminal_payload
from tests.conftest import ORDINARY, RESET_STEP


def _refuse(qlm):
    st = qlm.step(RESET_STEP)
    return T.refuse(qlm, st, st.gate.on_already_reset, (T.REASON_SECOND_RESET,))


def _caution(qlm, conditional=False):
    st = qlm.step(RESET_STEP)
    return T.caution(qlm, st, st.gate.on_first_reset, conditional=conditional)


def test_out_of_scope_defer_keeps_the_covered_list(kb):
    from llm.parse import out_of_scope_reason
    t = T.defer_to_TLC(out_of_scope_reason(kb))
    assert "I can verify: " in t.message and "safety relay trips" in t.message
    assert "out_of_scope_dropped_coverage" in guard(t, "This isn't in my procedure set. Contact TLC.")
    assert not guard(t, t.message)


def test_good_refusal_passes(qlm):
    r = phrase(_refuse(qlm), FakeProvider(text_queue=[
        "Do not reset QLM again. Make the log-book remark, inform TLC, and arrange a relief loco."]))
    assert not r.used_fallback and r.violations == ()


def test_refusal_that_instructs_reset_falls_back_to_kb_text(qlm):
    """The worst failure (§12.2): a refusal rewritten into a reset instruction."""
    t = _refuse(qlm)
    r = phrase(t, FakeProvider(text_queue=["Reset QLM once and inform TLC; arrange a relief loco."]))
    assert r.used_fallback and "refusal_instructs_reset" in r.violations
    assert r.text == t.message                                   # verbatim KB text


def test_refusal_softened_into_a_permission_falls_back(qlm):
    r = phrase(_refuse(qlm), FakeProvider(text_queue=[
        "Do not reset again. Inform TLC and arrange relief. You can reset it if TLC agrees."]))
    assert r.used_fallback and "refusal_instructs_reset" in r.violations


def test_refusal_missing_actions_falls_back(qlm):
    r = phrase(_refuse(qlm), FakeProvider(text_queue=["Don't reset it again."]))
    assert r.used_fallback
    assert {"refusal_missing_TLC", "refusal_missing_relief"} <= set(r.violations)


def test_caution_must_keep_every_condition(qlm):
    ok = "Reset QLM once and resume, checking feeding-power items every 10 min. Note it in the log book and inform TLC."
    assert guard(_caution(qlm), ok) == ()
    bad = phrase(_caution(qlm), FakeProvider(text_queue=["Reset QLM once and carry on."]))
    assert bad.used_fallback and "caution_missing_TLC" in bad.violations


def test_conditional_caution_keeps_the_condition(qlm):
    t = _caution(qlm, conditional=True)
    ok = ("If nothing abnormal was found, reset QLM once and resume, checking feeding-power items "
          "every 10 min; log it and inform TLC.")
    assert guard(t, ok) == ()
    dropped = ("Reset QLM once and resume, checking feeding-power items every 10 min; log it and inform TLC.")
    assert "caution_dropped_condition" in guard(t, dropped)
    assert render_verbatim(t).startswith("If no abnormality")


def test_questions_must_still_ask(qlm):
    t = T.ask_step(qlm, qlm.step(ORDINARY[2]), hold_action=ACTION_RESET_QLM)
    full = "the CGR arc chutes, RGR and RPGR for red-hot condition, and the TFR terminals, bushings, HT cable, TFILM, TFSPM and breathers"
    assert "question_not_asked" in guard(t, f"Check {full}.")
    assert "hold_action_dropped" in guard(t, f"Have you checked {full}?")
    assert guard(t, f"Before resetting, have you checked {full}?") == ()
    # a rendering that loses most of the equipment named is not the step (seen live: step (d)
    # of QRSI-2 rendered as "have you checked the equipment listed above?")
    assert "ask_step_lost_substance" in guard(t, "Before resetting, have you checked the CGR arc chutes and terminals?")
    assert "Hold the reset QLM" in render_verbatim(t)


def test_markdown_and_length_rejected(qlm):
    t = T.ask_history(qlm, qlm.step(RESET_STEP), "Was QLM reset earlier this trip? Check the log book.")
    assert "markdown_structure" in guard(t, "- Was QLM reset earlier this trip?")
    assert "too_long" in guard(t, "Was it reset? " + "x" * 700)


def test_provider_failure_falls_back(qlm):
    class Boom:
        def text(self, s, u): raise RuntimeError("down")
        def structured(self, s, u, sch): raise RuntimeError("down")
    t = _refuse(qlm)
    r = phrase(t, Boom())
    assert r.used_fallback and r.text == t.message and r.violations[0].startswith("provider_error")


def test_payload_contains_only_terminal_content(qlm):
    t = _caution(qlm, conditional=True)
    p = terminal_payload(t)
    assert t.message in p and "conditional: yes" in p
    assert ORDINARY[0] not in p          # the rest of the KB is not shown to the model


def test_invented_hold_instruction_is_rejected(qlm):
    t = T.ask_step(qlm, qlm.step(ORDINARY[0]))                      # no hold_action
    assert "added_hold_instruction" in guard(t, "Have you checked the HT-2 compartment and GR safety valve? Hold traction until that's done.")
    assert guard(t, "Have you checked the HT-2 compartment for smoke, smell, fire, heat or oil splashes from the explosion vent, vent pipe and GR safety valve?") == ()


def test_not_isolated_refusal_needs_tlc_but_not_a_negation(kb):
    f = kb.get("QLM_with_QOP_QRSI"); st = f.step("reset_decision")
    t = T.refuse(f, st, "Use fire extinguishers; try to isolate the abnormal equipment. Contact TLC for further instructions.",
                 (T.REASON_NOT_ISOLATED,))
    assert guard(t, "Use the fire extinguisher if needed; since it could not be isolated, contact TLC for further instructions.") == ()
    assert "refusal_missing_fire_precaution" in guard(t, "Since it could not be isolated, contact TLC.")
    assert "refusal_missing_TLC" in guard(t, "Since it could not be isolated, wait for advice.")
    assert "refusal_instructs_reset" in guard(t, "Reset the targets and contact TLC.")


def test_confirm_after_done_reset_uses_after_first_reset_text_and_forward_rule(kb):
    """After the permitted reset the confirm carries the (d)(e) follow-up worded for a done
    reset (never "Reset QLM once and ...") plus the (f)(ii) rule as the forward warning."""
    from engine.reassess import _confirm_after_reset
    from engine.state import DiagnosisState
    f = kb.get("QLM_dropped"); g = f.step(RESET_STEP).gate
    assert g.after_first_reset and g.after_first_reset.startswith("Resume traction")
    d = DiagnosisState(); d.matched_fault = f.fault_id; d.steps_claimed_done.add(RESET_STEP)
    t = _confirm_after_reset(d, f, (), f.source)
    assert T.RESET_DONE_NOTE in t.message
    assert not any(x.startswith("Reset QLM once") for x in t.guidance)
    assert any("10 minutes" in x for x in t.guidance) and any("DO NOT reset" in x for x in t.guidance)
    v = render_verbatim(t)
    assert "Reset QLM once and" not in v and "DO NOT reset" in v and "10 minutes" in v
    # guard: the forward rule and the follow-up must survive phrasing; no relief loco invented
    assert "confirm_missing_forward_rule" in guard(t, "Resume traction, check every 10 minutes, log it, tell TLC.")
    assert "confirm_added_relief" in guard(t, "Check every 10 minutes; if it acts again do not reset, get relief.")
    assert not guard(t, "Resume traction, checking every 10 minutes. Log it and inform TLC. QLM may be reset only once; if it acts again do not reset.")


def test_repeat_rewrites_only_a_confirm(kb):
    from llm.phrase import REPEAT_MESSAGE, for_repeat
    f = kb.get("QLM_dropped")
    t = T.confirm(f, guidance=("g",))
    assert for_repeat(t).message == REPEAT_MESSAGE and for_repeat(t).guidance == ("g",)
    r = phrase(t, FakeProvider(text_queue=["Nothing further. g"]), repeat=True)
    assert r.text.startswith("Nothing further")
    c = T.caution(f, f.step(RESET_STEP), "Reset once and monitor every 10 minutes.")
    assert for_repeat(c) is not c and for_repeat(c).message == REPEAT_MESSAGE     # helper is generic...
    r = phrase(c, FakeProvider(text_queue=["Reset once and monitor every 10 minutes."]), repeat=True)
    assert "Nothing further" not in r.text                                        # ...but phrase() applies it to confirm only


# --- per-step equipment vocabulary (kb/vocabulary.yaml): the three recorded slips ---------

def test_breathers_must_not_be_rendered_as_breakers(qlm):
    """Recorded slip (HANDOFF): QLM (c)'s 'breathers' rendered as 'breakers'. The substance
    guard passes it — every other word survives — so only the vocabulary reject catches it."""
    t = T.ask_step(qlm, qlm.step(ORDINARY[2]))
    tail = ("the CGR arc chutes, RGR and RPGR for red-hot condition, and the TFR terminals, "
            "A33/A0/A34 bushings, HT cable, TFILM, TFSPM and oil leakage from")
    bad = guard(t, f"Have you checked {tail} the breakers, drain plug and oil trap box?")
    assert any(x.startswith("equipment_noun_substituted") for x in bad)
    assert "breathers->breakers" in " ".join(bad)
    good = guard(t, f"Have you checked {tail} the breathers, drain plug and oil trap box?")
    assert not any(x.startswith("equipment_noun_substituted") for x in good)


def test_line_contactors_must_not_be_rendered_as_reactors(kb):
    """Recorded slip (HANDOFF): 'L-series reactors' for the L4/L5/L6 line contactors. This is
    a SHORT-FORM rendering, which the substance guard sanctions — the noun reject is the only
    check that fires."""
    f = kb.get("QRSI2_drops_on_run")
    t = T.ask_step(f, f.step("check_traction_circuit_2"))
    bad = guard(t, "Any smoke or burning smell anywhere on the RSI-2 side, including the "
                   "L-series reactors? Want the exact component list?")
    assert "ask_step_lost_substance" not in bad              # the old guards let this through
    assert any(x.startswith("equipment_noun_substituted") for x in bad)
    good = guard(t, "Any smoke or burning smell anywhere on the RSI-2 side, including the "
                    "L4, L5 and L6 line contactors? Want the exact component list?")
    assert not any(x.startswith("equipment_noun_substituted") for x in good)


def test_blowers_must_not_be_rendered_as_motor_contactors(kb):
    """Recorded slip (HANDOFF): 'motor contactors' for the MVMT/MVSL blowers."""
    f = kb.get("Op_B_part1")
    t = T.ask_step(f, f.step("isolate_mvsl_not_working_and_work_50pc"))
    bad = guard(t, "Since one of them is not working, isolate that MVSL and its motor "
                   "contactors, then work onwards with 50% of the maximum permitted load. "
                   "Tell me once it's done.")
    assert any(x.startswith("equipment_noun_substituted") for x in bad)
    good = guard(t, "Since one of them is not working, isolate that MVSL along with its MVMT "
                    "and MVSI, then work onwards with 50% of the maximum permitted load. "
                    "Tell me once it's done.")
    assert not any(x.startswith("equipment_noun_substituted") for x in good)


def test_vocabulary_reject_does_not_fire_on_unrelated_terminals(qlm):
    """No false positive: a terminal whose KB text names none of the recorded terms is never
    flagged, however it is worded — the entry is keyed on the KB text, not the reply."""
    t = T.ask_step(qlm, qlm.step(ORDINARY[1]))               # "Check TFP and GR oil level..."
    for rendering in ("Have you checked the TFP and GR oil levels for any abnormal increase?",
                      "Have you checked the breakers and reactors?"):
        assert not any(x.startswith("equipment_noun_substituted") for x in guard(t, rendering))


# --- allow_suffix: the KB writes these identifiers with a unit number ---------------------

def test_hyphenated_mvmt_still_matches_the_vocabulary_entry(kb):
    """§7.07 item 1 writes 'MVMT-1, MVMT-2', never bare 'MVMT'. Before `allow_suffix` the
    strict boundary missed every such step, so the recorded blower slip went uncaught."""
    f = kb.get("Op_O")
    t = T.ask_step(f, f.step("check_mvmt_mvrh_for_smell_smoke_fire"))
    assert "MVMT-1" in t.message and "MVMT " not in t.message      # hyphenated, not bare
    bad = guard(t, "Any smell, smoke or fire from the motor contactors or MVRH? If there is, "
                   "use the fire extinguishers and work with precautions.")
    assert any(x.startswith("equipment_noun_substituted") for x in bad)
    assert "MVMT->motor contactors" in " ".join(bad)


def test_unhyphenated_mvmt2_and_mvsl_match_the_vocabulary_entry(kb):
    """§7.01.3 writes 'MVMT1, MVMT2, MVRH, MVSL1, MVSL2' — no hyphen, no space."""
    f = kb.get("ICDJ_Q118_branch")
    t = T.ask_step(f, f.step("wedge_q118"))
    assert "MVMT2" in t.message and "MVSL2" in t.message
    bad = guard(t, "Wedge Q118 energised and work with precautions: all EM contactors open "
                   "before wedging, C118 fully open after closing DJ, check the motor "
                   "contactors frequently, watch the TFR oil level, and avoid quick GR "
                   "regression. Tell me when it's done.")
    joined = " ".join(bad)
    assert "MVMT->motor contactors" in joined and "MVSL->motor contactors" in joined


def test_hyphenated_mvsl2_matches_the_vocabulary_entry(kb):
    """§7.05 item 3 writes 'MVSL-1 and MVSL-2'."""
    f = kb.get("Op_B_part1")
    t = T.ask_step(f, f.step("check_mvsl1_mvsl2_working"))
    assert "MVSL-2" in t.message
    bad = guard(t, "Are the motor contactors working? Tell me whether both are working, one "
                   "is not, or neither is.")
    assert "MVSL->motor contactors" in " ".join(bad)


def test_widened_boundary_does_not_false_positive_on_correct_blower_wording(kb):
    """The widened match is KB-side only: a rendering that names the blowers correctly is
    never flagged, in any of the three spellings the KB uses."""
    cases = [("Op_O", "check_mvmt_mvrh_for_smell_smoke_fire",
              "Any smell, smoke or fire from MVMT-1, MVMT-2 or MVRH? If there is, use the "
              "fire extinguishers and work with precautions."),
             ("ICDJ_Q118_branch", "wedge_q118",
              "Wedge Q118 energised and work with precautions: all EM contactors open before "
              "wedging, C118 fully open after closing DJ, check MVMT1, MVMT2, MVRH, MVSL1 "
              "and MVSL2 frequently, watch the TFR oil level for colour change or abnormal "
              "increase, and avoid quick GR regression. Tell me when it's done."),
             ("Op_B_part1", "check_mvsl1_mvsl2_working",
              "Are MVSL-1 and MVSL-2 both working? Tell me whether both are working, one is "
              "not working, or both are not.")]
    for fault_id, step_id, rendering in cases:
        f = kb.get(fault_id)
        t = T.ask_step(f, f.step(step_id))
        v = guard(t, rendering)
        assert not any(x.startswith("equipment_noun_substituted") for x in v), (fault_id, v)


def test_allow_suffix_does_not_widen_the_other_entries(kb, qlm):
    """breathers and L1-L6 carry no allow_suffix, so their KB-side match is unchanged: a
    suffixed spelling must NOT satisfy them (proves the two boundaries stayed separate)."""
    from llm.phrase import _wrong_equipment_noun
    t = T.ask_step(qlm, qlm.step(ORDINARY[2]))                    # real step: bare "breathers"
    assert _wrong_equipment_noun(t, "checked the breakers?") == ["breathers->breakers"]
    f = kb.get("QRSI2_drops_on_run")
    t2 = T.ask_step(f, f.step("check_traction_circuit_2"))        # real step: bare "L4, L5, L6"
    assert any(h.startswith("L4->") for h in _wrong_equipment_noun(t2, "the l-series reactors?"))
    # a synthetic terminal whose text has ONLY suffixed forms: strict entries must not match
    synthetic = T.Terminal(kind="ask_step", fault_id=None, message="Check L4-1 and breathers-2.",
                           source="test")
    assert _wrong_equipment_noun(synthetic, "the reactors and the breakers?") == []
