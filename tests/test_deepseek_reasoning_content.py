"""DeepSeek thinking-mode reasoning_content pad (v0.10.2).

deepseek/deepseek-v4-pro 400s when a tool-shaped request replays
assistant history whose reasoning_content is missing: "The
`reasoning_content` in the thinking mode must be passed back to the
API." (attempt_events.error_body, 3x in the 72h before 2026-11-27).
Same family as the Gemini thought-signature bug fixed in v0.10.1: a
model-switching router replays assistant turns originated by other
models (which have no DeepSeek reasoning at all) and by clients whose
serialisers drop the extra field.

DeepSeek's own Thinking Mode documentation states the rule: for
requests carrying the `tools` parameter, the reasoning_content of all
previous turns must be passed back — even turns that made no tool
call — or the API returns 400. The established fix shape (the same
one Hermes itself shipped for this error) is a defensive pad: an
assistant message that lacks reasoning_content carries an empty
string, which DeepSeek accepts; a real value from the client is
preserved verbatim.

The gateway therefore pads ONLY for direct-DeepSeek reasoning entries
and ONLY on tool-shaped requests (a tools array, tool calls in
history, or tool results in history) — copy-on-write, so a fallback
attempt to another provider sees the client's original messages.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from conftest import auth  # noqa: E402
from gateway import (  # noqa: E402
    GatewayConfig,
    _needs_reasoning_content_pad,
    _with_reasoning_content_pad,
    create_app,
)

DEEPSEEK_PRO = "deepseek/deepseek-v4-pro"
TOOLS = [{"type": "function",
          "function": {"name": "get_weather",
                       "parameters": {"type": "object",
                                      "properties": {
                                          "city": {"type": "string"}}}}}]


def _entry(provider, model, reasoning):
    return SimpleNamespace(provider=provider, model=model,
                           reasoning=reasoning)


def _history():
    return [
        {"role": "user", "content": "What is the weather in Rapid City?"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "get_weather",
                         "arguments": "{\"city\": \"Rapid City\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny, 40F"},
        {"role": "user", "content": "And tomorrow?"},
        {"role": "assistant", "content": "Sunny again."},
    ]


# ----------------------------------------------------------------------
# predicate: only direct-DeepSeek reasoning entries
# ----------------------------------------------------------------------

def test_predicate_scope():
    assert _needs_reasoning_content_pad(
        _entry("deepseek", "deepseek-v4-pro", True))
    assert not _needs_reasoning_content_pad(
        _entry("deepseek", "deepseek-chat", False))
    assert not _needs_reasoning_content_pad(
        _entry("deepinfra", "deepseek-v4-pro", True))
    assert not _needs_reasoning_content_pad(
        _entry("openrouter", "deepseek/deepseek-v4-pro", True))
    assert not _needs_reasoning_content_pad(
        _entry("google", "gemini-3.1-flash-lite", True))


# ----------------------------------------------------------------------
# shaping: pad where absent, preserve where present, never mutate
# ----------------------------------------------------------------------

def test_pad_injected_on_assistant_messages_lacking_it():
    out = _with_reasoning_content_pad(_history())
    assert out[1]["reasoning_content"] == ""        # tool-call turn
    assert out[4]["reasoning_content"] == ""        # plain turn (docs:
    # with tools in the request, ALL previous turns must carry it)
    assert "reasoning_content" not in out[0]        # user untouched
    assert "reasoning_content" not in out[2]        # tool untouched


def test_real_reasoning_content_preserved():
    msgs = _history()
    msgs[1]["reasoning_content"] = "REAL-REASONING"
    out = _with_reasoning_content_pad(msgs)
    assert out[1]["reasoning_content"] == "REAL-REASONING"


def test_none_reasoning_content_counts_as_absent():
    msgs = _history()
    msgs[1]["reasoning_content"] = None
    out = _with_reasoning_content_pad(msgs)
    assert out[1]["reasoning_content"] == ""


def test_input_not_mutated():
    msgs = _history()
    before = copy.deepcopy(msgs)
    _with_reasoning_content_pad(msgs)
    assert msgs == before


# ----------------------------------------------------------------------
# gateway integration: the padded payload reaches DeepSeek only
# ----------------------------------------------------------------------

def build_recording_app(tmp_path, handler):
    def sha(v):
        return hashlib.sha256(v.encode()).hexdigest()

    clients_file = tmp_path / "clients-rec.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": sha("admin-test-key"),
        "clients": [{
            "id": "openclaw", "key_sha256": sha("openclaw-test-key"),
            "lanes": ["private-free", "paid", "premium"],
            "premium_allowed": True, "daily_target_usd": 3.0}]}))
    calls: list[dict] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return handler(body["model"], body, len(calls))

    cfg = GatewayConfig(
        state_dir=tmp_path / "state", clients_path=clients_file,
        registry_path=SRC / "registry.json",
        provider_keys={"deepseek": "fake", "openrouter": "fake",
                       "groq": "fake", "deepinfra": "fake",
                       "openai": "fake", "zai": "fake", "ollama": "fake"})
    app = create_app(
        cfg, upstream_transport=httpx.MockTransport(transport_handler))
    return app, calls


def _ok(model):
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant",
                                 "content": "Sunny again."}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                  "total_tokens": 15}})


def test_gateway_pads_for_deepseek_upstream(tmp_path):
    app, calls = build_recording_app(
        tmp_path, lambda model, body, n: _ok(model))
    body = {"model": DEEPSEEK_PRO, "messages": _history(),
            "tools": TOOLS, "max_tokens": 64}
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=body,
                        headers=auth("openclaw"))
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and calls[0]["model"] == "deepseek-v4-pro"
    assert calls[0]["messages"][1]["reasoning_content"] == ""


def test_pad_does_not_leak_into_a_fallback_attempt(tmp_path):
    def handler(model, body, n):
        if model == "deepseek-v4-pro":
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return _ok(model)

    app, calls = build_recording_app(tmp_path, handler)
    body = {"model": DEEPSEEK_PRO, "messages": _history(),
            "tools": TOOLS, "max_tokens": 64}
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=body,
                        headers=auth("openclaw"))
    assert r.status_code == 200, r.text
    assert len(calls) >= 2
    assert calls[0]["messages"][1]["reasoning_content"] == ""
    for later in calls[1:]:
        for msg in later["messages"]:
            assert "reasoning_content" not in msg
