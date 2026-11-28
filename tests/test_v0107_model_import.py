"""v0.10.7 model import (Jordan's order 2026-11-27 23:06 MST: "pull the
relevant models both free and not into ours"). Registry-only change:
every imported entry ships DISABLED, so routing is unchanged until a
probe passes and Jordan rules. These tests pin that contract."""
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from registry import load_registry  # noqa: E402

REG = load_registry(SRC / "registry.json")
BY_ID = {e.id: e for e in REG.entries}

PENDING_PROBE = {
    "deepinfra/qwen3-coder-480b-turbo", "deepinfra/qwen3-coder-30b",
    "deepinfra/deepseek-v4.1-flash", "deepinfra/nemotron-3-ultra",
    "deepinfra/kimi-k2.6", "huggingface/qwen3-coder-480b",
    "nvidia/nemotron-3-ultra", "nvidia/kimi-k3.1-pro",
}
PENDING_KEY = {
    "orcarouter/deepseek-v4.1-flash-free", "orcarouter/glm-5.3-flash-free",
    "kilo/auto-free", "kilo/beam-free",
}
IMPORTED = PENDING_PROBE | PENDING_KEY
# Providers with a key file on VM103 (names only, read 2026-11-27).
KEYED_PROVIDERS = {
    "aion", "cerebras", "cloudflare", "deepinfra", "deepseek", "google",
    "groq", "huggingface", "jina", "meta", "mistral", "novita", "nrouter",
    "nvidia", "ollama", "openai", "openrouter", "siliconflow", "unorouter",
    "voyage", "zai",
}


def test_all_imported_entries_present():
    assert IMPORTED <= set(BY_ID)


def test_imported_entries_ship_disabled():
    for i in IMPORTED:
        assert BY_ID[i].enabled is False, i


def test_enabled_set_unchanged_by_import():
    # 98 pre-import entries, 83 enabled (v0.10.6 / Job D tree).
    assert sum(1 for e in REG.entries if e.enabled) == 83
    assert not any(BY_ID[i].enabled for i in IMPORTED)


def test_pending_markers_match_key_presence():
    for i in PENDING_PROBE:
        e = BY_ID[i]
        assert e.provider in KEYED_PROVIDERS, i
        assert "PENDING-PROBE" in e.notes, i
    for i in PENDING_KEY:
        e = BY_ID[i]
        assert e.provider not in KEYED_PROVIDERS, i
        assert "PENDING-KEY" in e.notes, i


def test_keyless_providers_carry_explicit_base_url():
    for i in PENDING_KEY:
        assert BY_ID[i].base_url and BY_ID[i].base_url.startswith("https://"), i


def test_free_imports_are_public_lane_and_zero_priced():
    for i in IMPORTED:
        e = BY_ID[i]
        if e.lane == "public-only-free":
            assert e.input_usd_per_million == 0 and e.output_usd_per_million == 0, i
            assert not e.spillway_sensitive_excluded, i


def test_paid_imports_carry_verified_dated_prices():
    for i in IMPORTED:
        e = BY_ID[i]
        if e.lane == "paid":
            assert e.price_confidence == "verified", i
            assert e.price_verified_at == "2026-11-27", i
            assert e.spend_alert_usd and e.spend_alert_usd > 0, i


def test_no_imported_tooling_score_without_battery():
    for i in IMPORTED:
        assert "tooling" not in BY_ID[i].quality, i
        assert not BY_ID[i].tooling_source, i


def test_hf_import_pinned_and_non_sensitive():
    e = BY_ID["huggingface/qwen3-coder-480b"]
    assert e.model.endswith(":deepinfra")
    assert e.spillway_sensitive_excluded is True


def test_nvidia_imports_record_evaluation_terms():
    for i in ("nvidia/nemotron-3-ultra", "nvidia/kimi-k3.1-pro"):
        assert "evaluation" in BY_ID[i].notes.lower(), i
        assert BY_ID[i].lane == "public-only-free", i


def test_deprecated_or_sunset_candidates_not_imported():
    # Cloudflare kimi-k2.6 (paid-only, deprecated 2026-11-20) and ZenMux
    # deepseek-v4-flash-free (Sunset) were verified dead ends.
    models = {(e.provider, e.model) for e in REG.entries}
    assert ("cloudflare", "@cf/moonshotai/kimi-k2.6") not in models
    assert not any(e.provider == "zenmux" for e in REG.entries)


def test_registry_file_is_valid_json_with_new_count():
    data = json.loads((SRC / "registry.json").read_text(encoding="utf-8"))
    assert len(data["models"]) == 110
