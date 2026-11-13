"""v0.7.0 — pin hardening per ruling J-1 (2026-11-12, Cycle 1 aspect G).

Jordan's ruling, recorded in docs/cycles/cycle-01-plan.md: pins are
BOUND to the two hard boundaries —

  (a) a pin for a DISABLED entry does not serve it;
  (b) a pin cannot carry SENSITIVE data past the sensitive wall (the
      per-model spillway_sensitive_excluded flag, the public-lane bar,
      and provider clearance bind pins as they bind normal selection).

Quality floor, cost order, context fit and provider limits remain
pin-overridable — that overridability is what makes pins useful for
testing and for the voice stack, so an eligible-but-quality-skipped
pin must still serve.

And /v1/shadow must predict the live pin outcome exactly: the
pre-v0.7.0 shadow only reordered a pin already in candidates and
diverged on every force-insert case (the ADD-03 divergence). Every
case below runs shadow-then-live in one fresh app and asserts
would_route_to == the model actually served.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import _sha, auth, build_app, chat_body, fake_upstream  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"

# Credential-shaped content (basic-auth URL detector) => data_class
# sensitive, same trigger as the spillway suite.
SENSITIVE_TEXT = (
    "restart the nginx container, then check the deploy endpoint "
    "https://deploy:s3cr3tpass@192.168.0.166:8443/status and report back"
)
PLAIN_TEXT = "restart the nginx container and report the service status"


def _last_record(app):
    return app.state.telemetry.recent(0)[-1]


def _wide_app(tmp_path):
    """An app whose client holds ALL FOUR lanes (the conftest clients
    lack public-only-free, which the sensitive x public pin case needs
    to reach the pin wall rather than the handler's 403 lane gate)."""
    import sys
    sys.path.insert(0, str(SRC))
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
        registry_path=SRC / "registry.json",
        provider_keys={"groq": "fake", "openrouter": "fake",
                       "deepinfra": "fake", "ollama": "fake",
                       "openai": "fake", "deepseek": "fake", "zai": "fake"},
    )
    return create_app(cfg, upstream_transport=httpx.MockTransport(fake_upstream))


WIDE_AUTH = {"Authorization": "Bearer wide-test-key"}


def _shadow_then_live(tmp_path, who_headers, body, build=build_app):
    """Run shadow first (it writes nothing), then the live request, in
    one fresh app; return (shadow_body, live_response, app)."""
    app = build(tmp_path)
    with TestClient(app) as client:
        s = client.post("/v1/shadow", json=body, headers=who_headers)
        assert s.status_code == 200, s.text
        r = client.post("/v1/chat/completions", json=body, headers=who_headers)
    return s.json(), r, app


def _assert_parity(shadow, live_response):
    assert live_response.status_code == 200, live_response.text
    assert shadow["would_route_to"] is not None
    assert shadow["would_route_to"]["model"] == \
        live_response.headers["X-Sovereign-Model"]


# ----------------------------------------------------------------------
# (a) disabled pins never serve — plain or sensitive
# ----------------------------------------------------------------------

def test_pin_disabled_entry_plain_does_not_serve(tmp_path):
    body = chat_body(PLAIN_TEXT + " [case d1]", model="openrouter/kimi-k2.6")
    shadow, r, app = _shadow_then_live(tmp_path, auth("hermes"), body)
    _assert_parity(shadow, r)
    assert r.headers["X-Sovereign-Model"] != "openrouter/kimi-k2.6"
    rec = _last_record(app)
    assert {"model": "openrouter/kimi-k2.6",
            "reason": "pin_blocked:disabled"} in rec.skipped
    assert {"model": "openrouter/kimi-k2.6",
            "reason": "pin_blocked:disabled"} in shadow["skipped"]


def test_pin_disabled_entry_sensitive_does_not_serve(tmp_path):
    body = chat_body(SENSITIVE_TEXT + " [case d2]", model="openrouter/kimi-k2.6")
    shadow, r, app = _shadow_then_live(tmp_path, auth("hermes"), body)
    _assert_parity(shadow, r)
    assert r.headers["X-Sovereign-Model"] != "openrouter/kimi-k2.6"
    rec = _last_record(app)
    assert {"model": "openrouter/kimi-k2.6",
            "reason": "pin_blocked:disabled"} in rec.skipped


# ----------------------------------------------------------------------
# (b) the sensitive wall binds pins
# ----------------------------------------------------------------------

def test_pin_sensitive_excluded_entry_blocked(tmp_path):
    # Every OpenRouter paid route is spillway_sensitive_excluded.
    body = chat_body(SENSITIVE_TEXT + " [case x1]",
                     model="openrouter/deepseek-v4-flash")
    shadow, r, app = _shadow_then_live(tmp_path, auth("hermes"), body)
    _assert_parity(shadow, r)
    assert r.headers["X-Sovereign-Model"] != "openrouter/deepseek-v4-flash"
    assert r.headers["X-Sovereign-Lane"] == "private-free"
    rec = _last_record(app)
    assert {"model": "openrouter/deepseek-v4-flash",
            "reason": "pin_blocked:sensitive_spillway_excluded"} in rec.skipped


