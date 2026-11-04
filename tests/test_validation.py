"""Output validation checks + streamed tool-call assembly."""

from __future__ import annotations

from validation import ToolCallAssembler, validate_response

TOOLS = [{"type": "function",
          "function": {"name": "run_command", "parameters": {"type": "object"}}}]


def _resp(content=None, *, tool_calls=None, finish="stop"):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": finish}]}


def test_good_text_passes():
    assert validate_response(_resp("hello")) == []


def test_empty_fails():
    assert "empty_response" in validate_response(_resp(""))
    assert "empty_response" in validate_response(_resp(None))


def test_length_without_content_fails():
    assert "length_no_content" in validate_response(_resp("", finish="length"))


def test_tool_call_with_empty_content_passes():
    calls = [{"function": {"name": "run_command", "arguments": "{}"}}]
    assert validate_response(_resp(None, tool_calls=calls), tools=TOOLS) == []


def test_bad_tool_args_fail():
    calls = [{"function": {"name": "run_command", "arguments": "{oops"}}]
    assert "tool_args_invalid_json" in validate_response(
        _resp(None, tool_calls=calls), tools=TOOLS)


def test_unknown_function_fails():
    calls = [{"function": {"name": "nope", "arguments": "{}"}}]
    assert any(f.startswith("unknown_function")
               for f in validate_response(_resp(None, tool_calls=calls), tools=TOOLS))


def test_json_mode_parse():
    assert "json_mode_parse_fail" in validate_response(
        _resp("not json"), json_mode=True)
    assert validate_response(_resp('{"a": 1}'), json_mode=True) == []


def test_json_mode_fenced_payload_passes():
    # 2026-11-04: north-mini-code (reasoning disabled) wraps JSON-mode
    # output in a ```json fence; the payload inside is valid and usable.
    fenced = '```json\n{\n  "name": "Alex Morgan",\n  "age": 28\n}\n```'
    assert validate_response(_resp(fenced), json_mode=True) == []
    bare_fence = '```\n{"a": 1}\n```'
    assert validate_response(_resp(bare_fence), json_mode=True) == []


def test_json_mode_fence_does_not_rescue_bad_content():
    # A fence around non-JSON still fails, and prose around a fence is
    # not stripped (only a clean whole-content fence is tolerated).
    assert "json_mode_parse_fail" in validate_response(
        _resp('```json\nnot json\n```'), json_mode=True)
    assert "json_mode_parse_fail" in validate_response(
        _resp('Here is the JSON:\n```json\n{"a": 1}\n```'), json_mode=True)


def test_assembler_reassembles_split_arguments():
    asm = ToolCallAssembler()
    asm.feed({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "run_", "arguments": ""}}]},
        "finish_reason": None}]})
    asm.feed({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"name": "command", "arguments": "{\"comm"}}]},
        "finish_reason": None}]})
    asm.feed({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": "and\": \"ls\"}"}}]},
        "finish_reason": "tool_calls"}]})
    resp = asm.assembled_response()
    assert validate_response(resp, tools=[
        {"type": "function", "function": {"name": "run_command"}}]) == []
