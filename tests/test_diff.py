from engine.diff import diff_steps
from engine.state import StateUpdate, update_state
from tests.conftest import ORDINARY, RESET_STEP


def test_nothing_claimed(qlm_state, qlm):
    d = diff_steps(qlm_state, qlm)
    assert d.next_unmet == ORDINARY[0]
    assert d.missing == ORDINARY
    assert not d.complete


def test_next_unmet_follows_tsd_order_not_claim_order(qlm_state, qlm):
    """Claim (c) and (a) → the specific miss is (b), regardless of claim order."""
    update_state(qlm_state, StateUpdate(claimed_steps=(ORDINARY[2], ORDINARY[0])), qlm)
    d = diff_steps(qlm_state, qlm)
    assert d.next_unmet == ORDINARY[1]
    assert d.missing == (ORDINARY[1],)


def test_trace_turn1_miss_is_arc_chutes(qlm_state, qlm):
    """BUILD_PLAN §10.2 Turn 1: transformer + oil claimed → next_unmet = arc chutes."""
    update_state(qlm_state, StateUpdate(claimed_steps=ORDINARY[:2]), qlm)
    assert diff_steps(qlm_state, qlm).next_unmet == "check_arc_chutes_and_terminals"


def test_complete_ignores_gated_step(qlm_state, qlm):
    """The diff runs over ordinary steps only; the gated reset step is the reflex's."""
    update_state(qlm_state, StateUpdate(claimed_steps=ORDINARY), qlm)
    d = diff_steps(qlm_state, qlm)
    assert d.complete and d.next_unmet is None
    assert RESET_STEP not in d.missing


def test_unrecognised_claim_is_surfaced_not_accepted(qlm_state, qlm):
    """BUILD_PLAN §8: a claimed step must exist in the checklist; otherwise it is
    reported back, never silently counted as done."""
    update_state(qlm_state, StateUpdate(claimed_steps=("check_pantograph", ORDINARY[0])), qlm)
    d = diff_steps(qlm_state, qlm)
    assert d.unrecognised == ("check_pantograph",)
    assert "check_pantograph" not in qlm_state.steps_claimed_done
    assert d.next_unmet == ORDINARY[1]


def test_claims_accumulate_across_updates(qlm_state, qlm):
    update_state(qlm_state, StateUpdate(claimed_steps=ORDINARY[:1]), qlm)
    update_state(qlm_state, StateUpdate(claimed_steps=ORDINARY[1:2]), qlm)
    assert diff_steps(qlm_state, qlm).next_unmet == ORDINARY[2]
