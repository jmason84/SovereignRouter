"""v0.8.1 fix package — tool-bearing default output budget (F-09).

Origin: the route-health investigation
(docs/cycles/route-health-investigation-protocol.md §7) proved the
Hermes write_file stream stalls were ours-config: the gateway's
DEFAULT_MAX_TOKENS = 2048 bound every client that omits max_tokens,
Hermes omits it on every turn, and tool-call payloads — the longest
outputs those clients produce — truncated mid-stream at exactly
2048 (finish=length), while the model's published completion
ceiling is 460,800.

The fix: when the client omits max_tokens, the default budget is
TOOL_DEFAULT_MAX_TOKENS (8192) for tool-bearing requests and stays
DEFAULT_MAX_TOKENS (2048) for plain chat. Tool-bearing is read from
the request's own shape: a non-empty ``tools`` array, or an explicit
``tool_choice``.

Invariants pinned here (they must NOT move):
* a client-sent max_tokens passes through untouched, both shapes;
* the remaining-context clamp still applies on top of either
  default (proven by parity with a client-sent value in a
  tight-context variant);
* the budget is still written explicitly upstream — under
  max_completion_tokens for completion_tokens_param entries;
* the streaming path shares the same body builder, so it gets the
  same budgets (parity proven below).

The app built here uses a recording MockTransport so tests assert
on the exact body each upstream would have received (the
test_effort_and_gemma_shaping.py pattern).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from conftest import RAW_KEYS, _chunk, _completion, _sha, _sse, auth, chat_body  # noqa: E402
from gateway import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    TOOL_DEFAULT_MAX_TOKENS,
    GatewayConfig,
    _default_max_tokens,
    create_app,
)

SRC = Path(__file__).resolve().parent.parent / "src"
PROD_REGISTRY = SRC / "registry.json"

PROVIDER_KEYS = {"openrouter": "fake", "openai": "fake", "groq": "fake",
                 "deepinfra": "fake", "ollama": "fake", "deepseek": "fake",
                 "zai": "fake"}

# The F-09 entry itself: the free lane's 512k-context server for
# Hermes' longest sessions.
DOTS3 = "openrouter/dots-3-note-preview-free"
DOTS3_MODEL = "dots-studio/dots-3-note-preview:free"

TOOLS = [{"type": "function",
          "function": {"name": "write_file",
                       "description": "Write text to a file.",
                       "parameters": {"type": "object",
                                      "properties": {
                                          "path": {"type": "string"},
                                          "content": {"type": "string"}},
                                      "required": ["path", "content"]}}}]


def build_recording_app(tmp_path: Path, registry_path: Path = PROD_REGISTRY):
    """Gateway app (production-shaped client lanes) + the list of
    upstream bodies it sent, in attempt order."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps({
        "admin_key_sha256": _sha(RAW_KEYS["admin"]),
        "clients": [
            {"id": "hermes", "key_sha256": _sha(RAW_KEYS["hermes"]),
             "lanes": ["private-free", "paid", "public-only-free"],
             "premium_allowed": False, "daily_target_usd": 1.0},
            {"id": "openclaw", "key_sha256": _sha(RAW_KEYS["openclaw"]),
             "lanes": ["private-free", "paid", "premium", "public-only-free"],
             "premium_allowed": True, "daily_target_usd": 3.0},
            {"id": "n8n", "key_sha256": _sha(RAW_KEYS["n8n"]),
             "lanes": ["private-free", "paid", "public-only-free"],
             "premium_allowed": False, "daily_target_usd": 0.5},
        ],
    }))
    sent: list[dict] = []

    def recording_upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body.get("stream"):
            chunks = [
                _chunk(body["model"], {"role": "assistant", "content": "ok"}),
                _chunk(body["model"], {}, finish="stop"),
            ]
            usage = {"prompt_tokens": 100, "completion_tokens": 6,
                     "total_tokens": 106}
            return httpx.Response(
                200, content=_sse(chunks, usage),
                headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=_completion(body["model"], "ok"))

    cfg = GatewayConfig(state_dir=tmp_path / "state",
                        registry_path=registry_path,
                        clients_path=clients_file,
                        provider_keys=PROVIDER_KEYS)
    app = create_app(cfg, upstream_transport=httpx.MockTransport(recording_upstream))
    return app, sent


def bodies_for(sent: list[dict], upstream_model: str) -> list[dict]:
    return [b for b in sent if b.get("model") == upstream_model]


@pytest.fixture()
def rec(tmp_path):
    app, sent = build_recording_app(tmp_path)
    with TestClient(app) as tc:
        yield tc, sent


def post(tc, who="hermes", **overrides):
    body = chat_body("write the file please", model=DOTS3, **overrides)
    return tc.post("/v1/chat/completions", json=body, headers=auth(who))


# ----------------------------------------------------------------------
# the predicate, unit-level
# ----------------------------------------------------------------------
def test_default_max_tokens_predicate_matrix():
    assert _default_max_tokens({}) == DEFAULT_MAX_TOKENS
    assert _default_max_tokens({"messages": []}) == DEFAULT_MAX_TOKENS
    # an empty tools array is NOT tool-bearing (non-empty required)
    assert _default_max_tokens({"tools": []}) == DEFAULT_MAX_TOKENS
    assert _default_max_tokens({"tools": TOOLS}) == TOOL_DEFAULT_MAX_TOKENS
    # an explicit tool_choice is tool-bearing, "none" included: the
    # client has stated a tool-calling shape for the request
    assert _default_max_tokens({"tool_choice": "auto"}) == TOOL_DEFAULT_MAX_TOKENS
    assert _default_max_tokens({"tool_choice": "none"}) == TOOL_DEFAULT_MAX_TOKENS
    assert _default_max_tokens(
        {"tool_choice": {"type": "function",
                         "function": {"name": "write_file"}}}
    ) == TOOL_DEFAULT_MAX_TOKENS
    assert DEFAULT_MAX_TOKENS == 2048
    assert TOOL_DEFAULT_MAX_TOKENS == 8192


