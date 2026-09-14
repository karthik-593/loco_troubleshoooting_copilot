"""AnthropicProvider request shape, exercised through the real SDK over a mock HTTP
transport — no credentials, no network. Guards against SDK / API drift."""
import json

import httpx2 as httpx
import pytest

from llm.interface import DEFAULT_MODEL, AnthropicProvider
from llm.schemas import DecideOutput, ParseOutput


def _client(handler):
    import anthropic
    from anthropic import DefaultHttpxClient
    return anthropic.Anthropic(api_key="test",
                               http_client=DefaultHttpxClient(transport=httpx.MockTransport(handler)))


def _message(body, text, stop_reason="end_turn"):
    return httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason,
        "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})


def test_structured_request_and_parse():
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen.update(body)
        return _message(body, json.dumps({"tool": "diff_completed_steps", "reason": "need next step"}))

    out = AnthropicProvider(client=_client(handler)).structured("sys", "user", DecideOutput)
    assert isinstance(out, DecideOutput) and out.tool == "diff_completed_steps"
    assert seen["model"] == DEFAULT_MODEL
    assert seen["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in json.dumps(seen)
    fmt = seen["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert seen["messages"] == [{"role": "user", "content": "user"}]


def test_text_request():
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen.update(body)
        return _message(body, "  Do not reset QLM again.  ")

    txt = AnthropicProvider(client=_client(handler)).text("sys", "user")
    assert txt == "Do not reset QLM again."
    assert "format" not in seen["output_config"]
    assert seen["output_config"]["effort"] == "low"


def test_refusal_raises_rather_than_returning_garbage():
    def handler(request):
        body = json.loads(request.content)
        return _message(body, "", stop_reason="refusal")

    with pytest.raises(RuntimeError, match="refused"):
        AnthropicProvider(client=_client(handler)).structured("sys", "user", ParseOutput)
