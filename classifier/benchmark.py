#!/usr/bin/env python3
"""
Phase 2 classifier benchmark (spec v0.5.3 acceptance).

Loads held_out.jsonl (80 labeled examples, phrasings disjoint from the
training bank), runs the rules + embedding layers exactly as the sidecar
serves them (Arch-Router layer is reported separately and is NOT part of
this gate — it joins the request path only if its own p95 < 1.2 s test
passes on VM103), and reports:

  - exact level match %            (acceptance: >= 70%)
  - within-one level %             (reported; no longer the acceptance bar)
  - task-class exact match %
  - true L4/L5 classified <= L3 %  (acceptance: < 5% — the dangerous error)
  - per-level exact match + confusion counts
  - classify latency p50 / p95 (ms, in-process; sidecar HTTP adds ~1-3 ms)

Writes benchmark-results.json next to this file (served by GET /benchmark)
and prints a human report. Runnable anywhere the sidecar runs:

    python3 benchmark.py [--held-out held_out.jsonl] [--json]

Exit code 0 if both acceptance gates pass on the active embedder, else 1.
NOTE: with the TF-IDF fallback embedder these numbers are a floor. Re-run
with sentence-transformers (bge-small) installed before judging Phase 2.
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sidecar import Classifier  # noqa: E402


def pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def percentile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--held-out", default=str(HERE / "held_out.jsonl"))
    ap.add_argument("--examples", default=str(HERE / "examples.jsonl"))
    ap.add_argument("--json", action="store_true", help="print JSON only")
    args = ap.parse_args()

    held = [json.loads(l) for l in Path(args.held_out).read_text().splitlines() if l.strip()]
    clf = Classifier(examples_path=Path(args.examples))

    exact = within1 = task_exact = 0
    hard_total = hard_under = 0
    latencies = []
    layers = Counter()
    per_level_total = Counter()
    per_level_exact = Counter()
    confusion = defaultdict(Counter)
    misses = []

    for ex in held:
        t0 = time.perf_counter()
        got = clf.classify(ex["text"], client="benchmark")
        latencies.append((time.perf_counter() - t0) * 1000)
        layers[got["layer"]] += 1
        per_level_total[ex["level"]] += 1
        confusion[ex["level"]][got["level"]] += 1
        if got["level"] == ex["level"]:
            exact += 1
            per_level_exact[ex["level"]] += 1
        else:
            misses.append({"text": ex["text"], "true": ex["level"],
                           "got": got["level"], "layer": got["layer"],
                           "confidence": got["confidence"]})
        if abs(got["level"] - ex["level"]) <= 1:
            within1 += 1
        if got["task_class"] == ex["task_class"]:
            task_exact += 1
        if ex["level"] >= 4:
            hard_total += 1
            if got["level"] <= 3:
                hard_under += 1

    latencies.sort()
    results = {
        "held_out_n": len(held),
        "embedder": clf.embedder.name,
        "exact_level_pct": pct(exact, len(held)),
        "within_one_pct": pct(within1, len(held)),
        "task_class_exact_pct": pct(task_exact, len(held)),
        "l4_l5_total": hard_total,
        "l4_l5_underclassified_pct": pct(hard_under, hard_total),
        "latency_p50_ms": round(percentile(latencies, 0.50), 2),
        "latency_p95_ms": round(percentile(latencies, 0.95), 2),
        "layers": dict(layers),
        "per_level_exact_pct": {
            str(lvl): pct(per_level_exact[lvl], per_level_total[lvl])
            for lvl in sorted(per_level_total)
        },
        "confusion_true_to_pred": {
            str(lvl): dict(sorted(confusion[lvl].items()))
            for lvl in sorted(confusion)
        },
        "acceptance": {
            "exact_level_ge_70": pct(exact, len(held)) >= 70.0,
            "l4_l5_under_lt_5": pct(hard_under, hard_total) < 5.0,
        },
        "misses": misses,
    }

    (HERE / "benchmark-results.json").write_text(json.dumps(results, indent=2) + "\n")

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Held-out set:        {results['held_out_n']} examples")
        print(f"Embedder:            {results['embedder']}")
        print(f"Exact level match:   {results['exact_level_pct']}%  (gate >= 70%)")
        print(f"Within one level:    {results['within_one_pct']}%")
        print(f"Task class exact:    {results['task_class_exact_pct']}%")
        print(f"L4/L5 -> <=L3:       {results['l4_l5_underclassified_pct']}% of {hard_total} hard examples  (gate < 5%)")
        print(f"Latency p50 / p95:   {results['latency_p50_ms']} ms / {results['latency_p95_ms']} ms")
        print(f"Layers used:         {results['layers']}")
        print(f"Per-level exact:     {results['per_level_exact_pct']}")
        print(f"Confusion (true->pred): {results['confusion_true_to_pred']}")
        ok = results["acceptance"]["exact_level_ge_70"] and results["acceptance"]["l4_l5_under_lt_5"]
        print(f"ACCEPTANCE:          {'PASS' if ok else 'FAIL'}")
        if misses:
            print("\nMisses:")
            for m in misses:
                print(f"  true L{m['true']} got L{m['got']} [{m['layer']}, conf {m['confidence']}] {m['text'][:90]}")

    ok = results["acceptance"]["exact_level_ge_70"] and results["acceptance"]["l4_l5_under_lt_5"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
