"""Shadow mode + pulled-alerts outbox (ported from the parallel GitHub
build in the 2026-11-02 convergence).

Shadow contract: POST /v1/shadow returns the routing decision and cost
estimate with NO upstream call, NO usage ledger entry and NO route-log
line. Alerts contract: GET /admin/alerts?after=<id> returns alerts with
id > after in order, and polling counts as the alert-chain heartbeat.
"""

from __future__ import annotations

import json
import time

from conftest import auth, chat_body


# ----------------------------------------------------------------------
# shadow mode
# ----------------------------------------------------------------------
def test_shadow_requires_auth(client):
    r = client.post("/v1/shadow", json=chat_body("hello"))
    assert r.status_code == 401


def test_shadow_decision_matches_chat_decision(client):
    text = "restart the nginx container"
    r = client.post("/v1/chat/completions", json=chat_body(text),
                    headers=auth("hermes"))
    assert r.status_code == 200
    chat_model = r.headers["X-Sovereign-Model"]

    r = client.post("/v1/shadow", json=chat_body(text), headers=auth("hermes"))
    assert r.status_code == 200
    body = r.json()
    assert body["shadow"] is True
    assert body["would_route_to"]["model"] == chat_model
    assert body["level"] >= 1
    assert body["candidates"], "shadow must list considered candidates"


def test_shadow_makes_no_upstream_call_and_writes_nothing(client, app, tmp_path):
    # Baseline: one real chat so counters are non-zero and comparable.
    r = client.post("/v1/chat/completions", json=chat_body("hello"),
                    headers=auth("hermes"))
    assert r.status_code == 200
    usage_before = client.get("/v1/usage", headers=auth("hermes")).json()
    routes_file = tmp_path / "state" / "routes.jsonl"
    lines_before = len(routes_file.read_text().splitlines()) \
        if routes_file.exists() else 0

    r = client.post("/v1/shadow",
                    json=chat_body("delete the stack and migrate it"),
                    headers=auth("hermes"))
    assert r.status_code == 200

    usage_after = client.get("/v1/usage", headers=auth("hermes")).json()
    assert usage_after == usage_before, "shadow must not record usage"
    lines_after = len(routes_file.read_text().splitlines()) \
        if routes_file.exists() else 0
    assert lines_after == lines_before, "shadow must not write route lines"


def test_shadow_enforces_lane_rules(client):
    r = client.post("/v1/shadow", json=chat_body("hi", model="premium"),
                    headers=auth("hermes"))
    assert r.status_code == 403
    r = client.post("/v1/shadow", json=chat_body("hi", model="premium"),
                    headers=auth("openclaw"))
    assert r.status_code == 200
    assert r.json()["would_route_to"]["lane"] == "premium"


def test_shadow_classifies_hard_tasks_high(client):
    r = client.post("/v1/shadow",
                    json=chat_body("delete the old docker stack and migrate "
                                   "it to the new host, then debug the "
                                   "failed restore"),
                    headers=auth("hermes"))
    assert r.status_code == 200
    assert r.json()["level"] >= 4


# ----------------------------------------------------------------------
# pulled-alerts outbox
# ----------------------------------------------------------------------
def test_alerts_requires_admin(client):
    assert client.get("/admin/alerts").status_code == 401
    assert client.get("/admin/alerts", headers=auth("hermes")).status_code == 401
    assert client.get("/admin/alerts", headers=auth("admin")).status_code == 200


def test_alerts_outbox_checkpoints_by_id(client, app):
    app.state.store.add_alert("test_kind", "hermes", "dedup-1",
                              "first test alert", now=time.time())
    app.state.store.add_alert("test_kind2", "openclaw", "dedup-2",
                              "second test alert", now=time.time())

    r = client.get("/admin/alerts", headers=auth("admin"))
    body = r.json()
    kinds = [a["kind"] for a in body["alerts"]]
    assert "test_kind" in kinds and "test_kind2" in kinds
    last_id = body["last_id"]
    assert last_id == body["alerts"][-1]["id"]

    r = client.get(f"/admin/alerts?after={last_id}", headers=auth("admin"))
    body = r.json()
    assert body["alerts"] == []
    assert body["last_id"] == last_id


def test_alerts_poll_is_a_heartbeat(client):
    r = client.get("/health")
    assert r.json()["alert_chain"]["status"] == "degraded"
    client.get("/admin/alerts", headers=auth("admin"))
    r = client.get("/health")
    assert r.json()["alert_chain"]["status"] == "ok"
