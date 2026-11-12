# Operations review — live setup, logs and changes (2026-11-12)

Read-only review through the Portainer, n8n and GitHub connectors. Nothing was
changed on VM103. Log window: gateway 2026-11-11 20:23Z → 2026-11-12 00:42Z (last
1,000 lines), nginx 20:14Z → 21:10Z (last 1,000 lines), classifier last 300 lines.

## Live state (Portainer endpoint 8, `sv7-router-vm103-edge`)
- `sovereign-router-v05-gateway` — image **v0.5.7**, up 2 days, healthy.
- `sovereign-router-v05-nginx` (1.27-alpine, up 9 days) and
  `sovereign-router-v05-classifier` (v0.5.0, up 9 days) — healthy.
- Alert chain (n8n `nkYJEHhqEQ95Arkb`): runs every 15 min, **all recent executions
  succeeded, zero error executions**.

## Logs
- Gateway: **0 errors / warnings / tracebacks**. 377 chat completions, all 200;
  `/admin/costs` + `/admin/alerts` polled on schedule; 3 `/admin/reload` (the
  registry edits for dots-3 and Cerebras).
- nginx: no upstream errors. 3 `403`s all from 192.168.0.166 (VM103 itself, curl,
  during deploy checks) — the allowlist working as designed.
- **Finding:** 246 of 268 chat POSTs logged `a client request body is buffered to a
  temporary file` — fleet turns exceed nginx's 8k body buffer and spool to the 32 MB
  tmpfs. Fixed in this branch (`client_body_buffer_size 1m`; takes effect on nginx reload).
- **Finding (noise):** clients probe `/api/tags`, `/props`, `/v1/props`, `/version`,
  `/api/show`, `/api/v1/models` and get 404 (≈10 sweeps in 4 h). Harmless.
- Classifier: health checks plus `/classify` 200s; no errors.

## Changes on GitHub since the last review (main, 2026-11-11)
`fd300b1` operations review · `a837faf` dots-3 reasoning disabled (incident) ·
`bb5706e` HF/Cerebras gpt-oss route disabled · `1100338` incident addendum.
- **`main` was red:** `tests/test_registry.py` still asserted the Cerebras entry was
  enabled. Fixed here.
- **Root cause of the dots-3 incident was a gateway gap, not only a model problem:**
  streamed responses committed on the first (role-only) chunk, so an empty or
  reasoning-only stream passed as a blank 200 and sticky routing pinned the retries to
  the same model. Fixed here (v0.5.9 stream empty-failover) — this is the
  "sticky-fitness guard" listed as an open follow-up in the incident record.

## Other observations (not changed)
- Legacy containers still running on VM103: v0.2.1 router on **`0.0.0.0:6070`/`[::]:6070`**
  (3 weeks; needed until broker-test moves — open issue 4), `routellm` on
  **`0.0.0.0:6060`** (6 weeks), v0.3 and v0.4 canaries and `hermes-canary-proxy`
  (10–13 days). Only :6075 sits behind the nginx LAN allowlist; 6070/6060 are bound to
  every interface. Recommend binding to the LAN address or stopping once unused.
- GitHub default branch is `ccr-302db870-066n3h`, not `main` (open issue 2).
- Repo has no tags, so the deployed v0.5.7 cannot be located in git history.