# ----------------------------------------------------------------------
# the defaults, on the wire (pinned to the F-09 entry itself)
# ----------------------------------------------------------------------
def test_tool_bearing_unset_defaults_to_8192(rec):
    tc, sent = rec
    r = post(tc, tools=TOOLS)
    assert r.status_code == 200
    bodies = bodies_for(sent, DOTS3_MODEL)
    assert bodies and bodies[0]["max_tokens"] == TOOL_DEFAULT_MAX_TOKENS


def test_plain_chat_unset_keeps_2048(rec):
    tc, sent = rec
    r = post(tc)
    assert r.status_code == 200
    bodies = bodies_for(sent, DOTS3_MODEL)
    assert bodies and bodies[0]["max_tokens"] == DEFAULT_MAX_TOKENS


def test_empty_tools_array_keeps_2048(rec):
    tc, sent = rec
    r = post(tc, tools=[])
    assert r.status_code == 200
    bodies = bodies_for(sent, DOTS3_MODEL)
    assert bodies and bodies[0]["max_tokens"] == DEFAULT_MAX_TOKENS


def test_explicit_tool_choice_defaults_to_8192(rec):
    tc, sent = rec
    r = post(tc, tool_choice="auto")
    assert r.status_code == 200
    bodies = bodies_for(sent, DOTS3_MODEL)
    assert bodies and bodies[0]["max_tokens"] == TOOL_DEFAULT_MAX_TOKENS


# ----------------------------------------------------------------------
# client-sent values always pass through untouched (both shapes)
# ----------------------------------------------------------------------
def test_tool_bearing_client_max_tokens_passes_through(rec):
    tc, sent = rec
    r = post(tc, tools=TOOLS, max_tokens=4096)
    assert r.status_code == 200
    bodies = bodies_for(sent, DOTS3_MODEL)
    assert bodies and bodies[0]["max_tokens"] == 4096


def test_plain_chat_client_max_tokens_passes_through(rec):
    tc, sent = rec
    r = post(tc, max_tokens=8192)
    assert r.status_code == 200
    bodies = bodies_for(sent, DOTS3_MODEL)
    assert bodies and bodies[0]["max_tokens"] == 8192


# ----------------------------------------------------------------------
# the remaining-context clamp still wins over the raised default
# ----------------------------------------------------------------------
def test_tool_bearing_unset_clamp_still_wins(tmp_path):
    # Tight-context variant of the F-09 entry: room is a few thousand
    # tokens, well under the raised default. The unset request must
    # land on exactly the budget a client-sent 8192 lands on in the
    # same variant — the clamp applies identically on top of the new
    # default (parity proof, not a re-derivation of the clamp).
    data = json.loads(PROD_REGISTRY.read_text())
    for m in data["models"]:
        if m["id"] == DOTS3:
            m["context_tokens"] = 4096
    variant = tmp_path / "registry-tight.json"
    variant.write_text(json.dumps(data))
    app, sent = build_recording_app(tmp_path / "v", registry_path=variant)
    with TestClient(app) as tc:
        r1 = post(tc, tools=TOOLS, max_tokens=8192)
        r2 = post(tc, tools=TOOLS)
    assert r1.status_code == 200 and r2.status_code == 200
    bodies = bodies_for(sent, DOTS3_MODEL)
    assert len(bodies) == 2
    assert bodies[1]["max_tokens"] == bodies[0]["max_tokens"]
    assert bodies[0]["max_tokens"] < TOOL_DEFAULT_MAX_TOKENS


# ----------------------------------------------------------------------
# the budget is still written explicitly — under the name a
# completion_tokens_param upstream accepts
# ----------------------------------------------------------------------
def test_tool_bearing_unset_writes_max_completion_tokens_for_gpt55(rec):
    tc, sent = rec
    r = tc.post("/v1/chat/completions",
                json=chat_body("write the file please",
                               model="premium/gpt-5.5", tools=TOOLS),
                headers=auth("openclaw"))
    assert r.status_code == 200
    bodies = bodies_for(sent, "gpt-5.5")
    assert bodies, "gpt-5.5 entry was not attempted"
    assert "max_tokens" not in bodies[0]
    assert bodies[0]["max_completion_tokens"] == TOOL_DEFAULT_MAX_TOKENS


# ----------------------------------------------------------------------
# streaming parity: the shared body builder gives the stream path
# the same budgets (v0.8.0 composed the stream handler record-first;
# the budget logic it calls is this one)
# ----------------------------------------------------------------------
def test_streaming_tool_bearing_unset_defaults_to_8192(rec):
    tc, sent = rec
    r = post(tc, tools=TOOLS, stream=True)
    assert r.status_code == 200
    assert "data:" in r.text
    bodies = bodies_for(sent, DOTS3_MODEL)
    assert bodies and bodies[0]["max_tokens"] == TOOL_DEFAULT_MAX_TOKENS
    assert bodies[0]["stream"] is True


def test_streaming_plain_chat_unset_keeps_2048(rec):
    tc, sent = rec
    r = post(tc, stream=True)
    assert r.status_code == 200
    assert "data:" in r.text
    bodies = bodies_for(sent, DOTS3_MODEL)
    assert bodies and bodies[0]["max_tokens"] == DEFAULT_MAX_TOKENS
