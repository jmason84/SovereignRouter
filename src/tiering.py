"""Per-class tiered ranking — SHADOW ONLY (v0.10.6, Job C).

Owner go 2026-11-27 20:25 MST ("Do B C D"); policy reading 2026-11-27
17:26 MST: "best" means best for the request type and the tools or
capability it needs. Per task class: the best FREE model that can do
the work, then the best CHEAP model, then PREMIUM only when the request
requires it. The sensitivity wall stays hard.

This module never changes live routing. The gateway calls ``rank`` on
the live, already-walled candidate list (lanes, sensitive wall, premium
gate, tools support, quality floor, context, limits and cooldowns are
all applied by ``build_candidates`` first) and logs what the tiered
policy WOULD pick next to what live picked. Because the input is the
walled list, the shadow pick can never be a model the wall excludes.

Ranking key, within the quality floor:
    1. tier            free (0) < cheap (1) < premium (2)
    2. preferred       owner-named specialist for the class, if eligible
    3. score           measured capability for the class (tool turns:
                       min(tooling, class quality), the live definition),
                       less a production tool-error penalty on tool turns
    4. price           estimated USD for this turn
    5. latency         registry p50

Capability tags: ``tools`` (request carries tools), ``vision`` (request
carries images: entries without supports_vision are ineligible),
``structured-output`` (json_object / json_schema: recorded; no registry
flag exists yet, so it does not filter).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from registry import FREE_LANES, ModelEntry

TASK_CLASSES = ("chat", "infra", "code", "reasoning", "long-context")
CAPABILITY_TAGS = ("tools", "vision", "structured-output")
TIERS = ("free", "cheap", "premium")

# Owner defaults 2026-11-27 (unanswered question 5 -> defaults): the
# free coder is Codestral on UnoRouter, the cheap coder Qwen3 Coder 480B.
# Applied only when the entry is already eligible for the turn.
CLASS_PREFERRED: dict[str, dict[str, tuple[str, ...]]] = {
    "code": {
        "free": ("unorouter/codestral-latest",),
        # Order is the preference order (v0.10.7): the owner-default
        # nrouter coder, then DeepInfra's Qwen3-Coder-480B Turbo (direct
        # account, verified $0.30/$1.00), then the HF-billed copy as the
        # backup coder (separate billing pool; non-sensitive only). The
        # last two ship disabled, so the order only matters once Jordan
        # enables them; an ineligible entry is simply skipped.
        "cheap": ("nrouter/qwen3-coder-480b-a35b",
                  "deepinfra/qwen3-coder-480b-turbo",
                  "huggingface/qwen3-coder-480b"),
    },
}

# Production tool-error penalty on tool turns (initial heuristic; Job D
# proposes tuning). Needs MIN_TOOL_SAMPLES judged turns in the window.
MIN_TOOL_SAMPLES = 20
TOOL_ERR_PENALTY_STEPS = (0.15, 0.30)   # -1 at >=15%, -2 at >=30%

LOW_CONFIDENCE = 0.6


def tier_of(entry: ModelEntry) -> str:
    if entry.lane == "premium":
        return "premium"
    if entry.lane in FREE_LANES and entry.input_usd_per_million == 0 \
            and entry.output_usd_per_million == 0:
        return "free"
    return "cheap"


def capability_tags(*, has_tools: bool, has_images: bool,
                    json_mode: bool) -> list[str]:
    tags = []
    if has_tools:
        tags.append("tools")
    if has_images:
        tags.append("vision")
    if json_mode:
        tags.append("structured-output")
    return tags


@dataclass
class Ranked:
    entry: ModelEntry
    tier: str
    score: int
    penalty: int
    preferred: bool
    cost: float

    def brief(self) -> dict[str, Any]:
        return {"model": self.entry.id, "tier": self.tier,
                "score": self.score - self.penalty,
                "preferred": self.preferred,
                "cost_usd": round(self.cost, 6)}


@dataclass
class ShadowDecision:
    pick: Optional[str]
    pick_tier: Optional[str]
    live_pick: Optional[str]
    live_tier: Optional[str]
    agree: bool
    task_class: str
    tags: list[str] = field(default_factory=list)
    low_confidence: bool = False
    reason: str = ""
    top: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"pick": self.pick, "pick_tier": self.pick_tier,
                "live_pick": self.live_pick, "live_tier": self.live_tier,
                "agree": self.agree, "task_class": self.task_class,
                "tags": self.tags, "low_confidence": self.low_confidence,
                "reason": self.reason, "top": self.top}


def _penalty(rate_info: Optional[dict]) -> int:
    if not rate_info or rate_info.get("samples", 0) < MIN_TOOL_SAMPLES:
        return 0
    rate = float(rate_info.get("rate") or 0.0)
    return sum(1 for step in TOOL_ERR_PENALTY_STEPS if rate >= step)


def rank(candidates: Iterable[ModelEntry], *, task_class: str,
         has_tools: bool, has_images: bool,
         quality_fn: Callable[[ModelEntry, str, bool], int],
         est_tokens: int, max_tokens: int,
         tool_stats: Optional[dict[str, dict]] = None) -> list[Ranked]:
    prefs = CLASS_PREFERRED.get(task_class, {})
    out: list[Ranked] = []
    for e in candidates:
        if has_images and not e.supports_vision:
            continue
        tier = tier_of(e)
        out.append(Ranked(
            entry=e, tier=tier,
            score=int(quality_fn(e, task_class, has_tools)),
            penalty=_penalty((tool_stats or {}).get(e.id)) if has_tools else 0,
            preferred=e.id in prefs.get(tier, ()),
            cost=e.estimate_cost(est_tokens, max_tokens // 2)))

    def key(r: Ranked) -> tuple:
        pref_rank = 0
        if r.preferred:
            pref_rank = -1 - (len(prefs.get(r.tier, ()))
                              - prefs[r.tier].index(r.entry.id))
        return (TIERS.index(r.tier), pref_rank, -(r.score - r.penalty),
                r.cost, r.entry.latency_p50_ms or 99_999, r.entry.id)

    out.sort(key=key)
    return out


def decide(candidates: list[ModelEntry], *, task_class: str,
           has_tools: bool, has_images: bool, json_mode: bool,
           quality_fn: Callable[[ModelEntry, str, bool], int],
           est_tokens: int, max_tokens: int,
           classifier_confidence: float,
           tool_stats: Optional[dict[str, dict]] = None,
           reason: str = "") -> ShadowDecision:
    tags = capability_tags(has_tools=has_tools, has_images=has_images,
                           json_mode=json_mode)
    ranked = rank(candidates, task_class=task_class, has_tools=has_tools,
                  has_images=has_images, quality_fn=quality_fn,
                  est_tokens=est_tokens, max_tokens=max_tokens,
                  tool_stats=tool_stats)
    live = candidates[0] if candidates else None
    pick = ranked[0] if ranked else None
    return ShadowDecision(
        pick=pick.entry.id if pick else None,
        pick_tier=pick.tier if pick else None,
        live_pick=live.id if live else None,
        live_tier=tier_of(live) if live else None,
        agree=(pick is not None and live is not None
               and pick.entry.id == live.id),
        task_class=task_class, tags=tags,
        low_confidence=classifier_confidence < LOW_CONFIDENCE,
        reason=reason or ("no_eligible_candidate" if pick is None else ""),
        top=[r.brief() for r in ranked[:3]])


class ToolStatsCache:
    """60s cache over ToolErrorTracker.stats() so the shadow ranker does
    not hit the quota DB on every request."""

    def __init__(self, tracker: Any, ttl_s: float = 60.0) -> None:
        self.tracker = tracker
        self.ttl_s = ttl_s
        self._at = 0.0
        self._val: dict[str, dict] = {}

    def get(self, now: Optional[float] = None) -> dict[str, dict]:
        now = now or time.time()
        if now - self._at > self.ttl_s:
            try:
                self._val = self.tracker.stats(now)
            except Exception:  # noqa: BLE001 — shadow must never break serving
                self._val = {}
            self._at = now
        return self._val
