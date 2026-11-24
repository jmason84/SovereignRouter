"""Borrowed per-attempt telemetry contract (LiteLLM hook shape,
2026-11-24; accelerates R3 / J-3(i)).

Every upstream attempt record now carries, alongside the v0.8.0
fields: fallback_depth (the attempt's 0-based ordinal within its
logical request — LiteLLM's per-attempt hooks fire per retry/fallback
with exactly this field), provider_cost_usd (the cost the PROVIDER
reported for the attempt, when it sent one — captured verbatim,
never repriced, never read by selection), and reasoning_tokens
(the upstream-reported hidden-reasoning count, when sent).

Logging-only: nothing in selection, pricing, or the governor reads
these fields, and the final test proves a provider-reported cost
does not move the turn's registry-priced cost of record.
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

from conftest import auth, build_app, chat_body  # noqa: E402
from gateway import _attempt_usage_extras  # noqa: E402
from quota import AttemptEvent, QuotaStore  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402

T0 = time.time()


# ----------------------------------------------------------------------
# store round-trip + defaults
# ----------------------------------------------------------------------

def test_attempt_extras_roundtrip(tmp_path):
    store = QuotaStore(tmp_path / "quota.db")
    store.record_attempt(AttemptEvent(
        ts=T0, client_id="hermes", provider="openrouter",
        model_id="openrouter/some-free", pool_id="openrouter-free",
        outcome="served", status=200, fallback_depth=2,
        provider_cost_usd=0.001234, reasoning_tokens=512))
    store.record_attempt(AttemptEvent(
        ts=T0 + 1, client_id="hermes", provider="groq",
        model_id="groq/gpt-oss-120b", pool_id="groq:openai/gpt-oss-120b",
        outcome="failed", status=429, failure_class="congested"))
    rows = store.attempts_since(0)
    assert rows[0]["fallback_depth"] == 2
    assert rows[0]["provider_cost_usd"] == 0.001234
    assert rows[0]["reasoning_tokens"] == 512
    # Defaults for attempts whose upstream reported nothing: depth 0,
    # NULLs — a missing report is never fabricated as a zero cost.
    assert rows[1]["fallback_depth"] == 0
    assert rows[1]["provider_cost_usd"] is None
    assert rows[1]["reasoning_tokens"] is None


def test_attempt_schema_migration_on_pre_contract_db(tmp_path):
    # A quota.db created by the pre-2026-11-24 schema (no extras
    # columns) gains them additively when the store opens it, and
    # pre-existing rows read back with the documented defaults.
    db = tmp_path / "quota.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE attempt_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
        " client_id TEXT NOT NULL, provider TEXT NOT NULL,"
        " model_id TEXT NOT NULL, pool_id TEXT NOT NULL,"
        " outcome TEXT NOT NULL, status INTEGER, failure_class TEXT,"
        " scope TEXT, scope_target TEXT, would_demote TEXT)")
    conn.execute(
        "INSERT INTO attempt_events(ts, client_id, provider, model_id,"
        " pool_id, outcome) VALUES(?, 'hermes', 'groq', 'groq/x',"
        " 'groq:x', 'served')", (T0,))
    conn.commit()
    conn.close()
    store = QuotaStore(db)
    rows = store.attempts_since(0)
    assert len(rows) == 1
    assert rows[0]["fallback_depth"] == 0
    assert rows[0]["provider_cost_usd"] is None
    assert rows[0]["reasoning_tokens"] is None


# ----------------------------------------------------------------------
# usage-extras extraction shapes
# ----------------------------------------------------------------------

def test_usage_extras_openai_and_openrouter_shapes():
    cost, reasoning = _attempt_usage_extras({
        "prompt_tokens": 100, "completion_tokens": 50, "cost": 0.0025,
        "completion_tokens_details": {"reasoning_tokens": 33}})
    assert cost == 0.0025 and reasoning == 33


def test_usage_extras_absent_and_malformed():
    assert _attempt_usage_extras(None) == (None, None)
    assert _attempt_usage_extras({}) == (None, None)
    assert _attempt_usage_extras({"cost": "not-a-number"}) == (None, None)
    assert _attempt_usage_extras(
        {"completion_tokens_details": {"reasoning_tokens": 0}}) \
        == (None, None)
    # Google native shape carries neither field.
    assert _attempt_usage_extras(
        {"promptTokenCount": 10, "candidatesTokenCount": 5}) == (None, None)


# ----------------------------------------------------------------------
# gateway integration: depth counts attempts; provider cost captured
# ----------------------------------------------------------------------

def test_gateway_fallback_depth_and_provider_cost(tmp_path):
    def handler(model, body, call_no):
        if model == "openai/gpt-oss-120b":
            return httpx.Response(
                429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15, "cost": 0.00042,
                      "completion_tokens_details":
                          {"reasoning_tokens": 7}}})

    app, _calls = build_recording_app(tmp_path, handler)
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat/completions",
            json=chat_body("route this please",
                           model="groq/gpt-oss-120b"),
            headers=auth("hermes"))
    assert r.status_code == 200
    rows = app.state.store.attempts_since(0)
    assert len(rows) == 2
    failed, served = rows[0], rows[1]
    assert failed["outcome"] == "failed"
    assert failed["fallback_depth"] == 0
    assert failed["provider_cost_usd"] is None
    assert served["outcome"] == "served"
    assert served["fallback_depth"] == 1
    assert served["provider_cost_usd"] == 0.00042
    assert served["reasoning_tokens"] == 7
    # J-3(i) is capture-only: the turn's cost of record stays the
    # registry-priced estimate, NOT the provider-reported figure.
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.cost_usd != 0.00042


def test_gateway_first_attempt_depth_zero(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat/completions",
            json=chat_body("a plain clean turn please"),
            headers=auth("hermes"))
    assert r.status_code == 200
    rows = app.state.store.attempts_since(0)
    assert rows and all(x["fallback_depth"] == 0 for x in rows)
