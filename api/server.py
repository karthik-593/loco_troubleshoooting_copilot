"""FastAPI server — ``POST /diagnose {session_id, pilot_turn}`` (BUILD_PLAN §3).

Run:  uvicorn api.server:app --reload
Tests inject a ``Copilot`` built on scripted providers via ``create_app(copilot)``; the
module-level ``app`` binds the real providers from the environment lazily.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.graph import Copilot
from agent.session import SessionStore
from engine.gates import evaluate_gates
from engine.state import LocoInfo
from llm.interface import providers_from_env

DISCLAIMER = ("Demonstrator only — not certified for operational use. Follow the printed "
              "troubleshooting directory and TLC instructions.")


class LocoModel(BaseModel):
    """One loco of the session bar. All three fields are always carried; a fault consults
    only the axes it declares (kb.schema.Fault.config_dependency / type_dependency)."""
    loco_number: str = Field(default="", max_length=32)
    type: Literal["wag7", "wag5", "wap4", "unknown"] = "unknown"
    config: Literal["siv", "arno", "unknown"] = "unknown"
    rb: Literal["fitted", "not_fitted", "unknown"] = "unknown"    # optional; asked lazily by the engine


class LocosRequest(BaseModel):
    locos: list[LocoModel] = Field(min_length=1, max_length=2)   # [leading] or [leading, trailing]
    active: int = Field(default=0, ge=0, le=1)                    # which loco the fault is on


class DiagnoseRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    pilot_turn: str = Field(min_length=1, max_length=2000)
    locos: Optional[LocosRequest] = None                          # optional: set the bar with the turn


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
    locos: list[LocoModel]
    active_loco: int
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

    @app.get("/")
    def root():
        """Browser landing: this is the API, not the UI (the chat client is Streamlit on :8501)."""
        return {"service": app.title, "ui": "streamlit run client/streamlit_app.py  (http://localhost:8501)",
                "docs": "/docs", "health": "/health", "diagnose": "POST /diagnose"}

    @app.get("/health")
    def health():
        return {"ok": True, "faults": app.state.store.copilot.kb.fault_ids, "disclaimer": DISCLAIMER}

    def _apply_locos(sess, body: LocosRequest):
        sess.diag.set_locos([LocoInfo(l.loco_number, l.type, l.config, l.rb) for l in body.locos], body.active)

    @app.put("/session/{session_id}/locos")
    def set_locos(session_id: str, body: LocosRequest):
        """Session bar: single [leading] or multi [leading, trailing]; every row carries
        loco_number, type and config."""
        sess = app.state.store.get_or_create(session_id)
        _apply_locos(sess, body)
        return {"ok": True, "locos": [l.as_dict() for l in sess.diag.locos], "active_loco": sess.diag.active_loco}

    @app.post("/session/{session_id}/swap")
    def swap_locos(session_id: str):
        """Multi only: swap leading and trailing (the attributed loco follows its row)."""
        sess = app.state.store.get_or_create(session_id)
        if len(sess.diag.locos) != 2:
            raise HTTPException(status_code=409, detail="swap needs a leading + trailing pair")
        sess.diag.swap_locos()
        return {"ok": True, "locos": [l.as_dict() for l in sess.diag.locos], "active_loco": sess.diag.active_loco}

    @app.post("/diagnose", response_model=DiagnoseResponse)
    def diagnose(req: DiagnoseRequest):
        store: SessionStore = app.state.store
        if req.locos is not None:
            _apply_locos(store.get_or_create(req.session_id), req.locos)
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
            session_id=session_id, locos=[LocoModel(**l.as_dict()) for l in d.locos], active_loco=d.active_loco,
            matched_fault=d.matched_fault, fault_confirmed=d.fault_confirmed,
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
