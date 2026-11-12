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
    # 8 spec entries + 3 premium-lane additions (2026-11-05, v0.5.4)
    # + 2 SiliconFlow entries (2026-11-07, v0.5.6)
    # + 2 Hugging Face probe entries (2026-11-09, v0.5.7).
    assert len(reg.entries) == 15
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
    # 2026-11-09 correction: the 400k/day TPD was a router-side seed,
    # not an Ollama limit (owner console: 50 requests / $0.07 Nov 1-9).
    assert ol.limits["tpd"] == 10_000_000
    # Groq Developer plan 2026-11-09 (owner top-up; figures from
    # Groq's docs models page): 250K TPM / 1K RPM and real prices —
    # the lane stays private-free (data posture), prices are carried
    # for spend accounting.
    g120 = by_id["groq/gpt-oss-120b"]
    assert (g120.input_usd_per_million, g120.output_usd_per_million) == (0.15, 0.60)
    assert g120.limits == {"rpm": 1000, "tpm": 250000}
    g20 = by_id["groq/gpt-oss-20b"]
    assert (g20.input_usd_per_million, g20.output_usd_per_million) == (0.075, 0.30)
    assert g20.limits == {"rpm": 1000, "tpm": 250000}
    qw = by_id["groq/qwen3.8-27b"]
    assert (qw.input_usd_per_million, qw.output_usd_per_million) == (0.80, 4.00)
    # Hugging Face entries (2026-11-09): probe-proven and ENABLED on
    # the owner's order; still flagged non-sensitive-only, paid lane.
    # 2026-11-11: the Cerebras gpt-oss-120b route was DISABLED after it
    # leaked deliberation / drifted tool args on Hermes sessions
    # (Hermes-agent E-10); it stays in the file, flagged, for re-test.
    for hf_id, expected_enabled in (("huggingface/gpt-oss-120b-cerebras", False),
                                    ("huggingface/deepseek-v4-flash", True)):
        hf = by_id[hf_id]
        assert hf.enabled is expected_enabled and hf.spillway_sensitive_excluded is True
        assert hf.effective_base_url == "https://router.huggingface.co/v1"
        assert hf.lane == "paid"
    assert by_id["huggingface/gpt-oss-120b-cerebras"].model == \
        "openai/gpt-oss-120b:cerebras"
    assert by_id["huggingface/deepseek-v4-flash"].model == \
        "deepseek-ai/DeepSeek-V4-Flash:deepinfra"
    assert by_id["premium/gpt-5.5"].lane == "premium"
    assert by_id["premium/gpt-5.5"].quality_for("reasoning") == 5
    # Premium-lane expansion 2026-11-05 (direct providers, peak-rate
    # accounting for DeepSeek; Meta deployed disabled — billing 402).
    ds = by_id["deepseek/deepseek-v4-pro"]
    assert ds.lane == "premium" and ds.enabled is True
    assert (ds.input_usd_per_million, ds.output_usd_per_million) == (1.32, 3.96)
    assert ds.cached_input_usd_per_million == pytest.approx(0.044)
    assert ds.effective_base_url == "https://api.deepseek.com"
    glm = by_id["zai/glm-5.3"]
    assert glm.lane == "premium" and glm.enabled is True
    assert (glm.input_usd_per_million, glm.output_usd_per_million) == (1.40, 4.40)
    assert glm.effective_base_url == "https://api.z.ai/api/paas/v4"
    spark = by_id["meta/muse-spark-1.1"]
    assert spark.lane == "premium" and spark.enabled is False
    assert (spark.input_usd_per_million, spark.output_usd_per_million) == (1.25, 4.25)
    # SiliconFlow slot 2026-11-07 (v0.5.6): GLM-5.3 in the paid lane
    # at half of Z.ai direct; V4 Pro present but disabled. Both are
    # flagged spillway_sensitive_excluded (posture UNCLEAR — probe).
    sf = by_id["siliconflow/glm-5.3"]
    assert sf.lane == "paid" and sf.enabled is True
    assert sf.model == "zai-org/GLM-5.3"
    assert (sf.input_usd_per_million, sf.output_usd_per_million) == (0.70, 2.20)
    assert sf.cached_input_usd_per_million == pytest.approx(0.13)
    assert sf.effective_base_url == "https://api.siliconflow.com/v1"
    assert sf.spillway_sensitive_excluded is True
    assert sf.quality == glm.quality  # same model as zai/glm-5.3
    sfp = by_id["siliconflow/deepseek-v4-pro"]
    assert sfp.lane == "premium" and sfp.enabled is False
    assert sfp.model == "deepseek-ai/DeepSeek-V4-Pro"
    assert (sfp.input_usd_per_million, sfp.output_usd_per_million) == (1.00, 1.50)
    assert sfp.cached_input_usd_per_million == pytest.approx(0.135)
    assert sfp.spillway_sensitive_excluded is True
    # the flag defaults off everywhere else (the 2026-11-09 HF probe
    # entries are flagged as well — third-party-routed, posture varies)
    assert all(not e.spillway_sensitive_excluded
               for e in reg.entries
               if e.provider not in ("siliconflow", "huggingface"))


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
