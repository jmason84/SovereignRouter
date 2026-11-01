"""Alerts to an n8n webhook (which forwards to Signal).

A failed delivery is NOT marked as sent, so it is retried on the next check,
and the failure is counted and exposed on /health. `heartbeat()` is the
dead-man signal: if the daily summary stops arriving, the chain is broken."""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("sovereign.alerts")


class Alerter:
    def __init__(self, store, webhook_url: str | None, client: httpx.AsyncClient | None = None):
        self.store, self.url, self.http = store, webhook_url, client
        self.failures = 0
        self.last_ok: float | None = None
        self.pending: list[dict] = []

    async def send(self, kind: str, key: str, message: str, data: dict | None = None) -> bool:
        """Send once per `key`. Returns True when delivered (or already sent)."""
        if self.store.alert_seen(key):
            return True
        payload = {"kind": kind, "message": message, "data": data or {}, "ts": time.time()}
        log.warning(__import__("json").dumps({"event": "alert", "kind": kind, "key": key, "message": message}))
        if not self.url or self.http is None:
            self.pending.append(payload)
            return False
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

    def health(self) -> dict:
        return {"webhook_configured": bool(self.url), "failures": self.failures, "last_ok": self.last_ok}
