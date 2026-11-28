"""v0.10.8 (ROUTER-KEYS-20261127): OrcaRouter + Kilo, keys installed,
models switched off, NON-SENSITIVE ONLY.

Jordan's go 2026-11-27 23:39 MST: "store both keys in the vault and
VM103, add the models switched off, turn on OrcaRouter zero data
retention, Kilo non-sensitive only". OrcaRouter ZDR is NOT on (it needs
a DPA signature not yet approved), so OrcaRouter is walled exactly like
Kilo: public-only-free lane, which the gateway refuses for sensitive
turns (pins included) and which the paid spillway never considers.

The mechanism tests below enable the five entries in a registry
variant (production keeps them disabled) and prove the wall holds for
every one of them, pinned and unpinned, while a non-sensitive pin still
serves.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import _sha, chat_body, fake_upstream, registry_variant  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from registry import PROVIDER_BASE_URLS, load_registry  # noqa: E402

ORCA_IDS = ("orcarouter/deepseek-v4.1-flash-free",
            "orcarouter/glm-5.3-flash-free",
            "orcarouter/deepseek-v4-flash-free")
KILO_IDS = ("kilo/beam-free", "kilo/auto-free")
ALL_IDS = ORCA_IDS + KILO_IDS

REG = load_registry(SRC / "registry.json")
BY_ID = {e.id: e for e in REG.entries}

SENSITIVE_TEXT = (
    "restart the nginx container, then check the deploy endpoint "
    "https://deploy:s3cr3tpass@192.168.0.166:8443/status and report back"
)
PLAIN_TEXT = "summarise the plot of a short public-domain fable in one line"
WIDE_AUTH = {"Authorization": "Bearer wide-test-key"}


# ----------------------------------------------------------------------
# registry facts
# ----------------------------------------------------------------------

def test_provider_base_urls_verified():
    assert PROVIDER_BASE_URLS["orcarouter"] == "https://api.orcarouter.ai/v1"
    assert PROVIDER_BASE_URLS["kilo"] == "https://ai-gateway.kilo.ai/api/v1"
    for i in ALL_IDS:
        e = BY_ID[i]
        assert e.base_url in (None, PROVIDER_BASE_URLS[e.provider]), i


@pytest.mark.parametrize("entry_id", ALL_IDS)
def test_entries_disabled_public_lane_zero_priced(entry_id):
    e = BY_ID[entry_id]
    assert e.enabled is False
    assert e.lane == "public-only-free"
    assert e.input_usd_per_million == 0.0 and e.output_usd_per_million == 0.0
    assert e.sensitive_cleared is False
    # The lane is the wall; the flag is reserved for paid/private lanes
    # (tests/test_registry_invariants.py).
    assert e.spillway_sensitive_excluded is False
    assert "NON-SENSITIVE ONLY" in e.notes
    assert "ROUTER-KEYS-20261127" in e.notes


def test_new_orca_v4_flash_free_entry():
    e = BY_ID["orcarouter/deepseek-v4-flash-free"]
    assert e.provider == "orcarouter"
    assert e.model == "deepseek/deepseek-v4-flash-free"
    assert e.context_tokens == 1048576
    assert "DISABLED 2026-11-27" in e.notes


@pytest.mark.parametrize("entry_id", ORCA_IDS)
def test_orca_notes_zdr_gate_and_probe(entry_id):
    n = BY_ID[entry_id].notes
    assert "Zero Data Retention is NOT on" in n
    assert "Relax only AFTER ZDR is confirmed" in n
    assert "NOT-READY" in n and "free_rate_limited" in n
    assert "Stays DISABLED until Jordan says go" in n


def test_kilo_beam_trains_on_prompts_and_probe():
    n = BY_ID["kilo/beam-free"].notes
    assert "TRAINS ON PROMPTS" in n
    assert "READY-TO-ENABLE (non-sensitive)" in n
    assert "Stays DISABLED until Jordan says go" in n
    assert "tooling" not in BY_ID["kilo/beam-free"].quality


def test_enabled_count_unchanged():
    assert sum(1 for e in REG.entries if e.enabled) == 78
    assert len(REG.entries) == 111


# ----------------------------------------------------------------------
# the wall holds even when the entries are switched on (variant)
# ----------------------------------------------------------------------

def _wide_app(tmp_path):
    from gateway import GatewayConfig, create_app
    clients_file = tmp_path / "clients-wide.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha("admin-test-key"),
        "clients": [
            {"id": "wide", "key_sha256": _sha("wide-test-key"),
             "lanes": ["private-free", "public-only-free", "paid", "premium"],
             "premium_allowed": True, "daily_target_usd": 3.0},
        ],
    }))
    cfg = GatewayConfig(
        state_dir=tmp_path / "state-wide",
        clients_path=clients_file,
        registry_path=registry_variant(tmp_path, enable=ALL_IDS),
        provider_keys={"groq": "fake", "openrouter": "fake",
                       "deepinfra": "fake", "ollama": "fake",
                       "openai": "fake", "deepseek": "fake", "zai": "fake",
                       "orcarouter": "fake", "kilo": "fake"},
    )
    return create_app(cfg, upstream_transport=httpx.MockTransport(fake_upstream))


def _run(tmp_path, body):
    app = _wide_app(tmp_path)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=body, headers=WIDE_AUTH)
    return r, app.state.telemetry.recent(0)[-1]


@pytest.mark.parametrize("entry_id", ALL_IDS)
def test_sensitive_pin_to_orca_or_kilo_is_blocked(tmp_path, entry_id):
    body = chat_body(SENSITIVE_TEXT + f" [pin {entry_id}]", model=entry_id)
    r, rec = _run(tmp_path, body)
    assert r.status_code == 200, r.text
    assert r.headers["X-Sovereign-Model"] not in ALL_IDS
    assert {"model": entry_id,
            "reason": "pin_blocked:sensitive_public_lane"} in rec.skipped


def test_sensitive_unpinned_never_reaches_orca_or_kilo(tmp_path):
    body = chat_body(SENSITIVE_TEXT + " [unpinned]")
    r, rec = _run(tmp_path, body)
    assert r.status_code == 200, r.text
    assert r.headers["X-Sovereign-Model"] not in ALL_IDS
    skipped = {s["model"]: s["reason"] for s in rec.skipped}
    for i in ALL_IDS:
        assert skipped.get(i) == "sensitive_public_lane", (i, skipped.get(i))


def test_non_sensitive_pin_to_kilo_beam_serves(tmp_path):
    body = chat_body(PLAIN_TEXT + " [plain pin]", model="kilo/beam-free")
    r, _ = _run(tmp_path, body)
    assert r.status_code == 200, r.text
    assert r.headers["X-Sovereign-Model"] == "kilo/beam-free"
