import httpx

from conftest import auth, chat, completion
from sovereign_router.classifier import Classification, classify, resolve_tie
from sovereign_router.sensitivity import contains_secret

BIG = {"messages": [{"role": "system", "content": "context line " * 4000},
                    {"role": "user", "content": "migrate the docker stack"}]}


def ledger(app, model_id, client, cost, level=4):
    m = app.state.engine.registry.get(model_id)
    rid = app.state.store.reserve(m, client, level, 10, cost)
    app.state.store.finalize(rid, 10, cost)


def kinds(app):
    return [a["kind"] for a in app.state.store.alerts_after(0)]


# --- secret scan: known formats only ---------------------------------------------
def test_no_entropy_rule_false_positives():
    for text in ["sha256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                 "container 3d007246b347 image ghcr.io/foo/bar:latest", "commit 244c876e8e4f1a9d0f",
                 "uuid 123e4567-e89b-12d3-a456-426614174000", "Qm9ZsX7aLk2PqRt5VwYb8NcDe3FgHj6KmUo1TyIuEa4",
                 "bearer of bad news"]:
        assert not contains_secret(text), text


def test_known_formats_detected():
    for text in ["ghp_" + "a1B2c3D4e5" * 4, "AKIAABCDEFGHIJKLMNOP", "sk-ant-api03-abcdefghijklmnopqrstuvwx",
                 "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
                 "Authorization: Bearer abcDEF123xyz.456_ghiJKL789mno", "api_key = abcd1234efgh5678"]:
        assert contains_secret(text), text


async def test_log_only_mode_counts_but_does_not_reroute(make_app, upstream):
    app = make_app()
    app.state.engine.secret_scan = "log-only"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
        r = await c.post("/v1/chat/completions", headers=auth(), json=chat("say hello sk-abcdefghijklmnopqrstuvwxyz1234"))
        assert r.status_code == 200 and r.headers["x-sovereign-lane"] == "private-free"
        assert (await c.get("/health")).json()["secret_scan_hits"] == 1


async def test_sensitive_fallback_to_no_retention_lane_with_alert(make_app, upstream):
    from conftest import REGISTRY, model
    reg = {"models": [model("g", "groq", q=3, no_retention=True), model("d", "di", lane="paid", q=5, pi=1, po=2)]}
    app = make_app(registry=reg)  # nobody cleared for sensitive
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
        r = await c.post("/v1/chat/completions", headers=auth(), json=chat("deploy with sk-abcdefghijklmnopqrstuvwxyz1234"))
        assert r.status_code == 200 and r.headers["x-sovereign-model"] == "g"
        assert "sensitive_fallback" in kinds(app)
        assert all(h == "groq" for h, _ in upstream.calls)


async def test_sensitive_without_any_no_retention_lane_is_refused(make_app):
    from conftest import model
    app = make_app(registry={"models": [model("d", "di", lane="paid", q=5, pi=1, po=2)]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
        r = await c.post("/v1/chat/completions", headers=auth(), json=chat("key sk-abcdefghijklmnopqrstuvwxyz1234"))
        assert r.status_code == 403


# --- classifier ties --------------------------------------------------------------
def test_tie_goes_higher_for_levels_3_to_5():
    r = Classification(2, "chat", .5, "rules")
    assert resolve_tie(r, Classification(3, "chat", .8, "sidecar")).level == 3
    assert resolve_tie(Classification(4, "x", .5, "rules"), Classification(3, "x", .9, "sidecar")).level == 4
    assert resolve_tie(Classification(1, "x", .5, "rules"), Classification(2, "x", .9, "sidecar")).level == 2
    assert resolve_tie(Classification(2, "x", .5, "rules"), Classification(5, "x", .9, "sidecar")).level == 5


# --- governor: fixed thresholds, level mix, premium, slow leaks -------------------
async def test_fixed_hourly_threshold_in_first_14_days(client, upstream):
    upstream.handlers["di"] = lambda req, body: httpx.Response(200, json=completion(usage=(6_000_000, 0, 0)))
    r = await client.post("/v1/chat/completions", json=BIG, headers=auth())  # $0.54 > $0.50/hour
    assert r.status_code == 200 and "runaway" in kinds(client.app)
    assert client.app.state.governor.runaway_threshold() == 0.5


async def test_adaptive_threshold_after_14_days(client, clock):
    ledger(client.app, "deepseek", "hermes", 0.0)
    clock.t += 15 * 86400
    assert client.app.state.governor.runaway_threshold() == 1.0  # $1 minimum with no history


async def test_client_over_twice_target_alerts_in_fixed_period(client):
    ledger(client.app, "deepseek", "hermes", 2.5)  # target is $1
    await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())
    assert {"runaway", "client_target"} <= set(kinds(client.app))


async def test_level_mix_alert_when_hard_levels_exceed_40pct(client):
    for _ in range(10):
        ledger(client.app, "groq120", "hermes", 0.0, level=4)
    await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())
    assert "level_mix" in kinds(client.app)


