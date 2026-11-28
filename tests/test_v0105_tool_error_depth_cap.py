"""v0.10.5 (Job B, owner go 2026-11-27 8:25 PM MT, "Do B C D").

1. Tool-error detector: a success payload that merely contains an
   ``"error"`` key with an empty value (``{"error": null}``) is NOT a
   tool error. Pre-v0.10.5 a bare substring test counted it, feeding
   false positives into tool_error_next_turn, the F2 sticky break and
   the v0.10.2 production tool-error ledger.
2. Fallback-depth cap: at most MAX_UPSTREAM_ATTEMPTS (6) upstream calls
   per turn (72h census to 2026-11-27 saw depths of 21). The free-first
   pass hands over to the paid spillway with SPILLWAY_RESERVED_ATTEMPTS
   left, so the cap does not turn a servable turn into a 429.
3. Never escalate to premium because free pools are congested: in the
   spillway pass premium stays only when the request requires it
   (premium_permitted) or no paid entry can meet the floor at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import _chunk, _sse, auth, chat_body  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402

import gateway
from gateway import (MAX_UPSTREAM_ATTEMPTS, SPILLWAY_RESERVED_ATTEMPTS,
                     _tool_text_has_error)

SRC = Path(__file__).resolve().parent.parent / "src"
L4 = {"X-Sovereign-Level": "4"}
L5 = {"X-Sovereign-Level": "5"}

# ----------------------------------------------------------------------
# 1. detector
# ----------------------------------------------------------------------
NOT_ERRORS = [
    '{"error": null, "data": {"rows": 3}}',
    '{"result": "ok", "error": ""}',
    '{"ok": true, "error": false}',
    '{"errors": [], "items": [1, 2]}',
    '{"error": {}, "status": "ok"}',
    '{"error": 0}',
    '{"status": "success", "error_message": null}',
    '{"stdout": "no error found in the log", "exit_code": 0}',
    '[{"type": "text", "text": "listed 4 containers, 0 errors"}]',
    "errors: 0",
    "Errors: none",
    "error count: 0",
    "build finished; no error",
    "the log mentions \"error\" twice but the command succeeded",
    '{"message": "error handling middleware installed"}',
    "",
    "   ",
    "step ok",
]

ERRORS = [
    '{"error": "command not found"}',
    '{"error": {"code": 404, "message": "not found"}}',
    '{"status": "error", "error": "BLOCKED: execute_code runs arbitrary local code"}',
    '{"status": "failed", "message": "timeout"}',
    '{"ok": false, "reason": "denied"}',
    '{"success": false}',
    '{"isError": true, "content": [{"type": "text", "text": "boom"}]}',
    '{"result": {"error": "disk full"}}',
    '[{"is_error": true, "text": "x"}]',
    '{"errors": ["bad field"]}',
    "Error: file not found",
    "ERROR permission denied",
    "Traceback (most recent call last):\n  File \"x.py\"",
    "Exception: division by zero",
    "fatal: not a git repository",
    '{"error": "truncated payload',          # unparseable JSON, real error
    'prefix text {"status": "error", "detail": 1}',
    'noise {"isError": true}',
]


@pytest.mark.parametrize("text", NOT_ERRORS)
def test_success_payloads_are_not_errors(text):
    assert _tool_text_has_error(text) is False


@pytest.mark.parametrize("text", ERRORS)
def test_real_errors_are_detected(text):
    assert _tool_text_has_error(text) is True


TOOLS = [{"type": "function",
          "function": {"name": "run_command",
                       "parameters": {"type": "object",
                                      "properties": {
                                          "command": {"type": "string"}}}}}]


def _tool_loop(result: str) -> list[dict]:
    return [
        {"role": "system", "content": "You are an infra agent."},
        {"role": "user", "content": "restart the nginx container and verify it"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c0", "type": "function",
             "function": {"name": "run_command", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c0", "content": result},
    ]


def _completion(model: str, content: str) -> dict:
    return {
        "id": "chatcmpl-fake", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                  "total_tokens": 110},
    }


def _ok(model, body, n):
    if body.get("stream"):
        chunks = [_chunk(model, {"role": "assistant", "content": "ok "}),
                  _chunk(model, {"content": "done"}),
                  _chunk(model, {}, finish="stop")]
        return httpx.Response(
            200, content=_sse(chunks, {"prompt_tokens": 10,
                                       "completion_tokens": 2,
                                       "total_tokens": 12}),
            headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json=_completion(model, "done"))


@pytest.mark.parametrize("result,expected", [
    ('{"error": null, "stdout": "nginx restarted"}', False),
    ('{"error": "container not found"}', True),
])
def test_route_record_tool_error_signal(tmp_path, result, expected):
    app, _ = build_recording_app(tmp_path, _ok)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json={"model": "auto", "messages": _tool_loop(result),
                              "tools": TOOLS},
                        headers=auth("hermes"))
        assert r.status_code == 200, r.text
        rec = app.state.telemetry.recent(0)[-1]
    assert rec.tool_error_next_turn is expected
    assert ("tool_error_next_turn" in rec.signals) is expected


def test_latest_tool_error_uses_new_detector():
    msgs = _tool_loop('{"error": null}')
    assert gateway._latest_tool_error(msgs) is False
    msgs = _tool_loop('{"error": "boom"}')
    assert gateway._latest_tool_error(msgs) is True


# ----------------------------------------------------------------------
# 2. depth cap
# ----------------------------------------------------------------------
def test_constants():
    assert MAX_UPSTREAM_ATTEMPTS == 6
    assert SPILLWAY_RESERVED_ATTEMPTS == 2


def _lanes_by_model():
    data = json.loads((SRC / "registry.json").read_text())
    return {m["model"] if "model" in m else m["id"]: m["lane"]
            for m in data["models"]}, {m["id"]: m["lane"] for m in data["models"]}


def _lane_of(app, upstream_model: str) -> str:
    for e in app.state.registry.entries:
        if e.model == upstream_model:
            return e.lane
    return "?"


def _all_fail(model, body, n):
    return httpx.Response(429, json={"error": {"message": "pool congested"}})


@pytest.mark.parametrize("stream", [False, True])
def test_depth_cap_bounds_total_attempts(tmp_path, stream):
    app, calls = build_recording_app(tmp_path, _all_fail)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("summarise this paragraph about cats",
                                       stream=stream),
                        headers=auth("hermes"))
        if not stream:
            assert r.status_code == 429
        else:
            assert "rate_limited" in r.text
        rec = app.state.telemetry.recent(0)[-1]
    assert len(calls) <= MAX_UPSTREAM_ATTEMPTS
    assert any(s.startswith("fallback_depth_cap") for s in rec.signals) \
        or len(calls) < MAX_UPSTREAM_ATTEMPTS


@pytest.mark.parametrize("stream", [False, True])
def test_congested_free_hands_to_paid_within_cap(tmp_path, stream):
    """Every free entry is congested (429); paid serves. The free pass
    stops after cap - reserve attempts and the spillway serves inside
    the cap; premium is never called."""
    holder = {}

    def handler(model, body, n):
        lane = _lane_of(holder["app"], model)
        if lane in ("private-free", "public-only-free"):
            return _all_fail(model, body, n)
        if lane == "premium":
            raise AssertionError("premium must not be called on congestion")
        return _ok(model, body, n)

    app, calls = build_recording_app(tmp_path, handler)
    holder["app"] = app
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("summarise this paragraph about cats",
                                       stream=stream),
                        headers=auth("hermes"))
        assert r.status_code == 200, r.text
        rec = app.state.telemetry.recent(0)[-1]
    assert rec.lane == "paid"
    assert len(calls) <= MAX_UPSTREAM_ATTEMPTS
    free_calls = [c for c in calls
                  if _lane_of(app, c["model"]) in ("private-free", "public-only-free")]
    # Free-first is kept: paid is only reached after free attempts.
    assert free_calls and _lane_of(app, calls[0]["model"]) in (
        "private-free", "public-only-free")


def test_depth_cap_env_override(tmp_path, monkeypatch):
    app, calls = build_recording_app(tmp_path, _all_fail)
    app.state.cfg.max_upstream_attempts = 3
    with TestClient(app) as client:
        client.post("/v1/chat/completions",
                    json=chat_body("summarise this paragraph about cats"),
                    headers=auth("hermes"))
    assert len(calls) <= 3


def test_first_success_unaffected(tmp_path):
    app, calls = build_recording_app(tmp_path, _ok)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("summarise this paragraph about cats"),
                        headers=auth("hermes"))
        assert r.status_code == 200
        rec = app.state.telemetry.recent(0)[-1]
    assert len(calls) == 1
    assert not any(s.startswith("fallback_depth") for s in rec.signals)


# ----------------------------------------------------------------------
# 3. no premium on congestion
# ----------------------------------------------------------------------
@pytest.mark.parametrize("who", ["openclaw", "hermes"])
@pytest.mark.parametrize("stream", [False, True])
def test_congestion_never_reaches_premium(tmp_path, who, stream):
    holder = {}

    def handler(model, body, n):
        if _lane_of(holder["app"], model) == "premium":
            raise AssertionError("premium called on congestion")
        return _all_fail(model, body, n)

    app, calls = build_recording_app(tmp_path, handler)
    holder["app"] = app
    app.state.cfg.max_upstream_attempts = 60   # let every pass run fully
    with TestClient(app) as client:
        client.post("/v1/chat/completions",
                    json=chat_body("restart the nginx container and report the status",
                                   stream=stream),
                    headers={**auth(who), **L4})
        rec = app.state.telemetry.recent(0)[-1]
    assert all(_lane_of(app, c["model"]) != "premium" for c in calls)
    assert any(s.get("reason") == "premium_not_required_congestion"
               for s in rec.skipped)


def test_premium_still_serves_when_request_requires_it(tmp_path):
    """openclaw L5: premium is permitted by the request itself."""
    holder = {}

    def handler(model, body, n):
        if _lane_of(holder["app"], model) == "premium":
            return _ok(model, body, n)
        return _all_fail(model, body, n)

    app, calls = build_recording_app(tmp_path, handler)
    holder["app"] = app
    app.state.cfg.max_upstream_attempts = 60
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("design the full migration plan for the cluster"),
                        headers={**auth("openclaw"), **L5})
        assert r.status_code == 200, r.text
    assert r.headers["X-Sovereign-Lane"] == "premium"


def test_premium_kept_when_no_paid_can_meet_floor(tmp_path):
    """Capability, not congestion: when no paid entry clears the floor,
    premium stays available in the spillway (pre-v0.10.5 behaviour)."""
    data = json.loads((SRC / "registry.json").read_text())
    for m in data["models"]:
        if m["lane"] != "premium":
            m["quality"] = {k: 3 for k in m["quality"]}
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(data))
    app, calls = build_recording_app(tmp_path, _ok, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("restart the nginx container and report the status"),
                        headers={**auth("hermes"), **L4})
        assert r.status_code == 200, r.text
    assert r.headers["X-Sovereign-Lane"] == "premium"


@pytest.mark.parametrize("stream", [False, True])
def test_free_only_lanes_reserve_attempts_for_spillway(tmp_path, stream):
    """A client whose first pass is free-only (the live hermes/n8n lane
    sets carry public-only-free + private-free and reach paid through
    the spillway): with every free pool congested the free pass stops at
    cap - reserve and the paid spillway serves inside the cap."""
    holder = {}

    def handler(model, body, n):
        lane = _lane_of(holder["app"], model)
        if lane in ("private-free", "public-only-free"):
            return _all_fail(model, body, n)
        if lane == "premium":
            raise AssertionError("premium must not be called on congestion")
        return _ok(model, body, n)

    app, calls = build_recording_app(tmp_path, handler)
    holder["app"] = app
    for c in app.state.clients.clients.values() if isinstance(
            app.state.clients.clients, dict) else app.state.clients.clients:
        if c.id == "n8n":
            c.lanes = ("private-free", "public-only-free")
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("summarise this paragraph about cats",
                                       stream=stream),
                        headers=auth("n8n"))
        assert r.status_code == 200, r.text
        rec = app.state.telemetry.recent(0)[-1]
    free_calls = [c for c in calls
                  if _lane_of(app, c["model"]) in ("private-free", "public-only-free")]
    assert len(free_calls) == MAX_UPSTREAM_ATTEMPTS - SPILLWAY_RESERVED_ATTEMPTS
    assert rec.lane == "paid"
    assert len(calls) <= MAX_UPSTREAM_ATTEMPTS
    assert any(s.startswith("fallback_depth_reserve_spillway") for s in rec.signals)
    assert "free_exhausted_paid_fallthrough" in rec.escalation_reasons
