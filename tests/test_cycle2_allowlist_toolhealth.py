"""v0.9.3 (cycle 2): per-client model allowlist (F4), observe-only
per-model tool health, and the repeat_within_60s tool-loop fix."""

from __future__ import annotations

import dataclasses

from conftest import auth, build_app, chat_body  # noqa: F401
from fastapi.testclient import TestClient

from clients import ClientPolicy
from tool_health import (MIN_TURNS, SEVERE_RATE, WARN_RATE, WINDOW,
                         ToolHealth)

TOOLS = [{"type": "function",
          "function": {"name": "run_command",
                       "parameters": {"type": "object", "properties": {}}}}]


def _policy(**kw):
    base = dict(id="x", key_sha256="0" * 64)
    base.update(kw)
    return ClientPolicy(**base)


# ---------------- F4: allowlist ----------------

def test_empty_allowlist_allows_everything():
    p = _policy()
    assert p.model_allowed("anything/at-all")


def test_allowlist_exact_and_glob():
    p = _policy(model_allowlist=("groq/gpt-oss-120b", "deepinfra/*"))
    assert p.model_allowed("groq/gpt-oss-120b")
    assert p.model_allowed("deepinfra/deepseek-v4-flash-0731")
    assert not p.model_allowed("groq/gpt-oss-20b")


def test_allowlist_loaded_from_clients_json(tmp_path):
    import json
    from clients import load_clients
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"clients": [{
        "id": "a", "key_sha256": "0" * 64,
        "model_allowlist": ["groq/*"]}]}))
    assert load_clients(f).clients["a"].model_allowlist == ("groq/*",)


def test_allowlist_restricts_routing(tmp_path):
    app = build_app(tmp_path)
    pol = app.state.clients.clients["hermes"]
    app.state.clients.clients["hermes"] = dataclasses.replace(
        pol, model_allowlist=("groq/*",))
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions",
                   json=chat_body("say hello in one word"),
                   headers=auth("hermes"))
        assert r.status_code == 200, r.text
        assert r.headers["X-Sovereign-Model"].startswith("groq/")


def test_allowlist_blocks_explicit_pin(tmp_path):
    app = build_app(tmp_path)
    pol = app.state.clients.clients["hermes"]
    app.state.clients.clients["hermes"] = dataclasses.replace(
        pol, model_allowlist=("groq/*",))
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions",
                   json=chat_body("hi", model="deepinfra/deepseek-v4-flash-0731"),
                   headers=auth("hermes"))
        if r.status_code == 200:
            assert r.headers["X-Sovereign-Model"].startswith("groq/")


# ---------------- ToolHealth ----------------

def test_tool_health_untrusted_below_min_turns():
    th = ToolHealth()
    for _ in range(MIN_TURNS - 1):
        th.record("m", True)
    assert th.stats("m")["trusted"] is False
    assert th.would_cap("m") is None


def test_tool_health_caps_by_rate():
    th = ToolHealth()
    for i in range(100):
        th.record("warn", i < 30)      # 30%
        th.record("sev", i < 50)       # 50%
        th.record("ok", i < 5)         # 5%
    assert 0.25 <= WARN_RATE < SEVERE_RATE
    assert th.would_cap("warn") == 3
    assert th.would_cap("sev") == 2
    assert th.would_cap("ok") is None


def test_tool_health_window_and_persistence(tmp_path):
    p = tmp_path / "th.json"
    th = ToolHealth(p)
    for _ in range(WINDOW + 50):
        th.record("m", True)
    assert th.stats("m")["turns"] == WINDOW
    th.flush()
    assert ToolHealth(p).stats("m")["turns"] == WINDOW


def test_tool_health_corrupt_file_does_not_crash(tmp_path):
    p = tmp_path / "th.json"
    p.write_text("{not json")
    assert ToolHealth(p).snapshot() == {}


def test_admin_tool_health_endpoint(client):
    assert client.get("/admin/tool-health").status_code == 401
    assert client.get("/admin/tool-health", headers=auth("hermes")).status_code == 401
    client.app.state.tool_health.record("m/x", True)
    r = client.get("/admin/tool-health", headers=auth("admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["observe_only"] is True
    assert body["models"]["m/x"]["errors"] == 1


def _tool_loop(n_results=1, text="restart nginx and verify it"):
    msgs = [{"role": "system", "content": "infra agent"},
            {"role": "user", "content": text}]
    for i in range(n_results):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "run_command", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                     "content": "step ok"})
    return msgs


def test_tool_loop_continuation_not_flagged_repeat(client):
    body = {"model": "auto", "messages": _tool_loop(1), "tools": TOOLS}
    for _ in range(2):
        r = client.post("/v1/chat/completions", json=body,
                        headers=auth("hermes"))
        assert r.status_code == 200, r.text
    rows = client.app.state.telemetry.recent(0)
    assert rows[-1].repeat_within_60s is False


def test_plain_repeat_still_flagged(client):
    for _ in range(2):
        r = client.post("/v1/chat/completions",
                        json=chat_body("what is two plus two"),
                        headers=auth("hermes"))
        assert r.status_code == 200, r.text
    assert client.app.state.telemetry.recent(0)[-1].repeat_within_60s is True


def test_sticky_tool_loop_feeds_tool_health(client):
    first = {"model": "auto", "messages": _tool_loop(1), "tools": TOOLS}
    second = {"model": "auto", "messages": _tool_loop(2), "tools": TOOLS}
    r1 = client.post("/v1/chat/completions", json=first, headers=auth("hermes"))
    assert r1.status_code == 200, r1.text
    r2 = client.post("/v1/chat/completions", json=second, headers=auth("hermes"))
    assert r2.status_code == 200, r2.text
    snap = client.app.state.tool_health.snapshot()
    assert snap, "tool loop continuation should record an outcome"
    assert sum(v["turns"] for v in snap.values()) >= 1
