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


async def test_alert_retried_after_webhook_failure_then_deduped():
    store, state = Store(), {"up": False, "posts": 0}

    def hook(req):
        state["posts"] += 1
        return httpx.Response(200 if state["up"] else 500)
    a = Alerter(store, "http://n8n.test/hook", httpx.AsyncClient(transport=httpx.MockTransport(hook)))
    assert await a.send("x", "k1", "msg") is False and a.failures == 1
    state["up"] = True
    assert await a.send("x", "k1", "msg") is True  # retried, delivered
    assert await a.send("x", "k1", "msg") is True  # deduped
    assert state["posts"] == 2 and a.health()["last_ok"]


async def test_runaway_and_provider_alerts_do_not_block(client, upstream, clock):
    sent = []
    client.app.state.alerter.send = lambda kind, key, msg, data=None: sent.append(kind) or asyncio.sleep(0, True)
    upstream.handlers["di"] = lambda req, body: httpx.Response(200, json=completion(usage=(120_000_000, 0, 0)))
    big = {"messages": [{"role": "system", "content": "context line " * 4000},
                        {"role": "user", "content": "migrate the docker stack"}]}
    r = await client.post("/v1/chat/completions", json=big, headers=auth())
    assert r.status_code == 200  # $10.80 call: alerts fire, request is served
    assert {"runaway", "provider_spend", "client_target"} <= set(sent)


async def test_backstop_turns_paid_lanes_off_but_not_free(make_app, upstream):
    app = make_app(); gov = app.state.governor; gov.backstop = 1.0
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
