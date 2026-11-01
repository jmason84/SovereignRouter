import json
import logging

import httpx

from conftest import auth, chat, completion, stream_response


def model_of(r):
    return r.headers["x-sovereign-model"]


async def test_auth_required(client):
    assert (await client.post("/v1/chat/completions", json=chat("hi"))).status_code == 401
    assert (await client.get("/v1/models")).status_code == 401
    assert (await client.get("/v1/models", headers=auth())).status_code == 200


async def test_trivial_goes_free_with_headers(client):
    r = await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())
    assert r.status_code == 200
    assert r.headers["x-sovereign-lane"] == "private-free"
    assert r.headers["x-sovereign-level"] == "1"
    assert r.headers["x-sovereign-escalated"] == "false"


async def test_large_prompt_skips_groq_without_waiting(client, upstream):
    big = "log line about nothing important " * 2500  # ~20k tokens, like an agent system prompt
    body = {"messages": [{"role": "system", "content": big}, {"role": "user", "content": "summarize the log"}]}
    r = await client.post("/v1/chat/completions", json=body, headers=auth())
    assert r.status_code == 200
    assert model_of(r) == "orfree"
    assert all(h != "groq" for h, _ in upstream.calls)


async def test_infra_status_check_stays_free(client):
    r = await client.post("/v1/chat/completions", json=chat("check the status of the docker container"),
                          headers=auth())
    assert r.headers["x-sovereign-lane"] == "private-free"


async def test_infra_change_goes_to_cheapest_quality_4_paid(client):
    r = await client.post("/v1/chat/completions", json=chat("migrate the docker stack to the new vm"),
                          headers=auth())
    assert r.headers["x-sovereign-level"] == "4"
    assert model_of(r) == "groq120"  # a free quality-4 model that fits beats any paid model
    big = {"messages": [{"role": "system", "content": "context line " * 4000},
                        {"role": "user", "content": "migrate the docker stack to the new vm"}]}
    r = await client.post("/v1/chat/completions", json=big, headers=auth())
    assert model_of(r) == "deepseek"  # too big for groq -> cheapest quality-4+ paid model


async def test_failover_on_429_sets_cooldown(client, upstream):
    upstream.handlers["or"] = lambda req, body: httpx.Response(429, headers={"retry-after": "30"})
    big = "x y z " * 6000
    r = await client.post("/v1/chat/completions", json=chat(big + " summarize"), headers=auth())
    assert r.status_code == 200 and r.headers["x-sovereign-lane"] == "paid"
    n = len(upstream.calls)
    r2 = await client.post("/v1/chat/completions", json=chat(big + " summarize again"), headers=auth())
    assert r2.status_code == 200
    assert sum(1 for h, _ in upstream.calls[n:] if h == "or") == 0  # cooled down, not retried


async def test_empty_answer_escalates(client, upstream):
    calls = {"n": 0}

    def groq(req, body):
        calls["n"] += 1
        return httpx.Response(200, json=completion(content=""))
    upstream.handlers["groq"] = groq
    r = await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())
    assert r.status_code == 200 and r.headers["x-sovereign-escalated"] == "true"
    assert calls["n"] == 1


async def test_malformed_tool_call_escalates(client, upstream):
    bad = [{"id": "1", "type": "function", "function": {"name": "get", "arguments": "{broken"}}]
    upstream.handlers["groq"] = lambda req, body: httpx.Response(200, json=completion(tool_calls=bad))
    tools = [{"type": "function", "function": {"name": "get", "parameters": {}}}]
    r = await client.post("/v1/chat/completions", json=chat("say hello", tools=tools), headers=auth())
    assert r.status_code == 200 and r.headers["x-sovereign-escalated"] == "true"
    assert model_of(r) != "groq20"


async def test_secret_keeps_request_on_cleared_lanes(client, upstream):
    r = await client.post("/v1/chat/completions",
                          json=chat("migrate docker to prod, key sk-abcdefghijklmnopqrstuvwxyz1234"),
                          headers=auth())
    assert r.status_code == 200
    assert model_of(r).startswith("groq")  # only groq is cleared for sensitive in the test registry
    assert all(h == "groq" for h, _ in upstream.calls)


