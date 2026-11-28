"""F2 (v0.9.2): a sticky model whose tool results keep carrying errors is
abandoned, and the conversation is not routed back to it.

Regression for the 2026-11-19 routing diagnosis: 89% of Hermes tool turns
were sticky and tool errors never broke stickiness, so one bad first pick
held a whole session / cron run (nemotron-3.5-lightning-free: 36%
next-turn tool-error rate, 4 consecutive error turns in one cron session).
"""

from __future__ import annotations

from conftest import auth, build_app  # noqa: F401
from sticky import TOOL_ERROR_BREAK_AFTER, StickyStore

TOOLS = [{"type": "function",
          "function": {"name": "run_command",
                       "parameters": {"type": "object",
                                      "properties": {
                                          "command": {"type": "string"}}}}}]

ERR = '{"error": "command not found"}'
OK = "step ok"


def _msgs(results: list[str]) -> list[dict]:
    """A tool loop whose i-th tool result is results[i]."""
    msgs: list[dict] = [
        {"role": "system", "content": "You are an infra agent."},
        {"role": "user", "content": "restart the nginx container and verify it"},
    ]
    for i, content in enumerate(results):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "run_command", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                     "content": content})
    return msgs


def _post(client, results):
    r = client.post("/v1/chat/completions",
                    json={"model": "auto", "messages": _msgs(results),
                          "tools": TOOLS},
                    headers=auth("hermes"))
    assert r.status_code == 200, r.text
    return r.headers["X-Sovereign-Model"], r


def test_break_threshold_is_two_consecutive_errors():
    assert TOOL_ERROR_BREAK_AFTER == 2


# ----------------------------------------------------------------------
# store-level
# ----------------------------------------------------------------------

def test_record_tool_outcome_counts_and_resets():
    s = StickyStore()
    assert s.record_tool_outcome("k", True) == 0       # no entry yet
    s.set("k", "m/a", 3)
    assert s.record_tool_outcome("k", True) == 1
    assert s.record_tool_outcome("k", True) == 2
    assert s.record_tool_outcome("k", False) == 0      # clean turn resets
    assert s.record_tool_outcome("k", True) == 1


def test_set_keeps_error_run_for_same_model_only():
    s = StickyStore()
    s.set("k", "m/a", 3)
    s.record_tool_outcome("k", True)
    s.set("k", "m/a", 3)                  # same model re-affirmed
    assert s.get("k").consecutive_tool_errors == 1
    s.set("k", "m/b", 3)                  # a different model starts clean
    assert s.get("k").consecutive_tool_errors == 0


def test_avoid_is_per_conversation_and_expires_with_ttl():
    s = StickyStore(ttl_s=100)
    s.avoid("k1", "m/a", now=1000.0)
    assert s.avoided("k1", now=1050.0) == {"m/a"}
    assert s.avoided("k2", now=1050.0) == set()
    assert s.avoided("k1", now=1200.0) == set()        # past the TTL


# ----------------------------------------------------------------------
# end to end through the gateway
# ----------------------------------------------------------------------

def test_two_consecutive_tool_errors_move_the_session_off_the_model(client):
    first, _ = _post(client, [])                       # opening request
    on_first, _ = _post(client, [OK])
    assert on_first == first                           # sticky as before
    one_err, _ = _post(client, [OK, ERR])              # 1 error: still sticky
    assert one_err == first
    switched, _ = _post(client, [OK, ERR, ERR])        # 2nd in a row: break
    assert switched != first
    rec = client.app.state.telemetry.recent(0)[-1]
    assert "sticky_broken_tool_errors" in rec.signals
    assert rec.sticky is False


def test_session_does_not_return_to_the_abandoned_model(client):
    first, _ = _post(client, [])
    _post(client, [OK])
    _post(client, [OK, ERR])
    switched, _ = _post(client, [OK, ERR, ERR])
    assert switched != first
    # later turns (clean now) stay on the new model and never go back
    for results in ([OK, ERR, ERR, OK],
                    [OK, ERR, ERR, OK, OK],
                    [OK, ERR, ERR, OK, OK, OK]):
        model, _ = _post(client, results)
        assert model == switched
        assert model != first


def test_a_clean_turn_resets_the_run_so_isolated_errors_never_break(client):
    first, _ = _post(client, [])
    results: list[str] = []
    for r_ in (ERR, OK, ERR, OK, ERR, OK, ERR):        # never two in a row
        results.append(r_)
        model, _ = _post(client, results)
        assert model == first, f"switched on isolated errors at {results}"


def test_other_conversations_are_unaffected(client):
    first, _ = _post(client, [])
    _post(client, [OK]); _post(client, [OK, ERR]); _post(client, [OK, ERR, ERR])
    # a different conversation (different opening message) is independent
    r = client.post("/v1/chat/completions",
                    json={"model": "auto", "tools": TOOLS,
                          "messages": [
                              {"role": "system", "content": "You are an infra agent."},
                              {"role": "user", "content": "check disk usage on the host"}]},
                    headers=auth("hermes"))
    assert r.status_code == 200


def test_latest_tool_error_looks_only_at_the_trailing_tool_results():
    from gateway import _latest_tool_error
    # an error followed by a clean result is NOT a current error — the
    # telemetry heuristic's 3-message window would still see it
    assert _latest_tool_error(_msgs([ERR, OK])) is False
    assert _latest_tool_error(_msgs([OK, ERR])) is True
    assert _latest_tool_error(_msgs([])) is False
    # parallel tool results: any error in the trailing run counts
    msgs = _msgs([OK])
    msgs.append({"role": "tool", "tool_call_id": "c9", "content": ERR})
    assert _latest_tool_error(msgs) is True
    # a plain-text "Error: ..." prefix counts; the word elsewhere does not
    assert _latest_tool_error(_msgs(["Error: file not found"])) is True
    assert _latest_tool_error(_msgs(["no errors found"])) is False


# ----------------------------------------------------------------------
# per-client break threshold (v0.9.3, opt-in; default = global constant)
# ----------------------------------------------------------------------

def test_per_client_break_after_three_tolerates_two_errors(app, client):
    app.state.clients.get("hermes").tool_error_break_after = 3
    first, _ = _post(client, [])
    _post(client, [OK]); _post(client, [OK, ERR])
    still, _ = _post(client, [OK, ERR, ERR])          # 2 in a row: tolerated
    assert still == first
    switched, _ = _post(client, [OK, ERR, ERR, ERR])  # 3 in a row: break
    assert switched != first


def test_per_client_break_after_default_is_unchanged_and_invalid_ignored(app, client):
    from clients import load_clients
    import json, tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp()) / "c.json"
    base = {"id": "x", "key_sha256": "0" * 64}
    d.write_text(json.dumps({"clients": [
        dict(base), dict(base, id="y", tool_error_break_after=0),
        dict(base, id="z", tool_error_break_after=3)]}))
    st = load_clients(d)
    assert st.get("x").tool_error_break_after is None
    assert st.get("y").tool_error_break_after is None      # 0 is falsy -> default
    assert st.get("z").tool_error_break_after == 3
    # default path still breaks at 2 (covered by the pre-existing tests)
    first, _ = _post(client, [])
    _post(client, [OK]); _post(client, [OK, ERR])
    switched, _ = _post(client, [OK, ERR, ERR])
    assert switched != first
