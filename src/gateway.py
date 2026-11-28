"""Sovereign Router v0.5 — gateway application.

One OpenAI-compatible endpoint for OpenClaw, Hermes and n8n that sends
every request to the cheapest model able to do it well (spec v0.5.3).

Pipeline (spec "Request pipeline"):
  auth -> normalize -> sensitivity scan -> profile -> classify ->
  sticky check -> candidate selection -> cost governor steering ->
  execute with failover -> validate + escalate -> respond with route
  headers, finalize usage/cost, write one JSON route line.

Error contract (spec "Interfaces"):
  * 429 with Retry-After + skip reasons ONLY when every candidate is
    rate-limited by its provider or failing;
  * 413 ONLY when no eligible model can ever fit the request;
  * 403 for lanes the client may not use (incl. explicit premium without
    permission, and an active manual killswitch).
  * A request is NEVER refused for budget reasons.

Free-first / paid-spillway waterfall (v0.5.5, owner ruling 2026-11-07):
  candidate selection is free-first and unchanged; when it yields ZERO
  eligible candidates — or every candidate fails upstream — ONE
  spillway pass over the paid + premium entries (the gateway's normal
  cost order, cheapest paid first) may serve the turn instead of a
  429. In that pass ONLY, the sensitive provider-clearance wall and
  the per-client premium gate are relaxed (the owner cleared paid
  providers for sensitive data as the spillway of last resort);
  quality, tools, context, verification and provider rate limits
  still bind, and the governor's emergency paid-lane switch still
  vetoes. Spillway serves are recorded as escalations with the reason
  "free_exhausted_paid_fallthrough". This generalises and replaces
  the v0.5.3 sensitive no-candidate rescue — one mechanism, not two.

SiliconFlow non-sensitive paid slot (v0.5.6, owner ruling 2026-11-07):
  SiliconFlow joins the paid pool at half of Z.ai direct's GLM-5.3
  price, but its data posture was graded UNCLEAR by the 2026-11-07
  probe (training never explicitly prohibited, retention unspecified,
  no ZDR), so it must never receive a sensitive-classified turn —
  including via the spillway, whose whole point is relaxing the
  clearance wall. Registry entries carry ``spillway_sensitive_excluded``
  for this: candidate selection drops a flagged entry for sensitive
  requests in EVERY pass (normal, spillway, shadow, explicit pins),
  while non-sensitive requests see it in its normal cost-order
  position. The same change makes a provider with no usable key
  loaded (missing/empty ``keys/<provider>.key``) skip at selection
  time (``provider_key_missing``) instead of burning a failover
  attempt on an unauthenticated upstream call — SiliconFlow ships
  keyless until the owner's key lands, and must be invisible until
  then, never an error source.

Warn-only emergency + dead-lane alerts (v0.5.7, owner ruling
2026-11-09): the $25/day emergency point is an ALERT, never a cutoff —
the 2026-11-09 incident (DeepInfra out of credits at 04:48, cascade
forced onto premium, hard switch tripped at 12:29 and removed every
paid rung at once, agents 429-walled) proved the hard switch defeats
the router's purpose. With SOVEREIGN_EMERGENCY_WARN_ONLY=1 the
governor warns and the cascade keeps walking in cost order. The same
build records a ``lane_dead`` alert the first time each day a model
returns an account-class upstream failure (401/402/403) or exhausts
its daily token cap, so a dead cheap rung surfaces within one
alert-chain poll instead of burning silently for hours.

True-cost ledger (v0.8.3, owner order 2026-11-13 — "ADJUST THE LEDGER
TO THE ACTUAL AMOUNT", reversing his 2026-11-12 ruling that this fix
was not necessary): the gateway parses cached-input token counts from
every upstream usage shape (OpenAI prompt_tokens_details, DeepSeek
prompt_cache_hit_tokens, Anthropic cache_read_input_tokens, Google
usageMetadata.cachedContentTokenCount) on BOTH the JSON and streaming
paths, records them on the route row and usage event, and prices them
at the entry's cached_input_usd_per_million rate (falling back to the
full input rate where the provider publishes no cached rate). Before
this, cached_tokens was 0 on effectively every row and cache-heavy
turns were priced as full-price misses — the phantom-dollar
overstatement proven against the provider consoles on 2026-11-12
(DeepSeek's 30-day actual $9.34 vs the ledger's $109.32 for V4 Pro on
2026-11-11 alone). History is NOT rewritten: pre-v0.8.3 route rows
remain ceiling estimates; dollars are actuals only from this deploy.

No startup patches: this is the v0.5 source tree (the v0.3.3 canary's
PATCH_V033 mechanism is gone). Provider keys are read once at startup
from mode-600 files in the state dir (``keys/<provider>.key``); they are
never in the registry, compose files, logs or this source tree.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from classifier import REQUIRED_QUALITY, Classifier
from clients import ALIASES, ClientPolicy, ClientStore, extract_bearer_key, load_clients
from governor import EmergencyConfig, Governor
from pools import (DemotionTracker, classify_failure,
                   classify_truncated_tool_call, pool_for)
from quota import AttemptEvent, QuotaStore, UsageEvent
from registry import FREE_LANES, ModelEntry, Registry, load_registry
import tiering
from sensitivity import ScanResult, scan_messages, scan_pii_messages
from sticky import (StickyStore, TOOL_ERROR_BREAK_AFTER,
                    conversation_key)
from telemetry import RouteRecord, Telemetry
from toolstats import ToolErrorTracker
from validation import (ToolCallAssembler, validate_response,
                        validate_streamed_text_outcome)

VERSION = "0.10.10"

# Fields forwarded upstream (spec: "allowlisted fields"). Anything else
# a client sends is dropped so provider-specific junk cannot leak across
# providers when a request fails over.
# v0.6.1: reasoning_effort joins the allowlist — Meta's Muse Spark
# defaults an omitted effort to "high" and burns hundreds of hidden
# reasoning tokens per turn; until now a client-sent effort was dropped
# here and no registry entry could set one either, so Meta's documented
# control was unexercisable through the gateway (route-health
# diagnosis 2026-11-12, docs/cycles/route-health-investigation-protocol.md).
ALLOWED_FIELDS = {
    "messages", "temperature", "top_p", "max_tokens", "stream", "tools",
    "tool_choice", "response_format", "stop", "presence_penalty",
    "frequency_penalty", "seed", "user", "n", "reasoning_effort",
}
# Providers whose upstreams REJECT a top-level reasoning_effort field
# outright with HTTP 400 — and 400 is not in FAILOVER_STATUSES, so the
# poisoned field would fail the whole turn instead of failing over.
# upstream_body strips the field for these providers AFTER the
# extra_body merge, so neither a client nor a registry entry can leak
# it upstream. Evidence (2026-11-12, fix package v0.6.1; full table in
# docs/cycles/fix-package-v0.6.1-decision.md): Google — proven 400
# "Thinking budget is not supported for this model" on the Gemini
# OpenAI-compatible endpoint (protocol doc, probe G3). Mistral is
# handled in upstream_body rather than here because its validation is
# per-model (see _reasoning_effort_stripped). The other providers were
# probe-audited live from VM103 with the field set: accepted or
# ignored (cerebras, deepinfra, deepseek, groq, huggingface, novita,
# nvidia, openai, openrouter, siliconflow, aion); cloudflare, ollama
# and zai answered with account-level errors that do not implicate
# the field (dead neuron pool / no credits / overload).
REASONING_EFFORT_STRIPPED_PROVIDERS = {"google"}


def _reasoning_effort_stripped(entry: ModelEntry) -> bool:
    """True when a top-level reasoning_effort must not reach this
    entry's upstream (v0.6.1 poison-field guard; evidence above)."""
    if entry.provider in REASONING_EFFORT_STRIPPED_PROVIDERS:
        return True
    # Mistral validates reasoning_effort per model (live probes
    # 2026-11-12): ministral-8b rejects the field outright ("not
    # enabled for this model") and mistral-small / mistral-medium
    # reject the graded values, accepting only none/high — while
    # mistral-large (the registry's only reasoning-flagged Mistral
    # entry) accepts it. Strip for every Mistral entry that is not
    # reasoning-flagged; a lost client control on those entries is
    # cheaper than a 400 that fails the whole turn.
    if entry.provider == "mistral" and not entry.reasoning:
        return True
    return False


# ----------------------------------------------------------------------
# Gemini 3 thought-signature bypass (v0.10.1, 2026-11-25 census)
# ----------------------------------------------------------------------
# Gemini 3 enforces thought-signature validation on replayed function
# calls: an assistant tool_call sent back without a thought_signature
# is a hard 400 ("Function call is missing a thought_signature in
# functionCall parts"). A router that switches models mid-conversation
# replays tool calls ORIGINATED BY OTHER MODELS, which carry no Google
# signature — so every tool-history turn that landed on the direct
# google/gemini-3.x entries 400'd (112 request_fault attempts in the
# census week, chronic since 2026-11-13; root-caused by a pinned live
# probe that finally surfaced the upstream body). Google's own
# thought-signatures documentation names this exact case —
# functionCall parts "not generated by the API ... when transferring
# history from a model that does not include thought signatures" —
# and documents the bypass value below for it (a last resort that can
# cost some reasoning continuity, against the alternative of a 400).
# On the OpenAI-compatible wire the signature rides on the tool_call
# as extra_content.google.thought_signature.
_THOUGHT_SIGNATURE_BYPASS = "skip_thought_signature_validator"


def _needs_thought_signature_bypass(entry: ModelEntry) -> bool:
    """True only for direct-Google Gemini 3 entries — the upstreams
    that enforce signature validation. Aggregator-fronted Gemini
    (OpenRouter etc.) normalizes this itself and is not touched."""
    return entry.provider == "google" \
        and entry.model.startswith("gemini-3")


def _with_thought_signature_bypass(messages: Any) -> Any:
    """Return `messages` with the documented bypass signature on every
    assistant tool_call that lacks one. A real signature the client
    preserved is never overwritten. Copy-on-write: the client's list
    and dicts are never mutated, so a later fallback attempt to a
    different provider cannot inherit Google metadata."""
    if not isinstance(messages, list):
        return messages
    out: Optional[list] = None
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        new_calls: Optional[list] = None
        for j, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            extra = call.get("extra_content")
            google = extra.get("google") if isinstance(extra, dict) \
                else None
            if isinstance(google, dict) and google.get("thought_signature"):
                continue  # a real signature: leave it exactly as sent
            if new_calls is None:
                new_calls = list(tool_calls)
            new_call = dict(call)
            new_extra = dict(extra) if isinstance(extra, dict) else {}
            new_google = dict(google) if isinstance(google, dict) else {}
            new_google["thought_signature"] = _THOUGHT_SIGNATURE_BYPASS
            new_extra["google"] = new_google
            new_call["extra_content"] = new_extra
            new_calls[j] = new_call
        if new_calls is not None:
            if out is None:
                out = list(messages)
            new_msg = dict(msg)
            new_msg["tool_calls"] = new_calls
            out[i] = new_msg
    return out if out is not None else messages


# ----------------------------------------------------------------------
# DeepSeek thinking-mode reasoning_content pad (v0.10.2, 2026-11-27)
# ----------------------------------------------------------------------
# Same family as the Gemini bug above: direct DeepSeek thinking-mode
# entries (deepseek/deepseek-v4-pro) hard-400 when a tool-shaped
# request replays assistant history whose reasoning_content is
# missing — "The `reasoning_content` in the thinking mode must be
# passed back to the API" (attempt_events.error_body, 3x in the 72h
# before 2026-11-27). DeepSeek's Thinking Mode documentation states
# the rule: for requests carrying the `tools` parameter, the
# reasoning_content of ALL previous turns must be passed back — even
# turns that made no tool call — or the API returns 400. A
# model-switching router replays assistant turns originated by other
# models, which carry no DeepSeek reasoning at all, and clients whose
# serialisers drop the extra field lose it for DeepSeek's own turns.
# The established fix shape (the one Hermes itself shipped for this
# error) is a defensive pad: an assistant message lacking a
# reasoning_content carries an empty string, which DeepSeek accepts;
# a real value the client preserved is never overwritten.
def _needs_reasoning_content_pad(entry: ModelEntry) -> bool:
    """True only for direct-DeepSeek reasoning entries — the upstreams
    that enforce the pass-back rule. Aggregator-fronted DeepSeek
    (DeepInfra, OpenRouter) is served by those stacks' own rules and
    is not touched."""
    return entry.provider == "deepseek" and entry.reasoning


def _with_reasoning_content_pad(messages: Any) -> Any:
    """Return `messages` with reasoning_content="" on every assistant
    message that lacks a real one (absent or null). Copy-on-write:
    the client's list and dicts are never mutated, so a later
    fallback attempt to a different provider cannot inherit the pad."""
    if not isinstance(messages, list):
        return messages
    out: Optional[list] = None
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        if isinstance(msg.get("reasoning_content"), str):
            continue  # a real value (even ""): leave it exactly as sent
        if out is None:
            out = list(messages)
        new_msg = dict(msg)
        new_msg["reasoning_content"] = ""
        out[i] = new_msg
    return out if out is not None else messages


def _tool_shaped(body: dict[str, Any]) -> bool:
    """The request carries tools, or its history already contains a
    tool loop (assistant tool calls / tool results). DeepSeek's
    pass-back rule binds on the `tools` parameter; the history shapes
    cover a continuation whose client dropped the tools array but is
    still replaying a tool loop at a thinking entry."""
    if body.get("tools"):
        return True
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool" or msg.get("tool_calls"):
            return True
    return False


# ----------------------------------------------------------------------
# reasoning_effort value shaping (v0.10.3, 2026-11-27 cleanup loop)
# ----------------------------------------------------------------------
# Two premium last resorts reject specific reasoning_effort VALUES
# with hard 400s (captured verbatim in attempt_events.error_body);
# 400 is not in FAILOVER_STATUSES, so a poisoned value fails the
# whole turn when the cascade reaches these entries with nothing
# left behind them. Both remedies are the upstreams' own error text:
# * OpenAI gpt-5.5 + function tools: function tools with a graded
#   reasoning_effort are not supported for gpt-5.5 in
#   /v1/chat/completions; the accepted value on a tool-bearing turn
#   is "none". An omitted field defaults server-side to a graded
#   effort, so the value is SET even when the client sent none.
#   Tool-free turns keep the client's effort untouched.
# * Z.ai glm-5.3 (error 1210): the model always thinks and accepts
#   only low/high/max; a client-sent "none" is rewritten to "low".
#   An absent field stays absent — Z.ai applies its own default.
def _shape_reasoning_effort(entry: ModelEntry, out: dict[str, Any]) -> None:
    """Rewrite a reasoning_effort value this entry's upstream would
    400 on (v0.10.3). Narrow by provider+model; `out` is the
    per-attempt upstream body, so the rewrite cannot leak into a
    fallback attempt to a different provider."""
    if entry.provider == "openai" and entry.model == "gpt-5.5":
        if _tool_shaped(out):
            out["reasoning_effort"] = "none"
    elif entry.provider == "zai" and entry.model == "glm-5.3":
        if out.get("reasoning_effort") == "none":
            out["reasoning_effort"] = "low"


# ----------------------------------------------------------------------
# temperature guard (v0.10.3, 2026-11-27 cleanup loop — third shape)
# ----------------------------------------------------------------------
# premium/gpt-5.5 also rejects a client TEMPERATURE: "Unsupported
# value: 'temperature' does not support 0.2 with this model"
# (attempt_events.error_body, 2026-11-27 19:22 UTC — three n8n L5
# turns died end-to-end when the free pass exhausted on the
# OpenRouter daily cap + Cloudflare neuron wall and the premium step
# then 400'd on the forwarded 0.2). GPT-5-class reasoning models run
# at their default temperature only; omitting the field lets the
# upstream default apply. Narrow by provider+model, like the effort
# shaping above — every other entry keeps the client's temperature.
def _temperature_stripped(entry: ModelEntry) -> bool:
    """True when a client temperature must not reach this entry's
    upstream (v0.10.3): direct-OpenAI gpt-5.5 rejects every
    non-default value. Aggregator-fronted gpt-5.5 is normalized by
    that stack and is not touched."""
    return entry.provider == "openai" and entry.model == "gpt-5.5"


