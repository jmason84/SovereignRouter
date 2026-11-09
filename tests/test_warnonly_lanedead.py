"""v0.5.7 — warn-only emergency + dead-lane alerts (owner ruling
2026-11-09, after the DeepInfra-402 incident).

Jordan's restatement of the router's point: "start cheap and move
up… give me the warning, but keep switching in the order I said."
Two behaviours:

  * the $25/day emergency point ALERTS but never switches the paid
    lanes off (SOVEREIGN_EMERGENCY_WARN_ONLY=1) — replacing the
    2026-11-01 hard switch, which on 2026-11-09 removed every paid
    rung at once and left Hermes/OpenClaw facing gateway 429s;
  * the first account-class upstream failure (401/402/403) or daily
    token-cap exhaustion for a model each day records a ``lane_dead``
    alert, so a dead cheap rung surfaces within one alert-chain poll
    instead of burning silently for hours (DeepInfra died at 04:48;
    nobody knew until the switch tripped at 12:29).
"""

from __future__ import annotations

import time

import httpx
from fastapi.testclient import TestClient

from conftest import auth, chat_body, registry_variant  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402
from test_paid_spillway import BIG_SENSITIVE_TEXT, _completion, _ok_handler  # noqa: E402

from governor import EmergencyConfig  # noqa: E402

L4 = {"X-Sovereign-Level": "4"}


def _alerts(app, kind):
    return [a for a in app.state.store.alerts_since(time.time() - 3600)
            if a["kind"] == kind]


# ----------------------------------------------------------------------
# dead lane: Flash 402s, cascade serves the next rung, alert recorded
# ----------------------------------------------------------------------
def test_flash_402_alerts_lane_dead_and_cascade_continues(tmp_path):
    def handler(model, body, n):
        if model == "deepseek-ai/DeepSeek-V4-Flash":
            return httpx.Response(402, json={"error": {"message": "no credits"}})
        return _ok_handler(model, body, n)

    app, calls = build_recording_app(tmp_path, handler,
                        registry_path=registry_variant(tmp_path, groq_tpm=8000))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
        assert r.status_code == 200
        # Flash attempted first (cheapest paid), then the next rung up.
        assert [c["model"] for c in calls][:2] == [
            "deepseek-ai/DeepSeek-V4-Flash", "gpt-oss:120b"]
        assert r.headers["X-Sovereign-Model"] == "ollama/gpt-oss-120b"
        dead = _alerts(app, "lane_dead")
        assert len(dead) == 1 and "deepinfra" in dead[0]["message"]
        # A second identical turn does not duplicate the day's alert.
        r2 = client.post("/v1/chat/completions",
                         json=chat_body(BIG_SENSITIVE_TEXT),
                         headers={**auth("hermes"), **L4})
        assert r2.status_code == 200
        assert len(_alerts(app, "lane_dead")) == 1


def test_upstream_429_is_not_a_dead_lane(tmp_path):
    # Ordinary rate limits must NOT raise lane_dead — only
    # account-class failures (401/402/403) do.
    def handler(model, body, n):
        if model == "deepseek-ai/DeepSeek-V4-Flash":
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return _ok_handler(model, body, n)

    app, calls = build_recording_app(tmp_path, handler,
                        registry_path=registry_variant(tmp_path, groq_tpm=8000))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert _alerts(app, "lane_dead") == []


# ----------------------------------------------------------------------
# warn-only emergency: over the level, paid lanes stay ON
# ----------------------------------------------------------------------
def test_warn_only_gateway_keeps_serving_paid(tmp_path):
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000))
    app.state.governor.emergency = EmergencyConfig(
        enabled=True, daily_limit_usd=0.0, warn_only=True)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepinfra/deepseek-v4-flash"
    warns = _alerts(app, "emergency_paid_lane_switch")
    assert len(warns) == 1 and "REMAIN ON" in warns[0]["message"]


def test_hard_switch_still_vetoes_when_warn_only_off(tmp_path):
    # The Nov 1 behaviour remains available when warn-only is off —
    # v0.5.7 changes the deployed configuration, not the mechanism.
    app, calls = build_recording_app(tmp_path, _ok_handler,
                                     registry_path=registry_variant(tmp_path, groq_tpm=8000))
    app.state.governor.emergency = EmergencyConfig(
        enabled=True, daily_limit_usd=0.0, warn_only=False)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(BIG_SENSITIVE_TEXT),
                        headers={**auth("hermes"), **L4})
    assert r.status_code == 429
    assert calls == []
