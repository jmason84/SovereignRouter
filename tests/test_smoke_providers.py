"""Structured smoke tests for the 2026-11-11/12 provider expansion.

The registry grew from 15 to 22 models (21 enabled) across two days:
cerebras + novita + zai/glm-4.7-flash (2026-11-12), then five staged
entries enabled the same day (google, cloudflare x2, mistral x2 —
mistral reclassified to the PAID lane after its free tier proved to
issue no API keys), meta/muse-spark-1.1 enabled once billing cleared,
and huggingface/gpt-oss-120b-cerebras DELETED (the HF passthrough
ignored every reasoning control; Cerebras is called direct only).

These tests pin the *shape and intent* of that state so a future
registry edit that silently changes a lane, a price, a flag or a
client's lane list fails loudly here:

  * every entry: required fields present and well-typed, lane valid,
    prices sane for its lane class;
  * the Nov 11-12 additions carry exactly the lanes / flags / limits /
    prices they were deployed with;
  * exactly one disabled entry (the deliberate siliconflow duplicate);
  * client lane lists match the 2026-11-12 intent — hermes/n8n gained
    public-only-free, openclaw keeps premium, crm has NO public access;
  * selection-level: a sensitive turn never selects a public-only-free
    or sensitive-excluded entry (even when pinned by name), a public
    turn CAN select public-only-free entries, and a pin for each new
    entry serves that entry when eligible.

No network: HTTP-level tests run against a recording MockTransport,
mirroring tests/test_fixpass.py's build_recording_app. The production
clients.json lives only on VM103, so the client-policy tests build
the file from the stated intent (the same way conftest.py builds its
fixture clients) and load it through the real loader.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import SRC, _sha, auth, chat_body
from clients import load_clients
from gateway import GatewayConfig, create_app
from registry import LANES, TASK_CLASSES, load_registry

RAW = json.loads((SRC / "registry.json").read_text())
REG = load_registry(SRC / "registry.json")
BY_ID = {e.id: e for e in REG.entries}
RAW_BY_ID = {m["id"]: m for m in RAW["models"]}

# basic_auth_url detector payload (same marker as test_paid_spillway).
SENSITIVE_TEXT = (
    "restart the nginx container, then check the deploy endpoint "
    "https://deploy:s3cr3tpass@192.168.0.166:8443/status and report back"
)
L4 = {"X-Sovereign-Level": "4"}

PUBLIC_FREE_IDS = ("zai/glm-4.7-flash", "google/gemini-3.5-flash-lite",
                   "cloudflare/gpt-oss-120b", "cloudflare/glm-4.7-flash")
# Every entry whose provider posture was reviewed and NOT cleared for
# sensitive traffic. The gateway drops these for sensitive turns in
# every pass, pins included (gateway v0.5.6+).
SENSITIVE_EXCLUDED_IDS = (
    "siliconflow/glm-5.3", "siliconflow/deepseek-v4-pro",
    "huggingface/deepseek-v4-flash", "cerebras/gpt-oss-120b",
    "novita/gpt-oss-120b", "mistral/mistral-large", "mistral/codestral",
)
ALL_PROVIDER_KEYS = {p: "fake" for p in {e.provider for e in REG.entries}}


# ----------------------------------------------------------------------
# registry shape: every entry, parametrized
# ----------------------------------------------------------------------

@pytest.mark.parametrize("entry", REG.entries, ids=[e.id for e in REG.entries])
def test_entry_required_fields_well_typed(entry):
    assert isinstance(entry.id, str) and "/" in entry.id
    assert isinstance(entry.provider, str) and entry.provider
    assert isinstance(entry.model, str) and entry.model
    assert entry.lane in LANES
    assert isinstance(entry.context_tokens, int) and entry.context_tokens > 0
    assert isinstance(entry.enabled, bool)
    # prices: present (loader defaults 0.0), numeric, never negative
    for price in (entry.input_usd_per_million, entry.output_usd_per_million):
        assert isinstance(price, (int, float)) and price >= 0
    if entry.cached_input_usd_per_million is not None:
        assert entry.cached_input_usd_per_million >= 0
    # quality: only known task classes, scores in 1..5
    assert entry.quality
    assert set(entry.quality) <= set(TASK_CLASSES)
    assert all(isinstance(v, int) and 1 <= v <= 5
               for v in entry.quality.values())
    # an entry must resolve to a real https upstream: its own base_url
    # or the provider default in registry.PROVIDER_BASE_URLS
    assert entry.effective_base_url.startswith("https://")
    # limits: only the quota store's known windows, positive values
    assert set(entry.limits) <= {"rpm", "tpm", "rpd", "tpd"}
    assert all(isinstance(v, (int, float)) and v > 0
               for v in entry.limits.values())
    assert isinstance(entry.extra_body, dict)
    assert isinstance(entry.spillway_sensitive_excluded, bool)


@pytest.mark.parametrize("entry", REG.entries, ids=[e.id for e in REG.entries])
def test_entry_raw_json_required_keys(entry):
    raw = RAW_BY_ID[entry.id]
    for key in ("id", "provider", "model", "lane", "quality",
                "context_tokens", "input_usd_per_million",
                "output_usd_per_million"):
        assert key in raw, f"{entry.id}: raw entry missing {key!r}"


def test_registry_counts_and_unique_ids():
    assert len(REG.entries) == 22
    assert len(BY_ID) == 22  # ids unique (loader also enforces)
    assert sum(1 for e in REG.entries if e.enabled) == 21


def test_exactly_one_disabled_entry_and_it_is_the_siliconflow_duplicate():
    disabled = [e.id for e in REG.entries if not e.enabled]
    assert disabled == ["siliconflow/deepseek-v4-pro"]
    # it is a duplicate ROUTE, not a distinct model: DeepSeek V4 Pro is
    # still reachable via the enabled direct entry
    assert BY_ID["siliconflow/deepseek-v4-pro"].model == \
        "deepseek-ai/DeepSeek-V4-Pro"
    assert BY_ID["deepseek/deepseek-v4-pro"].enabled is True


def test_deleted_hf_cerebras_route_stays_deleted():
    # Deleted 2026-11-12 on the owner's order (HF passthrough fault,
    # 2026-11-11). Cerebras is called DIRECT only — the direct entry
    # below is the replacement, and no entry may route gpt-oss-120b
    # through Hugging Face again.
    assert "huggingface/gpt-oss-120b-cerebras" not in BY_ID
    assert all(":cerebras" not in e.model for e in REG.entries)
    assert all("cerebras" not in e.id or e.provider == "cerebras"
               for e in REG.entries)
    assert BY_ID["cerebras/gpt-oss-120b"].enabled is True


def test_sensitive_excluded_set_is_exactly_the_reviewed_providers():
    flagged = {e.id for e in REG.entries if e.spillway_sensitive_excluded}
    assert flagged == set(SENSITIVE_EXCLUDED_IDS)
    # no provider is owner-cleared for sensitive traffic yet (Decision:
    # sensitive turns stay in private lanes / the spillway ruling)
    assert all(not e.sensitive_cleared for e in REG.entries)


# ----------------------------------------------------------------------
# pricing posture per lane class
# ----------------------------------------------------------------------

@pytest.mark.parametrize("entry", REG.entries, ids=[e.id for e in REG.entries])
def test_prices_match_lane_posture(entry):
    if entry.lane == "public-only-free":
        # the lane exists to spend $0 allocations / free tiers
        assert entry.input_usd_per_million == 0
        assert entry.output_usd_per_million == 0
        assert entry.cached_input_usd_per_million in (None, 0)
    elif entry.lane in ("paid", "premium") and entry.enabled:
        # an enabled paid entry at $0 would corrupt spend accounting
        # and cost-order selection alike
        assert entry.input_usd_per_million > 0
        assert entry.output_usd_per_million > 0
    else:
        # private-free: the lane marks DATA POSTURE, not price — Groq
        # carries real Developer-plan prices for accounting (owner
        # decision 2026-11-09), OpenRouter's free routes are $0.
        assert entry.input_usd_per_million >= 0
        assert entry.output_usd_per_million >= 0


# ----------------------------------------------------------------------
# the Nov 11-12 additions, one by one
# ----------------------------------------------------------------------

# id -> (lane, (in, out) $/1M, context_tokens)
NEW_ENTRIES = {
    "cerebras/gpt-oss-120b": ("paid", (0.35, 0.75), 65536),
    "novita/gpt-oss-120b": ("paid", (0.05, 0.25), 131072),
    "zai/glm-4.7-flash": ("public-only-free", (0.0, 0.0), 200000),
    "google/gemini-3.5-flash-lite": ("public-only-free", (0.0, 0.0), 1048576),
    "cloudflare/gpt-oss-120b": ("public-only-free", (0.0, 0.0), 131072),
    "cloudflare/glm-4.7-flash": ("public-only-free", (0.0, 0.0), 131072),
    "mistral/mistral-large": ("paid", (2.00, 6.00), 262144),
    "mistral/codestral": ("paid", (0.30, 0.90), 262144),
    "meta/muse-spark-1.1": ("premium", (1.25, 4.25), 262144),
}


@pytest.mark.parametrize("entry_id", sorted(NEW_ENTRIES))
def test_new_entry_lane_prices_context(entry_id):
    lane, prices, context = NEW_ENTRIES[entry_id]
    e = BY_ID[entry_id]
    assert e.enabled is True
    assert e.lane == lane
    assert (e.input_usd_per_million, e.output_usd_per_million) == prices
    assert e.context_tokens == context


def test_cerebras_direct_trial_shape():
    e = BY_ID["cerebras/gpt-oss-120b"]
    # direct provider URL (the deleted route was the HF passthrough)
    assert e.effective_base_url == "https://api.cerebras.ai/v1"
    assert e.model == "gpt-oss-120b"
    # reasoning damped, never off: effort pinned low via extra_body
    assert e.extra_body == {"reasoning_effort": "low"}
    # trial-tier limits, exactly as deployed
    assert e.limits == {"rpm": 5, "tpm": 90000, "tpd": 1000000}
    assert e.spillway_sensitive_excluded is True


def test_novita_cheapest_120b_route():
    e = BY_ID["novita/gpt-oss-120b"]
    assert e.effective_base_url == "https://api.novita.ai/v3/openai"
    assert e.model == "openai/gpt-oss-120b"
    assert e.spillway_sensitive_excluded is True


def test_public_only_free_additions_are_zero_priced_and_flagged_by_lane():
    for entry_id in PUBLIC_FREE_IDS:
        e = BY_ID[entry_id]
        assert e.lane == "public-only-free"
        # public-only-free entries are kept out of sensitive selection
        # by their LANE (gateway: sensitive_public_lane), not the flag
        assert e.spillway_sensitive_excluded is False
    assert BY_ID["google/gemini-3.5-flash-lite"].limits == \
        {"rpm": 10, "rpd": 1000}
    assert BY_ID["google/gemini-3.5-flash-lite"].quality_for("long-context") == 5
    assert BY_ID["cloudflare/gpt-oss-120b"].model == "@cf/openai/gpt-oss-120b"
    assert BY_ID["cloudflare/glm-4.7-flash"].model == "@cf/zai-org/glm-4.7-flash"
    assert BY_ID["zai/glm-4.7-flash"].model == "glm-4.7-flash"


def test_mistral_is_paid_and_sensitive_excluded():
    # Deployed staged as "free"; the premise died at the console (the
    # Free plan issues no API keys) and the owner funded $10 PAYG —
    # both entries are PAID at published rates, still excluded from
    # sensitive traffic on the third-party-hosting rule.
    for entry_id in ("mistral/mistral-large", "mistral/codestral"):
        e = BY_ID[entry_id]
        assert e.lane == "paid"
        assert e.spillway_sensitive_excluded is True
        assert e.limits == {"rpm": 60, "tpm": 500000}
        assert e.effective_base_url == "https://api.mistral.ai/v1"
    assert BY_ID["mistral/codestral"].quality_for("code") == 5


def test_meta_muse_spark_enabled_in_premium():
    e = BY_ID["meta/muse-spark-1.1"]
    assert e.lane == "premium" and e.enabled is True
    assert e.cached_input_usd_per_million == pytest.approx(0.15)
    assert e.effective_base_url == "https://api.meta.ai/v1"
    assert e.quality_for("chat") == 5 and e.quality_for("reasoning") == 5
    # NOTE: unlike mistral/cerebras/novita, this entry carries NO
    # spillway_sensitive_excluded flag (gateway + registry treat the
    # premium lane as the sensitive spillway's last resort, and
    # test_registry.py pins the flag set provider-wise without meta).
    assert e.spillway_sensitive_excluded is False


# ----------------------------------------------------------------------
# client policies (2026-11-12 lane intent)
# ----------------------------------------------------------------------

INTENT_CLIENTS = {
    "admin_key_sha256": _sha("admin-test-key"),
    "clients": [
        {"id": "hermes", "key_sha256": _sha("hermes-test-key"),
         "lanes": ["private-free", "paid", "public-only-free"],
         "premium_allowed": False, "daily_target_usd": 1.25},
        {"id": "openclaw", "key_sha256": _sha("openclaw-test-key"),
         "lanes": ["private-free", "paid", "premium", "public-only-free"],
         "premium_allowed": True, "daily_target_usd": 5.0,
         "premium_monthly_alert_usd": 20.0, "premium_daily_count_alert": 20},
        {"id": "n8n", "key_sha256": _sha("n8n-test-key"),
         "lanes": ["private-free", "paid", "public-only-free"],
         "premium_allowed": False, "daily_target_usd": 2.5},
        {"id": "crm", "key_sha256": _sha("crm-test-key"),
         "lanes": ["private-free", "paid"],
         "premium_allowed": False, "daily_target_usd": 1.25},
    ],
}


def _intent_store(tmp_path):
    p = tmp_path / "clients-intent.json"
    p.write_text(json.dumps(INTENT_CLIENTS))
    return load_clients(p)


def test_client_lanes_match_nov12_intent(tmp_path):
    store = _intent_store(tmp_path)
    assert store.get("hermes").lanes == \
        ("private-free", "paid", "public-only-free")
    assert store.get("n8n").lanes == \
        ("private-free", "paid", "public-only-free")
    assert store.get("openclaw").lanes == \
        ("private-free", "paid", "premium", "public-only-free")
    assert store.get("crm").lanes == ("private-free", "paid")
    assert store.get("hermes").premium_allowed is False
    assert store.get("n8n").premium_allowed is False
    assert store.get("crm").premium_allowed is False
    assert store.get("openclaw").premium_allowed is True


def test_crm_has_no_public_only_free_access(tmp_path):
    crm = _intent_store(tmp_path).get("crm")
    assert crm.lane_allowed("public-only-free") is False
    assert crm.lane_allowed("premium") is False
    assert crm.lane_allowed("private-free") is True
    assert crm.lane_allowed("paid") is True
    # every lane any client names must be a real registry lane
    store = _intent_store(tmp_path)
    for policy in store.clients.values():
        assert set(policy.lanes) <= set(LANES)


def test_clients_example_file_shape():
    # The in-repo example is a template (real file lives on VM103);
    # what must hold here: it loads through the real loader, names the
    # four known clients, and its crm policy grants no public lane and
    # no premium.
    store = load_clients(SRC / "clients.example.json")
    assert set(store.clients) == {"hermes", "openclaw", "n8n", "crm"}
    assert store.get("crm").lanes == ("private-free", "paid")
    assert store.get("crm").premium_allowed is False
    assert store.get("openclaw").premium_allowed is True
    for policy in store.clients.values():
        assert set(policy.lanes) <= set(LANES)


# ----------------------------------------------------------------------
# selection-level smoke tests (recording MockTransport, no network)
# ----------------------------------------------------------------------

def _completion(model: str) -> dict:
    return {
        "id": "chatcmpl-smoke", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant",
                                 "content": f"answer from {model}"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                  "total_tokens": 110},
    }


def build_smoke_app(tmp_path: Path):
    """Production registry + Nov-12-intent clients + a fake key for
    EVERY provider, upstream recording which model was called."""
    clients = json.loads(json.dumps(INTENT_CLIENTS))
    clients["clients"].append(
        {"id": "publicprobe", "key_sha256": _sha("publicprobe-test-key"),
         "default_data_class": "public",
         "lanes": ["private-free", "paid", "public-only-free"],
         "premium_allowed": False, "daily_target_usd": 1.0})
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps(clients))
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=_completion(body["model"]))

    cfg = GatewayConfig(
        state_dir=tmp_path / "state",
        clients_path=clients_file,
        registry_path=SRC / "registry.json",
        provider_keys=dict(ALL_PROVIDER_KEYS),
    )
    app = create_app(cfg, upstream_transport=httpx.MockTransport(handler))
    return app, calls


def _auth(who: str) -> dict:
    return {"Authorization": f"Bearer {who}-test-key"}


def _last_record(app):
    return app.state.telemetry.recent(0)[-1]


def test_shadow_sensitive_never_selects_public_or_excluded(tmp_path):
    app, _calls = build_smoke_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/shadow", json=chat_body(SENSITIVE_TEXT),
                    headers={**_auth("hermes"), **L4})
    assert r.status_code == 200
    d = r.json()
    assert d["data_class"] == "sensitive"
    candidate_ids = [c["model"] for c in d["candidates"]]
    assert candidate_ids, "sensitive turn must still have candidates"
    # no public-only-free entry is ever a sensitive candidate
    assert not (set(candidate_ids) & set(PUBLIC_FREE_IDS))
    assert all(c["lane"] != "public-only-free" for c in d["candidates"])
    # no sensitive-excluded entry either, in any pass
    assert not (set(candidate_ids) & set(SENSITIVE_EXCLUDED_IDS))
    skipped = {(s["model"], s["reason"]) for s in d["skipped"]}
    for entry_id in PUBLIC_FREE_IDS:
        assert (entry_id, "sensitive_public_lane") in skipped
    for entry_id in SENSITIVE_EXCLUDED_IDS:
        # selection checks `enabled` before the exclusion flag, so the
        # one disabled entry reports "disabled"; every enabled flagged
        # entry must report the exclusion itself
        reason = "sensitive_spillway_excluded" \
            if BY_ID[entry_id].enabled else "disabled"
        assert (entry_id, reason) in skipped


def test_shadow_public_client_can_select_public_only_free(tmp_path):
    app, _calls = build_smoke_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/shadow",
                    json=chat_body("summarise this public press release"),
                    headers=_auth("publicprobe"))
    assert r.status_code == 200
    d = r.json()
    assert d["data_class"] == "public"
    candidate_ids = {c["model"] for c in d["candidates"]}
    assert set(PUBLIC_FREE_IDS) <= candidate_ids


def test_shadow_crm_candidates_have_no_public_lane(tmp_path):
    app, _calls = build_smoke_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/shadow", json=chat_body("draft a follow-up email"),
                    headers=_auth("crm"))
    assert r.status_code == 200
    d = r.json()
    assert all(c["lane"] != "public-only-free" for c in d["candidates"])
    skipped = {(s["model"], s["reason"]) for s in d["skipped"]}
    for entry_id in PUBLIC_FREE_IDS:
        assert (entry_id, "lane_not_allowed") in skipped


def test_public_client_auto_request_served_by_public_only_free(tmp_path):
    app, calls = build_smoke_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("hello, summarise the weather news"),
                    headers=_auth("publicprobe"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] == "public-only-free"
    assert r.headers["X-Sovereign-Model"] in PUBLIC_FREE_IDS
    assert len(calls) == 1


# pin target -> the client whose lane list admits the entry
PIN_CASES = {
    "cerebras/gpt-oss-120b": "hermes",
    "novita/gpt-oss-120b": "hermes",
    "zai/glm-4.7-flash": "hermes",
    "google/gemini-3.5-flash-lite": "hermes",
    "cloudflare/gpt-oss-120b": "hermes",
    "cloudflare/glm-4.7-flash": "hermes",
    "mistral/mistral-large": "hermes",
    "mistral/codestral": "hermes",
    "meta/muse-spark-1.1": "openclaw",
}


@pytest.mark.parametrize("entry_id", sorted(PIN_CASES))
def test_pin_new_entry_serves_that_entry(tmp_path, entry_id):
    app, calls = build_smoke_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("say hi", model=entry_id),
                    headers=_auth(PIN_CASES[entry_id]))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == entry_id
    assert [c["model"] for c in calls] == [BY_ID[entry_id].model]


def test_pin_meta_is_403_for_hermes(tmp_path):
    app, calls = build_smoke_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("say hi", model="meta/muse-spark-1.1"),
                    headers=_auth("hermes"))
    assert r.status_code == 403
    assert calls == []


def test_pin_public_entry_is_403_for_crm(tmp_path):
    app, calls = build_smoke_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("say hi",
                                   model="google/gemini-3.5-flash-lite"),
                    headers=_auth("crm"))
    assert r.status_code == 403
    assert calls == []


@pytest.mark.parametrize("entry_id", ["cerebras/gpt-oss-120b",
                                      "novita/gpt-oss-120b",
                                      "mistral/mistral-large",
                                      "mistral/codestral"])
def test_sensitive_pin_of_excluded_new_entry_never_serves_it(tmp_path,
                                                             entry_id):
    app, calls = build_smoke_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body(SENSITIVE_TEXT, model=entry_id),
                    headers={**_auth("hermes"), **L4})
    assert r.status_code == 200
    # the flagged entry is not resurrected by the pin; the private-free
    # lane serves instead (mirrors the siliconflow pin regression)
    assert r.headers["X-Sovereign-Model"] == "groq/gpt-oss-120b"
    assert [c["model"] for c in calls] == ["openai/gpt-oss-120b"]
    rec = _last_record(app)
    assert rec.data_class == "sensitive"
    assert {"model": entry_id,
            "reason": "sensitive_spillway_excluded"} in rec.skipped


def test_sensitive_auto_request_served_on_private_free(tmp_path):
    app, _calls = build_smoke_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body(SENSITIVE_TEXT),
                    headers={**_auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] == "private-free"
    rec = _last_record(app)
    assert rec.data_class == "sensitive"
    assert rec.model not in SENSITIVE_EXCLUDED_IDS
    assert rec.model not in PUBLIC_FREE_IDS
