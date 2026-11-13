"""v0.8.0 — pool ledger & failure memory (Cycle 1 aspect E, release R2).

Data layer, OBSERVE-ONLY: the attempt ledger records every upstream
attempt (serves included, failures classified), pools are derived
from the evaluation taxonomy, and the demotion tracker computes what
selection WOULD demote. Nothing here may change a routing decision —
the final test in this file proves selection ignores the tracker.
Enforcement is decision J-2, still the owner's.

Shapes covered (from the handoff evaluations): TEST-03 (model-scoped
429 stays model-scoped), TEST-04 (pool-exhaustion signal scopes to
the whole OpenRouter :free pool incl. the virtual router's inherited
membership), TEST-05 (Cloudflare neuron accounting), TEST-09
(half-open bookkeeping at the ledger level), TEST-13 (the Nov 9
census replay: repeated 402s converge on ACCOUNT scope immediately,
not after 369 attempts).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from conftest import auth, build_app, chat_body  # noqa: E402
from pools import (  # noqa: E402
    DemotionTracker, classify_failure, pool_for,
)
from quota import AttemptEvent, QuotaStore  # noqa: E402
from registry import ModelEntry, load_registry  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402

REG = load_registry(SRC / "registry.json")
BY_ID = {e.id: e for e in REG.entries}
T0 = 1_799_000_000.0  # fixed clock for tracker tests


def _entry(entry_id: str) -> ModelEntry:
    return BY_ID[entry_id]


def _fail(entry, status, body="", headers=None, transport=False):
    return classify_failure(entry=entry, status=status, body_text=body,
                            headers=headers, transport=transport)


# ----------------------------------------------------------------------
# pool model: every entry maps to exactly one pool
# ----------------------------------------------------------------------

def test_pool_derivation_covers_registry_exactly_once():
    pools = {e.id: pool_for(e) for e in REG.entries}
    assert len(pools) == len(REG.entries) == 81
    assert all(p.id and p.unit in ("requests", "tokens", "neurons")
               for p in pools.values())


def test_openrouter_free_pool_membership_and_inheritance():
    members = {e.id for e in REG.entries
               if pool_for(e).id == "openrouter-free"}
    expected = {e.id for e in REG.entries
                if e.provider == "openrouter"
                and (e.model.endswith(":free") or e.id == "openrouter/free-router")}
    assert members == expected
    assert len(members) == 14
    # NEW-01: the virtual router inherits the pool.
    assert "openrouter/free-router" in members
    assert pool_for(_entry("openrouter/free-router")).unit == "requests"
    # Paid OpenRouter is a separate class, never the free pool.
    assert pool_for(_entry("openrouter/deepseek-v4-flash")).id == "openrouter-paid"


def test_cloudflare_neuron_pool_and_google_buckets_and_groq_tokens():
    cf = pool_for(_entry("cloudflare/gpt-oss-120b"))
    assert cf.id == "cloudflare-workers-ai" and cf.unit == "neurons"
    g1 = pool_for(_entry("google/gemini-3.5-flash-lite"))
    g2 = pool_for(_entry("google/gemini-3.1-flash-lite"))
    assert g1.id != g2.id  # per-model buckets are separate capacity
    assert pool_for(_entry("groq/gpt-oss-120b")).unit == "tokens"


# ----------------------------------------------------------------------
# taxonomy
# ----------------------------------------------------------------------

def test_taxonomy_status_map():
    e = _entry("deepinfra/deepseek-v4-flash")
    assert _fail(e, 401).failure_class == "account_dead"
    assert _fail(e, 401).scope == "account"
    assert _fail(e, 402).failure_class == "account_dead"
    assert _fail(e, 403).scope == "account"
    assert _fail(e, 500).failure_class == "transient"
    assert _fail(e, None, transport=True).failure_class == "transient"
    assert _fail(e, 400).failure_class == "request_fault"
    assert _fail(e, 400).scope == "request"
    assert _fail(e, 413).failure_class == "request_fault"
    assert _fail(e, 422).failure_class == "request_fault"
    assert _fail(e, 404).failure_class == "entry_missing"
    assert _fail(e, 404).scope == "model"


def test_openrouter_in_flight_402_is_transient_not_account_death():
    e = _entry("openrouter/deepseek-v4-flash")
    body = json.dumps({"error": {"code": 402, "message": "concurrency",
                                 "metadata": {"limit_source": "in_flight_budget"}}})
    f = _fail(e, 402, body, headers={"retry-after": "30"})
    assert f.failure_class == "transient"
    assert f.scope == "model"
    assert f.retry_after_s == 30.0
    # A plain OpenRouter 402 (no limit_source) is still account death.
    assert _fail(e, 402, "{}").failure_class == "account_dead"


# ----------------------------------------------------------------------
# TEST-03: a bare model 429 stays model-scoped
# ----------------------------------------------------------------------

def test_test03_bare_429_stays_model_scoped():
    tracker = DemotionTracker()
    e = _entry("openrouter/dots-3-note-preview-free")
    ann = tracker.observe(entry=e, failure=_fail(e, 429, "{}"), now=T0)
    assert len(ann) == 1
    assert ann[0]["scope"] == "model" and ann[0]["target"] == e.id
    demoted = tracker.demoted_entry_ids(REG.entries, T0)
    assert demoted == {e.id}
    # Siblings in the same (healthy) pool are unaffected.
    assert "openrouter/nemotron-3-ultra-free" not in demoted
    assert "openrouter/free-router" not in demoted


# ----------------------------------------------------------------------
# TEST-04: a pool-exhaustion signal scopes to the whole pool
# ----------------------------------------------------------------------

def test_test04_pool_exhaustion_scopes_to_whole_pool():
    tracker = DemotionTracker()
    e = _entry("openrouter/nemotron-3-ultra-free")
    body = json.dumps({"error": {"message":
                                 "Rate limit exceeded: free-models-per-day"}})
    f = _fail(e, 429, body)
    assert f.failure_class == "congested" and f.scope == "pool"
    ann = tracker.observe(entry=e, failure=f, now=T0)
    assert ann[0]["target"] == "openrouter-free"
    assert ann[0]["reset_source"] == "documented_daily_reset"
    assert ann[0]["until"] is not None and ann[0]["until"] > T0
    demoted = tracker.demoted_entry_ids(REG.entries, T0)
    expected = {x.id for x in REG.entries
                if x.provider == "openrouter"
                and (x.model.endswith(":free") or x.id == "openrouter/free-router")}
    assert demoted == expected  # incl. the virtual router's inheritance
    # The annotation persists across subsequent requests until reset.
    assert tracker.would_demote(T0 + 3600) == ann or \
        tracker.would_demote(T0 + 3600)[0]["target"] == "openrouter-free"


def test_cloudflare_4006_is_pool_scoped():
    e = _entry("cloudflare/gpt-oss-120b")
    body = json.dumps({"errors": [{"code": 4006, "message":
                                   "daily free allocation exhausted"}]})
    f = _fail(e, 429, body)
    assert f.scope == "pool" and f.target == "cloudflare-workers-ai"
    # A bare Cloudflare 429 without the signal stays model-scoped.
    assert _fail(e, 429, "{}").scope == "model"


# ----------------------------------------------------------------------
# TEST-05: Cloudflare neuron accounting
# ----------------------------------------------------------------------

def test_test05_neuron_pool_accounting_marks_exhaustion():
    tracker = DemotionTracker()
    e = _entry("cloudflare/gpt-oss-120b")
    ann = tracker.observe(entry=e, failure=None, now=T0, units=6000.0)
    assert ann == []
    ann = tracker.observe(entry=e, failure=None, now=T0 + 10, units=4500.0)
    assert len(ann) == 1
    assert ann[0]["scope"] == "pool"
    assert ann[0]["target"] == "cloudflare-workers-ai"
    # Neuron exhaustion is congestion on a free pool — never mislabeled
    # as account death, and there is no paid overage to confuse it with.
    assert ann[0]["failure_class"] == "congested"
    # Observed reset behavior contradicts the documented midnight
    # reset, so the demotion runs until a successful probe.
    assert ann[0]["until"] is None
    assert ann[0]["reset_source"] == "observed_divergent"
    assert tracker.pool_day_units("cloudflare-workers-ai", T0) == 10500.0


# ----------------------------------------------------------------------
# TEST-09: half-open bookkeeping at the ledger level
# ----------------------------------------------------------------------

def test_test09_half_open_probe_cycle():
    tracker = DemotionTracker()
    e = _entry("deepinfra/deepseek-v4-flash")
    f = _fail(e, 429, "{}", headers={"retry-after": "10"})
    tracker.observe(entry=e, failure=f, now=T0)
    key = ("model", e.id)
    assert tracker.state_for(*key, now=T0) == "demoted"
    # Before the probe point: no probe admitted.
    assert tracker.admit_probe(*key, now=T0 + 5) is False
    # At the probe point: half-open, exactly one probe.
    assert tracker.admit_probe(*key, now=T0 + 10) is True
    assert tracker.state_for(*key, now=T0 + 10) == "half_open"
    assert tracker.admit_probe(*key, now=T0 + 10) is False
    # Probe success closes the scope.
    tracker.observe(entry=e, failure=None, now=T0 + 11)
    assert tracker.state_for(*key, now=T0 + 11) == "closed"
    assert tracker.would_demote(T0 + 11) == []

    # Probe failure re-demotes with a doubled cooldown.
    tracker.observe(entry=e, failure=f, now=T0 + 20)
    first_until = tracker._scopes[key].demoted_until
    tracker.admit_probe(*key, now=T0 + 30)
    tracker.observe(entry=e, failure=f, now=T0 + 31)
    second = tracker._scopes[key]
    assert second.state == "demoted"
    assert second.cooldown_s > (first_until - (T0 + 20))


# ----------------------------------------------------------------------
# TEST-13: Nov 9 census replay — 402s converge on account scope at once
# ----------------------------------------------------------------------

def test_test13_repeated_402s_converge_on_account_scope():
    tracker = DemotionTracker()
    e = _entry("deepinfra/deepseek-v4-flash")
    first_ann = None
    for i in range(369):
        ann = tracker.observe(entry=e, failure=_fail(e, 402, "{}"),
                              now=T0 + i)
        if i == 0:
            first_ann = ann
    # Convergence: the FIRST attempt already names the account scope —
    # the annotation set never grows past it.
    assert first_ann and first_ann[0]["scope"] == "account"
    assert first_ann[0]["target"] == "deepinfra"
    active = tracker.would_demote(T0 + 369)
    assert {(a["scope"], a["target"]) for a in active} == \
        {("account", "deepinfra")}
    demoted = tracker.demoted_entry_ids(REG.entries, T0 + 369)
    assert demoted == {x.id for x in REG.entries if x.provider == "deepinfra"}


def test_request_faults_never_demote():
    tracker = DemotionTracker()
    e = _entry("deepinfra/deepseek-v4-flash")
    for status in (400, 413, 422):
        ann = tracker.observe(entry=e, failure=_fail(e, status), now=T0)
        assert ann == []
    assert tracker.would_demote(T0) == []


# ----------------------------------------------------------------------
# attempt ledger store
# ----------------------------------------------------------------------

def test_attempt_ledger_roundtrip(tmp_path):
    store = QuotaStore(tmp_path / "quota.db")
    store.record_attempt(AttemptEvent(
        ts=T0, client_id="hermes", provider="deepinfra",
        model_id="deepinfra/deepseek-v4-flash", pool_id="deepinfra:x",
        outcome="failed", status=402, failure_class="account_dead",
        scope="account", scope_target="deepinfra",
        would_demote='[{"action":"would_demote"}]'))
    store.record_attempt(AttemptEvent(
        ts=T0 + 1, client_id="hermes", provider="groq",
        model_id="groq/gpt-oss-120b", pool_id="groq:openai/gpt-oss-120b",
        outcome="served", status=200))
    rows = store.attempts_since(0)
    assert len(rows) == 2
    assert rows[0]["failure_class"] == "account_dead"
    assert rows[0]["scope_target"] == "deepinfra"
    assert rows[1]["outcome"] == "served"
    counts = store.attempt_counts(0)
    assert counts["failed:account_dead"] == 1 and counts["served"] == 1


# ----------------------------------------------------------------------
# gateway integration: attempts land in the ledger; selection ignores it
# ----------------------------------------------------------------------

def test_gateway_records_failed_and_served_attempts(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat/completions",
            json=chat_body("FAIL::openai/gpt-oss-120b please route this",
                           model="groq/gpt-oss-120b"),
            headers=auth("hermes"))
    assert r.status_code == 200  # failover served the turn
    assert r.headers["X-Sovereign-Model"] != "groq/gpt-oss-120b"
    rows = app.state.store.attempts_since(0)
    failed = [x for x in rows if x["outcome"] == "failed"]
    served = [x for x in rows if x["outcome"] == "served"]
    assert len(failed) == 1 and len(served) == 1
    assert failed[0]["model_id"] == "groq/gpt-oss-120b"
    assert failed[0]["status"] == 429
    assert failed[0]["failure_class"] == "congested"
    assert failed[0]["scope"] == "model"
    assert failed[0]["pool_id"] == "groq:openai/gpt-oss-120b"


def test_gateway_account_dead_attempt_annotates_route_record(tmp_path):
    def handler(model, body, call_no):
        if model == "openai/gpt-oss-120b":
            return httpx.Response(402, json={"error": {"message": "no credits"}})
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15}})

    app, _calls = build_recording_app(tmp_path, handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("route this please",
                                       model="groq/gpt-oss-120b"),
                        headers=auth("hermes"))
    assert r.status_code == 200
    rec = app.state.telemetry.recent(0)[-1]
    assert any(a["scope"] == "account" and a["target"] == "groq"
               for a in rec.would_demote)
    rows = [x for x in app.state.store.attempts_since(0)
            if x["outcome"] == "failed"]
    assert rows and rows[0]["failure_class"] == "account_dead"
    assert json.loads(rows[0]["would_demote"])[0]["target"] == "groq"


def test_selection_ignores_demotion_state_observe_only(tmp_path):
    # The same model that just 429'd (and was annotated would_demote)
    # serves the very next clean request: v0.8.0 changes NO routing.
    app = build_app(tmp_path)
    with TestClient(app) as client:
        r1 = client.post(
            "/v1/chat/completions",
            json=chat_body("FAIL::openai/gpt-oss-120b first attempt",
                           model="groq/gpt-oss-120b"),
            headers=auth("hermes"))
        assert r1.status_code == 200
        assert app.state.demotions.would_demote(time.time()) != []
        r2 = client.post(
            "/v1/chat/completions",
            json=chat_body("second attempt, clean",
                           model="groq/gpt-oss-120b"),
            headers=auth("hermes"))
    assert r2.status_code == 200
    assert r2.headers["X-Sovereign-Model"] == "groq/gpt-oss-120b"
