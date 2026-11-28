"""Burst spreading across equally-ranked candidates (v0.10.11).

ROUTER-STRESS-20261128 found a herd effect: every concurrent turn
builds the identical cost-ordered candidate list, so a burst lands on
the same one or two models, they 429 together, and the v0.10.5 depth
cap (6 upstream calls per turn) runs out before the list reaches a
model with spare capacity. v0.10.3 hid this by walking up to ~77
models per turn.

This module keeps per-process load signals and uses them to reorder
candidates *within a tier* only:

* ``inflight[model]``: upstream calls currently open on the model.
* ``cap[model]``: a learned concurrency ceiling. An upstream 429/503
  that arrives while OTHER calls are open on the model is a concurrency
  limit: the ceiling becomes that number of other calls, and the model
  counts as *saturated* while ``inflight >= cap``. The ceiling grows by
  one after a success at the ceiling once ``cap_grow_after_s`` has
  passed without a new 429, and is forgotten ``cap_ttl_s`` after the
  last 429 (additive-increase, reset-on-limit).
* ``cooldown_until[model]``: a 429/503 with NO other call open is a
  rate limit, not a concurrency one: the model cools for a few seconds
  (Retry-After honoured, capped).

Ordering rules (``reorder``):

1. Tier order never changes: free lanes, then paid, then premium (and
   the v0.10.2 tool-flag bit inside a tier stays in front of the cost
   bucket). Nothing moves across a tier boundary, so free-first, the
   sensitivity wall (applied before ordering), the no-premium-on-
   congestion rule (applied after) and pins/sticky (applied by the
   caller after ordering) are unaffected.
2. Inside a tier, saturated entries go behind the unsaturated ones
   (least over-subscribed first) and cooled entries go to the end of
   the tier, each group keeping its relative order. They are not removed: when every entry of the
   tier is cooling they are still tried, so a cooldown can never turn
   a servable turn into a 429.
3. Inside a *band* (same tier, same selection quality, cost within
   ``band_rel`` of the band leader) the non-cooled entries are ordered
   by in-flight count, ties broken by a seedable RNG — but only while
   the band is contended (some member has in-flight calls). With no
   load the original deterministic order is kept, so single-turn
   behaviour (cheapest, best quality, lowest latency first) is exactly
   the pre-v0.10.11 order.

Counters are per gateway process (the live container runs one uvicorn
worker). They are hints, never correctness: losing them is harmless.
"""
from __future__ import annotations

import os
import random
import time
from typing import Any, Callable, Iterable, Optional, Sequence

FREE_LANES = ("private-free", "public-only-free")
COOLDOWN_STATUSES = frozenset({429, 503})


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def tier_of(lane: str) -> int:
    if lane in FREE_LANES:
        return 0
    if lane == "premium":
        return 2
    return 1


