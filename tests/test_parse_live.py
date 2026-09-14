"""LIVE parse test against the real model over the held-out set (M2 DoD).

Skipped unless credentials are available (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / an
``ant auth login`` profile). Run explicitly with::

    pytest tests/test_parse_live.py -m live -s
"""
import os
from pathlib import Path

import pytest
import yaml

from engine.state import DiagnosisState
from llm.parse import CLARIFY_THRESHOLD, parse_turn

DATA = Path(__file__).with_name("data") / "parse_heldout.yaml"
pytestmark = pytest.mark.live


def _has_credentials() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


@pytest.fixture(scope="module")
def provider():
    if not _has_credentials():
        pytest.skip("no Anthropic credentials — live parse test skipped")
    from llm.interface import AnthropicProvider
    return AnthropicProvider()


def _cases():
    with open(DATA, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["text"][:40])
def test_heldout_parse(case, kb, provider):
    r = parse_turn(case["text"], DiagnosisState(), kb, provider)
    raw = r.raw
    print(f"\n{case['text']!r}\n  -> {raw.model_dump()}")

    if "fault_confidence_below" in case:
        assert r.needs_clarification
        assert r.confidence < case["fault_confidence_below"]
        return
    if case.get("fault") is None and "fault" in case:
        assert r.update is None or r.update.fault_id is None
        return

    assert r.update is not None, "expected an actionable update"
    assert r.update.fault_id == case["fault"]
    if "claimed" in case:
        assert set(r.update.claimed_steps) - set(r.rejected_steps) == set(case["claimed"])
    if "abnormality" in case:
        assert r.update.history.get("abnormality_found") == case["abnormality"]
    if "reset_earlier" in case:
        assert r.update.history.get("was_QLM_reset_earlier_this_trip") == case["reset_earlier"]
    if "other_relays" in case:
        assert r.update.history.get("other_relays_acted") == case["other_relays"]
    if "intended_action" in case:
        assert r.update.intended_action == case["intended_action"]
    if "unmapped_min" in case:
        assert len(r.rejected_steps) >= case["unmapped_min"]
    # never a step outside the KB
    known = set(kb.get(case["fault"]).step_ids)
    accepted = set(r.update.claimed_steps) - set(r.rejected_steps)
    assert accepted <= known
