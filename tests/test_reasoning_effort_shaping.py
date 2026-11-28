"""reasoning_effort value shaping (v0.10.3, 2026-11-27 cleanup loop).

Two premium last resorts reject specific reasoning_effort VALUES with
hard 400s — and 400 is not in FAILOVER_STATUSES, so the poisoned value
fails the whole turn when the cascade reaches these entries with
nothing left behind them. Both error bodies were captured verbatim in
attempt_events.error_body (the v0.10.1 capture paying off):

* OpenAI gpt-5.5 (premium/gpt-5.5) + function tools: function tools
  with a graded reasoning_effort are not supported for gpt-5.5 in
  /v1/chat/completions. The accepted value on a tool-bearing turn is
  "none"; an omitted field defaults server-side to a graded effort,
  so the gateway SETS "none" on tool-shaped requests even when the
  client sent no effort. Tool-free turns keep the client's effort.
* Z.ai glm-5.3 (zai/glm-5.3), error 1210: the model always thinks
  and accepts only low/high/max. A client-sent "none" is rewritten
  to "low"; an absent field stays absent.

The shaping is narrow by provider+model and runs on the per-attempt
upstream body, so a fallback attempt to another provider never
inherits the rewritten value.

Third shape, same release (owner's fold-in ruling, 2026-11-27):
premium/gpt-5.5 also rejects a client TEMPERATURE — "Unsupported
value: 'temperature' does not support 0.2 with this model"
(attempt_events.error_body, 2026-11-27 19:22 UTC; three n8n L5
turns died end-to-end when the free pass exhausted on the OpenRouter
daily cap + the Cloudflare neuron wall and the premium step then
400'd on the forwarded 0.2). GPT-5-class reasoning models run at
their default temperature only, so for that entry the gateway omits
the field and lets the upstream default apply. Every other entry
keeps the client's temperature exactly as sent.
"""

from __future__ import annotations

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
from gateway import (GatewayConfig, _shape_reasoning_effort,  # noqa: E402
                     _temperature_stripped, create_app)

GPT55 = "premium/gpt-5.5"
GLM53 = "zai/glm-5.3"
TOOLS = [{"type": "function",
          "function": {"name": "get_weather",
                       "parameters": {"type": "object",
                                      "properties": {
                                          "city": {"type": "string"}}}}}]


def _entry(provider, model):
    return SimpleNamespace(provider=provider, model=model)


def _msgs():
    return [{"role": "user", "content": "What is the weather in Rapid City?"}]


# ----------------------------------------------------------------------
# gpt-5.5: tool-bearing turns are forced to "none"
# ----------------------------------------------------------------------

def test_gpt55_tools_client_effort_forced_to_none():
    out = {"messages": _msgs(), "tools": TOOLS, "reasoning_effort": "high"}
    _shape_reasoning_effort(_entry("openai", "gpt-5.5"), out)
    assert out["reasoning_effort"] == "none"


def test_gpt55_tools_absent_effort_is_set_to_none():
    # An omitted field defaults server-side to a graded effort, which
    # 400s just the same — the value must be set, not merely fixed.
    out = {"messages": _msgs(), "tools": TOOLS}
    _shape_reasoning_effort(_entry("openai", "gpt-5.5"), out)
    assert out["reasoning_effort"] == "none"


def test_gpt55_tool_history_counts_as_tool_shaped():
    history = _msgs() + [
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "get_weather",
                         "arguments": "{\"city\": \"Rapid City\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"}]
    out = {"messages": history, "reasoning_effort": "medium"}
    _shape_reasoning_effort(_entry("openai", "gpt-5.5"), out)
    assert out["reasoning_effort"] == "none"


def test_gpt55_tool_free_turn_keeps_client_effort():
    out = {"messages": _msgs(), "reasoning_effort": "high"}
    _shape_reasoning_effort(_entry("openai", "gpt-5.5"), out)
    assert out["reasoning_effort"] == "high"


def test_gpt55_tool_free_turn_keeps_absent_effort():
    out = {"messages": _msgs()}
    _shape_reasoning_effort(_entry("openai", "gpt-5.5"), out)
    assert "reasoning_effort" not in out


# ----------------------------------------------------------------------
# glm-5.3: only the "none" value is rewritten, to "low"
# ----------------------------------------------------------------------

def test_glm53_none_rewritten_to_low():
    out = {"messages": _msgs(), "reasoning_effort": "none"}
    _shape_reasoning_effort(_entry("zai", "glm-5.3"), out)
    assert out["reasoning_effort"] == "low"


