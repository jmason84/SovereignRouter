#!/usr/bin/env python3
"""Nightly model scorecard from routes.jsonl (and its rotated siblings).

Usage: scorecard.py [--state DIR] [--day YYYY-MM-DD | --days N]
                    [--json OUT.json] [--md OUT.md]

Streams the log line by line (no slurping), so it stays cheap as the
file grows. Prints markdown to stdout. Run it from cron on VM103, or let
the n8n alert chain pull ``GET /admin/scorecard`` instead. Read-only:
it proposes, it never edits the registry.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from scorecard import build_scorecard, render_markdown  # noqa: E402

TZ = ZoneInfo("America/Denver")


def log_files(state: Path) -> list[Path]:
    base = state / "routes.jsonl"
    rotated = sorted(state.glob("routes.jsonl.*"),
                     key=lambda p: int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0,
                     reverse=True)          # oldest first
    return [p for p in rotated if p.suffix[1:].isdigit()] + ([base] if base.exists() else [])


def iter_rows(state: Path, since: float, until: float):
    for path in log_files(state):
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if since <= rec.get("ts", 0) < until:
                    yield rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=os.environ.get(
        "SOVEREIGN_STATE_DIR", "/root/sovereign-router-v05/state"))
    ap.add_argument("--day", help="YYYY-MM-DD (America/Denver); default today")
    ap.add_argument("--days", type=int, help="trailing N days ending now")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--md", dest="md_out")
    a = ap.parse_args()
    now = datetime.now(TZ)
    if a.days:
        since, until, label = (now - timedelta(days=a.days)).timestamp(), now.timestamp(), f"last {a.days} days"
    else:
        day = datetime.strptime(a.day, "%Y-%m-%d").replace(tzinfo=TZ) if a.day \
            else now.replace(hour=0, minute=0, second=0, microsecond=0)
        since, until, label = day.timestamp(), (day + timedelta(days=1)).timestamp(), day.strftime("%Y-%m-%d")
    card = build_scorecard(iter_rows(Path(a.state), since, until))
    md = render_markdown(card, f"Model scorecard — {label}")
    print(md)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(card, indent=2), encoding="utf-8")
    if a.md_out:
        Path(a.md_out).write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
