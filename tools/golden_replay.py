#!/usr/bin/env python3
"""Golden-set shadow replay (Cycle 1 aspect F2) — v0.9.0 starter.

Runs the synthetic golden set (tests/golden/golden_set.json) against
/v1/shadow and checks two things:

1. VERIFIERS — per-item expectations on the shadow decision itself
   (classifier task class, data class, first-choice lane constraints;
   sensitive-marker items must never first-route to the public lane).
   Shadow is decision-level, so these are the only honest assertions
   available offline: answer-level verifiers belong to the live
   replay sample, which cycle-01-plan F2 designs but keeps
   report-only until separately approved (and Decision #5's judged
   quality sample stays OFF — nothing here judges answer quality).

2. DIFF — against a baseline capture of the same set, the selection
   outcome (ordered candidate list + would-route target) per item.
   A change in outcome is a finding unless the item id is declared
   in a manifest {item_id: "reason"} supplied with --manifest:
   registry or code changes that move selections must say so.

No upstream calls are made (shadow), so a replay is free and safe to
run nightly. Client keys come from the environment
(SR_KEY_HERMES / SR_KEY_OPENCLAW / SR_KEY_N8N), never argv.

Usage:
  python3 golden_replay.py --base-url http://127.0.0.1:6075/v1 \
      --golden tests/golden/golden_set.json \
      --write-baseline tests/golden/baseline.json [--out report.md]
  python3 golden_replay.py --base-url ... --golden ... \
      --baseline tests/golden/baseline.json \
      [--manifest declared-changes.json] [--out report.md]

Exit code 0 = verifiers clean and no undeclared diffs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

FILLER = ("Operational note: the nightly job archives the rotated logs "
          "to cold storage before the retention sweep runs, and the "
          "on-call engineer acknowledges the page within fifteen "
          "minutes. ")


def pad_body(body: dict, target_tokens: int) -> dict:
    """Approximately pad the last user message to target_tokens
    (gateway estimates ~4 chars/token) with neutral filler."""
    body = json.loads(json.dumps(body))
    msgs = body.get("messages") or []
    chars_now = sum(len(str(m.get("content") or "")) for m in msgs)
    need_chars = target_tokens * 4 - chars_now
    if need_chars > 0 and msgs:
        pad = (FILLER * (need_chars // len(FILLER) + 1))[:need_chars]
        msgs[-1]["content"] = (str(msgs[-1].get("content") or "")
                               + "\n\n" + pad)
    return body


def verify(item: dict, shadow: dict) -> list[str]:
    """Decision-level verifiers for one shadow response."""
    expect = item.get("expect") or {}
    out: list[str] = []
    if shadow.get("error"):
        return [f"shadow_error:{shadow['error']}"]
    if not shadow.get("would_route_to"):
        return ["no_route"]
    if expect.get("task_class") and \
            shadow.get("task_class") != expect["task_class"]:
        out.append(f"task_class:{shadow.get('task_class')}"
                   f"!={expect['task_class']}")
    if expect.get("task_class_in") and \
            shadow.get("task_class") not in expect["task_class_in"]:
        out.append(f"task_class:{shadow.get('task_class')}"
                   f" not in {expect['task_class_in']}")
    if expect.get("data_class") and \
            shadow.get("data_class") != expect["data_class"]:
        out.append(f"data_class:{shadow.get('data_class')}"
                   f"!={expect['data_class']}")
    cands = shadow.get("candidates") or []
    first = cands[0] if cands else {}
    if expect.get("first_lane_in") and \
            first.get("lane") not in expect["first_lane_in"]:
        out.append(f"first_lane:{first.get('lane')}"
                   f" not in {expect['first_lane_in']}")
    if expect.get("forbidden_first_lanes") and \
            first.get("lane") in expect["forbidden_first_lanes"]:
        out.append(f"first_lane:{first.get('lane')} is forbidden")
    if expect.get("first_choice_in") and \
            first.get("id") not in expect["first_choice_in"]:
        out.append(f"first_choice:{first.get('id')}"
                   f" not in {expect['first_choice_in']}")
    if expect.get("min_candidates") is not None and \
            len(cands) < expect["min_candidates"]:
        out.append(f"candidates:{len(cands)}<{expect['min_candidates']}")
    return out


def outcome_of(shadow: dict) -> dict:
    return {
        "would_route_to": (shadow.get("would_route_to") or {}).get("model"),
        # shadow candidates carry the entry id under "model"
        "candidates": [c.get("model") or c.get("id")
                       for c in shadow.get("candidates") or []],
        "task_class": shadow.get("task_class"),
        "data_class": shadow.get("data_class"),
        "level": shadow.get("level"),
    }


def tally(violations: dict, items: list[dict]) -> tuple[dict, dict]:
    """Split verifier violations into (new, known): an item may carry
    a `known_violation` note documenting a pre-existing finding
    (e.g. a classifier defect already logged); known violations stay
    visible in every report but do not fail the run — NEW ones do."""
    known_ids = {i["id"] for i in items if i.get("known_violation")}
    new = {k: v for k, v in violations.items() if k not in known_ids}
    known = {k: v for k, v in violations.items() if k in known_ids}
    return new, known


def diff_outcomes(base: dict, now: dict) -> list[dict]:
    """Per-item selection diffs between a baseline and a fresh run."""
    diffs = []
    for item_id, cur in now.items():
        old = base.get(item_id)
        if old is None:
            diffs.append({"item": item_id, "kind": "new_item"})
        elif old != cur:
            fields = [k for k in cur if old.get(k) != cur.get(k)]
            diffs.append({"item": item_id, "kind": "changed",
                          "fields": fields,
                          "from": {k: old.get(k) for k in fields},
                          "to": {k: cur.get(k) for k in fields}})
    for item_id in base:
        if item_id not in now:
            diffs.append({"item": item_id, "kind": "missing_item"})
    return diffs


def _shadow_call(base_url: str, key: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/shadow", method="POST",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"http_{e.code}"}
    except Exception as e:  # noqa: BLE001 — report, don't crash the set
        return {"error": f"transport:{type(e).__name__}"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--golden", required=True)
    ap.add_argument("--write-baseline")
    ap.add_argument("--baseline")
    ap.add_argument("--manifest",
                    help="JSON {item_id: reason} declaring expected diffs")
    ap.add_argument("--out", help="markdown report path")
    args = ap.parse_args(argv)

    golden = json.loads(Path(args.golden).read_text())
    items = golden["items"]
    results: dict[str, dict] = {}
    violations: dict[str, list[str]] = {}
    for it in items:
        key = os.environ.get(f"SR_KEY_{it['client'].upper()}", "")
        if not key:
            print(f"missing SR_KEY_{it['client'].upper()}", file=sys.stderr)
            return 2
        body = it["body"]
        if it.get("pad_to_tokens"):
            body = pad_body(body, int(it["pad_to_tokens"]))
        shadow = _shadow_call(args.base_url, key, body)
        results[it["id"]] = outcome_of(shadow) if not shadow.get("error") \
            else {"error": shadow["error"]}
        v = verify(it, shadow)
        if v:
            violations[it["id"]] = v

    if args.write_baseline:
        Path(args.write_baseline).write_text(json.dumps(
            {"golden_version": golden.get("version"), "items": results},
            indent=1))
        print(f"baseline written: {args.write_baseline}")

    diffs: list[dict] = []
    manifest = {}
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text())["items"]
        diffs = diff_outcomes(base, results)
        if args.manifest:
            manifest = json.loads(Path(args.manifest).read_text())
    undeclared = [d for d in diffs if d["item"] not in manifest]

    new_v, known_v = tally(violations, items)
    lines = ["# Golden replay report", ""]
    lines.append(f"items: {len(items)} · NEW verifier violations: "
                 f"{sum(len(v) for v in new_v.values())} · known: "
                 f"{sum(len(v) for v in known_v.values())} · "
                 f"diffs: {len(diffs)} ({len(undeclared)} undeclared)")
    for iid, vs in sorted(new_v.items()):
        lines.append(f"- VIOLATION {iid}: {', '.join(vs)}")
    for iid, vs in sorted(known_v.items()):
        lines.append(f"- KNOWN {iid}: {', '.join(vs)}")
    for d in diffs:
        tag = "declared" if d["item"] in manifest else "UNDECLARED"
        lines.append(f"- DIFF[{tag}] {d['item']}: {d['kind']} "
                     f"{d.get('fields', '')}")
    report = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(report)
    print(report)
    return 1 if (new_v or undeclared) else 0


if __name__ == "__main__":
    raise SystemExit(main())
