# Review: Sovereign Router v0.5.2 plan

Reviewed 2026-11-01 against the Drive document "Sovereign Router v0.5.2: Architecture, Specification and
Implementation Plan" (plus the v0.5 first draft and the v0.3.3 canary action log). Design review only: no router
source code from production or canary was inspected.

**Verdict:** a good plan. It diagnoses the real problem (easy requests over-classified as complex), ships from a
tested source tree instead of more startup patches, and keeps v0.2.1 as rollback with an owner go/no-go before
cutover. The no-cap revision removes the budget-outage problem of the first draft but adds new risks.

## What the revision fixes
- A request can no longer be refused for cost; provider rate limits only move traffic to the next model.
- Cost controls are coherent: daily targets, provider spend alerts, hourly runaway alarm, Signal path, kill switch.

## New risks from "no caps"
1. **The runaway alarm is the only real protection and has holes.** Threshold is 5x the trailing 7-day hourly
   average with a $1 minimum. In week one the average is near zero, so the $1 floor is the only trigger; a slow leak
   below threshold never fires; provider alerts are monthly.
2. **The alert chain can fail silently** (router -> n8n webhook -> Signal). Needs a dead-man heartbeat.
3. **Premium has no ceiling.** The premium alert level is an open decision; premium costs far more per request
   than DeepSeek.
4. **Client targets are a soft steer only.** Levels 4-5 are exempt and levels 1-3 drop one tier, so Hermes can
   overspend freely if the classifier marks most turns level 4.

## Earlier concerns that still stand
- The 60%-free target is probably optimistic for Hermes: Groq cannot fit its prompts (8k tokens/min) and the two
  OpenRouter free models share ~1,000 requests/day.
- The "50% lower than v0.2.1" baseline compares different traffic (OpenClaw vs Hermes).
- Streamed tool-call arguments cannot be validated before the response is committed.
- The secret scan will probably false-positive on infra hashes and IDs, and the plan does not say whether paid
  lanes are cleared for sensitive data. Sensitivity should persist for the rest of a conversation.
- Arch-Router-1.5B is built for route selection, not difficulty rating; its 1-3 s estimate probably exceeds the
  1.5 s timeout on 2 shared CPUs. "85% within one level" on 50 examples is too lenient: measure under-classification
  of levels 4-5.
- Credential scope slipped: production OpenRouter and DeepInfra keys were copied into canary, which also shares
  one OpenRouter free quota with production.
- Groq/Ollama key rotation has been open since Oct 22.
- Phase 0 adds more patches to v0.3.3 right after saying patching has reached its limit.
- The cause of v0.2.1's $3/day budget trips (~696k OpenClaw input tokens) is not addressed.
- Hand-set quality ratings differ between documents (DeepSeek q5 vs "4 to 5"), changing complex-request ordering.

## Status in this repository (Phase 1 source tree)
| Finding | Status |
|---|---|
| Alerts are the only protection | Partly: optional `SOVEREIGN_GLOBAL_DAILY_BACKSTOP_USD` turns paid lanes off for the UTC day (free keeps serving). Off by default; needs your value. |
| Silent alert chain | Partly: failed deliveries are retried and counted on `/health`; `POST /admin/daily-summary` is the heartbeat. Needs a scheduled caller and an external watcher. |
| Streamed tool-call validation | Done: tool-call and JSON streams are buffered to the end so escalation is still possible. |
| Secret-scan false positives | Done for hex digests/UUIDs; sensitivity persists per conversation. Paid-lane clearance is an explicit registry field, none cleared by default. |
| Infra keyword over-classification | Done: an infra term alone stays level 2; it needs a change or diagnosis verb. |
| Premium ceiling | Not done: premium lane has a `spend_alert_usd` field but no value chosen. |
| Classifier sidecar, learning loop, shadow mode, baseline replay | Not started (Phases 2-4). |
| Key rotation, canary credential separation | Operational, outside this repo. |

## Suggested next steps
1. Set a global backstop value and a premium alert level.
2. Schedule the daily summary and add an external dead-man check on it.
3. Run Phase 0 to get the real free/paid and level split before committing to the 60% and 50% targets.
4. Start with rules plus embeddings; benchmark Arch-Router before depending on it.
5. Rotate the carried-over keys and give canary its own provider keys.
