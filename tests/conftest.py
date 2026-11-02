"""Shared fixtures: an isolated gateway app per test + a fake upstream.

The fake upstream is marker-driven: magic strings in the conversation
text change its behaviour, so individual tests stay declarative. No real
provider calls are ever made (httpx MockTransport).

Markers:
  FAIL_ALL                 -> every model returns 500
  FAIL::<upstream model>   -> that model returns 429
  PLEASE_EMPTY             -> groq gpt-oss-120b returns an empty answer
  BADTOOL_STREAM           -> free models stream a tool call with bad JSON args
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from gateway import GatewayConfig, create_app  # noqa: E402

RAW_KEYS = {
    "hermes": "hermes-test-key",
    "openclaw": "openclaw-test-key",
    "n8n": "n8n-test-key",
    "admin": "admin-test-key",
}

FREE_MODELS = {
    "openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b",
    "cohere/north-mini-code:free", "dots-studio/dots-3-note-preview:free",
}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _all_text(body: dict) -> str:
    parts: list[str] = []
    for m in body.get("messages") or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(p.get("text", "") for p in c if isinstance(p, dict))
    return "\n".join(parts)


def _completion(model: str, content: str | None, *, tool_calls=None,
                finish: str = "stop") -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-fake", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                  "total_tokens": 110},
    }


def _sse(chunks: list[dict], usage: dict | None = None) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    if usage:
        lines.append(f"data: {json.dumps({'id': 'x', 'choices': [], 'usage': usage})}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _chunk(model: str, delta: dict, finish=None) -> dict:
    return {"id": "chatcmpl-fake", "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def fake_upstream(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    model = body["model"]
    text = _all_text(body)

    if "FAIL_ALL" in text:
        return httpx.Response(500, json={"error": {"message": "boom"}})
    if f"FAIL::{model}" in text:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    if body.get("stream"):
        usage = {"prompt_tokens": 100, "completion_tokens": 6, "total_tokens": 106}
        tools = body.get("tools") or []
        wants_tool = bool(tools) and "USE_TOOL" in text
        if wants_tool:
            name = tools[0]["function"]["name"]
            bad = "BADTOOL_STREAM" in text and model in FREE_MODELS
            # arguments split across chunks, but not inside the word
            # "command", so tests can assert on the reassembled call
            arg_parts = ["{not-", "json"] if bad else ['{"command": ', '"ls"}']
            chunks = [
                _chunk(model, {"role": "assistant", "tool_calls": [
                    {"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": name, "arguments": ""}}]}),
                _chunk(model, {"tool_calls": [
                    {"index": 0, "function": {"arguments": arg_parts[0]}}]}),
                _chunk(model, {"tool_calls": [
                    {"index": 0, "function": {"arguments": arg_parts[1]}}]}),
                _chunk(model, {}, finish="tool_calls"),
            ]
            return httpx.Response(200, content=_sse(chunks, usage),
                                  headers={"content-type": "text/event-stream"})
        chunks = [
            _chunk(model, {"role": "assistant", "content": "Hello "}),
            _chunk(model, {"content": "world"}),
            _chunk(model, {}, finish="stop"),
        ]
        return httpx.Response(200, content=_sse(chunks, usage),
                              headers={"content-type": "text/event-stream"})

    # non-streaming
    if "PLEASE_EMPTY" in text and model == "openai/gpt-oss-120b":
        return httpx.Response(200, json=_completion(model, ""))
    if "BADTOOL" in text and model in FREE_MODELS:
        name = (body.get("tools") or [{}])[0].get("function", {}).get("name", "run")
        return httpx.Response(200, json=_completion(
            model, None, finish="tool_calls",
            tool_calls=[{"id": "c1", "type": "function",
                         "function": {"name": name, "arguments": "{oops"}}]))
    if "UNKNOWNTOOL" in text and model in FREE_MODELS:
        return httpx.Response(200, json=_completion(
            model, None, finish="tool_calls",
            tool_calls=[{"id": "c1", "type": "function",
                         "function": {"name": "not_a_real_tool", "arguments": "{}"}}]))
    if "BADJSON" in text and model in FREE_MODELS:
        return httpx.Response(200, json=_completion(model, "this is not json"))
    return httpx.Response(200, json=_completion(model, f"answer from {model}"))


def build_app(tmp_path: Path, **cfg_kwargs):
    tmp_path.mkdir(parents=True, exist_ok=True)
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha(RAW_KEYS["admin"]),
        "clients": [
            {"id": "hermes", "key_sha256": _sha(RAW_KEYS["hermes"]),
             "lanes": ["private-free", "paid"], "premium_allowed": False,
             "daily_target_usd": 1.0},
            {"id": "openclaw", "key_sha256": _sha(RAW_KEYS["openclaw"]),
             "lanes": ["private-free", "paid", "premium"],
             "premium_allowed": True, "daily_target_usd": 3.0,
             "premium_monthly_alert_usd": 20.0, "premium_daily_count_alert": 20},
            {"id": "n8n", "key_sha256": _sha(RAW_KEYS["n8n"]),
             "lanes": ["private-free", "paid"], "premium_allowed": False,
             "daily_target_usd": 0.5},
        ],
    }))
    cfg_kwargs.setdefault("registry_path", SRC / "registry.json")
    cfg = GatewayConfig(
        state_dir=tmp_path / "state",
        clients_path=clients_file,
        provider_keys={"groq": "fake", "openrouter": "fake",
                       "deepinfra": "fake", "ollama": "fake", "openai": "fake"},
        **cfg_kwargs,
    )
    transport = httpx.MockTransport(fake_upstream)
    return create_app(cfg, upstream_transport=transport)


@pytest.fixture()
def app(tmp_path):
    return build_app(tmp_path)


@pytest.fixture()
def client(app):
    with TestClient(app) as tc:
        yield tc


def auth(who: str) -> dict:
    return {"Authorization": f"Bearer {RAW_KEYS[who]}"}


def chat_body(text: str, **overrides) -> dict:
    body = {"model": "auto",
            "messages": [{"role": "user", "content": text}]}
    body.update(overrides)
    return body
