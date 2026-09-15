"""§6.1.1(f) — the two refuse triggers, verified end-to-end through the LangGraph loop.

(f)(ii) "QLM acts second time":  reset once per (d) → resume → QLM re-locks → REFUSE.
(f)(i)  abnormality:             (a)/(b) findings get the generic terminal; (c)'s inline
                                 "fire extinguisher + relief engine" attaches to (c) only.

The recurrence guarantee lives in the ENGINE (update_state backstop), not in the parser.
"""
from agent.graph import REFLEX_SHORT_CIRCUIT, Copilot
from engine.gates import Outcome, evaluate_gates
from engine.state import (
    HF_ABNORMALITY,
    HF_RECURRED,
    HF_RESET_EARLIER,
    HF_RESET_INSTRUCTED,
    HF_RESET_PERFORMED,
    DiagnosisState,
    StateUpdate,
    update_state,
)
from engine.terminals import REASON_ABNORMALITY, REASON_RECURRED, REASON_SECOND_RESET
from llm.interface import FakeProvider, Providers
from llm.phrase import guard, render_verbatim
from llm.schemas import DecideOutput, ParseOutput
from tests.conftest import ORDINARY, QLM, RESET_STEP

QOP = "QLM_with_QOP_QRSI"
TRACTION = "check_traction_power_circuit"


def _prov(parse_outs, decide_tools):
    return Providers(
        parse=FakeProvider(structured_queue=list(parse_outs)),
        decide=FakeProvider(structured_queue=[DecideOutput(tool=t, reason="t") for t in decide_tools]),
        phrase=FakeProvider())


def _blind(**kw):
    """A parse with NO recurrence cue: fault_recurred and fault_presenting left unknown."""
    return ParseOutput(fault_guess=None, fault_confidence=0.0, **kw)


# ---------------------------------------------------------------------------
# TEST 1 — recurrence end-to-end: caution → pilot reports re-trip → refuse
# ---------------------------------------------------------------------------

def test_1_recurrence_end_to_end_caution_then_retrip_refuses():
    d = DiagnosisState()
    cp = Copilot(_prov(
        [_blind(claimed_steps=list(ORDINARY), abnormality_found="no", was_reset_earlier_this_trip="no"),
         _blind(claimed_steps=[RESET_STEP], fault_resolved="yes"),
         ParseOutput(fault_guess=QLM, fault_confidence=0.9, fault_presenting="yes", fault_recurred="yes")],
        ["diff_completed_steps", "diff_completed_steps", "diff_completed_steps"]))

    t1 = cp.turn(d, "QLM locked, first time. checked ht2, oil, arc chutes - all normal")
    assert t1.terminal.kind == "caution" and t1.terminal.instructs_reset       # (d) first reset
    assert d.history_facts[HF_RESET_INSTRUCTED] == "yes"

    t2 = cp.turn(d, "reset done, resumed traction", last_assistant=t1.reply)
    assert t2.terminal.kind == "confirm"                                        # monitoring path
    assert d.history_facts[HF_RESET_PERFORMED] == "yes"
    assert HF_RECURRED not in d.history_facts                                   # not a recurrence yet

    t3 = cp.turn(d, "QLM locked again after 5 minutes", last_assistant=t2.reply)
    assert t3.terminal.kind == "refuse"
    assert t3.tool_path == (REFLEX_SHORT_CIRCUIT,) and t3.stop_reason == "gate"
    assert t3.terminal.reasons == (REASON_RECURRED,)
    assert d.history_facts[HF_RECURRED] == "yes"
    m = t3.terminal.message
    assert "acted a second time" in m and "DO NOT reset" in m
    assert "log book" in m and "TLC" in m and "relief loco" in m
    assert not t3.terminal.instructs_reset


# ---------------------------------------------------------------------------
# TEST 2 — (a)-vs-(c) abnormality text
# ---------------------------------------------------------------------------

