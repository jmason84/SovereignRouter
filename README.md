# Sovereign Router v0.5.3 (Phase 1 source tree)

One OpenAI-compatible endpoint for OpenClaw, Hermes and n8n that sends each request to the cheapest model able
to do it well. Implements Phase 1 of the "Sovereign Router v0.5.3: Architecture, Specification and Implementation
Plan".

## What's running now

| Version | Status | Notes |
|---------|--------|-------|
| v0.2.1 | **Production** | Untouched, serving all production traffic |
| v0.3.3 | **Canary** | Untouched, canary traffic |
| v0.5.3 | **Not deployed** | Active development branch. 66 tests pass against mock providers. No provider has been called live. |

See [docs/deploy-checklist.md](docs/deploy-checklist.md) for the cutover path and
[docs/risks.md](docs/risks.md) for the open risk register.

## Pipeline (`engine.py`)
authenticate -> normalize (aliases, max_tokens clamp) -> secret scan -> profile -> classify (level 1-5) ->
sticky check -> select candidates (policy, data class, capability, fit, provider limits; cost order) ->
cost governor -> execute with failover (401/403/408/413/429/5xx/timeouts) -> validate output and escalate once ->
route headers + one JSON log line (never prompt text).

## Module map
| Module | Role |
|---|---|
| `registry.py` | Registry v2: lanes, per-task quality, prices, cached price, limits, `extra_body`, `spend_alert_usd` |
| `policy.py` | sha256 client keys, per-client lanes/premium/daily cost target |
| `sensitivity.py` | local secret scan; hex IDs excluded to avoid false positives on infra traffic |
| `classifier.py` | rules layer (infra term alone != complex) + timeout-guarded sidecar hook |
| `quota.py` | SQLite: rate-limit windows, atomic reservations, spend ledger, cooldowns, alert dedupe, kill switch |
| `governor.py` | steering above daily target, provider/client/runaway alerts, daily summary (dead-man heartbeat) |
| `alerts.py` | alert outbox polled by n8n; poll freshness drives `/health` |
| `sticky.py` | hash-keyed conversation -> model, 30 min TTL, sensitivity sticks for the conversation |
| `validate.py`, `upstream.py` | answer checks; streamed content commits at first token, tool-call/JSON streams are buffered to the end so escalation is still possible |
| `gateway.py` | FastAPI app, admin endpoints |

## v0.5.3 behaviour implemented
- **Emergency paid-lane switch** (owner decision, off unless set): `SOVEREIGN_EMERGENCY_PAID_SWITCH_USD` or
  `POST /admin/emergency-switch {"usd": 25}` / `{"usd": null}`. When today's spend crosses it, paid and premium
  lanes are skipped; free models keep serving; requests only paid models can serve get 429/413 with reasons.
- **Runaway alarms:** fixed thresholds for the first 14 days ($0.50 paid/hour, client over 2x target), then 5x the
  trailing 7-day hourly average ($1 minimum); projected-month and 3-day-trend alerts; level-mix alert (>40% level 4-5).
- **Premium alerts:** $20/month and >20 premium requests/day (both configurable). Above a client's target, premium
  needs an explicit `premium` request and levels 1-3 accept one quality tier lower.
- **Alerts are pulled:** stored in an outbox; n8n polls `GET /admin/alerts?after=<id>` / `GET /admin/costs`.
  `/health` reports `degraded` when no poll has arrived for 30 minutes. `POST /admin/daily-summary` creates the
  daily all-clear.
- **Secret scan:** known formats only (no entropy rule); `SOVEREIGN_SECRET_SCAN=enforce|log-only|off`; hit count on
  `/health`. If no provider is cleared for sensitive data, requests go to a registry entry with `no_retention: true`
  on the private-free lane and raise an alert; if there is none, 403.
- **Classifier:** ties between layers go to the higher level for levels 3-5; sidecar timeout 1.2 s.
- **Shadow mode:** `POST /v1/shadow` returns the routing decision and cost estimate with no upstream call and no
  ledger entry.

## Decisions made while implementing (please confirm)
1. Spending is never capped; the only limiter is the owner-set emergency switch above.
2. If no eligible model meets the required quality, the best lower-quality model serves rather than an error.
3. A free quality-4 model that fits the prompt is chosen over any paid model.
4. At level 5 / explicit `premium` the premium lane leads when the client has premium permission.
5. A header may raise the data class, never lower it.
6. Escalated attempts keep their real cost in the ledger.

## Not in this tree yet
Embedding and Arch-Router sidecar (Phase 2; the `sidecar=` hook is ready), nightly learning job (Phase 4),
container registry push, nginx/Portainer stack, Proxmox snapshot. No provider has been called live.

## Run tests
`pip install -e .[test] && pytest`

## Run
State dir `/state` needs `registry.json`, `clients.json` (see `examples/`) and mode-600 key files.
Env: `SOVEREIGN_STATE_DIR`, `SOVEREIGN_EMERGENCY_PAID_SWITCH_USD`, `SOVEREIGN_SECRET_SCAN`.
