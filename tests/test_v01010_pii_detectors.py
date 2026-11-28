"""v0.10.10 (ROUTER-STRESS-20261128): SSN + payment-card detectors behind
the owner policy flag SOVEREIGN_PII_DETECTORS (default off = log-only).

Live 2026-11-28 (v0.10.3) and the v0.10.8 branch both first-routed a
fake SSN + test card number to a public-only-free lane (data_class
internal). With the flag ON the turn is sensitive and public lanes are
refused; with it OFF routing is unchanged and the route row carries a
``pii_log_only:`` signal so the false-positive rate can be measured
before the owner decides.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import auth, build_app, chat_body  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sensitivity import scan_pii_text  # noqa: E402

PII = "Patient Pat Example, SSN 078-05-1120, card 4111 1111 1111 1111. Summarise."


@pytest.mark.parametrize("text,expected", [
    ("SSN 078-05-1120", ["pii_ssn"]),
    ("card 4111 1111 1111 1111", ["pii_payment_card"]),
    ("4111-1111-1111-1111", ["pii_payment_card"]),
    ("5555555555554444", ["pii_payment_card"]),
    ("amex 378282246310005", ["pii_payment_card"]),
    ("discover 6011111111111117", ["pii_payment_card"]),
    (PII, ["pii_payment_card", "pii_ssn"]),
])
def test_pii_detected(text, expected):
    assert sorted(scan_pii_text(text)) == expected


@pytest.mark.parametrize("text", [
    "order 1234567812345678", "phone 605-555-1234", "ssn 000-12-3456", "666-12-3456",
    "912-34-5678", "078-05-0000", "078-00-1120", "date 2026-11-28", "ip 192.168.0.166",
    "commit 4f9d2a1c0b7e", "id 4111111111111112", "ts 1795851313.123456",
    "uuid 123e4567-e89b-12d3-a456-426614174000", "range 100-20-3000-4000",
    "part no 078-05-11201", "x" * 5000,
])
def test_pii_negatives(text):
    assert scan_pii_text(text) == []


def _routes(tmp_path):
    p = tmp_path / "state" / "routes.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_default_is_log_only(tmp_path):
    app = build_app(tmp_path)
    assert app.state.cfg.pii_detectors is False
    with TestClient(app) as tc:
        sh = tc.post("/v1/shadow", json=chat_body(PII), headers=auth("hermes")).json()
        assert sh.get("data_class") != "sensitive"
        r = tc.post("/v1/chat/completions", json=chat_body(PII), headers=auth("hermes"))
        assert r.status_code == 200
    row = _routes(tmp_path)[-1]
    assert row["data_class"] != "sensitive"
    assert "pii_log_only:pii_payment_card,pii_ssn" in row["signals"]
    assert "078-05-1120" not in json.dumps(row)


def test_flag_on_promotes_to_sensitive(tmp_path):
    app = build_app(tmp_path, pii_detectors=True)
    with TestClient(app) as tc:
        sh = tc.post("/v1/shadow", json=chat_body(PII), headers=auth("hermes")).json()
        assert sh.get("data_class") == "sensitive"
        assert {c["lane"] for c in sh.get("candidates", [])} <= {"private-free", "paid", "premium"}
        r = tc.post("/v1/chat/completions", json=chat_body(PII), headers=auth("hermes"))
        assert r.status_code in (200, 429)
        assert r.headers.get("x-sovereign-lane") != "public-only-free"
    row = _routes(tmp_path)[-1]
    assert row["data_class"] == "sensitive"
    assert {"pii_ssn", "pii_payment_card"} <= set(row["sensitive_detectors"])
    assert not any(s.startswith("pii_log_only") for s in row["signals"])


def test_flag_on_plain_text_unchanged(tmp_path):
    app = build_app(tmp_path, pii_detectors=True)
    with TestClient(app) as tc:
        sh = tc.post("/v1/shadow", json=chat_body("summarise a fable in one line"), headers=auth("hermes")).json()
    assert sh.get("data_class") != "sensitive"
