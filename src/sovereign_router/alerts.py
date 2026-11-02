"""Alert outbox. Pull model (v0.5.3): alerts are stored, and an n8n workflow polls
GET /admin/alerts (and /admin/costs) every 15 minutes and forwards them to Signal.
The router never depends on n8n being reachable. Every poll is timestamped, and
/health reports the chain as degraded when no poll has arrived for 30 minutes."""
from __future__ import annotations

import json
import logging

log = logging.getLogger("sovereign.alerts")
POLL_STALE_SECONDS = 30 * 60


class Alerter:
    def __init__(self, store):
        self.store = store
        self.started = store.clock()

    async def send(self, kind: str, key: str, message: str, data: dict | None = None) -> bool:
        """Record an alert once per `key`. Returns True when newly recorded."""
        new = self.store.add_alert(key, kind, message, json.dumps(data or {}))
        if new:
            log.warning(json.dumps({"event": "alert", "kind": kind, "key": key, "message": message}))
        return new

    def touch_poll(self):
        self.store.set_meta("last_poll", str(self.store.clock()))

    def health(self) -> dict:
        now = self.store.clock()
        last = self.store.get_meta("last_poll")
        ref = float(last) if last else self.started
        degraded = now - ref > POLL_STALE_SECONDS
        return {"status": "degraded" if degraded else "ok", "last_poll": float(last) if last else None,
                "seconds_since_poll": int(now - ref)}
