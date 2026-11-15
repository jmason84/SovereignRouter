"""v0.8.2 — stream resilience (F-11).

The incident (2026-11-13, Hermes executive-ops): three consecutive
router 502s on zai/glm-4.5-flash. The upstream stream stalled, the
flat 120 s read timeout took ~2 minutes to notice, partial output had
already committed (so in-request failover was impossible by design),
and — the router's own aggravating fault — Hermes' automatic retries
were NEW requests that re-picked the same entry, because v0.8.0's
attempt ledger recorded failures but selection never read them.

This file pins the three fixes:
  (a) streaming reads carry first-chunk (45 s) and inter-chunk stall
      (60 s) bounds instead of the flat 120 s;
  (b) a streaming abort cools the entry (600 s, quota-store
      timestamps); selection and sticky reuse skip it with reason
      ``cooldown_stream_abort``;
  (c) the cooldown never manufactures a zero-candidate 429: when the
      cooling entry is the only candidate it still serves, annotated
      ``cooldown_bypassed_only_candidate``;
plus the carried R3 ledger class ``truncated_tool_call``
(finish_reason=length with tool calls present).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conftest import (RAW_KEYS, _all_text, _chunk, _sse, auth, build_app,  # noqa: E402
                      chat_body, fake_upstream)
from gateway import GatewayConfig, create_app  # noqa: E402

PIN = "groq/gpt-oss-120b"
PIN_MODEL = "openai/gpt-oss-120b"
SRC = Path(__file__).resolve().parent.parent / "src"

TOOLS = [{"type": "function",
          "function": {"name": "run_command",
                       "parameters": {"type": "object",
                                      "properties": {
                                          "command": {"type": "string"}}}}}]


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _app(tmp_path, handler, **cfg_kwargs):
    tmp_path.mkdir(parents=True, exist_ok=True)
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha(RAW_KEYS["admin"]),
        "clients": [
            {"id": "hermes", "key_sha256": _sha(RAW_KEYS["hermes"]),
             "lanes": ["private-free", "paid", "public-only-free"],
             "premium_allowed": False, "daily_target_usd": 1.0},
        ],
    }))
    cfg = GatewayConfig(
        state_dir=tmp_path / "state",
        clients_path=clients_file,
        registry_path=SRC / "registry.json",
        provider_keys={"groq": "fake", "openrouter": "fake",
                       "deepinfra": "fake", "ollama": "fake",
                       "openai": "fake", "deepseek": "fake", "zai": "fake",
                       "cloudflare": "fake", "google": "fake"},
        **cfg_kwargs)
    return create_app(cfg, upstream_transport=httpx.MockTransport(handler))


def _last_record(app):
    return app.state.telemetry.recent(0)[-1]


def _sse_bytes(chunks) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks)


class _DripStream(httpx.AsyncByteStream):
    """Yields part 1, sleeps, yields part 2 — a wedged mid-stream."""

    def __init__(self, part1: bytes, part2: bytes, drip_s: float,
                 request: httpx.Request):
        self._parts = (part1, part2)
        self._drip = drip_s
        self._request = request

    async def __aiter__(self):
        yield self._parts[0]
        await asyncio.sleep(self._drip)
        yield self._parts[1]


class _DropStream(httpx.AsyncByteStream):
    """Yields its bytes, then drops the connection (transport abort)."""

    def __init__(self, payload: bytes, request: httpx.Request):
        self._payload = payload
        self._request = request

    async def __aiter__(self):
        yield self._payload
        raise httpx.ReadError("connection dropped mid-stream",
                              request=self._request)


def _text_chunks(model, *texts):
    return [_chunk(model, {"role": "assistant", "content": texts[0]})] + \
           [_chunk(model, {"content": t}) for t in texts[1:]]


def _handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    model = body["model"]
    text = _all_text(body)
    if model == PIN_MODEL and "STALL" in text:
        payload = _sse_bytes(_text_chunks(model, "AAA "))
        rest = _sse_bytes(_text_chunks(model, "BBB")
                          + [_chunk(model, {}, finish="stop")])
        return httpx.Response(200, stream=_DripStream(payload, rest, 3.0, request),
                              headers={"content-type": "text/event-stream"})
    if model == PIN_MODEL and "SLOWSTART" in text:
        payload = _sse_bytes(_text_chunks(model, "Hello ", "world")
                             + [_chunk(model, {}, finish="stop")])
        return httpx.Response(200, stream=_DripStream(b"", payload, 3.0, request),
                              headers={"content-type": "text/event-stream"})
    if model == PIN_MODEL and "DROP" in text:
        payload = _sse_bytes(_text_chunks(model, "AAA ", "BBB"))
        return httpx.Response(200, stream=_DropStream(payload, request),
                              headers={"content-type": "text/event-stream"})
    if model == PIN_MODEL and "TRUNCJSON" in text and not body.get("stream"):
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "length",
                         "message": {"role": "assistant", "content": None,
                                     "tool_calls": [{
                                         "id": "c1", "type": "function",
                                         "function": {"name": "run_command",
                                                      "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                      "total_tokens": 110}})
    if model == PIN_MODEL and "TRUNCSTREAM" in text and body.get("stream"):
        name = (body.get("tools") or [{}])[0].get("function", {}).get("name", "run_command")
        chunks = [
            _chunk(model, {"role": "assistant", "tool_calls": [
                {"index": 0, "id": "call_1", "type": "function",
                 "function": {"name": name, "arguments": ""}}]}),
            _chunk(model, {"tool_calls": [
                {"index": 0, "function": {"arguments": "{}"}}]}),
            _chunk(model, {}, finish="length"),
        ]
        usage = {"prompt_tokens": 100, "completion_tokens": 6, "total_tokens": 106}
        return httpx.Response(200, content=_sse(chunks, usage),
                              headers={"content-type": "text/event-stream"})
    return fake_upstream(request)


# ----------------------------------------------------------------------
# (a) stall bound: one chunk, then silence — abort inside the bound,
#     not at the flat 120 s
# ----------------------------------------------------------------------
def test_stall_aborts_within_bound_not_120s(tmp_path):
    app = _app(tmp_path, _handler,
               stream_first_chunk_timeout_s=5.0, stream_stall_timeout_s=0.4)
    with TestClient(app) as client:
        t0 = time.monotonic()
        r = client.post("/v1/chat/completions",
                        json=chat_body("STALL stream please",
                                       model=PIN, stream=True),
                        headers=auth("hermes"))
        elapsed = time.monotonic() - t0
    assert elapsed < 2.5, f"stall abort took {elapsed:.1f}s (drip was 3s)"
    assert "AAA" in r.text                      # committed partial output kept
    assert "BBB" not in r.text                  # nothing after the stall
    assert "stream_interrupted" in r.text       # ADD-06 typed terminator
    rec = _last_record(app)
    assert f"stream_aborted_after_commit:{PIN}" in rec.signals
    assert f"stream_bound_timeout:{PIN}" in rec.signals
    assert rec.status == 502


# ----------------------------------------------------------------------
# (e) first-chunk bound: silence before ANY output fails over
#     pre-commit, exactly like a transport drop
# ----------------------------------------------------------------------
def test_first_chunk_timeout_fails_over_before_commit(tmp_path):
    app = _app(tmp_path, _handler,
               stream_first_chunk_timeout_s=0.4, stream_stall_timeout_s=5.0)
    with TestClient(app) as client:
        t0 = time.monotonic()
        r = client.post("/v1/chat/completions",
                        json=chat_body("SLOWSTART stream please",
                                       model=PIN, stream=True),
                        headers=auth("hermes"))
        elapsed = time.monotonic() - t0
    assert elapsed < 2.5
    assert r.status_code == 200
    assert "Hello" in r.text and "world" in r.text   # sibling served
    assert "stream_interrupted" not in r.text
    rec = _last_record(app)
    assert rec.status == 200
    assert rec.model != PIN
    assert {"model": PIN, "reason": "upstream_unreachable"} in rec.skipped
    assert f"stream_bound_timeout:{PIN}" in rec.signals


# ----------------------------------------------------------------------
# (b) a transport abort cools the entry; the next (new-conversation)
#     selection skips it and serves the sibling
# ----------------------------------------------------------------------
def test_abort_sets_cooldown_and_next_selection_skips(tmp_path):
    app = _app(tmp_path, _handler)
    with TestClient(app) as client:
        # Incident shape: pinned stream drops after partial output.
        r = client.post("/v1/chat/completions",
                        json=chat_body("DROP stream please",
                                       model=PIN, stream=True),
                        headers=auth("hermes"))
        assert "stream_interrupted" in r.text
        until = app.state.store.stream_cooldown_until(PIN)
        assert until is not None and until > time.time() + 60

        # Learn which entry serves a plain auto conversation, cool it
        # (as its own abort would), and re-ask in a NEW conversation:
        # selection must skip it with the cooldown reason.
        r1 = client.post("/v1/chat/completions",
                         json=chat_body("cooldown probe conversation one",
                                        stream=True),
                         headers=auth("hermes"))
        assert r1.status_code == 200
        first_server = _last_record(app).model
        app.state.store.set_stream_cooldown(first_server, time.time() + 600)
        r2 = client.post("/v1/chat/completions",
                         json=chat_body("cooldown probe conversation two",
                                        stream=True),
                         headers=auth("hermes"))
        assert r2.status_code == 200
        rec = _last_record(app)
        assert rec.model != first_server
        assert {"model": first_server,
                "reason": "cooldown_stream_abort"} in rec.skipped


# ----------------------------------------------------------------------
# (d) sticky reuse respects the cooldown: the sticky model is not
#     re-served while cooling, and the skip carries the cooldown
#     reason (not the generic sticky_no_longer_eligible)
# ----------------------------------------------------------------------
def test_sticky_reuse_respects_cooldown(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as tc:
        body = chat_body("sticky cooldown conversation", stream=True)
        r1 = tc.post("/v1/chat/completions", json=body, headers=auth("hermes"))
        assert r1.status_code == 200
        sticky_model = _last_record(app).model
        app.state.store.set_stream_cooldown(sticky_model, time.time() + 600)
        # Same conversation: sticky would re-pick the cooled model.
        r2 = tc.post("/v1/chat/completions", json=body, headers=auth("hermes"))
        assert r2.status_code == 200
        rec = _last_record(app)
        assert rec.model != sticky_model
        assert {"model": sticky_model,
                "reason": "cooldown_stream_abort"} in rec.skipped
        assert {"model": sticky_model,
                "reason": "sticky_no_longer_eligible"} not in rec.skipped


# ----------------------------------------------------------------------
# (c) the only-candidate exception: a cooling SOLE candidate still
#     serves — the cooldown never manufactures a zero-candidate 429
# ----------------------------------------------------------------------
def test_cooldown_only_candidate_still_serves(tmp_path):
    data = json.loads((SRC / "registry.json").read_text())
    keep = PIN
    for m in data["models"]:
        if m["id"] != keep:
            m["enabled"] = False
    variant = tmp_path / "registry-only-groq.json"
    variant.write_text(json.dumps(data))
    app = build_app(tmp_path, registry_path=variant)
    app.state.store.set_stream_cooldown(keep, time.time() + 600)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("only candidate conversation", stream=True),
                    headers=auth("hermes"))
    assert r.status_code == 200
    assert "Hello" in r.text
    rec = _last_record(app)
    assert rec.model == keep
    assert {"model": keep, "reason": "cooldown_stream_abort"} in rec.skipped
    assert {"model": keep,
            "reason": "cooldown_bypassed_only_candidate"} in rec.skipped


def test_cooldown_expires_on_its_own(tmp_path):
    app = build_app(tmp_path)
    app.state.store.set_stream_cooldown(PIN, time.time() - 1)  # already past
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("expired cooldown conversation",
                                   model=PIN, stream=True),
                    headers=auth("hermes"))
    assert r.status_code == 200
    rec = _last_record(app)
    assert {"model": PIN, "reason": "cooldown_stream_abort"} not in rec.skipped


# ----------------------------------------------------------------------
# (f) truncated_tool_call: finish_reason=length with tool calls lands
#     on the attempt ledger — JSON and streaming paths
# ----------------------------------------------------------------------
def test_truncated_tool_call_classified_json(tmp_path):
    app = _app(tmp_path, _handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("TRUNCJSON please", model=PIN,
                                       tools=TOOLS),
                        headers=auth("hermes"))
    assert r.status_code == 200
    rows = [a for a in app.state.store.attempts_since(0)
            if a["model_id"] == PIN]
    assert rows and rows[-1]["failure_class"] == "truncated_tool_call"
    assert rows[-1]["scope"] == "model"


def test_truncated_tool_call_classified_stream(tmp_path):
    app = _app(tmp_path, _handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("TRUNCSTREAM please USE_TOOL",
                                       model=PIN, tools=TOOLS, stream=True),
                        headers=auth("hermes"))
    assert r.status_code == 200
    rows = [a for a in app.state.store.attempts_since(0)
            if a["model_id"] == PIN]
    assert rows and rows[-1]["failure_class"] == "truncated_tool_call"


def test_normal_serve_has_no_failure_class(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("plain conversation", model=PIN),
                    headers=auth("hermes"))
    assert r.status_code == 200
    rows = [a for a in app.state.store.attempts_since(0)
            if a["model_id"] == PIN]
    assert rows and rows[-1]["failure_class"] is None
