"""Session store: one DiagnosisState + transcript per session_id, in memory.

The API is stateless per request (BUILD_PLAN §3: ``{session_id, pilot_turn}``); this is
the only place per-pilot conversation state lives. Swappable for Redis etc. later.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from agent.graph import Copilot, TurnResult
from engine.state import DiagnosisState


@dataclass
class Session:
    session_id: str
    diag: DiagnosisState = field(default_factory=DiagnosisState)
    transcript: list[dict] = field(default_factory=list)   # [{role, text, ...trace}]
    last_assistant: Optional[str] = None


class SessionStore:
    def __init__(self, copilot: Copilot):
        self.copilot = copilot
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> Session:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Session(session_id)
            return self._sessions[session_id]

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def diagnose(self, session_id: str, pilot_turn: str) -> TurnResult:
        sess = self.get_or_create(session_id)
        with self._lock:                                  # one turn at a time per store (demo scale)
            result = self.copilot.turn(sess.diag, pilot_turn, last_assistant=sess.last_assistant)
            sess.last_assistant = result.reply
            sess.transcript.append({"role": "pilot", "text": pilot_turn})
            sess.transcript.append({
                "role": "assistant", "text": result.reply,
                "terminal": result.terminal.kind, "tool_path": list(result.tool_path),
                "stop_reason": result.stop_reason, "reflex_runs": result.reflex_runs,
                "source": result.terminal.source, "phrase_fallback": result.phrase_fallback,
            })
        return result
