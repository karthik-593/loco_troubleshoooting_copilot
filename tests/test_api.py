"""FastAPI /diagnose over the graph with scripted LLMs. Importing the server module must not
construct real LLM clients."""
import importlib

from fastapi.testclient import TestClient

from agent.graph import REFLEX_SHORT_CIRCUIT, Copilot
from api.server import create_app
from llm.interface import FakeProvider, Providers
from llm.schemas import DecideOutput, ParseOutput
from tests.conftest import ORDINARY


def _client():
    prov = Providers(
        parse=FakeProvider(structured_queue=[
            ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=list(ORDINARY[:2])),
            ParseOutput(fault_guess=None, fault_confidence=0.0, claimed_steps=[ORDINARY[2]],
                        abnormality_found="no", was_reset_earlier_this_trip="yes", intended_action="reset_QLM"),
        ]),
        decide=FakeProvider(structured_queue=[DecideOutput(tool="diff_completed_steps", reason="t")]),
        phrase=FakeProvider(),
    )
    return TestClient(create_app(Copilot(prov)))


def test_import_does_not_build_clients():
    mod = importlib.import_module("api.server")
    assert "app" not in vars(mod)              # lazy via module __getattr__


def test_health():
    c = _client()
    r = c.get("/health")
    assert r.status_code == 200 and "QLM_dropped" in r.json()["faults"]
    assert "not certified" in r.json()["disclaimer"]


def test_diagnose_two_turns_and_session_state():
    c = _client()
    r1 = c.post("/diagnose", json={"session_id": "s1", "pilot_turn": "QLM locked. checked transformer and oil"})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["terminal"] == "ask_step" and b1["step_id"] == ORDINARY[2]
    assert b1["tool_path"] == ["diff_completed_steps"] and b1["reflex_runs"] == 2
    assert "§6.1.1(c)" in b1["source"] and b1["phrase_fallback"] is True

    r2 = c.post("/diagnose", json={"session_id": "s1", "pilot_turn": "all normal, reset once already, resetting"})
    b2 = r2.json()
    assert b2["terminal"] == "refuse" and b2["gate_type"] == "reset_limit"
    assert b2["tool_path"] == [REFLEX_SHORT_CIRCUIT] and b2["stop_reason"] == "gate"
    assert "DO NOT reset" in b2["reply"]

    s = c.get("/session/s1").json()
    assert s["matched_fault"] == "QLM_dropped" and s["reflex_verdict"] == "REFUSE"
    assert set(s["steps_claimed_done"]) == set(ORDINARY)
    assert [m["role"] for m in s["transcript"]] == ["pilot", "assistant", "pilot", "assistant"]

    assert c.delete("/session/s1").json() == {"ok": True}
    assert c.get("/session/s1").json()["matched_fault"] is None


def test_validation():
    c = _client()
    assert c.post("/diagnose", json={"session_id": "", "pilot_turn": "x"}).status_code == 422
    assert c.post("/diagnose", json={"session_id": "s", "pilot_turn": ""}).status_code == 422
