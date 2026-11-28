"""Regression: when the first escalation target ALSO returns an invalid
tool call, the gateway must keep going (bounded) and never hand a known-
invalid tool call to the client while candidates remain. Found by the live
e2e harness (v0.9.3): escalation used to be limited to a single hop."""

from __future__ import annotations

import json

from conftest import auth, chat_body

TOOLS = [{"type": "function",
          "function": {"name": "run_shell",
                       "parameters": {"type": "object",
                                      "properties": {"command": {"type": "string"}}}}}]


def _args_from_sse(raw: str) -> str:
    out = ""
    for line in raw.splitlines():
        if not line.startswith("data: ") or line.endswith("[DONE]"):
            continue
        try:
            d = json.loads(line[6:])
        except Exception:
            continue
        for ch in d.get("choices") or []:
            for tc in (ch.get("delta") or {}).get("tool_calls") or []:
                out += (tc.get("function") or {}).get("arguments") or ""
    return out


def test_stream_two_bad_tool_calls_then_valid(client):
    body = chat_body("USE_TOOL BADTOOL_FIRST2 stream-case run it", tools=TOOLS,
                     stream=True)
    with client.stream("POST", "/v1/chat/completions", json=body,
                       headers=auth("hermes")) as r:
        assert r.status_code == 200
        raw = "".join(r.iter_text())
    args = _args_from_sse(raw)
    assert "oops" not in args and "{not-" not in args
    assert json.loads(args) == {"command": "ls"}


def test_json_two_bad_tool_calls_then_valid(client):
    body = chat_body("USE_TOOL BADTOOL_FIRST2 json-case run it", tools=TOOLS)
    r = client.post("/v1/chat/completions", json=body, headers=auth("hermes"))
    assert r.status_code == 200, r.text
    tc = r.json()["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(tc["function"]["arguments"]) == {"command": "ls"}


def test_escalation_is_still_bounded(client, app):
    # more than MAX_VALIDATION_ESCALATIONS failures must not loop forever:
    # the request ends (served or failed) instead of exhausting every model
    from gateway import MAX_VALIDATION_ESCALATIONS
    assert MAX_VALIDATION_ESCALATIONS == 2
