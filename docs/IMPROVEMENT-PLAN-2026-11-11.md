# Sovereign Router — Improvement Plan

**Date:** 2026-11-11 · **Baseline:** `origin/main` @ `ea00e25` (v0.5.7) · **Status:** Proposal — nothing here is approved or built.

Basis: code read of `src/gateway.py`, `quota.py`, `telemetry.py`, `sticky.py`, `governor.py`; `docs/ROUTING-PRINCIPLES.md`; the 2026-11-08 routing discussion; the 2026-11-09 incident records. Test suite was not run (pytest not installed in the review container); performance concerns are from reading, not profiling.

## Constraints every item must respect (ROUTING-PRINCIPLES.md)

- No spending caps and no budget refusals. Targets steer and alert, never block.
- The $25/day level warns only; paid rungs are never cut off in bulk.
- Free/private first, paid only on exhaustion, in strict cost order.
- Sensitive-data walls and `spillway_sensitive_excluded` are not relaxed.
- Route logs never contain prompt or response text.
- Every change: tests green, snapshot/backup, deploy, verify through the router.

## Phase 1 — Honest failures and early warning (small, low risk)

| # | Change | Where | Done when |
|---|---|---|---|
| 1.1 | **Split the empty-candidate error.** `no_eligible_model_under_policy` (4xx, no `Retry-After`, lists policy skip reasons) vs `upstream_exhausted` (429/503 + `Retry-After`). Today both return "rate-limited or failing" (`gateway.py` end of `_json_response`, same in stream path). | gateway, tests | A pinned/policy-blocked request no longer says "rate-limited"; test per case |
| 1.2 | **`capacity_gap` alert.** When serving a large payload leaves ≤1 non-premium model able to carry it (context, TPM, TPD, key), raise a deduped alert. Would have fired when DeepInfra Flash died, hours before spend alerts. | `build_candidates`, governor alerts | Replay of 2026-11-09 data triggers it near 04:48 |
| 1.3 | **Log token-estimate fallback.** `_encode_len` silently drops to `len//4` on any error; record a signal when it does. | gateway | Signal appears in route row; no behavior change |

## Phase 2 — Evidence base (Direction A)

| # | Change | Notes |
|---|---|---|
| 2.1 | Turn `nightly_scoring` into a real job: per model × task class — share, error/429 rate, `tool_error_next_turn` rate, `repeat_within_60s`, cost per turn and per *successful* turn, latency p50/p95. | Extends `telemetry.py`; reuse `tools/soak_report.py` output format |
| 2.2 | Scheduled runner + report to the existing n8n alert chain (daily summary, not an alert). | Proposals remain owner-approved; the job never edits the registry |
| 2.3 | Label seed set from `routes.jsonl` (Nov 8 13:12 vs 13:17 pair first) for later classifier eval. | Data only |

## Phase 3 — Routing quality (Directions B, D, C)

| # | Change | Risk control |
|---|---|---|
| 3.1 **(B)** | Apply scorecard findings: demote proven-bad models per task class via quality values; no pins. | Registry change owner-approved, one model at a time |
| 3.2 **(D)** | Failure-triggered step-up: a tool error / sanitizer hit raises `required_quality` by 1 for that conversation's next turn, drops after a clean turn. Uses existing `sticky.note_tool_error`. | Behind a flag; cheapest capable model still wins; never forces premium |
| 3.3 **(C)** | Difficulty-score classifier in **shadow only** via `/v1/shadow` for a week; local, falls back to rules. Go-live needs an owner-set accuracy bar. | Open decisions in the Nov 8 doc §10 (voice-only vs all Hermes; eval bar) |

## Phase 4 — Cost per turn (large-context fleet traffic)

Fleet payloads are ~56k median / 108k max input tokens with no cache hits, so context size, not per-token price, drives spend.

| # | Change | Notes |
|---|---|---|
| 4.1 | Measure first: cached-token share per provider/model from `routes.jsonl`. | Decides whether 4.2/4.3 are worth building |
| 4.2 | Make cache-capable models preferred *within* cost order for sticky loops; keep affinity when only the failover reason is transient. | Sticky key is first-user-message + system prompt; edits to the system prompt break affinity — evaluate a more stable key |
| 4.3 | Optional payload trimming (stale tool output, old turns) as a per-client opt-in. | Needs owner approval; alters what the model sees |

## Phase 5 — Engineering health

| # | Change | Why |
|---|---|---|
| 5.1 | Move blocking work off the event loop: `asyncio.to_thread` (or batching) for `record_usage`; SQLite WAL + `synchronous=NORMAL`; reuse one telemetry file handle. | Per-request commit and file open sit inside `async def` handlers |
| 5.2 | Evaluate `evaluate_alerts` on a 30–60 s tick, not per request (currently several aggregate queries per client per request). | Same |
| 5.3 | Extract one shared attempt loop for JSON and streaming paths; move `build_candidates` to `selector.py` as a pure function. | Failover/escalation logic is duplicated; fixes (e.g. north-mini) must land twice |
| 5.4 | Cache the stable-prefix token estimate (system prompt + tools) by content hash. | Re-encodes full payload every request |
| 5.5 | Tidy: remove redundant `FAILOVER_STATUSES` check, bound `OutcomeTracker`, move long registry `notes` into DECISIONS.md. | Minor |

Do 5.1/5.2 before traffic grows; they are independent of Phases 2–4.

## Suggested order

1.1 → 1.2 → 2.1/2.2 → 3.2 → 5.1/5.2 → 3.3 (shadow) → 4.1 → decide 3.1/4.2/4.3 from data → 5.3.

## Owner decisions needed

1. Sequence (A first, per the Nov 8 recommendation, or C earlier).
2. Difficulty classifier scope: voice-only first or all Hermes; accuracy bar for going live.
3. Whether 3.2 (step-up on failure) ships enabled or flag-off for a soak.
4. Whether payload trimming (4.3) is acceptable at all.
