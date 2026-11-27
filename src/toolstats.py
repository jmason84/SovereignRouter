"""Production tool-error telemetry for selection (v0.10.2).

Owner ruling 2026-11-27 (the J-2-adjacent question, decided): measured
PRODUCTION tool-error rates may feed selection for tool-bearing turns.
This supersedes the measurement-only stance for this one signal; the
battery tooling scores are untouched — this is a separate, production
signal layered on selection, in the same spirit as the v0.8.2
stream-abort cooldown (bounded failure memory selection may read).

Why it exists — the 2026-11-27 diagnosis: Hermes' tool_error_next_turn
rate sat at ~24% pooled (72h) and did not move after F2/F3, because
F3 ranks on the battery tooling score and every major entry carries
tooling 5, while their measured production rates span 12.4%
(openrouter/nemotron-3-ultra-free) to 34.5% (novita/gpt-oss-120b),
with the gpt-oss-120b family at ~30% on every provider and
nemotron-3.5-lightning-free at 34.2%. The battery does not predict
production; the ledger does.

Signal: a tool turn is JUDGED when the conversation continues — the
next request's trailing tool results (the F2 signal,
``_latest_tool_error``) show whether the previous serve's tool calls
came back clean or in error. Each continuation judges exactly one
serve, attributed to the model that served it (the conversation's
sticky binding at arrival). Rates roll over a 72h window, matching
the diagnosis window.

Gates (both must hold before an entry is flagged):
  * MIN_SAMPLES = 50 judged turns in the window — the governor's
    level_mix precedent (F-14) is "never act on a thin sample" (n>=200
    for a daily fleet alert); per-entry tool turns run ~250/day for
    Hermes fleet-wide, so 50 is the scaled equivalent: an entry
    serving regularly accumulates it in well under the window, and a
    sporadic entry (e.g. cloudflare/gpt-oss-120b, 22 serves/72h at
    27.3%) stays unflagged until the evidence exists.
  * RATE_THRESHOLD = 0.25 — the diagnosis fleet spans 12.4% (best
    major entry) to 34.5%; Hermes pooled is ~24%. The bar sits just
    above the pooled rate, so it flags the offending cluster
    (26.9%–34.5% on the census method; 29.6%–40.0% on the
    sticky-continuation method this tracker measures) and leaves the
    12.4% entries untouched.

Effect (gateway.build_candidates, tool-bearing requests only): a
flagged entry is demoted within its cost tier — reordered behind
every non-flagged entry of the same tier. It is never removed and
never disabled: it still serves non-tool turns at its natural
position, still serves tool turns when the non-flagged candidates
are exhausted, and pins still reach it (J-1 unchanged). Free-first
is absolute: a flagged free entry still precedes every paid
candidate, so demotion can never push a turn to paid while a
non-flagged free entry exists.
"""

from __future__ import annotations

import time
from typing import Optional

from quota import QuotaStore

WINDOW_S = 72 * 3600.0
MIN_SAMPLES = 50
RATE_THRESHOLD = 0.25


class ToolErrorTracker:
    """Rolling per-entry production tool-error rates over the quota
    store's tool_outcomes ledger, with a short-lived flagged-set cache
    (selection runs several candidate builds per request; the rates
    move on a 72h window, so a 60s cache loses nothing). Recording an
    outcome invalidates the cache so tests and operators see fresh
    state on the next build."""

    CACHE_S = 60.0

    def __init__(self, store: QuotaStore,
                 window_s: float = WINDOW_S,
                 min_samples: int = MIN_SAMPLES,
                 rate_threshold: float = RATE_THRESHOLD,
                 cache_s: float = CACHE_S):
        self.store = store
        self.window_s = window_s
        self.min_samples = min_samples
        self.rate_threshold = rate_threshold
        self.cache_s = cache_s
        self._flagged_cache: Optional[set[str]] = None
        self._flagged_at = 0.0

    def record(self, model_id: str, had_error: bool,
               now: Optional[float] = None) -> None:
        now = now or time.time()
        self.store.record_tool_outcome(
            model_id, had_error, ts=now,
            prune_before=now - self.window_s)
        self._flagged_cache = None

    def stats(self, now: Optional[float] = None) -> dict[str, dict]:
        """{model_id: {samples, errors, rate}} over the rolling
        window ending at ``now``."""
        now = now or time.time()
        raw = self.store.tool_outcome_stats(now - self.window_s, now + 1)
        return {
            model: {"samples": v["samples"], "errors": v["errors"],
                    "rate": (v["errors"] / v["samples"]) if v["samples"]
                    else 0.0}
            for model, v in raw.items()
        }

    def flagged(self, now: Optional[float] = None) -> set[str]:
        """Entries whose production tool-error rate is egregious:
        >= RATE_THRESHOLD on >= MIN_SAMPLES judged turns in the
        window."""
        now = now or time.time()
        if self._flagged_cache is not None \
                and now - self._flagged_at < self.cache_s:
            return set(self._flagged_cache)
        flagged = {
            model for model, v in self.stats(now).items()
            if v["samples"] >= self.min_samples
            and v["rate"] >= self.rate_threshold
        }
        self._flagged_cache = flagged
        self._flagged_at = now
        return set(flagged)