class LoadSpreader:
    def __init__(self, *, enabled: Optional[bool] = None,
                 cooldown_s: Optional[float] = None,
                 max_cooldown_s: Optional[float] = None,
                 band_rel: Optional[float] = None,
                 seed: Optional[int] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.enabled = (os.environ.get("SOVEREIGN_SPREAD", "1") != "0"
                        if enabled is None else bool(enabled))
        self.cooldown_s = _env_float("SOVEREIGN_SPREAD_COOLDOWN_S", 3.0) \
            if cooldown_s is None else float(cooldown_s)
        self.max_cooldown_s = _env_float("SOVEREIGN_SPREAD_MAX_COOLDOWN_S", 30.0) \
            if max_cooldown_s is None else float(max_cooldown_s)
        self.band_rel = _env_float("SOVEREIGN_SPREAD_BAND_REL", 0.5) \
            if band_rel is None else float(band_rel)
        self.cap_ttl_s = _env_float("SOVEREIGN_SPREAD_CAP_TTL_S", 120.0)
        self.cap_grow_after_s = _env_float("SOVEREIGN_SPREAD_CAP_GROW_S", 10.0)
        self.cap: dict[str, int] = {}
        self.cap_at: dict[str, float] = {}
        if seed is None:
            env_seed = os.environ.get("SOVEREIGN_SPREAD_SEED")
            seed = int(env_seed) if env_seed and env_seed.lstrip("-").isdigit() else None
        self.rng = random.Random(seed)
        self.clock = clock
        self.inflight: dict[str, int] = {}
        self.cooldown_until: dict[str, float] = {}

    # -- signals -------------------------------------------------------
    def acquire(self, model_id: str) -> None:
        self.inflight[model_id] = self.inflight.get(model_id, 0) + 1

    def release(self, model_id: str) -> None:
        n = self.inflight.get(model_id, 0) - 1
        if n > 0:
            self.inflight[model_id] = n
        else:
            self.inflight.pop(model_id, None)

    def note_status(self, model_id: str, status: Optional[int],
                    headers: Any = None, *, self_inflight: bool = False) -> None:
        """Record a failed attempt's status. ``self_inflight`` says
        whether the failing call is still counted in ``inflight``."""
        if not self.enabled or status not in COOLDOWN_STATUSES:
            return
        now = self.clock()
        others = self.inflight.get(model_id, 0) - (1 if self_inflight else 0)
        if others > 0:
            # Concurrency limit: remember the ceiling, no cooldown.
            self.cap[model_id] = max(1, others)
            self.cap_at[model_id] = now
            return
        secs = self.cooldown_s
        try:
            ra = headers.get("retry-after") if headers is not None else None
            if ra is not None:
                secs = max(secs, float(ra))
        except (TypeError, ValueError, AttributeError):
            pass
        secs = min(secs, self.max_cooldown_s)
        until = now + secs
        if until > self.cooldown_until.get(model_id, 0.0):
            self.cooldown_until[model_id] = until

    def note_ok(self, model_id: str, concurrent: int) -> None:
        """A call succeeded while ``concurrent`` calls (itself included)
        were open on the model: grow a learned ceiling it has reached."""
        cap = self.cap.get(model_id)
        if cap is None:
            return
        now = self.clock()
        if now - self.cap_at.get(model_id, now) >= self.cap_grow_after_s \
                and concurrent >= cap:
            self.cap[model_id] = cap + 1
            self.cap_at[model_id] = now

    def saturated(self, model_id: str, now: Optional[float] = None) -> bool:
        cap = self.cap.get(model_id)
        if cap is None:
            return False
        now = self.clock() if now is None else now
        if now - self.cap_at.get(model_id, now) > self.cap_ttl_s:
            self.cap.pop(model_id, None)
            self.cap_at.pop(model_id, None)
            return False
        return self.inflight.get(model_id, 0) >= cap

    def _over(self, model_id: str) -> int:
        return self.inflight.get(model_id, 0) - self.cap.get(model_id, 0)

    def cooled(self, model_id: str, now: Optional[float] = None) -> bool:
        until = self.cooldown_until.get(model_id)
        if until is None:
            return False
        if until <= (self.clock() if now is None else now):
            self.cooldown_until.pop(model_id, None)
            return False
        return True

    # -- ordering ------------------------------------------------------
    def reorder(self, candidates: Sequence[Any], *,
                tier_key: Callable[[Any], Any],
                quality: Callable[[Any], int],
                cost: Callable[[Any], float]) -> tuple[list[Any], list[str]]:
        """Return (new order, ids moved back because cooling).
        ``candidates`` must already be in the caller's sorted order and
        ``tier_key`` must be monotone over it."""
        cands = list(candidates)
        if not self.enabled or len(cands) < 2:
            return cands, []
        now = self.clock()
        out: list[Any] = []
        cooled_ids: list[str] = []
        i = 0
        while i < len(cands):
            t = tier_key(cands[i])
            j = i
            while j < len(cands) and tier_key(cands[j]) == t:
                j += 1
            tier = cands[i:j]
            cold = [c for c in tier if self.cooled(c.id, now)]
            rest = [c for c in tier if not self.cooled(c.id, now)]
            full = [c for c in rest if self.saturated(c.id, now)]
            hot = [c for c in rest if not self.saturated(c.id, now)]
            # least over-subscribed first; stable within equal values
            full.sort(key=lambda c: self._over(c.id))
            out.extend(self._spread_bands(hot, quality, cost))
            out.extend(full)
            out.extend(cold)
            if hot or full:
                cooled_ids.extend(c.id for c in cold)
            i = j
        return out, cooled_ids

    def _spread_bands(self, tier: list[Any], quality: Callable[[Any], int],
                      cost: Callable[[Any], float]) -> list[Any]:
        out: list[Any] = []
        k = 0
        while k < len(tier):
            lead = tier[k]
            q0, c0 = quality(lead), cost(lead)
            m = k + 1
            while m < len(tier) and quality(tier[m]) == q0 and \
                    cost(tier[m]) <= c0 * (1.0 + self.band_rel) + 1e-12:
                m += 1
            band = tier[k:m]
            if len(band) > 1 and any(self.inflight.get(c.id, 0) for c in band):
                keyed = [(self.inflight.get(c.id, 0), self.rng.random(), n, c)
                         for n, c in enumerate(band)]
                keyed.sort(key=lambda x: (x[0], x[1], x[2]))
                band = [x[3] for x in keyed]
            out.extend(band)
            k = m
        return out

    def promote_uncooled(self, candidates: list[Any], idx: int,
                         tier_key: Callable[[Any], Any],
                         attempted: Iterable[str]) -> Optional[str]:
        """Attempt-time check: if candidates[idx] started cooling (or is
        saturated) after selection and a later, unattempted, uncooled entry exists in the
        SAME tier, move that entry to ``idx`` (the cooled one shifts one
        place back). Returns the id moved back, or None. Never crosses a
        tier, never drops an entry, spends no attempt budget."""
        if not self.enabled or idx >= len(candidates):
            return None
        cur = candidates[idx]
        if not (self.cooled(cur.id) or self.saturated(cur.id)):
            return None
        done = set(attempted)
        t = tier_key(cur)
        for j in range(idx + 1, len(candidates)):
            c = candidates[j]
            if tier_key(c) != t:
                break
            if c.id in done or self.cooled(c.id) or self.saturated(c.id):
                continue
            candidates.insert(idx, candidates.pop(j))
            return cur.id
        return None

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "enabled": self.enabled,
            "inflight": dict(self.inflight),
            "cooling": sorted(m for m, u in self.cooldown_until.items() if u > now),
            "caps": dict(self.cap),
        }
