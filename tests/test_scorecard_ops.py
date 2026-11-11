"""Scorecard, telemetry rotation, throttled alert evaluation (2026-11-11)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import SRC, auth, build_app, chat_body
from governor import Governor
from quota import QuotaStore
from scorecard import build_scorecard, render_markdown
from telemetry import RouteRecord, Telemetry


def row(model="m1", tc="chat", **kw):
    base = dict(model=model, task_class=tc, status=200, cost_usd=0.01,
                input_tokens=1000, cached_tokens=0, latency_upstream_ms=100.0,
                escalated=False, escalation_reasons=[], tool_error_next_turn=False,
                repeat_within_60s=False, upstream_failure=False)
    base.update(kw)
    return base


def test_scorecard_metrics():
    rows = [row() for _ in range(8)] + [
        row(status=429, cost_usd=0.0), row(tool_error_next_turn=True),
        row(cached_tokens=500, latency_upstream_ms=300.0)]
    card = build_scorecard(rows)
    m = card["models"]["m1"]
    assert m["requests"] == 11 and m["share"] == 1.0
    assert m["rate_limited_rate"] == round(1 / 11, 4)
    assert m["tool_error_next_turn_rate"] == round(1 / 11, 4)
    assert m["success_rate"] == round(9 / 11, 4)
    assert m["cost_per_successful_turn_usd"] > m["cost_per_turn_usd"]
    assert m["cached_token_share"] == round(500 / 11000, 4)
    assert "chat" in m["by_task_class"]
    assert "| m1 |" in render_markdown(card)


def test_spillway_serves_are_not_quality_misses():
    rows = [row(escalated=True, escalation_reasons=["free_exhausted_paid_fallthrough"])
            for _ in range(25)]
    card = build_scorecard(rows)
    assert card["models"]["m1"]["success_rate"] == 1.0
    assert card["models"]["m1"]["escalation_rate"] == 0.0
    assert card["proposals"] == []


def test_proposal_needs_samples_and_is_owner_gated():
    few = build_scorecard([row(status=500) for _ in range(5)])
    assert few["proposals"] == []
    bad = build_scorecard([row(tool_error_next_turn=True) for _ in range(25)])
    p = bad["proposals"][0]
    assert p["model"] == "m1" and p["requires_owner_approval"] is True


def test_admin_scorecard_endpoint(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        c.post("/v1/chat/completions", json=chat_body("hi"), headers=auth("hermes"))
        assert c.get("/admin/scorecard").status_code == 401
        r = c.get("/admin/scorecard", headers=auth("admin"))
    assert r.status_code == 200 and r.json()["total_requests"] == 1


def test_scorecard_cli_reads_rotated_files(tmp_path):
    now = time.time()
    (tmp_path / "routes.jsonl.1").write_text(
        json.dumps(row(ts=now, model="old")) + "\n")
    (tmp_path / "routes.jsonl").write_text(
        json.dumps(row(ts=now, model="new")) + "\nnot json\n")
    out = tmp_path / "card.json"
    root = Path(__file__).resolve().parent.parent
    subprocess.run([sys.executable, str(root / "tools" / "scorecard.py"),
                    "--state", str(tmp_path), "--days", "1", "--json", str(out)],
                   check=True, capture_output=True)
    assert set(json.loads(out.read_text())["models"]) == {"old", "new"}


# ---- telemetry rotation ---------------------------------------------------
def _rec(i):
    return RouteRecord(ts=time.time(), client="c", level=1, task_class="chat",
                       classifier_layer="rules", classifier_confidence=0.9,
                       model=f"m{i}", lane="paid")


def test_telemetry_rotates_and_keeps_live_name(tmp_path):
    log = tmp_path / "routes.jsonl"
    t = Telemetry(log, rotate_bytes=600, keep=2)
    for i in range(12):
        t.write(_rec(i))
    t.close()
    assert log.exists()
    assert (tmp_path / "routes.jsonl.1").exists()
    assert not (tmp_path / "routes.jsonl.3").exists()      # keep=2 bound
    lines = [l for p in tmp_path.glob("routes.jsonl*") for l in p.read_text().splitlines()]
    assert all(json.loads(l)["client"] == "c" for l in lines)
    assert 0 < len(lines) <= 12


def test_telemetry_keeps_handle_open(tmp_path):
    t = Telemetry(tmp_path / "r.jsonl")
    t.write(_rec(0)); fh = t._fh
    t.write(_rec(1))
    assert t._fh is fh
    assert len((tmp_path / "r.jsonl").read_text().splitlines()) == 2


# ---- throttled alert evaluation ---------------------------------------------
def test_alert_evaluation_is_throttled(tmp_path):
    gov = Governor(QuotaStore(tmp_path / "q.db"))
    calls = []
    gov.evaluate_alerts = lambda *a, **k: calls.append(1)
    assert gov.evaluate_alerts_throttled({}, {}, now=1000.0) is True
    assert gov.evaluate_alerts_throttled({}, {}, now=1010.0) is False
    assert gov.evaluate_alerts_throttled({}, {}, now=1031.0) is True
    assert len(calls) == 2


def test_quota_store_uses_wal(tmp_path):
    s = QuotaStore(tmp_path / "q.db")
    mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
