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

No startup patches: this is the v0.5 source tree (the v0.3.3 canary's
PATCH_V033 mechanism is gone). Provider keys are read once at startup
from mode-600 files in the state dir (``keys/<provider>.key``); they are
never in the registry, compose files, logs or this source tree.
"""

from __future__ import annotations

import json
import os
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
from pools import DemotionTracker, classify_failure, pool_for
from quota import AttemptEvent, QuotaStore, UsageEvent
from registry import FREE_LANES, ModelEntry, Registry, load_registry
from sensitivity import scan_messages
from sticky import StickyStore, conversation_key
from telemetry import RouteRecord, Telemetry
from validation import (ToolCallAssembler, validate_response,
                        validate_streamed_text_outcome)

VERSION = "0.8.1"

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
FAILOVER_STATUSES = {401, 403, 408, 413, 429, 500, 502, 503, 504}
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
    emergency_enabled: bool = os.environ.get("SOVEREIGN_EMERGENCY_SWITCH") == "1"
    emergency_daily_usd: float = float(os.environ.get("SOVEREIGN_EMERGENCY_DAILY_USD", "25"))
    # v0.5.7 (owner ruling 2026-11-09): warn-only emergency — alert at
    # the daily level, NEVER switch the paid lanes off.
    emergency_warn_only: bool = os.environ.get("SOVEREIGN_EMERGENCY_WARN_ONLY") == "1"
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


def _message_text(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type", "text") == "text")
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


def _usage_from_response(resp: dict[str, Any], est_input: int,
                         entry: ModelEntry) -> tuple[int, int, int, float]:
    usage = resp.get("usage") or {}
    in_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or est_input)
    out_tok = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cached = 0
    details = usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    cached = cached or int(usage.get("cache_read_input_tokens") or 0)
    if out_tok == 0:
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        if isinstance(msg.get("content"), str):
            out_tok = _encode_len(msg["content"])
    return in_tok, out_tok, cached, entry.estimate_cost(in_tok, out_tok, cached)


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


def note_lane_dead(app: FastAPI, entry: ModelEntry, cause: str) -> None:
    app.state.store.add_alert(
        "lane_dead", entry.provider,
        f"lane_dead:{entry.provider}:{entry.id}:{cause}",
        f"Lane dead: {entry.id} (provider {entry.provider}) — "
        f"{cause}. This is an account/credit/cap failure, not a "
        "rate limit. The cascade is serving from the next rungs "
        "in cost order; fix or top up this provider to restore "
        "the cheap rung.")


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
            if entry.quality_for(task_class) < required_quality:
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
                    note_lane_dead(app, entry,
                                   "daily token cap (TPD) exhausted")
                skip(reason); continue
            candidates.append(entry)

        def sort_key(e: ModelEntry) -> tuple[float, int, int]:
            est_out = max_tokens // 2
            return (e.estimate_cost(est_tokens, est_out),
                    -e.quality_for(task_class),
                    e.latency_p50_ms or 99_999)

        candidates.sort(key=sort_key)
        return candidates, skipped

    def build_spillway_candidates(
        *, client: ClientPolicy, level: int, task_class: str,
        required_quality: int, est_tokens: int, max_tokens: int,
        has_tools: bool, data_class: str, paid_off_reason: str,
        exclude_ids: frozenset = frozenset(),
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
        if exclude_ids:
            candidates = [c for c in candidates if c.id not in exclude_ids]
        return candidates, skipped

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
            app.state.clients.clients, provider_alert_levels(app.state.registry), now)
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
            app.state.clients.clients, provider_alert_levels(app.state.registry), now)
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
            exact = [c for c in candidates if c.quality_for(task_class) == 4]
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
                data_class=data_class, paid_off_reason=paid_off_reason)
            if spill_candidates:
                candidates = spill_candidates
                skipped = skipped + [{"model": "-", "reason": "paid_spillway_pass"}] \
                    + spill_skipped

        first = candidates[0] if candidates else None
        return {
            "shadow": True,
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
                 "quality": c.quality_for(task_class),
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
        has_tool_error = any(
            m.get("role") == "tool" and (
                '"error"' in _message_text(m).lower()
                or _message_text(m).strip().lower().startswith("error"))
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
        # A new user message at a higher level is the only classification
        # reason to abandon the sticky model; tool results never are.
        new_higher_level = bool(
            sticky_entry and not in_tool_loop and level > sticky_entry.level)

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
            exact = [c for c in candidates if c.quality_for(task_class) == 4]
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
                skipped.append({"model": sticky_entry.model_id,
                                "reason": "sticky_no_longer_eligible"})
                app.state.sticky.invalidate(conv_key)
        queue_ms = (time.perf_counter() - t_q0) * 1000

        if data_class == "sensitive":
            cleared_available = any(
                c.sensitive_cleared or c.provider in client.sensitive_cleared_providers
                for c in candidates)
            if not cleared_available:
                app.state.store.add_alert(
                    "sensitive_no_cleared_provider", client.id,
                    f"sensitive_fallback:{client.id}",
                    f"Client {client.id}: sensitive request had no "
                    "cleared provider in free-first selection; under "
                    "the v0.5.5 free-first / paid-spillway ruling "
                    "(2026-11-07) it may fall through to a paid "
                    "provider as a last resort.")

        record = RouteRecord(
            ts=time.time(), client=client.id, level=level, task_class=task_class,
            classifier_layer=classification.layer,
            classifier_confidence=classification.confidence,
            model=None, lane=None, skipped=skipped, stream=stream,
            sticky=sticky_used, data_class=data_class,
            sensitive_detectors=scan.detectors if scan.sensitive else [],
            sidecar_fallback=classification.sidecar_fallback,
            override=("header" if level_override else f"alias:{alias}" if alias else None),
            latency_classify_ms=round(classify_ms, 2),
            latency_queue_ms=round(queue_ms, 2),
            tool_error_next_turn=has_tool_error,
        )
        fingerprint = telemetry.outcomes.fingerprint(
            client.id, system_text, last_user_text, [n for n in tool_names if n])
        record.repeat_within_60s = telemetry.outcomes.check_and_record(fingerprint)
        if record.repeat_within_60s:
            record.signals.append("repeat_within_60s")
        if has_tool_error:
            record.signals.append("tool_error_next_turn")

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
                exclude_ids=exclude_ids)

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

        if not candidates:
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
            cost_usd=est_cost, estimated=True))

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
                    units: float = 0.0) -> None:
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
        annotations = app.state.demotions.observe(
            entry=entry, failure=failure, now=time.time(), units=units)
        app.state.store.record_attempt(AttemptEvent(
            ts=time.time(), client_id=client.id, provider=entry.provider,
            model_id=entry.id, pool_id=pool.id, outcome=outcome,
            status=status,
            failure_class=failure.failure_class if failure else None,
            scope=failure.scope if failure else None,
            scope_target=failure.target if failure else None,
            would_demote=json.dumps(annotations) if annotations else None))
        seen = {(a["scope"], a["target"]) for a in record.would_demote}
        for ann in annotations:
            if (ann["scope"], ann["target"]) not in seen:
                record.would_demote.append(ann)
    except Exception:  # noqa: BLE001 — observability never breaks serving
        record.signals.append("attempt_ledger_error")


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
    idx = 0
    while True:
        if idx >= len(candidates):
            if spillway_builder is not None and not spillway_engaged:
                extra, extra_skipped = spillway_builder(frozenset(attempted))
                if extra:
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
            break
        entry = candidates[idx]
        attempted.add(entry.id)
        payload = upstream_body(body, entry, est_tokens, stream=False,
                                max_tokens_override=budget_override)
        budget_override = None
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
                                outcome="served", units=_r_in + _r_out)
                continue
            # Escalate once: next candidate at quality+1 (spec). Fallback
            # (found by live E2E, 2026-11-02): when no candidate reaches
            # quality+1 — e.g. a level-1 chat where the best lane-allowed
            # model is already quality 4 — retry the next candidate at
            # >= current quality instead of serving a known-invalid
            # answer; a different model rarely fails the same way.
            target_q = entry.quality_for(task_class) + 1
            esc_idx = next(
                (j for j in range(idx + 1, len(candidates))
                 if candidates[j].quality_for(task_class) >= target_q),
                None)
            if esc_idx is None:
                esc_idx = next(
                    (j for j in range(idx + 1, len(candidates))
                     if candidates[j].quality_for(task_class)
                     >= entry.quality_for(task_class)),
                    None)
            if esc_idx is not None:
                escalated = True
                last_failures = failures
                skipped.append({"model": entry.id,
                                "reason": "validation_failed:" + ",".join(failures)})
                _r_in, _r_out, _, _ = _usage_from_response(
                    data, est_tokens, entry)
                _record_attempt(app, record, client, entry,
                                outcome="served", units=_r_in + _r_out)
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
            cached_tokens=cached, cost_usd=cost, estimated=False))
        _record_attempt(app, record, client, entry,
                        outcome="served", units=in_tok + out_tok)
        app.state.sticky.set(conv_key, entry.id, level)
        app.state.governor.evaluate_alerts(
            app.state.clients.clients,
            {e.provider: e.spend_alert_usd for e in app.state.registry.entries
             if e.spend_alert_usd is not None})
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
        idx = 0
        finalized = False
        while True:
            if idx >= len(candidates):
                # Every candidate failed upstream: engage the paid
                # spillway once rather than ending the stream in a
                # 429 (v0.5.5; mirrors the JSON path's loop guard).
                if spillway_builder is not None and not spillway_engaged:
                    extra, extra_skipped = spillway_builder(
                        frozenset(attempted))
                    if extra:
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
                break
            entry = candidates[idx]
            attempted.add(entry.id)
            payload = upstream_body(body, entry, est_tokens, stream=True)
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
                        raw = await resp.aread()
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
                    async for line in resp.aiter_lines():
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
                            assembler.feed(chunk)
                            buffered.append(sse_line)
                            continue
                        # text path: commit at first content chunk
                        if is_text:
                            saw_content = True
                            content_chars += len(delta["content"])
                        committed = True
                        yield sse_line
                    record.latency_upstream_ms += (time.perf_counter() - t_u0) * 1000

                    if decided == "tool":
                        assembled = assembler.assembled_response()
                        failures = validate_response(
                            assembled, tools=tools or None, json_mode=False)
                        if failures and not escalated:
                            target_q = entry.quality_for(task_class) + 1
                            esc_idx = next(
                                (j for j in range(idx + 1, len(candidates))
                                 if candidates[j].quality_for(task_class) >= target_q),
                                None)
                            if esc_idx is None:
                                # Same fallback as the JSON path: next
                                # candidate at >= current quality beats
                                # serving a known-invalid tool call.
                                esc_idx = next(
                                    (j for j in range(idx + 1, len(candidates))
                                     if candidates[j].quality_for(task_class)
                                     >= entry.quality_for(task_class)),
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
                            _record_attempt(
                                app, record, client, entry,
                                outcome="served",
                                units=int(usage_data.get("prompt_tokens") or 0)
                                + int(usage_data.get("completion_tokens") or 0))
                            continue
                        for sse_line in buffered:
                            yield sse_line
                        yield "data: [DONE]\n\n"
                        in_tok = int(usage_data.get("prompt_tokens") or est_tokens)
                        out_tok = int(usage_data.get("completion_tokens") or 0)
                        cost = entry.estimate_cost(in_tok, out_tok)
                        _finalize_stream(app, record, entry, client, conv_key,
                                         level, task_class, in_tok, out_tok, cost,
                                         escalated or spillway_reason is not None)
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
                    in_tok = int(usage_data.get("prompt_tokens") or est_tokens)
                    out_tok = int(usage_data.get("completion_tokens") or
                                  max(1, content_chars // 4))
                    cost = entry.estimate_cost(in_tok, out_tok)
                    _finalize_stream(app, record, entry, client, conv_key,
                                     level, task_class, in_tok, out_tok, cost,
                                     escalated or spillway_reason is not None)
                    finalized = True
                    return
            except (httpx.TimeoutException, httpx.TransportError):
                record.latency_upstream_ms += (time.perf_counter() - t_u0) * 1000
                # v0.8.0: the attempt is recorded FIRST in this handler —
                # when the v0.7.0 commit guard merges in, its post-commit
                # return path must not bypass the ledger write.
                _record_attempt(app, record, client, entry,
                                outcome="failed", transport=True)
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
                     escalated: bool) -> None:
    record.model = entry.id
    record.lane = entry.lane
    record.escalated = escalated
    record.input_tokens, record.output_tokens = in_tok, out_tok
    record.cost_usd = cost
    record.status = 200
    record.latency_upstream_ms = round(record.latency_upstream_ms, 2)
    app.state.store.record_usage(UsageEvent(
        ts=time.time(), client_id=client.id, provider=entry.provider,
        model_id=entry.id, lane=entry.lane, level=level, task_class=task_class,
        input_tokens=in_tok, output_tokens=out_tok, cached_tokens=0,
        cost_usd=cost, estimated=False))
    _record_attempt(app, record, client, entry,
                    outcome="served", units=in_tok + out_tok)
    app.state.sticky.set(conv_key, entry.id, level)
    app.state.telemetry.write(record)


# Local/dev entry point:  uvicorn gateway:app
app = create_app() if os.environ.get("SOVEREIGN_DEV_SERVE") == "1" else None  # type: ignore
