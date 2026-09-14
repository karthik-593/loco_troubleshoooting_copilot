"""Swappable LLM providers (BUILD_PLAN §3: "keep the LLM behind an interface").

Two operations only — that is the entire surface the three LLM jobs need:

* ``structured(system, user, schema)`` → a validated pydantic instance (parse, decide);
* ``text(system, user)``               → a short string (phrase).

Providers
* ``DeepSeekProvider``  — parse + agent_decide: ``deepseek-flash``, temperature 0,
  thinking mode explicitly DISABLED on every call (it is on by default for this model).
* ``AnthropicProvider`` — phrase: ``claude-haiku-4-5-20251001``, temperature 0.5.
Models are pinned constants passed explicitly on every request (never an SDK default).
* ``FakeProvider``      — scripted stand-in for tests / offline eval.

``providers_from_env()`` is the ONLY place jobs are bound to vendors; parse.py, decide.py
and phrase.py take a provider argument and never name one.

Credentials: read here and only here, from the environment variables named below, and
handed straight to the client constructor. They are never logged, printed, or stored.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

ANTHROPIC_KEY_VAR = "ANTHROPIC_AGENTIC_AI_PROJECT_KEY"
DEEPSEEK_KEY_VAR = "DEEPSEEK_AGENTIC_AI_PROJECT_KEY"

# Explicit model pins (user decision 2026-09-15). Passed on EVERY call; never an SDK default,
# never a "latest" alias. Do not substitute a stronger model without asking.
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"   # phrase
DEEPSEEK_MODEL = "deepseek-flash"               # parse + agent_decide (DeepSeek-V4.1-Flash)
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
REQUEST_TIMEOUT_S = 60.0
MAX_RETRIES = 1


class LLMProvider(Protocol):
    def structured(self, system: str, user: str, schema: type[T]) -> T: ...
    def text(self, system: str, user: str) -> str: ...


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------

class MissingCredential(RuntimeError):
    pass


def _secret(var: str) -> str:
    """Return the credential for the client constructor. Checks the process environment,
    then (Windows) the User-scope environment in the registry, so a key set after the
    parent process started is still found. The value is returned to the caller only."""
    v = os.environ.get(var)
    if not v and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v, _ = winreg.QueryValueEx(k, var)
        except OSError:
            v = None
    if not v:
        raise MissingCredential(f"{var} is not set")
    return v


def credential_present(var: str) -> bool:
    """Boolean presence check only — never exposes the value."""
    try:
        _secret(var)
        return True
    except MissingCredential:
        return False


# ---------------------------------------------------------------------------
# Anthropic — phrase
# ---------------------------------------------------------------------------

class AnthropicProvider:
    """Pinned to ``ANTHROPIC_MODEL`` (Haiku 4.5). No ``thinking`` / ``output_config.effort``
    — neither applies to this model, and phrase needs neither."""

    def __init__(self, model: str = ANTHROPIC_MODEL, temperature: float = 0.5,
                 max_tokens: int = 1024, client: Any = None):
        import anthropic  # lazy: the engine never depends on it
        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic(
            api_key=_secret(ANTHROPIC_KEY_VAR), timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        # messages.create + explicit json_schema format (rather than messages.parse) so the
        # stop_reason is checked BEFORE any validation of the body.
        resp = self.client.messages.create(
            model=self.model,                      # explicit on every call
            max_tokens=self.max_tokens,
            extra_body={"temperature": 0.0},       # structured → deterministic (sampling params
                                                   # are wire-valid on Haiku 4.5; SDK 1.x untyped)
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={
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
            model=self.model,                      # explicit on every call
            max_tokens=self.max_tokens,
            extra_body={"temperature": self.temperature},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused the request")
        return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# DeepSeek — parse + agent_decide
# ---------------------------------------------------------------------------

# DeepSeek thinking mode is ON by default (effort high). parse / agent_decide are constrained
# extraction + tool-selection tasks: thinking adds latency and billed tokens for no benefit
# and makes tool choice less deterministic, so it is disabled explicitly on EVERY call.
DEEPSEEK_NO_THINKING = {"thinking": {"type": "disabled"}}


class DeepSeekProvider:
    """DeepSeek's OpenAI-compatible chat API, non-thinking mode. Structured output uses
    JSON mode (``response_format={"type": "json_object"}``) with the pydantic JSON schema
    embedded in the system prompt; the reply is validated by the same pydantic model the
    Anthropic path uses, so the engine sees identical objects whichever vendor produced them."""

    def __init__(self, model: str = DEEPSEEK_MODEL, max_tokens: int = 2048,
                 temperature: float = 0.0, client: Any = None):
        from openai import OpenAI  # lazy
        self.client = client or OpenAI(api_key=_secret(DEEPSEEK_KEY_VAR), base_url=DEEPSEEK_BASE_URL,
                                       timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    @staticmethod
    def _schema_block(schema: type[BaseModel]) -> str:
        return ("\n\nRespond with a single JSON object and nothing else. It must conform to this "
                "JSON schema (all properties optional unless listed as required; use null for "
                "unknown optionals):\n" + json.dumps(schema.model_json_schema(), indent=None))

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        resp = self.client.chat.completions.create(
            model=self.model,                      # explicit on every call
            max_tokens=self.max_tokens,
            temperature=self.temperature,          # 0.0: consistent across eval runs
            response_format={"type": "json_object"},
            extra_body=dict(DEEPSEEK_NO_THINKING),  # non-thinking mode, sent explicitly
            messages=[
                {"role": "system", "content": system + self._schema_block(schema)},
                {"role": "user", "content": user},
            ],
        )
        choice = resp.choices[0]
        if choice.finish_reason not in (None, "stop"):
            raise RuntimeError(f"unexpected finish_reason={choice.finish_reason}")
        if getattr(choice.message, "reasoning_content", None):
            # Fail loudly rather than silently paying for thinking we asked to be off.
            raise RuntimeError("reasoning_content present: thinking mode was not disabled")
        content = choice.message.content or ""
        return schema.model_validate_json(content)

    def text(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,                      # explicit on every call
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            extra_body=dict(DEEPSEEK_NO_THINKING),  # non-thinking mode, sent explicitly
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return (resp.choices[0].message.content or "").strip()


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


# ---------------------------------------------------------------------------
# job → provider binding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Providers:
    parse: LLMProvider
    decide: LLMProvider
    phrase: LLMProvider


def providers_from_env() -> Providers:
    """The one place vendors are chosen: DeepSeek for parse/decide, Claude for phrase."""
    deepseek = DeepSeekProvider()
    return Providers(parse=deepseek, decide=deepseek, phrase=AnthropicProvider())


def fake_providers(fake: FakeProvider | None = None) -> Providers:
    f = fake or FakeProvider()
    return Providers(parse=f, decide=f, phrase=f)
