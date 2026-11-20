"""v0.9.0 — the tooling quality dimension (Cycle 1 aspect F / NEXT
plan §2d).

Contract under test:

* `tooling` is a sixth quality class, MEASURED-ONLY: the loader
  accepts it (with the same 1..5 range rule as every class), entries
  without it are unmeasured, and `ModelEntry.tooling_quality()`
  returns None for them instead of inventing a score from other
  classes the way quality_for()'s min-fallback would.
* Selection (the gateway's `_selection_quality`, used by the floor,
  the sort, the steering filter, shadow reporting and both escalation
  paths): for a tool-bearing request, a measured entry ranks on its
  tooling score; an UNMEASURED entry ranks on exactly the input it
  ranked on before the dimension existed — quality_for(task_class) —
  so the dimension never silently re-ranks unmeasured entries.
  Non-tool requests are untouched, and the binary supports_tools
  gate still applies first.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from registry import ModelEntry, validate_entry  # noqa: E402
from gateway import _selection_quality  # noqa: E402
from conftest import build_app, auth  # noqa: E402

TOOLS = [{
    "type": "function",
    "function": {
        "name": "noop",
        "description": "does nothing",
        "parameters": {"type": "object", "properties": {}},
    },
}]


def _entry(**over) -> ModelEntry:
    base = dict(id="x/test", provider="groq", model="test-model",
                lane="private-free",
                quality={"chat": 5, "infra": 5, "code": 1, "reasoning": 5,
                         "long-context": 5},
                context_tokens=131072)
    base.update(over)
    return ModelEntry(**base)


# ----------------------------------------------------------------------
# registry layer
# ----------------------------------------------------------------------

def test_loader_accepts_measured_tooling_score():
    raw = {"id": "x/test", "provider": "groq", "model": "m",
           "lane": "paid", "context_tokens": 8192,
           "quality": {"chat": 3, "tooling": 4},
           "tooling_source": "tooling-battery:2026-11-13"}
    e = validate_entry(raw)
    assert e.tooling_quality() == 4
    assert e.tooling_source == "tooling-battery:2026-11-13"


def test_loader_rejects_out_of_range_tooling_score():
    raw = {"id": "x/test", "provider": "groq", "model": "m",
           "lane": "paid", "context_tokens": 8192,
           "quality": {"chat": 3, "tooling": 9}}
    with pytest.raises(ValueError):
        validate_entry(raw)


def test_tooling_quality_is_none_when_unmeasured():
    assert _entry().tooling_quality() is None
    assert _entry(quality={"chat": 3, "tooling": 2}).tooling_quality() == 2


# ----------------------------------------------------------------------
# the fallback rule (unit level)
# ----------------------------------------------------------------------

def test_selection_quality_uses_measured_tooling_on_tool_turns():
    e = _entry(quality={"chat": 5, "infra": 5, "code": 1, "reasoning": 5,
                        "long-context": 5, "tooling": 2})
    assert _selection_quality(e, "chat", has_tools=True) == 2
    assert _selection_quality(e, "chat", has_tools=False) == 5


def test_selection_quality_fallback_is_todays_input_not_min():
    # The unmeasured entry's chat quality is 5 and its code quality is
    # 1 (min-known = 1). On a tool-bearing chat turn it must rank on
    # the 5 it ranked on before the dimension existed — NOT on the
    # min-fallback quality_for("tooling") would invent.
    e = _entry()
    assert e.quality_for("tooling") == 1  # the trap, documented
    assert _selection_quality(e, "chat", has_tools=True) == 5
    assert _selection_quality(e, "code", has_tools=True) == 1


# ----------------------------------------------------------------------
# selection end-to-end (shadow over a minimal registry)
# ----------------------------------------------------------------------

def _mini_registry(tmp_path: Path) -> Path:
    def m(id_, quality, latency, **kw):
        d = {"id": id_, "provider": "groq", "model": f"m-{id_}",
             "lane": "private-free", "quality": quality,
             "context_tokens": 131072, "latency_p50_ms": latency,
             "input_usd_per_million": 0.0, "output_usd_per_million": 0.0}
        d.update(kw)
        return d

    five = {"chat": 5, "infra": 5, "code": 5, "reasoning": 5,
            "long-context": 5}
    models = [
        # alpha: strong everywhere, UNMEASURED for tooling.
        m("groq/alpha", dict(five), 100),
        # beta: weak base classes, MEASURED tooling 5.
        m("groq/beta", {"chat": 2, "infra": 2, "code": 2, "reasoning": 2,
                        "long-context": 2, "tooling": 5}, 50,
          tooling_source="tooling-battery:2026-11-13"),
        # gamma: solid base classes, MEASURED tooling 2.
        m("groq/gamma", {"chat": 4, "infra": 4, "code": 4, "reasoning": 4,
                         "long-context": 4, "tooling": 2}, 100,
          tooling_source="tooling-battery:2026-11-13"),
        # delta: strong + measured, but the binary gate says no tools.
        m("groq/delta", dict(five, tooling=5), 100, supports_tools=False,
          tooling_source="tooling-battery:2026-11-13"),
    ]
    p = tmp_path / "mini-registry.json"
    p.write_text(json.dumps({"models": models}))
    return p


def _shadow_order(client, body, headers=None):
    r = client.post("/v1/shadow", json=body,
                    headers=headers or auth("hermes"))
    assert r.status_code == 200, r.text
    data = r.json()
    return [c["model"] for c in data["candidates"]], data


def test_tool_turns_rank_on_tooling_chat_turns_do_not(tmp_path):
    app = build_app(tmp_path / "a", registry_path=_mini_registry(tmp_path))
    with TestClient(app) as client:
        body = {"model": "auto",
                "messages": [{"role": "user", "content": "hello there"}]}
        chat_order, _ = _shadow_order(client, body)
        # chat (L1, floor 2): base classes rule — alpha(5) > gamma(4)
        # > beta(2); delta ties alpha on quality, loses on registry
        # order. Beta's measured tooling 5 buys it NOTHING here.
        assert chat_order == ["groq/alpha", "groq/delta", "groq/gamma",
                              "groq/beta"]

        tool_order, data = _shadow_order(client, {**body, "tools": TOOLS})
        # tools (v0.9.2 / F3): the measured tooling score is a CEILING —
        # a tool turn ranks on min(tooling, class quality). Beta's
        # saturated tooling 5 no longer lifts a model that is weak at
        # the task (class quality 2 -> 2); alpha (unmeasured, falls
        # back to its class quality 5) leads. Beta and gamma tie at 2
        # and latency breaks it (beta is faster). Delta is still gated
        # out by supports_tools despite its measured 5.
        assert tool_order == ["groq/alpha", "groq/beta", "groq/gamma"]
        reported = {c["model"]: c["quality"] for c in data["candidates"]}
        assert reported == {"groq/alpha": 5, "groq/beta": 2,
                            "groq/gamma": 2}
        skipped = {s["model"]: s["reason"] for s in data["skipped"]}
        assert skipped["groq/delta"] == "tools_unsupported"


def test_tooling_floor_bites_only_on_tool_turns(tmp_path):
    app = build_app(tmp_path / "b", registry_path=_mini_registry(tmp_path))
    lvl4 = {**auth("hermes"), "X-Sovereign-Level": "4"}
    body = {"model": "auto",
            "messages": [{"role": "user", "content": "hello there"}]}
    with TestClient(app) as client:
        chat_order, _ = _shadow_order(client, body, headers=lvl4)
        # L4 floor is 4 on base classes: gamma(4) in, beta(2) out.
        assert "groq/gamma" in chat_order and "groq/beta" not in chat_order

        tool_order, _ = _shadow_order(client, {**body, "tools": TOOLS},
                                      headers=lvl4)
        # v0.9.2 / F3: on tool turns the floor reads min(tooling, class
        # quality). Beta (tooling 5 but class 2) and gamma (class 4 but
        # tooling 2) are BOTH out at the L4 floor; only a model that is
        # good at tools AND at the task clears it (alpha).
        assert tool_order == ["groq/alpha"]


def test_saturated_tooling_does_not_let_a_weak_model_outrank_a_strong_one(
        tmp_path):
    """Regression for the 2026-11-19 routing diagnosis: with tooling
    scores saturated at 5, the fastest weak free model won every tool
    turn. A strong model with the same tooling score must outrank a weak
    one regardless of latency, and the weak one must fail an L4 floor."""
    def m(id_, cls, tooling, latency):
        return {"id": id_, "provider": "groq", "model": f"m-{id_}",
                "lane": "private-free",
                "quality": {"chat": cls, "infra": cls, "code": cls,
                            "reasoning": cls, "long-context": cls,
                            "tooling": tooling},
                "tooling_source": "tooling-battery:2026-11-19",
                "context_tokens": 131072, "latency_p50_ms": latency,
                "input_usd_per_million": 0.0, "output_usd_per_million": 0.0}
    reg = tmp_path / "sat.json"
    reg.write_text(json.dumps({"models": [
        m("groq/weak-fast", 2, 5, 50),
        m("groq/strong-slow", 5, 5, 900),
    ]}))
    app = build_app(tmp_path / "s", registry_path=reg)
    body = {"model": "auto", "tools": TOOLS,
            "messages": [{"role": "user", "content": "hello there"}]}
    with TestClient(app) as client:
        order, _ = _shadow_order(client, body)
        assert order == ["groq/strong-slow", "groq/weak-fast"]
        lvl4 = {**auth("hermes"), "X-Sovereign-Level": "4"}
        order4, _ = _shadow_order(client, body, headers=lvl4)
        assert order4 == ["groq/strong-slow"]
