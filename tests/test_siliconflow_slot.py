"""v0.5.6 — SiliconFlow non-sensitive paid slot (owner ruling 2026-11-07).

SiliconFlow's GLM-5.3 was seeded at half of Z.ai direct on a
2026-11-07 probe reading, so it joined the paid lane in cost order.
PRICE CORRECTION (2026-11-12, expansion phase 2): SiliconFlow's own
published price list puts GLM-5.3 at $1.40/$4.40 per 1M — identical
to Z.ai direct — and the registry seed was corrected to match; the
"half price" claim is dead. What is NOT price-dependent, and stands:
the same probe graded SF's data posture UNCLEAR — training never
explicitly prohibited, retention unspecified, no ZDR — so SF must
NEVER receive a sensitive-classified turn. That is not expressible with the pre-v0.5.6 mechanisms: the
spillway (v0.5.5) deliberately relaxes the sensitive-clearance wall
for the whole paid pool. Hence the per-model flag
``spillway_sensitive_excluded``: a flagged entry is dropped from
candidate selection for sensitive requests in EVERY pass (normal,
spillway, shadow, explicit pins), and keeps its normal cost-order
position for non-sensitive requests.

Companion behaviour in the same change: a provider with no usable
key loaded (missing/empty keys/<provider>.key) is skipped at
selection time (``provider_key_missing``) instead of being attempted
unauthenticated — SiliconFlow deploys keyless (the owner's key lands
via the Secure Vault separately) and must be invisible until then,
never an error source.

These tests pin:
  * sensitive + free exhausted -> the flagged model is skipped with
    the exclusion reason and the cheapest remaining paid serves,
    even when an SF key IS loaded (the flag, not keylessness, is
    what excludes it);
  * sensitive + the cheaper paid entries disabled -> the next
    eligible paid (deepseek-v4-pro) serves; SF is never attempted;
  * non-sensitive -> SF is reachable in cost order (after DeepInfra
    Flash and Ollama gpt-oss-120b, before DeepSeek V4 Pro);
  * a sensitive explicit pin of the flagged model never serves it;
  * keyless -> SF skips as provider_key_missing and the request is
    served normally by the next candidate (200, no SF attempt);
  * keyless + sensitive -> the recorded skip reason is still the
    exclusion (safety semantics outrank key state).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from conftest import RAW_KEYS, _sha, auth, chat_body, registry_variant  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402
from test_paid_spillway import _ok_handler  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"

SENSITIVE_TEXT = (
    "restart the nginx container, then check the deploy endpoint "
    "https://deploy:s3cr3tpass@192.168.0.166:8443/status and report back"
)
_FILLER = ("the nginx service on the deploy host reported a routine "
           "status check with no anomalies in the pipeline. ")
BIG_SENSITIVE_TEXT = (
    "check the deploy status endpoint "
    "https://deploy:s3cr3tpass@192.168.0.166:8443/status and then work "
    "through this service log: " + (_FILLER * 900)
)
BIG_PLAIN_TEXT = (
    "check the deploy status endpoint and then work through this "
    "service log: " + (_FILLER * 900)
)
L4 = {"X-Sovereign-Level": "4"}
SPILLWAY = "free_exhausted_paid_fallthrough"
SF_KEY = {"siliconflow": "fake"}


def _last_record(app):
    return app.state.telemetry.recent(0)[-1]


def _registry_variant(tmp_path: Path, *, disable: tuple[str, ...] = (),
                      flatten_all_but_sf: bool = False,
                      groq_tpm: int | None = None) -> Path:
    """Production registry with entries disabled and/or every model
    except siliconflow/glm-5.3 flattened to quality 3 (below the L4
    floor), which isolates SF as the only quality-eligible entry."""
    data = json.loads((SRC / "registry.json").read_text())
    for m in data["models"]:
        if m["id"] in disable:
            m["enabled"] = False
        if flatten_all_but_sf and m["id"] != "siliconflow/glm-5.3":
            m["quality"] = {k: 3 for k in m["quality"]}
        if groq_tpm is not None and m["provider"] == "groq":
            m.setdefault("limits", {})["tpm"] = groq_tpm
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(data))
    return p


# ----------------------------------------------------------------------
# the safety core: sensitive turns can never land on SiliconFlow
# ----------------------------------------------------------------------
def test_sensitive_spillway_excludes_siliconflow_even_with_key(tmp_path):
    # SF key INJECTED: only the exclusion flag can keep SF out. The
    # triage signature (sensitive + L4 + ~23k tokens) exhausts free,
    # the spillway engages — and must serve the cheapest UNFLAGGED
    # paid model, with SF recorded as excluded, not attempted. (The
    # cheapest unflagged paid was DeepInfra Flash in v0.5.5; since
    # expansion phase 2, 2026-11-12, it is DeepInfra's gpt-oss-120b
    # route at $0.037 in.)
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000),
                                     provider_keys_extra=SF_KEY)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepinfra/gpt-oss-120b"
    assert [c["model"] for c in calls] == ["openai/gpt-oss-120b"]
    rec = _last_record(app)
    assert rec.data_class == "sensitive"
    assert rec.escalation_reasons == [SPILLWAY]
    assert {"model": "siliconflow/glm-5.3",
            "reason": "sensitive_spillway_excluded"} in rec.skipped


def test_sensitive_spillway_skips_flagged_to_next_paid(tmp_path):
    # With the cheaper unflagged paid entries disabled, the spillway
    # must step OVER the flagged SF entry to the next eligible paid
    # model (deepseek-v4-pro) — SF is never attempted upstream.
    # (Phase 2, 2026-11-12, added two more unflagged DeepInfra rungs
    # ahead of V4 Pro; they join the disable list.)
    reg = _registry_variant(
        tmp_path, disable=("deepinfra/deepseek-v4-flash",
                           "deepinfra/gpt-oss-120b",
                           "deepinfra/deepseek-v4-flash-0731",
                           "ollama/gpt-oss-120b"),
        groq_tpm=8000)  # free TPM-exhausted premise (2026-11-09)
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=reg,
                                     provider_keys_extra=SF_KEY)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepseek/deepseek-v4-pro"
    assert [c["model"] for c in calls] == ["deepseek-v4-pro"]
    rec = _last_record(app)
    assert rec.data_class == "sensitive"
    assert {"model": "siliconflow/glm-5.3",
            "reason": "sensitive_spillway_excluded"} in rec.skipped


def test_sensitive_direct_pin_never_served_by_siliconflow(tmp_path):
    # An explicit pin is chosen from the candidate list, so the
    # exclusion reaches it too: a sensitive request pinning the SF
    # entry falls back to normal selection (free serves) and SF is
    # never attempted.
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000),
                                     provider_keys_extra=SF_KEY)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT,
                                       model="siliconflow/glm-5.3"),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "groq/gpt-oss-120b"
    assert [c["model"] for c in calls] == ["openai/gpt-oss-120b"]
    rec = _last_record(app)
    assert rec.data_class == "sensitive"
    assert {"model": "siliconflow/glm-5.3",
            "reason": "sensitive_spillway_excluded"} in rec.skipped


# ----------------------------------------------------------------------
# non-sensitive: reachable, in cost order
# ----------------------------------------------------------------------
def test_non_sensitive_spillway_reaches_siliconflow(tmp_path):
    # The spillway pass proper: a client whose lane list does NOT
    # include paid (the pass ignores the lane list; pass 1 honours
    # it), with every model except SF GLM-5.3 flattened below the
    # floor. Pass 1 is empty; the spillway's only quality-eligible
    # entry is the flagged model, serving a non-sensitive turn as a
    # normal spillway step with the spillway marking.
    import sys
    sys.path.insert(0, str(SRC))
    from gateway import GatewayConfig, create_app

    reg = _registry_variant(tmp_path, flatten_all_but_sf=True)
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha(RAW_KEYS["admin"]),
        "clients": [
            {"id": "hermes", "key_sha256": _sha(RAW_KEYS["hermes"]),
             "lanes": ["private-free"], "premium_allowed": False,
             "daily_target_usd": 1.0},
        ],
    }))
    cfg = GatewayConfig(
        state_dir=tmp_path / "state",
        clients_path=clients_file,
        registry_path=reg,
        provider_keys={"groq": "fake", "openrouter": "fake",
                       "deepinfra": "fake", "ollama": "fake",
                       "openai": "fake", "deepseek": "fake",
                       "zai": "fake", "siliconflow": "fake"},
    )
    calls: list[dict] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return _ok_handler(body["model"], body, len(calls))

    app = create_app(cfg, upstream_transport=httpx.MockTransport(transport_handler))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("restart the nginx container "
                                       "and report the service status"),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "siliconflow/glm-5.3"
    assert r.headers["X-Sovereign-Lane"] == "paid"
    assert [c["model"] for c in calls] == ["zai-org/GLM-5.3"]
    rec = _last_record(app)
    assert rec.data_class != "sensitive"
    assert rec.escalation_reasons == [SPILLWAY]
    assert "paid_spillway:siliconflow/glm-5.3" in rec.signals
    assert rec.cost_usd > 0


# Expansion phase 2 (2026-11-12) paid additions — disabled in the
# failover scenario below so its execution order is the v0.5.6
# comparison set the scenario was written against.
PHASE2_PAID_IDS = (
    "openrouter/deepseek-v4-flash", "openrouter/glm-5.3-flash",
    "openrouter/qwen3.8-flash", "openrouter/gemini-3.1-flash-lite",
    "openrouter/llama-4-maverick", "openrouter/claude-haiku-5.5",
    "siliconflow/qwen3-32b", "siliconflow/deepseek-v3.2",
    "siliconflow/deepseek-v4-flash", "deepinfra/gpt-oss-120b",
    "deepinfra/deepseek-v4-flash-0731", "deepinfra/llama-3.1-8b-turbo",
    "deepinfra/mistral-small-24b", "deepinfra/qwen3.8-27b",
    "novita/llama-3.1-8b", "novita/glm-4.7-flash",
    "novita/qwen3.8-flash", "novita/deepseek-v4-flash-0731",
    "novita/qwen3.8-27b", "novita/kimi-k2.5", "novita/glm-4.6",
    "huggingface/glm-5.3-flash", "huggingface/qwen3.8-27b",
    "mistral/mistral-small", "mistral/ministral-8b",
    "mistral/mistral-medium", "zai/glm-4.6", "zai/glm-5.3-flash",
    "cerebras/qwen-3.8-27b", "meta/muse-spark-1.2",
)


def test_non_sensitive_cost_order_position(tmp_path):
    # Premise 'free is TPM-out' uses the 8k Groq TPM variant
    # (pre-Developer-plan configuration, 2026-11-09).
    # Shadow candidate order for a big non-sensitive L4 turn (free is
    # TPM/quality-out, premium is gated for hermes). At v0.5.6 the
    # candidate list was EXACTLY DeepInfra Flash, Ollama, SiliconFlow
    # — the slot the probe recommended, produced by price alone.
    # Expansion phase 2 (2026-11-12) added a full paid bench around
    # them, so the claim is now asserted RELATIVELY on the live
    # registry: Flash still precedes Ollama, which still precedes
    # SF GLM-5.3 — SF's slot is still produced by its (corrected)
    # price alone. (That SF also precedes DeepSeek V4 Pro in
    # execution order is proven by
    # test_non_sensitive_failover_reaches_siliconflow_third, which
    # isolates the original comparison set.)
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000),
                                     provider_keys_extra=SF_KEY)
    with TestClient(app) as client:
        r = client.post("/v1/shadow", json=chat_body(BIG_PLAIN_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    ids = [c["model"] for c in r.json()["candidates"]]
    assert ids.index("deepinfra/deepseek-v4-flash") \
        < ids.index("ollama/gpt-oss-120b") \
        < ids.index("siliconflow/glm-5.3")
    assert calls == []  # shadow never calls upstream


def test_non_sensitive_failover_reaches_siliconflow_third(tmp_path):
    # Execution order matches the shadow order: Flash and Ollama fail
    # upstream, SF serves third — no SF attempt before its turn.
    # The phase-2 paid bench is disabled in this variant (see
    # PHASE2_PAID_IDS) so the execution order is the v0.5.6
    # comparison set; the live-registry slot is covered relatively
    # by test_non_sensitive_cost_order_position.
    def fail_two(model, body, n):
        if model in ("deepseek-ai/DeepSeek-V4-Flash", "gpt-oss:120b"):
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return _ok_handler(model, body, n)

    reg = _registry_variant(tmp_path, disable=PHASE2_PAID_IDS,
                            groq_tpm=8000)
    app, calls = build_recording_app(tmp_path, fail_two,
                                     registry_path=reg,
                                     provider_keys_extra=SF_KEY)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_PLAIN_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "siliconflow/glm-5.3"
    assert [c["model"] for c in calls] == [
        "deepseek-ai/DeepSeek-V4-Flash", "gpt-oss:120b", "zai-org/GLM-5.3"]
    rec = _last_record(app)
    assert rec.data_class != "sensitive"
    assert rec.model == "siliconflow/glm-5.3"


# ----------------------------------------------------------------------
# keyless deployment state: invisible, never an error source
# ----------------------------------------------------------------------
def test_keyless_siliconflow_skipped_and_next_candidate_serves(tmp_path):
    # Default fixtures carry NO siliconflow key — the production
    # state at deploy. A big non-sensitive turn must be served by
    # the cheapest eligible paid model (DeepInfra Flash at v0.5.6;
    # DeepInfra's gpt-oss-120b route since phase 2, 2026-11-12) with
    # SF recorded as provider_key_missing and never attempted; the
    # disabled SF V4 Pro reports "disabled".
    app, calls = build_recording_app(tmp_path, _ok_handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_PLAIN_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepinfra/gpt-oss-120b"
    assert [c["model"] for c in calls] == ["openai/gpt-oss-120b"]
    rec = _last_record(app)
    assert {"model": "siliconflow/glm-5.3",
            "reason": "provider_key_missing"} in rec.skipped
    assert {"model": "siliconflow/deepseek-v4-pro",
            "reason": "disabled"} in rec.skipped


def test_keyless_direct_pin_falls_back_without_attempt(tmp_path):
    # Pinning the SF entry by name while it is keyless must not
    # resurrect it via the explicit-pin force-insert: the request
    # falls back to normal selection and SF is never attempted.
    app, calls = build_recording_app(tmp_path, _ok_handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("restart the nginx container "
                                       "and report the service status",
                                       model="siliconflow/glm-5.3"),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] != "siliconflow/glm-5.3"
    assert not any(c["model"] == "zai-org/GLM-5.3" for c in calls)
    rec = _last_record(app)
    assert {"model": "siliconflow/glm-5.3",
            "reason": "provider_key_missing"} in rec.skipped


def test_keyless_sensitive_skip_reason_is_the_exclusion(tmp_path):
    # Even while keyless, a sensitive turn records the EXCLUSION as
    # SF's skip reason — the safety semantics are checked before key
    # state, so the route log never disguises why SF was kept out.
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepinfra/gpt-oss-120b"
    rec = _last_record(app)
    assert rec.data_class == "sensitive"
    assert {"model": "siliconflow/glm-5.3",
            "reason": "sensitive_spillway_excluded"} in rec.skipped
    assert not any(c["model"] == "zai-org/GLM-5.3" for c in calls)
