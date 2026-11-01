"""Alerts. Two delivery paths, both optional, both recorded:

* Pull (v0.5.3 default): every alert is queued in SQLite. n8n polls
  GET /admin/alerts?after=<id> every 15 minutes and forwards to Signal (email
  fallback). Each poll is recorded; /health reports the alert chain as
  degraded when no poll has arrived for `poll_stale_s`.
* Push (optional): POST to an n8n webhook. A failed push is NOT marked as
  sent, so it is retried on the next check, and the failure is counted.

Alerts never block traffic and never contain prompt text."""
from __future__ import annotations

import json
import logging
import time

import httpx

log = logging.getLogger("sovereign.alerts")
POLL_KEY = "alert_last_poll"


class Alerter:
    def __init__(self, store, webhook_url: str | None, client: httpx.AsyncClient | None = None, *,
                 mode: str = "pull", poll_stale_s: float = 1800, started: float | None = None):
        self.store, self.url, self.http = store, webhook_url, client
        self.mode = mode  # pull | push | both
        self.poll_stale_s = poll_stale_s
        self.started = started if started is not None else store.clock()
        self.failures = 0
        self.last_ok: float | None = None

    async def send(self, kind: str, key: str, message: str, data: dict | None = None) -> bool:
        """Record once per `key` and push if a webhook is configured.

        Returns True when the alert is safely recorded for polling (pull mode)
        or delivered by push; False when a required push failed (it will be
        retried on the next call with the same key)."""
        payload = {"kind": kind, "message": message, "data": data or {}, "ts": self.store.clock()}
        new = self.store.record_alert(kind, key, message, json.dumps(data or {}, default=str))
        if new:
            log.warning(json.dumps({"event": "alert", "kind": kind, "key": key, "message": message}))
        if not (self.url and self.http is not None and self.mode in ("push", "both")):
            return True  # queued for the n8n poller
        if self.store.alert_seen(key):
            return True  # already pushed
        try:
            r = await self.http.post(self.url, json=payload, timeout=5)
            r.raise_for_status()
        except Exception as e:  # keep serving traffic; retry on the next check
            self.failures += 1
            log.error("alert delivery failed: %s", type(e).__name__)
            return False
        self.store.mark_alert(key)
        self.last_ok = time.time()
        return True

    def poll(self, after: int) -> list[dict]:
        """Called by the n8n poller. Records the poll time (dead-man heartbeat)."""
        self.store.meta_set(POLL_KEY, str(self.store.clock()))
        return [{"id": r[0], "ts": r[1], "kind": r[2], "key": r[3], "message": r[4], "data": json.loads(r[5])}
                for r in self.store.alerts_after(after)]

    def chain(self) -> dict:
        now = self.store.clock()
        last = self.store.meta_get(POLL_KEY)
        last_poll = float(last) if last else None
        age = (now - last_poll) if last_poll else None
        degraded = False
        if self.mode in ("pull", "both"):
            # degraded when the poller is late, or has never polled 30 min after start
            degraded = (age > self.poll_stale_s) if age is not None else (now - self.started > self.poll_stale_s)
        return {"mode": self.mode, "last_poll_age_s": None if age is None else round(age),
                "degraded": degraded}

    def health(self) -> dict:
        return {"webhook_configured": bool(self.url), "failures": self.failures, "last_ok": self.last_ok,
                **self.chain()}