# ----------------------------------------------------------------------
# Upstream error-body capture (v0.10.1, 2026-11-25 census)
# ----------------------------------------------------------------------
# Failed attempts persist a bounded, scrubbed copy of the upstream
# error body (attempt_events.error_body). Before v0.10.1 the body was
# classified in memory and discarded, which left every 400-class
# failure undiagnosable from stored data. The scrub removes
# credential-shaped material before anything is written; the bound
# keeps a hostile or verbose upstream from bloating the ledger.
_ERROR_BODY_MAX = 500
_ERROR_BODY_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(api[_-]?key|authorization)\s*[:=]\s*\"?[^\s\",}]+"),
)


def _scrub_error_body(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    out = text
    for pattern in _ERROR_BODY_SECRET_PATTERNS:
        out = pattern.sub("[redacted]", out)
    out = out.strip()
    if not out:
        return None
    return out[:_ERROR_BODY_MAX]


def _default_max_tokens(body: dict[str, Any]) -> int:
    """The output budget a request gets when neither the client nor
    an internal override states one (v0.8.1, F-09).

    Tool-bearing is read from the request's own shape, the same way
    the handlers read it for selection (``body.get("tools") or []``):
    a non-empty ``tools`` array, or an explicit ``tool_choice``.
    Any explicit tool_choice counts — including "none": the client
    has stated a tool-calling shape for the request, and the raised
    budget is a ceiling that protects the tool-call payloads that
    shape produces; a turn that emits no tool call simply never
    approaches it. Everything else is plain chat and keeps the
    DEFAULT_MAX_TOKENS budget."""
    if body.get("tools") or body.get("tool_choice") is not None:
        return TOOL_DEFAULT_MAX_TOKENS
    return DEFAULT_MAX_TOKENS


# v0.10.5 (Job B, owner go 2026-11-27 8:25 PM MT): the tool-result
# error detector. Pre-v0.10.5 this was a bare substring test for
# '"error"', so a SUCCESS payload such as {"error": null, "data": ...}
# or {"result": "ok", "error": ""} counted as a tool error. That fed
# tool_error_next_turn, the F2 sticky break and (via the route rows)
# the v0.10.2 production tool-error ledger — false positives demoted
# healthy models. The detector now parses JSON when it can and only
# counts an error field that actually carries a value.
_ERROR_FIELD_KEYS = ("error", "errors", "error_message", "errormessage",
                     "exception", "traceback", "stderr_error")
_ERROR_FLAG_KEYS = ("is_error", "iserror")
_SUCCESS_FALSE_KEYS = ("ok", "success", "succeeded")
_ERROR_STATUS_VALUES = {"error", "errored", "failed", "failure", "fail",
                        "exception", "fatal"}
_TEXT_ERROR_PREFIXES = ("error", "traceback (most recent call last)",
                        "exception", "fatal:", "failed:", "failure:")
# A JSON-ish "error" field with a non-empty value, for payloads that are
# not valid JSON as a whole (truncated output, JSON lines, prose + JSON).
_ERROR_FIELD_VALUE_RE = re.compile(
    r'"(?:error|errors|error_message|exception)"\s*:\s*'
    r'(?!null\b|false\b|""|\[\s*\]|\{\s*\}|0\b)\S')
_ZERO_ERRORS_RE = re.compile(
    r"^errors?(?:\s+count)?\s*[:=]\s*(?:0|none|null|false|\[\s*\])\s*(?:$|[,;\n])")
_ERROR_FLAG_TRUE_RE = re.compile(r'"(?:is_error|isError)"\s*:\s*true\b')
_STATUS_ERROR_RE = re.compile(
    r'"status"\s*:\s*"(?:error|errored|failed|failure|fail|exception|fatal)"',
    re.IGNORECASE)


def _value_is_set(value: Any) -> bool:
    """An error field 'carries a value' unless it is null/false/empty/0."""
    if value is None or value is False:
        return False
    if isinstance(value, (str, list, dict, tuple)) and not value:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and value == 0:
        return False
    if isinstance(value, str) and value.strip().lower() in (
            "", "none", "null", "false", "0", "ok", "no error"):
        return False
    return True


def _json_obj_has_error(obj: Any, depth: int = 0) -> bool:
    if depth > 2:
        return False
    if isinstance(obj, list):
        # MCP-style content arrays / batched results: any item errors.
        return any(_json_obj_has_error(o, depth + 1) for o in obj[:20])
    if not isinstance(obj, dict):
        return False
    lower = {str(k).lower(): v for k, v in obj.items()}
    for k in _ERROR_FLAG_KEYS:
        if lower.get(k) is True:
            return True
    for k in _ERROR_FIELD_KEYS:
        if k in lower and _value_is_set(lower[k]):
            return True
    for k in _SUCCESS_FALSE_KEYS:
        if k in lower and lower[k] is False:
            return True
    status = lower.get("status")
    if isinstance(status, str) and status.strip().lower() in _ERROR_STATUS_VALUES:
        return True
    # One level of common wrappers.
    for k in ("result", "data", "response", "output", "content"):
        if k in lower and isinstance(lower[k], (dict, list)):
            if _json_obj_has_error(lower[k], depth + 1):
                return True
    return False


def _tool_text_has_error(text: str) -> bool:
    """True when a tool result reports a failure.

    Parsed JSON: an error-ish key with a real value, an is_error/isError
    flag, ok/success == false, or status in an error vocabulary. Plain
    text: a leading Error/Traceback/Exception/Fatal line, or an error
    field with a non-empty value embedded in otherwise unparseable
    JSON. A success payload that merely contains the word "error" (or an
    ``"error": null`` field) is NOT an error."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if stripped[0] in "{[":
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if parsed is not None:
            return _json_obj_has_error(parsed)
    low = stripped.lower()
    if _ZERO_ERRORS_RE.match(low):
        return False
    if low.startswith(_TEXT_ERROR_PREFIXES):
        return True
    if _ERROR_FLAG_TRUE_RE.search(stripped) or _STATUS_ERROR_RE.search(stripped):
        return True
    return bool(_ERROR_FIELD_VALUE_RE.search(low))


def _latest_tool_error(messages: list[dict]) -> bool:
    """True when the tool result(s) the model is about to react to carry
    an error — the trailing run of tool-role messages only.

    F2 needs this narrower signal. ``has_tool_error`` (telemetry) looks at
    the last three messages, so a single tool error stays inside that
    window on the NEXT request too and is counted twice; driving the
    sticky break from it would abandon a model after ONE isolated error
    followed by a clean turn."""
    trailing: list[dict] = []
    for m in reversed(messages):
        if m.get("role") != "tool":
            break
        trailing.append(m)
    return any(_tool_text_has_error(_message_text(m)) for m in trailing)


def _selection_quality(entry: ModelEntry, task_class: str,
                       has_tools: bool) -> int:
    """The quality selection ranks and floors on (v0.9.0, Cycle 1
    aspect F / NEXT plan §2d — the tooling dimension).

    For a tool-bearing request, an entry with a MEASURED tooling score
    is ranked on that score: tool competence is what the turn actually
    exercises, and tonight's incident record (2026-11-13) is three
    production tool-call defects on models that chat/code quality had
    ranked as peers. An entry with NO tooling score is unmeasured, and
    the fallback rule is strict: it ranks on exactly the input it
    ranked on before the dimension existed — quality_for(task_class),
    the request's classified class — so adding the dimension never
    silently re-ranks an unmeasured entry. Non-tool requests are
    untouched. The binary supports_tools gate is unchanged and still
    applies first (build_candidates).

    v0.9.2 (F3, 2026-11-19 routing diagnosis): the measured tooling score
    is a CEILING, not a replacement. The one-shot battery saturates —
    60 of 85 enabled entries scored 5, including a 1.7B model — so
    ranking on it alone made every free model tie and let the fastest
    weak one win. A tool turn now ranks on min(tooling, quality for the
    request's class): the model must be good at tools AND good at the
    task. Unmeasured entries are unchanged (quality_for(task_class))."""
    base = entry.quality_for(task_class)
    if has_tools:
        measured = entry.tooling_quality()
        if measured is not None:
            return min(measured, base)
    return base
FAILOVER_STATUSES = {401, 403, 408, 413, 429, 500, 502, 503, 504}
# v0.10.5 (Job B, owner go 2026-11-27): fallback-depth cap. A turn makes
# at most this many upstream calls across the free-first pass, the
# same-model length retry, validation escalation and the paid spillway.
# When a spillway may still engage, the first pass stops early enough to
# leave SPILLWAY_RESERVED_ATTEMPTS for it, so the cap never converts a
# servable turn into a 429 just because the free pools are congested.
MAX_UPSTREAM_ATTEMPTS = 6
SPILLWAY_RESERVED_ATTEMPTS = 2
DEPTH_CAP_REASON = "fallback_depth_cap"
PREMIUM_NOT_REQUIRED_REASON = "premium_not_required_congestion"
# Escalation reason recorded on every turn the paid spillway serves
# (v0.5.5 free-first / paid-spillway waterfall, owner ruling 2026-11-07).
SPILLWAY_REASON = "free_exhausted_paid_fallthrough"
DEFAULT_MAX_TOKENS = 2048          # spec: max_tokens clamp, 2048 default
# v0.8.1 (F-09; protocol §7): the 2048 default binds every client that
# omits max_tokens — and Hermes omits it on every turn. The victims
# were tool calls: a write_file body is a single tool-call payload,
# and tool payloads are the longest outputs those clients produce, so
# the default truncated tool calls mid-stream (finish=length at
# exactly 2048) while plain chat never noticed. A tool-bearing
# request that omits max_tokens therefore defaults to the raised
# budget below. Plain chat keeps 2048: free-lane cost discipline is
# preserved where outputs are naturally short, and the budget is a
# ceiling, not a target — a turn that generates less is unaffected.
# (Raising DEFAULT_MAX_TOKENS globally was considered and rejected
# in the §7 fix proposal: it would lengthen every free-lane
# generation, runaway ones included.)
TOOL_DEFAULT_MAX_TOKENS = 8192
# v0.8.2 (F-11) stream resilience bounds. The upstream client carries
# one flat httpx read timeout (120 s): a stream that stalls mid-answer
# hangs for the full two minutes before the transport gives up, and
# the client-visible turn dies with it (2026-11-13: three consecutive
# exec-ops 502s at 121–152 s upstream latency). Streaming calls get
# two tighter, purpose-shaped bounds instead: how long the upstream
# may take to produce its FIRST line, and how long it may go silent
# BETWEEN lines once flowing. Both are enforced around the stream
# iterator, so the ADD-06 commit guard sees them exactly like a
# transport drop. Defaults are generous against every healthy lane's
# observed time-to-first-chunk (seconds, incl. queued free tiers) and
# exist to catch a wedged stream, not to discipline slow models.
STREAM_FIRST_CHUNK_TIMEOUT_S = 45.0
STREAM_STALL_TIMEOUT_S = 60.0
# v0.8.2 (F-11) entry cooldown after a streaming abort: the attempt
# ledger recorded failures in v0.8.0 but selection never read them
# (J-2 observe-only), so a client's automatic retry — a new request —
# re-picked the same stalling entry and aborted again. A stream abort
# now cools the entry for this long; selection and sticky reuse skip
# it (reason "cooldown_stream_abort") unless it is the only candidate.
# Bounded and self-healing by construction: an expiry timestamp in
# the quota store, no manual reset, no permanent state.
STREAM_ABORT_COOLDOWN_S = 600.0
MIN_OUTPUT_TOKENS = 64             # reserve used by the context-fit check
TOKEN_ESTIMATE_FACTOR = 1.05       # carried from v0.3 (o200k_base x1.05)

# ----------------------------------------------------------------------
# token estimation (tiktoken o200k_base, as v0.3; heuristic fallback)
# ----------------------------------------------------------------------
_ENCODING = None


def _encode_len(text: str) -> int:
    global _ENCODING
    try:
        import tiktoken
        if _ENCODING is None:
            _ENCODING = tiktoken.get_encoding("o200k_base")
        return len(_ENCODING.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def estimate_input_tokens(body: dict[str, Any]) -> int:
    total = 0
    for msg in body.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, str):
            total += _encode_len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += _encode_len(part["text"])
        for call in (msg.get("tool_calls") or []):
            fn = (call or {}).get("function") or {}
            total += _encode_len(str(fn.get("arguments") or ""))
        total += 4  # per-message framing overhead
    if body.get("tools"):
        total += _encode_len(json.dumps(body["tools"]))
    return int(total * TOKEN_ESTIMATE_FACTOR) + 1


# ----------------------------------------------------------------------
# configuration
# ----------------------------------------------------------------------
@dataclass
class GatewayConfig:
    state_dir: Path = Path(os.environ.get("SOVEREIGN_STATE_DIR", "./state"))
    registry_path: Optional[Path] = None      # default: state/registry.json or bundled
    clients_path: Optional[Path] = None       # default: state/clients.json
    sidecar_url: Optional[str] = os.environ.get("SOVEREIGN_SIDECAR_URL") or None
    # Sensitivity scan: Phase 0 log-only trial is available via this flag;
    # default False = matches change routing (post-Phase-0 behaviour).
    sensitivity_log_only: bool = os.environ.get("SOVEREIGN_SENSITIVITY_LOG_ONLY") == "1"
    # v0.10.10 PII detectors (SSN, payment card). Default OFF = log-only:
    # matches are recorded as a ``pii_log_only:<names>`` route signal and
    # do not change routing. "1" promotes a match to data_class=sensitive
    # (public lanes refused). Owner policy gate — see sensitivity.py.
    pii_detectors: bool = os.environ.get("SOVEREIGN_PII_DETECTORS") == "1"
    emergency_enabled: bool = os.environ.get("SOVEREIGN_EMERGENCY_SWITCH") == "1"
    emergency_daily_usd: float = float(os.environ.get("SOVEREIGN_EMERGENCY_DAILY_USD", "25"))
    # v0.5.7 (owner ruling 2026-11-09): warn-only emergency — alert at
    # the daily level, NEVER switch the paid lanes off.
    emergency_warn_only: bool = os.environ.get("SOVEREIGN_EMERGENCY_WARN_ONLY") == "1"
    # v0.8.2 (F-11) stream resilience knobs; defaults from the module
    # constants above, overridable via the SOVEREIGN_ env pattern.
    stream_first_chunk_timeout_s: float = float(
        os.environ.get("SOVEREIGN_STREAM_FIRST_CHUNK_TIMEOUT_S",
                       STREAM_FIRST_CHUNK_TIMEOUT_S))
    stream_stall_timeout_s: float = float(
        os.environ.get("SOVEREIGN_STREAM_STALL_TIMEOUT_S",
                       STREAM_STALL_TIMEOUT_S))
    stream_abort_cooldown_s: float = float(
        os.environ.get("SOVEREIGN_STREAM_ABORT_COOLDOWN_S",
                       STREAM_ABORT_COOLDOWN_S))
    # v0.10.5 (Job B): hard cap on upstream attempts per turn (all
    # passes together; the 72h census to 2026-11-27 saw depths of 21).
    max_upstream_attempts: int = int(
        os.environ.get("SOVEREIGN_MAX_UPSTREAM_ATTEMPTS",
                       MAX_UPSTREAM_ATTEMPTS))
    # v0.10.6 (Job C): per-class tiered ranking in SHADOW mode — logs
    # what the free > cheap > premium policy would pick next to the live
    # pick on every route row (``tier_shadow``). Never changes routing.
    # SOVEREIGN_TIER_SHADOW=0 turns the logging off.
    tier_shadow: bool = os.environ.get("SOVEREIGN_TIER_SHADOW", "1") != "0"
    provider_keys: dict[str, str] = field(default_factory=dict)  # tests/dev injection

    def resolved_registry_path(self) -> Path:
        if self.registry_path:
            return self.registry_path
        candidate = self.state_dir / "registry.json"
        return candidate if candidate.exists() else Path(__file__).with_name("registry.json")

    def resolved_clients_path(self) -> Path:
        if self.clients_path:
            return self.clients_path
        return self.state_dir / "clients.json"

    def load_provider_keys(self) -> dict[str, str]:
        """Read keys/<provider>.key (mode-600) from the state dir, once.
        Env <PROVIDER>_API_KEY is a local-dev convenience only."""
        keys = dict(self.provider_keys)
        key_dir = self.state_dir / "keys"
        if key_dir.is_dir():
            for f in key_dir.iterdir():
                if f.suffix == ".key" and f.is_file():
                    keys.setdefault(f.stem, f.read_text(encoding="utf-8").strip())
        for provider in ("groq", "openrouter", "deepinfra", "ollama", "openai", "anthropic"):
            env_val = os.environ.get(f"{provider.upper()}_API_KEY")
            if env_val and provider not in keys:
                keys[provider] = env_val
        return keys


# ----------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------
def _err(status: int, message: str, code: str,
         extra: Optional[dict[str, Any]] = None,
         headers: Optional[dict[str, str]] = None) -> JSONResponse:
    payload: dict[str, Any] = {"error": {"message": message, "type": code, "code": code}}
    if extra:
        payload["detail"] = extra
    return JSONResponse(payload, status_code=status, headers=headers or {})


_VALID_ROLES = frozenset({"system", "developer", "user", "assistant", "tool", "function"})
_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
_NUMERIC_FIELDS = ("temperature", "top_p", "presence_penalty", "frequency_penalty")
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _scrub_surrogates(value: Any) -> Any:
    if isinstance(value, str):
        return _SURROGATE_RE.sub("\ufffd", value) if _SURROGATE_RE.search(value) else value
    if isinstance(value, list):
        return [_scrub_surrogates(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_surrogates(v) for k, v in value.items()}
    return value


def _normalize_chat_body(body: dict[str, Any]) -> Optional[JSONResponse]:
    """v0.10.9 (ROUTER-STRESS-20261128): reject malformed shapes with a
    400 instead of letting them crash the handler into a bare 500.

    Found by the 2026-11-28 stress matrix: a non-object ``messages`` item
    (``["hi"]``, ``[null]``) raised AttributeError, a non-numeric
    ``max_tokens`` raised ValueError, and a negative ``max_tokens`` gave a
    negative cost estimate that made a PAID model look cheaper than every
    free one. Normalises in place: ``max_tokens``/``max_completion_tokens``
    become positive ints, ``stream`` becomes a real bool (the string
    "false" used to mean streaming on). Returns an error response or None.
    """
    for i, m in enumerate(body["messages"]):
        if not isinstance(m, dict):
            return _err(400, f"messages[{i}] must be an object", "invalid_request")
        role = m.get("role")
        if role not in _VALID_ROLES:
            return _err(400, f"messages[{i}].role must be one of "
                             f"{sorted(_VALID_ROLES)}", "invalid_request")
        content = m.get("content")
        if content is None:
            if role in ("user", "system", "developer"):
                return _err(400, f"messages[{i}].content is required for role "
                                 f"{role}", "invalid_request")
        elif isinstance(content, list):
            if not all(isinstance(part, dict) for part in content):
                return _err(400, f"messages[{i}].content parts must be objects",
                            "invalid_request")
        elif not isinstance(content, str):
            return _err(400, f"messages[{i}].content must be a string, a list "
                             "of parts, or null", "invalid_request")
    # Sampling knobs: a wrong type here 400s at EVERY upstream, and the
    # failover used to walk the whole fleet — paid spillway and even the
    # premium last resort (whose temperature guard strips the field) —
    # before answering. Reject once, here, for free.
    for field in _NUMERIC_FIELDS:
        v = body.get(field)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return _err(400, f"{field} must be a number", "invalid_request")
    tools = body.get("tools")
    if tools is not None:
        if not isinstance(tools, list) or not all(
                isinstance(t, dict) and isinstance(t.get("function"), dict)
                and isinstance(t["function"].get("name"), str)
                for t in tools):
            return _err(400, "tools must be a list of {type: function, function: "
                             "{name, ...}} objects", "invalid_request")
    rf = body.get("response_format")
    if rf is not None and not isinstance(rf, dict):
        return _err(400, "response_format must be an object", "invalid_request")
    # reasoning_effort: an unknown value ("bogus", 7) 400s at most
    # upstreams and walked the failover onto paid (live 2026-11-28:
    # "bogus" was served by mistral/ministral-8b). The field is a hint,
    # not a contract, so an unknown value is dropped (provider default)
    # rather than refused; known values are case-normalised.
    if "reasoning_effort" in body:
        effort = body["reasoning_effort"]
        norm = effort.strip().lower() if isinstance(effort, str) else None
        if norm in _REASONING_EFFORTS:
            body["reasoning_effort"] = norm
        else:
            body.pop("reasoning_effort")
    # A lone UTF-16 surrogate ("\\ud800" in the JSON) is legal JSON but
    # not encodable UTF-8: it crashed the sticky hash and every upstream
    # call (httpx encodes UTF-8). Replace with U+FFFD, as browsers do.
    body["messages"] = _scrub_surrogates(body["messages"])
    for field in ("max_tokens", "max_completion_tokens"):
        if field not in body or body[field] is None:
            continue
        v = body[field]
        try:
            if isinstance(v, bool):
                raise ValueError
            n = int(v) if isinstance(v, (int, float)) else int(str(v).strip())
        except (TypeError, ValueError, OverflowError):
            return _err(400, f"{field} must be a positive integer", "invalid_request")
        if n < 1:
            return _err(400, f"{field} must be a positive integer", "invalid_request")
        body[field] = n
    if "stream" in body and not isinstance(body["stream"], bool):
        sv = body["stream"]
        body["stream"] = sv.strip().lower() == "true" if isinstance(sv, str) else bool(sv)
    return None


def _message_text(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content
                         if isinstance(p, dict) and p.get("type", "text") == "text"
                         and isinstance(p.get("text"), str))
    return ""


def _has_images(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                    return True
    return False


def _route_headers(entry: ModelEntry, level: int, task_class: str,
                   cost_estimate: float, escalated: bool) -> dict[str, str]:
    return {
        "X-Sovereign-Model": entry.id,
        "X-Sovereign-Level": str(level),
        "X-Sovereign-Task": task_class,
        "X-Sovereign-Lane": entry.lane,
        "X-Sovereign-Cost-Estimate": f"{cost_estimate:.6f}",
        "X-Sovereign-Escalated": "true" if escalated else "false",
    }


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cached_tokens_from_usage(usage: dict[str, Any]) -> int:
    """Cached-input token count from an upstream usage object, across
    the shapes the fleet's providers actually return (v0.8.3 — the
    true-cost ledger fix; before it, only the first two shapes were
    read and only on the non-streaming path, so route rows carried
    cached_tokens=0 on cache-served turns and every cache-heavy turn
    was priced as a full-price miss):

      * OpenAI-style: ``prompt_tokens_details.cached_tokens`` —
        OpenAI, OpenRouter-normalised, Groq, DeepInfra, SiliconFlow,
        Novita, Z.ai (the OpenAI-compatible majority).
      * DeepSeek: ``prompt_cache_hit_tokens`` (its complement,
        ``prompt_cache_miss_tokens``, sums with it to prompt_tokens;
        the hit count is the cached count).
      * Anthropic-style: ``cache_read_input_tokens`` (Anthropic models
        reached via aggregators; see _usage_counts for the native
        shape, where input_tokens excludes cache reads).
      * Google: ``cachedContentTokenCount`` — inside a nested
        ``usageMetadata`` object or at the usage level (Gemini-native
        shape flowing through a pass-through).

    Aggregators sometimes report the same cached tokens under two of
    these names at once (e.g. OpenRouter sets both the OpenAI details
    field and the Anthropic field for Claude routes), so the MAX of
    the recognised fields is the count — never their sum. Absent or
    unrecognised usage -> 0, exactly the pre-v0.8.3 behaviour; a
    malformed usage object must never break a response.
    """
    if not isinstance(usage, dict):
        return 0
    candidates: list[int] = []
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        candidates.append(_as_int(details.get("cached_tokens")))
    candidates.append(_as_int(usage.get("prompt_cache_hit_tokens")))
    candidates.append(_as_int(usage.get("cache_read_input_tokens")))
    meta = usage.get("usageMetadata")
    if isinstance(meta, dict):
        candidates.append(_as_int(meta.get("cachedContentTokenCount")))
    candidates.append(_as_int(usage.get("cachedContentTokenCount")))
    return max(candidates, default=0)


def _usage_counts(usage: Any, est_input: int) -> tuple[int, int, int]:
    """(input, output, cached) token counts from a usage object in any
    supported shape. ``cached`` is always a subset of ``input`` in the
    returned triple — the invariant estimate_cost prices against:

      * OpenAI/DeepSeek shapes: prompt_tokens already includes the
        cached count.
      * Google native shape (usageMetadata): promptTokenCount
        includes cachedContentTokenCount.
      * Anthropic native shape: input_tokens EXCLUDES cache reads, so
        reads (and cache creations, which the registry prices as
        fresh input — it carries no cache-write rate) are folded
        into the input total to restore the subset invariant.
    """
    if not isinstance(usage, dict):
        return est_input, 0, 0
    meta = usage.get("usageMetadata")
    if not isinstance(meta, dict):
        meta = {}
    cached = _cached_tokens_from_usage(usage)
    in_tok = _as_int(usage.get("prompt_tokens")) \
        or _as_int(usage.get("promptTokenCount")) \
        or _as_int(meta.get("promptTokenCount"))
    if not in_tok:
        native_in = _as_int(usage.get("input_tokens"))
        if native_in:
            in_tok = native_in + cached \
                + _as_int(usage.get("cache_creation_input_tokens"))
        else:
            in_tok = est_input
    out_tok = _as_int(usage.get("completion_tokens")) \
        or _as_int(usage.get("candidatesTokenCount")) \
        or _as_int(meta.get("candidatesTokenCount")) \
        or _as_int(usage.get("output_tokens"))
    if in_tok:
        cached = min(cached, in_tok)
    return in_tok, out_tok, cached


def _usage_from_response(resp: dict[str, Any], est_input: int,
                         entry: ModelEntry) -> tuple[int, int, int, float]:
    usage = resp.get("usage") or resp.get("usageMetadata") or {}
    in_tok, out_tok, cached = _usage_counts(usage, est_input)
    if out_tok == 0:
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        if isinstance(msg.get("content"), str):
            out_tok = _encode_len(msg["content"])
    return in_tok, out_tok, cached, entry.estimate_cost(in_tok, out_tok, cached)


def _attempt_usage_extras(
        usage: Any) -> tuple[Optional[float], Optional[int]]:
    """(provider_cost_usd, reasoning_tokens) an upstream reported for
    one attempt — the borrowed per-attempt contract (LiteLLM hook
    shape, 2026-11-24; J-3(i) / R3 telemetry).

    Provider-reported cost is honoured ONLY where the upstream sends
    it in the usage object (OpenRouter's ``cost`` field is the live
    example); it is captured verbatim for the ledger and NEVER used
    to reprice the turn — the registry-priced estimate stays the
    cost of record. Reasoning tokens come from the OpenAI/DeepSeek
    ``completion_tokens_details.reasoning_tokens`` shape. Absent or
    malformed usage yields (None, None): a missing report is a NULL
    in the ledger, never a zero and never an error.
    """
    if not isinstance(usage, dict):
        return None, None
    cost: Optional[float] = None
    raw_cost = usage.get("cost")
    if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool):
        cost = float(raw_cost)
    reasoning: Optional[int] = None
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        reasoning = _as_int(details.get("reasoning_tokens")) or None
    return cost, reasoning


# ======================================================================
# app factory (tests build isolated apps through this)
# ======================================================================
# ----------------------------------------------------------------------
# dead-lane alerts (v0.5.7, owner ruling 2026-11-09)
# ----------------------------------------------------------------------
# Account-class upstream failures (401/402/403) and daily token caps
# mean a rung is DEAD, not rate-limited — the owner wants a warning
# the moment it happens, while the cascade keeps serving from the
# next rungs. One alert per model per day (the store dedups on the
# key), delivered by the existing alert chain.
DEAD_LANE_STATUSES = {401, 402, 403}


def note_lane_dead(app: FastAPI, entry: ModelEntry, cause: str,
                   dedup_cause: Optional[str] = None) -> None:
    # The dedup key carries `dedup_cause` when given: the F-17(a)
    # TPD cause embeds live numbers (counted usage, the triggering
    # estimate), and keying on them would mint a fresh "first alert
    # of the day" for every distinct request — the alert is once per
    # entry per day by design, so its key stays on the stable class.
    app.state.store.add_alert(
        "lane_dead", entry.provider,
        f"lane_dead:{entry.provider}:{entry.id}:{dedup_cause or cause}",
        f"Lane dead: {entry.id} (provider {entry.provider}) — "
        f"{cause}. This is an account/credit/cap failure, not a "
        "rate limit. The cascade is serving from the next rungs "
        "in cost order; fix or top up this provider to restore "
        "the cheap rung.")


def note_sensitive_paid_serve(app: FastAPI, record: RouteRecord,
                              client: ClientPolicy, entry: ModelEntry,
                              cost: float) -> None:
    """v0.9.1 (F-15): sensitive_no_cleared_provider fires on OUTCOMES,
    not on the clearance list's emptiness. Pre-v0.9.1 the alert fired
    pre-serve whenever no owner-cleared candidate sat in the free-first
    set — a permanently-true condition (no provider is cleared), so it
    paged on the first sensitive request of every day while describing
    a paid fallthrough that usually never happened. Now it records
    only the two outcomes that matter: a sensitive request actually
    serving on a paid/premium lane (here), or finding no candidate at
    all (the chat handler's 429 path). The standing posture — who
    served sensitive traffic, and that the cleared set is empty — is
    the governor's daily sensitive_posture_digest."""
    if record.data_class != "sensitive" or entry.lane not in ("paid", "premium"):
        return
    app.state.store.add_alert(
        "sensitive_no_cleared_provider", client.id,
        f"sensitive_paid_serve:{client.id}",
        f"Client {client.id}: sensitive request SERVED on the "
        f"{entry.lane} lane via {entry.id} (${cost:.4f}) — no "
        "owner-cleared provider exists, so the v0.5.5 spillway served "
        "it uncleared as a last resort. (F-15: this alert fires on "
        "the outcome, not on the empty clearance list.)")


def note_stream_abort(app: FastAPI, entry: ModelEntry) -> None:
    """v0.8.2 (F-11): a streaming abort cools the entry in the quota
    store. Selection (build_candidates) and, through it, sticky reuse
    skip a cooling entry with reason ``cooldown_stream_abort`` —
    except when it is the only candidate, where it still serves (a
    cooldown must never manufacture a zero-candidate 429). The
    cooldown is a bounded failure memory for the ONE failure class
    where the client-visible damage is proven (a client's retry
    re-picking a stalling entry); it is not general demotion
    enforcement — J-2's observe-only posture stands for every other
    class, whose would_demote annotations remain advisory."""
    try:
        app.state.store.set_stream_cooldown(
            entry.id, time.time() + app.state.cfg.stream_abort_cooldown_s)
    except Exception:  # noqa: BLE001 — memory must not break serving
        pass


def create_app(config: Optional[GatewayConfig] = None,
               upstream_transport: Optional[httpx.BaseTransport] = None,
               sidecar_transport: Optional[httpx.BaseTransport] = None) -> FastAPI:
    cfg = config or GatewayConfig()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    registry = load_registry(cfg.resolved_registry_path())
    clients = load_clients(cfg.resolved_clients_path())
    store = QuotaStore(cfg.state_dir / "quota.db")
    governor = Governor(store, emergency=EmergencyConfig(
        enabled=cfg.emergency_enabled, daily_limit_usd=cfg.emergency_daily_usd,
        warn_only=cfg.emergency_warn_only))
    sticky = StickyStore()
    telemetry = Telemetry(cfg.state_dir / "routes.jsonl")
    provider_keys = cfg.load_provider_keys()

    # trust_env=False: provider traffic (carrying API keys) must go direct,
    # never through a proxy picked up from ambient environment variables,
    # and a malformed proxy env var must not be able to kill the gateway.
    upstream_client = httpx.AsyncClient(
        transport=upstream_transport, timeout=httpx.Timeout(120.0, connect=10.0),
        trust_env=False)
    sidecar_client = httpx.AsyncClient(transport=sidecar_transport,
                                       trust_env=False) \
        if sidecar_transport else None
    classifier = Classifier(sidecar_url=cfg.sidecar_url, http_client=sidecar_client)

    app = FastAPI(title="Sovereign Router", version=VERSION)
    app.state.cfg = cfg
    app.state.registry = registry
    app.state.clients = clients
    app.state.store = store
    app.state.governor = governor
    app.state.sticky = sticky
    app.state.telemetry = telemetry
    # v0.10.2 (owner ruling 2026-11-27): production tool-error rates
    # feed tool-bearing selection. The tracker reads the quota store's
    # tool_outcomes ledger (written by the chat handler's judging
    # hook); build_candidates demotes flagged entries within their
    # cost tier. See toolstats.py for gates and basis.
    app.state.toolstats = ToolErrorTracker(store)
    app.state.tier_tool_stats = tiering.ToolStatsCache(app.state.toolstats)
    app.state.classifier = classifier
    app.state.provider_keys = provider_keys
    app.state.http = upstream_client
    # v0.8.0 (aspect E): observe-only demotion tracker. Selection NEVER
    # reads it in this release; it exists so the attempt ledger and the
    # route records carry what enforcement WOULD do (decision J-2).
    app.state.demotions = DemotionTracker()

    # ------------------------------------------------------------------
    def provider_alert_levels(reg: Registry) -> dict[str, float]:
        levels: dict[str, float] = {}
        for e in reg.entries:
            if e.spend_alert_usd is not None:
                levels[e.provider] = max(levels.get(e.provider, 0.0), e.spend_alert_usd)
        return levels

    def auth_client(request: Request) -> Optional[ClientPolicy]:
        key = extract_bearer_key(request.headers.get("authorization"),
                                 request.headers.get("x-api-key"))
        return app.state.clients.authenticate(key)

    def auth_admin(request: Request) -> bool:
        key = extract_bearer_key(request.headers.get("authorization"),
                                 request.headers.get("x-api-key"))
        return app.state.clients.is_admin(key)

    # ------------------------------------------------------------------
    # candidate selection
    # ------------------------------------------------------------------
    def build_candidates(
        *, client: ClientPolicy, level: int, task_class: str,
        required_quality: int, est_tokens: int, max_tokens: int,
        has_tools: bool, data_class: str,
        premium_permitted: bool, paid_off_reason: str,
        premium_only: bool = False,
        spillway: bool = False,
    ) -> tuple[list[ModelEntry], list[dict[str, str]]]:
        skipped: list[dict[str, str]] = []
        candidates: list[ModelEntry] = []
        cooled: list[ModelEntry] = []
        now = time.time()
        for entry in app.state.registry.entries:
            def skip(reason: str) -> None:
                skipped.append({"model": entry.id, "reason": reason})

            if not entry.enabled:
                skip("disabled"); continue
            if data_class == "sensitive" and entry.spillway_sensitive_excluded:
                # Per-model sensitive exclusion (v0.5.6): the owner's
                # spillway ruling relaxes the clearance wall for the
                # paid pool in general, but a flagged provider's data
                # posture was never cleared for sensitive traffic
                # (SiliconFlow — probe 2026-11-07), so a flagged entry
                # is not a candidate for a sensitive request in ANY
                # pass, this one included. The check sits outside the
                # `not spillway` wall below on purpose: the spillway
                # must not relax it. Non-sensitive requests never see
                # this branch; the entry keeps its cost-order place.
                skip("sensitive_spillway_excluded"); continue
            if premium_only and entry.lane != "premium":
                # Explicit premium request (alias): only the premium lane
                # may serve it — falling back to a paid model would
                # silently change what the client asked for.
                skip("explicit_premium_only"); continue
            if spillway:
                # Spillway pass (v0.5.5): paid + premium entries only.
                # Free entries were the first pass's business — their
                # skip reasons are already on the route row; re-listing
                # them here would only duplicate it. The lane list and
                # the per-client premium gate do not apply in this pass
                # (uniform last resort, owner ruling 2026-11-07).
                if entry.lane not in ("paid", "premium"):
                    continue
            elif entry.lane == "premium":
                if not premium_permitted:
                    skip("premium_not_allowed"); continue
            elif not client.lane_allowed(entry.lane):
                skip("lane_not_allowed"); continue
            if paid_off_reason and entry.lane in ("paid", "premium"):
                skip(paid_off_reason); continue
            if not app.state.provider_keys.get(entry.provider):
                # No usable key loaded for this provider (missing or
                # empty keys/<provider>.key): the provider is
                # unavailable, full stop. Skipping at selection time
                # keeps a keyless provider from burning a failover
                # attempt on an unauthenticated upstream call, and a
                # request can never fail *because* a provider is
                # keyless — the next candidate simply serves.
                skip("provider_key_missing"); continue
            if data_class == "sensitive" and not spillway:
                cleared = entry.sensitive_cleared or \
                    entry.provider in client.sensitive_cleared_providers
                if entry.lane == "public-only-free":
                    skip("sensitive_public_lane"); continue
                if not cleared and entry.lane != "private-free":
                    # Uncleared paid/premium may not receive secrets; the
                    # private-free lane is the spec's fallback (strongest
                    # verified no-retention terms) and stays eligible.
                    # (In the spillway pass this wall is relaxed by the
                    # owner's 2026-11-07 ruling — see
                    # build_spillway_candidates.)
                    skip("sensitive_not_cleared"); continue
            if has_tools and not entry.supports_tools:
                skip("tools_unsupported"); continue
            if _selection_quality(entry, task_class, has_tools) \
                    < required_quality:
                skip("quality_below_required"); continue
            # Fit = the *input* fits; max_tokens is clamped to the
            # model's remaining room at call time, so only a minimal
            # output reserve (64 tokens) is required here.
            if est_tokens + MIN_OUTPUT_TOKENS > entry.context_tokens:
                skip("context_too_small"); continue
            if not entry.is_verified(now):
                skip("unverified_expired"); continue
            reason = app.state.store.check_provider_limits(
                entry.provider, entry.limits, est_tokens, now)
            if reason:
                if reason == "rate_limited_tpd":
                    # F-17(a) (v0.9.1): the cause must state what was
                    # actually consumed vs the cap AND the estimate
                    # that tripped it. The pre-v0.9.1 string asserted
                    # "exhausted" even when usage was zero and a single
                    # oversized request estimate alone exceeded the cap
                    # (Aion, 2026-11-15: 0 / 20,000 used, estimate
                    # 67,274).
                    cap = int(entry.limits.get("tpd") or 0)
                    day_start = time.mktime(time.strptime(
                        time.strftime("%Y-%m-%d", time.localtime(now)),
                        "%Y-%m-%d"))
                    _, counted = app.state.store.window_usage(
                        entry.provider, day_start, now + 1)
                    note_lane_dead(
                        app, entry,
                        f"daily token cap (TPD): {counted:,} / {cap:,} "
                        f"tokens used today; this request's estimate "
                        f"{est_tokens:,} tokens exceeds the cap",
                        dedup_cause="daily token cap (TPD)")
                skip(reason); continue
            # v0.8.2 (F-11): stream-abort cooldown. Checked last, so a
            # cooled entry in the list is one that passed EVERY other
            # gate — which is what makes the only-candidate restore
            # below sound: restoring it recreates no other violation.
            cooldown_until = app.state.store.stream_cooldown_until(entry.id)
            if cooldown_until is not None and cooldown_until > now:
                cooled.append(entry)
                skip("cooldown_stream_abort"); continue
            candidates.append(entry)

        # v0.10.2 (owner ruling 2026-11-27): production tool-error
        # demotion, tool-bearing requests only. A flagged entry (see
        # toolstats.py: >=25% production tool-error rate on >=50
        # judged turns, rolling 72h) is reordered behind every
        # non-flagged entry IN ITS COST TIER — the sort key gains a
        # tier bucket and a flag bit ahead of cost — so free-first is
        # absolute (a flagged free entry still precedes every paid
        # candidate; demotion can never push a turn to paid while a
        # non-flagged free entry exists) and the entry is never
        # removed: it remains in the list, reachable by failover and
        # by pin, and still serves when it is all that is left.
        # Non-tool requests take the exact pre-v0.10.2 key.
        tool_flagged = app.state.toolstats.flagged() if has_tools \
            else set()

        def sort_key(e: ModelEntry) -> tuple:
            est_out = max_tokens // 2
            cost = e.estimate_cost(est_tokens, est_out)
            quality = -_selection_quality(e, task_class, has_tools)
            latency = e.latency_p50_ms or 99_999
            if tool_flagged:
                return (0 if e.lane in FREE_LANES else 1,
                        1 if e.id in tool_flagged else 0,
                        cost, quality, latency)
            return (cost, quality, latency)

        candidates.sort(key=sort_key)
        if not candidates and cooled:
            # Only-candidate exception: every otherwise-eligible entry
            # is cooling. Serve from the cooled set rather than
            # manufacturing a zero-candidate 429 — a possibly-stalling
            # model beats a certainly-failed turn, and its next abort
            # simply refreshes the cooldown. The route row shows both
            # the cooldown skip and this bypass annotation.
            candidates = sorted(cooled, key=sort_key)
            for e in candidates:
                skipped.append({"model": e.id,
                                "reason": "cooldown_bypassed_only_candidate"})
        if tool_flagged:
            for e in candidates:
                if e.id in tool_flagged:
                    skipped.append({"model": e.id,
                                    "reason": "tool_error_rate_demoted"})
        return candidates, skipped

    def build_spillway_candidates(
        *, client: ClientPolicy, level: int, task_class: str,
        required_quality: int, est_tokens: int, max_tokens: int,
        has_tools: bool, data_class: str, paid_off_reason: str,
        exclude_ids: frozenset = frozenset(),
        premium_permitted: bool = False,
    ) -> tuple[list[ModelEntry], list[dict[str, str]]]:
        """Free-first / paid-spillway pass (v0.5.5, owner ruling
        2026-11-07: "allow paid models when we burn up the free — but
        the priority should be free processing FIRST", sensitive turns
        included, cheapest paid first).

        Runs ONLY when normal free-first selection produced ZERO
        eligible candidates, or every one of them failed upstream: the
        turn would otherwise be a hard 429. Selection re-runs over the
        paid + premium entries in the gateway's normal cost order
        (cheapest paid first; premium/gpt-5.5 sorts last by price —
        which is where the v0.5.4 rescue pin's effect now lives; the
        pin flag and the v0.5.3 rescue path are gone, this is the one
        mechanism).

        Origin: the 2026-11-06 Hermes dead end (36x 429 in a day). An
        hourly email-triage turn classified sensitive + L4 + infra at
        ~45k tokens had exactly one free candidate, TPM-walled at
        8k/min by size alone; paid was barred by the sensitive wall and
        premium by the client gate, and the v0.5.3 rescue deliberately
        excluded hermes — a policy dead end, not a defect.

        Relaxed in this pass ONLY:
          * the sensitive provider-clearance wall — the owner cleared
            paid providers to receive sensitive turns as the spillway
            of last resort;
          * the per-client premium gate and lane list — the pass is a
            uniform last resort for all three clients. Explicit
            premium requests and explicit model pins never reach it
            (callers gate on alias-based auto-selection), so it is not
            a premium unlock.
        Still binding: enabled/verified entries, tools support, the
        quality floor (with the same level-5 -> quality-4 fallback as
        normal selection), context fit, provider rate limits
        (TPM/RPM/RPD/TPD), per-model sensitive exclusions
        (``spillway_sensitive_excluded``, v0.5.6 — a flagged provider
        is out for sensitive turns even here), provider key presence,
        and the governor's emergency paid-lane
        switch, which vetoes the pass outright (its reason lands in
        the skip list via build_candidates). Governor steering
        (above-target quality delta is already inside required_quality;
        the cheapest-quality-4 narrowing and premium-explicit-only
        rules) shapes normal selection only — a last-resort pass that
        recreated the dead end above target would defeat its purpose.

        ``exclude_ids`` drops entries already attempted upstream this
        turn (execution-time engagement after failover exhaustion).
        """
        candidates, skipped = build_candidates(
            client=client, level=level, task_class=task_class,
            required_quality=required_quality, est_tokens=est_tokens,
            max_tokens=max_tokens, has_tools=has_tools,
            data_class=data_class, premium_permitted=True,
            paid_off_reason=paid_off_reason, spillway=True)
        if not candidates and level == 5 and required_quality >= 5:
            # Mirror normal selection's level-5 fallback: quality 4
            # may serve a level-5 turn rather than failing it.
            candidates2, skipped2 = build_candidates(
                client=client, level=level, task_class=task_class,
                required_quality=4, est_tokens=est_tokens,
                max_tokens=max_tokens, has_tools=has_tools,
                data_class=data_class, premium_permitted=True,
                paid_off_reason=paid_off_reason, spillway=True)
            if candidates2:
                candidates = candidates2
                skipped = skipped + skipped2 + [
                    {"model": "-", "reason": "level5_fallback_quality4"}]
        floor = required_quality
        if any(sk.get("reason") == "level5_fallback_quality4" for sk in skipped):
            floor = 4
        if exclude_ids:
            candidates = [c for c in candidates if c.id not in exclude_ids]
        # v0.10.5 (Job B, owner go 2026-11-27 8:25 PM MT): never
        # escalate to premium just because the free pools are
        # congested. Premium stays in this pass only when the request
        # itself requires it (premium_permitted: client allows premium
        # AND level 5 or an explicit premium ask), or when NO paid entry
        # can meet the quality floor at all — a capability requirement,
        # not a congestion one. "Can meet" is judged on capability
        # (enabled, key, tools, quality, context, sensitive exclusion,
        # emergency veto), not on momentary rate limits or failures, so
        # a busy-but-capable paid pool keeps premium out.
        if not premium_permitted and any(
                c.lane == "premium" for c in candidates):
            if _paid_capable(task_class=task_class, floor=floor,
                             est_tokens=est_tokens, has_tools=has_tools,
                             data_class=data_class,
                             paid_off_reason=paid_off_reason):
                for c in candidates:
                    if c.lane == "premium":
                        skipped.append({"model": c.id,
                                        "reason": PREMIUM_NOT_REQUIRED_REASON})
                candidates = [c for c in candidates if c.lane != "premium"]
        return candidates, skipped

    def _paid_capable(*, task_class: str, floor: int, est_tokens: int,
                      has_tools: bool, data_class: str,
                      paid_off_reason: str) -> bool:
        if paid_off_reason:
            return False
        for e in app.state.registry.entries:
            if e.lane != "paid" or not e.enabled:
                continue
            if data_class == "sensitive" and e.spillway_sensitive_excluded:
                continue
            if not app.state.provider_keys.get(e.provider):
                continue
            if has_tools and not e.supports_tools:
                continue
            if _selection_quality(e, task_class, has_tools) < floor:
                continue
            if est_tokens + MIN_OUTPUT_TOKENS > e.context_tokens:
                continue
            return True
        return False

    # ------------------------------------------------------------------
    # explicit model pins (Cycle 1 aspect G; ruling J-1, 2026-11-12)
    # ------------------------------------------------------------------
    def pin_safety_reason(entry: ModelEntry, client: ClientPolicy,
                          data_class: str) -> Optional[str]:
        """The two hard boundaries a pin may NOT override (J-1): the
        entry must be enabled, and a sensitive request's pin must satisfy
        the same sensitive wall normal selection applies — the per-model
        exclusion, the public-lane bar, and provider clearance.
        Everything else about selection (quality floor, cost order,
        context fit, provider limits) stays pin-overridable: that is
        what makes pins useful for testing and for the voice stack.
        Returns the build_candidates-style skip reason, or None."""
        if not entry.enabled:
            return "disabled"
        if data_class == "sensitive":
            if entry.spillway_sensitive_excluded:
                return "sensitive_spillway_excluded"
            if entry.lane == "public-only-free":
                return "sensitive_public_lane"
            cleared = entry.sensitive_cleared or \
                entry.provider in client.sensitive_cleared_providers
            if not cleared and entry.lane != "private-free":
                return "sensitive_not_cleared"
        return None

    def apply_pin(candidates: list[ModelEntry],
                  explicit_entry: Optional[ModelEntry],
                  client: ClientPolicy, data_class: str,
                  paid_off_reason: str
                  ) -> tuple[list[ModelEntry], Optional[dict[str, str]]]:
        """Move a client's explicit model pin to the front of the
        candidate list, force-inserting it when selection left it out
        for an overridable reason only. Live and shadow selection share
        this one implementation so /v1/shadow predicts the live pin
        outcome exactly (the pre-v0.7.0 shadow only reordered a pin
        already in candidates and diverged on every force-insert case).

        Returns (candidates, blocked) where blocked is a skip annotation
        when a J-1 hard boundary stopped the pin, else None. Blocks for
        non-safety reasons (lane membership, governor paid-off, missing
        provider key) stay silent here — build_candidates already put
        those reasons on the route row."""
        if explicit_entry is None:
            return candidates, None
        if explicit_entry in candidates:
            candidates.remove(explicit_entry)
            candidates.insert(0, explicit_entry)
            return candidates, None
        reason = pin_safety_reason(explicit_entry, client, data_class)
        if reason is not None:
            return candidates, {"model": explicit_entry.id,
                                "reason": f"pin_blocked:{reason}"}
        if explicit_entry.lane in client.lanes and not (
                paid_off_reason and explicit_entry.lane in ("paid", "premium")) \
                and app.state.provider_keys.get(explicit_entry.provider):
            candidates.insert(0, explicit_entry)
        return candidates, None

    # ------------------------------------------------------------------
    # upstream call helpers
    # ------------------------------------------------------------------
    def upstream_body(body: dict[str, Any], entry: ModelEntry,
                      est_tokens: int, stream: bool,
                      max_tokens_override: Optional[int] = None) -> dict[str, Any]:
        out: dict[str, Any] = {k: v for k, v in body.items() if k in ALLOWED_FIELDS}
        out["model"] = entry.model
        out["stream"] = stream
        room = entry.context_tokens - est_tokens
        requested = int(max_tokens_override or body.get("max_tokens")
                        or body.get("max_completion_tokens")
                        or _default_max_tokens(body))
        budget = max(64, min(requested, room)) if room > 64 else requested
        if entry.completion_tokens_param:
            # OpenAI's gpt-5.5 rejects max_tokens outright ("Unsupported
            # parameter: 'max_tokens'... Use 'max_completion_tokens'
            # instead") — live-diagnosed 2026-11-03, when every premium
            # call upstream-400'd and surfaced as a 429. Entries flagged
            # completion_tokens_param get the budget under the name the
            # upstream accepts.
            out.pop("max_tokens", None)
            out["max_completion_tokens"] = budget
        else:
            out["max_tokens"] = budget
        # extra_body: provider-specific fields (e.g. OpenRouter
        # data_collection=deny) merged one level deep.
        for k, v in entry.extra_body.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                merged = dict(out[k]); merged.update(v); out[k] = merged
            else:
                out[k] = v
        if _reasoning_effort_stripped(entry):
            # Poison-field guard (v0.6.1): this upstream 400s on a
            # top-level reasoning_effort; strip it no matter whether
            # the client or the entry's extra_body put it there.
            out.pop("reasoning_effort", None)
        # Value shaping (v0.10.3): two last-resort upstreams 400 on
        # specific reasoning_effort VALUES rather than the field
        # itself; rewrite to the value their own error text names.
        _shape_reasoning_effort(entry, out)
        if _temperature_stripped(entry):
            # Temperature guard (v0.10.3): this upstream 400s on any
            # non-default temperature; omit the field so the upstream
            # default applies, no matter what the client sent.
            out.pop("temperature", None)
        if _needs_thought_signature_bypass(entry):
            # Gemini 3 signature validation (v0.10.1): tool calls the
            # conversation inherited from other models carry no Google
            # thought_signature and hard-400 on replay; attach the
            # documented bypass value where a signature is absent.
            out["messages"] = _with_thought_signature_bypass(
                out.get("messages"))
        if _needs_reasoning_content_pad(entry) and _tool_shaped(out):
            # DeepSeek thinking-mode pass-back (v0.10.2): assistant
            # history without reasoning_content hard-400s on
            # tool-shaped requests; pad the empty string where a real
            # value is absent (DeepSeek's documented requirement,
            # Hermes' shipped fix shape).
            out["messages"] = _with_reasoning_content_pad(
                out.get("messages"))
        if stream:
            opts = dict(out.get("stream_options") or {})
            opts["include_usage"] = True
            out["stream_options"] = opts
        return out

    def upstream_headers(entry: ModelEntry) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = app.state.provider_keys.get(entry.provider)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def call_upstream(entry: ModelEntry, payload: dict[str, Any]) -> httpx.Response:
        url = f"{entry.effective_base_url}/chat/completions"
        return await app.state.http.post(url, json=payload,
                                         headers=upstream_headers(entry))

    # ------------------------------------------------------------------
    # routes: meta
    # ------------------------------------------------------------------
    @app.get("/health")
    async def health() -> dict[str, Any]:
        now = time.time()
        last_poll = app.state.store.last_poll_ts()
        since_poll = None if last_poll is None else now - last_poll
        alert_chain_ok = since_poll is not None and since_poll <= 1800
        paid_off, _ = app.state.governor.paid_lanes_off(now)
        return {
            "status": "ok",
            "version": VERSION,
            # Spec: /health flags the alert chain degraded when no poll
            # has arrived for 30 minutes (n8n polls /admin/costs).
            "alert_chain": {
                "status": "ok" if alert_chain_ok else "degraded",
                "last_poll_ts": last_poll,
                "seconds_since_poll": since_poll,
            },
            "classifier_sidecar": await app.state.classifier.health(),
            "registry_models": len(app.state.registry.entries),
            "clients": sorted(app.state.clients.clients),
            "emergency_paid_lane_switch": {
                "enabled": app.state.governor.emergency.enabled,
                "daily_limit_usd": app.state.governor.emergency.daily_limit_usd,
                "warn_only": app.state.governor.emergency.warn_only,
                "tripped": paid_off,
            },
            "sensitivity_log_only": cfg.sensitivity_log_only,
        }

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        data = [{"id": alias, "object": "model", "owned_by": "sovereign-router"}
                for alias in ALIASES]
        data += [{"id": e.id, "object": "model", "owned_by": e.provider}
                 for e in app.state.registry.entries if e.enabled]
        return {"object": "list", "data": data}

    @app.get("/v1/quota")
    async def quota(request: Request) -> Any:
        client = auth_client(request)
        if client is None:
            return _err(401, "invalid API key", "authentication_error")
        now = time.time()
        day0 = time.mktime(time.strptime(
            time.strftime("%Y-%m-%d", time.localtime(now)), "%Y-%m-%d"))
        providers: dict[str, Any] = {}
        for entry in app.state.registry.entries:
            n, t = app.state.store.window_usage(entry.provider, day0, now + 1)
            providers[entry.provider] = {
                "requests_today": n, "tokens_today": t,
                "limits": entry.limits,
                "spend_alert_usd": entry.spend_alert_usd,
            }
        return {
            "client": client.id,
            "spent_today_usd": app.state.governor.client_spent_today(client.id, now),
            "daily_target_usd": client.daily_target_usd,
            "providers": providers,
        }

    @app.get("/v1/usage")
    async def usage(request: Request) -> Any:
        """Usage scoped to the caller's key — a client only ever sees
        its own usage."""
        client = auth_client(request)
        if client is None:
            return _err(401, "invalid API key", "authentication_error")
        now = time.time()
        day0 = time.mktime(time.strptime(
            time.strftime("%Y-%m-%d", time.localtime(now)), "%Y-%m-%d"))
        month0 = time.mktime(time.strptime(
            time.strftime("%Y-%m-01", time.localtime(now)), "%Y-%m-%d"))
        return {
            "today": app.state.store.client_usage(client.id, day0, now + 1),
            "month": app.state.store.client_usage(client.id, month0, now + 1),
        }

    # ------------------------------------------------------------------
    # routes: admin
    # ------------------------------------------------------------------
    @app.post("/admin/reload")
    async def admin_reload(request: Request) -> Any:
        if not auth_admin(request):
            return _err(401, "admin key required", "authentication_error")
        app.state.registry = load_registry(cfg.resolved_registry_path())
        app.state.clients = load_clients(cfg.resolved_clients_path())
        app.state.provider_keys = cfg.load_provider_keys()
        return {"reloaded": True, "models": len(app.state.registry.entries),
                "clients": sorted(app.state.clients.clients)}

    @app.get("/admin/routes")
    async def admin_routes(request: Request) -> Any:
        if not auth_admin(request):
            return _err(401, "admin key required", "authentication_error")
        return app.state.telemetry.stats(time.time() - 86_400)

    @app.get("/admin/costs")
    async def admin_costs(request: Request) -> Any:
        if not auth_admin(request):
            return _err(401, "admin key required", "authentication_error")
        now = time.time()
        # This endpoint IS the alert-chain poll (spec: n8n polls it every
        # 15 min): record the heartbeat and surface pending alerts.
        app.state.store.record_poll(now)
        app.state.governor.evaluate_alerts(
            app.state.clients.clients, provider_alert_levels(app.state.registry), now,
            sensitive_cleared_entries=sum(
                1 for e in app.state.registry.entries if e.sensitive_cleared))
        day0 = time.mktime(time.strptime(
            time.strftime("%Y-%m-%d", time.localtime(now)), "%Y-%m-%d"))
        month0 = time.mktime(time.strptime(
            time.strftime("%Y-%m-01", time.localtime(now)), "%Y-%m-%d"))
        return {
            "today": app.state.store.costs_breakdown(day0, now + 1),
            "month": app.state.store.costs_breakdown(month0, now + 1),
            "alerts": app.state.store.alerts_since(day0),
            "emergency_paid_lane_switch": {
                "enabled": app.state.governor.emergency.enabled,
                "warn_only": app.state.governor.emergency.warn_only,
                "tripped": app.state.governor.paid_lanes_off(now)[0],
            },
        }

    @app.post("/admin/killswitch")
    async def admin_killswitch(request: Request) -> Any:
        if not auth_admin(request):
            return _err(401, "admin key required", "authentication_error")
        try:
            payload = await request.json()
        except Exception:
            return _err(400, "JSON body required", "invalid_request")
        if payload.get("clear_emergency"):
            app.state.governor.clear_emergency()
            return {"emergency_cleared": True}
        client_id = payload.get("client_id")
        active = bool(payload.get("active", True))
        if not client_id or app.state.clients.get(client_id) is None:
            return _err(400, "unknown client_id", "invalid_request")
        app.state.store.set_killswitch(client_id, active)
        return {"client_id": client_id, "killswitch_active": active}

    @app.get("/admin/alerts")
    async def admin_alerts(request: Request, after: int = 0) -> Any:
        """Pulled-alerts outbox (ported from the parallel GitHub build in
        the 2026-11-02 convergence): n8n polls with the last id it saw
        and checkpoints by id. Polling here also counts as the alert
        chain heartbeat, same as /admin/costs."""
        if not auth_admin(request):
            return _err(401, "admin key required", "authentication_error")
        now = time.time()
        app.state.store.record_poll(now)
        app.state.governor.evaluate_alerts(
            app.state.clients.clients, provider_alert_levels(app.state.registry), now,
            sensitive_cleared_entries=sum(
                1 for e in app.state.registry.entries if e.sensitive_cleared))
        alerts = app.state.store.alerts_after(after)
        return {"alerts": alerts,
                "last_id": alerts[-1]["id"] if alerts else after}

    # ------------------------------------------------------------------
    # shadow mode: the routing decision with no upstream call
    # (ported from the parallel GitHub build, 2026-11-02 convergence;
    # also the Phase 3 OpenClaw shadow-mode mechanism)
    # ------------------------------------------------------------------
    @app.post("/v1/shadow")
    async def shadow(request: Request) -> Any:
        client = auth_client(request)
        if client is None:
            return _err(401, "invalid API key", "authentication_error")
        try:
            body = await request.json()
        except Exception:
            return _err(400, "request body must be JSON", "invalid_request")
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list) \
                or not body["messages"]:
            return _err(400, "messages must be a non-empty list", "invalid_request")
        bad_shape = _normalize_chat_body(body)
        if bad_shape is not None:
            return bad_shape
        if app.state.governor.killswitch_active(client.id):
            return _err(403, f"client {client.id} is stopped by the admin killswitch",
                        "killswitch_active")

        messages: list[dict[str, Any]] = body["messages"]
        requested_model = str(body.get("model") or "auto")
        max_tokens = int(body.get("max_tokens") or DEFAULT_MAX_TOKENS)
        alias = requested_model if requested_model in ALIASES else None
        explicit_entry = app.state.registry.get(requested_model) \
            if alias is None else None
        if explicit_entry is not None and not client.lane_allowed(explicit_entry.lane):
            return _err(403, f"lane {explicit_entry.lane} is not allowed for "
                             f"client {client.id}", "lane_not_allowed")
        if alias == "premium" and not client.premium_allowed:
            return _err(403, "premium lane is not allowed for this client",
                        "lane_not_allowed")
        level_override: Optional[int] = None
        raw_override = request.headers.get("x-sovereign-level")
        if raw_override:
            try:
                level_override = int(raw_override)
                if not 1 <= level_override <= 5:
                    raise ValueError
            except ValueError:
                return _err(400, "X-Sovereign-Level must be an integer 1..5",
                            "invalid_request")

        est_tokens = estimate_input_tokens(body)
        tools = body.get("tools") or []
        has_images = _has_images(messages)
        rf = body.get("response_format") or {}
        json_mode = isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema")
        system_text = "\n".join(_message_text(m) for m in messages
                                if m.get("role") == "system")
        first_user_text = next((_message_text(m) for m in messages
                                if m.get("role") == "user"), "")
        last_user_text = next((_message_text(m) for m in reversed(messages)
                               if m.get("role") == "user"), "")

        scan = scan_messages(messages)
        pii = scan_pii_messages(messages)
        if pii and cfg.pii_detectors:
            scan = ScanResult(sensitive=True,
                              detectors=sorted(set(scan.detectors) | set(pii)))
        data_class = client.default_data_class
        if scan.sensitive and not cfg.sensitivity_log_only:
            data_class = "sensitive"

        classification = await app.state.classifier.classify(
            last_user_text, est_input_tokens=est_tokens, has_images=has_images,
            json_mode=json_mode, alias=alias, level_override=level_override)
        level = classification.level
        task_class = classification.task_class
        explicit_premium = classification.explicit_premium or \
            (explicit_entry is not None and explicit_entry.lane == "premium")

        conv_key = conversation_key(client.id, first_user_text, system_text)
        sticky_entry = app.state.sticky.get(conv_key)

        steering = app.state.governor.steering(
            client.id, client.daily_target_usd, level)
        required_quality = max(
            1, REQUIRED_QUALITY[level] + steering.required_quality_delta)
        premium_permitted = bool(
            client.premium_allowed
            and (level == 5 or explicit_premium)
            and (not steering.premium_explicit_only or explicit_premium))
        paid_off, paid_off_reason = app.state.governor.paid_lanes_off()

        candidates, skipped = build_candidates(
            client=client, level=level, task_class=task_class,
            required_quality=required_quality, est_tokens=est_tokens,
            max_tokens=max_tokens, has_tools=bool(tools),
            data_class=data_class, premium_permitted=premium_permitted,
            paid_off_reason=paid_off_reason,
            premium_only=explicit_premium and premium_permitted)
        if not candidates and level == 5 and required_quality >= 5:
            candidates, skipped2 = build_candidates(
                client=client, level=level, task_class=task_class,
                required_quality=4, est_tokens=est_tokens,
                max_tokens=max_tokens, has_tools=bool(tools),
                data_class=data_class, premium_permitted=False,
                paid_off_reason=paid_off_reason)
            if candidates:
                skipped = skipped2 + [{"model": "-", "reason": "level5_fallback_quality4"}]
        if steering.cheapest_quality4_only and len(candidates) > 1:
            exact = [c for c in candidates
                     if _selection_quality(c, task_class, bool(tools)) == 4]
            if exact:
                candidates = exact
        candidates, pin_blocked = apply_pin(
            candidates, explicit_entry, client, data_class, paid_off_reason)
        if pin_blocked:
            skipped.append(pin_blocked)
        if sticky_entry:
            match = next((c for c in candidates if c.id == sticky_entry.model_id), None)
            if match is not None:
                candidates.remove(match)
                candidates.insert(0, match)
        # Paid spillway (v0.5.5): shadow must predict what the live
        # path would do — a zero-candidate decision here would be a
        # served paid turn live. Same gate as chat_completions.
        if not candidates and alias is not None and alias != "premium" \
                and explicit_entry is None \
                and not (explicit_premium and premium_permitted):
            spill_candidates, spill_skipped = build_spillway_candidates(
                client=client, level=level, task_class=task_class,
                required_quality=required_quality, est_tokens=est_tokens,
                max_tokens=max_tokens, has_tools=bool(tools),
                data_class=data_class, paid_off_reason=paid_off_reason,
                premium_permitted=premium_permitted)
            if spill_candidates:
                candidates = spill_candidates
                skipped = skipped + [{"model": "-", "reason": "paid_spillway_pass"}] \
                    + spill_skipped

        first = candidates[0] if candidates else None
        tiered = None
        if cfg.tier_shadow:
            try:
                tiered = tiering.decide(
                    candidates, task_class=task_class, has_tools=bool(tools),
                    has_images=has_images, json_mode=json_mode,
                    quality_fn=_selection_quality, est_tokens=est_tokens,
                    max_tokens=max_tokens,
                    classifier_confidence=float(classification.confidence or 0.0),
                    tool_stats=app.state.tier_tool_stats.get() if tools else None,
                ).to_dict()
            except Exception:  # noqa: BLE001
                tiered = {"error": "tier_shadow_error"}
        return {
            "shadow": True,
            "tiered_shadow": tiered,
            "client": client.id,
            "requested_model": requested_model,
            "level": level,
            "task_class": task_class,
            "classifier": {"layer": classification.layer,
                           "confidence": classification.confidence},
            "data_class": data_class,
            "sensitive_detectors": scan.detectors if scan.sensitive else [],
            "estimated_input_tokens": est_tokens,
            "sticky_model": sticky_entry.model_id if sticky_entry else None,
            "premium_permitted": premium_permitted,
            "paid_lanes_off": paid_off_reason or None,
            "would_route_to": (
                {"model": first.id, "lane": first.lane,
                 "provider": first.provider,
                 "estimated_cost_usd": first.estimate_cost(est_tokens, max_tokens // 2)}
                if first else None),
            "candidates": [
                {"model": c.id, "lane": c.lane, "provider": c.provider,
                 "quality": _selection_quality(c, task_class, bool(tools)),
                 "estimated_cost_usd": c.estimate_cost(est_tokens, max_tokens // 2)}
                for c in candidates],
            "skipped": skipped,
        }

    # ------------------------------------------------------------------
    # the main event
    # ------------------------------------------------------------------
    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        t_start = time.perf_counter()
        client = auth_client(request)
        if client is None:
            return _err(401, "invalid API key", "authentication_error")
        try:
            body = await request.json()
        except Exception:
            return _err(400, "request body must be JSON", "invalid_request")
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list) \
                or not body["messages"]:
            return _err(400, "messages must be a non-empty list", "invalid_request")
        bad_shape = _normalize_chat_body(body)
        if bad_shape is not None:
            return bad_shape

        # Manual killswitch: an operator stop, not a budget refusal.
        if app.state.governor.killswitch_active(client.id):
            return _err(403, f"client {client.id} is stopped by the admin killswitch",
                        "killswitch_active")

        messages: list[dict[str, Any]] = body["messages"]
        requested_model = str(body.get("model") or "auto")
        stream = bool(body.get("stream"))
        max_tokens = int(body.get("max_tokens") or DEFAULT_MAX_TOKENS)

        # ---- normalize: aliases + explicit models ----------------------
        alias = requested_model if requested_model in ALIASES else None
        explicit_entry = app.state.registry.get(requested_model) \
            if alias is None else None
        if explicit_entry is not None and not client.lane_allowed(explicit_entry.lane):
            return _err(403, f"lane {explicit_entry.lane} is not allowed for "
                             f"client {client.id}", "lane_not_allowed")
        if alias == "premium" and not client.premium_allowed:
            return _err(403, "premium lane is not allowed for this client",
                        "lane_not_allowed")

        level_override: Optional[int] = None
        raw_override = request.headers.get("x-sovereign-level")
        if raw_override:
            try:
                level_override = int(raw_override)
                if not 1 <= level_override <= 5:
                    raise ValueError
            except ValueError:
                return _err(400, "X-Sovereign-Level must be an integer 1..5",
                            "invalid_request")

        # ---- profile ----------------------------------------------------
        est_tokens = estimate_input_tokens(body)
        tools = body.get("tools") or []
        tool_names = [t.get("function", {}).get("name") for t in tools
                      if isinstance(t, dict)]
        has_images = _has_images(messages)
        rf = body.get("response_format") or {}
        json_mode = isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema")
        system_text = "\n".join(_message_text(m) for m in messages
                                if m.get("role") == "system")
        first_user_text = next((_message_text(m) for m in messages
                                if m.get("role") == "user"), "")
        last_user_text = next((_message_text(m) for m in reversed(messages)
                               if m.get("role") == "user"), "")
        in_tool_loop = bool(messages) and messages[-1].get("role") == "tool"
        # v0.10.5: one detector for telemetry, F2 and the ledger.
        has_tool_error = any(
            m.get("role") == "tool" and _tool_text_has_error(_message_text(m))
            for m in messages[-3:])

        # 413 pre-check: no lane-allowed model can EVER fit -> 413.
        lane_entries = [e for e in app.state.registry.entries
                        if e.enabled and client.lane_allowed(e.lane)]
        if lane_entries and all(
                est_tokens + MIN_OUTPUT_TOKENS > e.context_tokens
                for e in lane_entries):
            return _err(413, "request exceeds the context of every eligible model",
                        "context_length_exceeded",
                        {"estimated_input_tokens": est_tokens})

        # ---- sensitivity scan ------------------------------------------
        scan = scan_messages(messages)
        pii = scan_pii_messages(messages)
        if pii and cfg.pii_detectors:
            scan = ScanResult(sensitive=True,
                              detectors=sorted(set(scan.detectors) | set(pii)))
        data_class = client.default_data_class
        sensitive_active = scan.sensitive and not cfg.sensitivity_log_only
        if sensitive_active:
            data_class = "sensitive"

        # ---- classify ----------------------------------------------------
        t_c0 = time.perf_counter()
        classification = await app.state.classifier.classify(
            last_user_text, est_input_tokens=est_tokens, has_images=has_images,
            json_mode=json_mode, alias=alias, level_override=level_override)
        classify_ms = (time.perf_counter() - t_c0) * 1000
        level = classification.level
        task_class = classification.task_class
        explicit_premium = classification.explicit_premium or \
            (explicit_entry is not None and explicit_entry.lane == "premium")

        # ---- sticky check -------------------------------------------------
        conv_key = conversation_key(client.id, first_user_text, system_text)
        sticky_entry = app.state.sticky.get(conv_key)
        # v0.10.2: judge the previous serve. A tool-loop continuation's
        # trailing tool results are the outcome of the turn the sticky
        # model just served; record that outcome against that model —
        # the production tool-error signal tool-bearing selection now
        # reads (owner ruling 2026-11-27; see toolstats.py). Recorded
        # before the F2 block below can invalidate the binding, uses
        # the F2 trailing-run signal (each continuation judges exactly
        # one serve — the telemetry 3-message window would count a
        # single error twice), and is never on the critical path.
        if in_tool_loop and sticky_entry is not None:
            try:
                app.state.toolstats.record(
                    sticky_entry.model_id, _latest_tool_error(messages))
            except Exception:  # noqa: BLE001 — telemetry never breaks serving
                pass
        # A new user message at a higher level is the only classification
        # reason to abandon the sticky model; tool results never are.
        new_higher_level = bool(
            sticky_entry and not in_tool_loop and level > sticky_entry.level)
        # F2 (v0.9.2): a sticky model whose tool results keep carrying
        # errors is abandoned and not returned to. Tool errors used to
        # never break stickiness, so one bad first pick (e.g. a model
        # that hallucinated tool names) held a whole session or cron run.
        sticky_broken_model = None
        if sticky_entry is not None and in_tool_loop:
            run = app.state.sticky.record_tool_outcome(
                conv_key, _latest_tool_error(messages))
            if run >= TOOL_ERROR_BREAK_AFTER:
                sticky_broken_model = sticky_entry.model_id
                app.state.sticky.avoid(conv_key, sticky_broken_model)
                app.state.sticky.invalidate(conv_key)
                sticky_entry = None
                new_higher_level = False

        # ---- governor steering -------------------------------------------
        steering = app.state.governor.steering(
            client.id, client.daily_target_usd, level)
        required_quality = max(
            1, REQUIRED_QUALITY[level] + steering.required_quality_delta)
        premium_permitted = bool(
            client.premium_allowed
            and (level == 5 or explicit_premium)
            and (not steering.premium_explicit_only or explicit_premium))
        paid_off, paid_off_reason = app.state.governor.paid_lanes_off()

        t_q0 = time.perf_counter()
        candidates, skipped = build_candidates(
            client=client, level=level, task_class=task_class,
            required_quality=required_quality, est_tokens=est_tokens,
            max_tokens=max_tokens, has_tools=bool(tools),
            data_class=data_class, premium_permitted=premium_permitted,
            paid_off_reason=paid_off_reason,
            premium_only=explicit_premium and premium_permitted)
        # Level 5 without premium permission falls back to the best
        # quality-4 model (spec routing table) instead of failing.
        if not candidates and level == 5 and required_quality >= 5:
            candidates, skipped2 = build_candidates(
                client=client, level=level, task_class=task_class,
                required_quality=4, est_tokens=est_tokens,
                max_tokens=max_tokens, has_tools=bool(tools),
                data_class=data_class, premium_permitted=False,
                paid_off_reason=paid_off_reason)
            if candidates:
                skipped = skipped2 + [{"model": "-", "reason": "level5_fallback_quality4"}]
        # Governor: above target, level 4 is held to the cheapest q4.
        if steering.cheapest_quality4_only and len(candidates) > 1:
            exact = [c for c in candidates
                     if _selection_quality(c, task_class, bool(tools)) == 4]
            if exact:
                for c in candidates:
                    if c not in exact:
                        skipped.append({"model": c.id,
                                        "reason": "governor_cheapest_quality4_only"})
                candidates = exact
        # Explicit real model name: pin it first when it is eligible.
        # J-1 (2026-11-12): the force-insert respects the two hard
        # boundaries — a disabled entry is never resurrected by a pin,
        # and a pin cannot carry sensitive data past the sensitive wall.
        # F2: drop models this conversation was moved off for repeated
        # tool errors — but never the last candidate left (an avoided
        # model is still better than a 5xx), and an explicit pin below
        # re-inserts its model regardless.
        avoided_models = app.state.sticky.avoided(conv_key)
        if avoided_models:
            kept = [c for c in candidates if c.id not in avoided_models]
            if kept:
                for c in candidates:
                    if c.id in avoided_models:
                        skipped.append({"model": c.id,
                                        "reason": "conversation_tool_errors"})
                candidates = kept
        candidates, pin_blocked = apply_pin(
            candidates, explicit_entry, client, data_class, paid_off_reason)
        if pin_blocked:
            skipped.append(pin_blocked)
        # Sticky reuse: same model if still eligible and no higher level.
        sticky_used = False
        if sticky_entry and not new_higher_level:
            match = next((c for c in candidates if c.id == sticky_entry.model_id), None)
            if match is not None:
                candidates.remove(match)
                candidates.insert(0, match)
                sticky_used = True
            else:
                # v0.8.2: a cooling sticky entry is already annotated
                # with its precise reason by build_candidates; the
                # generic sticky reason would only duplicate it.
                if not any(s["model"] == sticky_entry.model_id
                           and s["reason"] == "cooldown_stream_abort"
                           for s in skipped):
                    skipped.append({"model": sticky_entry.model_id,
                                    "reason": "sticky_no_longer_eligible"})
                app.state.sticky.invalidate(conv_key)
        queue_ms = (time.perf_counter() - t_q0) * 1000

        # v0.9.1 (F-15): NO alert is raised here anymore. The
        # pre-v0.9.1 block paged sensitive_no_cleared_provider whenever
        # the free-first set held no owner-cleared candidate — a
        # permanently-true condition that fired daily while describing
        # a fallthrough that usually never happened. The alert now
        # fires on outcomes only: note_sensitive_paid_serve at serve
        # time, and the zero-candidate 429 path below.

        record = RouteRecord(
            ts=time.time(), client=client.id, level=level, task_class=task_class,
            classifier_layer=classification.layer,
            classifier_confidence=classification.confidence,
            model=None, lane=None, skipped=skipped, stream=stream,
            sticky=sticky_used, data_class=data_class,
            sensitive_detectors=scan.detectors if scan.sensitive else [],
            sensitive_match_shapes=scan.match_shapes if scan.sensitive else [],
            sidecar_fallback=classification.sidecar_fallback,
            override=("header" if level_override else f"alias:{alias}" if alias else None),
            latency_classify_ms=round(classify_ms, 2),
            latency_queue_ms=round(queue_ms, 2),
            tool_error_next_turn=has_tool_error,
        )
        if pii and not cfg.pii_detectors:
            record.signals.append("pii_log_only:" + ",".join(pii))
        fingerprint = telemetry.outcomes.fingerprint(
            client.id, system_text, last_user_text, [n for n in tool_names if n])
        record.repeat_within_60s = telemetry.outcomes.check_and_record(fingerprint)
        if record.repeat_within_60s:
            record.signals.append("repeat_within_60s")
        if has_tool_error:
            record.signals.append("tool_error_next_turn")
        if sticky_broken_model:
            record.signals.append("sticky_broken_tool_errors")

        # Free-first / paid spillway (v0.5.5, owner ruling 2026-11-07):
        # normal selection above is the free-first pass and is
        # unchanged. When it yields ZERO eligible candidates, one
        # spillway pass over paid + premium in cost order may serve
        # the turn instead of a 429 — the generalisation of the
        # v0.5.3 sensitive rescue (same situation, 2026-11-04 bursts;
        # industrialised by the 2026-11-06 hermes triage dead end) to
        # every client and data class. Alias-based auto-selection
        # only: explicit premium requests and explicit model pins keep
        # their existing semantics and never spill — the pass is not a
        # premium unlock. The response functions also engage it (via
        # spillway_builder) when free candidates existed but every
        # attempt failed upstream.
        premium_only_mode = explicit_premium and premium_permitted
        spillway_allowed = bool(
            alias is not None and alias != "premium"
            and explicit_entry is None and not premium_only_mode)

        def spillway_builder(exclude_ids: frozenset):
            if not spillway_allowed:
                return [], []
            return build_spillway_candidates(
                client=client, level=level, task_class=task_class,
                required_quality=required_quality, est_tokens=est_tokens,
                max_tokens=max_tokens, has_tools=bool(tools),
                data_class=data_class, paid_off_reason=paid_off_reason,
                exclude_ids=exclude_ids, premium_permitted=premium_permitted)

        spillway_reason: Optional[str] = None
        if not candidates:
            spill_candidates, spill_skipped = spillway_builder(frozenset())
            if spill_candidates or spill_skipped:
                skipped.append({"model": "-", "reason": "paid_spillway_pass"})
                skipped.extend(spill_skipped)
            if spill_candidates:
                candidates = spill_candidates
                spillway_reason = SPILLWAY_REASON
                record.signals.append(f"paid_spillway:{spill_candidates[0].id}")

        # v0.10.6 (Job C) SHADOW ONLY: what the per-class tiered policy
        # would pick from this same walled candidate list. Reads the
        # list, never reorders it; any failure folds into a signal.
        if cfg.tier_shadow:
            try:
                record.tier_shadow = tiering.decide(
                    candidates, task_class=task_class, has_tools=bool(tools),
                    has_images=has_images, json_mode=json_mode,
                    quality_fn=_selection_quality, est_tokens=est_tokens,
                    max_tokens=max_tokens,
                    classifier_confidence=float(classification.confidence or 0.0),
                    tool_stats=app.state.tier_tool_stats.get() if tools else None,
                    reason=("pinned" if explicit_entry is not None
                            else "sticky" if sticky_used
                            else "spillway" if spillway_reason else ""),
                ).to_dict()
            except Exception:  # noqa: BLE001 — shadow never breaks serving
                record.signals.append("tier_shadow_error")

        if not candidates:
            if data_class == "sensitive":
                # F-15 (v0.9.1) outcome alert: a sensitive request
                # that finds no candidate at all is a real event —
                # the wall plus provider limits left nothing eligible.
                app.state.store.add_alert(
                    "sensitive_no_cleared_provider", client.id,
                    f"sensitive_no_candidate:{client.id}",
                    f"Client {client.id}: sensitive request found NO "
                    "candidate at all (429) — the sensitive wall and "
                    "provider limits left nothing eligible, and no "
                    "owner-cleared provider exists. (F-15: this alert "
                    "fires on the outcome, not on the empty clearance "
                    "list.)")
            record.status = 429
            record.upstream_failure = True
            telemetry.write(record)
            return _err(429, "all candidate models are rate-limited or unavailable",
                        "rate_limited",
                        {"skipped": skipped, "level": level, "task_class": task_class},
                        headers={"Retry-After": "60"})

        # Estimated cost recorded pre-call (governor), corrected post-call.
        first = candidates[0]
        est_cost = first.estimate_cost(est_tokens, max_tokens // 2)
        record.cost_estimate_usd = est_cost
        app.state.store.record_usage(UsageEvent(
            ts=time.time(), client_id=client.id, provider=first.provider,
            model_id=first.id, lane=first.lane, level=level,
            task_class=task_class, input_tokens=est_tokens,
            output_tokens=max_tokens // 2, cached_tokens=0,
            cost_usd=est_cost, estimated=True, data_class=data_class))

        if stream:
            return await _stream_response(
                app=app, cfg=cfg, body=body, candidates=candidates,
                record=record, client=client, conv_key=conv_key, level=level,
                task_class=task_class, est_tokens=est_tokens, tools=tools,
                json_mode=json_mode, skipped=skipped,
                call_upstream=call_upstream, upstream_body=upstream_body,
                t_start=t_start, spillway_reason=spillway_reason,
                spillway_builder=spillway_builder)
        return await _json_response(
            app=app, body=body, candidates=candidates, record=record,
            client=client, conv_key=conv_key, level=level,
            task_class=task_class, est_tokens=est_tokens, tools=tools,
            json_mode=json_mode, skipped=skipped,
            call_upstream=call_upstream, upstream_body=upstream_body,
            t_start=t_start, spillway_reason=spillway_reason,
            spillway_builder=spillway_builder)

    return app


# ======================================================================
# non-streaming execution with failover + validation escalation
# ======================================================================
def _record_attempt(app: FastAPI, record: RouteRecord, client: ClientPolicy,
                    entry: ModelEntry, *, outcome: str,
                    status: Optional[int] = None, body_text: str = "",
                    headers: Any = None, transport: bool = False,
                    units: float = 0.0,
                    truncated_tool_call: bool = False,
                    usage: Any = None) -> None:
    """Write one attempt to the pool ledger (v0.8.0, aspect E).

    Every upstream attempt lands here exactly once: failures are
    classified into the taxonomy (class + scope), the observe-only
    demotion tracker computes its would_demote annotations, and the
    attempt row + annotations are persisted (annotations also ride on
    the turn's route record). The ledger is observability, never on
    the critical path: a ledger failure must not break serving, so
    errors fold into a route-record signal instead of raising.
    Selection does not read any of this in v0.8.0 (decision J-2).
    """
    try:
        pool = pool_for(entry)
        failure = None
        if outcome == "failed":
            failure = classify_failure(
                entry=entry, status=status, body_text=body_text[:2000],
                headers=headers, transport=transport)
        elif truncated_tool_call:
            # v0.8.2: the response WAS received (outcome stays
            # "served"), but finish_reason=length cut it mid-tool-call
            # — the F-09 ledger gap. The class rides the attempt row
            # (and the observe-only demotion annotation) so the defect
            # is countable instead of masquerading as a clean serve.
            failure = classify_truncated_tool_call(entry)
        annotations = app.state.demotions.observe(
            entry=entry, failure=failure, now=time.time(), units=units)
        # Borrowed per-attempt contract (2026-11-24): the attempt's
        # ordinal within the turn rides on the route record as a plain
        # attribute (not a serialized field — routes.jsonl is unchanged);
        # provider-reported cost / reasoning tokens come from the
        # attempt's own usage object when the upstream sent one.
        depth = getattr(record, "attempt_ordinal", 0)
        record.attempt_ordinal = depth + 1  # type: ignore[attr-defined]
        p_cost, r_tokens = _attempt_usage_extras(usage)
        app.state.store.record_attempt(AttemptEvent(
            ts=time.time(), client_id=client.id, provider=entry.provider,
            model_id=entry.id, pool_id=pool.id, outcome=outcome,
            status=status,
            failure_class=failure.failure_class if failure else None,
            scope=failure.scope if failure else None,
            scope_target=failure.target if failure else None,
            would_demote=json.dumps(annotations) if annotations else None,
            fallback_depth=depth, provider_cost_usd=p_cost,
            reasoning_tokens=r_tokens,
            error_body=_scrub_error_body(body_text)
            if outcome == "failed" else None))
        seen = {(a["scope"], a["target"]) for a in record.would_demote}
        for ann in annotations:
            if (ann["scope"], ann["target"]) not in seen:
                record.would_demote.append(ann)
    except Exception:  # noqa: BLE001 — observability never breaks serving
        record.signals.append("attempt_ledger_error")


class _DepthBudget:
    """Per-turn upstream-attempt budget (v0.10.5, Job B).

    ``calls`` counts every upstream call the turn makes. ``exhausted``
    stops the turn at ``cap``. ``reserve_reached`` tells the free-first
    pass to hand over to the paid spillway once only
    SPILLWAY_RESERVED_ATTEMPTS calls are left, so a congested free pool
    cannot use the whole budget; if no spillway candidate exists the
    first pass simply continues up to the cap."""

    def __init__(self, cap: int) -> None:
        self.cap = max(1, int(cap))
        self.calls = 0
        self.early_probe_done = False

    def exhausted(self) -> bool:
        return self.calls >= self.cap

    def reserve_reached(self) -> bool:
        reserve = SPILLWAY_RESERVED_ATTEMPTS if self.cap > SPILLWAY_RESERVED_ATTEMPTS else 0
        return reserve > 0 and self.calls >= self.cap - reserve


def _keep_premium_skips(skipped: list[dict[str, str]],
                        extra_skipped: list[dict[str, str]]) -> None:
    """An empty spillway pass discards its skip list, but the v0.10.5
    premium-not-required decision must stay visible on the route row."""
    seen = {(s.get("model"), s.get("reason")) for s in skipped}
    for s in extra_skipped:
        if s.get("reason") == PREMIUM_NOT_REQUIRED_REASON and \
                (s.get("model"), s.get("reason")) not in seen:
            skipped.append(s)
            seen.add((s.get("model"), s.get("reason")))


def _note_depth_cap(record: RouteRecord, skipped: list[dict[str, str]],
                    budget: "_DepthBudget") -> None:
    sig = f"{DEPTH_CAP_REASON}:{budget.cap}"
    if sig not in record.signals:
        record.signals.append(sig)
        skipped.append({"model": "-", "reason": DEPTH_CAP_REASON})


async def _json_response(*, app: FastAPI, body: dict[str, Any],
                         candidates: list[ModelEntry], record: RouteRecord,
                         client: ClientPolicy, conv_key: str, level: int,
                         task_class: str, est_tokens: int,
                         tools: list[dict[str, Any]], json_mode: bool,
                         skipped: list[dict[str, str]],
                         call_upstream: Any, upstream_body: Any,
                         t_start: float,
                         spillway_reason: Optional[str] = None,
                         spillway_builder: Any = None) -> Any:
    telemetry: Telemetry = app.state.telemetry
    escalated = False
    last_failures: list[str] = []
    retried_same: set[int] = set()
    budget_override: Optional[int] = None
    attempted: set[str] = set()
    # The paid spillway engages at most once per turn: either the
    # caller adopted its list at selection time (spillway_reason
    # already set), or it engages in the loop guard below when every
    # first-pass candidate failed upstream (v0.5.5).
    spillway_engaged = spillway_reason is not None
    budget = _DepthBudget(app.state.cfg.max_upstream_attempts)
    idx = 0
    while True:
        if budget.exhausted():
            _note_depth_cap(record, skipped, budget)
            break
        early = (idx < len(candidates) and spillway_builder is not None
                 and not spillway_engaged and not budget.early_probe_done
                 and budget.reserve_reached())
        if idx >= len(candidates) or early:
            if early:
                budget.early_probe_done = True
            if spillway_builder is not None and not spillway_engaged:
                extra, extra_skipped = spillway_builder(frozenset(attempted))
                if extra:
                    if early:
                        record.signals.append(
                            f"fallback_depth_reserve_spillway:{budget.calls}")
                    spillway_engaged = True
                    spillway_reason = SPILLWAY_REASON
                    skipped.append({"model": "-", "reason": "paid_spillway_pass"})
                    skipped.extend(extra_skipped)
                    record.signals.append(f"paid_spillway:{extra[0].id}")
                    candidates = extra
                    idx = 0
                    retried_same = set()
                    budget_override = None
                    continue
                _keep_premium_skips(skipped, extra_skipped)
            if not early:
                break
        entry = candidates[idx]
        attempted.add(entry.id)
        payload = upstream_body(body, entry, est_tokens, stream=False,
                                max_tokens_override=budget_override)
        budget_override = None
        budget.calls += 1
        t_u0 = time.perf_counter()
        try:
            resp = await call_upstream(entry, payload)
        except (httpx.TimeoutException, httpx.TransportError):
            record.latency_upstream_ms += (time.perf_counter() - t_u0) * 1000
            skipped.append({"model": entry.id, "reason": "upstream_unreachable"})
            _record_attempt(app, record, client, entry,
                            outcome="failed", transport=True)
            if record.sticky:
                app.state.sticky.invalidate(conv_key)
            idx += 1
            continue
        record.latency_upstream_ms += (time.perf_counter() - t_u0) * 1000
        if resp.status_code in FAILOVER_STATUSES or resp.status_code >= 400:
            skipped.append({"model": entry.id,
                            "reason": f"upstream_{resp.status_code}"})
            _record_attempt(app, record, client, entry,
                            outcome="failed", status=resp.status_code,
                            body_text=resp.text, headers=resp.headers)
            if resp.status_code in DEAD_LANE_STATUSES:
                note_lane_dead(app, entry, f"upstream_{resp.status_code}")
            if record.sticky:
                app.state.sticky.invalidate(conv_key)
            idx += 1
            continue
        try:
            data = resp.json()
        except ValueError:
            skipped.append({"model": entry.id, "reason": "upstream_bad_json"})
            _record_attempt(app, record, client, entry,
                            outcome="failed", status=resp.status_code)
            idx += 1
            continue

        # v0.8.2: budget-truncated mid-tool-call (F-09 ledger gap).
        _choice0 = (data.get("choices") or [{}])[0]
        _msg0 = _choice0.get("message") or {}
        truncated_tc = _choice0.get("finish_reason") == "length" \
            and bool(_msg0.get("tool_calls"))

        failures = validate_response(data, tools=tools or None, json_mode=json_mode)
        if failures and not escalated:
            if "length_no_content" in failures and entry.reasoning \
                    and idx not in retried_same:
                # Reasoning models can spend the entire output budget on
                # hidden reasoning and return finish_reason=length with
                # no visible content (burn-in 2026-11-03: this was the
                # dominant plain-traffic failure on groq/gpt-oss-120b and
                # drove escalations to 9.1% vs the <8% target). One retry
                # of the SAME model with a doubled budget clears most of
                # these and is cheaper than escalating; it is not counted
                # as an escalation. A second length failure falls through
                # to the normal escalation below.
                retried_same.add(idx)
                current = payload.get("max_completion_tokens") \
                    or payload.get("max_tokens") or DEFAULT_MAX_TOKENS
                budget_override = int(current) * 2
                record.signals.append(
                    f"same_model_retry:{entry.id}:length_no_content")
                _r_in, _r_out, _, _ = _usage_from_response(
                    data, est_tokens, entry)
                _record_attempt(app, record, client, entry,
                                outcome="served", units=_r_in + _r_out,
                                truncated_tool_call=truncated_tc,
                                usage=data.get("usage")
                                or data.get("usageMetadata"))
                continue
            # Escalate once: next candidate at quality+1 (spec). Fallback
            # (found by live E2E, 2026-11-02): when no candidate reaches
            # quality+1 — e.g. a level-1 chat where the best lane-allowed
            # model is already quality 4 — retry the next candidate at
            # >= current quality instead of serving a known-invalid
            # answer; a different model rarely fails the same way.
            entry_q = _selection_quality(entry, task_class, bool(tools))
            target_q = entry_q + 1
            esc_idx = next(
                (j for j in range(idx + 1, len(candidates))
                 if _selection_quality(candidates[j], task_class,
                                       bool(tools)) >= target_q),
                None)
            if esc_idx is None:
                esc_idx = next(
                    (j for j in range(idx + 1, len(candidates))
                     if _selection_quality(candidates[j], task_class,
                                           bool(tools)) >= entry_q),
                    None)
            if esc_idx is not None:
                escalated = True
                last_failures = failures
                skipped.append({"model": entry.id,
                                "reason": "validation_failed:" + ",".join(failures)})
                _r_in, _r_out, _, _ = _usage_from_response(
                    data, est_tokens, entry)
                _record_attempt(app, record, client, entry,
                                outcome="served", units=_r_in + _r_out,
                                truncated_tool_call=truncated_tc,
                                usage=data.get("usage")
                                or data.get("usageMetadata"))
                idx = esc_idx
                continue
            # No stronger candidate: return the answer we have, logged.
            record.signals.append("validation_failed_no_escalation:" + ",".join(failures))

        in_tok, out_tok, cached, cost = _usage_from_response(data, est_tokens, entry)
        record.model = entry.id
        record.lane = entry.lane
        # A spillway serve is recorded as an escalation with its
        # distinct reason even though the local validation machine
        # never fired; validation-escalation failures from the same
        # turn stay in front of it when both somehow apply.
        record.escalated = escalated or spillway_reason is not None
        reasons = list(last_failures) if escalated else []
        if spillway_reason and spillway_reason not in reasons:
            reasons.append(spillway_reason)
        record.escalation_reasons = reasons
        record.input_tokens, record.output_tokens = in_tok, out_tok
        record.cached_tokens, record.cost_usd = cached, cost
        record.status = 200
        record.latency_upstream_ms = round(record.latency_upstream_ms, 2)
        app.state.store.record_usage(UsageEvent(
            ts=time.time(), client_id=client.id, provider=entry.provider,
            model_id=entry.id, lane=entry.lane, level=level,
            task_class=task_class, input_tokens=in_tok, output_tokens=out_tok,
            cached_tokens=cached, cost_usd=cost, estimated=False,
            data_class=record.data_class))
        _record_attempt(app, record, client, entry,
                        outcome="served", units=in_tok + out_tok,
                        truncated_tool_call=truncated_tc,
                        usage=data.get("usage") or data.get("usageMetadata"))
        note_sensitive_paid_serve(app, record, client, entry, cost)
        app.state.sticky.set(conv_key, entry.id, level)
        app.state.governor.evaluate_alerts(
            app.state.clients.clients,
            {e.provider: e.spend_alert_usd for e in app.state.registry.entries
             if e.spend_alert_usd is not None},
            sensitive_cleared_entries=sum(
                1 for e in app.state.registry.entries if e.sensitive_cleared))
        telemetry.write(record)
        return JSONResponse(
            data, status_code=200,
            headers=_route_headers(entry, level, task_class,
                                   record.cost_estimate_usd, record.escalated))

    record.status = 429
    record.upstream_failure = True
    record.escalated = escalated or spillway_reason is not None
    reasons = list(last_failures) if escalated else []
    if spillway_reason and spillway_reason not in reasons:
        reasons.append(spillway_reason)
    record.escalation_reasons = reasons
    telemetry.write(record)
    return _err(429, "all candidate models are rate-limited or failing",
                "rate_limited",
                {"skipped": skipped, "level": level, "task_class": task_class},
                headers={"Retry-After": "60"})


# ======================================================================
# streaming execution: tool calls buffered + validated before commit;
# text commits at the first content chunk (spec streaming rule).
# ======================================================================
async def _stream_response(*, app: FastAPI, cfg: GatewayConfig,
                           body: dict[str, Any], candidates: list[ModelEntry],
                           record: RouteRecord, client: ClientPolicy,
                           conv_key: str, level: int, task_class: str,
                           est_tokens: int, tools: list[dict[str, Any]],
                           json_mode: bool, skipped: list[dict[str, str]],
                           call_upstream: Any, upstream_body: Any,
                           t_start: float,
                           spillway_reason: Optional[str] = None,
                           spillway_builder: Any = None) -> Any:
    telemetry: Telemetry = app.state.telemetry
    first = candidates[0]
    headers = _route_headers(first, level, task_class,
                             record.cost_estimate_usd, False)
    http: httpx.AsyncClient = app.state.http

    async def produce() -> AsyncIterator[str]:
        nonlocal candidates, spillway_reason
        escalated = False
        if spillway_reason is not None:
            # Selection-time spillway (v0.5.5): the row must show the
            # fallthrough even though the streaming validation machine
            # never fired. A later tool-call escalation overwrites
            # these reasons with its own, as before.
            record.escalation_reasons = [spillway_reason]
        attempted: set[str] = set()
        # Engages at most once per turn (see _json_response).
        spillway_engaged = spillway_reason is not None
        budget = _DepthBudget(app.state.cfg.max_upstream_attempts)
        idx = 0
        finalized = False
        while True:
            if budget.exhausted():
                _note_depth_cap(record, skipped, budget)
                break
            early = (idx < len(candidates) and spillway_builder is not None
                     and not spillway_engaged and not budget.early_probe_done
                     and budget.reserve_reached())
            if idx >= len(candidates) or early:
                # Every candidate failed upstream (or the v0.10.5 depth
                # reserve is reached): engage the paid spillway once
                # rather than ending the stream in a 429 (v0.5.5;
                # mirrors the JSON path's loop guard).
                if early:
                    budget.early_probe_done = True
                if spillway_builder is not None and not spillway_engaged:
                    extra, extra_skipped = spillway_builder(
                        frozenset(attempted))
                    if extra:
                        if early:
                            record.signals.append(
                                f"fallback_depth_reserve_spillway:{budget.calls}")
                        spillway_engaged = True
                        spillway_reason = SPILLWAY_REASON
                        if SPILLWAY_REASON not in record.escalation_reasons:
                            record.escalation_reasons = [
                                *record.escalation_reasons, SPILLWAY_REASON]
                        record.signals.append(
                            f"paid_spillway:{extra[0].id}")
                        skipped.append({"model": "-",
                                        "reason": "paid_spillway_pass"})
                        skipped.extend(extra_skipped)
                        candidates = extra
                        idx = 0
                        continue
                    _keep_premium_skips(skipped, extra_skipped)
                if not early:
                    break
            entry = candidates[idx]
            attempted.add(entry.id)
            payload = upstream_body(body, entry, est_tokens, stream=True)
            budget.calls += 1
            url = f"{entry.effective_base_url}/chat/completions"
            hdrs = {"Content-Type": "application/json"}
            key = app.state.provider_keys.get(entry.provider)
            if key:
                hdrs["Authorization"] = f"Bearer {key}"
            t_u0 = time.perf_counter()
            # Hoisted before the try (v0.7.0): the transport-error
            # handler below must know whether visible output was
            # already committed for THIS candidate — including when the
            # failure happened before the in-stream reset further down.
            committed = False
            try:
                async with http.stream("POST", url, json=payload, headers=hdrs) as resp:
                    if resp.status_code != 200:
                        raw = await asyncio.wait_for(
                            resp.aread(),
                            timeout=cfg.stream_first_chunk_timeout_s)
                        skipped.append({"model": entry.id,
                                        "reason": f"upstream_{resp.status_code}"})
                        _record_attempt(
                            app, record, client, entry,
                            outcome="failed", status=resp.status_code,
                            body_text=raw.decode("utf-8", "replace"),
                            headers=resp.headers)
                        if resp.status_code in DEAD_LANE_STATUSES:
                            note_lane_dead(app, entry,
                                           f"upstream_{resp.status_code}")
                        idx += 1
                        continue
                    assembler = ToolCallAssembler()
                    buffered: list[str] = []
                    decided: Optional[str] = None   # None | "text" | "tool"
                    committed = False
                    content_chars = 0
                    saw_content = False
                    usage_data: dict[str, Any] = {}
                    retry_candidate = False
                    # v0.8.2 (F-11): the iteration is driven manually so
                    # each read carries its own bound — the first line
                    # gets the first-chunk bound, every later line the
                    # inter-chunk stall bound — instead of the client-
                    # wide flat 120 s read timeout, under which a
                    # wedged stream hung ~2 minutes before aborting.
                    # A bound expiry raises TimeoutError into the same
                    # handler as a transport drop, so the ADD-06 commit
                    # guard and pre-commit failover apply unchanged.
                    line_iter = resp.aiter_lines().__aiter__()
                    first_line = True
                    # v0.10.9 (ROUTER-STRESS-20261128): chunks before
                    # the first content/tool chunk (role-only deltas,
                    # keep-alives) are held, not committed, and an
                    # in-stream error event before any content is a
                    # FAILED ATTEMPT that fails over. Live: Groq's
                    # tool_use_failed arrives as a data: {"error":...}
                    # event after a role chunk; the role chunk used to
                    # commit the stream, so the error was handed to
                    # OpenClaw ("LLM request rejected: Tool call
                    # validation failed") instead of the next model.
                    pre_content: list[str] = []
                    stream_error: Optional[str] = None
                    while True:
                        bound = cfg.stream_first_chunk_timeout_s \
                            if first_line else cfg.stream_stall_timeout_s
                        try:
                            line = await asyncio.wait_for(
                                line_iter.__anext__(), timeout=bound)
                        except StopAsyncIteration:
                            break
                        first_line = False
                        if not line.startswith("data:"):
                            continue
                        payload_str = line[5:].strip()
                        if payload_str == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(payload_str)
                        except ValueError:
                            continue
                        if isinstance(chunk.get("usage"), dict):
                            usage_data = chunk["usage"]
                        if isinstance(chunk.get("usageMetadata"), dict):
                            # Gemini-native stream shape: usage rides
                            # at chunk level, not inside a usage object.
                            usage_data = {**usage_data,
                                          "usageMetadata": chunk["usageMetadata"]}
                        if chunk.get("error") and not committed \
                                and decided != "tool":
                            stream_error = payload_str[:2000]
                            break
                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        is_tool = bool(delta.get("tool_calls"))
                        is_text = isinstance(delta.get("content"), str) and delta["content"] != ""
                        sse_line = f"data: {payload_str}\n\n"
                        if decided is None and is_tool:
                            decided = "tool"
                        elif decided is None and is_text:
                            decided = "text"
                        if decided == "tool":
                            # Buffer tool-call chunks to completion; the
                            # client sees nothing until validation passes.
                            if pre_content:
                                buffered.extend(pre_content)
                                pre_content = []
                            assembler.feed(chunk)
                            buffered.append(sse_line)
                            continue
                        # text path: commit at first content chunk
                        if is_text:
                            saw_content = True
                            content_chars += len(delta["content"])
                        if not committed and not is_text:
                            pre_content.append(sse_line)
                            continue
                        if pre_content:
                            for held in pre_content:
                                yield held
                            pre_content = []
                        committed = True
                        yield sse_line
                    record.latency_upstream_ms += (time.perf_counter() - t_u0) * 1000
                    if stream_error is not None:
                        skipped.append({"model": entry.id,
                                        "reason": "upstream_stream_error"})
                        record.signals.append(
                            f"stream_error_before_commit:{entry.id}")
                        _record_attempt(app, record, client, entry,
                                        outcome="failed", status=None,
                                        body_text=stream_error)
                        idx += 1
                        continue
                    for held in pre_content:
                        yield held

                    if decided == "tool":
                        assembled = assembler.assembled_response()
                        # v0.8.2: budget-truncated mid-tool-call (F-09
                        # ledger gap) — classified on the attempt row.
                        truncated_tc = assembler.finish_reason == "length" \
                            and assembler.has_tool_calls
                        failures = validate_response(
                            assembled, tools=tools or None, json_mode=False)
                        if failures and not escalated:
                            entry_q = _selection_quality(
                                entry, task_class, bool(tools))
                            target_q = entry_q + 1
                            esc_idx = next(
                                (j for j in range(idx + 1, len(candidates))
                                 if _selection_quality(candidates[j], task_class,
                                                       bool(tools)) >= target_q),
                                None)
                            if esc_idx is None:
                                # Same fallback as the JSON path: next
                                # candidate at >= current quality beats
                                # serving a known-invalid tool call.
                                esc_idx = next(
                                    (j for j in range(idx + 1, len(candidates))
                                     if _selection_quality(candidates[j],
                                                           task_class,
                                                           bool(tools))
                                     >= entry_q),
                                    None)
                            if esc_idx is None and failures:
                                record.signals.append(
                                    "validation_failed_no_escalation:"
                                    + ",".join(failures))
                            if esc_idx is not None:
                                escalated = True
                                record.escalation_reasons = failures
                                skipped.append({
                                    "model": entry.id,
                                    "reason": "validation_failed:" + ",".join(failures)})
                                idx = esc_idx
                                retry_candidate = True
                        if retry_candidate:
                            _r_in, _r_out, _ = _usage_counts(
                                usage_data, est_tokens)
                            _record_attempt(
                                app, record, client, entry,
                                outcome="served",
                                units=_r_in + _r_out,
                                truncated_tool_call=truncated_tc,
                                usage=usage_data)
                            continue
                        for sse_line in buffered:
                            yield sse_line
                        yield "data: [DONE]\n\n"
                        # v0.8.3: the streaming path prices cached
                        # tokens too — before, it never passed them
                        # and the finalize hard-coded cached_tokens=0.
                        in_tok, out_tok, cached = _usage_counts(
                            usage_data, est_tokens)
                        cost = entry.estimate_cost(in_tok, out_tok, cached)
                        _finalize_stream(app, record, entry, client, conv_key,
                                         level, task_class, in_tok, out_tok, cost,
                                         escalated or spillway_reason is not None,
                                         truncated_tool_call=truncated_tc,
                                         cached_tokens=cached,
                                         usage=usage_data)
                        finalized = True
                        return
                    # text path finished (already committed live)
                    log_failures = validate_streamed_text_outcome(
                        saw_content=saw_content,
                        finish_reason=assembler.finish_reason,
                        content_chars=content_chars)
                    if log_failures:
                        record.signals.append(
                            "stream_text_log_only:" + ",".join(log_failures))
                    yield "data: [DONE]\n\n"
                    in_tok, out_tok, cached = _usage_counts(
                        usage_data, est_tokens)
                    if not out_tok:
                        out_tok = max(1, content_chars // 4)
                    cost = entry.estimate_cost(in_tok, out_tok, cached)
                    _finalize_stream(app, record, entry, client, conv_key,
                                     level, task_class, in_tok, out_tok, cost,
                                     escalated or spillway_reason is not None,
                                     cached_tokens=cached,
                                     usage=usage_data)
                    finalized = True
                    return
            except (httpx.TimeoutException, httpx.TransportError,
                    TimeoutError) as exc:
                record.latency_upstream_ms += (time.perf_counter() - t_u0) * 1000
                # v0.8.0: the attempt is recorded FIRST in this handler —
                # when the v0.7.0 commit guard merges in, its post-commit
                # return path must not bypass the ledger write.
                _record_attempt(app, record, client, entry,
                                outcome="failed", transport=True)
                # v0.8.2 (F-11): EVERY streaming abort — transport drop
                # or one of the new first-chunk / stall bounds firing
                # (a bare TimeoutError, not httpx's) — cools the entry,
                # committed or not, so the client's retry lands on a
                # sibling instead of re-picking the stalling entry.
                note_stream_abort(app, entry)
                if isinstance(exc, TimeoutError) and not isinstance(
                        exc, httpx.TimeoutException):
                    record.signals.append(
                        f"stream_bound_timeout:{entry.id}")
                if committed:
                    # Commit guard (ADD-06, v0.7.0): visible output from
                    # this model is already with the client. Failing
                    # over would append a SECOND model's output to the
                    # same stream — a corrupt transcript the client
                    # cannot separate. Terminate the stream with a
                    # typed error event instead; the route row carries
                    # the signal for the failure log. (The tool path
                    # never reaches this branch: its chunks are buffered
                    # until validation, so committed stays False and
                    # pre-commit failover below still applies.)
                    skipped.append({"model": entry.id,
                                    "reason": "stream_failed_after_commit"})
                    record.signals.append(
                        f"stream_aborted_after_commit:{entry.id}")
                    record.model = entry.id
                    record.lane = entry.lane
                    record.status = 502
                    record.upstream_failure = True
                    record.latency_upstream_ms = round(
                        record.latency_upstream_ms, 2)
                    telemetry.write(record)
                    yield ("data: " + json.dumps({
                        "error": {
                            "message": "upstream stream interrupted after partial output",
                            "type": "upstream_error",
                            "code": "stream_interrupted"}}) + "\n\n")
                    finalized = True
                    return
                skipped.append({"model": entry.id, "reason": "upstream_unreachable"})
                idx += 1
                continue
        if not finalized:
            record.status = 429
            record.upstream_failure = True
            if spillway_reason is not None:
                # The spillway was attempted and its candidates failed
                # upstream too — the row still records the escalation.
                record.escalated = True
            telemetry.write(record)
            yield ("data: " + json.dumps({
                "error": {"message": "all candidate models are rate-limited or failing",
                          "type": "rate_limited", "code": "rate_limited"}}) + "\n\n")

    return StreamingResponse(produce(), media_type="text/event-stream",
                             headers=headers)


def _finalize_stream(app: FastAPI, record: RouteRecord, entry: ModelEntry,
                     client: ClientPolicy, conv_key: str, level: int,
                     task_class: str, in_tok: int, out_tok: int, cost: float,
                     escalated: bool,
                     truncated_tool_call: bool = False,
                     cached_tokens: int = 0,
                     usage: Any = None) -> None:
    record.model = entry.id
    record.lane = entry.lane
    record.escalated = escalated
    record.input_tokens, record.output_tokens = in_tok, out_tok
    record.cached_tokens = cached_tokens
    record.cost_usd = cost
    record.status = 200
    record.latency_upstream_ms = round(record.latency_upstream_ms, 2)
    app.state.store.record_usage(UsageEvent(
        ts=time.time(), client_id=client.id, provider=entry.provider,
        model_id=entry.id, lane=entry.lane, level=level, task_class=task_class,
        input_tokens=in_tok, output_tokens=out_tok,
        cached_tokens=cached_tokens,
        cost_usd=cost, estimated=False, data_class=record.data_class))
    _record_attempt(app, record, client, entry,
                    outcome="served", units=in_tok + out_tok,
                    truncated_tool_call=truncated_tool_call,
                    usage=usage)
    note_sensitive_paid_serve(app, record, client, entry, cost)
    app.state.sticky.set(conv_key, entry.id, level)
    app.state.telemetry.write(record)


# Local/dev entry point:  uvicorn gateway:app
app = create_app() if os.environ.get("SOVEREIGN_DEV_SERVE") == "1" else None  # type: ignore
