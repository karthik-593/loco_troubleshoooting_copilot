"""Swappable LLM provider interface (BUILD_PLAN §3: "keep the LLM behind an interface").

Two operations only — that is the entire surface the three LLM jobs need:

* ``structured(system, user, schema)`` → a validated pydantic instance (parse, decide);
* ``text(system, user)``               → a short string (phrase).

``AnthropicProvider`` is the real one. ``FakeProvider`` is a scripted stand-in for tests
and offline eval so nothing in ``llm/`` needs credentials to be exercised.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5"


class LLMProvider(Protocol):
    def structured(self, system: str, user: str, schema: type[T]) -> T: ...
    def text(self, system: str, user: str) -> str: ...


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

class AnthropicProvider:
    """Claude via the official SDK. Credentials resolve from the environment
    (ANTHROPIC_API_KEY or an ``ant auth login`` profile) — nothing is hard-coded."""

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "medium",
                 max_tokens: int = 4096, client: Any = None):
        import anthropic  # imported lazily so the engine never depends on it
        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        # messages.create + explicit json_schema format (rather than messages.parse) so the
        # stop_reason is checked BEFORE any validation of the body.
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": self._anthropic.transform_schema(schema)},
            },
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused the request")
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"unexpected stop_reason={resp.stop_reason}")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return schema.model_validate_json(text)

    def text(self, system: str, user: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused the request")
        return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# Fake (tests / offline)
# ---------------------------------------------------------------------------

@dataclass
class FakeProvider:
    """Scripted provider. Queue responses per schema (for ``structured``) or as strings
    (for ``text``); every call is recorded so tests can assert what the LLM was shown."""
    structured_queue: list[BaseModel] = field(default_factory=list)
    text_queue: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        self.calls.append({"op": "structured", "system": system, "user": user, "schema": schema.__name__})
        if not self.structured_queue:
            raise AssertionError(f"FakeProvider: no queued response for {schema.__name__}")
        out = self.structured_queue.pop(0)
        if not isinstance(out, schema):
            raise AssertionError(f"FakeProvider: queued {type(out).__name__}, asked for {schema.__name__}")
        return out

    def text(self, system: str, user: str) -> str:
        self.calls.append({"op": "text", "system": system, "user": user})
        if not self.text_queue:
            raise AssertionError("FakeProvider: no queued text response")
        return self.text_queue.pop(0)
