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
    # + 2 Hugging Face probe entries (2026-11-09, v0.5.7)
    # + 3 provider additions (2026-11-12: cerebras, novita, zai/glm-4.7-flash)
    # + 5 entries added staged 2026-11-12 — all five enabled the same
    # day once their keys landed (mistral reclassified to the paid lane:
    # its free tier issues no API keys; owner funded $10 PAYG instead).
    # + 20 free-catalog expansion entries (2026-11-12, owner's order):
    # 15 enabled at deploy, 5 shipped disabled with dated probe-failure
    # notes (google-direct 500s, CF glm-5.3-flash not on the Free plan,
    # inkling harness-gated, OR gemma x2 upstream-throttled).
    # + 39 expansion phase-2 entries (2026-11-12, owner's order):
    # 36 enabled, 3 disabled (OR kimi-k2.6 reasoning-burn, meta
    # muse-spark-1.3 no-content probes, google gemma-4-26b-it
    # inconsistency); every paid price verified from the provider's
    # own published source.
    assert len(reg.entries) == 81
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
    # (The sibling HF/Cerebras entry was disabled 2026-11-11 for the
    # passthrough reasoning fault and deleted 2026-11-12; Flash stays.)
    hf = by_id["huggingface/deepseek-v4-flash"]
    assert hf.enabled is True and hf.spillway_sensitive_excluded is True
    assert hf.effective_base_url == "https://router.huggingface.co/v1"
    assert hf.lane == "paid"
    # The HF/Cerebras route was DELETED 2026-11-12 on the owner's order
    # (reasoning-control fault via the HF passthrough, 2026-11-11; the
    # direct cerebras/ entry replaced it). Guard against re-adding it:
    # Cerebras is called DIRECT only — see ROUTING-PRINCIPLES.
    assert "huggingface/gpt-oss-120b-cerebras" not in by_id
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
    assert spark.lane == "premium" and spark.enabled is True
    assert (spark.input_usd_per_million, spark.output_usd_per_million) == (1.25, 4.25)
    # SiliconFlow slot 2026-11-07 (v0.5.6): GLM-5.3 in the paid lane;
    # V4 Pro present but disabled. Both are flagged
    # spillway_sensitive_excluded (posture UNCLEAR — probe).
    # PRICE CORRECTION 2026-11-12 (expansion phase 2): the original
    # seeds were read from OpenRouter's SF endpoint listing and are
    # exactly half of SiliconFlow's OWN published prices; the entries
    # now carry SF's own figures ($1.40/$0.26/$4.40 for GLM-5.3 —
    # identical to Z.ai direct, so the "half of Z.ai direct" claim is
    # dead — and $1.50/$0.135/$3.135 for V4 Pro).
    sf = by_id["siliconflow/glm-5.3"]
    assert sf.lane == "paid" and sf.enabled is True
    assert sf.model == "zai-org/GLM-5.3"
    assert (sf.input_usd_per_million, sf.output_usd_per_million) == (1.40, 4.40)
    assert sf.cached_input_usd_per_million == pytest.approx(0.26)
    assert sf.effective_base_url == "https://api.siliconflow.com/v1"
    assert sf.spillway_sensitive_excluded is True
    assert sf.quality == glm.quality  # same model as zai/glm-5.3
    sfp = by_id["siliconflow/deepseek-v4-pro"]
    assert sfp.lane == "premium" and sfp.enabled is False
    assert sfp.model == "deepseek-ai/DeepSeek-V4-Pro"
    assert (sfp.input_usd_per_million, sfp.output_usd_per_million) == (1.50, 3.135)
    assert sfp.cached_input_usd_per_million == pytest.approx(0.135)
    assert sfp.spillway_sensitive_excluded is True
    # the flag is provider-wide for the five third-party-host
    # providers (the 2026-11-09 HF probe entries are flagged as well;
    # 2026-11-12: cerebras + novita flagged on the same grounds).
    assert all(e.spillway_sensitive_excluded
               for e in reg.entries
               if e.provider in ("siliconflow", "huggingface",
                                  "cerebras", "novita", "mistral"))
    # expansion phase 2 (2026-11-12) extended the flag per-entry to
    # the new aggregator/direct-paid routes on the brief's explicit
    # conservative direction — exactly these ids, nothing else.
    phase2_flagged = {
        "openrouter/deepseek-v4-flash", "openrouter/glm-5.3-flash",
        "openrouter/qwen3.8-flash", "openrouter/gemini-3.1-flash-lite",
        "openrouter/llama-4-maverick", "openrouter/claude-haiku-5.5",
        "openrouter/kimi-k2.6", "zai/glm-4.6", "zai/glm-5.3-flash",
    }
    assert all(not e.spillway_sensitive_excluded
               for e in reg.entries
               if e.provider not in ("siliconflow", "huggingface",
                                      "cerebras", "novita", "mistral")
               and e.id not in phase2_flagged)
    assert all(by_id[i].spillway_sensitive_excluded
               for i in phase2_flagged)

    # every 2026-11-12 addition is live; nothing remains staged
    assert all(by_id[i].enabled for i in
               ("google/gemini-3.5-flash-lite", "cloudflare/gpt-oss-120b",
                "cloudflare/glm-4.7-flash", "mistral/mistral-large",
                "mistral/codestral"))
    assert by_id["mistral/mistral-large"].lane == "paid"
    assert by_id["mistral/codestral"].lane == "paid"

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
