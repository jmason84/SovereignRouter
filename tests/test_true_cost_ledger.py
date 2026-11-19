"""v0.8.3 — true-cost ledger (owner order 2026-11-13: "ADJUST THE
LEDGER TO THE ACTUAL AMOUNT", reversing his 2026-11-12 ruling that the
cache-pricing fix was not necessary).

The defect: the gateway never read providers' cached-token counts —
route rows carried cached_tokens=0 on cache-served turns and every
cache-heavy turn was priced as a full-price miss (proven 2026-11-12
against the provider consoles: DeepSeek 30-day actual $9.34 vs the
ledger's $109.32 for V4 Pro on Nov 11 alone, ~12x overstatement).

This file proves:
  * the usage parser reads every provider shape (OpenAI details,
    DeepSeek hit/miss, Anthropic cache reads — aggregator AND native,
    Google usageMetadata), in isolation and mixed;
  * absent/garbage usage degrades to the pre-v0.8.3 behaviour
    (cached=0) and never breaks a response;
  * pricing math: cached subset at the cached rate, cached > input
    clamped, no published cached rate -> full input rate, a published
    0.0 cached rate -> cache reads are free;
  * end-to-end through the gateway, JSON and streaming: the route row
    AND the quota usage event carry the parsed cached count and the
    cache-priced cost.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from gateway import (  # noqa: E402
    GatewayConfig, _cached_tokens_from_usage, _usage_counts,
    _usage_from_response, create_app,
)
from registry import ModelEntry  # noqa: E402

from conftest import RAW_KEYS, auth, chat_body  # noqa: E402


def _entry(**kw) -> ModelEntry:
    base = dict(id="test/model", provider="test", model="model",
                lane="paid", quality={"chat": 3}, context_tokens=131072,
                input_usd_per_million=1.0, output_usd_per_million=2.0,
                cached_input_usd_per_million=0.1)
    base.update(kw)
    return ModelEntry(**base)


# ----------------------------------------------------------------------
# parser: provider usage shapes
# ----------------------------------------------------------------------

def test_openai_details_shape():
    usage = {"prompt_tokens": 1000, "completion_tokens": 50,
             "prompt_tokens_details": {"cached_tokens": 800}}
    assert _usage_counts(usage, est_input=1) == (1000, 50, 800)


def test_deepseek_hit_miss_shape():
    # DeepSeek reports hit and miss separately; hit + miss == prompt.
    usage = {"prompt_tokens": 30000, "completion_tokens": 120,
             "prompt_cache_hit_tokens": 29440,
             "prompt_cache_miss_tokens": 560}
    assert _usage_counts(usage, est_input=1) == (30000, 120, 29440)


def test_deepseek_all_miss_is_zero_cached():
    usage = {"prompt_tokens": 500, "completion_tokens": 10,
             "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 500}
    assert _usage_counts(usage, est_input=1) == (500, 10, 0)


def test_anthropic_aggregator_shape():
    # OpenRouter-normalised Claude usage: prompt_tokens INCLUDES the
    # cached count, reported under both names (equal values) — the
    # parser must take one, not sum them into a double count.
    usage = {"prompt_tokens": 2000, "completion_tokens": 30,
             "prompt_tokens_details": {"cached_tokens": 1500},
             "cache_read_input_tokens": 1500}
    assert _usage_counts(usage, est_input=1) == (2000, 30, 1500)


def test_anthropic_native_shape_folds_reads_into_input():
    # Native Anthropic shape: input_tokens EXCLUDES cache reads and
    # creations. The ledger's invariant is cached ⊆ input, so reads
    # and creations fold into the input total (creations price as
    # fresh input — the registry carries no cache-write rate).
    usage = {"input_tokens": 400, "output_tokens": 25,
             "cache_read_input_tokens": 1500,
             "cache_creation_input_tokens": 100}
    assert _usage_counts(usage, est_input=1) == (2000, 25, 1500)


def test_google_usage_metadata_shape():
    usage = {"usageMetadata": {"promptTokenCount": 4000,
                               "candidatesTokenCount": 60,
                               "cachedContentTokenCount": 3900,
                               "totalTokenCount": 4060}}
    assert _usage_counts(usage, est_input=1) == (4000, 60, 3900)


def test_google_top_level_usage_metadata_response():
    resp = {"choices": [{"message": {"content": "ok"}}],
            "usageMetadata": {"promptTokenCount": 100,
                              "candidatesTokenCount": 5,
                              "cachedContentTokenCount": 90}}
    in_tok, out_tok, cached, _ = _usage_from_response(resp, 1, _entry())
    assert (in_tok, out_tok, cached) == (100, 5, 90)


def test_absent_usage_degrades_to_estimate_and_zero_cached():
    assert _usage_counts({}, est_input=777) == (777, 0, 0)
    assert _usage_counts(None, est_input=777) == (777, 0, 0)
    assert _cached_tokens_from_usage({}) == 0
    assert _cached_tokens_from_usage(None) == 0


def test_garbage_usage_never_raises():
    usage = {"prompt_tokens": "lots", "completion_tokens": None,
             "prompt_tokens_details": "not-a-dict",
             "prompt_cache_hit_tokens": [1, 2, 3]}
    in_tok, out_tok, cached = _usage_counts(usage, est_input=42)
    assert (in_tok, out_tok, cached) == (42, 0, 0)


def test_cached_clamped_to_input():
    # A provider reporting more cached than prompt tokens must not
    # produce a negative fresh count (or a negative cost).
    usage = {"prompt_tokens": 100, "completion_tokens": 5,
             "prompt_tokens_details": {"cached_tokens": 250}}
    assert _usage_counts(usage, est_input=1) == (100, 5, 100)


# ----------------------------------------------------------------------
# pricing math (registry.estimate_cost — the one pricing path)
# ----------------------------------------------------------------------

def test_pricing_cached_subset_at_cached_rate():
    e = _entry()  # 1.0 in / 0.1 cached / 2.0 out
    # 200 fresh + 800 cached + 50 out
    expected = (200 * 1.0 + 800 * 0.1 + 50 * 2.0) / 1e6
    assert e.estimate_cost(1000, 50, cached_tokens=800) == pytest.approx(expected)


def test_pricing_no_published_cached_rate_is_full_price():
    e = _entry(cached_input_usd_per_million=None)
    assert e.estimate_cost(1000, 50, cached_tokens=800) == \
        pytest.approx(e.estimate_cost(1000, 50, cached_tokens=0))


def test_pricing_zero_cached_rate_is_free_reads():
    e = _entry(cached_input_usd_per_million=0.0)
    expected = (200 * 1.0 + 50 * 2.0) / 1e6
    assert e.estimate_cost(1000, 50, cached_tokens=800) == pytest.approx(expected)


def test_pricing_cached_above_input_clamps():
    e = _entry()
    assert e.estimate_cost(100, 5, cached_tokens=250) == \
        pytest.approx(e.estimate_cost(100, 5, cached_tokens=100))


# ----------------------------------------------------------------------
# end-to-end: canned provider usage through the gateway
# ----------------------------------------------------------------------

DEEPSEEK_USAGE = {"prompt_tokens": 30000, "completion_tokens": 120,
                  "total_tokens": 30120,
                  "prompt_cache_hit_tokens": 29440,
                  "prompt_cache_miss_tokens": 560}
DEEPSEEK_ENTRY_RATES = (1.32, 0.044, 3.96)  # in / cached / out, peak


def _expected_cost(usage, rates, cached_key):
    in_r, cached_r, out_r = rates
    cached = usage[cached_key]
    return ((usage["prompt_tokens"] - cached) * in_r
            + cached * cached_r
            + usage["completion_tokens"] * out_r) / 1e6


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _build(tmp_path, handler):
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha(RAW_KEYS["admin"]),
        "clients": [
            {"id": "openclaw", "key_sha256": _sha(RAW_KEYS["openclaw"]),
             "lanes": ["private-free", "paid", "premium"],
             "premium_allowed": True, "daily_target_usd": 3.0},
        ],
    }))
    cfg = GatewayConfig(
        state_dir=tmp_path / "state",
        registry_path=SRC / "registry.json",
        clients_path=clients_file,
        provider_keys={"deepseek": "fake", "openrouter": "fake"},
    )
    app = create_app(cfg, upstream_transport=httpx.MockTransport(handler))
    return app, tmp_path / "state"


def _route_rows(state_dir: Path) -> list[dict]:
    log = state_dir / "routes.jsonl"
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


def _deepseek_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body.get("stream"):
        def chunk(delta, finish=None, usage=None):
            c = {"id": "x", "object": "chat.completion.chunk",
                 "model": body["model"],
                 "choices": [{"index": 0, "delta": delta,
                              "finish_reason": finish}]}
            if usage is not None:
                c["usage"] = usage
            return f"data: {json.dumps(c)}\n\n"
        sse = (chunk({"role": "assistant", "content": "Hello "})
               + chunk({"content": "world"})
               + chunk({}, finish="stop", usage=DEEPSEEK_USAGE)
               + "data: [DONE]\n\n")
        return httpx.Response(200, content=sse.encode(),
                              headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={
        "id": "chatcmpl-ds", "object": "chat.completion",
        "model": body["model"],
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "ok"}}],
        "usage": DEEPSEEK_USAGE,
    })


def _check_ledger(app, state_dir, usage, rates, cached_key):
    rows = _route_rows(state_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["cached_tokens"] == usage[cached_key]
    assert row["input_tokens"] == usage["prompt_tokens"]
    assert row["cost_usd"] == pytest.approx(
        _expected_cost(usage, rates, cached_key), rel=1e-9)
    now = time.time()
    usage_today = app.state.store.client_usage("openclaw", now - 60, now + 60)
    assert sum(m["cached_tokens"] for m in usage_today["by_model"]) == \
        usage[cached_key]
    assert usage_today["total_cost_usd"] == pytest.approx(row["cost_usd"])


def test_e2e_json_deepseek_usage_priced_at_cache_rate(tmp_path):
    app, state_dir = _build(tmp_path, _deepseek_handler)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("reconcile the ledger",
                                   model="deepseek/deepseek-v4-pro"),
                    headers=auth("openclaw"))
    assert r.status_code == 200
    _check_ledger(app, state_dir, DEEPSEEK_USAGE, DEEPSEEK_ENTRY_RATES,
                  "prompt_cache_hit_tokens")


def test_e2e_stream_deepseek_usage_priced_at_cache_rate(tmp_path):
    app, state_dir = _build(tmp_path, _deepseek_handler)
    with TestClient(app) as tc:
        with tc.stream("POST", "/v1/chat/completions",
                       json=chat_body("reconcile the ledger", stream=True,
                                      model="deepseek/deepseek-v4-pro"),
                       headers=auth("openclaw")) as r:
            body = "".join(r.iter_text())
    assert "Hello " in body and "world" in body  # two SSE chunks
    _check_ledger(app, state_dir, DEEPSEEK_USAGE, DEEPSEEK_ENTRY_RATES,
                  "prompt_cache_hit_tokens")


def test_e2e_stream_openai_details_usage(tmp_path):
    or_usage = {"prompt_tokens": 5000, "completion_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 4500}}
    # openrouter/deepseek-v4-flash: 0.09 in / 0.018 cached / 0.18 out
    rates = (0.09, 0.018, 0.18)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        def chunk(delta, finish=None, usage=None):
            c = {"id": "x", "object": "chat.completion.chunk",
                 "model": body["model"],
                 "choices": [{"index": 0, "delta": delta,
                              "finish_reason": finish}]}
            if usage is not None:
                c["usage"] = usage
            return f"data: {json.dumps(c)}\n\n"
        sse = (chunk({"role": "assistant", "content": "Hi"})
               + chunk({}, finish="stop", usage=or_usage)
               + "data: [DONE]\n\n")
        return httpx.Response(200, content=sse.encode(),
                              headers={"content-type": "text/event-stream"})

    app, state_dir = _build(tmp_path, handler)
    with TestClient(app) as tc:
        with tc.stream("POST", "/v1/chat/completions",
                       json=chat_body("hi", stream=True,
                                      model="openrouter/deepseek-v4-flash"),
                       headers=auth("openclaw")) as r:
            "".join(r.iter_text())
    rows = _route_rows(state_dir)
    assert len(rows) == 1
    assert rows[0]["cached_tokens"] == 4500
    expected = ((5000 - 4500) * rates[0] + 4500 * rates[1]
                + 40 * rates[2]) / 1e6
    assert rows[0]["cost_usd"] == pytest.approx(expected, rel=1e-9)
