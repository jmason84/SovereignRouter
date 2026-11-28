#!/usr/bin/env python3
"""Price-drift tripwire — R5's input layer, borrowing LiteLLM's
cost-map pattern (2026-11-24; litellm-vs-sovereign borrow #1).

Compares every priced registry entry against LiteLLM's centrally
maintained ``model_prices_and_context_window.json`` and REPORTS
drift. It never writes the registry, never writes any file except an
explicit ``--out`` report, and its exit code is 0 unless ``--strict``
is passed and drift was found.

The F-04 rule stands and is the whole point of the tool's shape:
**aggregators nominate, providers decide.** A drift row is a
verification task — the value that eventually lands in the registry
still comes only from the provider's own source, recorded in the
entry's ``price_source``. This tool exists so drift is SEEN (the
F-04 wound was a wrong seed price nobody re-checked), not so it can
be auto-corrected from a third party's map.

Field schema borrowed from the map: per-token input / output /
cache-read rates, compared here per million tokens against the
registry's ``input_usd_per_million`` / ``output_usd_per_million`` /
``cached_input_usd_per_million``.

Matching: a registry entry is matched to the first LiteLLM key found
among, in order: the entry id itself (``openrouter/<model>`` entries
already use LiteLLM's key shape), ``<provider>/<upstream model>``,
and the bare upstream model id. Unmatched entries are reported as
unmatched — LiteLLM's coverage is broad but not total (four of our
providers are not in it at all), and an unmatched entry is a fact,
not an error.

Usage:
    python3 tools/price_check.py [--registry src/registry.json]
        [--map-file FILE | --map-url URL] [--tolerance 0.01]
        [--json] [--out report.md] [--strict]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hf_catalog  # noqa: E402  (v0.10.7: HF router catalog source)

DEFAULT_MAP_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json")

# Fields compared, registry name -> LiteLLM per-token field.
FIELDS = (
    ("input_usd_per_million", "input_cost_per_token"),
    ("output_usd_per_million", "output_cost_per_token"),
    ("cached_input_usd_per_million", "cache_read_input_token_cost"),
)


def load_litellm_map(map_file: Optional[str],
                     map_url: str) -> dict[str, Any]:
    """The LiteLLM cost map: {model_key: {input_cost_per_token, ...}}.
    The published file carries a ``sample_spec`` documentation entry,
    which is dropped."""
    if map_file:
        raw = Path(map_file).read_text(encoding="utf-8")
    else:
        req = urllib.request.Request(
            map_url, headers={"User-Agent": "sovereign-price-check"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("LiteLLM map is not a JSON object")
    data.pop("sample_spec", None)
    return data


def candidate_keys(entry: dict[str, Any]) -> list[str]:
    keys = [entry["id"]]
    provider, model = entry.get("provider", ""), entry.get("model", "")
    if provider and model:
        keys.append(f"{provider}/{model}")
    if model:
        keys.append(model)
    return keys


def compare_entry(entry: dict[str, Any], litellm: dict[str, Any],
                  tolerance: float) -> dict[str, Any]:
    """One registry entry vs the map. Returns a result dict with
    status matched|unmatched and, per compared field, both values
    (per million tokens) plus a drift flag. Fields the registry or
    the map does not carry are skipped, never treated as zero."""
    key = next((k for k in candidate_keys(entry) if k in litellm), None)
    if key is None:
        return {"id": entry["id"], "status": "unmatched", "fields": {}}
    remote = litellm[key]
    fields: dict[str, Any] = {}
    drift = False
    for reg_field, ll_field in FIELDS:
        reg_val = entry.get(reg_field)
        ll_raw = remote.get(ll_field)
        if reg_val is None or ll_raw is None:
            continue
        ll_val = float(ll_raw) * 1_000_000
        reg_f = float(reg_val)
        delta = abs(ll_val - reg_f)
        rel = delta / reg_f if reg_f else (0.0 if delta == 0 else 1.0)
        field_drift = rel > tolerance and delta > 0.0005
        drift = drift or field_drift
        fields[reg_field] = {
            "registry": reg_f, "litellm": ll_val, "drift": field_drift}
    return {"id": entry["id"], "status": "matched", "litellm_key": key,
            "fields": fields, "drift": drift}


def run_check(registry: dict[str, Any], litellm: dict[str, Any],
              tolerance: float) -> dict[str, Any]:
    results = [compare_entry(e, litellm, tolerance)
               for e in registry.get("models", [])
               if e.get("input_usd_per_million") is not None
               or e.get("output_usd_per_million") is not None]
    return {
        "entries_checked": len(results),
        "matched": sum(1 for r in results if r["status"] == "matched"),
        "unmatched": [r["id"] for r in results
                      if r["status"] == "unmatched"],
        "drifted": [r for r in results if r.get("drift")],
        "results": results,
    }


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Price-drift tripwire report",
        "",
        f"Entries checked: {summary['entries_checked']} · "
        f"matched: {summary['matched']} · "
        f"unmatched: {len(summary['unmatched'])} · "
        f"drifted: {len(summary['drifted'])}",
        "",
        "Drift rows are VERIFICATION TASKS (F-04: aggregators nominate,"
        " providers decide). Nothing was written to the registry.",
        "",
    ]
    for r in summary["drifted"]:
        lines.append(f"## {r['id']} (LiteLLM key: {r['litellm_key']})")
        for field, vals in r["fields"].items():
            if vals["drift"]:
                lines.append(
                    f"- {field}: registry {vals['registry']} vs "
                    f"LiteLLM {vals['litellm']:.6g} per million")
        lines.append("")
    if summary["unmatched"]:
        lines.append("## Unmatched (no LiteLLM key found)")
        for entry_id in summary["unmatched"]:
            lines.append(f"- {entry_id}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", default="src/registry.json")
    ap.add_argument("--map-file", default=None,
                    help="local LiteLLM map JSON (offline/tests)")
    ap.add_argument("--map-url", default=DEFAULT_MAP_URL)
    ap.add_argument("--tolerance", type=float, default=0.01,
                    help="relative drift threshold (default 1%%)")
    ap.add_argument("--json", action="store_true",
                    help="print the machine-readable summary")
    ap.add_argument("--out", default=None,
                    help="also write the Markdown report here")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when drift is found")
    ap.add_argument("--hf-file", default=None,
                    help="HF router catalog JSON (offline/tests); adds "
                         "the Hugging Face cross-check section")
    ap.add_argument("--hf-url", default=None,
                    help="fetch the public HF router catalog (no "
                         "credential is sent), e.g. "
                         + hf_catalog.DEFAULT_HF_URL)
    args = ap.parse_args(argv)

    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    litellm = load_litellm_map(args.map_file, args.map_url)
    summary = run_check(registry, litellm, args.tolerance)
    report = render_report(summary)
    if args.hf_file or args.hf_url:
        hf = hf_catalog.run_hf_check(
            registry, hf_catalog.load_hf_catalog(args.hf_file, args.hf_url),
            args.tolerance)
        summary["hf"] = hf
        report += "\n" + hf_catalog.render_hf_section(hf)
        summary["drifted_any"] = bool(summary["drifted"] or hf["drifted"])
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    print(json.dumps(summary, indent=2) if args.json else report)
    return 1 if (args.strict and (summary["drifted"]
                                   or summary.get("hf", {}).get("drifted"))) else 0


if __name__ == "__main__":
    sys.exit(main())
