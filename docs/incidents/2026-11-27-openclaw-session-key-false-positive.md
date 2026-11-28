# Incident: OpenClaw session key false-flagged as a credential (2026-11-15 → 2026-11-27)

**Status:** fixed in v0.10.4 (owner go 2026-11-27 14:23 MST). **Severity:** routing-quality/availability. This was a false positive. No secret was leaked by this mechanism.

## What happened
v0.9.1 (deployed 2026-11-15 03:51 MST) added the bare noun `session` to the `credential_assignment` detector (F-18, so that session-cookie values such as golden g180 are caught).

OpenClaw's system prompt includes a runtime line on every request:

`Runtime: agent=main | session=agent:main:main | host=… | repo=… | model=sovereign/auto`

The value `agent:main:main` (or `agent:main:telegram:direct:<chat id>`, `agent:devops-agent:cron:<uuid>`) is OpenClaw's session routing key. It is not a credential, but it matched `session\s*[:=]\s*<value>` and passed every value exclusion: it is not a UUID, not hex, not a placeholder, not templated and not elided.

## Evidence (Job ROUTER-J1-20261127, read-only, values never printed)
- **OpenClaw hourly flag rate, routes.jsonl:** 0 of 109 requests flagged on 2026-11-15 between 00:00 and 02:59 MST. From 04:00 MST every hour was 100% (4/4, 12/12, 5/5, …). Daily rates from Nov 16 to Nov 27 ran 95–99%.
- **Seven days to 2026-11-27 13:19 MST:** OpenClaw 1,173/1,207 flagged, every one by `credential_assignment` alone. Hermes was 134/1,548 and n8n 0/9,829.
- **Where the flagged OpenClaw requests were served:**
  - openrouter/free-router: 658
  - groq/gpt-oss-120b: 366
  - groq/gpt-oss-20b: 74
  - groq/qwen3.6-27b: 25
  - deepinfra/* (paid spillway): 50
- **Stored OpenClaw system-prompt events (VM102, Nov 27) carry the runtime line.** The router's own `scan_text` flags that line, and flags nothing once the `session=` field is removed.
- **No known-format detector fired for OpenClaw in the window.** That covers openai/groq/google/github keys, bearer tokens, PEM keys and basic-auth URLs.
- **OpenClaw's workspace bootstrap files are clean.** All 80 were scanned with zero hits.

## Fix (v0.10.4)
`sensitivity.py`: for the `session` noun only, skip values that fully match `^agent:[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.@+-]+)+$`. Random cookie and token values do not start with `agent:` and still flag. Other nouns are not affected. Values wrapped in markdown or punctuation are unwrapped first. A `cookie` earlier on the same line always keeps the flag, and that check is O(n log n). Tests: `tests/test_session_key_exclusion.py`. E2E harness and differential fuzz: `tests/e2e_session_key/` (results are in the CHANGELOG).

## Residual / follow-ups (owner decisions, not in this release)
- `sessionid=` / `session_token=` assignments are not in the noun list and were never flagged by `credential_assignment`.
- **Zoom join links with `pwd=` passcodes exist in older OpenClaw conversation history.** They are correctly flagged when present.
- **OpenClaw stores provider keys at rest** in its local cache, and an SSH private key sits in its workspace. Neither was observed in prompts.
- **`openrouter/free-router` sits in the private-free lane**, even though it forwards to arbitrary free upstreams.
