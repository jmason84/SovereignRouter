# Sovereign Router — Deployment Checklist (v0.5.3)

Track every step from "code on disk" to "production cutover." Each item needs an owner and a verification method.

## Phase 0 — Prerequisites (before canary)

| # | Item | Owner | Verification |
|---|------|-------|-------------|
| 0.1 | Set `SOVEREIGN_GLOBAL_DAILY_BACKSTOP_USD` to a concrete value | Jordan | Config file shows the value; alert fires if unset |
| 0.2 | Set premium lane `spend_alert_usd` and premium alert level | Jordan | Registry JSON has both fields populated |
| 0.3 | Rotate canary provider keys — canary must not share keys with production | Jordan / Muse | Canary uses distinct credential IDs; production keys retired from canary config |
| 0.4 | Schedule `POST /admin/daily-summary` cron job (daily, UTC) | Jordan / automated | Cron exists; last run < 25h ago |
| 0.5 | Deploy external dead-man watcher on the daily-summary heartbeat | Jordan / automated | Watcher runs; test by temporarily disabling summary and verifying alert |
| 0.6 | Confirm `SOVEREIGN_SECRET_SCAN` mode (enforce / log-only / off) | Jordan | Config matches policy decision |
| 0.7 | Verify registry has at least one `no_retention: true` entry on the private-free lane | Jordan | Registry JSON shows the entry |

## Phase 1 — Canary (VM103)

| # | Item | Owner | Verification |
|---|------|-------|-------------|
| 1.1 | Deploy v0.5.3 to VM103 canary | Jordan / automated | Container running; `/health` returns `ok` |
| 1.2 | Point Hermes and OpenClaw canary traffic at the new endpoint | Jordan | Traffic split configured; 5-10% of requests route to v0.5.3 |
| 1.3 | Run 7-day soak with real traffic | Jordan | Soak period complete; no unexplained cost spikes or errors |
| 1.4 | Benchmark Phase 2 classifier sidecar (Arch-Router-1.5B or rules+embeddings) | Jordan / automated | Benchmark results documented; accuracy within target vs. rules-only baseline |
| 1.5 | Verify live provider failure handling (429, 500, timeout) | Jordan / automated | Chaos test passes; failover works; costs tracked correctly |
| 1.6 | Verify no prompt text in ledger/log lines | Jordan / automated | Privacy test passes on real traffic sample |
| 1.7 | Verify alert chain end-to-end (router → n8n → Signal) | Jordan / automated | Synthetic alert delivered to Signal within 5 minutes |

## Phase 2 — Cutover

| # | Item | Owner | Verification |
|---|------|-------|-------------|
| 2.1 | Owner go/no-go review | Jordan | Decision documented; all Phase 1 items green |
| 2.2 | Migrate production traffic to v0.5.3 | Jordan / automated | 100% traffic on v0.5.3; v0.2.1 idle |
| 2.3 | Retire v0.2.1 | Jordan | Container stopped; snapshot kept for 30 days |
| 2.4 | Post-cutover review (7 days) | Jordan | Cost, latency, error rate within targets; no regressions |

## Rollback

If any Phase 2 item fails, revert to v0.2.1 by:
1. Pointing the load balancer back to the v0.2.1 endpoint
2. Confirming traffic is flowing
3. Filing a post-incident review

v0.2.1 is untouched and ready as rollback at all times.