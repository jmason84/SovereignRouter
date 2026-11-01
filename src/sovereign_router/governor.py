"""Cost governor. Spending is not capped: this steers and alerts.

v0.5.3 additions: fixed runaway thresholds for the first 14 days, a
projected-month alert, a 3-day rising-trend alert, a level-mix alert, premium
spend and request-count alerts, and an emergency paid-lane switch that can be
set, cleared for the day, or disabled at runtime from the admin API.

The emergency switch (`backstop`) is the only mechanism that limits spending.
When tripped, paid and premium lanes are skipped for the rest of the UTC day
(or until cleared); free models keep serving.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

DAY = 86400
BACKSTOP_KEY, BACKSTOP_CLEAR_KEY = "backstop_usd", "backstop_cleared_day"


def utc_day_start(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def utc_month_start(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def utc_next_month_start(now: float) -> float:
    d = datetime.fromtimestamp(utc_month_start(now), timezone.utc)
    return (d + timedelta(days=32)).replace(day=1).timestamp()


def _day(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


class Governor:
    def __init__(self, store, alerter, *, registry=None, global_daily_backstop_usd: float | None = None,
                 runaway_factor: float = 5.0, runaway_min_usd: float = 1.0, warmup_days: int = 14,
                 warmup_hourly_paid_usd: float = 0.50, warmup_client_target_factor: float = 2.0,
                 level_mix_share: float = 0.40, level_mix_min_requests: int = 20, trend_days: int = 3):
        self.store, self.alerter, self.registry = store, alerter, registry
        self._env_backstop = global_daily_backstop_usd
        self.runaway_factor, self.runaway_min = runaway_factor, runaway_min_usd
        self.warmup_days, self.warmup_hourly = warmup_days, warmup_hourly_paid_usd
        self.warmup_client_factor = warmup_client_target_factor
        self.level_mix_share, self.level_mix_min = level_mix_share, level_mix_min_requests
        self.trend_days = trend_days

    def _now(self) -> float:
        return self.store.clock()

    # --- emergency paid-lane switch ------------------------------------------
    @property
    def backstop(self) -> float | None:
        v = self.store.meta_get(BACKSTOP_KEY)
        if v is None:
            return self._env_backstop
        return None if v == "off" else float(v)

    @backstop.setter
    def backstop(self, usd: float | None):
        self.store.meta_set(BACKSTOP_KEY, "off" if usd is None else str(float(usd)))

    def clear_backstop_today(self):
        self.store.meta_set(BACKSTOP_CLEAR_KEY, _day(self._now()))

    def paid_blocked_by_backstop(self) -> bool:
        b = self.backstop
        if b is None or self.store.meta_get(BACKSTOP_CLEAR_KEY) == _day(self._now()):
            return False
        return self.store.spend(utc_day_start(self._now())) >= b

    # --- steering ------------------------------------------------------------
    def client_over_target(self, policy) -> bool:
        t = policy.daily_cost_target_usd
        return t is not None and self.store.spend(utc_day_start(self._now()), client=policy.name) >= t

    def adjust_required_quality(self, policy, level: int, required: int) -> tuple[int, bool]:
        """Above the daily target, levels 1-3 accept one quality tier lower. Level 4 stays at quality 4
        (the selector already takes the cheapest quality-4 model); premium needs an explicit request."""
        if level <= 3 and required > 1 and self.client_over_target(policy):
            return required - 1, True
        return required, False

    # --- runaway -------------------------------------------------------------
    def in_warmup(self) -> bool:
        first = self.store.first_ts()
        return first is None or self._now() - first < self.warmup_days * DAY

    def runaway_threshold(self) -> float:
        if self.in_warmup():
            return self.warmup_hourly
        hour_start = self._now() - 3600
        prior = self.store.spend(hour_start - 7 * DAY, hour_start)
        return max(self.runaway_min, self.runaway_factor * prior / (7 * 24))

    def _paid_ids(self) -> list[str]:
        return [m.id for m in self.registry.models if not m.is_free] if self.registry else []

    def _premium_ids(self) -> list[str]:
        return [m.id for m in self.registry.models if m.lane == "premium"] if self.registry else []

    def projected_month(self) -> tuple[float, float] | None:
        """(projected month spend, alert level) once at least one day of the month has passed."""
        if not self.registry:
            return None
        now = self._now()
        start, end = utc_month_start(now), utc_next_month_start(now)
        elapsed = now - start
        if elapsed < DAY:
            return None
        level = sum(m.spend_alert_usd or 0 for m in self.registry.models if not m.is_free)
        if level <= 0:
            return None
        return self.store.spend(start) * (end - start) / elapsed, level

    def rising_trend(self, policy) -> list[float] | None:
        """Daily paid spend for the last N complete days if strictly rising and the last day is over target."""
        t = policy.daily_cost_target_usd
        if t is None:
            return None
        today = utc_day_start(self._now())
        days = [self.store.spend(today - (i + 1) * DAY, today - i * DAY, client=policy.name)
                for i in reversed(range(self.trend_days))]
        if all(b > a for a, b in zip(days, days[1:])) and days[-1] > t:
            return days
        return None

    # --- per-call checks -----------------------------------------------------
    async def after_call(self, policy, model, cost: float = 0.0) -> None:
        """Run after every finalized call (free or paid). Never blocks."""
        now = self._now()
        day = _day(now)
        send = self.alerter.send
        name = policy.name

        # level mix: runs for every call, free ones included
        since = utc_day_start(now)
        n = self.store.requests(since, client=name)
        if n >= self.level_mix_min:
            hard = self.store.requests(since, client=name, min_level=4)
            if hard / n > self.level_mix_share:
                await send("level_mix", f"levelmix:{name}:{day}",
                           f"{name}: {100 * hard / n:.0f}% of today's {n} requests were classified level 4-5 "
                           f"(alert above {100 * self.level_mix_share:.0f}%). The classifier may be over-assigning "
                           "hard levels.", {"client": name, "hard": hard, "requests": n})
        if cost <= 0 and model.is_free:
            return

        t = policy.daily_cost_target_usd
        if t is not None:
            spent = self.store.spend(since, client=name)
            if spent >= t:
                await send("client_target", f"target:{name}:{day}",
                           f"{name} is over its daily cost target (${spent:.2f} / ${t:.2f}). Routing now prefers "
                           "cheaper models for levels 1-3; nothing is blocked.",
                           {"client": name, "spent": spent, "target": t})
            if self.in_warmup() and spent >= self.warmup_client_factor * t:
                await send("runaway", f"runaway-client:{name}:{day}",
                           f"{name} spent ${spent:.2f} today, over {self.warmup_client_factor:g}x its "
                           f"${t:.2f} target (first-{self.warmup_days}-days rule).",
                           {"client": name, "spent": spent, "target": t})
            trend = self.rising_trend(policy)
            if trend:
                await send("trend", f"trend:{name}:{day}",
                           f"{name}: daily spend rose {self.trend_days} days in a row ("
                           + " -> ".join(f"${d:.2f}" for d in trend) + f"), above its ${t:.2f} target.",
                           {"client": name, "days": trend, "target": t})

        if model.spend_alert_usd is not None:
            spent = self.store.spend(utc_month_start(now), model_id=model.id)
            if spent >= model.spend_alert_usd:
                await send("provider_spend", f"provider:{model.id}:{day[:7]}",
                           f"{model.id} monthly spend ${spent:.2f} passed its alert level "
                           f"${model.spend_alert_usd:.2f} (no cap).", {"model": model.id, "spent": spent})

        if model.lane == "premium":
            pids = self._premium_ids() or [model.id]
            lim = getattr(policy, "premium_monthly_alert_usd", None)
            if lim is not None:
                spent = self.store.spend_models(utc_month_start(now), pids, client=name)
                if spent >= lim:
                    await send("premium_spend", f"premium:{name}:{day[:7]}",
                               f"{name} premium spend this month ${spent:.2f} passed ${lim:.2f} (no cap).",
                               {"client": name, "spent": spent, "alert": lim})
            lim_n = getattr(policy, "premium_daily_requests_alert", None)
            if lim_n is not None:
                count = self.store.requests(since, client=name, model_ids=pids)
                if count > lim_n:
                    await send("premium_requests", f"premium-n:{name}:{day}",
                               f"{name} made {count} premium requests today (alert above {lim_n}).",
                               {"client": name, "requests": count, "alert": lim_n})

        paid_ids = self._paid_ids()
        hour_spend = self.store.spend_models(now - 3600, paid_ids) if paid_ids else self.store.spend(now - 3600)
        thr = self.runaway_threshold()
        if hour_spend > thr:
            hour = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H")
            await send("runaway", f"runaway:{hour}",
                       f"Paid spend in the last hour ${hour_spend:.2f} is above the runaway threshold ${thr:.2f}"
                       + (" (first-14-days fixed threshold)." if self.in_warmup() else "."),
                       {"hour_spend": hour_spend, "threshold": thr})

        pm = self.projected_month()
        if pm and pm[0] > pm[1]:
            await send("projected_month", f"projected:{day}",
                       f"Projected spend this month ${pm[0]:.2f} is above the sum of provider alert levels "
                       f"${pm[1]:.2f}.", {"projected": pm[0], "alert_sum": pm[1]})

        if self.paid_blocked_by_backstop():
            await send("backstop", f"backstop:{day}",
                       f"Emergency switch: total paid spend today reached ${self.backstop:.2f}. Paid and premium "
                       "lanes are off until 00:00 UTC or until cleared (POST /admin/backstop). Free models keep "
                       "serving.")

    # --- daily summary / heartbeat ------------------------------------------
    def summary(self) -> dict:
        now = self._now()
        since = utc_day_start(now)
        rows = self.store.breakdown(since)
        total = sum(r[6] or 0 for r in rows)
        n = sum(r[4] for r in rows)
        free = sum(r[4] for r in rows if not r[6])
        by_client, by_model, by_level = {}, {}, {}
        for r in rows:
            by_client[r[0]] = by_client.get(r[0], 0) + (r[6] or 0)
            by_model[r[2]] = by_model.get(r[2], 0) + (r[6] or 0)
            by_level[str(r[3])] = by_level.get(str(r[3]), 0) + r[4]
        return {"day": _day(now), "total_usd": round(total, 6), "requests": n,
                "free_share": round(free / n, 3) if n else None, "spend_by_client": by_client,
                "spend_by_model": by_model, "requests_by_level": by_level,
                "backstop_usd": self.backstop, "backstop_tripped": self.paid_blocked_by_backstop(),
                "alert_chain": self.alerter.chain()}

    async def daily_summary(self) -> bool:
        """Also the dead-man heartbeat: if this stops arriving, alerting is broken."""
        s = self.summary()
        msg = (f"Daily summary {s['day']}: ${s['total_usd']:.2f} over {s['requests']} requests, "
               f"{100 * (s['free_share'] or 0):.0f}% free.")
        return await self.alerter.send("daily_summary", f"summary:{s['day']}", msg, s)
