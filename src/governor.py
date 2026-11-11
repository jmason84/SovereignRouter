"""Cost governor — steering and alerts, never a cap.

Spec ("Cost governor"), with the owner's decision recorded 2026-10-31:
**spending is not capped; a request is never refused for budget reasons.**
The governor instead:

  * steers model choice per client: above the client's daily target,
    levels 1-3 drop one required quality level, level 4 is held to the
    cheapest quality-4 model, and premium requires an explicit request
    — plus one alert that day;
  * alerts on the level mix (>40% of a client's daily requests at
    level 4+5 usually means the classifier is over-assigning);
  * fires the hourly runaway alarm — first 14 days on fixed thresholds
    (paid spend > $0.50/hr, or a client > 2x its daily target), after
    that when an hour exceeds 5x the trailing 7-day hourly average
    with a $1 minimum;
  * fires slow-leak alerts: projected-month spend above the sum of the
    providers' monthly alert levels, and a 3-day rising trend ending
    above a client's target;
  * tracks per-provider monthly spend alerts (DeepInfra $10, Ollama $5)
    and OpenClaw's premium alerts ($20/month, >20 premium req/day);
  * holds the optional emergency paid-lane switch — the ONLY mechanism
    that can limit spending. Configurable, **default OFF pending the
    owner's decision** (spec proposes on at $25/day). When on and
    tripped, paid/premium lanes are off until the owner clears it or
    the day ends; free lanes keep serving (oversize requests that only
    paid models can fit then get a 429 with the reason, per spec).

Estimated cost is recorded pre-call (reservation rows) and corrected to
actual usage post-call by the gateway, including cached-token pricing
(see registry.ModelEntry.estimate_cost).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from quota import QuotaStore

DAY_S = 86_400.0
HOUR_S = 3_600.0


def _day_start(ts: float) -> float:
    return time.mktime(time.strptime(time.strftime("%Y-%m-%d", time.localtime(ts)),
                                     "%Y-%m-%d"))


def _month_start(ts: float) -> float:
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def _days_in_month(ts: float) -> int:
    lt = time.localtime(ts)
    if lt.tm_mon == 12:
        nxt = time.mktime((lt.tm_year + 1, 1, 1, 0, 0, 0, 0, 0, -1))
    else:
        nxt = time.mktime((lt.tm_year, lt.tm_mon + 1, 1, 0, 0, 0, 0, 0, -1))
    start = _month_start(ts)
    return int((nxt - start) // DAY_S)


@dataclass
class Steering:
    """Result of the governor's per-request steering decision."""

    required_quality_delta: int = 0        # -1 when above target, levels 1-3
    cheapest_quality4_only: bool = False   # level 4 held to cheapest q4
    premium_explicit_only: bool = False    # above target (or always for L5 policy)
    above_target: bool = False


@dataclass
class EmergencyConfig:
    enabled: bool = False                  # DEFAULT OFF — owner decision pending
    daily_limit_usd: float = 25.0          # proposed level in the spec
    # Warn-only mode (v0.5.7, owner ruling 2026-11-09): at the limit the
    # governor raises the emergency alert but NEVER switches the paid
    # lanes off — the cascade keeps walking in cost order. The owner
    # restated the router's point: "start cheap and move up… give me
    # the warning, but keep switching." Set via
    # SOVEREIGN_EMERGENCY_WARN_ONLY=1.
    warn_only: bool = False


