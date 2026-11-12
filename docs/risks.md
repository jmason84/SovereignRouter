> **Status (2026-11-11): historical planning record.** Authored by the
> Launch Exec Ops Hermes agent on 2026-11-11 against the v0.5.3 source
> tree (branch `ccr-302db870-066n3h`, commit `402bae2`). Several entries
> predate decisions that have since landed — e.g. the emergency level
> is warn-only (2026-11-09 ruling, never a hard cutoff) and the alert
> chain is built and live. Kept verbatim as the planning record;
> current routing truth lives in `docs/ROUTING-PRINCIPLES.md`.

# Sovereign Router â Risk Register (v0.5.3)

## Critical

### R1: Unlimited spend if emergency switch fails
- **Likelihood:** Low
- **Impact:** Critical
- **Mitigation:** Monthly audit of `SOVEREIGN_EMERGENCY_PAID_SWITCH_USD` config. Alert if unset for >7 days. Document the switch value in `docs/deploy-checklist.md` Phase 0.1.

### R2: Alert chain fails silently (router â n8n webhook â Signal)
- **Likelihood:** Medium
- **Impact:** High
- **Mitigation:** Dead-man heartbeat via `POST /admin/daily-summary`. External watcher on VM112 checks heartbeat independently of n8n. Failed deliveries retried and counted on `/health`.

## High

### R3: Classifier marks everything level 4
- **Likelihood:** Medium
- **Impact:** High
- **Mitigation:** Level-mix alert already exists (>40% level 4-5). Add weekly review of level distribution. If Hermes consistently gets level 4, revisit classifier rules.

### R4: Premium lane has no ceiling
- **Likelihood:** Medium
- **Impact:** High
- **Mitigation:** Premium `spend_alert_usd` must be set before cutover (Phase 0.2). Premium requests require explicit `premium` flag. Levels 1-3 accept one quality tier lower above target.

### R5: Canary shares provider keys with production
- **Likelihood:** High (current state)
- **Impact:** High
- **Mitigation:** Rotate canary keys before Phase 1. Canary must use distinct credential IDs. Production keys retired from canary config (Phase 0.3).

## Medium

### R6: Secret scan false positives on infra hashes/IDs
- **Likelihood:** Medium
- **Impact:** Medium
- **Mitigation:** Hex digests and UUIDs excluded. Known-format scanning only (no entropy rule). `SOVEREIGN_SECRET_SCAN=log-only` available for tuning. Sensitivity persists per conversation.

### R7: Free/paid target (60% free) is optimistic for Hermes
- **Likelihood:** High
- **Impact:** Medium
- **Mitigation:** Groq cannot fit Hermes prompts (8k tokens/min). Two OpenRouter free models share ~1,000 requests/day. Run Phase 0 to get real free/paid split before committing to targets.

### R8: v0.2.1 budget trips (~696k OpenClaw input tokens, $3/day)
- **Likelihood:** Ongoing
- **Impact:** Medium
- **Mitigation:** Root cause still unknown. P1 investigation before cutover. Document findings in this register.

## Low

### R9: Streamed tool-call arguments cannot be validated before commit
- **Likelihood:** Low (mitigated)
- **Impact:** Low
- **Mitigation:** Tool-call and JSON streams buffered to end so escalation is still possible. Done in v0.5.3.

### R10: Arch-Router-1.5B sidecar exceeds timeout
- **Likelihood:** Medium
- **Impact:** Low
- **Mitigation:** 1-3s estimate on 2 shared CPUs vs. 1.5s timeout. Benchmark before depending on it. Fall back to rules-only if needed.
