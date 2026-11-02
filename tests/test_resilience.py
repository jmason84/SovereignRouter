"""Failure, size, concurrency, spending and recovery behaviour against mock providers."""
import asyncio
import json

import httpx

from conftest import auth, chat, completion, stream_response

TOOLS = [{"type": "function", "function": {"name": f"tool_{i}", "description": "d" * 400,
                                           "parameters": {"type": "object", "properties": {
                                               f"p{j}": {"type": "string"} for j in range(12)}}}} for i in range(60)]


def agent_body(kb: int, **extra):
    return {"messages": [{"role": "system", "content": "You are an infrastructure agent. " + "rule " * (kb * 200)},
                         {"role": "user", "content": "check the status of the n8n container"}],
            "tools": TOOLS, **extra}


async def test_150kb_tool_heavy_request_routes_to_big_context_model(client, upstream):
    r = await client.post("/v1/chat/completions", json=agent_body(150), headers=auth())
    assert r.status_code == 200
    assert r.headers["x-sovereign-model"] in ("orfree", "deepseek")  # not Groq (8k tpm)
    sent = upstream.calls[-1][1]
    assert len(sent["tools"]) == 60 and sent["max_tokens"] == 2048


async def test_tool_round_trip_preserves_tool_messages_and_ids(client, upstream):
    call = [{"id": "call_1", "type": "function", "function": {"name": "tool_3", "arguments": "{\"p0\": \"x\"}"}}]
    msgs = [{"role": "user", "content": "check the status of the n8n container"},
            {"role": "assistant", "content": None, "tool_calls": call},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"}]
    r = await client.post("/v1/chat/completions", json={"messages": msgs, "tools": TOOLS}, headers=auth())
    assert r.status_code == 200 and upstream.calls[-1][1]["messages"][2]["tool_call_id"] == "call_1"


async def test_unknown_fields_are_not_forwarded(client, upstream):
    await client.post("/v1/chat/completions", headers=auth(),
                      json={**chat("say hello"), "provider": {"x": 1}, "api_key": "k", "user": "u"})
    sent = upstream.calls[-1][1]
    assert not ({"provider", "api_key", "user"} & set(sent))


async def test_every_provider_failure_mode_fails_over(client, upstream):
    modes = {
        "timeout": lambda req, body: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
        "reset": lambda req, body: (_ for _ in ()).throw(httpx.ConnectError("reset")),
        "500": lambda req, body: httpx.Response(500),
        "502": lambda req, body: httpx.Response(502),
        "non-json": lambda req, body: httpx.Response(200, content=b"<html>gateway</html>"),
        "401": lambda req, body: httpx.Response(401),
        "408": lambda req, body: httpx.Response(408),
    }
    for name, handler in modes.items():
        upstream.handlers["groq"] = handler
        # distinct conversation each time so sticky/cooldown from the previous mode does not mask this one
        r = await client.post("/v1/chat/completions", json=chat(f"say hello {name}"), headers=auth())
        assert r.status_code == 200, name
        assert r.headers["x-sovereign-model"] not in ("groq20", "groq120"), name
        client.app.state.store.db.execute("DELETE FROM cooldown")


async def test_mid_stream_drop_after_first_token_ends_cleanly(client, upstream):
    class Boom(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices": [{"delta": {"content": "partial"}}]}\n\n'
            raise httpx.ReadError("connection lost")
    upstream.handlers["groq"] = lambda req, body: httpx.Response(200, stream=Boom())
    r = await client.post("/v1/chat/completions", json=chat("say hello", stream=True), headers=auth())
    assert r.status_code == 200 and "partial" in r.text  # client keeps what it received; no 500


async def test_recovery_after_cooldown_expires(client, upstream, clock):
    upstream.handlers["groq"] = lambda req, body: httpx.Response(429, headers={"retry-after": "30"})
    r1 = await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())
    assert r1.headers["x-sovereign-model"] not in ("groq20", "groq120")
    upstream.handlers["groq"] = lambda req, body: httpx.Response(200, json=completion())
    clock.t += 31
    r2 = await client.post("/v1/chat/completions", json=chat("say hello again"), headers=auth())
    assert r2.headers["x-sovereign-model"].startswith("groq")  # recovered after the cooldown window


async def test_all_down_then_up_recovers(client, upstream, clock):
    for h in ("groq", "or", "di", "ol", "pr"):
        upstream.handlers[h] = lambda req, body: httpx.Response(503)
    assert (await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())).status_code == 429
    upstream.handlers.clear()
    clock.t += 301  # 401/403 cooldowns are 300 s; 503s set none
    assert (await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())).status_code == 200


async def test_concurrent_burst_never_exceeds_provider_limits(client, upstream):
    big = {"messages": [{"role": "system", "content": "x " * 20000}, {"role": "user", "content": "summarize"}]}
    results = await asyncio.gather(*[client.post("/v1/chat/completions", json=big, headers=auth())
                                     for _ in range(60)])
    assert all(r.status_code == 200 for r in results)  # nothing refused for budget or limits
    or_calls = sum(1 for h, _ in upstream.calls if h == "or")
    assert or_calls <= 20  # orfree rpm limit
    assert client.app.state.store.window_usage("orfree")["minute"]["requests"] <= 20


async def test_concurrent_burst_all_groq_requests_respect_tpm(client, upstream):
    results = await asyncio.gather(*[client.post("/v1/chat/completions", json=chat(f"say hello {i}"), headers=auth())
                                     for i in range(40)])
    assert all(r.status_code == 200 for r in results)
    for m in ("groq20", "groq120"):
        assert client.app.state.store.window_usage(m)["minute"]["tokens"] <= 8000


async def test_spending_behavior_under_load_serves_everything_and_alerts(client, upstream):
    upstream.handlers["di"] = lambda req, body: httpx.Response(200, json=completion(usage=(1_000_000, 100, 0)))
    body = {"messages": [{"role": "system", "content": "context line " * 4000},
                         {"role": "user", "content": "migrate the docker stack"}]}
    rs = await asyncio.gather(*[client.post("/v1/chat/completions", json=body, headers=auth()) for _ in range(25)])
    assert all(r.status_code == 200 for r in rs)  # no request refused for cost
    kinds = {a["kind"] for a in client.app.state.store.alerts_after(0)}
    assert {"client_target", "runaway"} <= kinds


async def test_ledger_never_stores_prompt_text(client):
    await client.post("/v1/chat/completions", json=chat("PROMPT_CANARY_7731 say hello"), headers=auth())
    db = client.app.state.store.db
    dump = json.dumps([list(db.execute(f"SELECT * FROM {t}")) for t in ("usage", "alerts", "meta", "cooldown")])
    assert "PROMPT_CANARY_7731" not in dump


async def test_malformed_requests_get_clear_errors(client):
    assert (await client.post("/v1/chat/completions", content=b"not json", headers=auth())).status_code == 400
    assert (await client.post("/v1/chat/completions", json={"messages": []}, headers=auth())).status_code == 400
    assert (await client.post("/v1/chat/completions", json=[1], headers=auth())).status_code == 400
