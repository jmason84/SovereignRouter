"""Gateway end-to-end tests against the fake upstream (no real calls)."""

from __future__ import annotations

import json
import time

from conftest import RAW_KEYS, auth, chat_body

TOOLS = [{"type": "function",
          "function": {"name": "run_command",
                       "parameters": {"type": "object",
                                      "properties": {"command": {"type": "string"}}}}}]


# ----------------------------------------------------------------------
# auth matrix
# ----------------------------------------------------------------------
def test_no_key_is_401(client):
    r = client.post("/v1/chat/completions", json=chat_body("hello"))
    assert r.status_code == 401


def test_wrong_key_is_401(client):
    r = client.post("/v1/chat/completions", json=chat_body("hello"),
                    headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_each_client_key_works(client):
    for who in ("hermes", "openclaw", "n8n"):
        r = client.post("/v1/chat/completions", json=chat_body("hello"),
                        headers=auth(who))
        assert r.status_code == 200, who


def test_admin_endpoints_require_admin_key(client):
    assert client.get("/admin/costs").status_code == 401
    assert client.get("/admin/costs", headers=auth("hermes")).status_code == 401
    assert client.get("/admin/costs", headers=auth("admin")).status_code == 200
    assert client.get("/admin/routes", headers=auth("admin")).status_code == 200


def test_models_lists_aliases(client):
    r = client.get("/v1/models")
    ids = {m["id"] for m in r.json()["data"]}
    assert {"auto", "cheap", "strong", "premium", "code", "long"} <= ids


# ----------------------------------------------------------------------
# happy path + route headers
# ----------------------------------------------------------------------
def test_route_headers_present(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("restart the nginx container"),
                    headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "groq/gpt-oss-120b"
    assert r.headers["X-Sovereign-Level"] == "3"
    assert r.headers["X-Sovereign-Task"] == "infra"
    assert r.headers["X-Sovereign-Lane"] == "private-free"
    assert r.headers["X-Sovereign-Escalated"] == "false"
    assert "X-Sovereign-Cost-Estimate" in r.headers


def test_level_override_header(client):
    r = client.post("/v1/chat/completions", json=chat_body("hello there"),
                    headers={**auth("hermes"), "X-Sovereign-Level": "4"})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Level"] == "4"


def test_large_prompt_skips_small_free_models(client):
    # ~15k tokens: Groq's 8k tokens/min window can never serve it, so the
    # router must skip Groq instead of waiting (v0.3.3 lesson).
    big = "word " * 15000
    r = client.post("/v1/chat/completions", json=chat_body(big),
                    headers=auth("hermes"))
    assert r.status_code == 200
    assert not r.headers["X-Sovereign-Model"].startswith("groq/")


# ----------------------------------------------------------------------
# lane permissions / error contract
# ----------------------------------------------------------------------
def test_premium_alias_forbidden_for_hermes(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("hello", model="premium"),
                    headers=auth("hermes"))
    assert r.status_code == 403


def test_premium_model_name_forbidden_for_hermes(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("hello", model="premium/gpt-5.5"),
                    headers=auth("hermes"))
    assert r.status_code == 403


def test_openclaw_premium_alias_uses_premium_lane(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("design the cutover plan", model="premium"),
                    headers=auth("openclaw"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] == "premium"
    # v0.5.4 (2026-11-05 premium-lane expansion): normal premium
    # selection is cost-first, so a quality-eligible premium request
    # lands on the cheapest premium entry (deepseek-v4-pro), not on
    # gpt-5.5. gpt-5.5 remains the top rung for classes where it is
    # the only quality-5 entry (and, by price, the spillway's last resort).
    assert r.headers["X-Sovereign-Model"] == "deepseek/deepseek-v4-pro"


def test_hermes_level5_never_gets_premium(client):
    r = client.post("/v1/chat/completions", json=chat_body("hello"),
                    headers={**auth("hermes"), "X-Sovereign-Level": "5"})
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] != "premium"


def test_429_when_all_candidates_fail(client):
    r = client.post("/v1/chat/completions", json=chat_body("FAIL_ALL please"),
                    headers=auth("hermes"))
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "60"
    assert r.json()["detail"]["skipped"]  # skip reasons are surfaced


def test_failover_to_next_model_on_429(client):
    r = client.post(
        "/v1/chat/completions",
        json=chat_body("restart the nginx container FAIL::openai/gpt-oss-120b"),
        headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] != "groq/gpt-oss-120b"


# ----------------------------------------------------------------------
# budget is never a refusal (spec decision 2026-10-31)
# ----------------------------------------------------------------------
def test_no_budget_refusal_when_above_target(client, app):
    from quota import UsageEvent
    app.state.store.record_usage(UsageEvent(
        ts=time.time(), client_id="hermes", provider="deepinfra",
        model_id="deepinfra/deepseek-v4-flash", lane="paid", level=2,
        task_class="chat", input_tokens=1000, output_tokens=100,
        cached_tokens=0, cost_usd=5.0, estimated=False))  # 5x the $1 target
    r = client.post("/v1/chat/completions",
                    json=chat_body("summarize this log file please"),
                    headers=auth("hermes"))
    assert r.status_code == 200


def test_killswitch_blocks_and_unblocks(client):
    r = client.post("/admin/killswitch", json={"client_id": "hermes", "active": True},
                    headers=auth("admin"))
    assert r.status_code == 200
    r = client.post("/v1/chat/completions", json=chat_body("hello"),
                    headers=auth("hermes"))
    assert r.status_code == 403
    client.post("/admin/killswitch", json={"client_id": "hermes", "active": False},
                headers=auth("admin"))
    r = client.post("/v1/chat/completions", json=chat_body("hello"),
                    headers=auth("hermes"))
    assert r.status_code == 200


# ----------------------------------------------------------------------
# validation + escalation
# ----------------------------------------------------------------------
def test_empty_answer_escalates_to_stronger_model(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("write a function to parse csv PLEASE_EMPTY"),
                    headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Escalated"] == "true"
    assert r.headers["X-Sovereign-Model"] == "deepinfra/deepseek-v4-flash"


def test_bad_tool_call_escalates(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("use the function BADTOOL", tools=TOOLS),
                    headers=auth("hermes"))
    assert r.status_code == 200
    # free models emit bad args; whoever finally answers must not be a
    # free model returning the malformed call as-is
    assert r.headers["X-Sovereign-Escalated"] == "true"


def test_unknown_tool_escalates(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("use the function UNKNOWNTOOL", tools=TOOLS),
                    headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Escalated"] == "true"


def test_json_mode_bad_json_escalates(client):
    r = client.post(
        "/v1/chat/completions",
        json=chat_body("give me function data BADJSON",
                       response_format={"type": "json_object"}),
        headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Escalated"] == "true"


# ----------------------------------------------------------------------
# sensitive routing
# ----------------------------------------------------------------------
def test_sensitive_request_stays_off_paid_lanes(client, app):
    r = client.post(
        "/v1/chat/completions",
        json=chat_body("here is my key sk-abc123def456ghi789jkl012 please store it"),
        headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Lane"] == "private-free"
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.data_class == "sensitive"
    reasons = {s["reason"] for s in rec.skipped}
    assert "sensitive_not_cleared" in reasons
    # the secret itself must never appear in telemetry
    assert "sk-abc123" not in rec.to_json()


def test_sensitive_log_only_mode_does_not_reroute(tmp_path):
    from conftest import build_app
    app2 = build_app(tmp_path / "b", sensitivity_log_only=True)
    from fastapi.testclient import TestClient
    with TestClient(app2) as tc:
        r = tc.post("/v1/chat/completions",
                    json=chat_body("key sk-abc123def456ghi789jkl012"),
                    headers=auth("hermes"))
        assert r.status_code == 200
        rec = app2.state.telemetry.recent(0)[-1]
        assert rec.data_class == "internal"          # routing unchanged
        assert rec.sensitive_detectors               # ...but logged (names only)


# ----------------------------------------------------------------------
# quota / usage scoping + health / alert chain
# ----------------------------------------------------------------------
def test_usage_scoped_to_caller(client):
    client.post("/v1/chat/completions", json=chat_body("hello"),
                headers=auth("hermes"))
    r = client.get("/v1/usage", headers=auth("openclaw"))
    assert r.json()["today"]["total_requests"] == 0
    r = client.get("/v1/usage", headers=auth("hermes"))
    assert r.json()["today"]["total_requests"] == 1
    assert client.get("/v1/usage").status_code == 401


def test_quota_endpoint(client):
    r = client.get("/v1/quota", headers=auth("n8n"))
    assert r.status_code == 200
    body = r.json()
    assert body["client"] == "n8n"
    assert body["daily_target_usd"] == 0.5
    assert "deepinfra" in body["providers"]


def test_health_alert_chain_degraded_then_ok(client):
    r = client.get("/health")
    assert r.json()["alert_chain"]["status"] == "degraded"  # no poll yet
    client.get("/admin/costs", headers=auth("admin"))       # the n8n poll
    r = client.get("/health")
    assert r.json()["alert_chain"]["status"] == "ok"
    assert r.json()["classifier_sidecar"] == "not_configured"


def test_admin_costs_shape(client):
    client.post("/v1/chat/completions", json=chat_body("hello"),
                headers=auth("hermes"))
    r = client.get("/admin/costs", headers=auth("admin"))
    body = r.json()
    assert body["today"]["total_requests"] == 1
    assert "by_client" in body["today"] and "alerts" in body


def test_admin_reload(client):
    r = client.post("/admin/reload", headers=auth("admin"))
    assert r.status_code == 200
    assert r.json()["models"] == 13


def test_routes_log_has_no_prompt_text(client, app, tmp_path):
    secret_text = "a very distinctive prompt sentence xyzzy"
    client.post("/v1/chat/completions", json=chat_body(secret_text),
                headers=auth("hermes"))
    log = (app.state.cfg.state_dir / "routes.jsonl").read_text()
    assert "xyzzy" not in log
    rec = json.loads(log.strip().splitlines()[-1])
    assert rec["client"] == "hermes" and rec["model"]
