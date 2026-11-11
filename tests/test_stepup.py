"""Failure-triggered step-up (plan 3.2) and precise tool-error detection."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import gateway
from conftest import auth, build_app


@pytest.mark.parametrize("text,expected", [
    ("Error: connection refused", True),
    ("Traceback (most recent call last):\n  File x", True),
    ('{"error": "boom"}', True),
    ('{"ok": false}', True),
    ('{"status": "failed"}', True),
    ('{"is_error": true}', True),
    ('{"error": null, "data": 1}', False),     # healthy JSON used to count
    ('{"result": "no errors found"}', False),
    ("ok, 3 rows", False),
    ("", False),
    ("[1, 2, 3]", False),
])
def test_tool_result_is_error(text, expected):
    assert gateway.tool_result_is_error(text) is expected


def _loop(tool_text):
    return {"model": "auto", "messages": [
        {"role": "user", "content": "hello, please check the thing"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "lookup", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": tool_text}],
        "tools": [{"type": "function", "function": {
            "name": "lookup", "parameters": {"type": "object", "properties": {}}}}]}


def _last(app):
    return app.state.telemetry.recent(0)[-1]


def test_stepup_off_by_default(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/v1/chat/completions", json=_loop("Error: boom"),
                      headers=auth("hermes")).status_code == 200
    assert "failure_stepup" not in _last(app).signals
    assert "tool_error_next_turn" in _last(app).signals


def test_stepup_raises_floor_after_tool_error(tmp_path):
    base_app = build_app(tmp_path / "a")
    app = build_app(tmp_path / "b", failure_stepup=True)
    with TestClient(base_app) as c0, TestClient(app) as c1:
        c0.post("/v1/chat/completions", json=_loop("Error: boom"), headers=auth("hermes"))
        c1.post("/v1/chat/completions", json=_loop("Error: boom"), headers=auth("hermes"))
        c1.post("/v1/chat/completions", json=_loop('{"error": null}'), headers=auth("hermes"))
    base, stepped = _last(base_app), None
    recs = app.state.telemetry.recent(0)
    assert "failure_stepup" in recs[0].signals
    assert "failure_stepup" not in recs[1].signals      # clean turn: back to normal
    reg = app.state.registry
    q = lambda r: reg.get(r.model).quality_for(r.task_class)
    assert q(recs[0]) >= q(base)
    assert recs[0].lane != "premium"                    # never forces premium
