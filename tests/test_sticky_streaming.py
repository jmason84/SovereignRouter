"""Sticky routing across a 10-step tool loop, sticky switch on failure,
and streaming behaviour (text commit + buffered tool-call validation)."""

from __future__ import annotations

from conftest import auth, build_app, chat_body, registry_variant

TOOLS = [{"type": "function",
          "function": {"name": "run_command",
                       "parameters": {"type": "object",
                                      "properties": {"command": {"type": "string"}}}}}]


def _loop_messages(step: int, extra_tool_text: str = "") -> list[dict]:
    msgs: list[dict] = [
        {"role": "system", "content": "You are an infra agent."},
        {"role": "user", "content": "restart the nginx container and verify it"},
    ]
    for i in range(step):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "run_command", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                     "content": f"step {i} ok {extra_tool_text}"})
    return msgs


def test_sticky_ten_step_tool_loop(client):
    models = []
    # step 0: the opening user request
    r = client.post("/v1/chat/completions",
                    json={"model": "auto", "messages": _loop_messages(0),
                          "tools": TOOLS},
                    headers=auth("hermes"))
    assert r.status_code == 200
    models.append(r.headers["X-Sovereign-Model"])
    for step in range(1, 10):
        r = client.post("/v1/chat/completions",
                        json={"model": "auto", "messages": _loop_messages(step),
                              "tools": TOOLS},
                        headers=auth("hermes"))
        assert r.status_code == 200
        models.append(r.headers["X-Sovereign-Model"])
    assert len(models) == 10
    assert len(set(models)) == 1, f"model switched mid-loop: {models}"


def test_sticky_switches_when_model_rate_limited(tmp_path):
    # Premise: the loop's first server is the cheapest route to the
    # gpt-oss-120b weights. Since 2026-11-09 Groq carries real prices
    # and the $0 OpenRouter models win small turns, so this test's
    # registry disables them (openrouter/free-router joined that set
    # in the 2026-11-12 free-catalog expansion — same $0 private-free
    # posture). Expansion phase 2 (2026-11-12) then put DeepInfra's
    # $0.037 route to the same weights ahead of Groq's metered one —
    # it is the first server now. The FAIL marker below keys on the
    # shared upstream string "openai/gpt-oss-120b", so the failure
    # injection is unchanged. v0.9.0 (2026-11-13): the tooling battery
    # measured deepinfra/llama-3.1-8b-turbo at tooling 5; for this
    # tool-bearing loop it now clears the quality floor its hand
    # ratings failed and wins on price — the premise needs the
    # gpt-oss route first, so it joins the disable list below.
    from fastapi.testclient import TestClient
    app = build_app(tmp_path, registry_path=registry_variant(
        tmp_path, disable=("openrouter/north-mini-code-free",
                           "openrouter/dots-3-note-preview-free",
                           "openrouter/free-router",
                           "deepinfra/deepseek-v4-flash",
                           "deepinfra/llama-3.1-8b-turbo")))
    client = TestClient(app)
    r = client.post("/v1/chat/completions",
                    json={"model": "auto", "messages": _loop_messages(0),
                          "tools": TOOLS},
                    headers=auth("hermes"))
    first = r.headers["X-Sovereign-Model"]
    assert first == "deepinfra/gpt-oss-120b"
    # next turn: the sticky model starts returning 429 (marker rides in
    # the tool result, as a real provider failure would surface mid-loop)
    msgs = _loop_messages(1, extra_tool_text="FAIL::openai/gpt-oss-120b")
    r = client.post("/v1/chat/completions",
                    json={"model": "auto", "messages": msgs, "tools": TOOLS},
                    headers=auth("hermes"))
    assert r.status_code == 200
    second = r.headers["X-Sovereign-Model"]
    assert second != first
    # and the new model sticks for the following turn (marker removed)
    r = client.post("/v1/chat/completions",
                    json={"model": "auto", "messages": _loop_messages(2),
                          "tools": TOOLS},
                    headers=auth("hermes"))
    assert r.headers["X-Sovereign-Model"] == second


