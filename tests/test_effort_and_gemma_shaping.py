"""v0.6.1 fix package — reasoning_effort control + Gemma thinking shaping.

Covers the two halves of the route-health diagnosis
(docs/cycles/route-health-investigation-protocol.md §5–§6):

* ``reasoning_effort`` is allowlisted so Meta's documented effort
  control can finally be exercised through the gateway, and the
  muse-spark registry entries pin ``minimal`` via extra_body (Meta
  defaults an omitted effort to "high" and burns hundreds of hidden
  reasoning tokens per turn).
* The field is POISON for some upstreams (HTTP 400, and 400 is not a
  failover status): Google 400s on it outright, and Mistral validates
  it per model. The gateway strips it for those entries no matter
  whether the client or an entry's extra_body put it there.
* The google/gemma-4 entries carry the proven
  ``google.thinking_config.thinking_level = "minimal"`` extra_body
  shape (probe G9) and stay disabled.

The app built here uses a recording MockTransport so tests assert on
the exact body each upstream would have received.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conftest import RAW_KEYS, _completion, _sha, auth, chat_body  # noqa: E402
from gateway import GatewayConfig, _reasoning_effort_stripped, create_app  # noqa: E402
from registry import load_registry  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
PROD_REGISTRY = SRC / "registry.json"

PROVIDER_KEYS = {"meta": "fake", "google": "fake", "mistral": "fake",
                 "groq": "fake", "openrouter": "fake", "deepinfra": "fake",
                 "ollama": "fake", "openai": "fake", "deepseek": "fake",
                 "zai": "fake"}


def build_recording_app(tmp_path: Path, registry_path: Path = PROD_REGISTRY):
    """Gateway app (production-shaped client lanes) + the list of
    upstream bodies it sent, in attempt order."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha(RAW_KEYS["admin"]),
        "clients": [
            {"id": "hermes", "key_sha256": _sha(RAW_KEYS["hermes"]),
             "lanes": ["private-free", "paid", "public-only-free"],
             "premium_allowed": False, "daily_target_usd": 1.0},
            {"id": "openclaw", "key_sha256": _sha(RAW_KEYS["openclaw"]),
             "lanes": ["private-free", "paid", "premium", "public-only-free"],
             "premium_allowed": True, "daily_target_usd": 3.0},
            {"id": "n8n", "key_sha256": _sha(RAW_KEYS["n8n"]),
             "lanes": ["private-free", "paid", "public-only-free"],
             "premium_allowed": False, "daily_target_usd": 0.5},
        ],
    }))
    sent: list[dict] = []

    def recording_upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(200, json=_completion(body["model"], "ok"))

    cfg = GatewayConfig(state_dir=tmp_path / "state",
                        registry_path=registry_path,
                        clients_path=clients_file,
                        provider_keys=PROVIDER_KEYS)
    app = create_app(cfg, upstream_transport=httpx.MockTransport(recording_upstream))
    return app, sent


def bodies_for(sent: list[dict], upstream_model: str) -> list[dict]:
    return [b for b in sent if b.get("model") == upstream_model]


@pytest.fixture()
def rec(tmp_path):
    app, sent = build_recording_app(tmp_path)
    with TestClient(app) as tc:
        yield tc, sent


def variant_without_extra_body(tmp_path: Path, entry_id: str) -> Path:
    data = json.loads(PROD_REGISTRY.read_text())
    for m in data["models"]:
        if m["id"] == entry_id:
            m.pop("extra_body", None)
    p = tmp_path / "registry-variant.json"
    p.write_text(json.dumps(data))
    return p


# ----------------------------------------------------------------------
# (1) the allowlist: a client-sent effort now reaches a Meta upstream
# ----------------------------------------------------------------------
def test_client_reasoning_effort_reaches_meta_upstream(tmp_path):
    # Registry variant with the entry profile removed, so what is being
    # proven is the allowlist itself, not the extra_body merge.
    variant = variant_without_extra_body(tmp_path, "meta/muse-spark-1.2")
    app, sent = build_recording_app(tmp_path / "v", registry_path=variant)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("what is the capital of France",
                                   model="meta/muse-spark-1.2",
                                   reasoning_effort="high"),
                    headers=auth("openclaw"))
    assert r.status_code == 200
    bodies = bodies_for(sent, "muse-spark-1.2")
    assert bodies and bodies[0]["reasoning_effort"] == "high"