async def test_sensitive_flag_persists_in_conversation(client, upstream):
    msgs = [{"role": "user", "content": "token = abcdef1234567890abcd please remember it"}]
    await client.post("/v1/chat/completions", json={"messages": msgs}, headers=auth())
    msgs += [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "migrate the docker stack"}]
    upstream.calls.clear()
    r = await client.post("/v1/chat/completions", json={"messages": msgs[:1] + msgs[1:]}, headers=auth())
    assert r.status_code == 200 and all(h == "groq" for h, _ in upstream.calls)


async def test_sticky_model_across_tool_loop(client, upstream):
    tools = [{"type": "function", "function": {"name": "get", "parameters": {}}}]
    msgs = [{"role": "system", "content": "you are an agent"}, {"role": "user", "content": "migrate the docker stack"}]
    first = await client.post("/v1/chat/completions", json={"messages": msgs, "tools": tools}, headers=auth())
    m = model_of(first)
    for i in range(3):
        msgs = msgs + [{"role": "assistant", "content": None, "tool_calls": [
            {"id": str(i), "type": "function", "function": {"name": "get", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": str(i), "content": "result"}]
        r = await client.post("/v1/chat/completions", json={"messages": msgs, "tools": tools}, headers=auth())
        assert model_of(r) == m and r.headers["x-sovereign-level"] == "4"


async def test_over_target_steers_cheaper_but_never_refuses(client, clock):
    app = client.app
    m = app.state.engine.registry.get("deepseek")
    rid = app.state.store.reserve(m, "hermes", 4, 1000, 5.0)  # $5 spent vs a $1 target
    app.state.store.finalize(rid, 1000, 5.0)
    r = await client.post("/v1/chat/completions", json=chat("explain this error: why is it failing"),
                          headers=auth())
    assert r.status_code == 200  # level 3 -> required 3 -> 2: still served
    assert r.headers["x-sovereign-lane"] == "private-free"
    big = {"messages": [{"role": "system", "content": "context line " * 4000},
                        {"role": "user", "content": "migrate the docker stack"}]}
    hard = await client.post("/v1/chat/completions", json=big, headers=auth())
    assert hard.status_code == 200 and hard.headers["x-sovereign-lane"] == "paid"  # level 4 not downgraded


async def test_premium_only_for_level_5_with_permission(client):
    ok = await client.post("/v1/chat/completions", headers=auth("openclaw"),
                           json=chat("rotate the production firewall credentials on proxmox"))
    assert ok.headers["x-sovereign-level"] == "5" and model_of(ok) == "opus"
    denied = await client.post("/v1/chat/completions", headers=auth("hermes"),
                               json={**chat("hi"), "model": "premium"})
    assert denied.status_code == 403


async def test_provider_prefixed_model_refused_real_names_aliased(client):
    r = await client.post("/v1/chat/completions", headers=auth(), json={**chat("hi"), "model": "openai/gpt-4o"})
    assert r.status_code == 403
    r = await client.post("/v1/chat/completions", headers=auth(), json={**chat("say hello"), "model": "gpt-4o"})
    assert r.status_code == 200


async def test_413_when_nothing_can_ever_fit(client):
    huge = "word " * 400000  # ~570k tokens
    r = await client.post("/v1/chat/completions", json=chat(huge), headers=auth())
    assert r.status_code == 413


async def test_429_when_everything_fails(client, upstream):
    for h in ("groq", "or", "di", "ol", "pr"):
        upstream.handlers[h] = lambda req, body: httpx.Response(503)
    r = await client.post("/v1/chat/completions", json=chat("say hello"), headers=auth())
    assert r.status_code == 429 and r.headers["retry-after"] == "60" and r.json()["error"]["skipped"]


async def test_cost_accounting_uses_cached_price(client, upstream):
    upstream.handlers["di"] = lambda req, body: httpx.Response(
        200, json=completion(usage=(1_000_000, 0, 1_000_000)))
    big = {"messages": [{"role": "system", "content": "context line " * 4000},
                        {"role": "user", "content": "migrate the docker stack"}]}
    r = await client.post("/v1/chat/completions", json=big, headers=auth())
    assert model_of(r) == "deepseek"
    assert abs(client.app.state.store.spend(0) - 0.018) < 1e-9  # 1M cached tokens at $0.018/M, not $0.09/M


async def test_kill_switch_and_admin_auth(client):
    assert (await client.post("/admin/killswitch", json={"client": "hermes"}, headers=auth())).status_code == 403
    r = await client.post("/admin/killswitch", json={"client": "hermes"}, headers=auth("admin"))
    assert r.json()["killed"] is True
    assert (await client.post("/v1/chat/completions", json=chat("hi"), headers=auth())).status_code == 403
    await client.post("/admin/killswitch", json={"client": "hermes", "on": False}, headers=auth("admin"))
    assert (await client.post("/v1/chat/completions", json=chat("hi"), headers=auth())).status_code == 200


async def test_reload_rejects_bad_registry_and_keeps_old(client, tmp_path):
    (tmp_path / "registry.json").write_text(json.dumps({"models": [{"id": "x"}]}))
    r = await client.post("/admin/reload", headers=auth("admin"))
    assert r.status_code == 400
    assert (await client.get("/health")).json()["models"] == 6


async def test_no_prompt_or_secret_text_in_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "sk-abcdefghijklmnopqrstuvwxyz1234"
    await client.post("/v1/chat/completions", headers=auth(),
                      json=chat(f"UNIQUE_PROMPT_TEXT_42 please migrate the docker stack {secret}"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "route" in text
    assert "UNIQUE_PROMPT_TEXT_42" not in text and secret not in text


async def test_streaming_content_passthrough_and_usage(client, upstream):
    chunks = [{"choices": [{"delta": {"content": "Hel"}}]}, {"choices": [{"delta": {"content": "lo"}}]},
              {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 5}}, "[DONE]"]
    upstream.handlers["groq"] = lambda req, body: stream_response(chunks)
    assert upstream.calls == []
    r = await client.post("/v1/chat/completions", json=chat("say hello", stream=True), headers=auth())
    assert r.status_code == 200 and "Hel" in r.text and r.text.rstrip().endswith("[DONE]")
    assert upstream.calls[0][1]["stream_options"] == {"include_usage": True}


async def test_streaming_bad_tool_args_escalate_before_commit(client, upstream):
    bad = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "get", "arguments": "{bro"}}]}}]},
           {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}, "[DONE]"]
    good = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "get", "arguments": "{}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}, "[DONE]"]
    upstream.handlers["groq"] = lambda req, body: stream_response(bad)
    upstream.handlers["or"] = lambda req, body: stream_response(good)
    upstream.handlers["ol"] = lambda req, body: stream_response(good)
    upstream.handlers["di"] = lambda req, body: stream_response(good)
    tools = [{"type": "function", "function": {"name": "get", "parameters": {}}}]
    r = await client.post("/v1/chat/completions", json=chat("say hello", stream=True, tools=tools), headers=auth())
    assert r.status_code == 200 and r.headers["x-sovereign-escalated"] == "true" and '{bro' not in r.text


async def test_streaming_empty_answer_escalates(client, upstream):
    empty = [{"choices": [{"delta": {"content": ""}, "finish_reason": "stop"}]}, "[DONE]"]
    fine = [{"choices": [{"delta": {"content": "hi"}}]}, "[DONE]"]
    upstream.handlers["groq"] = lambda req, body: stream_response(empty)
    upstream.handlers["or"] = lambda req, body: stream_response(fine)
    r = await client.post("/v1/chat/completions", json=chat("say hello", stream=True), headers=auth())
    assert r.status_code == 200 and r.headers["x-sovereign-escalated"] == "true" and "hi" in r.text
