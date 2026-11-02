import asyncio

import httpx

from conftest import auth, chat, completion, model
from sovereign_router.alerts import Alerter
from sovereign_router.governor import Governor
from sovereign_router.quota import Store
from sovereign_router.registry import parse_model


def test_concurrent_reservations_never_exceed_limit():
    store = Store()
    m = parse_model(model("m", "x", limits={"rpm": 5}), allow_http=True)
    got = [store.reserve(m, "c", 1, 10, 0) for _ in range(20)]
    assert sum(1 for g in got if g) == 5


async def test_alert_outbox_dedupes_and_is_pulled(client):
    a = client.app.state.alerter
    assert await a.send("x", "k1", "msg") is True
    assert await a.send("x", "k1", "msg") is False  # deduped by key
    r = await client.get("/admin/alerts", headers=auth("admin"))
    assert [x["key"] if "key" in x else x["kind"] for x in r.json()["alerts"]] == ["x"]
    last = r.json()["alerts"][-1]["id"]
    assert (await client.get(f"/admin/alerts?after={last}", headers=auth("admin"))).json()["alerts"] == []


async def test_health_degrades_without_polls_and_recovers(client, clock):
    assert (await client.get("/health")).json()["alert_chain"]["status"] == "ok"
    clock.t += 31 * 60
    h = (await client.get("/health")).json()
    assert h["status"] == "degraded" and h["alert_chain"]["status"] == "degraded"
    await client.get("/admin/costs", headers=auth("admin"))  # an n8n poll
    assert (await client.get("/health")).json()["status"] == "ok"


async def test_runaway_and_provider_alerts_do_not_block(client, upstream, clock):
    sent = []
    async def rec(kind, key, msg, data=None):
        sent.append(kind); return True
    client.app.state.alerter.send = rec
    upstream.handlers["di"] = lambda req, body: httpx.Response(200, json=completion(usage=(120_000_000, 0, 0)))
    big = {"messages": [{"role": "system", "content": "context line " * 4000},
                        {"role": "user", "content": "migrate the docker stack"}]}
    r = await client.post("/v1/chat/completions", json=big, headers=auth())
    assert r.status_code == 200  # $10.80 call: alerts fire, request is served
    assert {"runaway", "provider_spend", "client_target"} <= set(sent)


async def test_backstop_turns_paid_lanes_off_but_not_free(make_app, upstream):
    app = make_app(); gov = app.state.governor; gov.set_backstop(1.0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
        m = app.state.engine.registry.get("deepseek")
        rid = app.state.store.reserve(m, "hermes", 4, 10, 2.0); app.state.store.finalize(rid, 10, 2.0)
        big = {"messages": [{"role": "system", "content": "context line " * 4000},
                            {"role": "user", "content": "migrate the docker stack"}]}
        r = await c.post("/v1/chat/completions", json=big, headers=auth())
        assert r.headers["x-sovereign-lane"] == "private-free"  # free-only while backstop is hit


async def test_daily_summary_is_the_heartbeat(client, upstream):
    await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())
    sent = []
    async def fake(kind, key, msg, data=None):
        sent.append((kind, msg)); return True
    client.app.state.alerter.send = fake
    r = await client.post("/admin/daily-summary", headers=auth("admin"))
    assert r.json()["delivered"] is True and sent[0][0] == "daily_summary"
