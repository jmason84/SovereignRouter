"""Request pipeline: normalize -> scan -> profile -> classify -> sticky ->
select -> cost governor -> execute with failover -> validate/escalate."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from . import classifier
from .registry import DATA_CLASSES, data_rank
from .selector import REQUIRED_QUALITY, Profile, select
from .sensitivity import contains_secret, messages_text
from .sticky import conversation_key
from .upstream import Failover, Invalid, call_json, open_stream

route_log = logging.getLogger("sovereign.route")
ALIASES = {"auto", "cheap", "strong", "premium", "code", "long"}
ALLOWED_FIELDS = {"messages", "tools", "tool_choice", "temperature", "top_p", "max_tokens", "stop", "seed",
                  "response_format", "presence_penalty", "frequency_penalty", "parallel_tool_calls", "stream"}
MAX_ATTEMPTS = 6


class EngineError(Exception):
    def __init__(self, status: int, message: str, headers: dict | None = None, skips: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.headers, self.skips = status, message, headers or {}, skips or {}


@dataclass
class Ctx:
    policy: object
    body: dict
    stream: bool
    profile: Profile
    cls: classifier.Classification
    required: int
    key: str
    alias: str
    skips: dict = field(default_factory=dict)
    classify_ms: float = 0.0
    steered: bool = False


@dataclass
class Outcome:
    model: object
    headers: dict
    json: dict | None = None
    stream: object | None = None  # async generator of SSE payload strings


class Engine:
    def __init__(self, registry, store, http, governor, sticky, *, sidecar=None, default_max_tokens=2048,
                 max_tokens_cap=16384):
        self.registry, self.store, self.http = registry, store, http
        self.governor, self.sticky, self.sidecar = governor, sticky, sidecar
        self.default_max, self.max_cap = default_max_tokens, max_tokens_cap

    # --- 1-5: normalize, scan, profile, classify -----------------------------
    async def prepare(self, policy, raw: dict, headers: dict) -> Ctx:
        if self.store.is_killed(policy.name):
            raise EngineError(403, "This client has been stopped by the administrator.")
        if not isinstance(raw.get("messages"), list) or not raw["messages"]:
            raise EngineError(400, "'messages' must be a non-empty list.")
        name = str(raw.get("model") or "auto")
        name = policy.aliases.get(name, name)
        if name not in ALIASES:
            if "/" in name:
                raise EngineError(403, "Direct or provider-prefixed model selection is disabled.")
            name = "auto"  # real names like gpt-4o or claude-* are routed as auto
        if name == "premium" and not policy.premium:
            raise EngineError(403, "This client may not use the premium lane.")
        body = {k: v for k, v in raw.items() if k in ALLOWED_FIELDS}
        mt = raw.get("max_tokens") or raw.get("max_completion_tokens") or self.default_max
        body["max_tokens"] = max(1, min(int(mt), self.max_cap))
        msgs = body["messages"]

        data_class = policy.default_data_class
        hdr = headers.get("x-sovereign-data-class")
        if hdr in DATA_CLASSES and data_rank(hdr) > data_rank(data_class):
            data_class = hdr  # a header may raise sensitivity, never lower it
        key = conversation_key(policy.name, msgs)
        prior = self.sticky.get(key)
        if contains_secret(messages_text(msgs)) or (prior and prior.sensitive):
            data_class = "sensitive"

        size = len(json.dumps({"m": msgs, "t": body.get("tools")}))
        tools = body.get("tools") or []
        has_images = any(isinstance(m.get("content"), list) and
                         any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
                         for m in msgs)
        json_mode = (body.get("response_format") or {}).get("type") in ("json_object", "json_schema")
        profile = Profile(
            est_tokens=int(size / 3.5) + 1, max_tokens=body["max_tokens"], has_tools=bool(tools),
            has_images=has_images, json_mode=json_mode,
            tool_names={t.get("function", {}).get("name") for t in tools if isinstance(t, dict)},
            tool_loop=msgs[-1].get("role") == "tool", data_class=data_class,
            explicit_premium=(name == "premium"))

        last_user = next((m for m in reversed(msgs) if m.get("role") == "user"), {})
        text = last_user.get("content") if isinstance(last_user.get("content"), str) else \
            " ".join(p.get("text", "") for p in (last_user.get("content") or []) if isinstance(p, dict))
        hl = headers.get("x-sovereign-level")
        t0 = time.perf_counter()
        cls = await classifier.classify(
            text or "", sidecar=self.sidecar, alias=name if name != "auto" else None,
            header_level=int(hl) if hl and hl.isdigit() else None, has_images=has_images, json_mode=json_mode)
        if profile.tool_loop and prior and cls.layer not in ("header", "alias"):
            # mid tool loop the last *user* message is stale: keep the loop's level
            cls = classifier.Classification(max(prior.level, 1), cls.task, 1.0, "sticky", "tool loop")
        ctx = Ctx(policy, body, bool(body.get("stream")), profile, cls, REQUIRED_QUALITY[cls.level], key, name)
        ctx.classify_ms = (time.perf_counter() - t0) * 1000
        ctx.required, ctx.steered = self.governor.adjust_required_quality(policy, cls.level, ctx.required)
        return ctx

    # --- 6-10: select, execute, validate, escalate ---------------------------
    def _select(self, ctx: Ctx, required: int, tried: set):
        prior = self.sticky.get(ctx.key)
        return select(self.registry.usable(), ctx.policy, ctx.profile, ctx.cls.level, ctx.cls.task, required,
                      store=self.store, backstop_blocked=self.governor.paid_blocked_by_backstop(),
                      preferred=prior.model_id if prior else None, exclude=tried)

    async def execute(self, ctx: Ctx) -> Outcome:
        tried: set = set()
        required, escalated, attempts = ctx.required, False, 0
        last_sel = None
        restart = True
        while restart:
            restart = False
            sel = last_sel = self._select(ctx, required, tried)
            ctx.skips.update(sel.skips)
            for m in sel.ordered:
                if attempts >= MAX_ATTEMPTS:
                    break
                attempts += 1
                est_cost = m.est_cost(ctx.profile.est_tokens, ctx.profile.max_tokens)
                rid = self.store.reserve(m, ctx.policy.name, ctx.cls.level,
                                         ctx.profile.est_tokens + ctx.profile.max_tokens, est_cost)
                if rid is None:
                    ctx.skips[m.id] = "limit reached (race)"
                    continue
                t0 = time.perf_counter()
                try:
                    if ctx.stream:
                        sr = await open_stream(self.http, m, ctx.body, tool_names=ctx.profile.tool_names,
                                               json_mode=ctx.profile.json_mode)
                        return self._stream_outcome(ctx, m, sr, rid, est_cost, escalated, required, t0)
                    data, usage = await call_json(self.http, m, ctx.body, tool_names=ctx.profile.tool_names,
                                                  json_mode=ctx.profile.json_mode)
                except Failover as f:
                    self.store.cancel(rid)
                    tried.add(m.id)
                    ctx.skips[m.id] = f.reason
                    if f.status == 429:
                        self.store.set_cooldown(m.id, min(f.retry_after or 60, 300))
                    elif f.status in (401, 403):
                        self.store.set_cooldown(m.id, 300)
                    continue
                except Invalid as iv:
                    tried.add(m.id)
                    ctx.skips[m.id] = f"invalid answer: {iv.reason}"
                    await self._settle(ctx, m, rid, iv.usage, ctx.profile.est_tokens, 0.0)
                    if not escalated:
                        escalated, required = True, min(5, required + 1)
                        restart = True
                        break
                    continue
                cost = await self._settle(ctx, m, rid, usage, ctx.profile.est_tokens, est_cost)
                self._remember(ctx, m)
                self._log(ctx, m, usage, cost, t0, escalated)
                return Outcome(m, self._headers(ctx, m, est_cost, escalated), json=data)
        raise self._failure(ctx, last_sel)

    def _failure(self, ctx: Ctx, sel) -> EngineError:
        skips = dict(ctx.skips)
        if sel is None or not sel.ordered and sel.rate_limited == 0 and not skips:
            return EngineError(503, "No eligible model.", skips=skips)
        if sel.never_fit and sel.never_fit + sel.policy_blocked >= len(self.registry.usable()) - len(
                [s for s in skips.values() if s == "already tried"]) and sel.rate_limited == 0:
            return EngineError(413, "Request is too large for every eligible model.", skips=skips)
        if sel.policy_blocked >= len(self.registry.usable()):
            return EngineError(403, "No model is permitted for this client and data class.", skips=skips)
        return EngineError(429, "Every eligible model is rate-limited or failing.",
                           {"Retry-After": "60"}, skips)

    async def _settle(self, ctx, m, rid, usage, est_in, est_cost) -> float:
        if usage and (usage["in"] or usage["out"]):
            cost = m.est_cost(usage["in"], usage["out"], usage["cached"])
            self.store.finalize(rid, usage["in"] + usage["out"], cost)
        else:
            cost = est_cost
            self.store.finalize(rid, est_in, cost)
        if cost > 0:
            await self.governor.after_call(ctx.policy, m)
        return cost

    def _remember(self, ctx, m):
        self.sticky.put(ctx.key, m.id, ctx.cls.level, ctx.profile.data_class == "sensitive")

    def _headers(self, ctx, m, est_cost, escalated) -> dict:
        return {"X-Sovereign-Model": m.id, "X-Sovereign-Level": str(ctx.cls.level),
                "X-Sovereign-Task": ctx.cls.task, "X-Sovereign-Lane": m.lane,
                "X-Sovereign-Cost-Estimate": f"{est_cost:.6f}", "X-Sovereign-Escalated": str(escalated).lower()}

    def _stream_outcome(self, ctx, m, sr, rid, est_cost, escalated, required, t0) -> Outcome:
        async def gen():
            try:
                for p in sr.buffered:
                    yield p
                async for p in sr.rest:
                    yield p
            finally:
                usage = sr.usage_box or None
                cost = await self._settle(ctx, m, rid, usage, ctx.profile.est_tokens, est_cost)
                self._remember(ctx, m)
                self._log(ctx, m, usage, cost, t0, escalated)
        return Outcome(m, self._headers(ctx, m, est_cost, escalated), stream=gen())

    def _log(self, ctx, m, usage, cost, t0, escalated):
        """One JSON line per request. Never prompt or response text."""
        route_log.info(json.dumps({
            "event": "route", "ts": time.time(), "client": ctx.policy.name, "level": ctx.cls.level,
            "task": ctx.cls.task, "classifier": ctx.cls.layer, "confidence": ctx.cls.confidence,
            "reason": ctx.cls.reason, "model": m.id, "lane": m.lane, "paid": not m.is_free,
            "data_class": ctx.profile.data_class, "est_tokens": ctx.profile.est_tokens,
            "steered": ctx.steered, "escalated": escalated, "skipped": ctx.skips,
            "usage": usage, "cost": round(cost, 8), "tool_loop": ctx.profile.tool_loop,
            "classify_ms": round(ctx.classify_ms, 1), "upstream_ms": round((time.perf_counter() - t0) * 1000, 1)}))
