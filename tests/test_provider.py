"""Provider request shapes, exercised through the real SDKs over mock HTTP transports —
no credentials, no network. Guards against SDK / API drift. Nothing here ever reads or
prints a credential value."""
import json

import httpx2 as httpx
import pytest

from llm.interface import ANTHROPIC_MODEL, DEEPSEEK_MODEL, AnthropicProvider, DeepSeekProvider, MissingCredential, credential_present
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
    assert seen["model"] == ANTHROPIC_MODEL == "claude-haiku-4-5-20251001"   # explicit pin
    assert "thinking" not in seen and "effort" not in json.dumps(seen)
    assert seen["temperature"] == 0.0
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
    assert "output_config" not in seen
    assert seen["model"] == ANTHROPIC_MODEL
    assert 0.0 <= seen["temperature"] <= 1.0


def test_refusal_raises_rather_than_returning_garbage():
    def handler(request):
        body = json.loads(request.content)
        return _message(body, "", stop_reason="refusal")

    with pytest.raises(RuntimeError, match="refused"):
        AnthropicProvider(client=_client(handler)).structured("sys", "user", ParseOutput)


# ---------------------------------------------------------------------------
# DeepSeek (openai-compatible)
# ---------------------------------------------------------------------------

def _ds_client(handler):
    from openai import DefaultHttpxClient, OpenAI
    return OpenAI(api_key="test", base_url="https://api.deepseek.com",
                  http_client=DefaultHttpxClient(transport=httpx.MockTransport(handler)))


def _completion(body, content, finish_reason="stop"):
    return httpx.Response(200, json={
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": body["model"],
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def test_deepseek_structured_request_and_parse():
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen.update(body)
        seen["_url"] = str(request.url)
        return _completion(body, json.dumps({"tool": "none", "reason": "have a fresh diff"}))

    out = DeepSeekProvider(client=_ds_client(handler)).structured("sys", "user", DecideOutput)
    assert isinstance(out, DecideOutput) and out.tool == "none"
    assert seen["_url"].startswith("https://api.deepseek.com/")
    assert seen["model"] == DEEPSEEK_MODEL == "deepseek-flash"                # explicit pin
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["temperature"] <= 0.2
    assert seen["thinking"] == {"type": "disabled"}                          # non-thinking, on the wire
    assert seen["messages"][0]["role"] == "system" and "JSON schema" in seen["messages"][0]["content"]
    assert '"tool"' in seen["messages"][0]["content"]          # schema embedded for JSON mode
    assert seen["messages"][1] == {"role": "user", "content": "user"}


def test_deepseek_invalid_json_is_rejected_by_schema():
    def handler(request):
        body = json.loads(request.content)
        return _completion(body, json.dumps({"tool": "diff_completed_steps"}))  # missing 'reason'

    with pytest.raises(Exception):
        DeepSeekProvider(client=_ds_client(handler)).structured("sys", "user", DecideOutput)


def test_deepseek_truncation_raises():
    def handler(request):
        body = json.loads(request.content)
        return _completion(body, "{\"tool\": \"none\", \"reas", finish_reason="length")

    with pytest.raises(RuntimeError, match="finish_reason"):
        DeepSeekProvider(client=_ds_client(handler)).structured("sys", "user", DecideOutput)


def test_missing_credential_is_a_clear_error_without_leaking(monkeypatch):
    monkeypatch.delenv("NOT_A_REAL_KEY_VAR", raising=False)
    from llm import interface
    monkeypatch.setattr(interface, "DEEPSEEK_KEY_VAR", "NOT_A_REAL_KEY_VAR")
    assert credential_present("NOT_A_REAL_KEY_VAR") is False
    with pytest.raises(MissingCredential) as ei:
        DeepSeekProvider()
    assert "NOT_A_REAL_KEY_VAR" in str(ei.value)


def test_deepseek_text_call_also_disables_thinking():
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen.update(body)
        return _completion(body, "OK")

    assert DeepSeekProvider(client=_ds_client(handler)).text("sys", "user") == "OK"
    assert seen["model"] == "deepseek-flash" and seen["thinking"] == {"type": "disabled"}


def test_deepseek_unexpected_reasoning_content_is_an_error():
    def handler(request):
        body = json.loads(request.content)
        r = _completion(body, json.dumps({"tool": "none", "reason": "x"}))
        data = r.json()
        data["choices"][0]["message"]["reasoning_content"] = "let me think..."
        return httpx.Response(200, json=data)

    with pytest.raises(RuntimeError, match="thinking mode was not disabled"):
        DeepSeekProvider(client=_ds_client(handler)).structured("sys", "user", DecideOutput)
