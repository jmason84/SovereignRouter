#!/usr/bin/env python3
"""Sovereign Router nightly scorecard - PROPOSALS ONLY (Job D; tools-only, gateway VERSION unchanged).

Owner go 2026-11-27 20:25 MST ("Do B C D"). Reads the router's own logs
and writes a report of per-client and per-model x class health plus a
list of PROPOSED registry/routing changes for Jordan's approval.

It never changes anything:
  * routes.jsonl is read as text; quota.db is opened read-only
    (sqlite URI mode=ro) - the tool cannot write the database;
  * registry.json is read only to name models; it is never written;
  * output goes ONLY to --out (a directory) or stdout.
No network, no keys, no prompt text (route rows never contain any).

Usage (on VM103, read-only against the live state dir):
    python3 tools/scorecard.py --state /root/sovereign-router-v05/state \\
        --hours 24 --out /root/sovereign-router-v05/reports/scorecard
    python3 tools/scorecard.py --state ./state --hours 72 --stdout

Scheduling: see deploy/cron/scorecard.cron - shipped COMMENTED OUT
(inactive). Activating it is a separate owner decision.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Denver")
TARGETS = {"hermes": 1.25, "openclaw": 5.0, "n8n": 2.5, "crm": 1.25}

# Proposal thresholds (documented in docs/TIERED-ROUTING-2026-11-27.md).
TOOL_ERR_MIN_SAMPLES = 30
TOOL_ERR_PROPOSE = 0.20          # propose tooling -1 / tool-turn demotion
DEAD_MIN = 10                    # entry_missing / account_dead attempts
CONGESTED_SHARE = 0.50           # pool share of failed attempts
DEPTH_CAP = 6
SHADOW_MIN_ROWS = 50
SHADOW_DISAGREE_NOTE = 0.30


def _mt(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M MT")


def load_routes(path: Path, since: float, until: float) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", errors="replace") as fh:
        for line in fh:
            if not line.startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = float(rec.get("ts") or 0)
            if since <= ts < until:
                rows.append(rec)
    return rows


def open_ro(db: Path) -> Optional[sqlite3.Connection]:
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def q(con: Optional[sqlite3.Connection], sql: str, args: tuple = ()) -> list:
    if con is None:
        return []
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def load_registry(path: Optional[Path]) -> dict[str, dict]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {m["id"]: m for m in data.get("models", [])}


def pct(vals: list[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(p * len(s)))]


def build(rows: list[dict], con: Optional[sqlite3.Connection],
          registry: dict[str, dict], since: float, until: float) -> dict:
    out: dict[str, Any] = {"window": {"since": _mt(since), "until": _mt(until),
                                      "hours": round((until - since) / 3600, 1)}}
    # ---- per client
    clients: dict[str, dict] = {}
    by_client: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_client[r.get("client") or "?"].append(r)
    for c, part in sorted(by_client.items()):
        n = len(part)
        status = Counter(int(r.get("status") or 0) for r in part)
        tool_rows = [r for r in part if "tool_error_next_turn" in r]
        esc = Counter(x for r in part for x in (r.get("escalation_reasons") or []))
        sig = Counter(s.split(":")[0] for r in part for s in (r.get("signals") or []))
        lanes = Counter(r.get("lane") or "none" for r in part)
        cost = sum(float(r.get("cost_usd") or 0) for r in part)
        days = max(1.0, (until - since) / 86400)
        per_day: dict[str, float] = defaultdict(float)
        for r in part:
            per_day[datetime.fromtimestamp(float(r.get("ts") or 0), TZ).strftime("%Y-%m-%d")] += float(r.get("cost_usd") or 0)
        worst_day = max(per_day.items(), key=lambda kv: kv[1]) if per_day else (None, 0.0)
        clients[c] = {
            "requests": n,
            "cost_usd": round(cost, 4),
            "cost_per_day_usd": round(cost / days, 4),
            "target_per_day_usd": TARGETS.get(c),
            "worst_day": worst_day[0],
            "worst_day_cost_usd": round(worst_day[1], 4),
            "non_200": n - status.get(200, 0),
            "status": dict(status),
            "sensitive_share": round(sum(1 for r in part if r.get("data_class") == "sensitive") / n, 4) if n else 0,
            "tool_error_next_turn_rate": round(sum(1 for r in tool_rows if r.get("tool_error_next_turn")) / len(tool_rows), 4) if tool_rows else None,
            "lanes": dict(lanes),
            "premium_serves": lanes.get("premium", 0),
            "spillway_serves": esc.get("free_exhausted_paid_fallthrough", 0),
            "escalations": dict(esc),
            "depth_cap_hits": sig.get("fallback_depth_cap", 0),
            "premium_not_required_skips": sum(1 for r in part for s in (r.get("skipped") or []) if s.get("reason") == "premium_not_required_congestion"),
            "classes": dict(Counter(r.get("task_class") or "?" for r in part)),
        }
    out["clients"] = clients

    # ---- per model x class (serves from routes)
    mc: dict[tuple, dict] = defaultdict(lambda: {"serves": 0, "non_200": 0, "escalated": 0, "cost_usd": 0.0})
    for r in rows:
        m = r.get("model")
        if not m:
            continue
        k = (m, r.get("task_class") or "?")
        mc[k]["serves"] += 1
        mc[k]["non_200"] += int(int(r.get("status") or 0) != 200)
        mc[k]["escalated"] += int(bool(r.get("escalated")))
        mc[k]["cost_usd"] += float(r.get("cost_usd") or 0)
    out["model_class"] = [
        {"model": m, "class": c, "lane": (registry.get(m) or {}).get("lane"),
         "quality": ((registry.get(m) or {}).get("quality") or {}).get(c),
         **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}}
        for (m, c), d in sorted(mc.items(), key=lambda kv: -kv[1]["serves"])]

    # ---- tool outcomes (production tool-error ledger)
    tools = []
    for row in q(con, "SELECT model_id, COUNT(*) n, SUM(error) e FROM tool_outcomes "
                      "WHERE ts>=? AND ts<? GROUP BY model_id ORDER BY n DESC", (since, until)):
        n, e = int(row["n"]), int(row["e"] or 0)
        reg = registry.get(row["model_id"]) or {}
        tools.append({"model": row["model_id"], "judged": n, "errors": e,
                      "rate": round(e / n, 4) if n else 0.0,
                      "lane": reg.get("lane"),
                      "tooling": (reg.get("quality") or {}).get("tooling")})
    out["tool_outcomes"] = tools

    # ---- attempts / failures
    fails = q(con, "SELECT model_id, pool_id, failure_class, COUNT(*) n FROM attempt_events "
                   "WHERE ts>=? AND ts<? AND outcome='failed' GROUP BY model_id, pool_id, failure_class", (since, until))
    by_class = Counter()
    by_pool_cong = Counter()
    by_model_dead = Counter()
    for f in fails:
        by_class[f["failure_class"] or "?"] += int(f["n"])
        if f["failure_class"] == "congested":
            by_pool_cong[f["pool_id"]] += int(f["n"])
        if f["failure_class"] in ("entry_missing", "account_dead"):
            by_model_dead[(f["model_id"], f["failure_class"])] += int(f["n"])
    depth = q(con, "SELECT MAX(fallback_depth) mx, SUM(CASE WHEN fallback_depth>=? THEN 1 ELSE 0 END) deep, COUNT(*) n "
                   "FROM attempt_events WHERE ts>=? AND ts<?", (DEPTH_CAP, since, until))
    out["attempts"] = {
        "failed_by_class": dict(by_class.most_common()),
        "congested_by_pool": dict(by_pool_cong.most_common(10)),
        "dead_by_model": {f"{m} ({c})": n for (m, c), n in by_model_dead.most_common(15)},
        "max_fallback_depth": (depth[0]["mx"] if depth else None),
        "attempts_at_or_beyond_cap": (depth[0]["deep"] if depth else None),
        "attempts_total": (depth[0]["n"] if depth else None),
    }

    # ---- tier shadow (v0.10.6)
    sh = [r for r in rows if isinstance(r.get("tier_shadow"), dict) and r["tier_shadow"].get("pick")]
    shadow: dict[str, Any] = {"rows": len(sh)}
    if sh:
        agree = sum(1 for r in sh if r["tier_shadow"].get("agree"))
        shadow["agree_share"] = round(agree / len(sh), 4)
        per_class: dict[str, list] = defaultdict(lambda: [0, 0])
        moves = Counter()
        for r in sh:
            t = r["tier_shadow"]
            per_class[t.get("task_class") or "?"][0] += 1
            per_class[t.get("task_class") or "?"][1] += int(not t.get("agree"))
            if not t.get("agree"):
                moves[f"{t.get('live_tier')}->{t.get('pick_tier')}"] += 1
        shadow["disagree_by_class"] = {c: {"rows": a, "disagree": b, "share": round(b / a, 4)} for c, (a, b) in per_class.items()}
        shadow["tier_moves"] = dict(moves.most_common())
        shadow["top_swaps"] = dict(Counter(
            f"{r['tier_shadow']['live_pick']} -> {r['tier_shadow']['pick']}"
            for r in sh if not r["tier_shadow"].get("agree")).most_common(10))
        shadow["low_confidence_rows"] = sum(1 for r in sh if r["tier_shadow"].get("low_confidence"))
    out["tier_shadow"] = shadow

    out["proposals"] = proposals(out, registry)
    return out


def proposals(card: dict, registry: dict[str, dict]) -> list[dict]:
    props: list[dict] = []

    def add(kind, target, evidence, change, risk):
        props.append({"id": f"P{len(props) + 1:02d}", "kind": kind, "target": target,
                      "evidence": evidence, "proposed_change": change, "risk": risk,
                      "status": "PROPOSED - needs Jordan's approval; nothing applied"})

    for t in card["tool_outcomes"]:
        if t["judged"] >= TOOL_ERR_MIN_SAMPLES and t["rate"] >= TOOL_ERR_PROPOSE:
            cur = t.get("tooling")
            add("tool_quality", t["model"],
                f"{t['errors']}/{t['judged']} judged tool turns errored ({t['rate']:.0%}) in the window",
                (f"registry quality.tooling {cur} -> {max(1, int(cur) - 1)} (re-run the tooling battery to confirm)"
                 if cur is not None else "add a measured tooling score via the tooling battery; until then keep out of tool turns"),
                "Fewer tool turns on this model; free-tier tool capacity shrinks if it is a free entry.")
    for label, n in card["attempts"]["dead_by_model"].items():
        if n >= DEAD_MIN:
            model = label.split(" (")[0]
            if (registry.get(model) or {}).get("enabled") is False:
                continue   # already disabled in the registry read; nothing to propose
            add("dead_entry", model, f"{n} failed attempts classed {label.split('(')[1].rstrip(')')}",
                "disable the entry with a dated note after a manual re-probe", "None if the re-probe confirms; lost capacity if transient.")
    total_fail = sum(card["attempts"]["failed_by_class"].values()) or 0
    for pool, n in card["attempts"]["congested_by_pool"].items():
        if total_fail and n / total_fail >= CONGESTED_SHARE:
            add("pool_pressure", pool, f"{n} of {total_fail} failed attempts ({n / total_fail:.0%}) were congestion on this shared pool",
                "lower this pool's share for the heaviest client (n8n bulk) or add a second free pool for its classes",
                "Routing change; evaluate with the tier shadow first.")
    mx = card["attempts"].get("max_fallback_depth")
    if mx is not None and mx >= DEPTH_CAP:
        add("depth", "gateway", f"max fallback depth {mx}; {card['attempts'].get('attempts_at_or_beyond_cap')} attempt rows were the 7th call of a turn or later",
            "deploy v0.10.5 (depth cap 6) if not yet live; if live, investigate why the cap was exceeded",
            "Turns that today succeed at depth 7-21 may 429 instead (Retry-After 60).")
    for c, d in card["clients"].items():
        tgt = d.get("target_per_day_usd")
        if tgt and (d["cost_per_day_usd"] > tgt or d["worst_day_cost_usd"] > tgt):
            add("cost", c, f"average ${d['cost_per_day_usd']:.2f}/day, worst day {d['worst_day']} ${d['worst_day_cost_usd']:.2f}, vs target ${tgt:.2f}",
                "review this client's top paid model x class rows; consider the tier shadow's cheaper picks",
                "Targets steer, never cap (ROUTING-PRINCIPLES #4).")
        if d.get("sensitive_share", 0) > 0.5 and c == "openclaw":
            add("sensitivity", c, f"{d['sensitive_share']:.0%} of requests flagged sensitive",
                "deploy v0.10.4 (session-key false-positive fix) - Job A, waiting on the PR",
                "None known; E2E 252/252.")
        if d.get("premium_serves", 0) and d.get("premium_not_required_skips", 0) == 0:
            pass
    sh = card["tier_shadow"]
    if sh.get("rows", 0) >= SHADOW_MIN_ROWS:
        for cls, v in (sh.get("disagree_by_class") or {}).items():
            if v["rows"] >= SHADOW_MIN_ROWS and v["share"] >= SHADOW_DISAGREE_NOTE:
                add("tier_policy", cls, f"tiered shadow disagrees with live on {v['disagree']}/{v['rows']} ({v['share']:.0%}) {cls} turns",
                    f"review top swaps; if acceptable, enable tiered selection for class '{cls}' behind a flag (new job)",
                    "Live routing change for that class; canary first.")
    return props


def render_md(card: dict) -> str:
    w = card["window"]
    L = [f"# Sovereign Router scorecard - {w['since']} to {w['until']} ({w['hours']}h)", "",
         "Read-only report generated by `tools/scorecard.py`. **Nothing was changed.** Every item under *Proposals* needs Jordan's approval.", ""]
    L += ["## Clients", "", "| Client | Requests | Non-200 | Cost/day (avg) | Worst day | Target | Sensitive | Tool-err next turn | Spillway | Premium | Depth-cap hits |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for c, d in card["clients"].items():
        te = d["tool_error_next_turn_rate"]
        L.append(f"| {c} | {d['requests']} | {d['non_200']} | ${d['cost_per_day_usd']:.2f} | "
                 f"{d['worst_day'] or '-'} ${d['worst_day_cost_usd']:.2f} | "
                 f"{'$%.2f' % d['target_per_day_usd'] if d['target_per_day_usd'] else '-'} | {d['sensitive_share']:.0%} | "
                 f"{'-' if te is None else f'{te:.1%}'} | {d['spillway_serves']} | {d['premium_serves']} | {d['depth_cap_hits']} |")
    L += ["", "## Production tool outcomes (judged tool turns)", "", "| Model | Lane | Tooling score | Judged | Errors | Rate |", "|---|---|---:|---:|---:|---:|"]
    for t in card["tool_outcomes"][:20]:
        L.append(f"| {t['model']} | {t['lane'] or '?'} | {t['tooling'] if t['tooling'] is not None else '-'} | {t['judged']} | {t['errors']} | {t['rate']:.1%} |")
    a = card["attempts"]
    L += ["", "## Failed attempts", "", f"- By class: {', '.join(f'{k} {v}' for k, v in a['failed_by_class'].items()) or 'none'}",
          f"- Congestion by pool: {', '.join(f'{k} {v}' for k, v in a['congested_by_pool'].items()) or 'none'}",
          f"- Dead entries: {', '.join(f'{k} {v}' for k, v in a['dead_by_model'].items()) or 'none'}",
          f"- Max fallback depth: {a['max_fallback_depth']}; attempt rows at depth index >= {DEPTH_CAP} (the 7th call of a turn or later): {a['attempts_at_or_beyond_cap']} of {a['attempts_total']}"]
    L += ["", "## Top model x class", "", "| Model | Class | Lane | Quality | Serves | Non-200 | Escalated | Cost |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for m in card["model_class"][:20]:
        L.append(f"| {m['model']} | {m['class']} | {m['lane'] or '?'} | {m['quality'] if m['quality'] is not None else '-'} | {m['serves']} | {m['non_200']} | {m['escalated']} | ${m['cost_usd']:.3f} |")
    sh = card["tier_shadow"]
    L += ["", "## Tiered shadow (v0.10.6)", ""]
    if not sh.get("rows"):
        L.append("No `tier_shadow` rows in this window (v0.10.6 not live yet).")
    else:
        L.append(f"- Rows: {sh['rows']}; agree with live: {sh['agree_share']:.0%}; low-confidence rows: {sh['low_confidence_rows']}")
        L.append(f"- Tier moves (live -> shadow): {sh['tier_moves']}")
        for c, v in sh["disagree_by_class"].items():
            L.append(f"- {c}: {v['disagree']}/{v['rows']} disagree ({v['share']:.0%})")
        L.append(f"- Top swaps: {sh['top_swaps']}")
    L += ["", "## Proposals (none applied)", ""]
    if not card["proposals"]:
        L.append("No proposals this window.")
    for p in card["proposals"]:
        L += [f"### {p['id']} - {p['kind']}: {p['target']}", f"- Evidence: {p['evidence']}",
              f"- Proposed change: {p['proposed_change']}", f"- Risk: {p['risk']}", f"- Status: {p['status']}", ""]
    return "\n".join(L) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--state", type=Path, default=Path("/root/sovereign-router-v05/state"))
    ap.add_argument("--registry", type=Path, default=None,
                    help="registry.json to name models (default: <state>/registry.json, then src/registry.json)")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--until", type=float, default=None, help="epoch seconds (default now)")
    ap.add_argument("--out", type=Path, default=None, help="directory for scorecard-<date>.md/.json")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args(argv)
    until = args.until or time.time()
    since = until - args.hours * 3600
    reg_path = args.registry
    if reg_path is None:
        for cand in (args.state / "registry.json", Path(__file__).resolve().parent.parent / "src" / "registry.json"):
            if cand.exists():
                reg_path = cand
                break
    registry = load_registry(reg_path)
    rows = load_routes(args.state / "routes.jsonl", since, until)
    con = open_ro(args.state / "quota.db")
    try:
        card = build(rows, con, registry, since, until)
    finally:
        if con is not None:
            con.close()
    md = render_md(card)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        day = datetime.fromtimestamp(until, TZ).strftime("%Y-%m-%d")
        (args.out / f"scorecard-{day}.md").write_text(md)
        (args.out / f"scorecard-{day}.json").write_text(json.dumps(card, indent=1))
    if args.stdout or not args.out:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
