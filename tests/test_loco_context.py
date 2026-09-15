"""Session loco context ("session bar"): loco_number + type + config per loco, single or
leading+trailing with swap; faults consult ONLY the axes they declare."""
import copy

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent.graph import Copilot
from api.server import create_app
from engine.diff import diff_steps
from engine.run_turn import run_turn
from engine.state import DiagnosisState, LocoInfo, StateUpdate, update_state
from engine.tools import run_tool
from kb.schema import FAULTS_DIR, Fault
from llm.interface import FakeProvider, Providers
from llm.schemas import DecideOutput, ParseOutput
from tests.conftest import ORDINARY, QLM


# ---------------------------------------------------------------------------
# state model
# ---------------------------------------------------------------------------

def test_every_loco_carries_all_three_fields_single_and_multi():
    d = DiagnosisState()
    assert d.loco.as_dict() == {"loco_number": "", "type": "unknown", "config": "unknown"}
    d.set_locos([LocoInfo("27312", "wag7", "siv"), LocoInfo("22451", "wag5", "arno")], active=1)
    assert [l.as_dict() for l in d.locos] == [
        {"loco_number": "27312", "type": "wag7", "config": "siv"},
        {"loco_number": "22451", "type": "wag5", "config": "arno"}]
    assert d.loco.loco_number == "22451" and d.config == "arno"     # §10.1 config mirrors the active loco


def test_swap_leading_trailing_keeps_attribution_on_the_same_loco():
    d = DiagnosisState()
    d.set_locos([LocoInfo("A", "wag7", "siv"), LocoInfo("B", "wap4", "arno")], active=0)
    d.swap_locos()
    assert [l.loco_number for l in d.locos] == ["B", "A"]
    assert d.loco.loco_number == "A" and d.active_loco == 1 and d.config == "siv"
    d2 = DiagnosisState(); d2.set_locos([LocoInfo("solo")]); d2.swap_locos()          # single: no-op
    assert d2.loco.loco_number == "solo"


def test_state_update_from_free_text_writes_the_active_loco(kb):
    d = DiagnosisState()
    update_state(d, StateUpdate(fault_id=QLM, config="siv", loco_type="wag7"), kb.get(QLM))
    assert d.loco.config == "siv" and d.loco.type == "wag7" and d.config == "siv"


# ---------------------------------------------------------------------------
# axis-scoped consultation
# ---------------------------------------------------------------------------

def test_current_yamls_declare_no_type_dependency(kb):
    for fid in kb.fault_ids:
        f = kb.get(fid)
        assert f.type_dependency == "none" and f.type_axes == () and not f.depends_on_type
        assert f.config_dependency == "none"                     # none of the encoded faults branch on config yet


def test_axis_value_is_none_unless_the_fault_declares_the_dependency(kb):
    d = DiagnosisState(); d.set_locos([LocoInfo("1", "wag7", "siv")])
    qlm = kb.get(QLM)                                              # declares neither axis
    assert d.axis_value("loco_config", qlm) is None and d.axis_value("loco_type", qlm) is None
    r = run_tool("resolve_config", d, qlm).result
    assert r["axes"] == {} and r["needed"] is False                # not consulted, even though known


