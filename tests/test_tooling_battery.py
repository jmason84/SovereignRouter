"""Unit tests for tools/tooling_battery.py — the scoring machinery
against fake models with known behaviour. The live run is stage 6 of
the release; these tests pin the rubric: exact-args fidelity,
large-payload integrity, parallel calls, follow-through, the
error-recovery discriminator (identical re-issue = the GLM failure
mode), envelope validity, the failed-validation attribution, the
no-evidence rule, and the registry patch the battery is allowed to
emit (measured scores + verified_until stamps, nothing else).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

spec = importlib.util.spec_from_file_location(
    "tooling_battery", ROOT / "tools" / "tooling_battery.py")
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)


def _msg_tool(name: str, args, call_id: str = "c1") -> dict:
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name,
                                         "arguments": args}}]}


def _result(message, served, escalated=False, error=None):
    return {"error": error, "served_model": served,
            "escalated": escalated, "message": message}


def _task_of(messages) -> str:
    text = messages[0]["content"]
    if "battery:" not in text:
        return ""
    nonce = text.split("]", 1)[0]
    return nonce.rsplit(":", 1)[-1]


def _is_followup(messages) -> bool:
    return any(m.get("role") == "tool" for m in messages)


def good_model(messages, tools, pin):
    task = _task_of(messages)
    if task == "t1_exact_args":
        return _result(_msg_tool("record_observation", json.dumps(
            {"station": "north-ridge", "samples": 42, "mode": "manual",
             "calibrated": True, "pressure_kpa": 87.5})), pin)
    if task == "t2_large_payload":
        content = messages[0]["content"].split("\n\n", 1)[1]
        return _result(_msg_tool("write_file", json.dumps(
            {"path": "/tmp/battery-note.txt", "content": content})), pin)
    if task == "t3_parallel_calls":
        msg = _msg_tool("get_weather", '{"city": "Denver"}')
        msg["tool_calls"].append(
            {"id": "c2", "type": "function",
             "function": {"name": "get_time",
                          "arguments": '{"timezone": "America/Denver"}'}})
        return _result(msg, pin)
    if task == "t4_follow_through":
        if _is_followup(messages):
            return _result({"role": "assistant",
                            "content": "The reading was 4815 kPa, from "
                                       "the gauge in the north vault."},
                           pin)
        return _result(_msg_tool("lookup_gauge_reading", '{"gauge": 7}'),
                       pin)
    if task == "t5_error_recovery":
        if _is_followup(messages):
            return _result(_msg_tool("list_elements", "{}"), pin)
        return _result(_msg_tool(
            "click_element",
            json.dumps({"selector": 'a[href="#add-bot-to-channel"]'})), pin)
    raise AssertionError(task)


def fixated_model(messages, tools, pin):
    if _task_of(messages) == "t5_error_recovery" and _is_followup(messages):
        return _result(_msg_tool(
            "click_element",
            json.dumps({"selector": 'a[href="#add-bot-to-channel"]'})), pin)
    return good_model(messages, tools, pin)


def badjson_model(messages, tools, pin):
    name = (tools[0].get("function") or {}).get("name", "x")
    if _is_followup(messages):
        return _result({"role": "assistant", "content": "no idea"}, pin)
    return _result(_msg_tool(name, "{oops"), pin)


def truncating_model(messages, tools, pin):
    if _task_of(messages) == "t2_large_payload":
        content = messages[0]["content"].split("\n\n", 1)[1]
        half = "\n".join(content.split("\n")[:40])  # 40 of 160 lines
        return _result(_msg_tool("write_file", json.dumps(
            {"path": "/tmp/battery-note.txt", "content": half})), pin)
    return good_model(messages, tools, pin)


def never_serves(messages, tools, pin):
    return _result({"role": "assistant", "content": "someone else"},
                   "other/model", escalated=False)


def always_escalated(messages, tools, pin):
    return _result({"role": "assistant", "content": "someone else"},
                   "other/model", escalated=True)


# ----------------------------------------------------------------------

def test_good_model_scores_five():
    r = tb.run_entry(good_model, "good/model", "2026-11-13")
    assert r["measured"] and r["tooling_score"] == 5
    assert r["composite"] == 1.0
    assert r["envelope"]["invalid"] == 0


def test_fixated_model_fails_recovery_only():
    r = tb.run_entry(fixated_model, "fix/model", "2026-11-13")
    assert r["components"]["t5_error_recovery"] == 0.0
    assert r["tasks"]["t5_error_recovery"]["outcome"] == "identical_reissue"
    assert r["tooling_score"] == 4  # (1+1+1+1+0+env1)/6 = 0.833 -> 1+round(3.33)


def test_badjson_model_scores_floor_and_envelope_zero():
    r = tb.run_entry(badjson_model, "bad/model", "2026-11-13")
    assert r["components"]["v_envelope"] == 0.0
    assert r["tooling_score"] == 1


def test_truncating_model_loses_payload_component():
    r = tb.run_entry(truncating_model, "trunc/model", "2026-11-13")
    assert r["components"]["t2_large_payload"] == 0.0
    assert r["tasks"]["t2_large_payload"]["integrity"] == 0.25


def test_never_served_is_unmeasured_and_unstamped():
    r = tb.run_entry(never_serves, "gone/model", "2026-11-13")
    assert not r["measured"] and r["tooling_score"] is None
    assert not r["served_self"]
    patch = tb.build_registry_patch([r], "2026-11-13",
                                    today=date(2026, 11, 13))
    assert patch == {}


def test_escalated_away_is_failed_validation_not_no_evidence():
    r = tb.run_entry(always_escalated, "esc/model", "2026-11-13")
    assert r["escalations"] == 5
    assert r["components"]["t1_exact_args"] == 0.0
    assert r["tooling_score"] == 1


def test_registry_patch_shape_for_measured_entry():
    r = tb.run_entry(good_model, "good/model", "2026-11-13")
    patch = tb.build_registry_patch([r], "2026-11-13",
                                    today=date(2026, 11, 13))
    assert patch == {"good/model": {
        "tooling": 5,
        "tooling_source": "tooling-battery:2026-11-13",
        "verified_until": "2026-12-13"}}


def test_derive_score_bounds():
    assert tb.derive_score(1.0) == 5
    assert tb.derive_score(0.0) == 1
    assert tb.derive_score(0.5) == 3
