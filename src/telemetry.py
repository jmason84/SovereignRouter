"""Telemetry: one JSON route line per request, plus outcome signals.

Spec ("Telemetry and learning loop"): each request writes one JSON line
with time, client, level, task class, classifier layer and confidence,
chosen model, skipped models with reasons, escalations, latency split
(classify / queue / upstream), token usage and cost. **Never prompt or
response text** — this module only accepts pre-shaped scalar fields, and
the gateway never passes message content into it.

Outcome signals recorded per request (they feed the nightly scoring):
  * ``escalated`` — output validation rejected the first answer;
  * ``upstream_failure`` — the chosen upstream errored;
  * ``repeat_within_60s`` — the same client re-sent an equivalent request
    fingerprint within 60 s (likely a retry of a turn it disliked);
  * ``tool_error_next_turn`` — the next turn of a sticky conversation
    carried a tool error (set via the sticky store).

The nightly job is a stub here (Phase 4): it turns outcome signals into
per-model, per-task-class quality scores and *proposes* registry changes
for owner approval — it never edits the registry itself.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class RouteRecord:
    ts: float
    client: str
    level: int
    task_class: str
    classifier_layer: str
    classifier_confidence: float
    model: Optional[str]                 # registry id actually used (final)
    lane: Optional[str]
    skipped: list[dict[str, str]] = field(default_factory=list)  # {model, reason}
    escalated: bool = False
    escalation_reasons: list[str] = field(default_factory=list)
    upstream_failure: bool = False
    status: int = 200
    latency_classify_ms: float = 0.0
    latency_queue_ms: float = 0.0
    latency_upstream_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    cost_estimate_usd: float = 0.0
    stream: bool = False
    sticky: bool = False
    data_class: str = "internal"
    sensitive_detectors: list[str] = field(default_factory=list)  # names only
    sidecar_fallback: bool = False
    override: Optional[str] = None       # alias / header that overrode routing
    repeat_within_60s: bool = False
    tool_error_next_turn: bool = False
    signals: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


class OutcomeTracker:
    """Detects 'same request repeated within 60 s' per client.

    The fingerprint is a hash of client + system + first/last user text +
    tool names — hashes only, no text stored.
    """

    WINDOW_S = 60.0

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(client_id: str, system_text: str, user_text: str,
                    tool_names: list[str]) -> str:
        h = hashlib.sha256()
        for part in (client_id, system_text or "", user_text or "",
                     ",".join(sorted(tool_names))):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def check_and_record(self, fingerprint: str, now: Optional[float] = None) -> bool:
        now = now or time.time()
        with self._lock:
            prev = self._seen.get(fingerprint)
            self._seen[fingerprint] = now
            # opportunistic cleanup
            if len(self._seen) > 4096:
                cutoff = now - self.WINDOW_S
                self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
            return prev is not None and (now - prev) <= self.WINDOW_S


class Telemetry:
    """Writes route lines to a JSONL file (if configured) and keeps a
    bounded in-memory ring for /admin/routes."""

    def __init__(self, log_path: Optional[str | Path] = None, ring_size: int = 10_000):
        self.log_path = Path(log_path) if log_path else None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._ring: deque[RouteRecord] = deque(maxlen=ring_size)
        self._lock = threading.Lock()
        self.outcomes = OutcomeTracker()

    def write(self, record: RouteRecord) -> None:
        line = record.to_json()
        with self._lock:
            self._ring.append(record)
            if self.log_path:
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")

    def recent(self, since: float) -> list[RouteRecord]:
        with self._lock:
            return [r for r in self._ring if r.ts >= since]

    def stats(self, since: float) -> dict[str, Any]:
        """Decision statistics for /admin/routes (last 24 h by default)."""
        records = self.recent(since)
        by_model: dict[str, int] = {}
        by_level: dict[str, int] = {}
        skip_reasons: dict[str, int] = {}
        total_cost = 0.0
        escalations = 0
        failures = 0
        for r in records:
            if r.model:
                by_model[r.model] = by_model.get(r.model, 0) + 1
            by_level[str(r.level)] = by_level.get(str(r.level), 0) + 1
            for s in r.skipped:
                skip_reasons[s["reason"]] = skip_reasons.get(s["reason"], 0) + 1
            total_cost += r.cost_usd
            escalations += 1 if r.escalated else 0
            failures += 1 if r.status >= 400 else 0
        n = len(records)
        return {
            "requests": n,
            "by_model": by_model,
            "by_level": by_level,
            "skip_reasons": skip_reasons,
            "escalation_rate": (escalations / n) if n else 0.0,
            "failure_rate": (failures / n) if n else 0.0,
            "total_cost_usd": total_cost,
        }


def nightly_scoring(records: list[RouteRecord]) -> dict[str, Any]:
    """Phase 4 stub: per-model, per-task-class quality scores from
    outcome signals. Returns *proposals* only — applying them to the
    registry needs owner approval (spec: Phase 4).

    Score starts at the model's observed success share: a request counts
    against a model when it escalated or its upstream failed.
    """
    buckets: dict[tuple[str, str], dict[str, int]] = {}
    for r in records:
        if not r.model:
            continue
        b = buckets.setdefault((r.model, r.task_class),
                               {"total": 0, "bad": 0})
        b["total"] += 1
        if r.escalated or r.upstream_failure or r.tool_error_next_turn:
            b["bad"] += 1
    scores: dict[str, dict[str, Any]] = {}
    proposals: list[dict[str, Any]] = []
    for (model, task_class), b in sorted(buckets.items()):
        success = 1.0 - (b["bad"] / b["total"] if b["total"] else 0.0)
        scores.setdefault(model, {})[task_class] = {
            "requests": b["total"], "success_rate": round(success, 4),
        }
        # Proposal heuristic (stub): >=20 samples and <85% success ->
        # propose lowering that model's quality for the task class by 1.
        if b["total"] >= 20 and success < 0.85:
            proposals.append({
                "model": model, "task_class": task_class,
                "action": "propose_quality_decrement",
                "evidence_requests": b["total"],
                "success_rate": round(success, 4),
                "requires_owner_approval": True,
            })
    return {"scores": scores, "proposals": proposals}