def _synthetic(kb, **over):
    """A copy of QLM that branches on the loco axes (no such fault is in the TSD yet)."""
    with open(FAULTS_DIR / "qlm_dropped.yaml", encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    d["fault_id"] = "SYN_branching"; d["aliases"] = ["synthetic branching fault"]
    d.update(over)
    return Fault.model_validate(d)


def test_schema_rejects_branching_on_an_undeclared_axis(kb):
    with open(FAULTS_DIR / "qlm_dropped.yaml", encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    d["steps"][1]["applies_when"] = {"loco_config": "siv"}          # branches on config …
    with pytest.raises(ValidationError, match="config_dependency is none"):
        Fault.model_validate(d)                                    # … without declaring it
    d["steps"][1]["applies_when"] = {"loco_type": "wag7"}
    with pytest.raises(ValidationError, match="type_dependency is none"):
        Fault.model_validate(d)
    d["type_dependency"] = ["wag7", "wag5"]
    f = Fault.model_validate(d)
    assert f.type_axes == ("wag7", "wag5") and f.depends_on_type


def test_config_branch_asks_only_when_reached_and_only_if_unknown(kb):
    with open(FAULTS_DIR / "qlm_dropped.yaml", encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    d["fault_id"] = "SYN_cfg"; d["aliases"] = ["syn cfg"]; d["config_dependency"] = "branch-specific"
    d["steps"][1]["applies_when"] = {"loco_config": "siv"}          # oil check only on SIV locos
    f = Fault.model_validate(d)

    # branch not yet reached (step (a) still due) → no config question
    s = DiagnosisState(); s.matched_fault = f.fault_id; s.steps_required = f.step_ids
    assert diff_steps(s, f).needs_axis is None and diff_steps(s, f).next_unmet == ORDINARY[0]
    # branch reached, config unknown → the engine asks for the config (§2.4)
    s.steps_claimed_done.add(ORDINARY[0])
    assert diff_steps(s, f).needs_axis == "loco_config"
    # config known: SIV → the branch applies; ARNO → it is skipped
    s.set_locos([LocoInfo("1", "unknown", "siv")]);  assert diff_steps(s, f).next_unmet == ORDINARY[1]
    s.set_locos([LocoInfo("1", "unknown", "arno")]); assert diff_steps(s, f).next_unmet == ORDINARY[2]
    # the type axis is never consulted by this fault even though only config is declared
    s.set_locos([LocoInfo("1", "wag7", "arno")]);    assert s.axis_value("loco_type", f) is None


def test_multi_loco_attribution_uses_the_active_row(kb):
    with open(FAULTS_DIR / "qlm_dropped.yaml", encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    d["fault_id"] = "SYN_cfg2"; d["aliases"] = ["syn cfg2"]; d["config_dependency"] = "branch-specific"
    d["steps"][1]["applies_when"] = {"loco_config": "siv"}
    f = Fault.model_validate(d)
    s = DiagnosisState(); s.matched_fault = f.fault_id; s.steps_required = f.step_ids
    s.steps_claimed_done.add(ORDINARY[0])
    s.set_locos([LocoInfo("lead", "wag7", "siv"), LocoInfo("trail", "wag5", "arno")], active=1)
    assert diff_steps(s, f).next_unmet == ORDINARY[2]              # trailing (ARNO) → skip the SIV-only step
    s.swap_locos()                                                  # attribution follows the row
    assert diff_steps(s, f).next_unmet == ORDINARY[2]
    s.active_loco = 1 - s.active_loco; s.config = s.loco.config     # re-attribute to the SIV loco
    assert diff_steps(s, f).next_unmet == ORDINARY[1]


# ---------------------------------------------------------------------------
# API session bar
# ---------------------------------------------------------------------------

def _client():
    prov = Providers(parse=FakeProvider(structured_queue=[ParseOutput(fault_guess=None, fault_confidence=0.0,
                                                                      claimed_steps=list(ORDINARY[:2]))]),
                     decide=FakeProvider(structured_queue=[DecideOutput(tool="diff_completed_steps", reason="t")]),
                     phrase=FakeProvider())
    return TestClient(create_app(Copilot(prov)))


def test_api_session_bar_single_multi_swap_and_persistence():
    c = _client()
    r = c.put("/session/s/locos", json={"locos": [{"loco_number": "27312", "type": "wag7", "config": "siv"}]})
    assert r.status_code == 200 and r.json()["locos"][0]["config"] == "siv"
    assert c.post("/session/s/swap").status_code == 409                       # single: nothing to swap
    r = c.put("/session/s/locos", json={"locos": [
        {"loco_number": "27312", "type": "wag7", "config": "siv"},
        {"loco_number": "22451", "type": "wag5", "config": "arno"}], "active": 1})
    assert r.json()["active_loco"] == 1
    r = c.post("/session/s/swap").json()
    assert [l["loco_number"] for l in r["locos"]] == ["22451", "27312"] and r["active_loco"] == 0
    # locos survive a diagnose turn and are visible in the state
    c.post("/diagnose", json={"session_id": "s", "pilot_turn": "QLM locked, checked transformer and oil"})
    s = c.get("/session/s").json()
    assert s["locos"][0] == {"loco_number": "22451", "type": "wag5", "config": "arno"} and s["active_loco"] == 0
    assert s["matched_fault"] == QLM
    assert c.put("/session/s/locos", json={"locos": []}).status_code == 422
    assert c.put("/session/s/locos", json={"locos": [{"type": "wdg4"}]}).status_code == 422
