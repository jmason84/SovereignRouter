"""Output validation. Returns a reason string when an answer is unusable."""
from __future__ import annotations

import json


def validate_message(message: dict, finish_reason: str | None, *, tool_names: set[str],
                     json_mode: bool) -> str | None:
    content = message.get("content") or ""
    calls = message.get("tool_calls") or []
    if not content.strip() and not calls:
        return "length_no_content" if finish_reason == "length" else "empty"
    for c in calls:
        fn = c.get("function", {})
        if tool_names and fn.get("name") not in tool_names:
            return "unknown_tool"
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (ValueError, TypeError):
            return "bad_tool_args"
        if not isinstance(args, dict):
            return "bad_tool_args"
    if json_mode and not calls:
        try:
            json.loads(content)
        except ValueError:
            return "bad_json"
    return None


def validate_response(resp: dict, *, tool_names: set[str], json_mode: bool) -> str | None:
    try:
        choice = resp["choices"][0]
        return validate_message(choice.get("message", {}), choice.get("finish_reason"),
                                tool_names=tool_names, json_mode=json_mode)
    except (KeyError, IndexError, TypeError):
        return "malformed"
