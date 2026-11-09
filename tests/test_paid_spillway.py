"""v0.5.5 — free-first / paid-spillway waterfall (owner ruling 2026-11-07).

Jordan's words: "we need to allow paid models when we burn up the
free — but the priority should be free processing FIRST", with the
design fork confirmed: SENSITIVE TURNS INCLUDED, CHEAPEST PAID FIRST.

This replaces the v0.5.3 sensitive no-candidate rescue (and its
v0.5.4 gpt-5.5 pin) with ONE mechanism:

  * normal (free-first) selection is unchanged — a servable free turn
    NEVER spills;
  * when it yields ZERO eligible candidates, or every candidate fails
    upstream, one spillway pass re-runs selection over paid + premium
    in normal cost order (cheapest paid first; gpt-5.5 sorts last by
    price, which is where the rescue pin's effect now lives);
  * in that pass only, the sensitive provider-clearance wall and the
    per-client premium gate are relaxed; quality, tools, context,
    verification, provider limits and the emergency paid-lane switch
    still bind;
  * spillway serves are recorded as escalations with the reason
    "free_exhausted_paid_fallthrough";
  * alias-based auto-selection only — explicit premium requests and
    explicit model pins keep their semantics (hermes premium is still
    a 403; the pass is not a premium unlock);
  * all three clients uniformly.

Origin: the 2026-11-06 Hermes dead end — the hourly email-triage job
(sensitive + L4 + infra, ~45k tokens) 429'd 36 times in a day because
its only free candidate was TPM-walled by size, paid was barred by
the sensitive wall, premium by the client gate, and the rescue
deliberately excluded hermes.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from conftest import RAW_KEYS, _chunk, _sha, _sse, auth, chat_body, registry_variant  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"

# basic_auth_url detector: scheme://user:password@host (password >= 4).
SENSITIVE_TEXT = (
    "restart the nginx container, then check the deploy endpoint "
    "https://deploy:s3cr3tpass@192.168.0.166:8443/status and report back"
)
PLAIN_TEXT = "restart the nginx container and report the service status"
L4 = {"X-Sovereign-Level": "4"}
L5 = {"X-Sovereign-Level": "5"}
SPILLWAY = "free_exhausted_paid_fallthrough"

# The triage signature: a credential-shaped string inside a payload far
# larger than the Groq free window (8k tokens/min). ~900 repetitions of
# a ~105-char sentence ≈ 23k tokens — the only quality-eligible free
# model (groq/gpt-oss-120b) is TPM-skipped on size alone, exactly as on
# 2026-11-06.
BIG_SENSITIVE_TEXT = (
    "check the deploy status endpoint "
    "https://deploy:s3cr3tpass@192.168.0.166:8443/status and then work "
    "through this service log: "
    + ("the nginx service on the deploy host reported a routine "
       "status check with no anomalies in the pipeline. " * 900)
)


def _completion(model: str, content: str) -> dict:
    return {
        "id": "chatcmpl-fake", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                  "total_tokens": 110},
    }


def _ok_handler(model, body, n):
    if body.get("stream"):
        chunks = [
            _chunk(model, {"role": "assistant", "content": "Paid "}),
            _chunk(model, {"content": "answer"}),
            _chunk(model, {}, finish="stop"),
        ]
        usage = {"prompt_tokens": 100, "completion_tokens": 6,
                 "total_tokens": 106}
        return httpx.Response(200, content=_sse(chunks, usage),
                              headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json=_completion(model, f"answer from {model}"))


def _registry_below_l4(tmp_path: Path, *, premium_quality: int | None = None,
                       disable: tuple[str, ...] = ()) -> Path:
    """Production registry with every non-premium model's quality
    flattened to 3 — below the level-4 floor for every task class — so
    normal selection deterministically yields zero candidates at L4.
    Optionally re-rate premium (all classes) and/or disable entries."""
    data = json.loads((SRC / "registry.json").read_text())
    for m in data["models"]:
        if m["lane"] != "premium":
            m["quality"] = {k: 3 for k in m["quality"]}
        elif premium_quality is not None:
            m["quality"] = {k: premium_quality for k in m["quality"]}
        if m["id"] in disable:
            m["enabled"] = False
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(data))
    return p


def _last_record(app):
    return app.state.telemetry.recent(0)[-1]


# ----------------------------------------------------------------------
# free first: a servable free turn never spills
# ----------------------------------------------------------------------
def test_free_available_no_spill_sensitive_hermes(tmp_path):
    # Production registry: groq/gpt-oss-120b is quality-eligible for
    # this L4 sensitive turn and inside its TPM window for a small
    # prompt — it must serve, on the private-free lane, untouched by
    # the spillway.
    app, calls = build_recording_app(tmp_path, _ok_handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "groq/gpt-oss-120b"
    assert r.headers["X-Sovereign-Lane"] == "private-free"
    assert r.headers["X-Sovereign-Escalated"] == "false"
    assert [c["model"] for c in calls] == ["openai/gpt-oss-120b"]
    rec = _last_record(app)
    assert rec.escalated is False
    assert rec.escalation_reasons == []
    assert not any(s.startswith("paid_spillway") for s in rec.signals)


def test_ordinary_free_traffic_untouched(tmp_path):
    app, calls = build_recording_app(tmp_path, _ok_handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("summarize this log file please"),
                        headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] == "private-free"
    rec = _last_record(app)
    assert rec.escalated is False
    assert rec.escalation_reasons == []
    assert rec.cost_usd == 0


# ----------------------------------------------------------------------
# the 2026-11-06 triage signature, fixed: sensitive + hermes + ~45k
# ----------------------------------------------------------------------
def test_triage_signature_spills_to_cheapest_paid(tmp_path):
    # Premise: free is exhausted (Groq TPM window) — stated via the
    # pre-Developer-plan 8k TPM variant (2026-11-09 config change).
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200  # was 429 before v0.5.5
    assert r.headers["X-Sovereign-Model"] == "deepinfra/deepseek-v4-flash"
    assert r.headers["X-Sovereign-Lane"] == "paid"
    assert r.headers["X-Sovereign-Escalated"] == "true"
    # exactly ONE upstream attempt: the cheapest paid model
    assert [c["model"] for c in calls] == ["deepseek-ai/DeepSeek-V4-Flash"]
    rec = _last_record(app)
    assert rec.status == 200
    assert rec.data_class == "sensitive"
    assert rec.lane == "paid"
    assert rec.escalated is True
    assert rec.escalation_reasons == [SPILLWAY]
    assert rec.cost_usd > 0  # paid pricing applies normally
    assert "paid_spillway:deepinfra/deepseek-v4-flash" in rec.signals
    # the route row shows WHY free was exhausted: the only
    # quality-eligible free model was TPM-walled by payload size
    assert {"model": "groq/gpt-oss-120b", "reason": "rate_limited_tpm"} \
        in rec.skipped
    assert {"model": "-", "reason": "paid_spillway_pass"} in rec.skipped


def test_triage_signature_spills_streaming(tmp_path):
    # The organic triage failures were streaming turns (stream=true).
    # Premise: free TPM-exhausted — 8k variant (2026-11-09).
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT, stream=True),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert "Paid " in r.text and "answer" in r.text
    assert [c["model"] for c in calls] == ["deepseek-ai/DeepSeek-V4-Flash"]
    rec = _last_record(app)
    assert rec.stream is True
    assert rec.status == 200
    assert rec.lane == "paid"
    assert rec.escalated is True
    assert rec.escalation_reasons == [SPILLWAY]


def test_spillway_applies_to_n8n_uniformly(tmp_path):
    # Premise: free is exhausted (Groq TPM window) — stated via the
    # pre-Developer-plan 8k TPM variant (2026-11-09 config change).
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("n8n"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepinfra/deepseek-v4-flash"
    rec = _last_record(app)
    assert rec.client == "n8n"
    assert rec.escalation_reasons == [SPILLWAY]


# ----------------------------------------------------------------------
# non-sensitive dead ends spill too; cost order replaces the pin
# ----------------------------------------------------------------------
def test_non_sensitive_zero_candidate_spills_cheapest_premium(tmp_path):
    # All non-premium quality flattened below the L4 floor: normal
    # selection is empty even though the turn is NOT sensitive (paid
    # is quality-ineligible, premium is gated). The spillway re-runs
    # over paid + premium — paid still fails the quality floor, so the
    # cheapest quality-eligible premium entry serves. Under the old
    # v0.5.4 pin this turn (when sensitive) went to gpt-5.5; cost
    # order now decides, and deepseek-v4-pro is cheaper.
    reg = _registry_below_l4(tmp_path)
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(PLAIN_TEXT),
                        headers={**auth("openclaw"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepseek/deepseek-v4-pro"
    assert r.headers["X-Sovereign-Lane"] == "premium"
    assert len(calls) == 1 and calls[0]["model"] == "deepseek-v4-pro"
    # deepseek takes the plain budget parameter (no dialect rename)
    assert "max_tokens" in calls[0]
    assert "max_completion_tokens" not in calls[0]
    rec = _last_record(app)
    assert rec.data_class != "sensitive"
    assert rec.escalation_reasons == [SPILLWAY]
    assert rec.cost_usd > 0


def test_gpt55_reached_only_when_cheaper_paid_ineligible(tmp_path):
    # With the cheaper premium entries disabled, the same dead end
    # lands on gpt-5.5 — last by price, exactly where the removed
    # rescue pin used to force it — with its budget dialect intact.
    reg = _registry_below_l4(
        tmp_path, disable=("deepseek/deepseek-v4-pro", "zai/glm-5.3"))
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("openclaw"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "premium/gpt-5.5"
    assert len(calls) == 1 and calls[0]["model"] == "gpt-5.5"
    assert "max_completion_tokens" in calls[0]
    assert "max_tokens" not in calls[0]
    rec = _last_record(app)
    assert rec.data_class == "sensitive"
    assert rec.escalation_reasons == [SPILLWAY]


# ----------------------------------------------------------------------
# the quality floor still binds inside the pass
# ----------------------------------------------------------------------
def test_quality_floor_still_enforced_in_spillway(tmp_path):
    # EVERY model flattened to quality 3, premium included: the
    # spillway must not serve a below-floor paid model — the turn
    # still 429s, with the empty pass visible in the skip list.
    reg = _registry_below_l4(tmp_path, premium_quality=3)
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 429
    assert calls == []  # no upstream attempt, paid included
    rec = _last_record(app)
    assert rec.status == 429
    assert rec.escalated is False
    assert rec.escalation_reasons == []
    assert {"model": "-", "reason": "paid_spillway_pass"} in rec.skipped
    assert {"model": "deepinfra/deepseek-v4-flash",
            "reason": "quality_below_required"} in rec.skipped


def test_spillway_level5_quality5_serves_premium(tmp_path):
    reg = _registry_below_l4(tmp_path, premium_quality=5)
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L5})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] == "premium"
    assert r.headers["X-Sovereign-Model"] == "deepseek/deepseek-v4-pro"
    rec = _last_record(app)
    assert rec.escalation_reasons == [SPILLWAY]


def test_spillway_level5_falls_back_to_quality4(tmp_path):
    # Premium rated 4 across the board: the spillway's own level-5
    # fallback (mirroring normal selection) lets quality 4 serve
    # rather than failing the turn.
    reg = _registry_below_l4(tmp_path, premium_quality=4)
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L5})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepseek/deepseek-v4-pro"
    rec = _last_record(app)
    assert rec.escalation_reasons == [SPILLWAY]
    assert {"model": "-", "reason": "level5_fallback_quality4"} in rec.skipped


# ----------------------------------------------------------------------
# execution-time engagement: free candidates existed, all failed
# ----------------------------------------------------------------------
def _fail_free_handler(model, body, n):
    if model == "openai/gpt-oss-120b":
        return httpx.Response(500, json={"error": {"message": "boom"}})
    return _ok_handler(model, body, n)


def test_execution_exhaustion_engages_spillway_json(tmp_path):
    # Small sensitive L4 turn: the ONLY first-pass candidate is
    # groq/gpt-oss-120b (paid is behind the sensitive wall). It fails
    # upstream — instead of the old 429, the spillway engages and the
    # cheapest paid model serves.
    app, calls = build_recording_app(tmp_path, _fail_free_handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepinfra/deepseek-v4-flash"
    assert [c["model"] for c in calls] == [
        "openai/gpt-oss-120b", "deepseek-ai/DeepSeek-V4-Flash"]
    rec = _last_record(app)
    assert rec.status == 200
    assert rec.escalated is True
    assert rec.escalation_reasons == [SPILLWAY]
    assert {"model": "groq/gpt-oss-120b", "reason": "upstream_500"} \
        in rec.skipped


def test_execution_exhaustion_engages_spillway_streaming(tmp_path):
    app, calls = build_recording_app(tmp_path, _fail_free_handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT, stream=True),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert "Paid " in r.text and "answer" in r.text
    assert [c["model"] for c in calls] == [
        "openai/gpt-oss-120b", "deepseek-ai/DeepSeek-V4-Flash"]
    rec = _last_record(app)
    assert rec.status == 200
    assert rec.escalated is True
    assert rec.escalation_reasons == [SPILLWAY]


def test_all_fail_including_spillway_still_429(tmp_path):
    # FAIL_ALL shape: every model 500s, spillway entries included —
    # the spec 429 contract is preserved, just later.
    def fail_all(model, body, n):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    app, calls = build_recording_app(tmp_path, fail_all)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "60"
    # the free candidate AND spillway entries were all attempted
    attempted = {c["model"] for c in calls}
    assert "openai/gpt-oss-120b" in attempted
    assert "deepseek-ai/DeepSeek-V4-Flash" in attempted
    rec = _last_record(app)
    assert rec.status == 429
    assert SPILLWAY in rec.escalation_reasons


# ----------------------------------------------------------------------
# guardrails: emergency veto; explicit premium is not unlocked
# ----------------------------------------------------------------------
def test_emergency_switch_vetoes_spillway(tmp_path):
    import sys
    sys.path.insert(0, str(SRC))
    from gateway import GatewayConfig, create_app

    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha(RAW_KEYS["admin"]),
        "clients": [
            {"id": "hermes", "key_sha256": _sha(RAW_KEYS["hermes"]),
             "lanes": ["private-free", "paid"], "premium_allowed": False,
             "daily_target_usd": 1.0},
        ],
    }))
    cfg = GatewayConfig(
        state_dir=tmp_path / "state",
        clients_path=clients_file,
        registry_path=registry_variant(tmp_path, groq_tpm=8000),
        provider_keys={"groq": "fake", "openrouter": "fake",
                       "deepinfra": "fake", "ollama": "fake",
                       "openai": "fake", "deepseek": "fake", "zai": "fake"},
        emergency_enabled=True, emergency_daily_usd=0.0,  # tripped at $0
    )
    calls: list[dict] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return _ok_handler(body["model"], body, len(calls))

    app = create_app(cfg, upstream_transport=httpx.MockTransport(transport_handler))
    with TestClient(app) as client:
        # free still serves under the switch (it only kills paid lanes)
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
        assert r.status_code == 200
        assert r.headers["X-Sovereign-Lane"] == "private-free"
        # the dead-end shape has nowhere to spill: veto -> 429
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
        assert r.status_code == 429
    assert all(c["model"] == "openai/gpt-oss-120b" for c in calls)


def test_premium_alias_unchanged_by_spillway(tmp_path):
    app, calls = build_recording_app(tmp_path, _ok_handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("design the cutover plan",
                                       model="premium"),
                        headers=auth("openclaw"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] == "premium"
    assert r.headers["X-Sovereign-Escalated"] == "false"
    rec = _last_record(app)
    assert rec.escalated is False
    assert rec.escalation_reasons == []
    assert not any(s.startswith("paid_spillway") for s in rec.signals)


# ----------------------------------------------------------------------
# shadow mode predicts the spillway; registry/version shape
# ----------------------------------------------------------------------
def test_shadow_reflects_spillway(tmp_path):
    # Premise: free is exhausted (Groq TPM window) — stated via the
    # pre-Developer-plan 8k TPM variant (2026-11-09 config change).
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000))
    with TestClient(app) as client:
        r = client.post("/v1/shadow",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    body = r.json()
    assert body["would_route_to"]["model"] == "deepinfra/deepseek-v4-flash"
    assert body["would_route_to"]["lane"] == "paid"
    assert calls == []  # shadow never calls upstream


def test_registry_and_version_shape_v056():
    import sys
    sys.path.insert(0, str(SRC))
    import gateway
    from registry import load_registry
    assert gateway.VERSION == "0.5.7"  # v0.5.7: warn-only + dead-lane
    entries = {e.id: e for e in load_registry(SRC / "registry.json").entries}
    assert len(entries) == 15  # +2 HF probe entries, 2026-11-09
    # the rescue pin is gone from code and data alike
    assert not hasattr(entries["premium/gpt-5.5"], "rescue_eligible")
    assert entries["deepseek/deepseek-v4-pro"].enabled is True
    assert entries["zai/glm-5.3"].enabled is True
    # Meta billing still unresolved: present but disabled, so the
    # spillway cannot land on it either.
    assert entries["meta/muse-spark-1.1"].enabled is False
    # SiliconFlow (v0.5.6): GLM-5.3 enabled in the paid lane, V4 Pro
    # present but disabled; both sensitive-excluded (see
    # tests/test_siliconflow_slot.py for the behavioural proofs).
    assert entries["siliconflow/glm-5.3"].enabled is True
    assert entries["siliconflow/glm-5.3"].spillway_sensitive_excluded is True
    assert entries["siliconflow/deepseek-v4-pro"].enabled is False
    assert entries["siliconflow/deepseek-v4-pro"].spillway_sensitive_excluded is True
