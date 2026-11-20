# Deploy plan: v0.9.1 -> v0.9.3 (DRAFT FOR OWNER APPROVAL)

Status: **NOT APPROVED. Nothing in this plan has been executed.** Written 2026-11-20.
Live today: v0.9.1 gateway on VM103 (`192.168.0.166:6075` via nginx), classifier v0.5.0.
Candidate: PR #15 head (`feat/router-hardening-cycle2`). v0.9.2 was never deployed, so this is a two-version jump.

## 1. What changes for traffic

| Change | Live effect |
|---|---|
| F3: selection quality capped by measured tooling score | Tool-call requests stop going to weak tool models. Golden replay: g20, g21, g22 move `openrouter/particle-1-free` -> `openrouter/nemotron-3-ultra-free`. |
| F2: break sticky routing after 2 consecutive tool-error turns | Bounded per-conversation exception (owner ruling J-2 stays OPEN for auto-demotion). |
| F4: per-client `model_allowlist` | **No effect**: default empty = unrestricted. Nothing is written to `clients.json`. |
| ToolHealth (observe-only) | Records tool-loop outcomes; **selection never reads it**. Read via `GET /admin/tool-health`. |
| Validation escalation 1 -> 2 hops | More resilient when the first escalation target also fails. |
| Repeat flag fix in tool loops | Telemetry accuracy only. |

Evidence so far: 819 unit/integration tests, e2e_live 31/31, and a **golden shadow replay on VM103 against a fresh v0.9.1 baseline: 24 items, 0 new verifier violations, exactly 3 routing changes (the tool items above), nothing else moved.** Shadow shows decisions only; it cannot prove tool calls succeed more often. That is what the canary below is for.

## 2. Preconditions (all must be true before Phase 1)

- [ ] Owner approves this plan and names a window (suggest a weekday morning; not during an OpenClaw batch).
- [ ] `twenty-crm` key rotated (exposed 2026-11-20; see action log).
- [ ] Fresh Proxmox snapshot of VM103 taken within 24h (V7 guest-lifecycle, name `pre-v093-<date>`).
- [ ] Backups copied on VM103: `state/clients.json`, `state/registry.json`, `state/quota.db` (sqlite backup API, not cp), nginx.conf, the compose file.
- [ ] Admin key available to the owner for `/admin/tool-health` (the assistant has no raw admin key on the box).

## 3. Phase 1: build the image (no live impact)

Overlay image, no pip/network needed:

```
FROM 192.168.0.28:5000/sovereign-router:v0.9.1
COPY --chmod=644 *.py ./
```

- Source reaches VM103 as the verified patch used in the replay (sha256 of patch + each of the 4 changed files, abort on mismatch; image code must equal `main` first).
- Tag `sovereign-router:v0.9.3`, push to `192.168.0.28:5000`. Do **not** retag or overwrite `v0.9.1`.
- Gate: `docker run --rm` import check and the golden replay again on the built image (expect the same 3 changes only).

## 4. Phase 2: real-traffic canary (low risk, reversible)

- Start `sovereign-router-v093-canary` from the v0.9.3 image on the internal network with a **copy** of state (own `quota.db`; its spend for the canary client is not counted by live during the canary, acceptable for 24-48 h).
- nginx: add a `map $remote_addr $sr_upstream` so only **n8n (192.168.0.28)** goes to the canary; everyone else stays on live v0.9.1. Hermes, OpenClaw and 20CRM are untouched. `nginx -s reload` (no downtime).
- Rollback of this phase: remove the `map`, reload nginx, stop the canary container. Under one minute, no data loss.
- Duration: at least 24 h and at least 50 canary tool-loop turns (ToolHealth `MIN_TURNS=50`).

### Pass criteria (all required)
- No 5xx increase vs live over the same period; p95 latency within +25% of live.
- Canary tool-loop error rate <= live's rate for the same client (from telemetry), and `/admin/tool-health` shows no model over `WARN_RATE=0.25`.
- No sensitive-wall breach: the canary's telemetry has no private-lane request routed to a public provider (existing invariant test run against telemetry).
- Validation escalation rate not above 2x live's.
- Zero unexpected "client_model_allowlist" skips (the list is empty).

### Fail triggers (any one = rollback and write findings)
- Any sensitive-wall violation.
- Canary 5xx rate > 2x live, or sustained health-check failures.
- Tool-loop error rate worse than live by more than 10 points.

## 5. Phase 3: cutover (needs a second explicit "go")

- Compose: change the gateway image line to `v0.9.3`, `docker compose up -d gateway` (about 10 s interruption; clients retry). nginx map removed.
- Gates immediately after: `/health` 200 via nginx path, one authenticated shadow call per client, golden replay on live, container healthy for 15 min, telemetry shows normal traffic.
- Keep the canary running 1 h longer as a comparison, then stop it.

### Rollback (any time)
1. Compose image line back to `v0.9.1`, `docker compose up -d gateway` (about 10 s).
2. If state looks damaged: restore `clients.json` / `registry.json` / `quota.db` backups, restart.
3. Last resort: Proxmox snapshot rollback of VM103 (loses state written since the snapshot).

## 6. After a clean cutover

- Merge PRs #14 -> #15 -> #16 -> #17 into `main` in order (retargeting each), tag `v0.9.3`.
- Record the new live baseline (`baseline-v093-<date>.json`).
- Update CHANGELOG status to deployed; add the deploy to the infra action log.

## 7. Decisions needed from the owner

1. Approve Phase 2 canary on **n8n only**, or choose another client (Hermes is the heaviest tool user, so highest signal but highest risk).
2. Window for Phases 2 and 3.
3. Who reads `/admin/tool-health` (owner has the admin key), and daily check-in cadence.
4. J-2 (auto-demotion) stays observe-only: confirm.

## 8. Known notes

- nginx returns 403 to requests originating from the VM itself on `192.168.0.166:6075` because only the listed client IPs (and 127.0.0.1 for `/health` inside the container) are allowed. This is the allowlist working, not a fault.
- Network note: the Docker `sovereign-v05-internal` network is internal, so containers on it cannot publish host ports; test canaries are reached by container IP from the host.
