"""Cost governor (v0.5.3). Spending is not capped: this steers and alerts.

Protection against a true runaway is the *emergency paid-lane switch*
(`backstop`, owner decision, unset = off). When today's spend crosses it, paid
and premium lanes are skipped until cleared or the UTC day ends; free models keep
serving and requests only paid models can serve get a 429 with the reason.
"""
from __future__ import annotations

from datetime import datetime, timezone

DAY = 86400
FIXED_THRESHOLD_DAYS = 14
FIXED_HOURLY_PAID_USD = 0.50
FIXED_CLIENT_MULTIPLE = 2.0
LEVEL_MIX_THRESHOLD = 0.40
LEVEL_MIX_MIN_REQUESTS = 10
PREMIUM_DAILY_REQUEST_ALERT = 20


def utc_day_start(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def utc_month_start(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def _day(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


class Governor:
    def __init__(self, store, alerter, *, backstop_usd: float | None = None, clients=None,
                 registry=None, runaway_factor: float = 5.0, runaway_min_usd: float = 1.0,
                 premium_monthly_alert_usd: float = 20.0,
                 premium_daily_request_alert: int = PREMIUM_DAILY_REQUEST_ALERT):
        self.store, self.alerter = store, alerter
        self._backstop = backstop_usd
        self.clients, self.registry = clients, registry
        self.runaway_factor, self.runaway_min = runaway_factor, runaway_min_usd
        self.premium_monthly = premium_monthly_alert_usd
        self.premium_daily = premium_daily_request_alert

    # --- emergency switch: raise, lower or disable at runtime (persisted) ---------
    @property
    def backstop(self) -> float | None:
        v = self.store.get_meta("backstop_usd")
        return float(v) if v else self._backstop

    def set_backstop(self, usd: float | None):
        self._backstop = None
        self.store.set_meta("backstop_usd", None if usd is None else str(usd))
        if usd is None:
            self.store.set_meta("backstop_disabled", "1")
        else:
            self.store.set_meta("backstop_disabled", None)

    def _now(self) -> float:
        return self.store.clock()

    def paid_blocked_by_backstop(self) -> bool:
        b = self.backstop
        if b is None or self.store.get_meta("backstop_disabled"):
            return False
        return self.store.spend(utc_day_start(self._now())) >= b

    # --- steering -----------------------------------------------------------------
    def client_over_target(self, policy) -> bool:
        t = policy.daily_cost_target_usd
        return t is not None and self.store.spend(utc_day_start(self._now()), client=policy.name) >= t

    def adjust_required_quality(self, policy, level: int, required: int) -> tuple[int, bool]:
        """Above the daily target, levels 1-3 accept one quality tier lower. (Level 4 is held to
        the cheapest quality-4 model and premium needs an explicit request; see selector.)"""
        if self.client_over_target(policy):
            if level <= 3 and required > 1:
                return required - 1, True
            return required, True
        return required, False

    def in_fixed_threshold_period(self) -> bool:
        fs = self.store.first_seen()
        return fs is None or self._now() - fs < FIXED_THRESHOLD_DAYS * DAY

    def runaway_threshold(self) -> float:
        now = self._now()
        if self.in_fixed_threshold_period():
            return FIXED_HOURLY_PAID_USD
        prior = self.store.spend(now - 3600 - 7 * DAY, now - 3600)
        return max(self.runaway_min, self.runaway_factor * prior / (7 * 24))

    # --- alerts (all idempotent per key) -------------------------------------------
    async def after_call(self, policy, model) -> None:
        now = self._now()
        day = _day(now)
        today = utc_day_start(now)
        send = self.alerter.send

        t = policy.daily_cost_target_usd
        if t is not None:
            spent = self.store.spend(today, client=policy.name)
            if spent >= t:
                await send("client_target", f"target:{policy.name}:{day}",
                           f"{policy.name} is over its daily cost target (${spent:.2f} / ${t:.2f}). Routing now "
                           "prefers cheaper models; nothing is blocked.", {"client": policy.name, "spent": spent})
            if self.in_fixed_threshold_period() and spent > FIXED_CLIENT_MULTIPLE * t:
                await send("runaway", f"runaway-client:{policy.name}:{day}",
                           f"{policy.name} spent ${spent:.2f} today, over {FIXED_CLIENT_MULTIPLE:g}x its target ${t:.2f}.",
                           {"client": policy.name, "spent": spent})

        n = self.store.count(today, client=policy.name)
        hard = self.store.count(today, client=policy.name, min_level=4)
        if n >= LEVEL_MIX_MIN_REQUESTS and hard / n > LEVEL_MIX_THRESHOLD:
            await send("level_mix", f"levelmix:{policy.name}:{day}",
                       f"{policy.name}: {100 * hard / n:.0f}% of today's requests are level 4-5 "
                       "(the classifier may be over-assigning hard levels).", {"client": policy.name, "hard": hard, "n": n})

        if model.spend_alert_usd is not None:
            spent = self.store.spend(utc_month_start(now), model_id=model.id)
            if spent >= model.spend_alert_usd:
                await send("provider_spend", f"provider:{model.id}:{day[:7]}",
                           f"{model.id} monthly spend ${spent:.2f} passed its alert level "
                           f"${model.spend_alert_usd:.2f} (no cap).", {"model": model.id, "spent": spent})

        if model.lane == "premium":
            ps = self.store.spend(utc_month_start(now), lane="premium")
            if ps >= self.premium_monthly:
                await send("premium_spend", f"premium-month:{day[:7]}",
                           f"Premium spend this month ${ps:.2f} passed ${self.premium_monthly:.2f}.", {"spent": ps})
            pc = self.store.count(today, lane="premium")
            if pc > self.premium_daily:
                await send("premium_requests", f"premium-count:{day}",
                           f"{pc} premium requests today (alert above {self.premium_daily}).", {"count": pc})

        hour_spend = self.store.spend(now - 3600)
        thr = self.runaway_threshold()
        if hour_spend > thr:
            await send("runaway", f"runaway:{datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%dT%H')}",
                       f"Spend in the last hour ${hour_spend:.2f} is above the runaway threshold ${thr:.2f}.",
                       {"hour_spend": hour_spend, "threshold": thr})

        await self._slow_leaks(policy, now, day)

        b = self.backstop
        if b is not None and self.paid_blocked_by_backstop():
            await send("emergency_switch", f"backstop:{day}",
                       f"Emergency switch tripped at ${b:.2f}/day: paid and premium lanes are off until cleared or "
                       "00:00 UTC.", {"limit": b})

    async def _slow_leaks(self, policy, now: float, day: str):
        # Projected month: month-to-date extrapolated above the sum of provider alert levels.
        ms = utc_month_start(now)
        elapsed_days = (now - ms) / DAY
        if elapsed_days >= 1 and self.registry is not None:
            budget = sum(m.spend_alert_usd or 0 for m in self.registry.models)
            if budget > 0:
                mtd = self.store.spend(ms)
                projected = mtd / elapsed_days * 30
                if projected > budget:
                    await self.alerter.send("projected_month", f"projected:{day[:7]}",
                                            f"Month-to-date ${mtd:.2f} projects to ${projected:.2f}, above the "
                                            f"${budget:.2f} sum of provider alert levels.", {"projected": projected})
        # Trend: client's daily spend rising 4 points (3 rises) in a row and ending above its target.
        t = policy.daily_cost_target_usd
        if t is not None:
            d0 = utc_day_start(now)
            series = [self.store.spend(d0 - k * DAY, d0 - (k - 1) * DAY if k else None, client=policy.name)
                      for k in (3, 2, 1, 0)]
            if series[0] < series[1] < series[2] < series[3] and series[3] > t:
                await self.alerter.send("trend", f"trend:{policy.name}:{day}",
                                        f"{policy.name} daily spend has risen 3 days running and is above target "
                                        f"(${series[3]:.2f}).", {"series": series})

    async def daily_summary(self) -> bool:
        """The daily all-clear: n8n relays it to Signal at 08:10 MT; its absence means the chain is broken."""
        now = self._now()
        rows = self.store.breakdown(utc_day_start(now))
        total = sum(r[6] for r in rows)
        n = sum(r[4] for r in rows)
        free = sum(r[4] for r in rows if r[6] == 0)
        by_client: dict[str, float] = {}
        for r in rows:
            by_client[r[0]] = by_client.get(r[0], 0) + r[6]
        day = _day(now)
        return await self.alerter.send(
            "daily_summary", f"summary:{day}",
            f"Daily all-clear {day}: ${total:.2f} over {n} requests, {100 * free / n if n else 0:.0f}% free.",
            {"spend_by_client": by_client, "requests": n, "total": total})
