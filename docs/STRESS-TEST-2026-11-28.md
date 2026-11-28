# Sovereign Router stress test - 2026-11-28 (ROUTER-STRESS-20261128)

Owner ask: "Test, retest, and keep testing until solid. Use Hermes and openclaw trying wide variety. Try to break it and fix it" / "Stress test it".
Window: 2026-11-27 ~22:30 MT to 2026-11-28 ~00:55 MT. Nothing on VM103/VM112/VM102 was reconfigured, restarted or reloaded; the live gateway was only sent test prompts.

## What was tested

| Target | Version | How |
|---|---|---|
| Live gateway (VM103 container `sovereign-router-v05-gateway`) | 0.10.3 (code sha256-matched to `1133f38`) | direct to container IP from VM103 with the `crm` client key (no premium, keeps tests out of Hermes/OpenClaw budgets) |
| Hermes CLI (VM112) | live | `hermes -z` prompts only |
| OpenClaw CLI (VM102) | live | `openclaw agent --session-id stress-* --model sovereign/auto` prompts only, no `--deliver` |
| Local live-equivalent | `1133f38` | uvicorn + classifier sidecar + mock upstream on the box (no Docker on the box) |
| Local branch | `feat/v0.10.8-orca-kilo` (reports 0.10.7) | same stack |
| Local fix | `fix/v0.10.10-pii-detectors` (includes v0.10.9), `SOVEREIGN_PII_DETECTORS=1` | same stack |

The matrix (82 cases, 15 categories) covers basic, streaming, coding, JSON output, tools, long context (~18k tokens with needle), sensitive/PII, malformed bodies, auth, model aliases/unknown models, unicode (emoji, RTL, zero-width, lone surrogates), reasoning_effort variants, concurrent conversations, misc (huge max_tokens, empty messages) and endpoints. The mock upstream logs which lane received any sensitive marker (marker names only).

## Matrix results (pass / total by category)

| Category | Live direct (crm) | Local live-eq (hermes) | Local branch (hermes) | Local fix + PII on (hermes / crm / openclaw) |
|---|---|---|---|---|
| basic | 1/1 | 1/1 | 1/1 | 1/1 all |
| streaming | 2/2 | 2/2 | 2/2 | 2/2 all |
| coding | 2/2 | 2/2 | 2/2 | 2/2 all |
| json | 2/2 | 2/2 | 2/2 | 2/2 all |
| tools | 5/5 | 5/5 | 5/5 | 5/5 all |
| malformed | 19/25 | 22/25 | 22/25 | 25/25 all |
| long_context | 2/2 | 2/2 | 2/2 | 2/2 all |
| sensitive | 8/8 (crm has no public lane) | 6/8 | 6/8 | 8/8 all |
| auth | 2/2 | 2/2 | 2/2 | 2/2 all |
| models | 6/6 | 6/6 | 6/6 | 6/6 (openclaw 5/6: premium alias is allowed for openclaw, test expectation only) |
| unicode | 5/6 | 5/6 | 5/6 | 6/6 all |
| reasoning_effort | 8/8 | 8/8 | 8/8 | 8/8 all |
| concurrency | 4/7 | 7/7 | 7/7 | 7/7 all |
| misc / endpoints | 6/6 | 6/6 | 6/6 | 6/6 all |
| **Total** | **72/82** | **76/82** | **76/82** | **82/82, 82/82, 81/82** |

Local, PII flag off: live-eq hermes 76, crm 78, openclaw 75; branch identical; fix (v0.10.9 only) 80 / 82 / 79 (remaining = the two PII cases and the openclaw premium-alias expectation).

Sensitive markers reaching a public-only-free upstream (mock log): live-eq `ssn x4, card x4`; branch same; fix with PII flag off same (log-only by design); **fix with `SOVEREIGN_PII_DETECTORS=1`: none**.

## Real clients (live, verified)

- **Hermes: 15/15** (sequential 10/10, long-context needle 1/1, 4 concurrent 4/4). 22-33 s sequential, 43-56 s concurrent. Mostly `openrouter/nemotron-3-ultra-free` ($0); fake-credential prompt went to groq private-free (sensitive). Cost $0.004652 over 31 rows.
- **OpenClaw: 10/11**, 4-9 s. The JSON case failed: `LLM request rejected: Tool call validation failed ... tool 'JSON'` - a Groq `tool_use_failed` error delivered in-stream after a role chunk, which the gateway passed through. Fixed in v0.10.9 (2/2). Cost $0.017916 over 12 rows.
- **OpenClaw false-sensitive**: every test turn, and 227 of 243 OpenClaw turns in the prior 24 h, were `data_class=sensitive` via `credential_assignment`, which pins OpenClaw to groq/gpt-oss-120b. The v0.10.4 session-key exclusion is live, so the trigger is something else in runtime-injected text (workspace .md, skills and memory files on VM102 were scanned by shape only: no trigger). v0.10.9 adds `sensitive_match_shapes` to route rows (char-class shapes like `a5:a4:a~`, never values) so the trigger can be named once deployed.

## Load ramp

Live gateway (crm key, 2 requests per worker, short prompts, 60 s between levels, outside the Hermes :18-:30 UTC triage window):

