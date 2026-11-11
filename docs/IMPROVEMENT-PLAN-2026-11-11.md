# Sovereign Router — Improvement Plan (v2)

**Date:** 2026-11-11 · **Baseline:** `origin/main` @ `ea00e25` (v0.5.7; unchanged on re-check) · **Status:** Proposal — nothing here is approved or built.

**Basis.** Full-repo read (68 files): `src/` (gateway, quota, telemetry, sticky, governor, sensitivity, validation, clients, classifier, registry), `classifier/` sidecar and benchmark, `deploy/` (compose, nginx, Dockerfile, env), `tools/`, `tests/`, the docs and the incident records. The test suite was run: **141 passed** (needs `pytest` and `pytest-asyncio`, neither listed anywhere in the repo). Findings marked **[verified]** were reproduced by running code against the repo; the rest come from reading and have not been profiled.

## Constraints every item must respect (ROUTING-PRINCIPLES.md)

- No spending caps and no budget refusals. Targets steer and alert, never block.
- The $25/day level warns only; paid rungs are never cut off in bulk.
- Free/private first, paid only on exhaustion, in strict cost order.
- Sensitive-data walls and `spillway_sensitive_excluded` are never relaxed.
- Route logs never contain prompt or response text.
- Every change: tests green, snapshot/backup, deploy, verify through the router.

## What the full scan changed

The first version of this plan was about routing quality and speed. The scan found problems that rank above those: **a deploy default that reverses an owner ruling, a credential scan that misses common formats, and request handling that crashes on malformed input.** They are now Phase 0.

## Phase 0 — Correctness and safety (do first; small, each with a test)

| # | Finding | Evidence | Fix |
|---|---|---|---|
| 0.1 | **Repo deploy defaults undo the 2026-11-09 owner ruling.** `deploy/docker-compose.v05.yml` and `deploy/.env.example` both default `SOVEREIGN_EMERGENCY_WARN_ONLY` to `0`, and `GatewayConfig` treats anything but `"1"` as off. Rule 5 in ROUTING-PRINCIPLES says the $25 level must warn, never cut. A redeploy from repo defaults (or a lost `.env`) silently restores the hard switch that 429-walled both fleets. | **[verified]** `compose:SOVEREIGN_EMERGENCY_WARN_ONLY:-0`, `.env.example`, `gateway.py:163` | Default to warn-only in code, compose and `.env.example`; make the hard switch an explicit opt-in. Add a test that the unset-env default is warn-only. |
| 0.2 | **Credential scan misses common formats.** JSON-quoted secrets (`{"password": "…"}`, `{"api_key":"…"}`), JWTs, Stripe-style `sk_live_…` keys and credentials inside assistant `tool_calls` arguments all scan as clean. Agents pass JSON constantly, so a miss routes secrets to a non-cleared provider. | **[verified]** probe against `sensitivity.py`: all five returned no detectors | Allow an optional closing quote before `:`/`=` in the assignment regex; add JWT, Stripe, Twilio, SendGrid, npm and Azure-style detectors; scan `tool_calls[].function.arguments`. Measure false positives in log-only mode first (the mechanism already exists). |
| 0.3 | **Malformed requests raise unhandled exceptions (HTTP 500).** `max_tokens: "abc"` → `ValueError`; `messages: ["hi"]` → `AttributeError`. Also `stream: "false"` is treated as streaming because of `bool(str)`. `max_completion_tokens` is ignored when sizing the request. | **[verified]** via TestClient probe | Validate and coerce the request body once at the top of `chat_completions`; return 400 `invalid_request`. Parse `stream` strictly; read `max_completion_tokens` as a fallback. |
| 0.4 | **No CI, no pinned test dependencies.** No `.github/`, no `pyproject.toml`/`pytest.ini`, `pytest-asyncio` used but undeclared (a fixture-scope warning shows it). `src/requirements.txt` uses open ranges, so image builds are not reproducible. | repo listing | Add `requirements-dev.txt`, a lockfile (or pinned versions) for the image, a CI job that runs the 141 tests and the registry/state byte-sync check. |
| 0.5 | **Config drift is policed by hand.** The state-dir `registry.json` must stay byte-identical to the repo copy; `tools/soak_report.py` hard-codes client targets (`hermes 1.25, openclaw 5.0, n8n 2.5`, no CRM) that duplicate `clients.json`. | `soak_report.py:TARGETS`; principles rule 9 | Read targets from `clients.json`; add a `registry-sync` script/CI check; add the CRM client. |
| 0.6 | **Stale docs in deploy files.** Compose still says the classifier serves "Arch-Router-1.5B" (rejected on license); nginx header says "exactly three LAN clients" but allows four (a tab-indented `192.168.0.92`, labelled `CT1007` vs `CT107` in the docs). | `docker-compose.v05.yml`, `nginx.conf` | Correct comments and the allowlist label. |

## Phase 1 — Honest failures and early warning

