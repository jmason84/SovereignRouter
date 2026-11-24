"""Tests for the LiteLLM borrow tools (2026-11-24): the price-drift
tripwire (borrow #1) and the discovery tripwire (borrow #3).

Both tools are REPORT-ONLY by construction — the invariant under
test, beyond the comparisons themselves, is that neither tool can
write the registry: the registry file's bytes are asserted unchanged
across a CLI run, and drift produces a report (exit 0), never a
mutation. F-04: aggregators nominate, providers decide.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


price_check = _load_tool("price_check")
discovery = _load_tool("discovery_tripwire")

REGISTRY = {
    "models": [
        {"id": "deepinfra/deepseek-v4-flash-0731", "provider": "deepinfra",
         "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
         "input_usd_per_million": 0.06, "output_usd_per_million": 0.18,
         "cached_input_usd_per_million": 0.015},
        {"id": "openrouter/some-free", "provider": "openrouter",
         "model": "some/free", "input_usd_per_million": 0.0,
         "output_usd_per_million": 0.0},
        {"id": "nrouter/qwen3-32b", "provider": "nrouter",
         "model": "qwen3-32b", "input_usd_per_million": 0.10,
         "output_usd_per_million": 0.30},
        {"id": "groq/no-price-yet", "provider": "groq",
         "model": "unpriced/model"},
    ]
}

LITELLM = {
    "sample_spec": {"input_cost_per_token": 0.0},
    "deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731": {
        "input_cost_per_token": 0.00000006,
        "output_cost_per_token": 0.00000018,
        "cache_read_input_token_cost": 0.000000015},
    "openrouter/some-free": {
        "input_cost_per_token": 0.0, "output_cost_per_token": 0.0},
    "qwen/qwen3-32b": {
        "input_cost_per_token": 0.0000002,
        "output_cost_per_token": 0.0000006},
    "openai/brand-new-model": {
        "litellm_provider": "openai", "mode": "chat",
        "input_cost_per_token": 0.000001,
        "output_cost_per_token": 0.000004},
}


# ----------------------------------------------------------------------
# price_check
# ----------------------------------------------------------------------

def test_price_check_matches_and_reports_drift():
    summary = price_check.run_check(REGISTRY, LITELLM, tolerance=0.01)
    # The unpriced groq entry is not checked at all.
    assert summary["entries_checked"] == 3
    assert summary["matched"] == 2
    # nrouter is not in LiteLLM at all: unmatched is a fact, not drift.
    assert summary["unmatched"] == ["nrouter/qwen3-32b"]
    assert summary["drifted"] == []


def test_price_check_detects_real_drift():
    drifted_map = json.loads(json.dumps(LITELLM))
    drifted_map["deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731"][
        "input_cost_per_token"] = 0.00000012  # doubled upstream
    summary = price_check.run_check(REGISTRY, drifted_map, tolerance=0.01)
    assert len(summary["drifted"]) == 1
    row = summary["drifted"][0]
    assert row["id"] == "deepinfra/deepseek-v4-flash-0731"
    assert row["fields"]["input_usd_per_million"]["drift"] is True
    assert row["fields"]["output_usd_per_million"]["drift"] is False
    report = price_check.render_report(summary)
    assert "VERIFICATION TASKS" in report
    assert "Nothing was written to the registry" in report


def test_price_check_never_writes_registry(tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    mapf = tmp_path / "map.json"
    drifted = json.loads(json.dumps(LITELLM))
    drifted["deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731"][
        "output_cost_per_token"] = 0.0000009
    mapf.write_text(json.dumps(drifted), encoding="utf-8")
    before = reg.read_bytes()
    rc = price_check.main(["--registry", str(reg), "--map-file",
                           str(mapf), "--json"])
    assert rc == 0  # drift reports; it does not fail the run
    assert reg.read_bytes() == before
    rc = price_check.main(["--registry", str(reg), "--map-file",
                           str(mapf), "--strict"])
    assert rc == 1  # --strict is the caller's choice, still no write
    assert reg.read_bytes() == before


# ----------------------------------------------------------------------
# discovery_tripwire
# ----------------------------------------------------------------------

def test_discovery_map_diff_nominates_only_unknown_fleet_models():
    known = discovery.registry_known_ids(REGISTRY)
    providers = discovery.registry_providers(REGISTRY)
    noms = discovery.nominate_from_map(LITELLM, known, providers)
    keys = [n["model_key"] for n in noms]
    # qwen/qwen3-32b: prefix qwen is not a fleet provider -> filtered.
    # openai/brand-new-model: openai is not a fleet provider either.
    assert keys == []
    noms_all = discovery.nominate_from_map(LITELLM, known, None)
    keys_all = [n["model_key"] for n in noms_all]
    assert "openai/brand-new-model" in keys_all
    assert "qwen/qwen3-32b" in keys_all
    # Registry-known keys are never nominated.
    assert "deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731" not in keys_all
    assert "openrouter/some-free" not in keys_all


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>LiteLLM changelog</title>
<item><title>New model: openrouter/brand-new-thing now supported</title>
<link>https://example.invalid/1</link>
<pubDate>Mon, 24 Nov 2026 00:00:00 GMT</pubDate></item>
<item><title>openrouter/some-free price updated</title>
<link>https://example.invalid/2</link>
<pubDate>Sun, 23 Nov 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_discovery_feed_nominations_carry_provenance():
    known = discovery.registry_known_ids(REGISTRY)
    providers = discovery.registry_providers(REGISTRY)
    noms = discovery.nominate_from_feed(RSS, known, providers)
    assert len(noms) == 1
    n = noms[0]
    assert n["model_key"] == "openrouter/brand-new-thing"
    assert n["source"] == "litellm-changelog"
    assert n["link"] == "https://example.invalid/1"
    assert "Nov 2026" in n["date"]


def test_discovery_never_writes_registry(tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    mapf = tmp_path / "map.json"
    mapf.write_text(json.dumps(LITELLM), encoding="utf-8")
    before = reg.read_bytes()
    rc = discovery.main(["--registry", str(reg), "--map-file",
                         str(mapf), "--all-providers", "--json"])
    assert rc == 0
    assert reg.read_bytes() == before
