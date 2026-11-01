"""v0.5.3 behaviour: runaway warm-up, slow-leak, level mix, premium alerts,
emergency switch at runtime, pull alerts + heartbeat, shadow mode, secret
scan modes and sensitive fallback."""
import asyncio
import json

import httpx

from conftest import REGISTRY, auth, chat, clients, completion, model, sha
from sovereign_router.alerts import Alerter
from sovereign_router.governor import DAY, Governor
from sovereign_router.policy import ClientPolicy
from sovereign_router.quota import Store
from sovereign_router.registry import Registry, parse_model
from sovereign_router.sensitivity import contains_secret

BIG = {"messages": [{"role": "system", "content": "context line " * 4000},
                    {"role": "user", "content": "migrate the docker stack"}]}


def spend(store, m, client, cost, level=4):
    rid = store.reserve(m, client, level, 10, cost)
    store.finalize(rid, 10, cost)


def capture(app):
    sent = []

    async def fake(kind, key, msg, data=None):
        sent.append((kind, key, msg, data))
        return True
    app.state.alerter.send = fake
    return sent


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def gov(clock=None, **kw):
    clock = clock or Clock()
    store = Store(clock=clock)
    reg = Registry([parse_model(m, allow_http=True) for m in REGISTRY["models"]])
    return Governor(store, Alerter(store, None), registry=reg, **kw), store, reg, clock


# --- runaway: fixed thresholds during warm-up, average afterwards ------------
def test_warmup_uses_fixed_hourly_threshold_then_average():
    g, store, reg, clock = gov()
    assert g.in_warmup() and g.runaway_threshold() == 0.50
    spend(store, reg.get("deepseek"), "hermes", 0.10)
    clock.t += 15 * DAY
    assert not g.in_warmup()
    assert g.runaway_threshold() == 1.0  # $1 minimum: last week's average is tiny


async def test_warmup_client_over_2x_target_alerts():
    g, store, reg, clock = gov()
    sent = []
    g.alerter.send = lambda kind, key, msg, data=None: sent.append(kind) or asyncio.sleep(0, True)
    p = ClientPolicy("hermes", "x", daily_cost_target_usd=1.0)
    spend(store, reg.get("deepseek"), "hermes", 2.5)
    await g.after_call(p, reg.get("deepseek"), 2.5)
    assert sent.count("runaway") >= 1 and "client_target" in sent


# --- slow leaks ---------------------------------------------------------------
async def test_rising_three_day_trend_alerts():
    g, store, reg, clock = gov()
    p = ClientPolicy("hermes", "x", daily_cost_target_usd=1.0)
    ds = reg.get("deepseek")
    for days_ago, cost in ((3, 0.5), (2, 0.9), (1, 1.4)):
        clock.t -= days_ago * DAY
        spend(store, ds, "hermes", cost)
        clock.t += days_ago * DAY
    assert g.rising_trend(p) is not None
    sent = []
    g.alerter.send = lambda kind, key, msg, data=None: sent.append(kind) or asyncio.sleep(0, True)
    await g.after_call(p, ds, 0.01)
    assert "trend" in sent


def test_projected_month_needs_a_day_then_extrapolates():
    clock = Clock(1_801_958_400.0 + 3600)  # 2027-02-07 is not a month start; use a computed month start
    g, store, reg, clock = gov(clock)
    from sovereign_router.governor import utc_month_start
    clock.t = utc_month_start(clock.t) + 3600
    assert g.projected_month() is None  # under a day into the month
    clock.t += 2 * DAY
    spend(store, reg.get("deepseek"), "hermes", 3.0)
    proj, level = g.projected_month()
    assert level == 15 and proj > level  # $3 in ~2 days -> ~$40 projected vs $10 + $5 alert levels


# --- level mix ----------------------------------------------------------------
async def test_level_mix_alert_over_40_percent_hard():
    g, store, reg, clock = gov()
    sent = []
    g.alerter.send = lambda kind, key, msg, data=None: sent.append(kind) or asyncio.sleep(0, True)
    p = ClientPolicy("hermes", "x")
    free = reg.get("orfree")
    for i in range(20):
        spend(store, free, "hermes", 0.0, level=4 if i < 10 else 2)
    await g.after_call(p, free, 0.0)  # free calls still evaluate the level mix
    assert sent == ["level_mix"]


