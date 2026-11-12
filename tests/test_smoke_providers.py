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

Later the same day (2026-11-12) the free-catalog expansion added 20
more routes (registry 42 entries, 36 enabled) on the owner's order:
15 public-only-free, openrouter/free-router in private-free (its
data_collection=deny probe passed ONLY without the reasoning-disable
— the endpoint mandates reasoning), and 5 shipped DISABLED with dated
probe-failure notes in the registry: google/gemma-4-31b-it (Google
500 INTERNAL on every probe shape), cloudflare/glm-5.3-flash (not
available on the Workers Free plan — structural), inkling (403,
gated to "agentic harnesses"), and both OpenRouter gemma routes
(upstream 429 rate-limit wall on every probe round). Four Cloudflare
routes ship enabled although their deploy probes were blocked by the
shared neuron pool being exhausted for the day — an explained,
pool-level failure that hit the pre-existing Cloudflare entries
identically; their notes say so.
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
                   "cloudflare/gpt-oss-120b", "cloudflare/glm-4.7-flash",
                   # free-catalog expansion 2026-11-12 (enabled subset)
                   "zai/glm-4.5-flash", "zai/glm-4.6v-flash",
                   "cloudflare/llama-4-scout", "cloudflare/qwen3.8-27b",
                   "cloudflare/gpt-oss-20b", "cloudflare/mistral-small-3.1",
                   "openrouter/nemotron-3.5-lightning-free",
                   "openrouter/nemotron-3-nano-omni-free",
                   "openrouter/ernie-5.5-flash-free",
                   "openrouter/flint-free",
                   "openrouter/lfm-3-1.7b-free",
                   "openrouter/lfm-2.5-2.6b-free",
                   "openrouter/nemotron-3-ultra-free",
                   "openrouter/nemotron-3.5-super-vl-free")
# The five expansion routes that shipped DISABLED, each with a dated
# probe-failure note in the registry (see module docstring).
FREE20_DISABLED_IDS = ("google/gemma-4-31b-it", "cloudflare/glm-5.3-flash",
                       "openrouter/inkling-2-small-free",
                       "openrouter/gemma-4-31b-free",
                       "openrouter/gemma-4-26b-free")
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
    assert len(REG.entries) == 42
    assert len(BY_ID) == 42  # ids unique (loader also enforces)
    assert sum(1 for e in REG.entries if e.enabled) == 36


def test_disabled_entries_are_exactly_the_known_set():
    disabled = {e.id for e in REG.entries if not e.enabled}
    # the deliberate siliconflow duplicate + the five free-expansion
    # routes whose deploy probes failed (each registry note is dated
    # 2026-11-12 and states the failure; none may silently re-enable)
    assert disabled == {"siliconflow/deepseek-v4-pro"} | \
        set(FREE20_DISABLED_IDS)
    # the siliconflow entry is a duplicate ROUTE, not a distinct
    # model: DeepSeek V4 Pro is still reachable via the direct entry
    assert BY_ID["siliconflow/deepseek-v4-pro"].model == \
        "deepseek-ai/DeepSeek-V4-Pro"
    assert BY_ID["deepseek/deepseek-v4-pro"].enabled is True
    for entry_id in FREE20_DISABLED_IDS:
        assert "DISABLED AT DEPLOY" in BY_ID[entry_id].notes


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
# the free-catalog expansion (2026-11-12, +20 routes)
# ----------------------------------------------------------------------

# id -> (lane, enabled, context_tokens); all are $0 in/out by design
FREE20_ENTRIES = {
    "google/gemma-4-31b-it": ("public-only-free", False, 262144),
    "zai/glm-4.5-flash": ("public-only-free", True, 200000),
    "zai/glm-4.6v-flash": ("public-only-free", True, 200000),
    "cloudflare/llama-4-scout": ("public-only-free", True, 131072),
    "cloudflare/qwen3.8-27b": ("public-only-free", True, 262144),
    "cloudflare/glm-5.3-flash": ("public-only-free", False, 1048576),
    "cloudflare/gpt-oss-20b": ("public-only-free", True, 131072),
    "cloudflare/mistral-small-3.1": ("public-only-free", True, 131072),
    "openrouter/nemotron-3.5-lightning-free": ("public-only-free", True, 1000000),
    "openrouter/nemotron-3-nano-omni-free": ("public-only-free", True, 256000),
    "openrouter/inkling-2-small-free": ("public-only-free", False, 1048576),
    "openrouter/ernie-5.5-flash-free": ("public-only-free", True, 1024000),
    "openrouter/flint-free": ("public-only-free", True, 262144),
    "openrouter/lfm-3-1.7b-free": ("public-only-free", True, 262144),
    "openrouter/lfm-2.5-2.6b-free": ("public-only-free", True, 65536),
    "openrouter/nemotron-3-ultra-free": ("public-only-free", True, 1000000),
    "openrouter/gemma-4-31b-free": ("public-only-free", False, 262144),
    "openrouter/gemma-4-26b-free": ("public-only-free", False, 262144),
    "openrouter/nemotron-3.5-super-vl-free": ("public-only-free", True, 1000000),
    "openrouter/free-router": ("private-free", True, 200000),
}


