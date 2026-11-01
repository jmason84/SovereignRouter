# n8n alert poller (v0.5.3 "Alert delivery and dead-man check")

Workflow on CT100, two triggers. Uses the router admin key from an n8n credential (never in node parameters).

1. **Every 15 minutes**
   - Read the cursor from workflow static data (`after`, default 0).
   - `GET http://192.168.0.166:6075/admin/alerts?after={{after}}` with `Authorization: Bearer <admin key>`.
   - For each alert: send `message` to Signal (Signal MCP / signal-cli). If the Signal send fails, send the
     same text by email through the registered mailbox.
   - Save `next_after` as the new cursor only after every alert was delivered.
   - If the HTTP call itself fails twice in a row, send "Sovereign Router unreachable from n8n" to Signal.

2. **Daily at 08:10 America/Denver**
   - `GET /admin/summary` and send a one-line all-clear to Signal: spend, requests, free share, alerts today,
     backstop state, alert chain state. A missing morning message means the chain is broken.

Every poll is recorded by the router; `/health` shows `"status": "degraded"` (and `/health?strict=1` returns 503)
when no poll has arrived for 30 minutes.
