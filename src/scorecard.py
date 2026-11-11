"""Per-model scorecard (Direction A, 2026-11-11).

Turns route rows (dicts, as written to routes.jsonl) into a per model x
task-class report: share of traffic, error / 429 rate, outcome-signal
rates (tool_error_next_turn, repeat_within_60s, escalation), cost per
turn and per *successful* turn, upstream latency percentiles, and cached
token share (the measurement Phase 4 depends on).

Pure functions over plain dicts, so it runs identically on the live
telemetry ring (``/admin/scorecard``) and on log files (``tools/scorecard.py``).
It only ever *proposes*: nothing here edits the registry — applying a
proposal needs owner approval (spec Phase 4; plan 3.1).
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

MIN_SAMPLES_FOR_PROPOSAL = 20
SUCCESS_RATE_FLOOR = 0.85
TOOL_ERROR_RATE_CEILING = 0.15


def _pct(sorted_vals: list[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def _is_error(r: dict[str, Any]) -> bool:
    return (r.get("status") or 200) >= 400


def _is_success(r: dict[str, Any]) -> bool:
    return not (_is_error(r) or r.get("escalated") and not _spillway_only(r)
                or r.get("tool_error_next_turn") or r.get("repeat_within_60s")
                or r.get("upstream_failure"))


def _spillway_only(r: dict[str, Any]) -> bool:
    """A paid-spillway serve is recorded as an 'escalation' with its own
    reason though nothing failed validation; it is not a quality miss."""
    reasons = r.get("escalation_reasons") or []
    return bool(reasons) and all(x == "free_exhausted_paid_fallthrough" for x in reasons)


def _bucket(rows: list[dict[str, Any]], total_rows: int) -> dict[str, Any]:
    n = len(rows)
    served = [r for r in rows if not _is_error(r)]
    ok = [r for r in rows if _is_success(r)]
    cost = sum(float(r.get("cost_usd") or 0.0) for r in rows)
    in_tok = sum(int(r.get("input_tokens") or 0) for r in rows)
    cached = sum(int(r.get("cached_tokens") or 0) for r in rows)
    lat = sorted(float(r.get("latency_upstream_ms") or 0.0) for r in served
                 if r.get("latency_upstream_ms"))
    return {
        "requests": n,
        "share": round(n / total_rows, 4) if total_rows else 0.0,
        "error_rate": round(sum(1 for r in rows if _is_error(r)) / n, 4),
        "rate_limited_rate": round(sum(1 for r in rows if r.get("status") == 429) / n, 4),
        "escalation_rate": round(sum(1 for r in rows
                                     if r.get("escalated") and not _spillway_only(r)) / n, 4),
        "tool_error_next_turn_rate": round(
            sum(1 for r in rows if r.get("tool_error_next_turn")) / n, 4),
        "repeat_within_60s_rate": round(
            sum(1 for r in rows if r.get("repeat_within_60s")) / n, 4),
        "success_rate": round(len(ok) / n, 4),
        "cost_usd": round(cost, 6),
        "cost_per_turn_usd": round(cost / n, 6),
        "cost_per_successful_turn_usd": round(cost / len(ok), 6) if ok else None,
        "upstream_latency_ms_p50": _pct(lat, 0.5),
        "upstream_latency_ms_p95": _pct(lat, 0.95),
        "input_tokens": in_tok,
        "cached_token_share": round(cached / in_tok, 4) if in_tok else 0.0,
    }


def build_scorecard(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Scorecard + owner-approval proposals from route rows."""
    rows = [r for r in rows if r.get("model")]
    total = len(rows)
    by_model: dict[str, list[dict[str, Any]]] = {}
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)
        by_cell.setdefault((r["model"], r.get("task_class") or "?"), []).append(r)

    models: dict[str, Any] = {}
    for model, mrows in sorted(by_model.items()):
        entry = _bucket(mrows, total)
        entry["by_task_class"] = {
            tc: _bucket(crows, total)
            for (m, tc), crows in sorted(by_cell.items()) if m == model}
        models[model] = entry

    proposals: list[dict[str, Any]] = []
    for (model, tc), crows in sorted(by_cell.items()):
        if len(crows) < MIN_SAMPLES_FOR_PROPOSAL:
            continue
        b = _bucket(crows, total)
        why = []
        if b["success_rate"] < SUCCESS_RATE_FLOOR:
            why.append(f"success_rate {b['success_rate']:.0%} < {SUCCESS_RATE_FLOOR:.0%}")
        if b["tool_error_next_turn_rate"] > TOOL_ERROR_RATE_CEILING:
            why.append(f"tool_error_next_turn_rate {b['tool_error_next_turn_rate']:.0%} "
                       f"> {TOOL_ERROR_RATE_CEILING:.0%}")
        if why:
            proposals.append({
                "model": model, "task_class": tc,
                "action": "propose_quality_decrement",
                "evidence_requests": len(crows), "reasons": why,
                "requires_owner_approval": True,
            })
    return {"total_requests": total, "models": models, "proposals": proposals}


def render_markdown(card: dict[str, Any], title: str = "Model scorecard") -> str:
    lines = [f"# {title}", "",
             f"{card['total_requests']} routed requests.", "",
             "| model | n | share | err | 429 | esc | tool-err | success | $/turn | $/ok turn | p50 ms | p95 ms | cached |",
             "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    def f(v, spec):
        return "-" if v is None else format(v, spec)
    for model, m in card["models"].items():
        lines.append(
            f"| {model} | {m['requests']} | {m['share']:.0%} | {m['error_rate']:.1%} "
            f"| {m['rate_limited_rate']:.1%} | {m['escalation_rate']:.1%} "
            f"| {m['tool_error_next_turn_rate']:.1%} | {m['success_rate']:.1%} "
            f"| {m['cost_per_turn_usd']:.4f} | {f(m['cost_per_successful_turn_usd'], '.4f')} "
            f"| {f(m['upstream_latency_ms_p50'], '.0f')} | {f(m['upstream_latency_ms_p95'], '.0f')} "
            f"| {m['cached_token_share']:.0%} |")
    lines.append("")
    if card["proposals"]:
        lines.append("## Proposals (owner approval required; nothing is applied automatically)")
        for p in card["proposals"]:
            lines.append(f"- {p['model']} / {p['task_class']}: lower quality by 1 — "
                         f"{'; '.join(p['reasons'])} (n={p['evidence_requests']})")
    else:
        lines.append("No proposals: no model x task class below the floors with enough samples.")
    return "\n".join(lines) + "\n"
