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

---

## Round 2 - burst spreading and PII on by default (v0.10.11, 2026-11-28 01:40-02:30 MT)

Owner go (1:40 AM MT): "Go: fix the burst failures by spreading requests across models, then retest up to 80" and "Turn the SSN and card detectors on by default". Branch `fix/v0.10.11-burst-spread`, stacked on #30. Nothing live was deployed, restarted or reconfigured. No live load was run this round; the only live action was a read-only count of 24 h of route logs.

### What changed

- **Burst spreading** (`src/spread.py`). Per-process load hints:
  - in-flight count per model;
  - a learned concurrency ceiling, set from a 429/503 that arrives while other calls are open on the model;
  - a 3 s cooldown for a 429/503 with nothing else open, i.e. a plain rate limit.

  Candidates are reordered only inside a contiguous run of one tier (free / paid / premium):
  - saturated entries move behind free ones, and cooled entries go to the back of the run;
  - contended equal-rank bands are spread by in-flight count, with a seedable RNG breaking ties;
  - at attempt time, a candidate that has become saturated or cooled swaps with a later sibling in the same tier, without spending attempt budget.

  Tier order, the sensitivity wall, no-premium-on-congestion, pins and sticky reuse are unchanged, and with no load the order is the old one. The "preferred coder" order lives only in the v0.10.6 tier *shadow* (`tiering.py`), which never reorders serving, so it is untouched.
- **Depth cap.** Attempts were already distinct models: the spillway excludes attempted ids, and the only repeat is the length retry. The cap stays at 6. The attempt-time swap is what stops a turn from spending budget on models other turns have just saturated.
- **PII detectors ON by default.** `SOVEREIGN_PII_DETECTORS=0` turns them off. The false-positive guards are listed in the CHANGELOG.

### Load: same mock (300-1500 ms latency, 3 in-flight per model), run to 80

Errors are failed turns as a share of all turns. Served lanes come from each stack's own route log, not response headers. Premium serves were 0 everywhere.

crm key (no public lane; its free pool is RPM-limited, so paid carries bursts):

| Concurrency | v0.10.3 (live code) | #30 branch (v0.10.10) | v0.10.11 (final code) | v0.10.11 replicates |
|---|---|---|---|---|
| 1 | 0% p95 0.92 s | 0% p95 1.55 s | 0% p95 1.65 s | 0%, 0%, 0% |
| 5 | 0% p95 1.49 | 0% p95 1.61 | 0% p95 1.39 | 0%, 0%, 0% |
| 10 | 0% p95 1.44 | 0% p95 1.62 | 0% p95 1.53 | 0%, 0%, 0% |
| 20 | 0% p95 1.70 | 1.7% p95 1.96 | 0% p95 2.48 | 0%, 0%, 0% |
| 40 | 0% p95 2.37 | **33.3%** p95 1.76 | 0% p95 2.02 | 0%, 0%, 0% |
| 60 | 0% p95 2.90 | **37.2%** p95 2.61 | 0.6% p95 2.47 | 1.1%, 0.6%, 1.1% |
| 80 | 0% p95 3.79 | **45.4%** p95 2.93 | **0.0%** p95 2.64 | 0.8%, 0.0%, 0.4% |
| Failed upstream calls per turn at 80 | 5.11 | 3.98 | 0.89 | 1.25, 1.16, - |

hermes key (public lane allowed):

| Concurrency | v0.10.3 | #30 branch | v0.10.11 run A | v0.10.11 run B (final code) |
|---|---|---|---|---|
| 20 | 0%; 56 free / 4 paid | 0%; 41 free / 19 paid | 0%; 60 free | 0%; 60 free |
| 40 | 0%; 109 / 11 | **25.0%** | 0%; 101 / 19 | 0%; 108 / 12 |
| 60 | 0%; 140 / 40 | **35.0%** | 0%; 120 / 60 | 0.6%; 113 / 66 |
| 80 | 0%; 159 / 81, p95 3.43 s | **37.9%** | 0%; 167 / 73, p95 2.53 s | **1.3%**; 141 / 96, p95 2.93 s |

**Target:** under 2% errors at 80 with no premium for non-premium keys. Met in all 6 runs at 80 (worst 1.3%). Paid share at 80 on the hermes key is 30-40%, against 34% on v0.10.3. Latency p95 is equal to or below v0.10.3.

The remaining few 429s are turns that hit six 429s before a ceiling was learned: the first seconds of a burst, or a ceiling growing back by one. v0.10.3 never 429s here because it has no depth cap, but it pays for that with about 5 failed upstream calls per turn.

### Matrix (82 cases, local, PII default)

| Stack | hermes | crm | openclaw |
|---|---|---|---|
| v0.10.3 | 76 | 78 | 75 |
| #30 branch (PII flag off) | 80 | 82 | 79 |
| v0.10.11 (PII on by default) | **82** | **82** | **81** |

The single OpenClaw "failure" is a test-expectation artifact: the premium alias is allowed for OpenClaw. Sensitive markers reaching a public-only-free upstream in the mock log: v0.10.3 `ssn 4, card 4`; #30 `ssn 4, card 4`; v0.10.11 **none**.

### PII impact estimate (live 24 h route log, counts only; read 2026-11-28 ~01:58 MT)

Route rows carry no prompt text, so the log cannot say how many turns contained an SSN or card. What it can bound is the exposure: turns that would change route if they did.

| Client | Turns | Non-sensitive turns served on public-only-free | Sensitive turns today (served private-free / paid) |
|---|---|---|---|
| n8n | 1459 | 1204 | 0 |
| hermes | 325 | 185 | 26 (13 / 13), $0.439 total |
| openclaw | 192 | 11 | 178 (164 / 14), $0.987 total |
| crm | 214 | 0 (no public lane) | 13 (13 / 0) |

- **What can move:** about 1,400 public-lane turns a day (n8n 86%, hermes 13%). A turn moves only if it carries an SSN-format or Luhn-valid card number, which is rare in automation traffic. OpenClaw is already ~93% sensitive, and crm has no public lane, so neither changes.
- **Inferred cost:** today's sensitive turns averaged about $0.017 (hermes) when they spilled to paid.
  - 1% of public turns matching: about 14 turns a day, roughly +$0.10 to +$0.25 a day.
  - 5% matching: roughly +$0.50 to +$1.20 a day, plus extra load on the private-free pool.
- **How to measure it:** run a day with `SOVEREIGN_PII_DETECTORS=0`. v0.10.10's `pii_log_only` signal then gives the exact rate.

### Remaining risks

- The load hints are per process. With more than one uvicorn worker each worker learns separately; that is still safe, just slower to learn. The live container runs one worker.
- The mock limits concurrency, not RPM, so real provider limits (RPM/TPM, provider-wide 429s) will learn differently. An RPM limit shows up as cooldowns rather than ceilings. Not proven live.
- A learned ceiling can sit one below a provider's true limit for up to 120 s after a 429. That is a little paid spill that could have been free.
- PII-on can move up to ~1,400 public turns a day onto private-free, then paid. The rate is unknown until it is measured.
- The PII detectors still flag Luhn-valid, Visa-prefixed 16-digit order numbers.