def test_2_a_finding_generic_c_finding_adds_extinguisher(kb):
    qlm = kb.get(QLM)

    # (a) finding: smoke in the HT-2 compartment → generic (f)(i) only
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=QLM, claimed_steps=ORDINARY[:1], history={HF_ABNORMALITY: "yes"}), qlm)
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.REFUSE and v.reasons == (REASON_ABNORMALITY,)
    assert "do not reset" in v.message.lower() and "relief loco" in v.message.lower()
    assert "extinguisher" not in v.message.lower() and "relief engine" not in v.message.lower()

    # (b) finding: abnormal oil level → same generic terminal
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=QLM, claimed_steps=ORDINARY[:2], history={HF_ABNORMALITY: "yes"}), qlm)
    assert "extinguisher" not in evaluate_gates(s, qlm).message.lower()

    # (c) finding: red-hot arc chute → generic PLUS (c)'s inline consequence
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=QLM, claimed_steps=ORDINARY,
                                history={HF_ABNORMALITY: "yes", "arc_chute_terminal_abnormality": "yes"}), qlm)
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.REFUSE and v.reasons == (REASON_ABNORMALITY,)
    assert "use fire extinguisher" in v.message.lower() and "relief engine" in v.message.lower()
    assert "do not reset" in v.message.lower()
    assert "§6.1.1(c)" in qlm.step(ORDINARY[2]).citation


# ---------------------------------------------------------------------------
# TEST 3 — recurrence with the parser BLIND: no "again", no flags → engine backstop
# ---------------------------------------------------------------------------

def test_3_backstop_fires_without_any_parser_cue():
    """Turn 3 says just 'QLM dropped' — no 'again'; the scripted parser returns NOTHING:
    fault_recurred unknown, fault_presenting unknown, no claims. The alias hit alone makes
    the message 'presenting'; the engine already knows a reset was performed → REFUSE."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [_blind(claimed_steps=list(ORDINARY), abnormality_found="no", was_reset_earlier_this_trip="no"),
         _blind(claimed_steps=[RESET_STEP]),
         _blind()],                                     # ← completely blind parse
        ["diff_completed_steps", "diff_completed_steps", "diff_completed_steps"]))

    cp.turn(d, "QLM locked, first time. checked ht2, oil, arc chutes - all normal")
    cp.turn(d, "reset done")
    assert d.history_facts[HF_RESET_PERFORMED] == "yes"

    t3 = cp.turn(d, "QLM dropped")                      # second time, no linguistic cue
    assert d.history_facts[HF_RECURRED] == "yes"        # set by update_state, not by the parser
    assert t3.terminal.kind == "refuse" and t3.terminal.reasons == (REASON_RECURRED,)
    assert t3.tool_path == (REFLEX_SHORT_CIRCUIT,)
    assert cp.providers.parse.calls[-1]["user"].count("Context: a first reset") == 1


def test_3b_backstop_fires_when_reset_only_instructed_not_yet_claimed():
    """Engine said 'reset once'; pilot's next message is just 'QLM locked' (terse: did it,
    it re-tripped). Over-refuse by design."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [_blind(claimed_steps=list(ORDINARY), abnormality_found="no", was_reset_earlier_this_trip="no"),
         _blind()],
        ["diff_completed_steps", "diff_completed_steps"]))
    t1 = cp.turn(d, "QLM locked, first time. all three checks normal")
    assert t1.terminal.kind == "caution"
    t2 = cp.turn(d, "QLM locked", last_assistant=t1.reply)
    assert t2.terminal.kind == "refuse" and REASON_RECURRED in t2.terminal.reasons


