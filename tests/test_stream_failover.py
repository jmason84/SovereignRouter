"""Empty / reasoning-only streams fail over instead of serving a blank 200
(v0.5.9; 2026-11-11 dots-3 incident)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from conftest import FREE_MODELS, auth, build_app, chat_body


def _stream(c, text, who="hermes"):
    r = c.post("/v1/chat/completions",
               json=chat_body(text, stream=True), headers=auth(who))
    assert r.status_code == 200
    chunks = [json.loads(l[5:]) for l in r.text.splitlines()
              if l.startswith("data:") and "[DONE]" not in l]
    content = "".join(
        ((ch.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
        for ch in chunks)
    return r, content


def test_empty_free_stream_fails_over_and_client_sees_only_the_good_answer(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        r, content = _stream(c, "EMPTY_STREAM please answer")
    assert content.strip(), "client got a blank answer"
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.model not in FREE_MODELS          # served by a different model
    assert any(s.startswith("stream_empty_failover:") for s in rec.signals)
    assert any(x["reason"].startswith("validation_failed:") for x in rec.skipped)
    assert rec.escalated is True
    # nothing from the failed attempt leaked to the client
    assert "thinking..." not in r.text


def test_empty_stream_drops_sticky_binding(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        _stream(c, "EMPTY_STREAM first turn")
        # the binding now points at the model that actually answered
        assert len(app.state.sticky) == 1
        entry = next(iter(app.state.sticky._entries.values()))
        assert entry.model_id not in FREE_MODELS


def test_all_models_empty_still_terminates_cleanly(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions",
                   json=chat_body("EMPTY_STREAM_ALL nothing works", stream=True),
                   headers=auth("hermes"))
    assert r.status_code == 200
    assert r.text.rstrip().endswith("data: [DONE]")      # no hang, no crash


def test_normal_stream_unchanged(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        _, content = _stream(c, "hello there")
    assert content == "Hello world"
    rec = app.state.telemetry.recent(0)[-1]
    assert not any(s.startswith("stream_empty_failover") for s in rec.signals)
