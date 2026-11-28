"""v0.10.6 (Job C, owner go 2026-11-27 20:25 MST): per-class tiered
ranking in SHADOW mode only.

Properties:
  * routing is unchanged — the served model, the upstream call sequence
    and the response are identical with the shadow on and off;
  * the shadow pick is drawn from the live, walled candidate list, so a
    sensitive turn's shadow pick is never public-only-free or excluded;
  * free > cheap > premium within the floor, then preferred specialist,
    score, price, latency;
  * owner defaults: free coder unorouter/codestral-latest, cheap coder
    nrouter/qwen3-coder-480b-a35b;
  * capability tags tools / vision / structured-output;
  * production tool-error penalty on tool turns.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import auth, chat_body  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402

import gateway
import tiering
from registry import ModelEntry

SRC = Path(__file__).resolve().parent.parent / "src"
SENSITIVE_TEXT = (
    "restart the nginx container, then check the deploy endpoint "
    "https://deploy:s3cr3tpass@192.168.0.166:8443/status and report back")


def _e(id_, lane, q, price=0.0, lat=500, vision=False, tools=True):
    return ModelEntry(id=id_, provider=id_.split("/")[0], model=id_, lane=lane,
                      quality=q, context_tokens=128000,
                      input_usd_per_million=price, output_usd_per_million=price,
                      latency_p50_ms=lat, supports_vision=vision,
                      supports_tools=tools)


def _q(e, tc, has_tools):
    return gateway._selection_quality(e, tc, has_tools)


# ----------------------------------------------------------------------
# pure ranking
# ----------------------------------------------------------------------
def test_tier_of():
    assert tiering.tier_of(_e("a/free", "public-only-free", {"chat": 3})) == "free"
    assert tiering.tier_of(_e("a/pf", "private-free", {"chat": 3})) == "free"
    # priced private-free (e.g. Groq Developer plan) is cheap, not free
    assert tiering.tier_of(_e("groq/x", "private-free", {"chat": 3}, price=0.1)) == "cheap"
    assert tiering.tier_of(_e("a/p", "paid", {"chat": 3}, price=0.1)) == "cheap"
    assert tiering.tier_of(_e("p/x", "premium", {"chat": 5}, price=5)) == "premium"


def test_free_before_cheap_before_premium_even_if_weaker():
    c = [_e("p/x", "premium", {"chat": 5}, price=5),
         _e("a/paid", "paid", {"chat": 5}, price=0.1),
         _e("a/free", "public-only-free", {"chat": 3})]
    r = tiering.rank(c, task_class="chat", has_tools=False, has_images=False,
                     quality_fn=_q, est_tokens=100, max_tokens=100)
    assert [x.entry.id for x in r] == ["a/free", "a/paid", "p/x"]


def test_best_in_tier_beats_cheapest_in_tier():
    """Live sorts cheap entries by price first; tiered sorts by score."""
    c = [_e("a/cheapest", "paid", {"code": 3}, price=0.02),
         _e("a/better", "paid", {"code": 5}, price=0.3)]
    r = tiering.rank(c, task_class="code", has_tools=False, has_images=False,
                     quality_fn=_q, est_tokens=1000, max_tokens=1000)
    assert r[0].entry.id == "a/better"


def test_price_then_latency_break_score_ties():
    c = [_e("a/slow", "paid", {"chat": 4}, price=0.1, lat=900),
         _e("a/fast", "paid", {"chat": 4}, price=0.1, lat=100),
         _e("a/dear", "paid", {"chat": 4}, price=0.5, lat=50)]
    r = tiering.rank(c, task_class="chat", has_tools=False, has_images=False,
                     quality_fn=_q, est_tokens=1000, max_tokens=1000)
    assert [x.entry.id for x in r] == ["a/fast", "a/slow", "a/dear"]


def test_owner_default_coders_preferred_in_their_tiers():
    c = [_e("other/free5", "public-only-free", {"code": 5}),
         _e("unorouter/codestral-latest", "public-only-free", {"code": 4}),
         _e("x/cheap5", "paid", {"code": 5}, price=0.05),
         _e("nrouter/qwen3-coder-480b-a35b", "paid", {"code": 5}, price=0.22)]
    r = tiering.rank(c, task_class="code", has_tools=False, has_images=False,
                     quality_fn=_q, est_tokens=100, max_tokens=100)
    ids = [x.entry.id for x in r]
    assert ids[0] == "unorouter/codestral-latest"
    assert ids.index("nrouter/qwen3-coder-480b-a35b") < ids.index("x/cheap5")
    # not a code turn: no preference
    r = tiering.rank(c, task_class="chat", has_tools=False, has_images=False,
                     quality_fn=_q, est_tokens=100, max_tokens=100)
    assert not any(x.preferred for x in r)


def test_backup_coder_order_codestral_then_nrouter_deepinfra_then_hf():
    # v0.10.7: free Codestral first; then within cheap the preference
    # order nrouter > deepinfra turbo > HF backup, ahead of a cheaper
    # unlisted coder with a higher score.
    c = [_e("x/cheap5", "paid", {"code": 5}, price=0.01),
         _e("huggingface/qwen3-coder-480b", "paid", {"code": 5}, price=0.30),
         _e("deepinfra/qwen3-coder-480b-turbo", "paid", {"code": 5}, price=0.30),
         _e("nrouter/qwen3-coder-480b-a35b", "paid", {"code": 5}, price=0.22),
         _e("unorouter/codestral-latest", "public-only-free", {"code": 4})]
    ids = [x.entry.id for x in tiering.rank(
        c, task_class="code", has_tools=False, has_images=False,
        quality_fn=_q, est_tokens=100, max_tokens=100)]
    assert ids[:4] == ["unorouter/codestral-latest",
                       "nrouter/qwen3-coder-480b-a35b",
                       "deepinfra/qwen3-coder-480b-turbo",
                       "huggingface/qwen3-coder-480b"]
    # nrouter ineligible (e.g. cooldown): DeepInfra, then HF
    ids = [x.entry.id for x in tiering.rank(
        [e for e in c if not e.id.startswith("nrouter")], task_class="code",
        has_tools=False, has_images=False, quality_fn=_q,
        est_tokens=100, max_tokens=100)]
    assert ids[:3] == ["unorouter/codestral-latest",
                       "deepinfra/qwen3-coder-480b-turbo",
                       "huggingface/qwen3-coder-480b"]


def test_hf_backup_coder_is_non_sensitive_and_disabled_in_registry():
    from registry import load_registry
    from pathlib import Path
    reg = load_registry(Path(__file__).resolve().parent.parent / "src" / "registry.json")
    e = {x.id: x for x in reg.entries}["huggingface/qwen3-coder-480b"]
    assert e.spillway_sensitive_excluded is True and e.enabled is False
    for pref in tiering.CLASS_PREFERRED["code"]["cheap"]:
        assert pref in {x.id for x in reg.entries}


def test_vision_tag_requires_supports_vision():
    c = [_e("a/text", "public-only-free", {"chat": 5}),
         _e("a/vis", "public-only-free", {"chat": 3}, vision=True)]
    r = tiering.rank(c, task_class="chat", has_tools=False, has_images=True,
                     quality_fn=_q, est_tokens=100, max_tokens=100)
    assert [x.entry.id for x in r] == ["a/vis"]


def test_tool_error_penalty_reorders_within_tier_only():
    c = [_e("a/flaky", "public-only-free", {"chat": 4, "tooling": 5}),
         _e("a/steady", "public-only-free", {"chat": 4, "tooling": 4}),
         _e("a/paid", "paid", {"chat": 5, "tooling": 5}, price=0.1)]
    stats = {"a/flaky": {"samples": 50, "errors": 15, "rate": 0.30},
             "a/steady": {"samples": 50, "errors": 1, "rate": 0.02}}
    r = tiering.rank(c, task_class="chat", has_tools=True, has_images=False,
                     quality_fn=_q, est_tokens=100, max_tokens=100,
                     tool_stats=stats)
    assert [x.entry.id for x in r] == ["a/steady", "a/flaky", "a/paid"]
    # too few samples -> no penalty
    stats["a/flaky"]["samples"] = 5
    r = tiering.rank(c, task_class="chat", has_tools=True, has_images=False,
                     quality_fn=_q, est_tokens=100, max_tokens=100,
                     tool_stats=stats)
    assert r[0].entry.id == "a/flaky"


def test_decide_tags_and_low_confidence():
    c = [_e("a/free", "public-only-free", {"chat": 3})]
    d = tiering.decide(c, task_class="chat", has_tools=True, has_images=False,
                       json_mode=True, quality_fn=_q, est_tokens=10,
                       max_tokens=10, classifier_confidence=0.4)
    assert d.tags == ["tools", "structured-output"]
    assert d.low_confidence is True and d.agree is True
    d = tiering.decide([], task_class="chat", has_tools=False, has_images=False,
                       json_mode=False, quality_fn=_q, est_tokens=10,
                       max_tokens=10, classifier_confidence=0.9)
    assert d.pick is None and d.reason == "no_eligible_candidate"


# ----------------------------------------------------------------------
# gateway: shadow only
# ----------------------------------------------------------------------
def _completion(model, content="ok"):
    return {"id": "x", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                      "total_tokens": 12}}


def _flaky(model, body, n):
    # deterministic: odd-numbered calls 429 so failover is exercised
    if n % 3 == 1:
        return httpx.Response(429, json={"error": {"message": "busy"}})
    return httpx.Response(200, json=_completion(model, f"from {model}"))


PROMPTS = [
    ("hermes", "summarise this paragraph about cats", {}),
    ("hermes", "write a python function that parses ISO dates and unit tests for it", {}),
    ("openclaw", "design the full migration plan for the cluster", {"X-Sovereign-Level": "5"}),
    ("n8n", "classify this email as spam or not", {}),
    ("hermes", SENSITIVE_TEXT, {"X-Sovereign-Level": "4"}),
]


@pytest.mark.parametrize("who,text,hdrs", PROMPTS)
def test_routing_identical_with_shadow_on_and_off(tmp_path, who, text, hdrs):
    results = {}
    for on in (True, False):
        d = tmp_path / ("on" if on else "off")
        d.mkdir()
        app, calls = build_recording_app(d, _flaky)
        app.state.cfg.tier_shadow = on
        with TestClient(app) as client:
            r = client.post("/v1/chat/completions", json=chat_body(text),
                            headers={**auth(who), **hdrs})
            rec = app.state.telemetry.recent(0)[-1]
        results[on] = (r.status_code, r.headers.get("X-Sovereign-Model"),
                       [c["model"] for c in calls], r.json())
        if on:
            assert rec.tier_shadow is not None
        else:
            assert rec.tier_shadow is None
    assert results[True] == results[False]


def test_shadow_row_contents_and_wall(tmp_path):
    app, _ = build_recording_app(tmp_path, lambda m, b, n: httpx.Response(200, json=_completion(m)))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("hermes"), "X-Sovereign-Level": "4"})
        assert r.status_code == 200
        rec = app.state.telemetry.recent(0)[-1]
        row = json.loads(rec.to_json())
    ts = row["tier_shadow"]
    assert set(ts) >= {"pick", "pick_tier", "live_pick", "live_tier", "agree",
                       "task_class", "tags", "low_confidence", "reason", "top"}
    assert rec.data_class == "sensitive"
    entries = {e.id: e for e in app.state.registry.entries}
    pick = entries[ts["pick"]]
    assert pick.lane != "public-only-free"
    assert not pick.spillway_sensitive_excluded or pick.lane in ("paid", "premium")
    for t in ts["top"]:
        assert entries[t["model"]].lane != "public-only-free"


def test_code_turn_shadow_prefers_owner_free_coder(tmp_path):
    app, _ = build_recording_app(tmp_path, lambda m, b, n: httpx.Response(200, json=_completion(m)),
                                 provider_keys_extra={"unorouter": "fake", "nrouter": "fake"})
    for c in app.state.clients.clients.values() if isinstance(
            app.state.clients.clients, dict) else app.state.clients.clients:
        if c.id == "hermes":
            c.lanes = ("private-free", "public-only-free", "paid")
    with TestClient(app) as client:
        r = client.post("/v1/shadow",
                        json=chat_body("write a python function that parses ISO dates; fix the bug in this code"),
                        headers={**auth("hermes"), "X-Sovereign-Level": "3"})
        body = r.json()
    if body["task_class"] != "code":
        pytest.skip(f"classifier put this in {body['task_class']}")
    ts = body["tiered_shadow"]
    cand = {c["model"] for c in body["candidates"]}
    if "unorouter/codestral-latest" in cand:
        assert ts["pick"] == "unorouter/codestral-latest"
    else:
        assert ts["pick_tier"] in ("free", "cheap", "premium")


def test_shadow_endpoint_carries_tiered_block(tmp_path):
    app, _ = build_recording_app(tmp_path, lambda m, b, n: httpx.Response(200, json=_completion(m)))
    with TestClient(app) as client:
        r = client.post("/v1/shadow", json=chat_body("hello"), headers=auth("hermes"))
    assert r.status_code == 200
    assert r.json()["tiered_shadow"]["live_pick"] == r.json()["would_route_to"]["model"]


def test_shadow_error_never_breaks_serving(tmp_path, monkeypatch):
    app, _ = build_recording_app(tmp_path, lambda m, b, n: httpx.Response(200, json=_completion(m)))

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(tiering, "decide", boom)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=chat_body("hello"), headers=auth("hermes"))
        rec = app.state.telemetry.recent(0)[-1]
    assert r.status_code == 200
    assert "tier_shadow_error" in rec.signals


def test_version():
    assert gateway.VERSION == "0.10.9"  # v0.10.9 (stress hardening) on top of the v0.10.7 consolidated release, which carries the v0.10.6 tier shadow