def test_3c_no_false_recurrence_on_a_non_presenting_message():
    """After the reset, a status message that does not present the fault must not refuse."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [_blind(claimed_steps=list(ORDINARY), abnormality_found="no", was_reset_earlier_this_trip="no"),
         _blind(claimed_steps=[RESET_STEP]),
         ParseOutput(fault_guess=QLM, fault_confidence=0.9, fault_presenting="no")],
        ["diff_completed_steps"] * 3))
    cp.turn(d, "QLM locked, first time. all three checks normal")
    cp.turn(d, "reset done")
    t3 = cp.turn(d, "running fine, checking every 10 min")
    assert t3.terminal.kind == "confirm" and HF_RECURRED not in d.history_facts


def test_3d_first_message_with_reset_claim_is_not_a_recurrence():
    """'QLM dropped, checked all, reset once, resumed' — the reset and the presentation are
    the SAME occurrence; must confirm, not refuse."""
    d = DiagnosisState()
    cp = Copilot(_prov([_blind(claimed_steps=[*ORDINARY, RESET_STEP], abnormality_found="no",
                               was_reset_earlier_this_trip="no")], ["diff_completed_steps"]))
    t = cp.turn(d, "QLM dropped, checked ht2 oil arc chutes all ok, reset once, resumed")
    assert t.terminal.kind == "confirm" and HF_RECURRED not in d.history_facts


# ---------------------------------------------------------------------------
# TEST 4 — recurrence across a combination reroute
# ---------------------------------------------------------------------------

def test_4_recurrence_fires_against_the_rerouted_fault_identity():
    d = DiagnosisState()
    cp = Copilot(_prov(
        [_blind(claimed_steps=list(ORDINARY), abnormality_found="no", other_relays_acted=["QOP-1"]),
         _blind(claimed_steps=[TRACTION], facts={"traction_abnormality_found": "no"},
                was_reset_earlier_this_trip="no"),
         _blind(claimed_steps=[RESET_STEP]),
         _blind()],                                     # blind parse of the re-trip
        ["diff_completed_steps"] * 4))

    t1 = cp.turn(d, "QLM locked and QOP-1 also dropped. ht2, oil, arc chutes all normal")
    assert d.matched_fault == QOP and t1.terminal.step_id == TRACTION
    t2 = cp.turn(d, "traction circuit checked, all normal. not reset before this trip", last_assistant=t1.reply)
    assert t2.terminal.kind == "caution" and "other relay targets" in t2.terminal.message   # §6.1.2(c)
    t3 = cp.turn(d, "reset all targets, resumed", last_assistant=t2.reply)
    assert t3.terminal.kind == "confirm" and d.history_facts[HF_RESET_PERFORMED] == "yes"

    t4 = cp.turn(d, "QLM locked", last_assistant=t3.reply)   # alias → QLM_dropped family
    assert d.matched_fault == QOP                             # identity NOT dragged back
    assert t4.terminal.kind == "refuse" and t4.terminal.reasons == (REASON_RECURRED,)
    assert t4.terminal.fault_id == QOP
    assert "§6.1.2(d)" in t4.terminal.source
    assert "even though there is no abnormality" in t4.terminal.message


# ---------------------------------------------------------------------------
# framing: same verdict, different reason → different phrasing requirement
# ---------------------------------------------------------------------------

def test_phrase_framing_distinguishes_the_two_f_ii_triggers(qlm):
    from engine import terminals as T
    st = qlm.step(RESET_STEP)
    rec = T.refuse(qlm, st, "QLM has acted a second time, after the first reset. " + st.gate.on_already_reset,
                   (REASON_RECURRED,))
    prior = T.refuse(qlm, st, st.gate.on_already_reset, (REASON_SECOND_RESET,))
    good_rec = "QLM tripped again after your reset, so the fault is real — do not reset it. Log it, inform TLC and get a relief loco."
    good_prior = "It was already reset once this trip — do not reset again. Log it, inform TLC and get a relief loco."
    assert guard(rec, good_rec) == ()
    assert guard(prior, good_prior) == ()
    assert "refusal_recurrence_framing_missing" in guard(rec, good_prior)   # wrong framing for a re-trip
    assert guard(rec, render_verbatim(rec)) == ()                             # fallback text is valid


def test_second_reset_and_recurrence_both_present_keep_both_reasons(qlm):
    s = DiagnosisState()
    update_state(s, StateUpdate(fault_id=QLM, history={HF_RESET_EARLIER: "yes", HF_RECURRED: "yes"}), qlm)
    v = evaluate_gates(s, qlm)
    assert v.outcome is Outcome.REFUSE and set(v.reasons) == {REASON_SECOND_RESET, REASON_RECURRED}
    assert v.message.count("DO NOT reset") == 1


def test_reporting_the_instructed_reset_is_not_a_prior_reset():
    """Seen live: 'reset done, resumed' parsed as was_reset_earlier_this_trip=yes → refused
    as a second reset. The instructed reset reported done must confirm; a later
    re-presentation must still refuse."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [_blind(claimed_steps=list(ORDINARY), abnormality_found="no", was_reset_earlier_this_trip="no"),
         _blind(claimed_steps=[RESET_STEP], was_reset_earlier_this_trip="yes"),     # parser's mistake
         _blind()],
        ["diff_completed_steps"] * 3))
    t1 = cp.turn(d, "QLM locked, first time. checked ht2, oil, arc chutes - all normal")
    assert t1.terminal.kind == "caution"
    t2 = cp.turn(d, "reset done, resumed traction", last_assistant=t1.reply)
    assert t2.terminal.kind == "confirm"                                # not a second-reset refusal
    assert d.history_facts.get(HF_RESET_EARLIER) == "no" and d.history_facts[HF_RESET_PERFORMED] == "yes"
    t3 = cp.turn(d, "QLM dropped", last_assistant=t2.reply)
    assert t3.terminal.kind == "refuse" and REASON_RECURRED in t3.terminal.reasons