def test_different_conversation_different_sticky(client):
    r1 = client.post("/v1/chat/completions",
                     json=chat_body("restart the nginx container and verify it"),
                     headers=auth("hermes"))
    r2 = client.post("/v1/chat/completions",
                     json=chat_body("write a haiku about the moon"),
                     headers=auth("hermes"))
    assert r1.status_code == r2.status_code == 200
    # independent conversations tracked independently (both may share a
    # model by merit, but neither errors and headers are present)
    assert r1.headers["X-Sovereign-Model"]
    assert r2.headers["X-Sovereign-Model"]


# ----------------------------------------------------------------------
# streaming
# ----------------------------------------------------------------------
def test_streaming_text(client):
    with client.stream("POST", "/v1/chat/completions",
                       json=chat_body("say hello", stream=True),
                       headers=auth("hermes")) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "Hello " in body and "world" in body
    assert "data: [DONE]" in body


def test_streaming_tool_call_buffered_and_valid(client):
    with client.stream("POST", "/v1/chat/completions",
                       json=chat_body("USE_TOOL please call the function",
                                      stream=True, tools=TOOLS),
                       headers=auth("hermes")) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    # the reassembled arguments arrive complete and valid (SSE payloads
    # JSON-escape the inner quotes, so normalise before asserting)
    plain = body.replace('\\"', '"')
    assert '"command"' in plain and '"ls"' in plain
    assert "data: [DONE]" in body


def test_streaming_bad_tool_call_escalates(tmp_path):
    # 2026-11-19 battery v2 (T6/T7 + no-failed-task cap): the
    # escalation arithmetic moved twice over. groq/gpt-oss-120b
    # re-measured tooling 4, so the target is now q5 (not the old
    # q6 -> >=5 fallback); and openrouter/north-mini-code-free
    # re-measured tooling 5 at $0 / 900 ms, which puts it FIRST in
    # that q5 scan — but its upstream is a scripted-bad FREE_MODELS
    # entry, and the streaming path escalates at most once, so its
    # bad call would be committed. The premise therefore disables
    # north-mini (the registry_variant precedent from
    # test_sticky_switches): with it out, the first q5 candidate is
    # deepinfra/llama-3.1-8b-turbo (tooling 5, $0.02/$0.04 — the
    # cheapest qualifying route, as in the v0.9.0 note below).
    # openrouter/free-router left the target set the other way: it
    # re-measured tooling 4 (its T2 response failed validation in
    # the live battery), so it no longer meets a q5 target.
    from fastapi.testclient import TestClient
    app = build_app(tmp_path, registry_path=registry_variant(
        tmp_path, disable=("openrouter/north-mini-code-free",)))
    client = TestClient(app)
    with client.stream("POST", "/v1/chat/completions",
                       json=chat_body("USE_TOOL BADTOOL_STREAM call the function",
                                      stream=True, tools=TOOLS,
                                      model="groq/gpt-oss-120b"),
                       headers=auth("hermes")) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    # the malformed args from the free model were buffered, rejected and
    # never sent to the client; the escalated (paid) model's call was.
    assert "{not-" not in body
    assert '"command"' in body.replace('\\"', '"')
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.escalated is True
    # cheapest stronger route; the 0731 checkpoint since expansion
    # phase 2 (2026-11-12). v0.9.0 (2026-11-13): the escalation
    # filter reads the measured tooling dimension for tool-bearing
    # requests — deepinfra/llama-3.1-8b-turbo (tooling 5,
    # $0.02/$0.04) was then the cheapest qualifying route.
    # 2026-11-15 battery completion: openrouter/free-router measured
    # tooling 5 at $0 — it is now the cheapest qualifying route, and
    # the first server is pinned (groq/gpt-oss-120b, a scripted-bad
    # FREE_MODELS entry) because auto-selection's first pick for a
    # tool turn moved off the scripted set with the new rankings.
    # v0.9.2 (F3): the measured tooling score is now a ceiling
    # (min(tooling, class quality)). deepinfra/llama-3.1-8b-turbo is
    # tooling 5 but class quality 2 — an 8B model is no longer a valid
    # "stronger" escalation target — so the cheapest route that is good
    # at tools AND at the task is deepinfra/deepseek-v4-flash-0731
    # (tooling 5, infra 5, $0.06/$0.18).
    assert rec.model == "deepinfra/deepseek-v4-flash-0731"