def test_glm53_graded_effort_untouched():
    out = {"messages": _msgs(), "reasoning_effort": "high"}
    _shape_reasoning_effort(_entry("zai", "glm-5.3"), out)
    assert out["reasoning_effort"] == "high"


def test_glm53_absent_effort_stays_absent():
    out = {"messages": _msgs()}
    _shape_reasoning_effort(_entry("zai", "glm-5.3"), out)
    assert "reasoning_effort" not in out


# ----------------------------------------------------------------------
# scope: no other entry is shaped
# ----------------------------------------------------------------------

def test_other_entries_never_shaped():
    out = {"messages": _msgs(), "tools": TOOLS, "reasoning_effort": "high"}
    _shape_reasoning_effort(_entry("groq", "gpt-oss-120b"), out)
    assert out["reasoning_effort"] == "high"
    # Same weights, different host: SiliconFlow's glm-5.3 produced no
    # such 400 and is not touched.
    out = {"messages": _msgs(), "reasoning_effort": "none"}
    _shape_reasoning_effort(_entry("siliconflow", "glm-5.3"), out)
    assert out["reasoning_effort"] == "none"
    # OpenRouter-fronted gpt-5.5 is normalized by that stack.
    out = {"messages": _msgs(), "tools": TOOLS, "reasoning_effort": "high"}
    _shape_reasoning_effort(_entry("openrouter", "openai/gpt-5.5"), out)
    assert out["reasoning_effort"] == "high"


# ----------------------------------------------------------------------
# gateway integration: the shaped payload reaches the upstream
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
        provider_keys={"openai": "fake", "zai": "fake"})
    app = create_app(
        cfg, upstream_transport=httpx.MockTransport(transport_handler))
    return app, calls


def _ok(model):
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant",
                                 "content": "Sunny."}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                  "total_tokens": 15}})


def test_gateway_shapes_gpt55_tool_turn(tmp_path):
    app, calls = build_recording_app(
        tmp_path, lambda model, body, n: _ok(model))
    body = {"model": GPT55, "messages": _msgs(), "tools": TOOLS,
            "reasoning_effort": "high", "max_tokens": 64}
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=body,
                        headers=auth("openclaw"))
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and calls[0]["model"] == "gpt-5.5"
    assert calls[0]["reasoning_effort"] == "none"


def test_gateway_shapes_glm53_none_effort(tmp_path):
    app, calls = build_recording_app(
        tmp_path, lambda model, body, n: _ok(model))
    body = {"model": GLM53, "messages": _msgs(),
            "reasoning_effort": "none", "max_tokens": 64}
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=body,
                        headers=auth("openclaw"))
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and calls[0]["model"] == "glm-5.3"
    assert calls[0]["reasoning_effort"] == "low"


# ----------------------------------------------------------------------
# temperature guard: gpt-5.5 rejects every non-default temperature
# ----------------------------------------------------------------------

def test_temperature_predicate_scope():
    assert _temperature_stripped(_entry("openai", "gpt-5.5"))
    # Aggregator-fronted gpt-5.5 is normalized by that stack.
    assert not _temperature_stripped(_entry("openrouter", "openai/gpt-5.5"))
    assert not _temperature_stripped(_entry("zai", "glm-5.3"))
    assert not _temperature_stripped(_entry("groq", "gpt-oss-120b"))


def test_gateway_strips_temperature_for_gpt55(tmp_path):
    # The exact shape that killed the three n8n L5 turns on
    # 2026-11-27: a client temperature of 0.2 reaching premium/gpt-5.5.
    app, calls = build_recording_app(
        tmp_path, lambda model, body, n: _ok(model))
    body = {"model": GPT55, "messages": _msgs(), "temperature": 0.2,
            "max_tokens": 64}
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=body,
                        headers=auth("openclaw"))
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and calls[0]["model"] == "gpt-5.5"
    assert "temperature" not in calls[0]


def test_gateway_forwards_temperature_to_other_entries(tmp_path):
    # The guard is narrow: glm-5.3 accepts the client's temperature
    # and must keep receiving it exactly as sent.
    app, calls = build_recording_app(
        tmp_path, lambda model, body, n: _ok(model))
    body = {"model": GLM53, "messages": _msgs(), "temperature": 0.2,
            "max_tokens": 64}
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=body,
                        headers=auth("openclaw"))
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and calls[0]["model"] == "glm-5.3"
    assert calls[0]["temperature"] == 0.2