# ----------------------------------------------------------------------
# (3) entry profile: lands on ordinary turns; precedence is entry-wins
# ----------------------------------------------------------------------
def test_meta_entry_profile_applies_on_ordinary_turns(rec):
    tc, sent = rec
    r = tc.post("/v1/chat/completions",
                json=chat_body("what is the capital of France",
                               model="meta/muse-spark-1.1"),
                headers=auth("openclaw"))
    assert r.status_code == 200
    bodies = bodies_for(sent, "muse-spark-1.1")
    assert bodies and bodies[0]["reasoning_effort"] == "minimal"


def test_entry_profile_wins_over_client_sent_effort(rec):
    # Documented precedence (upstream_body merge semantics): an entry's
    # extra_body overwrites the client field. This is the same rule
    # that makes OpenRouter's data_collection=deny unoverridable, so
    # the registry effort profile is an operator guardrail, not a
    # default the client can silently raise.
    tc, sent = rec
    r = tc.post("/v1/chat/completions",
                json=chat_body("what is the capital of France",
                               model="meta/muse-spark-1.1",
                               reasoning_effort="high"),
                headers=auth("openclaw"))
    assert r.status_code == 200
    bodies = bodies_for(sent, "muse-spark-1.1")
    assert bodies and bodies[0]["reasoning_effort"] == "minimal"