| # | Change | Where | Done when |
|---|---|---|---|
| 1.1 | **Split the empty-candidate error:** `no_eligible_model_under_policy` (4xx, no `Retry-After`, lists policy skip reasons) vs `upstream_exhausted` (429/503 + `Retry-After`). Today every empty case returns "rate-limited or failing". | `_json_response`, `_stream_response`, tests | A policy-blocked request no longer says "rate-limited" |
| 1.2 | **`capacity_gap` alert:** when a large payload leaves ≤1 non-premium model able to carry it (context, TPM, TPD, key), raise a deduped alert. On Nov 9 this would have fired when DeepInfra Flash died, hours before spend alerts. | `build_candidates`, governor | Replay of 2026-11-09 data triggers it near 04:48 |
| 1.3 | **Log token-estimate fallback.** `_encode_len` swallows every error and drops to `len//4`. | gateway | Signal in the route row; no behaviour change |
| 1.4 | **Provider-key rotation without restart.** Keys are read at start and on `/admin/reload`, but a dead key (401/402/403) only produces an alert. Surface "key present but rejected" in `/health` and `/admin/costs`. | gateway | Dead provider visible on the status endpoints, not only in the alert stream |

## Phase 2 — Evidence base (Direction A)

| # | Change | Notes |
|---|---|---|
| 2.1 | Turn `nightly_scoring` (a stub) into a real job: per model × task class — share, error/429 rate, `tool_error_next_turn` rate, `repeat_within_60s`, cost per turn and per *successful* turn, latency p50/p95. | Extends `telemetry.py`; reuse `tools/soak_report.py` output |
| 2.2 | Scheduled runner and daily summary through the existing n8n alert chain. Proposals stay owner-approved; the job never edits the registry. | |
| 2.3 | Label seed set from `routes.jsonl` (Nov 8 13:12 vs 13:17 pair first). | Data only |
| 2.4 | **Rotate `routes.jsonl`.** The log is append-only with no rotation and `soak_report.py` reads the whole file with `read_text`. Add daily rotation/retention and a streaming reader. | Prevents the report and `/admin` from slowing as the file grows |

## Phase 3 — Routing quality (Directions B, D, C)

| # | Change | Risk control |
|---|---|---|
| 3.1 **(B)** | Apply scorecard findings: demote proven-bad models per task class via quality values; no pins. | Registry change owner-approved, one model at a time |
| 3.2 **(D)** | Failure-triggered step-up: a tool error or sanitizer hit raises `required_quality` by 1 for that conversation's next turn and drops after a clean turn. Uses the existing `sticky.note_tool_error`. | Behind a flag; cheapest capable model still wins; never forces premium |
| 3.3 **(C)** | Difficulty-score classifier in **shadow only** via `/v1/shadow` for a week; local; falls back to rules. | Go-live needs an owner-set accuracy bar |
| 3.4 | **Strengthen the classifier eval before relying on it.** Benchmark is 80 held-out items; level 3 is only 66.7% exact (2 of 15 under-classified to level 1, 2 to level 2) and the held-out set is small for the claims made. Grow the labeled bank from real traffic and report per-level confusion on every change. | Level 3 is the largest real-world bucket |

## Phase 4 — Cost per turn on large-context fleet traffic

Fleet payloads are ~56k median / 108k max input tokens with no cache hits, so context size, not per-token price, drives spend.

| # | Change | Notes |
|---|---|---|
| 4.1 | Measure first: cached-token share per provider/model from `routes.jsonl`. | Decides whether 4.2/4.3 are worth building |
| 4.2 | Prefer cache-capable models *within* cost order for sticky loops; keep affinity on transient failover. | Sticky key is first-user-message + system prompt, so a system-prompt edit breaks affinity; evaluate a steadier key |
| 4.3 | Optional payload trimming (stale tool output, old turns) as a per-client opt-in. | Needs owner approval: changes what the model sees |

## Phase 5 — Engineering health

| # | Change | Why |
|---|---|---|
| 5.1 | Move blocking work off the event loop: `asyncio.to_thread` (or batching) for `record_usage`; SQLite WAL + `synchronous=NORMAL`; one open telemetry file handle. | A per-request commit and file open run inside `async def` handlers |
| 5.2 | Run `evaluate_alerts` on a 30–60 s tick, not per request (several aggregate queries per client per request). | Same |
| 5.3 | One shared attempt loop for the JSON and streaming paths; move `build_candidates` into `selector.py` as a pure function. `create_app` is ~900 lines of closures in a 1,560-line file. | Failover/escalation logic is duplicated; fixes (e.g. north-mini) land twice |
| 5.4 | Cache the stable-prefix token estimate (system prompt + tools) by content hash. | Full payload re-encoded every request |
| 5.5 | Tidy: drop the redundant `FAILOVER_STATUSES` check, bound `OutcomeTracker`, move long registry `notes` into DECISIONS.md. | Minor |
| 5.6 | Container: pin the base image by digest; add a non-root, read-only-rootfs smoke test in CI. | Hardening is documented in the Dockerfile and compose but not tested |

## Suggested order

**0.1 → 0.3 → 0.2 → 0.4** (safety and a test gate), then 0.5/0.6 → 1.1 → 1.2 → 2.1/2.2/2.4 → 3.2 → 5.1/5.2 → 3.3/3.4 (shadow, bigger eval set) → 4.1 → decide 3.1/4.2/4.3 from data → 5.3.

0.1 should happen before the next redeploy or rebuild of VM103.

## Owner decisions needed

1. **0.1:** confirm the default flips to warn-only everywhere (matches the 2026-11-09 ruling).
2. **0.2:** accept a log-only trial of the expanded credential detectors before they change routing.
3. Sequence: scorecard (A) first, as the Nov 8 doc recommends, or classifier (C) earlier.
4. Difficulty classifier scope: voice-only first or all Hermes; accuracy bar for going live.
5. Whether 3.2 (step-up on failure) ships enabled or flag-off for a soak.
6. Whether payload trimming (4.3) is acceptable at all.
