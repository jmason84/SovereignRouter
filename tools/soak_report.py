#!/usr/bin/env python3
"""Sovereign Router v0.5 — Phase 3 soak report.

Runs ON VM103 (reads the live state dir directly; no keys involved).
Usage: python3 soak_report.py [YYYY-MM-DD]   (default: today, America/Denver)

Prints a markdown summary of the runbook section 7 success measures for
one day of v0.5 route-log data, per client and in total.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = Path(os.environ.get("SOVEREIGN_STATE_DIR", "/root/sovereign-router-v05/state"))
TZ = ZoneInfo("America/Denver")


def load_targets() -> dict:
    """Daily targets come from clients.json (the source of truth), not a
    copy that drifts. Falls back to the example file shipped in the repo."""
    for path in (STATE / "clients.json",
                 Path(__file__).resolve().parent.parent / "src" / "clients.example.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {c["id"]: float(c["daily_target_usd"])
                    for c in data.get("clients", []) if "daily_target_usd" in c}
        except (OSError, ValueError, KeyError):
            continue
    return {}


TARGETS = load_targets()


def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[i]


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime("%Y-%m-%d")
    rows = []
    log = STATE / "routes.jsonl"
    if log.exists():
        for line in log.read_text(errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if datetime.fromtimestamp(rec.get("ts", 0), TZ).strftime("%Y-%m-%d") == day:
                rows.append(rec)

    print(f"# v0.5 soak — {day} (America/Denver)")
    print()
    if not rows:
        print("No v0.5 requests logged this day yet.")
        return 0

    clients = sorted({r.get("client", "?") for r in rows})
    for scope in clients + ["ALL"]:
        part = rows if scope == "ALL" else [r for r in rows if r.get("client") == scope]
        n = len(part)
        cost = sum(r.get("cost_usd", 0.0) for r in part)
        free = sum(1 for r in part if r.get("lane") == "private-free" or r.get("cost_usd", 0) == 0)
        esc = sum(1 for r in part if r.get("escalated"))
        failed = sum(1 for r in part
                     if (r.get("status") or 200) >= 400 and r.get("status") not in (401, 403))
        routing_ms = sorted((r.get("latency_classify_ms", 0) + r.get("latency_queue_ms", 0))
                            for r in part)
        premium = [r for r in part if r.get("lane") == "premium"]
        levels = {}
        for r in part:
            levels[r.get("level")] = levels.get(r.get("level"), 0) + 1
        l45 = sum(v for k, v in levels.items() if (k or 0) >= 4)
        print(f"## {scope} — {n} requests, ${cost:.4f}")
        tgt = TARGETS.get(scope)
        if tgt:
            print(f"- Cost vs daily target ${tgt:.2f}: {'OVER' if cost > tgt else 'within'}")
        print(f"- Free share: {free / n:.0%}  (measure 2)")
        print(f"- Escalations: {esc} ({esc / n:.1%}, target <8%)")
        print(f"- Failed (non-auth 4xx/5xx): {failed} ({failed / n:.1%}, target <1%)")
        p50, p95 = pct(routing_ms, 0.5), pct(routing_ms, 0.95)
        print(f"- Routing latency p50 {p50:.0f} ms / p95 {p95:.0f} ms (targets <300 / <1500)")
        print(f"- Premium requests: {len(premium)} (cost ${sum(r.get('cost_usd', 0) for r in premium):.4f})")
        spill = [r for r in part
                 if "free_exhausted_paid_fallthrough" in (r.get("escalation_reasons") or [])]
        print(f"- Paid spillway: {len(spill)} turns (cost ${sum(r.get('cost_usd', 0) for r in spill):.4f}) — free exhausted, paid/premium served (v0.5.5)")
        if spill:
            # Per-model attribution (v0.5.6): which paid/premium entry
            # actually served each spillway turn — SiliconFlow serves
            # (siliconflow/*) must be visible by name, not folded into
            # the aggregate.
            by_model: dict[str, list[float]] = {}
            for r in spill:
                by_model.setdefault(r.get("model") or "?", []).append(
                    r.get("cost_usd", 0.0))
            parts = ", ".join(
                f"{m} ×{len(c)} (${sum(c):.4f})"
                for m, c in sorted(by_model.items()))
            print(f"  - Spillway serves by model: {parts}")
        print(f"- Level mix: {dict(sorted(levels.items(), key=lambda x: str(x[0])))}; L4+L5 = {l45 / n:.0%} (alert >40%)")
        print()

    # Alert-chain heartbeat + killswitch state from the quota DB.
    db_path = STATE / "quota.db"
    if db_path.exists():
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            tables = [r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            print(f"Quota DB tables: {', '.join(tables)}")
        except sqlite3.Error as e:
            print(f"Quota DB read failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
