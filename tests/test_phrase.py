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
    assert "I can verify: QLM dropped" in t.message
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
    assert "question_not_asked" in guard(t, "Check the CGR arc chutes and terminals.")
    assert "hold_action_dropped" in guard(t, "Have you checked the CGR arc chutes and terminals?")
    assert guard(t, "Before resetting, have you checked the CGR arc chutes and terminals?") == ()
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
    assert "added_hold_instruction" in guard(t, "Have you checked the HT-2 compartment? Hold traction until that's done.")
    assert guard(t, "Have you checked the HT-2 compartment for smoke or oil splashes?") == ()


def test_not_isolated_refusal_needs_tlc_but_not_a_negation(kb):
    f = kb.get("QLM_with_QOP_QRSI"); st = f.step("reset_decision")
    t = T.refuse(f, st, "Use fire extinguishers; try to isolate the abnormal equipment. Contact TLC for further instructions.",
                 (T.REASON_NOT_ISOLATED,))
    assert guard(t, "Use the fire extinguisher if needed; since it could not be isolated, contact TLC for further instructions.") == ()
    assert "refusal_missing_fire_precaution" in guard(t, "Since it could not be isolated, contact TLC.")
    assert "refusal_missing_TLC" in guard(t, "Since it could not be isolated, wait for advice.")
    assert "refusal_instructs_reset" in guard(t, "Reset the targets and contact TLC.")


def test_confirm_after_done_reset_carries_the_already_done_cue(kb):
    from dataclasses import replace
    f = kb.get("QLM_dropped")
    t = T.confirm(f, guidance=(f.step(RESET_STEP).gate.on_first_reset,))
    assert "already_done" not in terminal_payload(t)
    t = replace(t, message=t.message + " The one permitted reset has been done.")
    assert "already_done" in terminal_payload(t) and "10 minutes" in terminal_payload(t)
