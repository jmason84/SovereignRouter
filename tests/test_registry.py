"""Registry v2 loading/validation, cached pricing, and the 413 contract
(only when no eligible model can ever fit)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import SRC, auth, build_app, chat_body
from registry import load_registry, validate_entry


def test_starting_registry_matches_spec_table():
    reg = load_registry(SRC / "registry.json")
    assert len(reg.entries) == 8
    by_id = {e.id: e for e in reg.entries}
    assert by_id["groq/gpt-oss-20b"].quality_for("chat") == 2
    assert by_id["groq/gpt-oss-120b"].quality_for("infra") == 4
    assert by_id["groq/qwen3.8-27b"].quality_for("chat") == 3
    assert by_id["openrouter/north-mini-code-free"].extra_body["provider"]["data_collection"] == "deny"
    # 2026-11-04: north-mini's hidden reasoning expands to consume any
    # budget on most code prompts (finish=length, zero content at every
    # budget 512-8192; provider ignores reasoning caps) — the registry
    # disables reasoning for it so it answers directly.
    assert by_id["openrouter/north-mini-code-free"].extra_body["reasoning"]["enabled"] is False
    assert by_id["openrouter/dots-3-note-preview-free"].context_tokens == 512 * 1024
    di = by_id["deepinfra/deepseek-v4-flash"]
    assert (di.input_usd_per_million, di.output_usd_per_million) == (0.09, 0.18)
    assert di.cached_input_usd_per_million == pytest.approx(0.018)  # 20% cached
    assert di.spend_alert_usd == 10.0 and di.lane == "paid"
    ol = by_id["ollama/gpt-oss-120b"]
    assert (ol.input_usd_per_million, ol.output_usd_per_million) == (0.15, 0.60)
    assert ol.spend_alert_usd == 5.0
    assert by_id["premium/gpt-5.5"].lane == "premium"
    assert by_id["premium/gpt-5.5"].quality_for("reasoning") == 5


def test_cached_pricing_math():
    reg = load_registry(SRC / "registry.json")
    di = reg.get("deepinfra/deepseek-v4-flash")
    # 1M fresh input + 1M cached input + 1M output
    cost = di.estimate_cost(2_000_000, 1_000_000, cached_tokens=1_000_000)
    assert cost == pytest.approx(0.09 + 0.018 + 0.18)


def test_validate_entry_rejects_bad_lane_and_quality():
    base = {"id": "x/y", "provider": "x", "model": "y", "lane": "paid",
            "quality": {"chat": 3}, "context_tokens": 1000}
    with pytest.raises(ValueError):
        validate_entry({**base, "lane": "free-ish"})
    with pytest.raises(ValueError):
        validate_entry({**base, "quality": {"chat": 9}})
    with pytest.raises(ValueError):
        validate_entry({**base, "quality": {"telepathy": 3}})


def test_413_only_when_no_model_can_ever_fit(tmp_path):
    # mini registry: a single 1k-context model
    mini = {"models": [{
        "id": "test/tiny", "provider": "groq", "model": "openai/gpt-oss-20b",
        "lane": "private-free", "quality": {"chat": 3, "infra": 3, "code": 3,
                                            "reasoning": 3, "long-context": 3},
        "context_tokens": 1000}]}
    reg_path = tmp_path / "mini-registry.json"
    reg_path.write_text(json.dumps(mini))
    app = build_app(tmp_path / "a", registry_path=reg_path)
    with TestClient(app) as tc:
        big = "word " * 3000  # ~3000 tokens > 1000 context
        r = tc.post("/v1/chat/completions", json=chat_body(big),
                    headers=auth("hermes"))
        assert r.status_code == 413
        # a fitting request still succeeds on the same app
        r = tc.post("/v1/chat/completions", json=chat_body("hi"),
                    headers=auth("hermes"))
        assert r.status_code == 200
