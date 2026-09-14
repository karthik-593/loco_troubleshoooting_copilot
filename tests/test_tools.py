"""Class-A tools: deterministic, idempotent, KB-backed; the reflex is NOT among them."""
import pytest

from engine.state import StateUpdate, update_state
from engine.tools import HF_OTHER_RELAYS, REGISTRY, TOOL_NAMES, is_registered, run_tool
from tests.conftest import ORDINARY


def test_reflex_is_not_a_selectable_tool():
    """§5.1 / §7: the safety reflex must never appear in the discretionary registry."""
    for name in TOOL_NAMES:
        assert "gate" not in name and "safety" not in name
    assert not is_registered("evaluate_gates")
    assert not is_registered("check_safety_gate")


def test_unregistered_tool_is_rejected_not_attempted(qlm_state, qlm):
    with pytest.raises(KeyError):
        run_tool("check_safety_gate", qlm_state, qlm)


def test_diff_tool_and_idempotency(qlm_state, qlm):
    update_state(qlm_state, StateUpdate(claimed_steps=ORDINARY[:2]), qlm)
    r1 = run_tool("diff_completed_steps", qlm_state, qlm)
    assert not r1.cached and r1.result["next_unmet"] == ORDINARY[2]
    assert qlm_state.stuck_at == ORDINARY[2]
    r2 = run_tool("diff_completed_steps", qlm_state, qlm)      # state unchanged → cached
    assert r2.cached and r2.result == r1.result
    update_state(qlm_state, StateUpdate(claimed_steps=(ORDINARY[2],)), qlm)
    r3 = run_tool("diff_completed_steps", qlm_state, qlm)      # state changed → recomputed
    assert not r3.cached and r3.result["complete"]


def test_check_combination_routes_on_reported_relays(qlm_state, qlm):
    assert run_tool("check_combination", qlm_state, qlm).result["applies"] is False
    update_state(qlm_state, StateUpdate(history={HF_OTHER_RELAYS: ["qop-1"]}), qlm)
    r = run_tool("check_combination", qlm_state, qlm).result
    assert r["applies"] and r["route_to"] == "QLM_with_QOP_QRSI" and r["matched_relays"] == ["QOP-1"]
    assert "§6.1.2" in r["source"]
    update_state(qlm_state, StateUpdate(history={HF_OTHER_RELAYS: ["QLA"]}), qlm)
    assert run_tool("check_combination", qlm_state, qlm).result["route_to"] == "QLM_with_QLA_QOA"


def test_required_observations(qlm_state, qlm):
    r = run_tool("get_required_observations", qlm_state, qlm).result
    assert not r["already_stated"]
    assert set(r["relays_to_observe"]) == {"QOP-1", "QOP-2", "QRSI-1", "QRSI-2", "QLA", "QOA"}
    update_state(qlm_state, StateUpdate(history={HF_OTHER_RELAYS: []}), qlm)
    r = run_tool("get_required_observations", qlm_state, qlm).result
    assert r["already_stated"] and r["relays_to_observe"] == []


def test_resolve_config_not_needed_for_qlm(qlm_state, qlm):
    assert run_tool("resolve_config", qlm_state, qlm).result["needed"] is False


def test_lookup_procedure_cites_sources(qlm_state, qlm):
    r = run_tool("lookup_procedure", qlm_state, qlm).result
    assert [s["id"] for s in r["steps"]] == list(qlm.step_ids)
    assert all(s["source"] for s in r["steps"])


def test_every_tool_has_description():
    assert all(t.description for t in REGISTRY.values())
