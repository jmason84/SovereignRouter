"""Cost governor. Spending is not capped: this only steers and alerts.

Optional, off by default: `global_daily_backstop_usd`. When set and reached,
paid/premium lanes are skipped for the rest of the UTC day (free models keep
serving). Per-client kill switches are set only by the admin.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone


def utc_day_start(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def utc_month_start(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


class Governor:
    def __init__(self, store, alerter, *, global_daily_backstop_usd: float | None = None,
                 runaway_factor: float = 5.0, runaway_min_usd: float = 1.0):
        self.store, self.alerter = store, alerter
        self.backstop = global_daily_backstop_usd
        self.runaway_factor, self.runaway_min = runaway_factor, runaway_min_usd

    def _now(self) -> float:
        return self.store.clock()

    def client_over_target(self, policy) -> bool:
        t = policy.daily_cost_target_usd
        return t is not None and self.store.spend(utc_day_start(self._now()), client=policy.name) >= t

    def paid_blocked_by_backstop(self) -> bool:
        if self.backstop is None:
            return False
        return self.store.spend(utc_day_start(self._now())) >= self.backstop

    def adjust_required_quality(self, policy, level: int, required: int) -> tuple[int, bool]:
        """Above the daily target, levels 1-3 accept one quality tier lower."""
        if level <= 3 and required > 1 and self.client_over_target(policy):
            return required - 1, True
        return required, False

    def runaway_threshold(self) -> float:
        now = self._now()
        hour_start = now - 3600
        prior = self.store.spend(hour_start - 7 * 86400, hour_start)
        return max(self.runaway_min, self.runaway_factor * prior / (7 * 24))

    async def after_call(self, policy, model) -> None:
        """Run after every finalized paid call: target, provider and runaway alerts."""
        now = self._now()
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        t = policy.daily_cost_target_usd
        if t is not None:
            spent = self.store.spend(utc_day_start(now), client=policy.name)
            if spent >= t:
                await self.alerter.send("client_target", f"target:{policy.name}:{day}",
                                        f"{policy.name} is over its daily cost target (${spent:.2f} / ${t:.2f}). "
                                        "Routing now prefers cheaper models for levels 1-3; nothing is blocked.",
                                        {"client": policy.name, "spent": spent, "target": t})
        if model.spend_alert_usd is not None:
            spent = self.store.spend(utc_month_start(now), model_id=model.id)
            if spent >= model.spend_alert_usd:
                month = day[:7]
                await self.alerter.send("provider_spend", f"provider:{model.id}:{month}",
                                        f"{model.id} monthly spend ${spent:.2f} passed its alert level "
                                        f"${model.spend_alert_usd:.2f} (no cap).",
                                        {"model": model.id, "spent": spent})
        hour_spend = self.store.spend(now - 3600)
        thr = self.runaway_threshold()
        if hour_spend > thr:
            hour = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H")
            await self.alerter.send("runaway", f"runaway:{hour}",
                                    f"Spend in the last hour ${hour_spend:.2f} is above the runaway threshold "
                                    f"${thr:.2f}.", {"hour_spend": hour_spend, "threshold": thr})
        if self.backstop is not None and self.paid_blocked_by_backstop():
            await self.alerter.send("backstop", f"backstop:{day}",
                                    f"Global daily backstop ${self.backstop:.2f} reached: paid lanes are off "
                                    "until 00:00 UTC.")

    async def daily_summary(self) -> bool:
        """Also the dead-man heartbeat: if this stops arriving, alerting is broken."""
        now = self._now()
        since = utc_day_start(now)
        rows = self.store.breakdown(since)
        total = sum(r[6] for r in rows)
        free = sum(r[4] for r in rows if r[6] == 0)
        n = sum(r[4] for r in rows)
        by_client: dict[str, float] = {}
        for r in rows:
            by_client[r[0]] = by_client.get(r[0], 0) + r[6]
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        return await self.alerter.send(
            "daily_summary", f"summary:{day}",
            f"Daily summary {day}: ${total:.2f} over {n} requests, {100 * free / n if n else 0:.0f}% free.",
            {"spend_by_client": by_client, "requests": n, "total": total})
