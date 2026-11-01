# Sovereign Router v0.5.2 (Phase 1 source tree)

One OpenAI-compatible endpoint for OpenClaw, Hermes and n8n that sends each request to the cheapest model able
to do it well. Implements Phase 1 of the "Sovereign Router v0.5.2: Architecture, Specification and Implementation
Plan". **Nothing here is deployed.** Production v0.2.1 and the v0.3.3 canary are untouched.

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
| `alerts.py` | n8n webhook; failed delivery is retried, not marked sent, and counted on `/health` |
| `sticky.py` | hash-keyed conversation -> model, 30 min TTL, sensitivity sticks for the conversation |
| `validate.py`, `upstream.py` | answer checks; streamed content commits at first token, tool-call/JSON streams are buffered to the end so escalation is still possible |
| `gateway.py` | FastAPI app, admin endpoints |

## Decisions made while implementing (please confirm)
1. **Spending is never capped** (v0.5.2). Above the daily target, levels 1-3 accept one quality tier lower.
2. **Optional hard backstop** `SOVEREIGN_GLOBAL_DAILY_BACKSTOP_USD` (unset by default): when reached, paid lanes
   are skipped until 00:00 UTC; free models keep serving. This addresses the "alerts are the only protection" gap.
3. **Last resort over refusal:** if no eligible model meets the required quality, the best lower-quality model
   serves rather than returning an error.
4. **Free quality-4 beats paid:** a Groq quality-4 model that fits the prompt is chosen over any paid model.
5. **Premium leads at level 5 / explicit `premium`** when the client has premium permission, even if a cheaper
   quality-5 model exists.
6. **Data class:** each registry entry declares `max_data_class`; nothing is cleared for `sensitive` unless you say
   so. A header may raise sensitivity, never lower it.
7. Escalated attempts keep their real cost in the ledger.

## Not in this tree yet
Embedding and Arch-Router sidecar (Phase 2; the `sidecar=` hook is ready), nightly learning job (Phase 4),
container registry push, nginx/Portainer stack, Proxmox snapshot. No provider has been called live.

## Run tests
`pip install -e .[test] && pytest`

## Run
State dir `/state` needs `registry.json`, `clients.json` (see `examples/`) and mode-600 key files.
Env: `SOVEREIGN_STATE_DIR`, `SOVEREIGN_ALERT_WEBHOOK`, `SOVEREIGN_GLOBAL_DAILY_BACKSTOP_USD`.
