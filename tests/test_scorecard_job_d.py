"""Job D (tools-only; owner go 2026-11-27 20:25 MST): nightly scorecard
that only writes PROPOSALS. It must never modify the quota DB or the
registry, must open the DB read-only, and must surface the proposals
the thresholds call for."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import scorecard  # noqa: E402
from quota import AttemptEvent, QuotaStore  # noqa: E402

NOW = time.time()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _route(client, model, lane, cls="chat", status=200, cost=0.0, sens=False,
           tool_err=False, esc=(), sig=(), shadow=None, ts=None):
    return {"ts": ts or NOW - 600, "client": client, "model": model, "lane": lane,
            "task_class": cls, "status": status, "cost_usd": cost,
            "data_class": "sensitive" if sens else "internal",
            "tool_error_next_turn": tool_err, "escalated": bool(esc),
            "escalation_reasons": list(esc), "signals": list(sig),
            "skipped": [], "tier_shadow": shadow}


@pytest.fixture
def state(tmp_path):
    st = tmp_path / "state"
    st.mkdir()
    store = QuotaStore(st / "quota.db")
    for i in range(40):
        store.record_tool_outcome("openrouter/free-router", i % 3 == 0, ts=NOW - 100 - i)
        store.record_tool_outcome("groq/gpt-oss-120b", i == 0, ts=NOW - 100 - i)
    for i in range(12):
        store.record_attempt(AttemptEvent(
            ts=NOW - 50 - i, client_id="n8n", provider="openrouter",
            model_id="openrouter/dead-free", pool_id="openrouter-free",
            outcome="failed", status=404, failure_class="entry_missing",
            scope="entry", scope_target="openrouter/dead-free", would_demote=None,
            fallback_depth=i))
    for i in range(30):
        store.record_attempt(AttemptEvent(
            ts=NOW - 40 - i, client_id="n8n", provider="cloudflare",
            model_id="cloudflare/gpt-oss-120b", pool_id="cloudflare-neurons",
            outcome="failed", status=429, failure_class="congested",
            scope="pool", scope_target="cloudflare-neurons", would_demote=None,
            fallback_depth=i % 8))
    del store
    rows = []
    for i in range(30):
        rows.append(_route("hermes", "groq/gpt-oss-120b", "private-free", "infra",
                           cost=0.1, tool_err=(i % 5 == 0)))
    for i in range(60):
        agree = i % 2 == 0
        rows.append(_route("n8n", "google/gemini-3.5-flash-lite", "public-only-free",
                           "code", shadow={"pick": "google/gemini-3.5-flash-lite" if agree else "unorouter/codestral-latest",
                                           "pick_tier": "free", "live_pick": "google/gemini-3.5-flash-lite",
                                           "live_tier": "free", "agree": agree, "task_class": "code",
                                           "tags": [], "low_confidence": False, "reason": "", "top": []}))
    rows.append(_route("openclaw", "groq/gpt-oss-120b", "private-free", sens=True))
    rows.append(_route("hermes", None, None, status=429, sig=("fallback_depth_cap:6",)))
    rows.append(_route("hermes", "x", "paid", ts=NOW - 10 * 86400))  # outside window
    (st / "routes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    return st


def test_scorecard_is_read_only_and_writes_only_out(state, tmp_path):
    db, rl = state / "quota.db", state / "routes.jsonl"
    reg = ROOT / "src" / "registry.json"
    before = (_sha(db), _sha(rl), _sha(reg), sorted(p.name for p in state.iterdir()))
    out = tmp_path / "out"
    assert scorecard.main(["--state", str(state), "--registry", str(reg),
                           "--hours", "24", "--until", str(NOW), "--out", str(out)]) == 0
    after = (_sha(db), _sha(rl), _sha(reg), sorted(p.name for p in state.iterdir()))
    assert before == after
    files = sorted(p.name for p in out.iterdir())
    assert len(files) == 2 and files[0].endswith(".json") and files[1].endswith(".md")


def test_db_opened_read_only(state):
    con = scorecard.open_ro(state / "quota.db")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM tool_outcomes")
    con.close()


def test_card_contents_and_proposals(state):
    rows = scorecard.load_routes(state / "routes.jsonl", NOW - 86400, NOW)
    assert len(rows) == 92                       # outside-window + junk dropped
    con = scorecard.open_ro(state / "quota.db")
    reg = scorecard.load_registry(ROOT / "src" / "registry.json")
    card = scorecard.build(rows, con, reg, NOW - 86400, NOW)
    con.close()
    h = card["clients"]["hermes"]
    assert h["requests"] == 31 and h["non_200"] == 1 and h["depth_cap_hits"] == 1
    assert h["tool_error_next_turn_rate"] == pytest.approx(6 / 31, abs=1e-3)
    assert card["clients"]["openclaw"]["sensitive_share"] == 1.0
    tools = {t["model"]: t for t in card["tool_outcomes"]}
    assert tools["openrouter/free-router"]["judged"] == 40
    assert card["attempts"]["max_fallback_depth"] == 11
    assert card["tier_shadow"]["rows"] == 60
    assert card["tier_shadow"]["disagree_by_class"]["code"]["share"] == 0.5
    kinds = {(p["kind"], p["target"]) for p in card["proposals"]}
    assert ("tool_quality", "openrouter/free-router") in kinds
    assert ("tool_quality", "groq/gpt-oss-120b") not in kinds
    assert ("dead_entry", "openrouter/dead-free") in kinds
    assert ("pool_pressure", "cloudflare-neurons") in kinds
    assert ("depth", "gateway") in kinds
    assert ("cost", "hermes") in kinds           # $3.00 vs $1.25
    assert card["clients"]["hermes"]["worst_day_cost_usd"] >= 3.0
    assert ("sensitivity", "openclaw") in kinds
    assert ("tier_policy", "code") in kinds
    assert all(p["status"].startswith("PROPOSED") for p in card["proposals"])
    md = scorecard.render_md(card)
    assert "Nothing was changed" in md and "P01" in md


def test_empty_state_is_fine(tmp_path):
    st = tmp_path / "s"
    st.mkdir()
    assert scorecard.main(["--state", str(st), "--hours", "1", "--stdout"]) == 0


def test_cron_ships_inactive():
    text = (ROOT / "deploy" / "cron" / "scorecard.cron").read_text()
    active = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert active == []


def test_dead_entry_already_disabled_is_not_proposed(state, tmp_path):
    reg = json.loads((ROOT / "src" / "registry.json").read_text())
    reg["models"].append({"id": "openrouter/dead-free", "provider": "openrouter",
                          "model": "x", "lane": "public-only-free", "quality": {"chat": 2},
                          "context_tokens": 1000, "enabled": False})
    rp = tmp_path / "reg.json"
    rp.write_text(json.dumps(reg))
    rows = scorecard.load_routes(state / "routes.jsonl", NOW - 86400, NOW)
    con = scorecard.open_ro(state / "quota.db")
    card = scorecard.build(rows, con, scorecard.load_registry(rp), NOW - 86400, NOW)
    con.close()
    assert ("dead_entry", "openrouter/dead-free") not in {(p["kind"], p["target"]) for p in card["proposals"]}


def test_worst_day_triggers_cost_proposal(tmp_path):
    st = tmp_path / "s"
    st.mkdir()
    rows = [_route("hermes", "groq/gpt-oss-120b", "private-free", cost=1.5, ts=NOW - 3600),
            _route("hermes", "groq/gpt-oss-120b", "private-free", cost=0.01, ts=NOW - 2 * 86400)]
    (st / "routes.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    card = scorecard.build(scorecard.load_routes(st / "routes.jsonl", NOW - 3 * 86400, NOW),
                           None, {}, NOW - 3 * 86400, NOW)
    assert card["clients"]["hermes"]["cost_per_day_usd"] < 1.25
    assert ("cost", "hermes") in {(p["kind"], p["target"]) for p in card["proposals"]}
