"""SQLite-backed quota / usage / alert store for Sovereign Router v0.5.

Adapted from the v0.3 QuotaStore concept (see spec: "Per provider account:
the provider's real rate limits (minute and day request and token windows)
stay in the quota store").

Responsibilities:
  * Provider rate-limit windows (requests/tokens per minute and per day),
    checked before a call and recorded after it.
  * The usage ledger: one row per upstream attempt outcome that carried
    usage. Estimated cost is recorded pre-call by the governor (as a
    reservation row with ``estimated=1``) and corrected post-call with the
    actual usage reported by the provider, including cached-token pricing.
  * Alert log with per-day de-duplication keys (the governor decides *when*
    to alert; this store guarantees "one alert per key per day").
  * Alert-chain poll heartbeat: n8n polls /admin/costs; the timestamp of
    the last poll backs the /health degraded flag (no poll for 30 min).
  * Manual killswitch state and the emergency paid-lane switch state.

No prompt or response text is ever stored here.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class QuotaExceeded(Exception):
    """Raised when a provider rate-limit window would be exceeded.

    Carries the machine-readable ``reason`` used in skip-reason lists and
    the 429 error contract.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class UsageEvent:
    ts: float
    client_id: str
    provider: str
    model_id: str
    lane: str
    level: int
    task_class: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    estimated: bool = False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    client_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    lane TEXT NOT NULL,
    level INTEGER NOT NULL,
    task_class TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_client_ts ON usage_events(client_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_provider_ts ON usage_events(provider, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    message TEXT NOT NULL,
    UNIQUE(day, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS killswitch (
    client_id TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 0,
    updated_ts REAL NOT NULL
);
"""


def _day_str(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class QuotaStore:
    """Thread-safe sqlite store. One connection, serialized by a lock —
    the gateway is a single process and request volume is small."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            # First-seen timestamp drives the governor's "first 14 days"
            # fixed-threshold window (spec: runaway alarm).
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key='first_seen_ts'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES('first_seen_ts', ?)",
                    (str(time.time()),),
                )
                self._conn.commit()

    # ------------------------------------------------------------------
    # meta helpers
    # ------------------------------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    @property
    def first_seen_ts(self) -> float:
        v = self.get_meta("first_seen_ts")
        return float(v) if v else time.time()

    # ------------------------------------------------------------------
    # usage ledger
    # ------------------------------------------------------------------
    def record_usage(self, ev: UsageEvent) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage_events(ts, client_id, provider, model_id,"
                " lane, level, task_class, input_tokens, output_tokens,"
                " cached_tokens, cost_usd, estimated)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ev.ts, ev.client_id, ev.provider, ev.model_id, ev.lane,
                    ev.level, ev.task_class, ev.input_tokens, ev.output_tokens,
                    ev.cached_tokens, ev.cost_usd, 1 if ev.estimated else 0,
                ),
            )
            self._conn.commit()

    def _sum(self, where: str, params: tuple, since: float, until: float,
             paid_only: bool = False) -> float:
        lane_clause = " AND lane IN ('paid','premium')" if paid_only else ""
        with self._lock:
            row = self._conn.execute(
                f"SELECT COALESCE(SUM(cost_usd),0) AS s FROM usage_events "
                f"WHERE ts>=? AND ts<? {where} {lane_clause} AND estimated=0",
                (since, until, *params),
            ).fetchone()
            return float(row["s"])

    def client_spend(self, client_id: str, since: float, until: float) -> float:
        return self._sum("AND client_id=?", (client_id,), since, until)

    def paid_spend(self, since: float, until: float,
                   client_id: Optional[str] = None) -> float:
        if client_id:
            return self._sum("AND client_id=?", (client_id,), since, until,
                             paid_only=True)
        return self._sum("", (), since, until, paid_only=True)

    def provider_spend(self, provider: str, since: float, until: float) -> float:
        return self._sum("AND provider=?", (provider,), since, until)

    def premium_stats(self, client_id: str, since: float, until: float) -> tuple[int, float]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS s "
                "FROM usage_events WHERE ts>=? AND ts<? AND client_id=? "
                "AND lane='premium' AND estimated=0",
                (since, until, client_id),
            ).fetchone()
            return int(row["n"]), float(row["s"])

    def level_mix(self, client_id: str, since: float, until: float) -> tuple[int, int]:
        """Return (total, level>=4 count) of *final* (non-estimated) events."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, "
                "COALESCE(SUM(CASE WHEN level>=4 THEN 1 ELSE 0 END),0) AS hi "
                "FROM usage_events WHERE ts>=? AND ts<? AND client_id=? "
                "AND estimated=0",
                (since, until, client_id),
            ).fetchone()
            return int(row["n"]), int(row["hi"])

    def costs_breakdown(self, since: float, until: float) -> dict[str, Any]:
        with self._lock:
            def grouped(col: str) -> list[dict[str, Any]]:
                rows = self._conn.execute(
                    f"SELECT {col} AS k, COUNT(*) AS n, "
                    "COALESCE(SUM(cost_usd),0) AS s FROM usage_events "
                    "WHERE ts>=? AND ts<? AND estimated=0 GROUP BY k ORDER BY s DESC",
                    (since, until),
                ).fetchall()
                return [{"key": r["k"], "requests": r["n"], "cost_usd": r["s"]}
                        for r in rows]
            total_row = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS s "
                "FROM usage_events WHERE ts>=? AND ts<? AND estimated=0",
                (since, until),
            ).fetchone()
            free_row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM usage_events WHERE ts>=? AND ts<? "
                "AND estimated=0 AND lane IN ('private-free','public-only-free')",
                (since, until),
            ).fetchone()
            return {
                "total_requests": total_row["n"],
                "total_cost_usd": total_row["s"],
                "free_requests": free_row["n"],
                "free_share": (free_row["n"] / total_row["n"]) if total_row["n"] else 0.0,
                "by_client": grouped("client_id"),
                "by_provider": grouped("provider"),
                "by_level": grouped("level"),
                "by_lane": grouped("lane"),
            }

    def client_usage(self, client_id: str, since: float, until: float) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT model_id, lane, COUNT(*) AS n, "
                "COALESCE(SUM(input_tokens),0) AS it, "
                "COALESCE(SUM(output_tokens),0) AS ot, "
                "COALESCE(SUM(cached_tokens),0) AS ct, "
                "COALESCE(SUM(cost_usd),0) AS s FROM usage_events "
                "WHERE ts>=? AND ts<? AND client_id=? AND estimated=0 "
                "GROUP BY model_id, lane ORDER BY s DESC",
                (since, until, client_id),
            ).fetchall()
            return {
                "client": client_id,
                "total_cost_usd": sum(r["s"] for r in rows),
                "total_requests": sum(r["n"] for r in rows),
                "by_model": [
                    {"model": r["model_id"], "lane": r["lane"],
                     "requests": r["n"], "input_tokens": r["it"],
                     "output_tokens": r["ot"], "cached_tokens": r["ct"],
                     "cost_usd": r["s"]}
                    for r in rows
                ],
            }

    # ------------------------------------------------------------------
    # provider rate-limit windows
    # ------------------------------------------------------------------
    def window_usage(self, provider: str, since: float, until: float) -> tuple[int, int]:
        """(requests, tokens) consumed in a window, actuals only."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, "
                "COALESCE(SUM(input_tokens+output_tokens),0) AS t "
                "FROM usage_events WHERE provider=? AND ts>=? AND ts<? "
                "AND estimated=0",
                (provider, since, until),
            ).fetchone()
            return int(row["n"]), int(row["t"])

    def check_provider_limits(self, provider: str, limits: dict[str, Any],
                              est_tokens: int, now: Optional[float] = None) -> Optional[str]:
        """Return a skip reason if adding this request would breach a
        configured provider window, else None.

        ``limits`` keys (all optional): rpm, tpm, rpd, tpd.
        Spec decision: reaching a provider limit *moves traffic to the
        next model* — it never blocks the request outright.
        """
        now = now or time.time()
        if not limits:
            return None
        day_start = time.mktime(time.strptime(_day_str(now), "%Y-%m-%d"))
        if limits.get("rpm") or limits.get("tpm"):
            n, t = self.window_usage(provider, now - 60, now + 1)
            if limits.get("rpm") and n + 1 > int(limits["rpm"]):
                return "rate_limited_rpm"
            if limits.get("tpm") and t + est_tokens > int(limits["tpm"]):
                return "rate_limited_tpm"
        if limits.get("rpd") or limits.get("tpd"):
            n, t = self.window_usage(provider, day_start, now + 1)
            if limits.get("rpd") and n + 1 > int(limits["rpd"]):
                return "rate_limited_rpd"
            if limits.get("tpd") and t + est_tokens > int(limits["tpd"]):
                return "rate_limited_tpd"
        return None

    # ------------------------------------------------------------------
    # alerts (de-duplicated per day by the governor's dedup key)
    # ------------------------------------------------------------------
    def add_alert(self, kind: str, scope: str, dedup_key: str, message: str,
                  now: Optional[float] = None) -> bool:
        """Insert an alert; returns True if it is new today."""
        now = now or time.time()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO alerts(ts, day, kind, scope, dedup_key, message)"
                    " VALUES(?,?,?,?,?,?)",
                    (now, _day_str(now), kind, scope, dedup_key, message),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def alerts_since(self, since: float) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, kind, scope, message FROM alerts WHERE ts>=? "
                "ORDER BY ts ASC", (since,),
            ).fetchall()
            return [dict(r) for r in rows]

    def alerts_after(self, after_id: int) -> list[dict[str, Any]]:
        """Pulled-alerts outbox (ported from the parallel GitHub build,
        2026-11-02 convergence): alerts with id > ``after_id``, in id
        order, so an n8n poller can checkpoint by id instead of time."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, kind, scope, message FROM alerts "
                "WHERE id>? ORDER BY id ASC", (after_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # alert-chain heartbeat (n8n poll of /admin/costs)
    # ------------------------------------------------------------------
    def record_poll(self, now: Optional[float] = None) -> None:
        self.set_meta("last_poll_ts", str(now or time.time()))

    def last_poll_ts(self) -> Optional[float]:
        v = self.get_meta("last_poll_ts")
        return float(v) if v else None

    # ------------------------------------------------------------------
    # killswitch + emergency switch state
    # ------------------------------------------------------------------
    def set_killswitch(self, client_id: str, active: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO killswitch(client_id, active, updated_ts)"
                " VALUES(?,?,?) ON CONFLICT(client_id) DO UPDATE SET"
                " active=excluded.active, updated_ts=excluded.updated_ts",
                (client_id, 1 if active else 0, time.time()),
            )
            self._conn.commit()

    def killswitch_active(self, client_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT active FROM killswitch WHERE client_id=?", (client_id,)
            ).fetchone()
            return bool(row and row["active"])

    def set_emergency_cleared_day(self, day: str) -> None:
        self.set_meta("emergency_cleared_day", day)

    def emergency_cleared_today(self, now: Optional[float] = None) -> bool:
        return self.get_meta("emergency_cleared_day") == _day_str(now or time.time())
