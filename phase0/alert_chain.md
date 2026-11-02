# Alert Chain — n8n Build Spec (Phase 0)

Spec basis: v0.5.3 "Alert delivery and dead-man check" + Success measures
("Daily all-clear received every day; no missed poll longer than 30 minutes").
Design principle: **pull, not push** — n8n polls the router; the router never
depends on n8n to keep serving. A missing daily all-clear *is* the alarm for
a broken chain.

This document is a build spec only — the workflow is not created by Phase 0
tooling. Build it in n8n on CT100 from the node outline in §3.

---

## 1. Behaviour contract

| Element | Contract |
|---|---|
| Poll | n8n calls `GET /admin/costs` (admin key) **every 15 minutes** |
| Alert delivery | Any *new* alerts in the response go to **Signal** to Jordan |
| Daily all-clear | **08:10 America/Denver**, every day: Signal message with the cost summary (spend by client, provider and level, free share, alerts raised in the last 24 h) — sent even when there were zero alerts, so silence means "chain broken", never "nothing happened" |
| Signal failure fallback | If a Signal send fails (non-2xx / node error), the *same* message goes by **email** via the registered mailbox connector, subject prefixed `[Sovereign Router alert chain — Signal failed]` |
| Router side | Router records last-poll time; `GET /health` reports alert chain **degraded after 30 minutes with no poll** (two missed polls), visible to Portainer health checks and surfaced on the next successful poll |
| Dedup | An alert is delivered once: the workflow tracks the highest alert id/timestamp already sent (workflow static data or a Data Table row) and only sends alerts newer than that watermark |
| Recovery notice | When polls resume after a degraded gap, the next poll's delivery includes one line: `Alert chain recovered — gap of N minutes (router flagged degraded in /health)` |

Alert payloads come from `/admin/costs` — spend/runaway/level-mix/premium
alerts defined in the spec's Cost governor section (hourly runaway alarm
with fixed first-14-days thresholds, projected-month, 3-day trend,
level 4+5 > 40% of a client's day, per-provider monthly spend alerts,
premium $20/month + >20 premium requests/day once set). This workflow
*delivers* alerts; it does not compute them.

---

## 2. Message formats

Alert (Signal, one message per poll batch):

```
⚠️ Sovereign Router — 2 new alerts
• [runaway] OpenClaw paid spend $0.62 in the last hour (fixed threshold $0.50, first-14-days rule)
• [level-mix] Hermes L4+L5 = 47% of today's requests (alert at 40%)
Since: 2026-11-02 14:15 MT · /admin/costs poll OK
```

Daily all-clear (Signal, 08:10 MT):

```
✅ Sovereign Router — daily all-clear (2026-11-02)
Spend yesterday: OpenClaw $2.41 · Hermes $0.03 · n8n $0.00
By provider: DeepInfra $1.87 · Ollama $0.12 · premium lane $0.45
Free share: OpenClaw 41% · Hermes 96%
Levels (all clients): L1 18% · L2 34% · L3 27% · L4 16% · L5 5%
Alerts raised in 24 h: 1 (runaway, 14:15) — all delivered
Alert chain: polls OK, worst gap 15 min
```

Email fallback body = the same text verbatim; subject =
`[Sovereign Router alert chain — Signal failed] <first alert headline or "daily all-clear">`.

---

## 3. n8n workflow outline — "Sovereign Router — Alert Chain (Phase 0)"

Build as ONE workflow with two trigger branches sharing the delivery tail.

### Branch A — poll & alert (every 15 min)

1. **Schedule Trigger** — cron `*/15 * * * *`, timezone America/Denver.
2. **HTTP Request — GET /admin/costs**
   - URL: router admin base (VM103 LAN `:6075` once v0.5 is up; v0.3.3 canary
     endpoint during Phase 0 — store as a workflow variable, never hardcode
     in two places)
   - Auth: Header Auth credential, admin key (n8n credential store only)
   - Timeout 10 s; on error → continue to step 7 (chain-gap handling)
3. **Code / Set — extract & watermark**
   - Read `alerts[]` from the response; compare each alert's id/ts against
     the stored watermark (workflow static data key `last_alert_ts`)
   - Output: `new_alerts[]`, and update the watermark **only after**
     successful delivery (step 5) — a failed send must retry next poll
4. **IF — new alerts?** (`new_alerts.length > 0`)
   - False → end (the poll itself is the heartbeat; the router saw it)
   - True → format the batch message (§2) → step 5
5. **Signal — send message** (Signal node / HTTP to the Signal bridge in use
   on CT100 — match the existing Launch Collective Signal setup)
   - On success → write watermark (step 3's pending value) → end
   - On failure → step 6
6. **Email — fallback send** (registered mailbox connector)
   - Same text, subject per §2 → write watermark → end
   - If email ALSO fails: leave the watermark untouched (alerts re-deliver
     next poll), and set static-data flag `chain_broken = true` so the
     daily all-clear branch reports it
7. **Chain-gap handling** (HTTP step 2 error path)
   - Increment static-data `consecutive_poll_failures`; at **2 consecutive
     failures (30 min)** send one Signal message (email fallback as above):
     `🔴 Sovereign Router — alert chain cannot reach /admin/costs (2 polls failed)`.
     Do not repeat until a poll succeeds again (then send the recovery line
     from §1 with the next delivery).

### Branch B — daily all-clear (08:10 MT)

1. **Schedule Trigger** — cron `10 8 * * *`, timezone America/Denver.
2. **HTTP Request — GET /admin/costs?window=24h** (same credential as A2;
   if the endpoint has no window parameter in the Phase 0 build, use the
   plain response's daily summary fields — confirm at build time).
3. **Code / Set — format all-clear** per §2 from the response (spend by
   client/provider/level, free share, alerts last 24 h) plus chain stats
   from static data (worst poll gap, `chain_broken` flag).
4. **Signal — send** → on failure → **Email fallback** (same as A6).
5. Clear `chain_broken` only after a successful send on either channel.

### Workflow settings

- **Error workflow:** point at the existing Launch Collective error handler
  if one is configured; the in-flow fallbacks above are the primary path.
- **Timezone:** set workflow timezone to America/Denver (both triggers
  depend on it — 08:10 MT is the human contract).
- **Static data keys:** `last_alert_ts`, `consecutive_poll_failures`,
  `chain_broken`, `worst_gap_minutes`.
- **Credentials:** admin key and Signal/mail credentials live in the n8n
  credential store only — nothing in node parameters, nothing in exports.

---

## 4. Acceptance (spec Phase 0)

- [ ] Workflow active on CT100; polls visible in router logs every ~15 min
- [ ] Forced test alert delivered via Signal; forced Signal failure
      re-delivered via email with the prefixed subject
- [ ] Router `/health` shows the chain degraded after a deliberate
      30-minute poll pause, and recovers on resume with the recovery line
- [ ] **3 consecutive daily all-clear messages received** (08:10 MT) —
      Phase 0 acceptance gate
- [ ] No alert delivered twice across a workflow restart (watermark check)
