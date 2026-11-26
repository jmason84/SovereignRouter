"""Upstream error-body capture on failed attempts (2026-11-25 census).

The past-week error census found the gateway discards the one piece of
evidence most failure diagnosis needs: the upstream error body. It is
passed into _record_attempt for classification and then thrown away —
attempt_events had no body column, the gateway has no application
logger, and routes.jsonl records only `upstream_400`. The chronic
google/gemini-3.1-flash-lite 400s (112 in the census window, daily
since Nov 13) were undiagnosable from stored data for exactly this
reason; the root cause only surfaced when a live probe happened to
return the body to the client.

v0.10.1: a failed attempt stores a bounded (<=500 chars), scrubbed
copy of the upstream error body in attempt_events.error_body. Served
attempts store nothing. Scrubbing redacts credential-shaped material
before persistence; the column is observability only — classification
and selection never read it.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from conftest import auth, chat_body  # noqa: E402
from gateway import _scrub_error_body  # noqa: E402
from quota import AttemptEvent, QuotaStore  # noqa: E402
from test_gemini_thought_signature import build_google_app  # noqa: E402

T0 = time.time()
GEMINI = "google/gemini-3.1-flash-lite"


# ----------------------------------------------------------------------
# store round-trip + migration
# ----------------------------------------------------------------------

def test_error_body_roundtrip(tmp_path):
    store = QuotaStore(tmp_path / "quota.db")
    store.record_attempt(AttemptEvent(
        ts=T0, client_id="hermes", provider="google", model_id=GEMINI,
        pool_id="google:gemini-3.1-flash-lite", outcome="failed",
        status=400, failure_class="request_fault", scope="request",
        scope_target="-",
        error_body='{"error": {"message": "thought_signature missing"}}'))
    store.record_attempt(AttemptEvent(
        ts=T0 + 1, client_id="hermes", provider="google", model_id=GEMINI,
        pool_id="google:gemini-3.1-flash-lite", outcome="served",
        status=200))
    rows = store.attempts_since(0)
    assert "thought_signature missing" in rows[0]["error_body"]
    assert rows[1]["error_body"] is None


def test_error_body_migration_on_pre_v0101_db(tmp_path):
    # A quota.db from the v0.10.0 schema (telemetry columns present,
    # no error_body) gains the column additively; old rows read NULL.
    db = tmp_path / "quota.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE attempt_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
        " client_id TEXT NOT NULL, provider TEXT NOT NULL,"
        " model_id TEXT NOT NULL, pool_id TEXT NOT NULL,"
        " outcome TEXT NOT NULL, status INTEGER, failure_class TEXT,"
        " scope TEXT, scope_target TEXT, would_demote TEXT,"
        " fallback_depth INTEGER NOT NULL DEFAULT 0,"
        " provider_cost_usd REAL, reasoning_tokens INTEGER)")
    conn.execute(
        "INSERT INTO attempt_events(ts, client_id, provider, model_id,"
        " pool_id, outcome) VALUES(?, 'hermes', 'google',"
        " 'google/gemini-3.1-flash-lite', 'google:x', 'served')", (T0,))
    conn.commit()
    conn.close()
    store = QuotaStore(db)
    rows = store.attempts_since(0)
    assert len(rows) == 1
    assert rows[0]["error_body"] is None


# ----------------------------------------------------------------------
# scrub + bound (pure helper)
# ----------------------------------------------------------------------

def test_scrub_redacts_credentials_and_bounds_length():
    body = ('{"error": "Authorization: Bearer abcdefghij1234567890 '
            'failed for api_key=sk-TESTKEY0123456789abcdef"}')
    out = _scrub_error_body(body)
    assert "abcdefghij1234567890" not in out
    assert "sk-TESTKEY0123456789abcdef" not in out
    assert "[redacted]" in out
    long_body = "x" * 5000
    assert len(_scrub_error_body(long_body)) <= 500
    assert _scrub_error_body("") is None
    assert _scrub_error_body(None) is None


# ----------------------------------------------------------------------
# gateway integration
# ----------------------------------------------------------------------

def test_gateway_captures_upstream_error_body(tmp_path):
    def handler(model, body, call_no):
        if model == "gemini-3.1-flash-lite":
            return httpx.Response(400, json={"error": {
                "message": "Function call is missing a thought_signature"
                           " in functionCall parts",
                "status": "INVALID_ARGUMENT"}})
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15}})

    app, _calls = build_google_app(tmp_path, handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("weather please", model=GEMINI),
                        headers=auth("hermes"))
    # The cascade falls through to a sibling and serves (200) — the
    # point of this test is what the LEDGER kept from the 400.
    assert r.status_code == 200
    rows = [x for x in app.state.store.attempts_since(0)
            if x["model_id"] == GEMINI]
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "failed"
    assert row["failure_class"] == "request_fault"
    assert row["error_body"] is not None
    assert "thought_signature" in row["error_body"]


def test_gateway_served_attempt_stores_no_body(tmp_path):
    def handler(model, body, call_no):
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15}})

    app, _calls = build_google_app(tmp_path, handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("weather please", model=GEMINI),
                        headers=auth("hermes"))
    assert r.status_code == 200
    rows = app.state.store.attempts_since(0)
    assert rows and all(x["error_body"] is None for x in rows)
