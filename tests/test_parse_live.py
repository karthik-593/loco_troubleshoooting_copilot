"""LIVE parse test against the real parse model (DeepSeek) over the held-out set (M2 DoD).

Skipped unless the credential is present. Only structured model output is printed —
never anything about credentials. Run explicitly with::

    pytest tests/test_parse_live.py -m live -s
"""
from pathlib import Path

import pytest
import yaml

from engine.state import DiagnosisState
from llm.interface import DEEPSEEK_KEY_VAR, credential_present, providers_from_env
from llm.parse import parse_turn

DATA = Path(__file__).with_name("data") / "parse_heldout.yaml"
pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def provider():
    if not credential_present(DEEPSEEK_KEY_VAR):
        pytest.skip(f"{DEEPSEEK_KEY_VAR} not present — live parse test skipped")
    return providers_from_env().parse


def _cases():
    with open(DATA, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["text"][:40])
def test_heldout_parse(case, kb, provider):
    d = DiagnosisState()
    if case.get("state_fault"):                     # a mid-conversation line: fault already matched
        d.matched_fault = case["state_fault"]; d.fault_confirmed = True
    r = parse_turn(case["text"], d, kb, provider, last_assistant=case.get("last_assistant"))
    raw = r.raw
    print(f"\n{case['text']!r}\n  -> {raw.model_dump()}")

    if case.get("out_of_scope"):
        assert r.out_of_scope and r.update is None, "expected a §5.6 out-of-scope defer"
        return
    if case.get("never_acts"):
        # borderline scope call left to the model; the invariant is: no update, and the
        # deterministic backstop defers on the very next unresolved turn.
        assert r.update is None and (r.out_of_scope or r.needs_clarification)
        d = DiagnosisState(); d.clarify_asked = 1
        assert parse_turn(case["text"], d, kb, provider).out_of_scope
        return
    if "fault_confidence_below" in case:
        assert r.needs_clarification and not r.out_of_scope
        assert r.confidence < case["fault_confidence_below"]
        return
    if case.get("fault") is None and "fault" in case:
        assert r.update is None or r.update.fault_id is None
        return

    if case.get("allow_clarify_if_no_fault") and r.update is None:
        return                               # a context-free follow-up line may legitimately clarify
    assert r.update is not None, "expected an actionable update"
    if "fault_any_of" in case:
        assert r.update.fault_id in case["fault_any_of"]
        case = {**case, "fault": r.update.fault_id}
    else:
        assert (r.update.fault_id or d.matched_fault) == case["fault"]
    if "asks_for_detail" in case:
        assert r.update.wants_detail == case["asks_for_detail"]
    if "denies_asked_step" in case:
        assert r.update.denies_asked_step == case["denies_asked_step"]
    if "claimed" in case:
        assert set(r.update.claimed_steps) - set(r.rejected_steps) == set(case["claimed"])
    if "claimed_min" in case:                    # long procedures: how many of the stated steps
        assert len(set(r.update.claimed_steps) - set(r.rejected_steps)) >= case["claimed_min"]
    if "abnormality" in case:
        assert r.update.history.get("abnormality_found") == case["abnormality"]
    if "reset_earlier" in case:
        from engine.state import reset_history_key
        assert r.update.history.get(reset_history_key(kb.get(case["fault"]))) == case["reset_earlier"]
    if "loco_rb" in case:
        assert r.update.loco_rb == case["loco_rb"]
    if "reset_earlier_not" in case:
        assert r.update.history.get("was_QLM_reset_earlier_this_trip") != case["reset_earlier_not"]
    if "other_relays" in case:
        assert r.update.history.get("other_relays_acted") == case["other_relays"]
    if "intended_action" in case:
        assert r.update.intended_action == case["intended_action"]
    if "resolved" in case:
        assert r.update.history.get("fault_resolved") == case["resolved"]
    for k, v in case.get("facts", {}).items():
        assert r.update.history.get(k) == v, k
    if "unmapped_min" in case:
        assert len(r.rejected_steps) >= case["unmapped_min"]
    # never a step outside the KB
    from llm.parse import same_family
    known = {sid for fid in kb.fault_ids if same_family(kb, fid, case["fault"]) for sid in kb.get(fid).step_ids}
    accepted = set(r.update.claimed_steps) - set(r.rejected_steps)
    assert accepted <= known                     # the fault's family (route / combination targets)