| Concurrency | Requests | p50 s | p95 s | Errors | 429 | Lanes |
|---|---|---|---|---|---|---|
| 1 | 2 | 17.34 | 17.34 | 0% | 0 | 1 private-free, 1 paid |
| 5 | 10 | 2.01 | 10.82 | 0% | 0 | 9 private-free, 1 paid |
| 10 | 20 | 2.65 | 6.99 | 0% | 0 | 15 private-free, 5 paid |
| 20 | 40 | 2.86 | 9.46 | 0% | 0 | 27 private-free, 13 paid |
| 40 | 80 | 2.41 | 12.01 | 0% | 0 | 44 private-free, 36 paid |

Route rows for the window: 152 rows, 0 non-200, 0 escalations/fallback attempts recorded, 64 paid (42%), 0 premium, cost **$0.000667**. 8 streamed turns from `openrouter/free-router` were served 200 with `stream_text_log_only:empty_response` (log-only validator). Gateway /health ok and Hermes healthy afterwards (triage cron last run ok 00:22 MT).

Local, mock with 300-1500 ms latency and 3 in-flight per model (crm key):

| Concurrency | live-eq v0.10.3 err | branch v0.10.8 err | fix v0.10.10 err |
|---|---|---|---|
| 1 / 5 / 10 | 0% | 0% | 0% |
| 20 | 0% (p95 1.96 s) | 0% (p95 1.93 s) | 3% (2x429) |
| 40 | 0% (p95 2.15 s) | **35% (25x429), aborted** | **29% (22x429), aborted** |
| 80 | 0% (p95 3.8 s) | not run | not run |

The v0.10.5 depth cap (6 attempts/turn) plus every turn trying the same cost-ordered candidates creates a herd: under a burst against per-model limits, turns run out of attempts and 429 where v0.10.3 kept walking the list. Not fixed here - owner call.

## Provider-down scenarios (local)

| Scenario | v0.10.3 | branch / fix |
|---|---|---|
| public free down | 200 private-free free-router after 15 upstream calls | 200 **paid** llama-8b after 4 |
| all free down | 200 paid after 25 | 200 paid after 4 |
| all but premium down (crm) | 200 **premium** deepseek-v4-pro after 50 (premium not allowed for crm) | fast 429 after 6 |
| all but premium down (openclaw) | 200 premium after 74 | fast 429 after 6 |
| everything down | 429 after 77 | 429 after 6 |
| free 429 storm | 200 paid after 25 | 200 paid after 4 |

Streaming responses carry `X-Sovereign-*` headers of the *first* candidate (headers are sent before failover can happen, `gateway.py` stream path). Route rows are authoritative; headers on streams can name a model that did not serve the turn.

## Bugs found and fixed

v0.10.9 (`fix/v0.10.9-stress-hardening`, 63 tests):
1. Malformed bodies 500'd at ingress: message as string/null, `max_tokens` as string, lone surrogates (also crashed sticky/telemetry hashing and the httpx encode).
2. Malformed shapes (`tools` malformed, content int / list of garbage) took 11-23 s walking the fleet then 429'd.
3. Malformed values silently promoted turns to paid/premium on live: `temperature:"0.2"` was served by premium gpt-5.5 for crm (premium not allowed, $0.00088); negative `max_tokens` produced a negative cost estimate so paid looked cheapest; bad `response_format`, unknown `reasoning_effort`, float `max_tokens`, `tools` not a list all landed on paid.
   Fix: `_normalize_chat_body` - 400 with a clear message for invalid shapes, drop unknown `reasoning_effort`, coerce `stream`, scrub surrogates to U+FFFD.
4. In-stream upstream error before any content (Groq `tool_use_failed` after a role chunk) reached the client. Fix: hold role-only chunks until first content/tool chunk; an error chunk before commit now fails over (`stream_error_before_commit:<id>`).
5. Diagnostic: `sensitive_match_shapes` on route rows.

v0.10.10 (`fix/v0.10.10-pii-detectors`, 26 tests): SSN and payment-card (Luhn + issuer prefix) detectors. **Default off**: logs `pii_log_only:<names>` on the route row; with `SOVEREIGN_PII_DETECTORS=1` the turn becomes sensitive and never reaches public-only-free. Owner policy gate.

Full suite: 1091 passed on v0.10.10 (1065 on v0.10.9).

## Not fixed - needs Jordan's decision

- Deploying v0.10.9 / v0.10.10 (stream-error failover and shape diagnostics can only be proven live).
- Turning on `SOVEREIGN_PII_DETECTORS`.
- Depth-cap herd regression under bursts (tune `SOVEREIGN_MAX_UPSTREAM_ATTEMPTS`, add herd avoidance/jitter, or enforce J-2 demotion).
- Branch goes paid when only the public lane is down (v0.10.3 found the free private-free router).
- Reasoning models return empty answers at small `max_tokens` (live: groq/gpt-oss-120b, `length_no_content` at 64) - e.g. a minimum budget for reasoning models.
- Root cause of the OpenClaw false-sensitive flag (needs the shapes diagnostic live).
- `free-router` empty streamed answers served 200 (validator is log-only on streams).

## Verified vs inferred

Verified: all pass/fail counts, latencies, live load numbers, route-row costs, sensitive-marker lanes in the mock, the test suite counts, the in-stream Groq error text from OpenClaw.
Inferred: that the OpenClaw trigger is runtime-injected text; that the herd effect on the branch will appear live at real provider limits (live load at 40 showed 0 errors because real limits were not hit); that v0.10.9 fixes the OpenClaw JSON failure live (proven only against a mock that reproduces the chunk sequence).

## Spend

Live test traffic: crm matrix $0.002366, live load $0.000667, Hermes $0.004652, OpenClaw $0.017916 = **about $0.026** (route rows). Hard cap was $2.