@pytest.mark.parametrize("entry_id", sorted(FREE20_ENTRIES))
def test_free20_entry_lane_enabled_context(entry_id):
    lane, enabled, context = FREE20_ENTRIES[entry_id]
    e = BY_ID[entry_id]
    assert e.lane == lane
    assert e.enabled is enabled
    assert e.context_tokens == context
    # the whole batch exists to spend $0 allocations: zero prices,
    # and the shared-pool / posture story must stay in the notes
    assert e.input_usd_per_million == 0
    assert e.output_usd_per_million == 0
    assert "free-catalog expansion" in e.notes


def test_free20_openrouter_limits_seed_shared_pool_rpd():
    for entry_id, (_lane, _en, _ctx) in FREE20_ENTRIES.items():
        if entry_id.startswith("openrouter/"):
            assert BY_ID[entry_id].limits == {"rpd": 1000}, entry_id


def test_free_router_is_private_with_deny_only_extra_body():
    # The §5.2 conditional resolved POSITIVE for exactly one route,
    # and only in this shape: data_collection=deny WITHOUT the
    # reasoning-disable (the endpoint 400s "Reasoning is mandatory"
    # when reasoning.enabled=false is sent). Do not "fix" this to
    # match the other OpenRouter entries — it is probe-verified.
    e = BY_ID["openrouter/free-router"]
    assert e.lane == "private-free"
    assert e.extra_body == {"provider": {"data_collection": "deny"}}
    assert e.model == "openrouter/free"


def test_free20_conditionals_that_failed_deny_stay_public():
    # nemotron ultra + super-vl answered plain probes but their deny
    # probes 404'd ("No endpoints found matching your data policy"):
    # they must carry NO deny extra_body and sit in public-only-free.
    for entry_id in ("openrouter/nemotron-3-ultra-free",
                     "openrouter/nemotron-3.5-super-vl-free"):
        e = BY_ID[entry_id]
        assert e.lane == "public-only-free"
        assert e.extra_body == {}


def test_free20_tiny_models_are_seeded_at_the_floor():
    # the LFM minis exist for classification/extraction grunt work;
    # if their quality ever creeps up they would start winning task
    # classes they cannot actually serve
    assert BY_ID["openrouter/lfm-3-1.7b-free"].quality_for("chat") == 1
    assert BY_ID["openrouter/lfm-3-1.7b-free"].quality_for("reasoning") == 1
    assert BY_ID["openrouter/lfm-2.5-2.6b-free"].quality_for("chat") <= 2


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
    # every enabled public-only-free entry is lane-eligible for a
    # public turn: it is either a candidate, or it is filtered by the
    # QUALITY floor for this L2 chat task (the floor-seeded minis —
    # gpt-oss-20b's CF route and the sub-3-quality expansion entries —
    # exist for the easiest classes only, by design). A lane or
    # privacy skip here would be a regression; a quality skip is the
    # seeded intent proving itself.
    skipped = {(s["model"], s["reason"]) for s in d["skipped"]}
    for entry_id in PUBLIC_FREE_IDS:
        if entry_id in candidate_ids:
            continue
        assert (entry_id, "quality_below_required") in skipped, entry_id
        assert BY_ID[entry_id].quality_for("chat") < 3, entry_id
    # and the quality-eligible majority really are candidates
    eligible = {i for i in PUBLIC_FREE_IDS
                if BY_ID[i].quality_for("chat") >= 3}
    assert eligible <= candidate_ids


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
    # free-catalog expansion 2026-11-12 — enabled routes only (the
    # five disabled ones have no serve to pin)
    "zai/glm-4.5-flash": "hermes",
    "zai/glm-4.6v-flash": "hermes",
    "cloudflare/llama-4-scout": "hermes",
    "cloudflare/qwen3.8-27b": "hermes",
    "cloudflare/gpt-oss-20b": "hermes",
    "cloudflare/mistral-small-3.1": "hermes",
    "openrouter/nemotron-3.5-lightning-free": "hermes",
    "openrouter/nemotron-3-nano-omni-free": "hermes",
    "openrouter/ernie-5.5-flash-free": "hermes",
    "openrouter/flint-free": "hermes",
    "openrouter/lfm-3-1.7b-free": "hermes",
    "openrouter/lfm-2.5-2.6b-free": "hermes",
    "openrouter/nemotron-3-ultra-free": "hermes",
    "openrouter/nemotron-3.5-super-vl-free": "hermes",
    "openrouter/free-router": "hermes",
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
