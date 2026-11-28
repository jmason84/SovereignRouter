"""v0.10.9 (ROUTER-STRESS-20261128): malformed-input hardening.

Every case here returned a bare 500 (or routed to a PAID model) in the
2026-11-28 stress matrix, against both the live v0.10.3 gateway and the
v0.10.8 branch. Each must now be a clean 400 (or a normal 200 for the
shapes OpenAI itself accepts) and never an unhandled exception.
"""
from __future__ import annotations

import json

import pytest

from conftest import auth, chat_body  # noqa: E402

URL = "/v1/chat/completions"


def _post_raw(client, raw: bytes, who: str = "hermes"):
    h = dict(auth(who)); h["Content-Type"] = "application/json"
    return client.post(URL, content=raw, headers=h)


@pytest.mark.parametrize("messages", [["hello"], [None], [{"role": "user", "content": "hi"}, 7]])
@pytest.mark.parametrize("path", [URL, "/v1/shadow"])
def test_non_object_message_is_400_not_500(client, messages, path):
    r = client.post(path, json={"model": "auto", "messages": messages}, headers=auth("hermes"))
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("value", ["abc", "", [], {}, True, -5, 0, -0.5, "1e999"])
@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
def test_bad_max_tokens_is_400(client, field, value):
    r = client.post(URL, json=chat_body("say hi", **{field: value}), headers=auth("hermes"))
    assert r.status_code == 400, (value, r.status_code, r.text)
    assert "positive integer" in r.json()["error"]["message"]


def test_negative_max_tokens_never_routes_to_paid(client):
    # Before: cost estimate went negative, a paid model looked cheapest.
    r = client.post("/v1/shadow", json=chat_body("say hi", max_tokens=-5), headers=auth("hermes"))
    assert r.status_code == 400


@pytest.mark.parametrize("value", [64, 64.0, "64", 10**9])
def test_numeric_max_tokens_still_served(client, value):
    r = client.post(URL, json=chat_body("say hi", max_tokens=value), headers=auth("hermes"))
    assert r.status_code == 200, r.text


def test_lone_surrogate_is_not_a_500(client):
    raw = b'{"model":"auto","messages":[{"role":"user","content":"bad \\ud800 surrogate"}]}'
    r = _post_raw(client, raw)
    assert r.status_code == 200, r.text


def test_lone_surrogate_in_system_prompt_is_not_a_500(client):
    raw = (b'{"model":"auto","messages":[{"role":"system","content":"sys \\udfff"},'
           b'{"role":"user","content":"hi"}]}')
    r = _post_raw(client, raw)
    assert r.status_code == 200, r.text


def test_non_string_text_part_is_not_a_500(client):
    body = {"model": "auto", "messages": [{"role": "user", "content": [
        {"type": "text", "text": 123}, {"type": "text", "text": None}, {"type": "text", "text": "hi"}]}]}
    r = client.post(URL, json=body, headers=auth("hermes"))
    assert r.status_code == 200, r.text


