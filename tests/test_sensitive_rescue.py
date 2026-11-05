"""v0.5.3 — sensitive no-candidate rescue (auto-trial fix, 2026-11-05).

Origin: the 2026-11-04 OpenClaw auto trial. Under alias=auto, sensitive
(basic_auth_url) level-4 infra turns found NO eligible candidate — the
only quality-eligible private-free model was TPM-limited, paid lanes are
not cleared for sensitive data, premium is barred under auto — and the
gateway returned 429 outright (20 failures in two bursts).

The fix is a narrow last resort: alias=auto + sensitive-classified +
zero eligible candidates after ALL normal filtering -> ONE premium
fallback attempt, recorded as escalated with the distinct reason
"sensitive_no_eligible_lane". It is not a general premium escalation
path, it clears no provider for sensitive data, and clients without
premium permission (hermes) never qualify.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from conftest import _chunk, _sse, auth, chat_body  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"

# basic_auth_url detector: scheme://user:password@host (password >= 4).
SENSITIVE_TEXT = (
    "restart the nginx container, then check the deploy endpoint "
    "https://deploy:s3cr3tpass@192.168.0.166:8443/status and report back"
)
PLAIN_TEXT = "restart the nginx container and report the service status"
L4 = {"X-Sovereign-Level": "4"}


def _completion(model: str, content: str) -> dict:
    return {
        "id": "chatcmpl-fake", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                  "total_tokens": 110},
    }


def _registry_below_l4(tmp_path: Path) -> Path:
    """Production registry with every non-premium model's quality
    flattened to 3 — below the level-4 floor (4) for every task class.
    At L4 the only quality-eligible entry left is premium, so normal
    filtering deterministically yields the zero-candidate state the
    2026-11-04 bursts hit organically via a TPM skip."""
    data = json.loads((SRC / "registry.json").read_text())
    for m in data["models"]:
        if m["lane"] != "premium":
            m["quality"] = {k: 3 for k in m["quality"]}
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(data))
    return p


def _ok_handler(model, body, n):
    if body.get("stream"):
        chunks = [
            _chunk(model, {"role": "assistant", "content": "Rescue "}),
            _chunk(model, {"content": "answer"}),
            _chunk(model, {}, finish="stop"),
        ]
        usage = {"prompt_tokens": 100, "completion_tokens": 6,
                 "total_tokens": 106}
        return httpx.Response(200, content=_sse(chunks, usage),
                              headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json=_completion(model, f"answer from {model}"))


# ----------------------------------------------------------------------
# the rescue fires: sensitive + L4 + auto + zero candidates
# ----------------------------------------------------------------------
def test_sensitive_l4_auto_no_candidates_rescues_to_premium(tmp_path):
    reg = _registry_below_l4(tmp_path)
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("openclaw"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "premium/gpt-5.5"
    assert r.headers["X-Sovereign-Lane"] == "premium"
    assert r.headers["X-Sovereign-Escalated"] == "true"
    # exactly ONE upstream attempt, on premium, with its budget dialect
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-5.5"
    assert "max_completion_tokens" in calls[0]
    assert "max_tokens" not in calls[0]
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.status == 200
    assert rec.data_class == "sensitive"
    assert rec.lane == "premium"
    assert rec.escalated is True
    assert rec.escalation_reasons == ["sensitive_no_eligible_lane"]
    assert "sensitive_rescue:premium/gpt-5.5" in rec.signals
    assert rec.cost_usd > 0  # premium pricing applies normally


def test_sensitive_l4_auto_no_candidates_rescues_to_premium_streaming(tmp_path):
    # The 2026-11-04 failures were all streaming turns (stream=true).
    reg = _registry_below_l4(tmp_path)
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT, stream=True),
                        headers={**auth("openclaw"), **L4})
    assert r.status_code == 200
    assert "Rescue " in r.text and "answer" in r.text
    assert len(calls) == 1 and calls[0]["model"] == "gpt-5.5"
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.stream is True
    assert rec.status == 200
    assert rec.lane == "premium"
    assert rec.escalated is True
    assert rec.escalation_reasons == ["sensitive_no_eligible_lane"]


# ----------------------------------------------------------------------
# the rescue does NOT fire when a candidate exists
# ----------------------------------------------------------------------
def test_sensitive_l4_auto_with_candidate_no_premium(tmp_path):
    # Production registry: groq/gpt-oss-120b is quality-eligible for L4
    # infra and inside its TPM window for a small prompt.
    app, calls = build_recording_app(tmp_path, _ok_handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("openclaw"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] == "private-free"
    assert r.headers["X-Sovereign-Escalated"] == "false"
    assert all(c["model"] != "gpt-5.5" for c in calls)
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.escalated is False
    assert rec.escalation_reasons == []


# ----------------------------------------------------------------------
# non-sensitive zero-candidate still fails exactly as before
# ----------------------------------------------------------------------
def test_non_sensitive_no_candidates_still_429(tmp_path):
    reg = _registry_below_l4(tmp_path)
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(PLAIN_TEXT),
                        headers={**auth("openclaw"), **L4})
    assert r.status_code == 429
    assert calls == []  # no upstream attempt, premium included
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.status == 429
    assert rec.escalated is False
    assert rec.escalation_reasons == []


# ----------------------------------------------------------------------
# hermes never qualifies (premium denial intact on the rescue path)
# ----------------------------------------------------------------------
def test_hermes_sensitive_no_candidates_never_rescued(tmp_path):
    reg = _registry_below_l4(tmp_path)
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 429
    assert calls == []
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.status == 429
    assert rec.escalated is False


# ----------------------------------------------------------------------
# v0.5.4 rescue pin: cheaper premium entries never rescue (2026-11-05)
# ----------------------------------------------------------------------
def test_rescue_pinned_to_gpt55_despite_cheaper_premium(tmp_path):
    # The production registry now carries enabled premium entries far
    # cheaper than gpt-5.5 (deepseek $1.32/$3.96, glm $1.40/$4.40).
    # Cost-first rescue selection would land on deepseek; the pin must
    # keep rescued sensitive traffic on premium/gpt-5.5 exactly.
    reg = _registry_below_l4(tmp_path)
    data = json.loads(reg.read_text())
    premium = {m["id"]: m for m in data["models"] if m["lane"] == "premium"}
    assert premium["deepseek/deepseek-v4-pro"].get("enabled", True) is True
    assert premium["zai/glm-5.3"].get("enabled", True) is True
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("openclaw"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "premium/gpt-5.5"
    assert len(calls) == 1 and calls[0]["model"] == "gpt-5.5"
    rec = app.state.telemetry.recent(0)[-1]
    assert "sensitive_rescue:premium/gpt-5.5" in rec.signals
    assert rec.escalation_reasons == ["sensitive_no_eligible_lane"]


def test_rescue_unavailable_when_gpt55_disabled(tmp_path):
    # With the only rescue-eligible entry disabled, the rescue must NOT
    # fall through to a cheaper premium entry: the request 429s, the
    # same as a world with no rescue at all.
    data = json.loads((SRC / "registry.json").read_text())
    for m in data["models"]:
        if m["lane"] != "premium":
            m["quality"] = {k: 3 for k in m["quality"]}
        if m["id"] == "premium/gpt-5.5":
            m["enabled"] = False
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(data))
    app, calls = build_recording_app(tmp_path, _ok_handler, registry_path=reg)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT),
                        headers={**auth("openclaw"), **L4})
    assert r.status_code == 429
    assert calls == []
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.status == 429
    assert rec.escalated is False


def test_premium_lane_registry_shape():
    # The pin lives in the shipped registry data, not just in code.
    import sys
    sys.path.insert(0, str(SRC))
    from registry import load_registry
    entries = {e.id: e for e in load_registry(SRC / "registry.json").entries}
    assert entries["premium/gpt-5.5"].rescue_eligible is True
    assert entries["deepseek/deepseek-v4-pro"].enabled is True
    assert entries["deepseek/deepseek-v4-pro"].rescue_eligible is False
    assert entries["zai/glm-5.3"].enabled is True
    assert entries["zai/glm-5.3"].rescue_eligible is False
    # Meta billing unresolved at deploy time: present but disabled.
    assert entries["meta/muse-spark-1.1"].enabled is False
    assert entries["meta/muse-spark-1.1"].rescue_eligible is False


# ----------------------------------------------------------------------
# the premium alias path is unchanged (no rescue bookkeeping)
# ----------------------------------------------------------------------
def test_premium_alias_unchanged_by_rescue(tmp_path):
    app, calls = build_recording_app(tmp_path, _ok_handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("design the cutover plan",
                                       model="premium"),
                        headers=auth("openclaw"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] == "premium"
    assert r.headers["X-Sovereign-Escalated"] == "false"
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.escalated is False
    assert rec.escalation_reasons == []
    assert not any(s.startswith("sensitive_rescue") for s in rec.signals)
