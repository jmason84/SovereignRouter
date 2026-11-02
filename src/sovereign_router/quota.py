"""SQLite store: provider rate-limit windows, spend ledger, cooldowns,
alert dedupe, kill switches. Holds no prompt or response text."""
from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, model_id TEXT NOT NULL,
  provider TEXT NOT NULL, lane TEXT NOT NULL DEFAULT '', client TEXT NOT NULL, level INTEGER, tokens INTEGER NOT NULL,
  cost REAL NOT NULL, final INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE TABLE IF NOT EXISTS cooldown(model_id TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS alerts_sent(key TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS killswitch(client TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, ts REAL NOT NULL,
  kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}');
"""


class Store:
    def __init__(self, path: str = ":memory:", clock=time.time):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.clock = clock

    # --- rate-limit windows -------------------------------------------------
    def _window(self, model_id: str, seconds: int) -> tuple[int, int]:
        row = self.db.execute("SELECT COUNT(*), COALESCE(SUM(tokens),0) FROM usage WHERE model_id=? AND ts>?",
                              (model_id, self.clock() - seconds)).fetchone()
        return row[0], row[1]

    def check_limits(self, model, est_tokens: int) -> str | None:
        """Return a skip reason if a provider limit would be exceeded, else None."""
        lim = model.limits
        with self.lock:
            cd = self.db.execute("SELECT until FROM cooldown WHERE model_id=?", (model.id,)).fetchone()
            if cd and cd[0] > self.clock():
                return f"cooldown {int(cd[0] - self.clock())}s"
            for secs, rk, tk, label in ((60, "rpm", "tpm", "minute"), (86400, "rpd", "tpd", "day")):
                if rk in lim or tk in lim:
                    reqs, toks = self._window(model.id, secs)
                    if rk in lim and reqs + 1 > lim[rk]:
                        return f"{label} request limit"
                    if tk in lim and toks + est_tokens > lim[tk]:
                        return f"{label} token limit"
        return None

    def reserve(self, model, client: str, level: int, est_tokens: int, est_cost: float) -> int | None:
        """Atomically re-check limits and record the estimate. None = limit hit."""
        with self.lock:
            lim = model.limits
            for secs, rk, tk in ((60, "rpm", "tpm"), (86400, "rpd", "tpd")):
                reqs, toks = self._window(model.id, secs)
                if (rk in lim and reqs + 1 > lim[rk]) or (tk in lim and toks + est_tokens > lim[tk]):
                    return None
            cur = self.db.execute(
                "INSERT INTO usage(ts,model_id,provider,lane,client,level,tokens,cost) VALUES(?,?,?,?,?,?,?,?)",
                (self.clock(), model.id, model.provider, model.lane, client, level, est_tokens, est_cost))
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('first_seen', ?)", (str(self.clock()),))
            return cur.lastrowid

    def finalize(self, rid: int, tokens: int, cost: float):
        with self.lock:
            self.db.execute("UPDATE usage SET tokens=?, cost=?, final=1 WHERE id=?", (tokens, cost, rid))

    def cancel(self, rid: int):
        with self.lock:
            self.db.execute("DELETE FROM usage WHERE id=?", (rid,))

    def set_cooldown(self, model_id: str, seconds: float):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO cooldown VALUES(?,?)", (model_id, self.clock() + seconds))

    # --- spend --------------------------------------------------------------
    def spend(self, since: float, until: float | None = None, client: str | None = None,
              model_id: str | None = None, provider: str | None = None, lane: str | None = None) -> float:
        q, args = "SELECT COALESCE(SUM(cost),0) FROM usage WHERE ts>=?", [since]
        if until is not None:
            q += " AND ts<?"; args.append(until)
        for col, val in (("client", client), ("model_id", model_id), ("provider", provider), ("lane", lane)):
            if val is not None:
                q += f" AND {col}=?"; args.append(val)
        with self.lock:
            return self.db.execute(q, args).fetchone()[0]

    def breakdown(self, since: float) -> list[tuple]:
        with self.lock:
            return self.db.execute(
                "SELECT client, provider, model_id, level, COUNT(*), SUM(tokens), SUM(cost) FROM usage "
                "WHERE ts>=? GROUP BY client, provider, model_id, level", (since,)).fetchall()

    def window_usage(self, model_id: str) -> dict:
        with self.lock:
            (rm, tm), (rd, td) = self._window(model_id, 60), self._window(model_id, 86400)
        return {"minute": {"requests": rm, "tokens": tm}, "day": {"requests": rd, "tokens": td}}

    # --- alerts / kill switch ----------------------------------------------
    def alert_seen(self, key: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM alerts_sent WHERE key=?", (key,)).fetchone() is not None

    def mark_alert(self, key: str):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO alerts_sent VALUES(?,?)", (key, self.clock()))

    def set_kill(self, client: str, on: bool):
        with self.lock:
            if on:
                self.db.execute("INSERT OR IGNORE INTO killswitch VALUES(?)", (client,))
            else:
                self.db.execute("DELETE FROM killswitch WHERE client=?", (client,))

    def is_killed(self, client: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM killswitch WHERE client=?", (client,)).fetchone() is not None

    def count(self, since: float, client: str | None = None, lane: str | None = None, min_level: int | None = None) -> int:
        q, args = "SELECT COUNT(*) FROM usage WHERE ts>=?", [since]
        for col, val in (("client", client), ("lane", lane)):
            if val is not None:
                q += f" AND {col}=?"; args.append(val)
        if min_level is not None:
            q += " AND level>=?"; args.append(min_level)
        with self.lock:
            return self.db.execute(q, args).fetchone()[0]

    def first_seen(self) -> float | None:
        v = self.get_meta("first_seen")
        return float(v) if v else None

    # --- meta -----------------------------------------------------------------
    def get_meta(self, k: str) -> str | None:
        with self.lock:
            r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def set_meta(self, k: str, v: str | None):
        with self.lock:
            if v is None:
                self.db.execute("DELETE FROM meta WHERE k=?", (k,))
            else:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v))

    # --- alert outbox (pulled by n8n; the router never depends on n8n) ---------
    def add_alert(self, key: str, kind: str, message: str, data: str = "{}") -> bool:
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO alerts(key,ts,kind,message,data) VALUES(?,?,?,?,?)",
                                  (key, self.clock(), kind, message, data))
            return cur.rowcount == 1

    def alerts_after(self, after_id: int, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id,ts,kind,message,data FROM alerts WHERE id>? ORDER BY id LIMIT ?",
                                   (after_id, limit)).fetchall()
        return [{"id": r[0], "ts": r[1], "kind": r[2], "message": r[3], "data": r[4]} for r in rows]
