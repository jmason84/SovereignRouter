"""Phase 0 hardening (2026-11-11): emergency default, credential-scan
gaps, malformed-request handling."""

from __future__ import annotations

import importlib
import os

import pytest

from conftest import auth, chat_body
from sensitivity import scan_messages, scan_text


# ---- 0.1 emergency default is warn-only --------------------------------
def test_emergency_warn_only_is_default(monkeypatch):
    import gateway
    monkeypatch.delenv("SOVEREIGN_EMERGENCY_WARN_ONLY", raising=False)
    importlib.reload(gateway)
    assert gateway.GatewayConfig().emergency_warn_only is True
    monkeypatch.setenv("SOVEREIGN_EMERGENCY_WARN_ONLY", "0")
    importlib.reload(gateway)
    assert gateway.GatewayConfig().emergency_warn_only is False
    monkeypatch.delenv("SOVEREIGN_EMERGENCY_WARN_ONLY", raising=False)
    importlib.reload(gateway)


def test_deploy_files_default_warn_only():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "deploy"
    assert "SOVEREIGN_EMERGENCY_WARN_ONLY:-1" in (root / "docker-compose.v05.yml").read_text()
    assert "SOVEREIGN_EMERGENCY_WARN_ONLY=1" in (root / ".env.example").read_text()


# ---- 0.2 credential scan gaps -------------------------------------------
@pytest.mark.parametrize("text", [
    '{"password": "Sup3rS3cret!x"}',
    '{"api_key":"abcd1234efgh"}',
    "{'client_secret': 'zzzz9999yyyy'}",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk",
    "sk_live_51Habcdefghijklmnop",
    "rk_test_abcdefghijklmnop1234",
    "SG.abcdefghijklmnop1234.abcdefghijklmnop1234",
    "npm_" + "a" * 36,
    "hf_" + "a" * 34,
    "dop_v1_" + "a" * 64,
    "AccountKey=" + "A" * 44 + "==",
])
def test_new_formats_detected(text):
    assert scan_text(text).sensitive, text


@pytest.mark.parametrize("text", [
    '{"password": ""}',
    '{"api_key": "<redacted>"}',
    '{"secret": true}',
    "the password policy requires 12 characters",
    "commit 3e41a3dea6be61472f6f68925a7643fa5baf33dc",
    '{"id": "123e4567-e89b-12d3-a456-426614174000"}',
])
def test_new_formats_no_false_positive(text):
    assert not scan_text(text).sensitive, text


def test_tool_call_arguments_scanned():
    msgs = [{"role": "assistant", "content": None, "tool_calls": [
        {"function": {"name": "x",
                      "arguments": '{"k":"sk-abcdefghijklmnopqrstu"}'}}]}]
    assert scan_messages(msgs).sensitive


# ---- 0.3 malformed requests -> 400, never 500 ---------------------------
@pytest.mark.parametrize("override", [
    {"max_tokens": "abc"},
    {"max_tokens": -5},
    {"max_tokens": True},
    {"max_completion_tokens": "x"},
    {"stream": "maybe"},
    {"stream": 3},
    {"tools": "x"},
    {"tools": ["x"]},
])
def test_bad_fields_are_400(client, override):
    r = client.post("/v1/chat/completions",
                    json=chat_body("hi", **override), headers=auth("hermes"))
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_request"


def test_non_object_messages_are_400(client):
    r = client.post("/v1/chat/completions",
                    json={"messages": ["hi"]}, headers=auth("hermes"))
    assert r.status_code == 400


def test_shadow_validates_too(client):
    r = client.post("/v1/shadow", json=chat_body("hi", max_tokens="abc"),
                    headers=auth("hermes"))
    assert r.status_code == 400


def test_stream_false_string_is_not_streaming(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("hi", stream="false"), headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_numeric_string_max_tokens_accepted(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("hi", max_tokens="64"), headers=auth("hermes"))
    assert r.status_code == 200


def test_null_fields_accepted(client):
    r = client.post("/v1/chat/completions",
                    json=chat_body("hi", max_tokens=None, stream=None),
                    headers=auth("hermes"))
    assert r.status_code == 200
