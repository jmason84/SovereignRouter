"""v0.7.0 — streaming commit guard (ADD-06).

The failure this closes: the streaming producer's transport-error
handler used to fail over UNCONDITIONALLY. If model A had already
streamed visible text chunks to the client and its connection then
dropped, the gateway moved to model B and appended B's output to the
same stream — one response, two models' text, inseparable downstream.

The rule now: once a stream has committed visible output, a transport
failure TERMINATES the stream with a typed error event (no second
model is attempted, the route row carries
``stream_aborted_after_commit:<entry>``). Before the first visible
chunk, failover works exactly as before. The tool-call path is safe by
construction (chunks are buffered until validation, nothing is
committed), and stays on the pre-commit path.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from conftest import _all_text, _chunk, _sse, auth, build_app, chat_body, fake_upstream  # noqa: E402

PIN = "groq/gpt-oss-120b"
PIN_MODEL = "openai/gpt-oss-120b"


class _FailAfterChunks(httpx.AsyncByteStream):
    """Yields the given bytes, then drops the connection mid-stream."""

    def __init__(self, parts: list[bytes], request: httpx.Request):
        self._parts = parts
        self._request = request

    async def __aiter__(self):
        for part in self._parts:
            yield part
        raise httpx.ReadError("connection dropped mid-stream",
                              request=self._request)


def _handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    model = body["model"]
    text = _all_text(body)
    if model == PIN_MODEL and "MIDFAIL" in text:
        chunks = [
            _chunk(model, {"role": "assistant", "content": "AAA "}),
            _chunk(model, {"content": "BBB"}),
        ]
        payload = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks)
        return httpx.Response(
            200, stream=_FailAfterChunks([payload], request),
            headers={"content-type": "text/event-stream"})
    if model == PIN_MODEL and "PREFAIL" in text:
        raise httpx.ConnectError("connection refused", request=request)
    return fake_upstream(request)


def _custom_app(tmp_path):
    """build_app with the failing-stream transport swapped in."""
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "src"))
    import conftest
    from gateway import GatewayConfig, create_app

    # Reuse conftest's app construction by monkey-building the config
    # the same way build_app does, but with our transport.
    tmp_path.mkdir(parents=True, exist_ok=True)
    clients_file = tmp_path / "clients.json"
    import hashlib

    def _sha(v: str) -> str:
        return hashlib.sha256(v.encode()).hexdigest()

    from conftest import RAW_KEYS
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha(RAW_KEYS["admin"]),
        "clients": [
            {"id": "hermes", "key_sha256": _sha(RAW_KEYS["hermes"]),
             "lanes": ["private-free", "paid"], "premium_allowed": False,
             "daily_target_usd": 1.0},
        ],
    }))
    cfg = GatewayConfig(
        state_dir=tmp_path / "state",
        clients_path=clients_file,
        registry_path=_P(__file__).resolve().parent.parent / "src" / "registry.json",
        provider_keys={"groq": "fake", "openrouter": "fake",
                       "deepinfra": "fake", "ollama": "fake",
                       "openai": "fake", "deepseek": "fake", "zai": "fake"},
    )
    return create_app(cfg, upstream_transport=httpx.MockTransport(_handler))


def _last_record(app):
    return app.state.telemetry.recent(0)[-1]


def test_stream_failure_after_commit_terminates_without_second_model(tmp_path):
    app = _custom_app(tmp_path)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("MIDFAIL stream please",
                                       model=PIN, stream=True),
                        headers=auth("hermes"))
    assert r.status_code == 200  # headers were already committed
    # The client keeps model A's partial output...
    assert "AAA" in r.text and "BBB" in r.text
    # ...gets the typed interruption event...
    assert "stream_interrupted" in r.text
    # ...and NEVER a second model's output appended behind it.
    assert "Hello" not in r.text and "world" not in r.text
    rec = _last_record(app)
    assert f"stream_aborted_after_commit:{PIN}" in rec.signals
    assert {"model": PIN, "reason": "stream_failed_after_commit"} in rec.skipped
    assert rec.model == PIN
    assert rec.status == 502
    assert rec.upstream_failure is True


def test_stream_failure_before_commit_fails_over(tmp_path):
    app = _custom_app(tmp_path)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("PREFAIL stream please",
                                       model=PIN, stream=True),
                        headers=auth("hermes"))
    assert r.status_code == 200
    # The next candidate served the full stream (fake upstream text).
    assert "Hello" in r.text and "world" in r.text
    assert "stream_interrupted" not in r.text
    rec = _last_record(app)
    assert rec.status == 200
    assert rec.model != PIN
    assert {"model": PIN, "reason": "upstream_unreachable"} in rec.skipped
    assert not any(s.startswith("stream_aborted_after_commit")
                   for s in rec.signals)