def test_a_genuine_prior_reset_stated_with_the_claim_still_refuses():
    """'Reset it now — that's the second time this trip, I reset it once before' → refuse."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [_blind(claimed_steps=list(ORDINARY), abnormality_found="no", was_reset_earlier_this_trip="no"),
         _blind(claimed_steps=[RESET_STEP], was_reset_earlier_this_trip="yes", fault_presenting="yes")],
        ["diff_completed_steps"] * 2))
    cp.turn(d, "QLM locked, checks all normal")
    t2 = cp.turn(d, "reset it, QLM dropped straight away")
    assert t2.terminal.kind == "refuse"


def test_confirm_after_reset_keeps_monitoring_guidance_even_if_marked_resolved():
    """'reset done, resumed' parsed with fault_resolved=yes must still carry §6.1.1(d)(e):
    10-minute feeding-circuit checks, log book, TLC."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [_blind(claimed_steps=list(ORDINARY), abnormality_found="no", was_reset_earlier_this_trip="no"),
         _blind(claimed_steps=[RESET_STEP], fault_resolved="yes")],
        ["diff_completed_steps"] * 2))
    cp.turn(d, "QLM locked, first time. all checks normal")
    t2 = cp.turn(d, "reset done, resumed traction")
    assert t2.terminal.kind == "confirm"
    assert any("10 minutes" in g and "TLC" in g for g in t2.terminal.guidance)
    assert "10 minutes" in t2.reply                      # verbatim fallback carries it too


# ---------------------------------------------------------------------------
# Un-instructed single reset reported in the first message (seen live 2026-09-16)
# ---------------------------------------------------------------------------

def test_first_message_single_reset_double_booked_by_parser_asks_not_refuses():
    """'qlm dropped, i resetted, now working fine' parsed as BOTH reset_decision claimed AND
    was_reset_earlier=yes (one reset, booked twice). Parser-blind engine rule: with no reset
    known before this turn and no recurrence stated, that is ONE reset -> the reflex ASKS the
    (before-this-reset) history question; it does not refuse under (f)(ii)."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=QLM, fault_confidence=0.9, fault_presenting="yes",
                     claimed_steps=[RESET_STEP], was_reset_earlier_this_trip="yes", fault_resolved="yes"),
         _blind(was_reset_earlier_this_trip="no"),
         _blind(fault_presenting="yes")],
        ["diff_completed_steps"] * 3))
    t1 = cp.turn(d, "qlm dropped , i resetted, now working fine")
    assert t1.terminal.kind == "ask_history" and t1.reflex_runs >= 1
    assert "Before the reset you just did" in t1.terminal.message
    assert HF_RESET_EARLIER not in d.history_facts and d.history_facts[HF_RESET_PERFORMED] == "yes"
    t2 = cp.turn(d, "no, only once", last_assistant=t1.reply)
    assert t2.terminal.kind == "confirm"
    assert any("10 minutes" in g and "TLC" in g for g in t2.terminal.guidance)   # §6.1.1(d)(e) rides along
    t3 = cp.turn(d, "QLM dropped again", last_assistant=t2.reply)
    assert t3.terminal.kind == "refuse" and REASON_RECURRED in t3.terminal.reasons


def test_first_message_single_reset_with_prior_answered_yes_refuses():
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=QLM, fault_confidence=0.9, fault_presenting="yes",
                     claimed_steps=[RESET_STEP], was_reset_earlier_this_trip="yes", fault_resolved="yes"),
         _blind(was_reset_earlier_this_trip="yes")],
        ["diff_completed_steps"] * 2))
    t1 = cp.turn(d, "qlm dropped, reset it, running fine")
    assert t1.terminal.kind == "ask_history"
    t2 = cp.turn(d, "yes, once before near the last station", last_assistant=t1.reply)
    assert t2.terminal.kind == "refuse" and REASON_SECOND_RESET in t2.terminal.reasons


def test_first_message_reset_with_recurrence_cue_still_refuses():
    """'dropped again after reset, reset once more, running' -> fault_recurred wins; no ask."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=QLM, fault_confidence=0.9, fault_presenting="yes", fault_recurred="yes",
                     claimed_steps=[RESET_STEP], was_reset_earlier_this_trip="yes")],
        ["diff_completed_steps"]))
    t1 = cp.turn(d, "qlm dropped again after reset, reset once more, running now")
    assert t1.terminal.kind == "refuse" and REASON_RECURRED in t1.terminal.reasons


