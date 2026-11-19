"""Unit tests for tools/golden_replay.py and the golden set itself:
data well-formedness, the decision-level verifiers (incl. the
sensitive-marker lane assertion), padding, and the diff/manifest
semantics — an undeclared selection change fails; a declared one is
reported but passes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "golden_replay", ROOT / "tools" / "golden_replay.py")
gr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gr)

GOLDEN = json.loads((ROOT / "tests" / "golden" / "golden_set.json")
                    .read_text())


def _shadow(first_lane="private-free", task_class="infra",
            data_class="normal", cand_ids=("a", "b")):
    return {
        "task_class": task_class, "data_class": data_class, "level": 2,
        "would_route_to": {"model": cand_ids[0]},
        "candidates": [{"id": cid, "lane": first_lane if i == 0 else "paid"}
                       for i, cid in enumerate(cand_ids)],
    }


# ----------------------------------------------------------------------
# the set itself

def test_golden_set_well_formed():
    items = GOLDEN["items"]
    assert len(items) >= 20
    ids = [i["id"] for i in items]
    assert len(set(ids)) == len(ids)
    for i in items:
        assert i["client"] in ("hermes", "openclaw", "n8n", "crm")
        assert i["body"]["messages"]
        assert isinstance(i["expect"], dict)


def test_golden_set_has_tool_and_sensitive_items():
    items = GOLDEN["items"]
    assert sum(1 for i in items if i["body"].get("tools")) >= 3
    sens = [i for i in items if i["expect"].get("data_class") == "sensitive"]
    assert len(sens) >= 2
    for i in sens:
        assert "public-only-free" in i["expect"]["forbidden_first_lanes"]


# ----------------------------------------------------------------------
# verifiers

_ITEM = {"id": "x", "expect": {"task_class": "infra",
                               "first_lane_in": ["private-free"]}}


def test_verify_clean():
    assert gr.verify(_ITEM, _shadow()) == []


def test_verify_task_class_mismatch():
    assert any(v.startswith("task_class")
               for v in gr.verify(_ITEM, _shadow(task_class="chat")))


def test_verify_sensitive_first_to_public_lane_is_a_violation():
    item = {"id": "s", "expect": {"data_class": "sensitive",
            "forbidden_first_lanes": ["public-only-free"]}}
    assert gr.verify(item, _shadow(first_lane="public-only-free",
                                   data_class="sensitive"))
    assert gr.verify(item, _shadow(first_lane="private-free",
                                   data_class="sensitive")) == []


def test_verify_no_route_and_shadow_error():
    assert gr.verify(_ITEM, {"error": "http_500"}) == ["shadow_error:http_500"]
    assert gr.verify(_ITEM, {}) == ["no_route"]


# ----------------------------------------------------------------------
# padding + diff

def test_pad_body_grows_last_message():
    body = {"messages": [{"role": "user", "content": "short"}]}
    out = gr.pad_body(body, 1000)
    assert len(out["messages"][0]["content"]) > 3900
    assert body["messages"][0]["content"] == "short"  # input untouched


def test_diff_clean_when_identical():
    base = {"a": gr.outcome_of(_shadow())}
    assert gr.diff_outcomes(base, dict(base)) == []


def test_diff_detects_first_choice_change():
    base = {"a": gr.outcome_of(_shadow(cand_ids=("a", "b")))}
    now = {"a": gr.outcome_of(_shadow(cand_ids=("b", "a")))}
    diffs = gr.diff_outcomes(base, now)
    assert len(diffs) == 1 and diffs[0]["item"] == "a"
    assert "would_route_to" in diffs[0]["fields"]


def test_tally_splits_known_from_new():
    items = [{"id": "a", "known_violation": "F-10 ..."},
             {"id": "b"}]
    new, known = gr.tally({"a": ["x"], "b": ["y"]}, items)
    assert new == {"b": ["y"]} and known == {"a": ["x"]}


def test_outcome_uses_model_key_for_candidates():
    out = gr.outcome_of(_shadow())
    assert out["candidates"] == ["a", "b"]