class Governor:
    def __init__(self, store: QuotaStore,
                 emergency: Optional[EmergencyConfig] = None,
                 first_days_fixed: int = 14,
                 runaway_fixed_hourly_usd: float = 0.50,
                 level_mix_threshold: float = 0.40):
        self.store = store
        self.emergency = emergency or EmergencyConfig()
        self.first_days_fixed = first_days_fixed
        self.runaway_fixed_hourly_usd = runaway_fixed_hourly_usd
        self.level_mix_threshold = level_mix_threshold

    # ------------------------------------------------------------------
    # per-request steering
    # ------------------------------------------------------------------
    def client_spent_today(self, client_id: str, now: Optional[float] = None) -> float:
        now = now or time.time()
        return self.store.client_spend(client_id, _day_start(now), now + 1)

    def steering(self, client_id: str, daily_target_usd: float, level: int,
                 now: Optional[float] = None) -> Steering:
        """Steering for one request. Never blocks — only reshapes the
        candidate ordering/filter the gateway applies."""
        now = now or time.time()
        spent = self.client_spent_today(client_id, now)
        above = spent > daily_target_usd
        st = Steering(above_target=above)
        if above:
            if 1 <= level <= 3:
                st.required_quality_delta = -1
            if level == 4:
                st.cheapest_quality4_only = True
            # Above target, premium needs an explicit request (any level).
            st.premium_explicit_only = True
            self.store.add_alert(
                "client_above_target", client_id,
                f"above_target:{client_id}",
                f"Client {client_id} is above its daily cost target "
                f"(${spent:.2f} > ${daily_target_usd:.2f}); steering to "
                "cheaper models. This is an alert, not a cap.",
                now,
            )
        return st

    # ------------------------------------------------------------------
    # emergency paid-lane switch + killswitch
    # ------------------------------------------------------------------
    def paid_lanes_off(self, now: Optional[float] = None) -> tuple[bool, str]:
        """(off, reason). Off only when the switch is enabled AND tripped
        AND not cleared for today. Default config never trips."""
        now = now or time.time()
        if not self.emergency.enabled:
            return False, ""
        if self.store.emergency_cleared_today(now):
            return False, ""
        spent = self.store.paid_spend(_day_start(now), now + 1)
        if spent >= self.emergency.daily_limit_usd:
            if self.emergency.warn_only:
                # v0.5.7 warn-only (owner ruling 2026-11-09): alert,
                # never veto. The message says plainly that traffic is
                # NOT stopped, mirroring the runaway alarms.
                self.store.add_alert(
                    "emergency_paid_lane_switch", "all",
                    "emergency_switch_warn",
                    f"Emergency spend WARNING (warn-only): ${spent:.2f} "
                    f"paid spend today >= ${self.emergency.daily_limit_usd:.2f} "
                    "alert level. Paid/premium lanes REMAIN ON — the "
                    "cascade continues in cost order; traffic is NOT "
                    "stopped. (Owner ruling 2026-11-09 replaced the "
                    "2026-11-01 hard switch.)",
                    now,
                )
                return False, ""
            self.store.add_alert(
                "emergency_paid_lane_switch", "all",
                "emergency_switch",
                f"Emergency paid-lane switch tripped: ${spent:.2f} paid "
                f"spend today >= ${self.emergency.daily_limit_usd:.2f} "
                "limit. Paid/premium lanes off until cleared or day end; "
                "free lanes keep serving.",
                now,
            )
            return True, "emergency_paid_lane_off"
        return False, ""

    def clear_emergency(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        self.store.set_emergency_cleared_day(
            time.strftime("%Y-%m-%d", time.localtime(now)))

    def killswitch_active(self, client_id: str) -> bool:
        return self.store.killswitch_active(client_id)

    # ------------------------------------------------------------------
    # periodic alert evaluation — called after each request and whenever
    # /admin/costs is polled, so alerts exist even if traffic pauses.
    # Every alert is de-duplicated to once per day per key by the store.
    # ------------------------------------------------------------------
    ALERT_EVAL_MIN_INTERVAL_S = 30.0

    def evaluate_alerts_throttled(self, clients: dict[str, Any],
                                  provider_alerts: dict[str, float],
                                  now: Optional[float] = None) -> bool:
        """Per-request entry point: run the (aggregate-query-heavy)
        alert evaluation at most once per ALERT_EVAL_MIN_INTERVAL_S.
        The admin poll endpoints still call evaluate_alerts directly, so
        the alert chain always sees fresh state. Returns True if it ran."""
        now = now or time.time()
        last = getattr(self, "_last_alert_eval", 0.0)
        if now - last < self.ALERT_EVAL_MIN_INTERVAL_S:
            return False
        self._last_alert_eval = now
        self.evaluate_alerts(clients, provider_alerts, now)
        return True

    def evaluate_alerts(self, clients: dict[str, Any],
                        provider_alerts: dict[str, float],
                        now: Optional[float] = None) -> None:
        now = now or time.time()
        day0 = _day_start(now)

        # --- per-client: level mix, premium alerts ----------------------
        for client_id, policy in clients.items():
            total, hi = self.store.level_mix(client_id, day0, now + 1)
            if total >= 20 and hi / total > self.level_mix_threshold:
                self.store.add_alert(
                    "level_mix", client_id, f"level_mix:{client_id}",
                    f"Client {client_id}: {hi}/{total} requests today are "
                    f"level 4-5 ({hi / total:.0%} > {self.level_mix_threshold:.0%}); "
                    "the classifier may be over-assigning hard levels.",
                    now,
                )
            if policy.premium_daily_count_alert is not None or \
                    policy.premium_monthly_alert_usd is not None:
                day_n, _ = self.store.premium_stats(client_id, day0, now + 1)
                if policy.premium_daily_count_alert is not None and \
                        day_n > policy.premium_daily_count_alert:
                    self.store.add_alert(
                        "premium_daily_count", client_id,
                        f"premium_count:{client_id}",
                        f"Client {client_id}: {day_n} premium requests today "
                        f"(> {policy.premium_daily_count_alert} alert level).",
                        now,
                    )
                if policy.premium_monthly_alert_usd is not None:
                    _, m_cost = self.store.premium_stats(
                        client_id, _month_start(now), now + 1)
                    if m_cost > policy.premium_monthly_alert_usd:
                        self.store.add_alert(
                            "premium_monthly_spend", client_id,
                            f"premium_month:{client_id}",
                            f"Client {client_id}: premium spend this month "
                            f"${m_cost:.2f} > ${policy.premium_monthly_alert_usd:.2f} "
                            "alert level.",
                            now,
                        )

        # --- provider monthly spend alerts ------------------------------
        for provider, level_usd in provider_alerts.items():
            spent = self.store.provider_spend(provider, _month_start(now), now + 1)
            if spent > level_usd:
                self.store.add_alert(
                    "provider_spend", provider, f"provider_spend:{provider}",
                    f"Provider {provider}: month-to-date spend ${spent:.2f} "
                    f"> ${level_usd:.2f} alert level (alert only, no cap).",
                    now,
                )

        self._runaway_alarm(clients, now)
        self._slow_leak_alerts(clients, provider_alerts, now)

    # ------------------------------------------------------------------
    def _runaway_alarm(self, clients: dict[str, Any], now: float) -> None:
        hour0 = now - HOUR_S
        paid_hour = self.store.paid_spend(hour0, now + 1)
        age_days = (now - self.store.first_seen_ts) / DAY_S
        if age_days < self.first_days_fixed:
            # First 14 days: fixed thresholds (no reliable history yet).
            if paid_hour > self.runaway_fixed_hourly_usd:
                self.store.add_alert(
                    "runaway_hourly", "all", "runaway_fixed",
                    f"Runaway alarm (fixed threshold): paid spend in the "
                    f"last hour ${paid_hour:.2f} > ${self.runaway_fixed_hourly_usd:.2f}. "
                    "Traffic is NOT stopped; check for a looping agent or "
                    "a leaked key.",
                    now,
                )
            for client_id, policy in clients.items():
                target = policy.daily_target_usd
                spent = self.client_spent_today(client_id, now)
                if target > 0 and spent > 2 * target:
                    self.store.add_alert(
                        "runaway_client", client_id, f"runaway_client:{client_id}",
                        f"Runaway alarm (fixed threshold): client {client_id} "
                        f"at ${spent:.2f} today, > 2x its ${target:.2f} daily "
                        "target. Traffic is NOT stopped.",
                        now,
                    )
        else:
            # After 14 days: 5x trailing 7-day hourly average, $1 minimum.
            trailing = self.store.paid_spend(now - 7 * DAY_S, hour0)
            hourly_avg = trailing / (7 * 24)
            threshold = max(5 * hourly_avg, 1.0)
            if paid_hour > threshold:
                self.store.add_alert(
                    "runaway_hourly", "all", "runaway_relative",
                    f"Runaway alarm: paid spend in the last hour "
                    f"${paid_hour:.2f} > threshold ${threshold:.2f} "
                    f"(5x trailing 7-day hourly average ${hourly_avg:.2f}, "
                    "$1 minimum). Traffic is NOT stopped.",
                    now,
                )

    # ------------------------------------------------------------------
    def _slow_leak_alerts(self, clients: dict[str, Any],
                          provider_alerts: dict[str, float], now: float) -> None:
        # Projected month: month-to-date extrapolated linearly vs the sum
        # of provider monthly alert levels.
        month0 = _month_start(now)
        lt = time.localtime(now)
        elapsed_days = max(lt.tm_mday - 1 + (now - day0_frac(now)) / DAY_S, 1 / 24)
        mtd = self.store.paid_spend(month0, now + 1)
        projected = mtd / elapsed_days * _days_in_month(now)
        ceiling = sum(provider_alerts.values())
        if ceiling > 0 and projected > ceiling:
            self.store.add_alert(
                "projected_month", "all", "projected_month",
                f"Slow-leak alert: month-to-date paid spend ${mtd:.2f} "
                f"projects to ${projected:.2f} for the month, above the "
                f"${ceiling:.2f} sum of provider alert levels.",
                now,
            )
        # Trend: daily paid spend rose 3 days in a row and the last day
        # ended above the (largest) client target. Client-scoped variant:
        # per-client 3 rising days ending above that client's target.
        for client_id, policy in clients.items():
            days = []
            for back in (3, 2, 1):
                d0 = _day_start(now) - back * DAY_S
                days.append(self.store.paid_spend(d0, d0 + DAY_S, client_id))
            if days[0] < days[1] < days[2] and days[2] > policy.daily_target_usd \
                    and days[0] > 0:
                self.store.add_alert(
                    "spend_trend", client_id, f"trend:{client_id}",
                    f"Slow-leak alert: client {client_id} paid spend rose "
                    f"3 days in a row (${days[0]:.2f} -> ${days[1]:.2f} -> "
                    f"${days[2]:.2f}) and ended above its "
                    f"${policy.daily_target_usd:.2f} daily target.",
                    now,
                )


def day0_frac(now: float) -> float:
    """Seconds since local midnight (helper for elapsed-month math)."""
    return now - _day_start(now)
