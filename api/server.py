"""FastAPI server — ``POST /diagnose {session_id, pilot_turn}`` (BUILD_PLAN §3).

Run:  uvicorn api.server:app --reload
Tests inject a ``Copilot`` built on scripted providers via ``create_app(copilot)``; the
module-level ``app`` binds the real providers from the environment lazily.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.graph import Copilot
from agent.session import SessionStore
from engine.gates import evaluate_gates
from llm.interface import providers_from_env

DISCLAIMER = ("Demonstrator only — not certified for operational use. Follow the printed "
              "troubleshooting directory and TLC instructions.")


class DiagnoseRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    pilot_turn: str = Field(min_length=1, max_length=2000)


class DiagnoseResponse(BaseModel):
    session_id: str
    reply: str
    terminal: str
    source: str
    tool_path: list[str]
    stop_reason: str
    reflex_runs: int
    phrase_fallback: bool
    gate_type: Optional[str] = None
    step_id: Optional[str] = None
    disclaimer: str = DISCLAIMER


class StateResponse(BaseModel):
    session_id: str
    matched_fault: Optional[str]
    fault_confirmed: bool
    steps_claimed_done: list[str]
    history_facts: dict
    intended_action: Optional[str]
    stuck_at: Optional[str]
    reflex_verdict: str
    transcript: list[dict]


def build_store(copilot: Optional[Copilot] = None) -> SessionStore:
    return SessionStore(copilot or Copilot(providers_from_env()))


def create_app(copilot: Optional[Copilot] = None) -> FastAPI:
    app = FastAPI(title="Loco Troubleshooting Verification Copilot", version="0.3.0")
    app.state.store = build_store(copilot)

    @app.get("/health")
    def health():
        return {"ok": True, "faults": app.state.store.copilot.kb.fault_ids, "disclaimer": DISCLAIMER}

    @app.post("/diagnose", response_model=DiagnoseResponse)
    def diagnose(req: DiagnoseRequest):
        store: SessionStore = app.state.store
        try:
            r = store.diagnose(req.session_id, req.pilot_turn)
        except Exception as exc:             # provider outage etc. → 503, never a made-up reply
            raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc
        return DiagnoseResponse(
            session_id=req.session_id, reply=r.reply, terminal=r.terminal.kind,
            source=r.terminal.source, tool_path=list(r.tool_path), stop_reason=r.stop_reason,
            reflex_runs=r.reflex_runs, phrase_fallback=r.phrase_fallback,
            gate_type=r.terminal.gate_type, step_id=r.terminal.step_id,
        )

    @app.get("/session/{session_id}", response_model=StateResponse)
    def session_state(session_id: str):
        store: SessionStore = app.state.store
        sess = store.get_or_create(session_id)
        d = sess.diag
        fault = store.copilot.kb.get(d.matched_fault) if d.matched_fault in store.copilot.kb.fault_ids else None
        return StateResponse(
            session_id=session_id, matched_fault=d.matched_fault, fault_confirmed=d.fault_confirmed,
            steps_claimed_done=sorted(d.steps_claimed_done), history_facts=d.history_facts,
            intended_action=d.intended_action, stuck_at=d.stuck_at,
            reflex_verdict=evaluate_gates(d, fault).outcome.value, transcript=sess.transcript,
        )

    @app.delete("/session/{session_id}")
    def reset(session_id: str):
        app.state.store.reset(session_id)
        return {"ok": True}

    return app


def __getattr__(name):
    # ``uvicorn api.server:app`` builds the app lazily so importing this module (tests, tooling)
    # never constructs real LLM clients or touches credentials.
    if name == "app":
        return create_app()
    raise AttributeError(name)
