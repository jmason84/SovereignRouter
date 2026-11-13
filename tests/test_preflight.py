"""v0.7.0 — deploy preflight tool (Cycle 1 aspect A, code-side basics).

tools/preflight.py is stdlib-only and read-only; these tests cover its
pure parts (hash compare, file report, health verdict, poll loop with
a fake fetcher/clock) so the checks themselves are verified before
anyone leans on them at a deploy gate.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "preflight", ROOT / "tools" / "preflight.py")
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)

MINIMAL_REGISTRY = {"models": [
    {"id": "a/x", "provider": "a", "model": "m", "lane": "paid",
     "quality": {"chat": 3}, "context_tokens": 8192},
]}


def _write_registry(tmp_path: Path, mode: int = 0o644) -> Path:
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(MINIMAL_REGISTRY))
    os.chmod(p, mode)
    return p


# ----------------------------------------------------------------------
# compare
# ----------------------------------------------------------------------

def test_compare_identical_and_drifted(tmp_path):
    a = _write_registry(tmp_path)
    b = tmp_path / "b.json"
    b.write_text(a.read_text())
    result = preflight.compare_hashes([a, b])
    assert result["identical"] is True
    assert result["md5"] == preflight.md5_of(a)

    b.write_text(a.read_text() + " ")
    assert preflight.compare_hashes([a, b])["identical"] is False
    assert preflight.compare_hashes([a, tmp_path / "missing.json"])["identical"] is False


# ----------------------------------------------------------------------
# check-file
# ----------------------------------------------------------------------

def test_file_report_world_readable_loads(tmp_path):
    p = _write_registry(tmp_path, 0o644)
    report = preflight.file_report(p)
    assert report["ok"] is True
    assert report["entries"] == 1


def test_file_report_gateway_unreadable_fails(tmp_path):
    # The crash-loop shape: root-owned 600 in the state dir. Judged
    # against the gateway uid, not the (root) test runner.
    p = _write_registry(tmp_path, 0o600)
    report = preflight.file_report(p, expected_uid=10001)
    if os.getuid() == 10001:
        return  # running AS the gateway uid: owner-read applies
    assert report["ok"] is False
    assert "FAIL" in report["checks"]["readable_by_gateway_uid"]


def test_file_report_broken_json_fails(tmp_path):
    p = tmp_path / "registry.json"
    p.write_text("{not json")
    os.chmod(p, 0o644)
    report = preflight.file_report(p)
    assert report["ok"] is False
    assert "FAIL" in report["checks"]["loads_via_gateway_loader"]


def test_file_report_missing_fails(tmp_path):
    report = preflight.file_report(tmp_path / "nope.json")
    assert report["ok"] is False


def test_file_report_real_registry(tmp_path):
    report = preflight.file_report(ROOT / "src" / "registry.json")
    assert report["ok"] is True
    assert report["entries"] == 81


# ----------------------------------------------------------------------
# health
# ----------------------------------------------------------------------

def test_health_verdict():
    ok, problems = preflight.health_verdict(
        {"status": "ok", "registry_models": 81}, 81)
    assert ok and problems == []
    ok, problems = preflight.health_verdict(
        {"status": "ok", "registry_models": 42}, 81)
    assert not ok and any("registry_models" in p for p in problems)
    ok, _ = preflight.health_verdict({"status": "down"}, None)
    assert not ok


def test_poll_health_recovers_and_times_out():
    clock = {"t": 0.0}
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("connection refused")
        return {"status": "ok", "registry_models": 81}

    ok, problems = preflight.poll_health(
        "http://x", 81, timeout_s=30.0, interval_s=2.0, fetch=flaky,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        now=lambda: clock["t"])
    assert ok and problems == []

    clock["t"] = 0.0
    ok, problems = preflight.poll_health(
        "http://x", 81, timeout_s=5.0, interval_s=2.0,
        fetch=lambda url: (_ for _ in ()).throw(OSError("refused")),
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        now=lambda: clock["t"])
    assert not ok and problems


def test_rollback_notes_mentions_the_gate():
    assert "registry_models" in preflight.ROLLBACK_NOTES
    assert "up -d" in preflight.ROLLBACK_NOTES
