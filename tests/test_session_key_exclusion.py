"""v0.10.4 (2026-11-27, Job ROUTER-J1B-20261127): OpenClaw session keys
are routing identifiers, not credentials.

OpenClaw's system prompt carries a runtime line on every request
("Runtime: agent=main | session=agent:main:main | ...") and its /status
output prints "Session: agent:<id>:<scope>". Since v0.9.1 the bare
``session`` noun of credential_assignment matched that value and flagged
~97% of OpenClaw traffic sensitive. The fix excludes ONLY the
``agent:<agentId>:<scope>[:...]`` shape for the ``session`` noun; every
real cookie/token shape that flagged before still flags.

All token values below are synthetic test fixtures.
"""

from __future__ import annotations

import pytest

from sensitivity import scan_messages, scan_text

# Synthetic, random-looking value (not a real credential).
FAKE_TOKEN = "Zk9qx7Rb2LmT4vWp8Ns3Yd6Hc1Jf5Ga0Ke"

OPENCLAW_RUNTIME_LINES = [
    # main agent, direct session
    "Runtime: agent=main | session=agent:main:main | host=3718e29d0d93 | "
    "repo=/home/node/.openclaw/workspace/main | os=Linux 6.8.0 (x64) | "
    "node=v24.22.1 | model=sovereign/auto | default_model=sovereign/auto",
    # main agent, telegram direct session (numeric chat id)
    "Runtime: agent=main | session=agent:main:telegram:direct:8607462311 | "
    "host=3718e29d0d93 | model=sovereign/cheap",
    # devops agent, cron session with a UUID-style id
    "Runtime: agent=devops-agent | "
    "session=agent:devops-agent:cron:43022d56-cb5d-4a1e-9f00-0123456789ab | "
    "model=sovereign/auto",
    # /status output form
    "Context: 40k/131k (30%)\n\nSession: agent:devops-agent:main",
]


@pytest.mark.parametrize("text", OPENCLAW_RUNTIME_LINES)
def test_openclaw_session_key_not_flagged(text):
    r = scan_text(text)
    assert not r.sensitive, r.detectors


def test_openclaw_system_prompt_message_not_flagged():
    msgs = [
        {"role": "system", "content": "You are OpenClaw.\n"
                                      + OPENCLAW_RUNTIME_LINES[0]},
        {"role": "user", "content": "check disk usage on the NAS"},
    ]
    assert not scan_messages(msgs).sensitive


@pytest.mark.parametrize("text", [
    f"session={FAKE_TOKEN}",
    f"session: {FAKE_TOKEN}",
    f"Cookie: session={FAKE_TOKEN}",
    f"Set-Cookie: session={FAKE_TOKEN}; Path=/; HttpOnly",
    f"session=s:{FAKE_TOKEN}.sigABC123",           # express signed cookie
    "session=FAKESESSIONVALUE000",                   # golden g180 shape
    f"Runtime: agent=main | session={FAKE_TOKEN} | host=x",
])
def test_real_session_values_still_flag(text):
    r = scan_text(text)
    assert r.sensitive and "credential_assignment" in r.detectors


def test_exclusion_is_session_noun_only():
    # the agent:… shape is NOT a pass for other credential nouns
    assert scan_text("password=agent:main:main").sensitive
    assert scan_text("api_key=agent:main:main").sensitive
    assert scan_text("secret: agent:x:y").sensitive


def test_runtime_line_with_real_secret_elsewhere_still_flags():
    line = OPENCLAW_RUNTIME_LINES[0] + f"\npassword={FAKE_TOKEN}"
    r = scan_text(line)
    assert r.sensitive and "credential_assignment" in r.detectors


def test_prose_session_cookie_still_flags():
    r = scan_text(f"the session cookie is {FAKE_TOKEN}")
    assert r.sensitive and "credential_prose_assignment" in r.detectors


def test_known_format_detectors_unaffected():
    assert scan_text("Authorization: Bearer abcdef1234567890ABCDEF").sensitive
    assert scan_text("gsk_abcdefghijklmnopqrstuvwxyz0123456789").sensitive


# --- E2E loop round 1 (2026-11-27): wrapping + cookie-context guard -------

@pytest.mark.parametrize("text", [
    "my runtime says `session=agent:main:main` - what does it mean?",
    "**session=agent:main:main**",
    "(session=agent:main:main)",
    "The OpenClaw session=agent:main:main.",
    "set session='agent:main:main' in the config",
])
def test_wrapped_openclaw_session_key_not_flagged(text):
    assert not scan_text(text).sensitive


@pytest.mark.parametrize("text", [
    "Cookie: session=agent:main:main",
    "Set-Cookie: session=agent:x:y; Path=/",
    "cookie header -> session=agent:devops-agent:main",
])
def test_cookie_context_never_exempt(text):
    r = scan_text(text)
    assert r.sensitive and "credential_assignment" in r.detectors


def test_many_session_keys_on_one_huge_line_is_linear():
    # E2E perf finding 2026-11-27: unbounded cookie look-back was quadratic.
    import time
    big = "lorem ipsum session=agent:main:main | dolor " * 20000   # ~900k chars, one line
    t0 = time.perf_counter()
    r = scan_text(big)
    assert time.perf_counter() - t0 < 2.0
    assert not r.sensitive
    assert scan_text(big + f" password={FAKE_TOKEN}").sensitive
