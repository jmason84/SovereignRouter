# Sovereign Router v0.5.3 (Phase 1 source tree)

One OpenAI-compatible endpoint for OpenClaw, Hermes and n8n that sends each request to the cheapest model able
to do it well. Implements Phase 1 of the "Sovereign Router v0.5.2: Architecture, Specification and Implementation
Plan" (v0.5.3 revision). **Nothing here is deployed.** Production v0.2.1 and the v0.3.3 canary are untouched.

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
| `sensitivity.py` | known-format secret scan (entropy opt-in); modes route/log/off; hex IDs excluded |
| `classifier.py` | rules layer (infra term alone != complex) + timeout-guarded sidecar hook |
| `quota.py` | SQLite: rate-limit windows, atomic reservations, spend ledger, cooldowns, alert dedupe, kill switch |
| `governor.py` | steering above daily target; provider, premium, client-target, level-mix, runaway (fixed thresholds for 14 days), 3-day trend and projected-month alerts; runtime emergency switch; daily summary |
| `alerts.py` | alert queue in SQLite, polled by n8n (`/admin/alerts`); optional push webhook with retry; poll heartbeat drives `/health` degraded state |
| `sticky.py` | hash-keyed conversation -> model, 30 min TTL, sensitivity sticks for the conversation |
| `validate.py`, `upstream.py` | answer checks; streamed content commits at first token, tool-call/JSON streams are buffered to the end so escalation is still possible |
| `gateway.py` | FastAPI app, admin endpoints |

## v0.5.3 additions
- **Runaway alarm** uses fixed thresholds for the first 14 days ($0.50 paid spend in an hour, or a client at 2x its
  daily target), then 5x the trailing 7-day hourly average with a $1 minimum. Paid spend only.
- **Slow-leak alerts:** projected month above the sum of provider alert levels; daily spend rising 3 days in a row
  and ending above the client's target.
- **Level-mix alert** when more than 40% of a client's requests today (minimum 20) were level 4-5.
- **Premium:** per-client `premium_monthly_alert_usd` and `premium_daily_requests_alert`. Above the daily target,
  premium needs an explicit `premium` request; an explicit request also overrides stickiness.
- **Emergency paid-lane switch** (`SOVEREIGN_GLOBAL_DAILY_BACKSTOP_USD`, or `POST /admin/backstop` with
  `{"usd": 25}`, `{"usd": null}`, `{"clear_today": true}`). Stored in SQLite so it survives restarts. When tripped,
  paid and premium lanes are skipped until 00:00 UTC or until cleared; free models keep serving.
- **Pull alerts:** `GET /admin/alerts?after=<id>` (n8n, every 15 min) and `GET /admin/summary` (daily all-clear).
  `/health` reports `degraded` when no poll arrived for 30 min; `/health?strict=1` returns 503 then.
  See `deploy/n8n-alert-poller.md`.
- **Shadow mode:** `POST /v1/shadow` returns the model and estimated cost v0.5 would choose, with no upstream call
  and no quota use; `GET /admin/shadow` summarizes it for the OpenClaw comparison.
- **Secret scan:** known credential formats only by default (JWTs, provider key prefixes, PEM blocks, assignments);
  `SOVEREIGN_SECRET_SCAN=log` records matches without rerouting (Phase 0 false-positive trial). When nothing is
  cleared for `sensitive`, entries marked `sensitive_fallback: true` serve the request and an alert is raised.
- **Deploy files:** `deploy/docker-compose.yml` (gateway + nginx on 192.168.0.166:6075, admin paths only from n8n)
  and `deploy/nginx.conf`.

## Decisions made while implementing (please confirm)
1. **Spending is never capped** (v0.5.2). Above the daily target, levels 1-3 accept one quality tier lower.
2. **Emergency paid-lane switch** is off unless set (v0.5.3 proposes $25/day; owner decision). See above.
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
Env: `SOVEREIGN_STATE_DIR`, `SOVEREIGN_ALERT_MODE` (pull|push|both, default pull), `SOVEREIGN_ALERT_WEBHOOK`,
`SOVEREIGN_SECRET_SCAN` (route|log|off), `SOVEREIGN_GLOBAL_DAILY_BACKSTOP_USD`.
