"""Output validation and escalation checks.

Spec ("Output validation and escalation") — a response is *unusable*
when any of these hold:

  * empty answer with no tool call;
  * ``finish_reason == "length"`` with no usable content;
  * a tool call whose arguments are not valid JSON;
  * a tool call whose function name is not in the request's tool list;
  * JSON mode was requested and the content does not parse as JSON.

On failure the gateway retries once on the next candidate at quality+1
and logs an escalation. These checks run on non-streamed answers in
full. For streamed answers, tool-call chunks are buffered to completion
and validated *before* anything is sent to the client (see gateway);
streamed text commits at the first content chunk, after which only the
empty/length checks can be logged, not escalated — that limit is stated
in the spec and mirrored in :func:`validate_streamed_text_outcome`.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def _first_choice(resp: dict[str, Any]) -> dict[str, Any]:
    choices = resp.get("choices") or []
    return choices[0] if choices else {}


def _message(resp: dict[str, Any]) -> dict[str, Any]:
    return _first_choice(resp).get("message") or {}


def _strip_code_fence(text: str) -> str:
    """Remove one clean markdown code fence wrapping the whole content.

    Some models wrap JSON output in a ```json ... ``` fence even when
    JSON mode was requested (observed live 2026-11-04: cohere
    north-mini-code via OpenRouter fences 4/4 samples when its reasoning
    is disabled, and the wrapped payload itself is valid JSON). Only a
    fence that wraps the entire content is stripped; prose around a
    fence, partial fences, and unfenced text are returned unchanged so
    genuinely unparseable content still fails validation.
    """
    t = text.strip()
    if len(t) > 6 and t.startswith("```") and t.endswith("```"):
        inner = t[3:-3]
        # Drop an optional language-tag line (e.g. "json") on its own line.
        first, sep, rest = inner.partition("\n")
        if sep and first.strip().isalnum() and len(first.strip()) <= 10:
            inner = rest
        return inner.strip()
    return text


def validate_response(resp: dict[str, Any], *, tools: Optional[list[dict]] = None,
                      json_mode: bool = False) -> list[str]:
    """Return a list of failure codes; empty list means usable."""
    failures: list[str] = []
    choice = _first_choice(resp)
    message = _message(resp)
    content = message.get("content")
    tool_calls = message.get("tool_calls") or []
    finish = choice.get("finish_reason")

    if not tool_calls and (content is None or str(content).strip() == ""):
        failures.append("length_no_content" if finish == "length" else "empty_response")

    allowed_names: Optional[set[str]] = None
    if tools:
        allowed_names = {
            t.get("function", {}).get("name")
            for t in tools
            if isinstance(t, dict) and t.get("type", "function") == "function"
        }
        allowed_names.discard(None)

    for call in tool_calls:
        fn = (call or {}).get("function") or {}
        name = fn.get("name")
        if allowed_names is not None and name not in allowed_names:
            failures.append(f"unknown_function:{name}")
        args = fn.get("arguments")
        if args is not None:
            if isinstance(args, str):
                try:
                    json.loads(args)
                except (ValueError, TypeError):
                    failures.append("tool_args_invalid_json")
            elif not isinstance(args, dict):
                failures.append("tool_args_invalid_json")

    if json_mode and content:
        try:
            json.loads(_strip_code_fence(content))
        except (ValueError, TypeError):
            failures.append("json_mode_parse_fail")

    return failures


def validate_streamed_text_outcome(*, saw_content: bool, finish_reason: Optional[str],
                                   content_chars: int) -> list[str]:
    """Log-only checks for streamed text after commit (spec: these can be
    logged, not escalated, once the first chunk has been sent)."""
    failures: list[str] = []
    if not saw_content:
        failures.append("length_no_content" if finish_reason == "length" else "empty_response")
    elif finish_reason == "length" and content_chars == 0:
        failures.append("length_no_content")
    return failures


class ToolCallAssembler:
    """Reassembles streamed tool-call deltas (OpenAI SSE shape).

    Arguments arrive as string fragments across chunks; the call is only
    complete — and therefore only validatable — at the end of the stream.
    The gateway buffers these chunks and validates before flushing them
    to the client (spec streaming rule).
    """

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: Optional[str] = None

    def feed(self, chunk: dict[str, Any]) -> None:
        choice = (chunk.get("choices") or [{}])[0]
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        for call in delta.get("tool_calls") or []:
            idx = int(call.get("index", 0))
            slot = self._calls.setdefault(
                idx, {"id": call.get("id"), "type": "function",
                      "function": {"name": "", "arguments": ""}})
            if call.get("id"):
                slot["id"] = call["id"]
            fn = (call or {}).get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]

    @property
    def has_tool_calls(self) -> bool:
        return bool(self._calls)

    def assembled_response(self) -> dict[str, Any]:
        """Shape an OpenAI completion dict from the assembled calls so the
        same :func:`validate_response` checks apply."""
        calls = [self._calls[i] for i in sorted(self._calls)]
        return {
            "choices": [{
                "finish_reason": self.finish_reason or "tool_calls",
                "message": {"role": "assistant", "content": None,
                            "tool_calls": calls},
            }]
        }