def test_pin_sensitive_uncleared_paid_entry_blocked(tmp_path):
    # DeepInfra direct is not excluded and not cleared: the clearance
    # wall (not the exclusion flag) is what stops this pin.
    body = chat_body(SENSITIVE_TEXT + " [case x2]",
                     model="deepinfra/deepseek-v4-flash")
    shadow, r, app = _shadow_then_live(tmp_path, auth("hermes"), body)
    _assert_parity(shadow, r)
    assert r.headers["X-Sovereign-Model"] != "deepinfra/deepseek-v4-flash"
    rec = _last_record(app)
    assert {"model": "deepinfra/deepseek-v4-flash",
            "reason": "pin_blocked:sensitive_not_cleared"} in rec.skipped


def test_pin_sensitive_uncleared_premium_entry_blocked(tmp_path):
    # Pinning a premium entry by name makes the request an explicit
    # premium request (premium_only selection), so with the pin walled
    # there is no eligible premium candidate left: the turn refuses
    # (429) rather than leaking sensitive data to the uncleared entry
    # or silently downgrading. Shadow predicts exactly that (no route).
    body = chat_body(SENSITIVE_TEXT + " [case x3]",
                     model="deepseek/deepseek-v4-pro")
    shadow, r, app = _shadow_then_live(tmp_path, auth("openclaw"), body)
    assert shadow["would_route_to"] is None
    assert r.status_code == 429
    assert {"model": "deepseek/deepseek-v4-pro",
            "reason": "pin_blocked:sensitive_not_cleared"} in shadow["skipped"]
    rec = _last_record(app)
    assert {"model": "deepseek/deepseek-v4-pro",
            "reason": "pin_blocked:sensitive_not_cleared"} in rec.skipped


def test_pin_sensitive_public_lane_entry_blocked(tmp_path):
    body = chat_body(SENSITIVE_TEXT + " [case x4]", model="zai/glm-4.7-flash")
    shadow, r, app = _shadow_then_live(tmp_path, WIDE_AUTH, body, build=_wide_app)
    _assert_parity(shadow, r)
    assert r.headers["X-Sovereign-Model"] != "zai/glm-4.7-flash"
    rec = _last_record(app)
    assert {"model": "zai/glm-4.7-flash",
            "reason": "pin_blocked:sensitive_public_lane"} in rec.skipped


# ----------------------------------------------------------------------
# eligible pins still serve — including wall-exempt and overridable ones
# ----------------------------------------------------------------------

def test_pin_eligible_paid_entry_serves_plain(tmp_path):
    body = chat_body(PLAIN_TEXT + " [case e1]",
                     model="deepinfra/deepseek-v4-flash")
    shadow, r, app = _shadow_then_live(tmp_path, auth("hermes"), body)
    _assert_parity(shadow, r)
    assert r.headers["X-Sovereign-Model"] == "deepinfra/deepseek-v4-flash"


def test_pin_sensitive_private_free_entry_serves(tmp_path):
    # The private-free lane is the sensitive wall's standing exemption.
    body = chat_body(SENSITIVE_TEXT + " [case e2]", model="groq/gpt-oss-120b")
    shadow, r, app = _shadow_then_live(tmp_path, auth("hermes"), body)
    _assert_parity(shadow, r)
    assert r.headers["X-Sovereign-Model"] == "groq/gpt-oss-120b"
    assert r.headers["X-Sovereign-Lane"] == "private-free"


def test_pin_public_entry_serves_plain_for_lane_holder(tmp_path):
    body = chat_body(PLAIN_TEXT + " [case e3]", model="zai/glm-4.7-flash")
    shadow, r, app = _shadow_then_live(tmp_path, WIDE_AUTH, body, build=_wide_app)
    _assert_parity(shadow, r)
    assert r.headers["X-Sovereign-Model"] == "zai/glm-4.7-flash"


def test_pin_quality_ineligible_entry_still_serves(tmp_path):
    # J-1's other half: the quality floor is pin-OVERRIDABLE. A level-5
    # turn requires quality 5; groq/gpt-oss-20b is quality 2 across the
    # board, so selection skips it — the pin must still serve it.
    body = chat_body(PLAIN_TEXT + " [case e4]", model="groq/gpt-oss-20b")
    headers = {**auth("hermes"), "X-Sovereign-Level": "5"}
    shadow, r, app = _shadow_then_live(tmp_path, headers, body)
    _assert_parity(shadow, r)
    assert r.headers["X-Sovereign-Model"] == "groq/gpt-oss-20b"
    rec = _last_record(app)
    assert not any(s["model"] == "groq/gpt-oss-20b"
                   and s["reason"].startswith("pin_blocked")
                   for s in rec.skipped)
