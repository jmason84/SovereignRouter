#!/usr/bin/env python3
"""
Phase 0 measurement — Sovereign Router v0.5 (spec v0.5.3, Phase 0).

Parses BOTH production log shapes and reports, per client:
  free/paid split, tier (v0.2.1) / level (v0.3.3) distribution, total cost,
  cost per request, premium share, and failed turns.

Input shapes
------------
v0.2.1 prod usage jsonl (one JSON object per line):
  {ts, client, tier, reason, model, fallback_index, stream, latency_ms,
   prompt_tokens, completion_tokens, cost_usd, status}

v0.3.3 route logs (canary): field names vary by patch level; this parser
  normalises defensively — it looks for level/task/classifier fields,
  model, cost_usd (or cost), client, and a status/outcome field, and treats
  a record as v0.3.3 when it has a "level" key and no "tier" key. Unknown
  fields are ignored; malformed lines are counted, not fatal.

Free vs paid
------------
A request counts as FREE when cost_usd == 0, or its lane/tier says free, or
the model id ends in ":free". Everything else with a known cost counts as
PAID. Records with no cost information count as "unknown" and are excluded
from the split denominator (reported separately so the gap is visible).

Failed turns
------------
status not in {200, "200", "ok", "success", "completed"} — or an explicit
error/outcome field — counts as failed. Escalations (v0.3.3 "escalated"
field / escalation count) are reported where present.

Usage
-----
  python3 analyze_usage.py LOG.jsonl [MORE.jsonl ...]            # markdown
  python3 analyze_usage.py LOG.jsonl --format json -o report.json
  python3 analyze_usage.py LOG.jsonl --format md -o PHASE0-DATA.md

No provider calls, no network. Read-only on the log files.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

OK_STATUSES = {200, "200", "ok", "success", "succeeded", "completed", "complete"}


def is_free(rec) -> bool:
    cost = rec.get("cost_usd", rec.get("cost"))
    if isinstance(cost, (int, float)):
        return cost == 0
    lane = str(rec.get("lane", "")).lower()
    if "free" in lane:
        return True
    if str(rec.get("model", "")).endswith(":free"):
        return True
    return False


def has_cost_info(rec) -> bool:
    return isinstance(rec.get("cost_usd", rec.get("cost")), (int, float)) or (
        "free" in str(rec.get("lane", "")).lower()
        or str(rec.get("model", "")).endswith(":free")
    )


def is_failed(rec) -> bool:
    if rec.get("error"):
        return True
    outcome = rec.get("outcome")
    if isinstance(outcome, str) and outcome.lower() in {"failed", "failure", "error"}:
        return True
    status = rec.get("status")
    if status is None:
        return False
    if isinstance(status, (int, float)):
        return status >= 400
    return str(status).lower() not in OK_STATUSES


def is_premium(rec) -> bool:
    return (
        str(rec.get("tier", "")).lower() == "premium"
        or str(rec.get("lane", "")).lower() == "premium"
        or rec.get("premium") is True
    )


def new_bucket():
    return {
        "requests": 0, "free": 0, "paid": 0, "unknown_cost": 0,
        "tiers": Counter(), "levels": Counter(), "models": Counter(),
        "task_classes": Counter(), "layers": Counter(),
        "cost_total": 0.0, "premium": 0, "failed": 0, "escalations": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "fallbacks": 0,
    }


def analyse(paths):
    per_client = defaultdict(new_bucket)
    totals = new_bucket()
    malformed = 0
    schemas = Counter()

    for path in paths:
        for line in Path(path).read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                malformed += 1
                continue
            if not isinstance(rec, dict):
                malformed += 1
                continue
            schemas["v0.3.3-route" if ("level" in rec and "tier" not in rec) else "v0.2.1-usage"] += 1
            client = str(rec.get("client", "unknown"))
            for bucket in (per_client[client], totals):
                bucket["requests"] += 1
                if has_cost_info(rec):
                    bucket["free" if is_free(rec) else "paid"] += 1
                else:
                    bucket["unknown_cost"] += 1
                if rec.get("tier"):
                    bucket["tiers"][str(rec["tier"])] += 1
                if rec.get("level") is not None:
                    bucket["levels"][str(rec["level"])] += 1
                if rec.get("model"):
                    bucket["models"][str(rec["model"])] += 1
                task = rec.get("task_class") or rec.get("task")
                if task:
                    bucket["task_classes"][str(task)] += 1
                if rec.get("layer"):
                    bucket["layers"][str(rec["layer"])] += 1
                cost = rec.get("cost_usd", rec.get("cost"))
                if isinstance(cost, (int, float)):
                    bucket["cost_total"] += cost
                if is_premium(rec):
                    bucket["premium"] += 1
                if is_failed(rec):
                    bucket["failed"] += 1
                if rec.get("escalated") or rec.get("escalation"):
                    bucket["escalations"] += 1
                if isinstance(rec.get("fallback_index"), (int, float)) and rec["fallback_index"] > 0:
                    bucket["fallbacks"] += 1
                for field, key in (("prompt_tokens", "prompt_tokens"),
                                   ("completion_tokens", "completion_tokens")):
                    if isinstance(rec.get(field), (int, float)):
                        bucket[key] += int(rec[field])
    return per_client, totals, malformed, schemas


def bucket_summary(b):
    n = b["requests"]
    known = b["free"] + b["paid"]
    return {
        "requests": n,
        "free": b["free"],
        "paid": b["paid"],
        "unknown_cost_records": b["unknown_cost"],
        "free_share_pct": round(100 * b["free"] / known, 1) if known else None,
        "paid_share_pct": round(100 * b["paid"] / known, 1) if known else None,
        "tier_distribution": dict(b["tiers"].most_common()),
        "level_distribution": dict(sorted(b["levels"].items())),
        "task_class_distribution": dict(b["task_classes"].most_common()),
        "classifier_layers": dict(b["layers"].most_common()),
        "models": dict(b["models"].most_common()),
        "cost_total_usd": round(b["cost_total"], 4),
        "cost_per_request_usd": round(b["cost_total"] / n, 4) if n else 0.0,
        "premium_requests": b["premium"],
        "premium_share_pct": round(100 * b["premium"] / n, 1) if n else 0.0,
        "failed_turns": b["failed"],
        "failed_pct": round(100 * b["failed"] / n, 1) if n else 0.0,
        "escalations": b["escalations"],
        "fallback_requests": b["fallbacks"],
        "prompt_tokens": b["prompt_tokens"],
        "completion_tokens": b["completion_tokens"],
    }


def to_json(per_client, totals, malformed, schemas):
    return {
        "schemas_seen": dict(schemas),
        "malformed_lines": malformed,
        "totals": bucket_summary(totals),
        "per_client": {c: bucket_summary(b) for c, b in sorted(per_client.items())},
    }


def dist_str(counter_dict):
    return ", ".join(f"{k}: {v}" for k, v in counter_dict.items()) or "—"


def to_markdown(data):
    lines = ["# Phase 0 usage analysis", ""]
    lines.append(f"Log schemas seen: {dist_str(data['schemas_seen'])}; "
                 f"malformed lines skipped: {data['malformed_lines']}")
    lines.append("")
    lines.append("| Client | Requests | Free | Paid | Free % | Cost total | Cost/req | Premium % | Failed % |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    rows = list(data["per_client"].items()) + [("**ALL**", data["totals"])]
    for client, s in rows:
        lines.append(
            f"| {client} | {s['requests']} | {s['free']} | {s['paid']} | "
            f"{s['free_share_pct'] if s['free_share_pct'] is not None else '—'} | "
            f"${s['cost_total_usd']:.2f} | ${s['cost_per_request_usd']:.4f} | "
            f"{s['premium_share_pct']} | {s['failed_pct']} |")
    lines.append("")
    for client, s in rows:
        lines.append(f"## {client}")
        lines.append(f"- Tier distribution: {dist_str(s['tier_distribution'])}")
        lines.append(f"- Level distribution (v0.3.3): {dist_str(s['level_distribution'])}")
        lines.append(f"- Task classes: {dist_str(s['task_class_distribution'])}")
        lines.append(f"- Classifier layers: {dist_str(s['classifier_layers'])}")
        lines.append(f"- Models: {dist_str(s['models'])}")
        lines.append(f"- Escalations: {s['escalations']}; fallback requests: {s['fallback_requests']}; "
                     f"unknown-cost records: {s['unknown_cost_records']}")
        lines.append(f"- Tokens: {s['prompt_tokens']:,} prompt / {s['completion_tokens']:,} completion")
        lines.append("")
    lines.append("Phase 0 reading guide: the v0.2.1 logs have tiers but no difficulty "
                 "levels; only v0.3.3 route logs collected over the 3–5 day Phase 0 "
                 "window can produce the level distribution that resets the v0.5 targets.")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+", help="jsonl log files (v0.2.1 usage and/or v0.3.3 route logs)")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    ap.add_argument("-o", "--output", help="write to file instead of stdout")
    args = ap.parse_args()

    per_client, totals, malformed, schemas = analyse(args.logs)
    data = to_json(per_client, totals, malformed, schemas)
    out = json.dumps(data, indent=2) + "\n" if args.format == "json" else to_markdown(data)
    if args.output:
        Path(args.output).write_text(out)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