def test_stream_string_false_means_not_streaming(client):
    r = client.post(URL, json=chat_body("say hi", stream="false"), headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["choices"][0]["message"]["content"]


def test_stream_string_true_still_streams(client):
    r = client.post(URL, json=chat_body("say hi", stream="true"), headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")


# ---- shapes that 400 at every upstream: reject at ingress -------------
# Live 2026-11-28 (v0.10.3): each of these walked the failover chain for
# 11-25 s; temperature="hot" ended on premium/gpt-5.5 for the crm client
# (premium_allowed=false), response_format="json" on a paid Claude, and
# content=12345 / [1, None] / bad tools ended in a misleading 429.
@pytest.mark.parametrize("overrides", [
    {"temperature": "hot"}, {"top_p": "x"}, {"temperature": True},
    {"presence_penalty": [1]}, {"frequency_penalty": {}},
    {"tools": "x"}, {"tools": [{"type": "function", "function": {"name": "ok"}}, "nonsense"]},
    {"tools": [{"type": "function"}]}, {"response_format": "json"},
])
def test_wrongly_typed_params_are_400_with_no_upstream_call(client, overrides):
    r = client.post(URL, json=chat_body("say hi", **overrides), headers=auth("hermes"))
    assert r.status_code == 400, (overrides, r.text)
    assert "x-sovereign-model" not in {k.lower() for k in r.headers}


@pytest.mark.parametrize("message", [
    {"role": "user", "content": 12345},
    {"role": "user", "content": [1, None, {"x": 1}]},
    {"role": "user", "content": None},
    {"role": "wizard", "content": "hi"},
    {"content": "no role"},
])
def test_wrongly_shaped_message_is_400(client, message):
    r = client.post(URL, json={"model": "auto", "messages": [message]}, headers=auth("hermes"))
    assert r.status_code == 400, (message, r.text)


@pytest.mark.parametrize("messages", [
    [{"role": "user", "content": ""}],
    [{"role": "developer", "content": "be brief"}, {"role": "user", "content": "hi"}],
    [{"role": "user", "content": "hi"}, {"role": "assistant", "content": None,
      "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]},
     {"role": "tool", "tool_call_id": "c1", "content": "ok"}],
    [{"role": "user", "content": [{"type": "text", "text": "hi"},
                                  {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}],
])
def test_valid_openai_shapes_still_pass(client, messages):
    body = {"model": "auto", "messages": messages, "temperature": 0.2, "top_p": 1,
            "tools": [{"type": "function", "function": {"name": "terminal", "parameters": {"type": "object"}}}],
            "response_format": {"type": "text"}}
    r = client.post(URL, json=body, headers=auth("hermes"))
    assert r.status_code == 200, r.text


# ---- reasoning_effort: unknown values are dropped, known ones kept ------
@pytest.mark.parametrize("value,expected", [
    ("bogus", None), (7, None), ("", None), ("HIGH", "high"),
    ("low", "low"), ("xhigh", "xhigh"), ("none", "none")])
def test_reasoning_effort_normalised(tmp_path, monkeypatch, value, expected):
    from fastapi.testclient import TestClient
    import conftest
    seen = []
    real = conftest.fake_upstream

    def spy(request):
        seen.append(json.loads(request.content))
        return real(request)
    monkeypatch.setattr(conftest, "fake_upstream", spy)
    app = conftest.build_app(tmp_path)
    with TestClient(app) as tc:
        r = tc.post(URL, json=chat_body("say hi", reasoning_effort=value), headers=auth("hermes"))
    assert r.status_code == 200, r.text
    assert seen, "no upstream call captured"
    for b in seen:
        if expected is None:
            assert "reasoning_effort" not in b, b.get("reasoning_effort")
        else:
            assert b.get("reasoning_effort") == expected


# ---- diagnostics: credential hits carry a value SHAPE, never the value ----
def test_route_row_carries_match_shape_not_value(tmp_path):
    from fastapi.testclient import TestClient
    import conftest
    app = conftest.build_app(tmp_path)
    secret = "Zq9vLmT3xK"
    with TestClient(app) as tc:
        r = tc.post(URL, json=chat_body(f"deploy with password={secret} please"), headers=auth("hermes"))
    assert r.status_code in (200, 429)
    row = [json.loads(l) for l in (tmp_path / "state" / "routes.jsonl").read_text().splitlines() if l.strip()][-1]
    assert row["data_class"] == "sensitive"
    assert row["sensitive_match_shapes"] and row["sensitive_match_shapes"][0].startswith("user:password=")
    assert secret not in json.dumps(row)


def test_value_shape_is_lossy():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from sensitivity import value_shape
    assert value_shape("agent:main:main") == "a5:a4:a~"
    assert value_shape("abc") == "a3"
    assert "Zq9" not in value_shape("Zq9vLmT3xK")


# ---- streaming: an in-stream error before any content fails over ------
# Live 2026-11-28 (OpenClaw, groq/gpt-oss-120b): Groq delivers
# tool_use_failed as `data: {"error": ...}` after a role-only chunk. The
# role chunk committed the stream, so the client got the error instead
# of the next model's answer.
def _stream_app(tmp_path, monkeypatch, first_error_after_content=False):
    import httpx
    import conftest
    calls = []

    def up(request):
        body = json.loads(request.content)
        if not body.get("stream"):
            return conftest.fake_upstream(request)
        calls.append(body["model"])
        m = body["model"]
        role = {"id": "x", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
        err = {"error": {"message": "tool call validation failed: attempted to call tool 'JSON'",
                         "type": "invalid_request_error", "code": "tool_use_failed"}}
        text = {"id": "x", "choices": [{"index": 0, "delta": {"content": f"hello from {m}"}, "finish_reason": None}]}
        stop = {"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        if len(calls) == 1:
            chunks = [role, text, err] if first_error_after_content else [role, err]
        else:
            chunks = [role, text, stop]
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=data.encode(), headers={"content-type": "text/event-stream"})
    monkeypatch.setattr(conftest, "fake_upstream", up)
    return conftest.build_app(tmp_path), calls


def test_stream_error_before_content_fails_over(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app, calls = _stream_app(tmp_path, monkeypatch)
    with TestClient(app) as tc:
        r = tc.post(URL, json=chat_body("say hi", stream=True), headers=auth("hermes"))
    assert r.status_code == 200
    assert len(calls) >= 2, calls
    assert "tool_use_failed" not in r.text
    assert f"hello from {calls[1]}" in r.text
    assert r.text.index('"role"') < r.text.index("hello from")
    row = [json.loads(l) for l in (tmp_path / "state" / "routes.jsonl").read_text().splitlines() if l.strip()][-1]
    assert any(s.startswith("stream_error_before_commit:") for s in row["signals"])
    assert any(s["reason"] == "upstream_stream_error" for s in row["skipped"])


def test_stream_error_after_content_is_not_spliced(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app, calls = _stream_app(tmp_path, monkeypatch, first_error_after_content=True)
    with TestClient(app) as tc:
        r = tc.post(URL, json=chat_body("say hi", stream=True), headers=auth("hermes"))
    assert r.status_code == 200
    assert len(calls) == 1          # committed: never a second model's output
    assert "tool_use_failed" in r.text