# --- premium ------------------------------------------------------------------
async def test_premium_alerts_and_explicit_only_above_target(make_app):
    cfg = clients()
    cfg["clients"][1].update(premium_monthly_alert_usd=20, premium_daily_requests_alert=1)
    app = make_app(client_cfg=cfg)
    sent = capture(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
        lvl5 = chat("rotate the production firewall credentials on proxmox")
        r1 = await c.post("/v1/chat/completions", json=lvl5, headers=auth("openclaw"))
        assert r1.headers["x-sovereign-model"] == "opus"
        r2 = await c.post("/v1/chat/completions", json=lvl5, headers=auth("openclaw"))
        kinds = [s[0] for s in sent]
        assert "premium_requests" in kinds  # 2 premium requests today > alert level 1
        # opus calls cost ~$0.003 each in the mock; push the client over its $3 target
        spend(app.state.store, app.state.engine.registry.get("deepseek"), "openclaw", 5.0)
        r3 = await c.post("/v1/chat/completions", json=lvl5, headers=auth("openclaw"))
        assert r3.headers["x-sovereign-lane"] != "premium"  # level 5 above target: no automatic premium
        r4 = await c.post("/v1/chat/completions", json={**lvl5, "model": "premium"}, headers=auth("openclaw"))
        assert r4.headers["x-sovereign-model"] == "opus"  # explicit premium still allowed


# --- emergency switch at runtime ---------------------------------------------
async def test_backstop_admin_set_clear_disable(client):
    h = auth("admin")
    r = await client.post("/admin/backstop", json={"usd": 1.0}, headers=h)
    assert r.json()["backstop_usd"] == 1.0
    spend(client.app.state.store, client.app.state.engine.registry.get("deepseek"), "hermes", 2.0)
    assert (await client.get("/health")).json()["backstop_tripped"] is True
    big = await client.post("/v1/chat/completions", json=BIG, headers=auth())
    assert big.status_code == 200 and big.headers["x-sovereign-lane"] == "private-free"
    r = await client.post("/admin/backstop", json={"clear_today": True}, headers=h)
    assert r.json()["tripped"] is False
    big = await client.post("/v1/chat/completions", json=BIG, headers=auth())
    assert big.headers["x-sovereign-lane"] == "paid"
    r = await client.post("/admin/backstop", json={"usd": None}, headers=h)
    assert r.json()["backstop_usd"] is None
    assert (await client.post("/admin/backstop", json={"usd": -3}, headers=h)).status_code == 400
    assert (await client.post("/admin/backstop", json={"usd": 5}, headers=auth())).status_code == 403


# --- pull alerts + heartbeat --------------------------------------------------
async def test_pull_alerts_cursor_and_dead_man_health(client, upstream, clock):
    h = auth("admin")
    assert (await client.get("/health")).json()["status"] == "ok"
    clock.t += 1801  # 30 minutes with no poll after start
    assert (await client.get("/health")).json()["status"] == "degraded"
    assert (await client.get("/health?strict=1")).status_code == 503
    upstream.handlers["di"] = lambda req, body: httpx.Response(200, json=completion(usage=(20_000_000, 0, 0)))
    await client.post("/v1/chat/completions", json=BIG, headers=auth())  # $1.80 -> alerts queued
    r = (await client.get("/admin/alerts?after=0", headers=h)).json()
    kinds = {a["kind"] for a in r["alerts"]}
    assert {"runaway", "client_target"} <= kinds and r["alert_chain"]["degraded"] is False
    again = (await client.get(f"/admin/alerts?after={r['next_after']}", headers=h)).json()
    assert again["alerts"] == []  # cursor: nothing re-sent
    assert (await client.get("/health?strict=1")).status_code == 200
    assert (await client.get("/admin/alerts", headers=auth())).status_code == 403
    s = (await client.get("/admin/summary", headers=h)).json()
    assert s["requests"] >= 1 and "spend_by_client" in s and "free_share" in s


async def test_alerts_have_no_prompt_text(client, upstream):
    upstream.handlers["di"] = lambda req, body: httpx.Response(200, json=completion(usage=(20_000_000, 0, 0)))
    secret_word = "zebra-quartz-17"
    body = {"messages": [{"role": "system", "content": "context line " * 4000},
                         {"role": "user", "content": f"migrate the docker stack {secret_word}"}]}
    await client.post("/v1/chat/completions", json=body, headers=auth())
    r = await client.get("/admin/alerts", headers=auth("admin"))
    assert secret_word not in r.text


# --- shadow mode --------------------------------------------------------------
async def test_shadow_decides_without_calling_upstream(client, upstream):
    r = await client.post("/v1/shadow", json=BIG, headers=auth("openclaw"))
    d = r.json()
    assert r.status_code == 200 and d["model"] == "deepseek" and d["est_cost_usd"] > 0
    assert upstream.calls == []  # no provider call
    assert (await client.get("/v1/quota", headers=auth())).json()["deepseek"]["minute"]["requests"] == 0
    rep = (await client.get("/admin/shadow", headers=auth("admin"))).json()
    assert rep[0]["client"] == "openclaw" and rep[0]["requests"] == 1


# --- secret scan --------------------------------------------------------------
def test_secret_scan_known_formats_only_by_default():
    assert contains_secret("hf_" + "a1B2c3D4e5" * 4)
    assert contains_secret("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36P")
    random_like = "Xk9Qm2Lp7Rt4Vw8Zy1Ab3Cd5Ef6Gh0JkLmNoPq"  # high entropy, no known format
    assert not contains_secret(f"image tag {random_like}")
    assert contains_secret(f"image tag {random_like}", entropy=True)


async def test_secret_scan_log_mode_does_not_reroute(make_app, upstream, caplog, monkeypatch):
    monkeypatch.setenv("SOVEREIGN_SECRET_SCAN", "log")
    app = make_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
        import logging
        caplog.set_level(logging.INFO, logger="sovereign.route")
        body = {"messages": [{"role": "system", "content": "context line " * 4000},
                             {"role": "user", "content": "migrate docker, key sk-abcdefghijklmnopqrstuvwxyz1234"}]}
        r = await c.post("/v1/chat/completions", json=body, headers=auth())
        assert r.status_code == 200 and r.headers["x-sovereign-model"] == "deepseek"  # not forced to groq
        line = json.loads([rec.message for rec in caplog.records if '"event": "route"' in rec.message][-1])
        assert line["secret_match"] is True and line["data_class"] == "internal"
        assert "sk-abcdef" not in caplog.text


async def test_sensitive_fallback_when_nothing_cleared(make_app, upstream):
    reg = {"models": [
        model("orfree", "or", q=3, ctx=262144),
        model("deepseek", "di", lane="paid", q=5, pi=0.09, po=0.18, ctx=262144, sensitive_fallback=True),
    ]}
    app = make_app(registry=reg)
    sent = capture(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
        r = await c.post("/v1/chat/completions", json=chat("say hi, password = hunter2hunter2"), headers=auth())
        assert r.status_code == 200 and r.headers["x-sovereign-model"] == "deepseek"
        assert any(s[0] == "sensitive_fallback" for s in sent)