def test_prior_reset_stated_without_a_reset_claim_still_refuses_on_the_spot():
    """'QLM red. already reset once this trip near the last station' -> unchanged: refuse."""
    d = DiagnosisState()
    cp = Copilot(_prov([ParseOutput(fault_guess=QLM, fault_confidence=0.9, fault_presenting="yes",
                                    was_reset_earlier_this_trip="yes")], ["diff_completed_steps"]))
    t1 = cp.turn(d, "QLM red. already reset once this trip near the last station")
    assert t1.terminal.kind == "refuse" and REASON_SECOND_RESET in t1.terminal.reasons


def test_reset_claim_plus_prior_reset_without_resolution_still_refuses():
    """'QLM locked.' then 'Yes, I reset it once already this trip' — seen live parsed as a
    reset_decision claim AND was_reset_earlier=yes. The relay is live NOW and nothing says
    that reset cleared it: the prior-reset fact stands → refuse (eval gold; over-refuse bias)."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=QLM, fault_confidence=0.9, fault_presenting="yes"),
         ParseOutput(fault_guess=None, fault_confidence=0.0, confirms_fault="yes",
                     claimed_steps=[RESET_STEP], was_reset_earlier_this_trip="yes")],
        ["diff_completed_steps"] * 2))
    t1 = cp.turn(d, "DJ tripped, QLM is locked.")
    t2 = cp.turn(d, "Yes, I reset it once already this trip.", last_assistant=t1.reply)
    assert t2.terminal.kind == "refuse" and REASON_SECOND_RESET in t2.terminal.reasons


def test_no_news_turn_after_confirm_is_a_repeat_not_a_replay():
    """'anything else to do?' / 'ok' after a confirm: empty update, same terminal → the reply
    is rendered from the engine's 'nothing further' sentence (repeat=True), never the
    confirmation replayed verbatim; the (f)(ii) forward rule rides along. A later
    re-presentation is news and refuses."""
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=QLM, fault_confidence=0.9, fault_presenting="yes",
                     claimed_steps=[*ORDINARY, RESET_STEP], abnormality_found="no",
                     was_reset_earlier_this_trip="no", fault_resolved="yes"),
         _blind(), _blind(), _blind(fault_presenting="yes")],
        ["diff_completed_steps"] * 4))
    t1 = cp.turn(d, "qlm dropped once. i checked and reset. now working fine. anything to keep notice of?")
    assert t1.terminal.kind == "confirm" and not t1.repeat
    assert "Reset QLM once and" not in t1.reply and "DO NOT reset" in t1.reply and "10 minutes" in t1.reply
    t2 = cp.turn(d, "anything else to do?", last_assistant=t1.reply)
    assert t2.terminal.kind == "confirm" and t2.repeat and t2.reply.startswith("Nothing further")
    t3 = cp.turn(d, "ok", last_assistant=t2.reply)
    assert t3.repeat
    t4 = cp.turn(d, "QLM dropped again", last_assistant=t3.reply)
    assert t4.terminal.kind == "refuse" and not t4.repeat and REASON_RECURRED in t4.terminal.reasons


def test_no_news_turn_on_a_pending_caution_restates_it():
    d = DiagnosisState()
    cp = Copilot(_prov(
        [ParseOutput(fault_guess=QLM, fault_confidence=0.9, claimed_steps=list(ORDINARY),
                     abnormality_found="no", was_reset_earlier_this_trip="no"), _blind()],
        ["diff_completed_steps"] * 2))
    t1 = cp.turn(d, "QLM locked, first time. all checks normal")
    assert t1.terminal.kind == "caution"
    t2 = cp.turn(d, "ok?", last_assistant=t1.reply)
    assert t2.terminal.kind == "caution" and t2.repeat and "Nothing further" not in t2.reply
