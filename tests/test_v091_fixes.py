"""v0.9.1 gateway-level fix tests (owner's full-fix-set ruling,
2026-11-15):

  * F-15 — sensitive_no_cleared_provider fires on OUTCOMES (a paid /
    premium sensitive serve, or a zero-candidate sensitive request),
    never on a sensitive serve that lands inside the wall;
  * F-16 — openrouter/dots-3-note-preview-free and
    openrouter/north-mini-code-free are sensitive-excluded in every
    pass, pins included (they sat inside the wall via private-free);
  * F-17(a) — the TPD lane_dead cause states counted usage vs cap
    and the triggering request's estimate, and still dedups to one
    alert per entry per day.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import auth, build_app, chat_body, registry_variant  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402
from test_paid_spillway import BIG_SENSITIVE_TEXT, SENSITIVE_TEXT, _ok_handler  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src"
L4 = {"X-Sovereign-Level": "4"}
DOTS3 = "openrouter/dots-3-note-preview-free"
NORTH = "openrouter/north-mini-code-free"


def _alerts(app, kind):
    return [a for a in app.state.store.alerts_since(time.time() - 3600)
            if a["kind"] == kind]


def _last_route(tmp_path):
    lines = (tmp_path / "state" / "routes.jsonl").read_text().splitlines()
    return json.loads(lines[-1])


# ----------------------------------------------------------------------
# F-16: the two OpenRouter free routes are out of the sensitive wall
# ----------------------------------------------------------------------
# Premise for the three F-16 tests (2026-11-27, v0.10.2): both
# entries are DISABLED in the production registry (upstreams proven
# deleted), which would mask the F-16 mechanism entirely — a disabled
# entry is excluded for the wrong reason and invisible to non-
# sensitive selection. The variant re-enables both so the wall
# behaviour under test is what the assertions measure.
def _f16_client(tmp_path):
    app = build_app(
        tmp_path,
        registry_path=registry_variant(tmp_path, enable=(DOTS3, NORTH)))
    return app


def test_f16_sensitive_shadow_excludes_both_entries(tmp_path):
    app = _f16_client(tmp_path)
    with TestClient(app) as client:
        r = client.post("/v1/shadow", json=chat_body(SENSITIVE_TEXT),
                        headers=auth("hermes"))
    assert r.status_code == 200
    body = r.json()
    assert body["data_class"] == "sensitive"
    cand_ids = [c["model"] for c in body["candidates"]]
    assert DOTS3 not in cand_ids and NORTH not in cand_ids
    reasons = {(s["model"], s["reason"]) for s in body["skipped"]}
    assert (DOTS3, "sensitive_spillway_excluded") in reasons
    assert (NORTH, "sensitive_spillway_excluded") in reasons


def test_f16_nonsensitive_shadow_still_sees_both_entries(tmp_path):
    # The exclusion is sensitive-only: ordinary traffic keeps both
    # routes (north-mini is the default first pick for plain chat in
    # this registry — see test_gateway.py).
    app = _f16_client(tmp_path)
    with TestClient(app) as client:
        r = client.post("/v1/shadow",
                        json=chat_body("restart the nginx container"),
                        headers=auth("hermes"))
    assert r.status_code == 200
    cand_ids = [c["model"] for c in r.json()["candidates"]]
    assert NORTH in cand_ids


def test_f16_sensitive_pin_to_dots3_is_dropped(tmp_path):
    app = _f16_client(tmp_path)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(SENSITIVE_TEXT, model=DOTS3),
                        headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] != DOTS3
    rec = _last_route(tmp_path)
    assert {"model": DOTS3,
            "reason": "pin_blocked:sensitive_spillway_excluded"} in rec["skipped"]


# ----------------------------------------------------------------------
# F-15: outcome-only sensitive alerts
# ----------------------------------------------------------------------
def test_f15_sensitive_serve_inside_wall_raises_no_alert(client, app, tmp_path):
    r = client.post("/v1/chat/completions", json=chat_body(SENSITIVE_TEXT),
                    headers=auth("hermes"))
    assert r.status_code == 200
    # Served inside the wall (private-free lane): the pre-v0.9.1
    # alert would have fired here on the empty clearance list alone.
    rec = _last_route(tmp_path)
    assert rec["data_class"] == "sensitive" and rec["lane"] == "private-free"
    assert _alerts(app, "sensitive_no_cleared_provider") == []


def test_f15_sensitive_paid_spillway_serve_alerts(tmp_path):
    app, calls = build_recording_app(
        tmp_path, _ok_handler,
        registry_path=registry_variant(tmp_path, groq_tpm=8000))
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body(BIG_SENSITIVE_TEXT),
                    headers={**auth("hermes"), **L4})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "deepinfra/gpt-oss-120b"
    alerts = _alerts(app, "sensitive_no_cleared_provider")
    assert len(alerts) == 1
    assert "SERVED on the paid lane" in alerts[0]["message"]


def test_f15_sensitive_no_candidate_alerts(tmp_path):
    data = json.loads((SRC / "registry.json").read_text())
    all_ids = tuple(m["id"] for m in data["models"])
    app = build_app(tmp_path,
                    registry_path=registry_variant(tmp_path, disable=all_ids))
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions", json=chat_body(SENSITIVE_TEXT),
                    headers=auth("hermes"))
    assert r.status_code == 429
    alerts = _alerts(app, "sensitive_no_cleared_provider")
    assert len(alerts) == 1
    assert "NO candidate" in alerts[0]["message"]


def test_f15_nonsensitive_traffic_never_alerts(client, app):
    r = client.post("/v1/chat/completions",
                    json=chat_body("restart the nginx container"),
                    headers=auth("hermes"))
    assert r.status_code == 200
    assert _alerts(app, "sensitive_no_cleared_provider") == []


# ----------------------------------------------------------------------
# F-17(a): TPD lane-dead cause carries counted / cap / estimate
# ----------------------------------------------------------------------
def _registry_with_groq_tpd(tmp_path: Path, tpd: int) -> Path:
    data = json.loads((SRC / "registry.json").read_text())
    for m in data["models"]:
        if m["provider"] == "groq":
            m.setdefault("limits", {})["tpd"] = tpd
    p = tmp_path / "registry-groq-tpd.json"
    p.write_text(json.dumps(data))
    return p


def test_f17_tpd_cause_states_counted_cap_and_estimate(tmp_path):
    app = build_app(tmp_path,
                    registry_path=_registry_with_groq_tpd(tmp_path, 1000))
    text = "restart the nginx container and report the service status. " * 130
    with TestClient(app) as tc:
        r = tc.post("/v1/chat/completions", json=chat_body(text),
                    headers=auth("hermes"))
        assert r.status_code == 200  # cascade serves from other rungs
        dead = _alerts(app, "lane_dead")
        assert dead, "expected a lane_dead alert for the TPD-tripped groq rung"
        msg = dead[0]["message"]
        assert "0 / 1,000 tokens used today" in msg
        assert "this request's estimate" in msg
        assert "exceeds the cap" in msg
        # Dedup stability: a second, larger request must not mint a
        # second alert for the same entry (the key is the stable
        # cause class, not the live numbers).
        r2 = tc.post("/v1/chat/completions",
                     json=chat_body(text + " More detail follows. " * 40),
                     headers=auth("hermes"))
        assert r2.status_code == 200
        assert len(_alerts(app, "lane_dead")) == len(dead)