# ----------------------------------------------------------------------
# (2) the poison guard: Google never sees the field
# ----------------------------------------------------------------------
def test_reasoning_effort_never_reaches_gemma_upstream(tmp_path):
    # v0.7.0 (J-1): this test originally pinned the DISABLED Gemma
    # entry and relied on the pin force-insert resurrecting it — the
    # latent route J-1 closed (a pin for a disabled entry no longer
    # serves it; see tests/test_pin_hardening.py). The shaping property
    # itself still matters for the day the weekly re-probe re-enables
    # Gemma, so the test now runs against a variant registry with the
    # entry enabled: the body must carry the Gemma thinking shape and
    # must NOT carry the field Google 400s on.
    data = json.loads(PROD_REGISTRY.read_text())
    for m in data["models"]:
        if m["id"] == "google/gemma-4-31b-it":
            m["enabled"] = True
    variant = tmp_path / "registry-gemma-enabled.json"
    variant.write_text(json.dumps(data))
    app, sent = build_recording_app(tmp_path / "v", registry_path=variant)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("what is the capital of France",
                                   model="google/gemma-4-31b-it",
                                   reasoning_effort="low"),
                    headers=auth("openclaw"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "google/gemma-4-31b-it"
    bodies = bodies_for(sent, "gemma-4-31b-it")
    assert bodies, "gemma entry was not attempted"
    body = bodies[0]
    assert "reasoning_effort" not in body
    assert body["google"] == {
        "thinking_config": {"thinking_level": "minimal"}}


def test_reasoning_effort_stripped_for_enabled_google_entry(rec):
    tc, sent = rec
    r = tc.post("/v1/chat/completions",
                json=chat_body("what is the capital of France",
                               model="google/gemini-3.5-flash-lite",
                               reasoning_effort="low"),
                headers=auth("openclaw"))
    assert r.status_code == 200
    bodies = bodies_for(sent, "gemini-3.5-flash-lite")
    assert bodies and "reasoning_effort" not in bodies[0]


# ----------------------------------------------------------------------
# Mistral's per-model validation: strip unless reasoning-flagged
# ----------------------------------------------------------------------
def test_reasoning_effort_stripped_for_non_reasoning_mistral(rec):
    tc, sent = rec
    r = tc.post("/v1/chat/completions",
                json=chat_body("what is the capital of France",
                               model="mistral/ministral-8b",
                               reasoning_effort="low"),
                headers=auth("openclaw"))
    assert r.status_code == 200
    bodies = bodies_for(sent, "ministral-8b-latest")
    assert bodies and "reasoning_effort" not in bodies[0]


def test_reasoning_effort_passes_to_reasoning_flagged_mistral(rec):
    tc, sent = rec
    r = tc.post("/v1/chat/completions",
                json=chat_body("what is the capital of France",
                               model="mistral/mistral-large",
                               reasoning_effort="low"),
                headers=auth("openclaw"))
    assert r.status_code == 200
    bodies = bodies_for(sent, "mistral-large-latest")
    assert bodies and bodies[0]["reasoning_effort"] == "low"


def test_strip_predicate_matches_probe_evidence():
    reg = load_registry(PROD_REGISTRY)
    for entry_id in ("google/gemma-4-31b-it", "google/gemma-4-26b-it",
                     "google/gemini-3.5-flash-lite",
                     "mistral/ministral-8b", "mistral/mistral-small",
                     "mistral/mistral-medium", "mistral/codestral"):
        assert _reasoning_effort_stripped(reg.get(entry_id)), entry_id
    for entry_id in ("mistral/mistral-large", "meta/muse-spark-1.1",
                     "meta/muse-spark-1.3", "openrouter/free-router"):
        assert not _reasoning_effort_stripped(reg.get(entry_id)), entry_id


# ----------------------------------------------------------------------
# (4)+(5) registry invariants
# ----------------------------------------------------------------------
def _entries():
    data = json.loads(PROD_REGISTRY.read_text())
    return {m["id"]: m for m in data["models"]}


def test_all_muse_spark_entries_carry_minimal_effort_profile():
    entries = _entries()
    sparks = [e for eid, e in entries.items()
              if eid.startswith("meta/muse-spark-")]
    assert len(sparks) == 3
    for e in sparks:
        assert e["extra_body"]["reasoning_effort"] == "minimal", e["id"]


def test_all_google_gemma_entries_carry_thinking_shape():
    entries = _entries()
    gemmas = [e for eid, e in entries.items()
              if eid.startswith("google/gemma-")]
    assert len(gemmas) == 2
    for e in gemmas:
        assert e["extra_body"] == {
            "google": {"thinking_config": {"thinking_level": "minimal"}}}, e["id"]


def test_shaping_change_set_is_exactly_the_intended_entries():
    # The full set of effort-pinning entries: the three muse-spark
    # entries are THIS package's change; the cerebras/aion pins
    # pre-date it (v0.5.8 staging) and must be untouched by it. Any
    # future addition belongs in this list deliberately, not silently.
    entries = _entries()
    effort = {eid: (e.get("extra_body") or {})["reasoning_effort"]
              for eid, e in entries.items()
              if "reasoning_effort" in (e.get("extra_body") or {})}
    assert effort == {
        "meta/muse-spark-1.1": "minimal",
        "meta/muse-spark-1.2": "minimal",
        "meta/muse-spark-1.3": "minimal",
        "cerebras/gpt-oss-120b": "low",
        "cerebras/qwen-3.8-27b": "low",
        "aion/aion-3.5-mini": "low",
        "aion/aion-2.0": "none",
    }
    google_shape = {eid for eid, e in entries.items()
                    if "google" in (e.get("extra_body") or {})}
    assert google_shape == {"google/gemma-4-31b-it", "google/gemma-4-26b-it"}


def test_disabled_set_matches_owner_decisions():
    entries = _entries()
    assert entries["google/gemma-4-31b-it"].get("enabled", True) is False
    assert entries["google/gemma-4-26b-it"].get("enabled", True) is False
    # muse-spark-1.3 shipped disabled in the fix package and was
    # RE-ENABLED 2026-11-12 by the owner's deploy decision, at the
    # minimal-effort profile pinned above (see its registry note).
    assert entries["meta/muse-spark-1.3"].get("enabled", True) is True
    assert entries["meta/muse-spark-1.1"].get("enabled", True) is True
    assert entries["meta/muse-spark-1.2"].get("enabled", True) is True
