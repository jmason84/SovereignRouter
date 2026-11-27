"""Production tool-error avoidance (v0.10.2, owner ruling 2026-11-27).

The 2026-11-27 diagnosis: Hermes' production tool_error_next_turn rate
is ~24% pooled and has not moved since the F2/F3 fixes, because the
battery tooling scores cannot discriminate — every major entry carries
tooling 5 while measured production rates span 12.4% (nemotron-3-ultra)
to 34.5% (novita gpt-oss-120b). The owner ruled that PRODUCTION
tool-error telemetry may feed selection for tool-bearing turns (the
J-2-adjacent question, decided 2026-11-27; the battery scores
themselves are NOT edited — this is a separate measured signal layered
on selection, exactly as the stream-abort cooldown is).

Mechanism: every tool-loop continuation request judges the previous
serve (the sticky model's trailing tool results — the F2 signal,
counted once per continuation, never double-counted through the
telemetry 3-message window). Outcomes persist in the quota store; an
entry whose rolling-72h rate reaches RATE_THRESHOLD on at least
MIN_SAMPLES judged turns is DEMOTED for tool-bearing selection:
reordered behind every non-flagged entry in its cost tier, never
removed, never disabled. Free-first is absolute — a flagged free entry
still precedes every paid candidate. Non-tool turns are untouched,
pins still reach flagged entries (J-1 semantics unchanged), and a
flagged entry still serves when it is the only candidate left.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from conftest import auth, build_app  # noqa: E402
from quota import QuotaStore  # noqa: E402
from toolstats import (  # noqa: E402
    MIN_SAMPLES,
    RATE_THRESHOLD,
    WINDOW_S,
    ToolErrorTracker,
)

TOOLS = [{"type": "function",
          "function": {"name": "run_command",
                       "parameters": {"type": "object",
                                      "properties": {
                                          "command": {"type": "string"}}}}}]

ERR = '{"error": "command not found"}'
OK = "step ok"


def _tracker(tmp_path):
    return ToolErrorTracker(QuotaStore(tmp_path / "q.db"))


def _seed(tracker, model_id, samples, errors, now=None):
    now = now or time.time()
    for i in range(samples):
        tracker.record(model_id, i < errors, now=now - i)


# ----------------------------------------------------------------------
# the gates themselves: documented values, min sample, threshold, window
# ----------------------------------------------------------------------

def test_gate_constants_match_the_diagnosis_basis():
    # Fleet spans 12.4% (best major) to 34.5% (worst) on the census
    # method; the bar sits between the pooled Hermes rate (~24%) and
    # the offending cluster, and the sample floor mirrors the
    # governor's level_mix n-gating scaled to per-entry tool turns.
    assert RATE_THRESHOLD == 0.25
    assert MIN_SAMPLES == 50
    assert WINDOW_S == 72 * 3600


def test_below_min_samples_never_flags_even_at_100_percent(tmp_path):
    tr = _tracker(tmp_path)
    _seed(tr, "bad/model", MIN_SAMPLES - 1, MIN_SAMPLES - 1)
    assert tr.flagged() == set()
    st = tr.stats()["bad/model"]
    assert st["samples"] == MIN_SAMPLES - 1 and st["errors"] == MIN_SAMPLES - 1


def test_at_min_samples_and_threshold_flags(tmp_path):
    tr = _tracker(tmp_path)
    _seed(tr, "bad/model", MIN_SAMPLES, MIN_SAMPLES)      # 100% error
    assert tr.flagged() == {"bad/model"}
    tr2 = _tracker(tmp_path / "b")
    _seed(tr2, "edge/model", 100, 25)                      # exactly 25%
    assert tr2.flagged() == {"edge/model"}


def test_below_threshold_never_flags_at_any_sample(tmp_path):
    tr = _tracker(tmp_path)
    _seed(tr, "good/model", 400, 49)                       # 12.25% — ultra-like
    assert tr.flagged() == set()
    assert tr.stats()["good/model"]["rate"] == 49 / 400


def test_window_excludes_old_outcomes(tmp_path):
    tr = _tracker(tmp_path)
    now = time.time()
    _seed(tr, "old/model", MIN_SAMPLES, MIN_SAMPLES,
          now=now - WINDOW_S - 3600)
    assert tr.flagged(now=now) == set()
    assert tr.stats(now=now) == {}


def test_flagging_is_per_entry_not_per_provider(tmp_path):
    tr = _tracker(tmp_path)
    _seed(tr, "prov/bad", 100, 40)
    _seed(tr, "prov/good", 100, 5)
    assert tr.flagged() == {"prov/bad"}


# ----------------------------------------------------------------------
# gateway integration: demotion in selection, tool-bearing turns only
# ----------------------------------------------------------------------

def _shadow(client, with_tools=True):
    body = {"model": "auto",
            "messages": [{"role": "user",
                          "content": "restart the nginx container and verify it"}]}
    if with_tools:
        body["tools"] = TOOLS
    r = client.post("/v1/shadow", json=body, headers=auth("hermes"))
    assert r.status_code == 200, r.text
    return r.json()


def test_tool_turn_demotes_flagged_entry_within_its_tier(tmp_path):
    app = build_app(tmp_path)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        base = _shadow(client)
        first = base["candidates"][0]
        assert first["lane"] in ("private-free", "public-only-free")
        _seed(app.state.toolstats, first["model"], 120, 41)   # ~34%
        after = _shadow(client)
        ids = [c["model"] for c in after["candidates"]]
        # demoted, not removed: still a candidate, no longer first
        assert first["model"] in ids
        assert ids[0] != first["model"]
        # free-first is absolute: the flagged FREE entry still
        # precedes every paid/premium candidate
        lanes = {c["model"]: c["lane"] for c in after["candidates"]}
        paid_idx = [i for i, m in enumerate(ids)
                    if lanes[m] in ("paid", "premium")]
        if paid_idx:
            assert ids.index(first["model"]) < min(paid_idx)
        # and the demotion is annotated on the decision
        assert {"model": first["model"],
                "reason": "tool_error_rate_demoted"} in after["skipped"]


def test_non_tool_turn_is_untouched_by_flagging(tmp_path):
    app = build_app(tmp_path)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        base = _shadow(client, with_tools=False)
        first = base["candidates"][0]
        _seed(app.state.toolstats, first["model"], 120, 41)
        after = _shadow(client, with_tools=False)
        assert [c["model"] for c in after["candidates"]] == \
            [c["model"] for c in base["candidates"]]
        assert not any(s["reason"] == "tool_error_rate_demoted"
                       for s in after["skipped"])


def test_unflagged_tool_turn_order_is_unchanged(tmp_path):
    app = build_app(tmp_path)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        base = _shadow(client)
        # seed a DIFFERENT entry into flag territory: the first
        # candidate's position must not move for its own sake
        other = base["candidates"][-1]["model"]
        _seed(app.state.toolstats, other, 120, 60)
        after = _shadow(client)
        assert after["candidates"][0]["model"] == \
            base["candidates"][0]["model"]


def test_pin_still_reaches_a_flagged_entry(tmp_path):
    app = build_app(tmp_path)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        base = _shadow(client)
        first = base["candidates"][0]["model"]
        _seed(app.state.toolstats, first, 120, 41)
        r = client.post("/v1/chat/completions",
                        json={"model": first, "tools": TOOLS,
                              "messages": [{"role": "user",
                                            "content": "restart nginx"}]},
                        headers=auth("hermes"))
        assert r.status_code == 200, r.text
        assert r.headers["X-Sovereign-Model"] == first


# ----------------------------------------------------------------------
# the recording hook: a continuation judges the previous serve, once
# ----------------------------------------------------------------------

def _msgs(results):
    msgs = [
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


def test_continuation_requests_record_judged_outcomes(tmp_path):
    app = build_app(tmp_path)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        def post(results):
            r = client.post("/v1/chat/completions",
                            json={"model": "auto", "messages": _msgs(results),
                                  "tools": TOOLS},
                            headers=auth("hermes"))
            assert r.status_code == 200, r.text
            return r.headers["X-Sovereign-Model"]

        first = post([])                       # opening serve: unjudged
        assert app.state.toolstats.stats() == {}
        assert post([ERR]) == first            # judges serve 1: error
        st = app.state.toolstats.stats()[first]
        assert (st["samples"], st["errors"]) == (1, 1)
        assert post([ERR, OK]) == first        # judges serve 2: clean
        st = app.state.toolstats.stats()[first]
        assert (st["samples"], st["errors"]) == (2, 1)
