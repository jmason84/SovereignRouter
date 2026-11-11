"""Phase 1 (2026-11-11): honest failure labels, capacity-gap early
warning, estimate-fallback signal, dead-lane visibility."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import gateway
from conftest import SRC, auth, build_app, chat_body, registry_variant


def _all_ids():
    return [m["id"] for m in json.loads((SRC / "registry.json").read_text())["models"]]


# ---- 1.1 empty candidate set: policy vs availability ---------------------
def test_policy_empty_is_not_labelled_rate_limited(tmp_path):
    reg = registry_variant(tmp_path, disable=_all_ids())
    app = build_app(tmp_path, registry_path=reg)
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", json=chat_body("hello"),
                   headers=auth("hermes"))
    assert r.status_code == 429          # status kept for client compatibility
    err = r.json()["error"]
    assert err["code"] == "no_eligible_model"
    assert "Retry-After" not in r.headers
    assert "disabled" in r.json()["detail"]["skip_summary"]
    assert "rate-limited" not in err["message"]


def test_transient_empty_keeps_rate_limited_and_retry_after():
    resp = gateway.no_candidates_response(
        [{"model": "a", "reason": "rate_limited_tpm"},
         {"model": "b", "reason": "disabled"}], 3, "chat")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    assert json.loads(resp.body)["error"]["code"] == "rate_limited"


# ---- 1.2 capacity gap -------------------------------------------------------
def _gap_alerts(app):
    return [a for a in app.state.store.alerts_since(0) if a["kind"] == "capacity_gap"]


def test_capacity_gap_alerts_when_one_provider_remains(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "CAPACITY_GAP_MIN_TOKENS", 100)
    keep = {"groq/gpt-oss-120b"}
    reg = registry_variant(tmp_path, disable=[i for i in _all_ids() if i not in keep])
    app = build_app(tmp_path, registry_path=reg)
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions",
                   json=chat_body("word " * 400), headers=auth("hermes"))
    assert r.status_code == 200
    alerts = _gap_alerts(app)
    assert len(alerts) == 1
    assert "groq" in alerts[0]["message"] and "not blocked" in alerts[0]["message"]


def test_no_capacity_gap_with_redundancy_or_small_payload(tmp_path, monkeypatch):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        c.post("/v1/chat/completions", json=chat_body("hi"), headers=auth("hermes"))
        monkeypatch.setattr(gateway, "CAPACITY_GAP_MIN_TOKENS", 100)
        c.post("/v1/chat/completions", json=chat_body("word " * 400),
               headers=auth("hermes"))
    assert _gap_alerts(app) == []


# ---- 1.3 estimate fallback signal --------------------------------------------
def test_estimate_fallback_is_recorded(client, app, monkeypatch):
    monkeypatch.setattr(gateway, "_ENCODING_FAILED", True)
    monkeypatch.setattr(gateway, "_encode_cache", gateway.OrderedDict())
    r = client.post("/v1/chat/completions", json=chat_body("hello there"),
                    headers=auth("hermes"))
    assert r.status_code == 200
    rec = app.state.telemetry.recent(0)[-1]
    assert "token_estimate_fallback" in rec.signals


def test_encode_cache_is_used_for_large_text(monkeypatch):
    calls = []
    monkeypatch.setattr(gateway, "_encode_cache", gateway.OrderedDict())
    monkeypatch.setattr(gateway, "_encode_len_raw",
                        lambda t: calls.append(1) or 7)
    big = "x" * 5000
    assert gateway._encode_len(big) == 7
    assert gateway._encode_len(big) == 7
    assert len(calls) == 1
    gateway._encode_len("short")
    gateway._encode_len("short")
    assert len(calls) == 3               # small strings are not cached


def test_special_token_text_does_not_force_fallback():
    before = gateway.ESTIMATE_FALLBACKS
    gateway.estimate_input_tokens(
        {"messages": [{"role": "user", "content": "<|endoftext|> hello"}]})
    if not gateway._ENCODING_FAILED:      # tokenizer available in this env
        assert gateway.ESTIMATE_FALLBACKS == before


# ---- 1.4 dead lanes visible ----------------------------------------------------
def test_dead_lane_visible_then_cleared(app, client):
    entry = app.state.registry.entries[0]
    gateway.note_lane_dead(app, entry, "upstream_402")
    assert client.get("/health").json()["dead_lane_count"] == 1
    costs = client.get("/admin/costs", headers=auth("admin")).json()
    assert costs["dead_lanes"][entry.id]["cause"] == "upstream_402"
    gateway.note_lane_alive(app, entry)
    assert client.get("/health").json()["dead_lane_count"] == 0
