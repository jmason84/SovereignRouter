"""Fix pass 2026-11-03 — regression tests for the two burn-in failures.

Fix 1 (premium lane): entries flagged ``completion_tokens_param`` must
receive the output budget as ``max_completion_tokens``; OpenAI's gpt-5.5
rejects ``max_tokens`` with a 400, which surfaced as a 429 for every
premium call in the 2026-11-03 burn-in.

Fix 2 (escalation rate): a reasoning-flagged model that returns
finish_reason=length with no content gets ONE same-model retry with a
doubled budget before the gateway escalates. The retry is not an
escalation; a second length failure escalates normally.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from conftest import RAW_KEYS, _sha, auth, chat_body  # noqa: E402
from gateway import GatewayConfig, create_app  # noqa: E402
from registry import ModelEntry, load_registry  # noqa: E402


def _completion(model: str, content: str | None, *, finish: str = "stop") -> dict:
    return {
        "id": "chatcmpl-fake", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                  "total_tokens": 110},
    }


def build_recording_app(tmp_path: Path, handler, registry_path: Path | None = None,
                        provider_keys_extra: dict | None = None):
    """Gateway app whose upstream is `handler(model, body, call_no)`.

    `provider_keys_extra` merges additional fake provider keys over
    the base set (e.g. {"siliconflow": "fake"} for tests that need a
    normally-keyless provider to be selectable)."""
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha(RAW_KEYS["admin"]),
        "clients": [
            {"id": "hermes", "key_sha256": _sha(RAW_KEYS["hermes"]),
             "lanes": ["private-free", "paid"], "premium_allowed": False,
             "daily_target_usd": 1.0},
            {"id": "openclaw", "key_sha256": _sha(RAW_KEYS["openclaw"]),
             "lanes": ["private-free", "paid", "premium"],
             "premium_allowed": True, "daily_target_usd": 3.0,
             "premium_monthly_alert_usd": 20.0, "premium_daily_count_alert": 20},
            {"id": "n8n", "key_sha256": _sha(RAW_KEYS["n8n"]),
             "lanes": ["private-free", "paid"], "premium_allowed": False,
             "daily_target_usd": 0.5},
        ],
    }))
    calls: list[dict] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return handler(body["model"], body, len(calls))

    cfg = GatewayConfig(
        state_dir=tmp_path / "state",
        clients_path=clients_file,
        registry_path=registry_path or SRC / "registry.json",
        provider_keys={"groq": "fake", "openrouter": "fake",
                       "deepinfra": "fake", "ollama": "fake",
                       "openai": "fake", "deepseek": "fake", "zai": "fake",
                       **(provider_keys_extra or {})},
    )
    app = create_app(cfg, upstream_transport=httpx.MockTransport(transport_handler))
    return app, calls


# ----------------------------------------------------------------------
# registry flags
# ----------------------------------------------------------------------
def test_registry_flags_loaded():
    reg = load_registry(SRC / "registry.json")
    premium = reg.get("premium/gpt-5.5")
    assert premium.completion_tokens_param is True
    assert premium.reasoning is True
    for mid in ("groq/gpt-oss-20b", "groq/gpt-oss-120b", "ollama/gpt-oss-120b"):
        assert reg.get(mid).reasoning is True, mid
    # 2026-11-04: north-mini-code-free gained the reasoning flag — its
    # length_no_content signature (49 of 97 battery escalations on
    # 2026-11-03) matches the reasoning models, so the doubled-budget
    # same-model retry now applies to it too.
    north = reg.get("openrouter/north-mini-code-free")
    assert north.reasoning is True
    assert north.completion_tokens_param is False
    plain = reg.get("openrouter/dots-3-note-preview-free")
    assert plain.reasoning is False
    assert plain.completion_tokens_param is False


def test_model_entry_flag_defaults():
    e = ModelEntry(id="x/y", provider="groq", model="y", lane="paid",
                   quality={"chat": 3}, context_tokens=1024)
    assert e.completion_tokens_param is False
    assert e.reasoning is False


# ----------------------------------------------------------------------
# Fix 1: parameter rename for the premium model only
# ----------------------------------------------------------------------
def test_premium_sends_max_completion_tokens(tmp_path):
    def handler(model, body, n):
        return httpx.Response(200, json=_completion(model, "premium answer"))

    app, calls = build_recording_app(tmp_path, handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("summarise the quarter", model="premium",
                                       max_tokens=800),
                        headers=auth("openclaw"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "premium/gpt-5.5"
    assert len(calls) == 1
    sent = calls[0]
    assert sent["model"] == "gpt-5.5"
    assert sent.get("max_completion_tokens") == 800
    assert "max_tokens" not in sent


def test_non_premium_keeps_max_tokens(tmp_path):
    def handler(model, body, n):
        return httpx.Response(200, json=_completion(model, "plain answer"))

    app, calls = build_recording_app(tmp_path, handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("hello there", max_tokens=321),
                        headers=auth("hermes"))
    assert r.status_code == 200
    assert len(calls) == 1
    assert calls[0].get("max_tokens") == 321
    assert "max_completion_tokens" not in calls[0]


# ----------------------------------------------------------------------
# Fix 2: same-model retry on length_no_content for reasoning models
# ----------------------------------------------------------------------
def test_reasoning_length_failure_retries_same_model(tmp_path):
    seen: dict[str, int] = {}

    def handler(model, body, n):
        seen[model] = seen.get(model, 0) + 1
        if model == "openai/gpt-oss-120b" and seen[model] == 1:
            # first attempt: budget burned on reasoning, no content
            return httpx.Response(200, json=_completion(model, "",
                                                        finish="length"))
        return httpx.Response(200, json=_completion(model, f"answer from {model}"))

    app, calls = build_recording_app(tmp_path, handler)
    with TestClient(app) as client:
        # pinned: groq must be the first server (since 2026-11-09
        # it no longer wins small turns on cost — real prices now)
        r = client.post("/v1/chat/completions",
                        json=chat_body("restart the nginx container",
                                       model="groq/gpt-oss-120b"),
                        headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "groq/gpt-oss-120b"
    # the retry cleared it: this must NOT be recorded as an escalation
    assert r.headers["X-Sovereign-Escalated"] == "false"
    oss_calls = [c for c in calls if c["model"] == "openai/gpt-oss-120b"]
    assert len(oss_calls) == 2
    assert oss_calls[1]["max_tokens"] == oss_calls[0]["max_tokens"] * 2


def test_reasoning_retry_only_once_then_escalates(tmp_path):
    def handler(model, body, n):
        if model == "openai/gpt-oss-120b":
            return httpx.Response(200, json=_completion(model, "",
                                                        finish="length"))
        return httpx.Response(200, json=_completion(model, f"answer from {model}"))

    app, calls = build_recording_app(tmp_path, handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("restart the nginx container",
                                       model="groq/gpt-oss-120b"),
                        headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Escalated"] == "true"
    assert r.headers["X-Sovereign-Model"] != "groq/gpt-oss-120b"
    oss_calls = [c for c in calls if c["model"] == "openai/gpt-oss-120b"]
    assert len(oss_calls) == 2  # original + exactly one retry


def _registry_without_reasoning(tmp_path: Path, model_id: str) -> Path:
    """Copy of the production registry with one model's reasoning flag
    stripped — lets mechanism tests pin behaviour for a non-reasoning
    entry without depending on production flag state."""
    data = json.loads((SRC / "registry.json").read_text())
    for m in data["models"]:
        if m["id"] == model_id:
            m.pop("reasoning", None)
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(data))
    return p


def test_non_reasoning_length_failure_escalates_without_retry(tmp_path):
    # A model WITHOUT the reasoning flag: a length failure must go
    # straight to escalation (no same-model retry). Uses north-mini's
    # routing shape (forced first candidate via a large prompt — Groq
    # TPM window skips Groq) but with the flag stripped by fixture,
    # because production north-mini IS flagged since 2026-11-04 (see
    # test_north_mini_length_failure_retries_same_model below).
    def handler(model, body, n):
        if model == "cohere/north-mini-code:free":
            return httpx.Response(200, json=_completion(model, "",
                                                        finish="length"))
        return httpx.Response(200, json=_completion(model, f"answer from {model}"))

    reg = _registry_without_reasoning(tmp_path, "openrouter/north-mini-code-free")
    app, calls = build_recording_app(tmp_path, handler, registry_path=reg)
    big = "word " * 15000
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=chat_body(big),
                        headers=auth("hermes"))
    assert r.status_code == 200
    north_calls = [c for c in calls
                   if c["model"] == "cohere/north-mini-code:free"]
    assert len(north_calls) <= 1  # never retried on the same model


def test_north_mini_length_failure_retries_same_model(tmp_path):
    # Production registry (2026-11-04): north-mini-code-free IS
    # reasoning-flagged, so a length_no_content first attempt earns one
    # same-model retry at doubled budget instead of an escalation.
    # 2026-11-12 (expansion phase 2): the request PINS the model —
    # north's chat quality was lowered to 2 on Article Engine drafting
    # evidence, so auto-selection no longer picks it for a big
    # chat-class request (that is the correction working); the retry
    # mechanic itself is what this test exercises, via the pin.
    seen: dict[str, int] = {}

    def handler(model, body, n):
        seen[model] = seen.get(model, 0) + 1
        if model == "cohere/north-mini-code:free" and seen[model] == 1:
            return httpx.Response(200, json=_completion(model, "",
                                                        finish="length"))
        return httpx.Response(200, json=_completion(model, f"answer from {model}"))

    app, calls = build_recording_app(tmp_path, handler)
    big = "word " * 15000
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body(big,
                                       model="openrouter/north-mini-code-free"),
                        headers=auth("hermes"))
    assert r.status_code == 200
    assert r.headers["X-Sovereign-Model"] == "openrouter/north-mini-code-free"
    assert r.headers["X-Sovereign-Escalated"] == "false"
    north_calls = [c for c in calls
                   if c["model"] == "cohere/north-mini-code:free"]
    assert len(north_calls) == 2  # original + exactly one retry
    assert north_calls[1]["max_tokens"] == north_calls[0]["max_tokens"] * 2