async def test_premium_alerts(client, upstream):
    gov = client.app.state.governor
    gov.premium_daily = 2
    for _ in range(3):
        ledger(client.app, "opus", "openclaw", 7.5, level=5)  # $22.5 > $20 monthly
    r = await client.post("/v1/chat/completions", headers=auth("openclaw"),
                          json={**chat("rotate the production firewall credentials"), "model": "premium"})
    assert r.headers["x-sovereign-model"] == "opus"  # seeded spend put openclaw over target: explicit request
    assert {"premium_spend", "premium_requests"} <= set(kinds(client.app))


async def test_over_target_premium_needs_explicit_request(client):
    ledger(client.app, "deepseek", "openclaw", 5.0)  # over $3 target
    auto = await client.post("/v1/chat/completions", headers=auth("openclaw"),
                             json=chat("rotate the production firewall credentials on proxmox"))
    assert auto.headers["x-sovereign-level"] == "5" and auto.headers["x-sovereign-model"] != "opus"
    explicit = await client.post("/v1/chat/completions", headers=auth("openclaw"),
                                 json={**chat("rotate the production firewall"), "model": "premium"})
    assert explicit.headers["x-sovereign-model"] == "opus"


async def test_projected_month_alert(client, clock):
    gov = client.app.state.governor
    clock.t = 1_800_000_000.0 + 3 * 86400
    ledger(client.app, "deepseek", "hermes", 9.0)  # budget = 10 + 5 = 15; $9 in ~3 days projects far higher
    await gov.after_call(type("P", (), {"name": "hermes", "daily_cost_target_usd": None})(),
                         client.app.state.engine.registry.get("groq20"))
    assert "projected_month" in kinds(client.app)


async def test_trend_alert_three_rises_above_target(client, clock):
    from sovereign_router.governor import utc_day_start
    d0 = utc_day_start(clock())
    store = client.app.state.store
    m = client.app.state.engine.registry.get("deepseek")
    for k, cost in ((3, .2), (2, .4), (1, .6), (0, 1.5)):
        store.clock = lambda t=d0 - k * 86400 + 100: t
        rid = store.reserve(m, "hermes", 4, 10, cost); store.finalize(rid, 10, cost)
    store.clock = clock
    clock.t = d0 + 3600
    await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())
    assert "trend" in kinds(client.app)


# --- emergency switch -------------------------------------------------------------
async def test_emergency_switch_set_clear_and_free_keeps_serving(client):
    r = await client.post("/admin/emergency-switch", json={"usd": 25}, headers=auth("admin"))
    assert r.json()["emergency_paid_switch_usd"] == 25
    ledger(client.app, "deepseek", "hermes", 26.0)
    assert (await client.get("/health")).json()["paid_lanes_off"] is True
    ok = await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())
    assert ok.status_code == 200 and ok.headers["x-sovereign-lane"] == "private-free"
    only_paid = {"messages": [{"role": "system", "content": "w " * 700000}, {"role": "user", "content": "hi"}]}
    # too big for every free model -> not a budget refusal: nothing free fits and paid lanes are off
    r2 = await client.post("/v1/chat/completions", json=only_paid, headers=auth())
    assert r2.status_code in (413, 429)
    await client.post("/admin/emergency-switch", json={"usd": None}, headers=auth("admin"))
    assert (await client.get("/health")).json()["paid_lanes_off"] is False


# --- shadow mode ------------------------------------------------------------------
async def test_shadow_decides_without_calling_or_billing(client, upstream):
    r = await client.post("/v1/shadow", json=BIG, headers=auth("openclaw"))
    d = r.json()
    assert r.status_code == 200 and d["model"] == "deepseek" and d["cost_estimate_usd"] > 0
    assert upstream.calls == []
    assert client.app.state.store.spend(0) == 0
    assert (await client.post("/v1/shadow", json=BIG)).status_code == 401
