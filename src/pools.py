"""Pool model, failure taxonomy, and observe-only demotion (v0.8.0).

Cycle 1 aspect E, release R2 — the DATA LAYER only. Three pieces:

1. **Pool model.** Scarce quota is rarely per-model: OpenRouter's
   `:free` routes share ONE account pool (20 RPM / 1,000 RPD, NEW-01),
   Cloudflare's routes share one 10,000-Neuron/day account pool,
   Google's free quotas are per-model buckets, Groq's enforced windows
   are per-model token ceilings. ``pool_for`` maps every registry entry
   to exactly one pool, derived programmatically from provider + model
   string (the registry this ships against predates explicit pool
   ids). Each pool declares its ``quota_unit``: requests, tokens, or
   neurons.

2. **Failure taxonomy.** Every upstream failure classifies into
   ``account_dead`` (401/402/403), ``congested`` (429), ``transient``
   (5xx / timeout / transport / unparseable), ``request_fault``
   (400/413/422 — the request's fault, never counted against the
   provider), or ``entry_missing`` (404 — that entry only), and into a
   SCOPE: model, pool, account, or request. Scope is what the old
   system lacked: on 2026-11-09 one dead account absorbed 369 attempts
   because nothing remembered the failure was account-shaped.

   OpenRouter subclassification (handoff-evaluation amendment): a 402
   whose body carries ``limit_source`` = in-flight budget is a
   TRANSIENT concurrency limit (it carries Retry-After), NOT account
   death. It is subclassified before any account-scope reaction.

3. **Demotion, observe-only.** ``DemotionTracker`` consumes classified
   attempts and computes what selection WOULD demote (scope, target,
   until when) — and writes it down as annotations. **Nothing in
   selection reads this in v0.8.0.** Whether demotion may ever enforce
   is decision J-2, which remains the owner's. ``reset_at`` is
   OBSERVED data where observation exists: Cloudflare's documented
   00:00 UTC reset did NOT restore its pool on 2026-11-12/13 (still
   exhausted at 00:01 and 03:25 UTC), so the Cloudflare pool demotes
   "until a successful probe" (reset_source=observed_divergent)
   instead of trusting the documented midnight.

No prompt or response text is stored anywhere in this module: the
classifier pattern-matches a bounded error-body excerpt for signal
phrases and keeps only the resulting class/scope.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from registry import ModelEntry

# ----------------------------------------------------------------------
# pool model
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Pool:
    id: str
    unit: str      # requests | tokens | neurons
    kind: str      # account_pool | account_class | per_model
    provider: str


def pool_for(entry: ModelEntry) -> Pool:
    """The one pool an entry draws from. Derived from the taxonomy in
    the Cycle 1 evaluations; every entry maps to exactly one pool."""
    p = entry.provider
    if p == "openrouter":
        if entry.model.endswith(":free") or entry.id == "openrouter/free-router":
            # NEW-01: the :free models AND the virtual router draw on
            # the one account-level free pool.
            return Pool("openrouter-free", "requests", "account_pool", p)
        return Pool("openrouter-paid", "requests", "account_class", p)
    if p == "cloudflare":
        return Pool("cloudflare-workers-ai", "neurons", "account_pool", p)
    if p == "google":
        # Google's free quotas are per-model buckets — real, separate
        # capacity per model.
        return Pool(f"google:{entry.model}", "requests", "per_model", p)
    if p == "groq":
        # Groq's enforced ceilings are per-model token windows.
        return Pool(f"groq:{entry.model}", "tokens", "per_model", p)
    return Pool(f"{p}:{entry.model}", "requests", "per_model", p)


# Daily unit budgets where a free pool has a hard account-level cap.
POOL_UNIT_BUDGETS: dict[str, float] = {
    "cloudflare-workers-ai": 10_000.0,   # neurons/day, Workers Free plan
}

# Pool reset policy. "documented" pools demote until the next daily
# reset (00:00 UTC). "observed_divergent" pools demote until a
# successful probe, because observation contradicted the documentation.
POOL_RESET_POLICY: dict[str, str] = {
    "openrouter-free": "documented",
    "cloudflare-workers-ai": "observed_divergent",
}

PROBE_INTERVAL_S = 300.0  # min seconds before a demoted scope admits a probe


def _next_utc_midnight(now: float) -> float:
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    nxt = (dt + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                           microsecond=0)
    return nxt.timestamp()


def _day_key(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


# ----------------------------------------------------------------------
# failure taxonomy
# ----------------------------------------------------------------------

FAILURE_CLASSES = ("account_dead", "congested", "transient",
                   "request_fault", "entry_missing")
SCOPES = ("model", "pool", "account", "request")


@dataclass
class ClassifiedFailure:
    failure_class: str
    scope: str            # model | pool | account | request
    target: str           # entry id | pool id | provider | "-"
    retry_after_s: Optional[float] = None


def _retry_after(headers: Any) -> Optional[float]:
    if not headers:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None  # HTTP-date form: not parsed in this release
    return min(max(value, 1.0), 3600.0)


def _openrouter_in_flight(body: str) -> bool:
    return "limit_source" in body and ("in_flight" in body or "in-flight" in body)


def _pool_exhaustion_signal(entry: ModelEntry, pool: Pool, body: str) -> bool:
    """A 429 that implicates the whole pool, not just this model:
    Cloudflare's error 4006 (daily free allocation exhausted) and
    OpenRouter's per-day free-pool limit phrasing are the observed
    shapes. Bare 429s stay model-scoped (TEST-03)."""
    if entry.provider == "cloudflare" and "4006" in body:
        return True
    if pool.kind == "account_pool":
        low = body.lower()
        return ("free-models" in low or "per-day" in low
                or "per day" in low or "daily limit" in low
                or "daily free allocation" in low)
    return False


def classify_failure(*, entry: ModelEntry, status: Optional[int],
                     body_text: str = "", headers: Any = None,
                     transport: bool = False) -> ClassifiedFailure:
    """Classify one failed upstream attempt (aspect E.3.1)."""
    pool = pool_for(entry)
    retry = _retry_after(headers)
    if transport or status is None:
        return ClassifiedFailure("transient", "model", entry.id)
    if status in (400, 413, 422):
        # The request's fault (shape/size/params): never counts
        # against the provider at any scope.
        return ClassifiedFailure("request_fault", "request", "-")
    if status == 404:
        return ClassifiedFailure("entry_missing", "model", entry.id)
    if status in (401, 403):
        return ClassifiedFailure("account_dead", "account", entry.provider)
    if status == 402:
        if entry.provider == "openrouter" and _openrouter_in_flight(body_text):
            # In-flight budget 402: transient concurrency accounting,
            # subclassified BEFORE any account-scope reaction.
            return ClassifiedFailure("transient", "model", entry.id,
                                     retry_after_s=retry)
        return ClassifiedFailure("account_dead", "account", entry.provider)
    if status == 429:
        if _pool_exhaustion_signal(entry, pool, body_text):
            return ClassifiedFailure("congested", "pool", pool.id,
                                     retry_after_s=retry)
        return ClassifiedFailure("congested", "model", entry.id,
                                 retry_after_s=retry)
    # 5xx and anything unmapped: transient, model scope.
    return ClassifiedFailure("transient", "model", entry.id)


# ----------------------------------------------------------------------
# demotion tracker (observe-only)
# ----------------------------------------------------------------------

_BASE_COOLDOWNS = {"account": 3600.0, "pool": 900.0, "model": 60.0}
_TRANSIENT_COOLDOWN_S = 30.0
_ENTRY_MISSING_COOLDOWN_S = 3600.0
_MAX_COOLDOWN_S = 4 * 3600.0


@dataclass
class _ScopeState:
    state: str = "closed"                 # closed | demoted | half_open
    demoted_until: Optional[float] = None
    probe_after: float = 0.0
    cooldown_s: float = 0.0
    failures: int = 0
    failure_class: str = ""
    probe_in_flight: bool = False


@dataclass
class DemotionTracker:
    """Failure memory that computes would_demote annotations.

    OBSERVE-ONLY in v0.8.0: selection never consults this tracker.
    State is in-memory (a gateway restart forgets demotions — the
    durable record is the attempt ledger in the quota store).
    """

    _scopes: dict[tuple[str, str], _ScopeState] = field(default_factory=dict)
    _pool_day_units: dict[tuple[str, str], float] = field(default_factory=dict)

    # -- internal helpers ------------------------------------------------
    def _annotation(self, scope: str, target: str, failure_class: str,
                    state: _ScopeState) -> dict[str, Any]:
        reset_source = "cooldown"
        if scope == "pool":
            reset_source = POOL_RESET_POLICY.get(target, "cooldown")
            if reset_source == "documented":
                reset_source = "documented_daily_reset"
            elif reset_source == "observed_divergent":
                reset_source = "observed_divergent"
        return {
            "action": "would_demote",
            "scope": scope,
            "target": target,
            "failure_class": failure_class,
            "until": state.demoted_until,
            "reset_source": reset_source,
        }

    def _demote(self, scope: str, target: str, failure: ClassifiedFailure,
                now: float) -> dict[str, Any]:
        key = (scope, target)
        st = self._scopes.setdefault(key, _ScopeState())
        was = st.state
        st.failures += 1
        st.failure_class = failure.failure_class
        if failure.failure_class == "account_dead":
            cooldown = _BASE_COOLDOWNS["account"]
        elif failure.failure_class == "entry_missing":
            cooldown = _ENTRY_MISSING_COOLDOWN_S
        elif failure.failure_class == "transient":
            cooldown = _TRANSIENT_COOLDOWN_S
        elif scope == "pool":
            cooldown = _BASE_COOLDOWNS["pool"]
        else:
            cooldown = failure.retry_after_s or _BASE_COOLDOWNS["model"]
        if was == "half_open":
            # A failed probe re-demotes with a doubled cooldown.
            cooldown = min(max(cooldown, st.cooldown_s * 2), _MAX_COOLDOWN_S)
        st.cooldown_s = cooldown
        if scope == "pool" and \
                POOL_RESET_POLICY.get(target) == "documented":
            st.demoted_until = _next_utc_midnight(now)
        elif scope == "pool" and \
                POOL_RESET_POLICY.get(target) == "observed_divergent":
            # Documented reset is known NOT to hold: demote until a
            # successful probe, admitted after the probe interval.
            st.demoted_until = None
        else:
            st.demoted_until = now + cooldown
        st.probe_after = now + min(cooldown, PROBE_INTERVAL_S) \
            if st.demoted_until is None else min(st.demoted_until,
                                                 now + PROBE_INTERVAL_S)
        st.state = "demoted"
        st.probe_in_flight = False
        return self._annotation(scope, target, failure.failure_class, st)

    # -- public API --------------------------------------------------------
    def observe(self, *, entry: ModelEntry,
                failure: Optional[ClassifiedFailure], now: float,
                units: float = 0.0) -> list[dict[str, Any]]:
        """Record one attempt outcome; return would_demote annotations
        this attempt produced (possibly empty). ``units`` is the
        attempt's consumption in the entry pool's unit (for neuron
        pools: input+output tokens as the neuron estimate)."""
        pool = pool_for(entry)
        annotations: list[dict[str, Any]] = []

        if pool.unit == "neurons" and units > 0:
            key = (pool.id, _day_key(now))
            total = self._pool_day_units.get(key, 0.0) + units
            self._pool_day_units[key] = total
            budget = POOL_UNIT_BUDGETS.get(pool.id)
            if budget is not None and total >= budget:
                existing = self._scopes.get(("pool", pool.id))
                if existing is None or existing.state == "closed":
                    pseudo = ClassifiedFailure("congested", "pool", pool.id)
                    annotations.append(
                        self._demote("pool", pool.id, pseudo, now))

        if failure is None:
            # A serve is a successful probe for every demoted scope the
            # entry belongs to: close them (half-open bookkeeping).
            for key in (("model", entry.id), ("pool", pool.id),
                        ("account", entry.provider)):
                st = self._scopes.get(key)
                if st is not None and st.state in ("demoted", "half_open"):
                    st.state = "closed"
                    st.failures = 0
                    st.demoted_until = None
                    st.probe_in_flight = False
            return annotations

        if failure.scope == "request":
            return annotations  # request faults never demote anything
        annotations.append(
            self._demote(failure.scope, failure.target, failure, now))
        return annotations

    def state_for(self, scope: str, target: str, now: float) -> str:
        st = self._scopes.get((scope, target))
        if st is None:
            return "closed"
        if st.state == "demoted" and now >= st.probe_after:
            return "half_open"
        return st.state

    def admit_probe(self, scope: str, target: str, now: float) -> bool:
        """Half-open admission: once the demotion's probe point passes,
        exactly one probe attempt is admitted; its outcome (via
        ``observe``) closes or re-demotes the scope."""
        st = self._scopes.get((scope, target))
        if st is None or st.state == "closed":
            return True  # not demoted: ordinary traffic, not a probe
        if st.state == "demoted" and now >= st.probe_after:
            st.state = "half_open"
            st.probe_in_flight = True
            return True
        if st.state == "half_open" and not st.probe_in_flight:
            st.probe_in_flight = True
            return True
        return False

    def would_demote(self, now: float) -> list[dict[str, Any]]:
        """All currently-active would_demote annotations."""
        out = []
        for (scope, target), st in sorted(self._scopes.items()):
            if st.state in ("demoted", "half_open"):
                out.append(self._annotation(scope, target,
                                            st.failure_class, st))
        return out

    def demoted_entry_ids(self, entries: list[ModelEntry],
                          now: float) -> set[str]:
        """Expand active demotions to the entry ids they would cover
        (pool demotions expand to every pool member — including
        inherited members like openrouter/free-router)."""
        out: set[str] = set()
        for ann in self.would_demote(now):
            if ann["scope"] == "model":
                out.add(ann["target"])
            elif ann["scope"] == "account":
                out.update(e.id for e in entries
                           if e.provider == ann["target"])
            elif ann["scope"] == "pool":
                out.update(e.id for e in entries
                           if pool_for(e).id == ann["target"])
        return out

    def pool_day_units(self, pool_id: str, now: float) -> float:
        return self._pool_day_units.get((pool_id, _day_key(now)), 0.0)
