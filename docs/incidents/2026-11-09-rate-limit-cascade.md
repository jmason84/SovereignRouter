# Incident 2026-11-09 — Fleet "rate limited" (Hermes + OpenClaw)

**Status:** Resolved same day. Fix shipped as gateway **v0.5.7** (warn-only
emergency + dead-lane alerts), owner-approved in chat
(`e89e1886-1d55-457f-9746-3c8eb0a79274`).

## What the users saw

From ~11:00 MST, Hermes and OpenClaw agents began failing turns with
rate-limit errors. Jordan reported it at 12:37 MST with a screenshot of the
fleet preview page showing no active preview (an agent had hit the same
wall mid-task).

## Root-cause chain (all times MST, from VM103 routes.jsonl + quota.db)

1. **04:48:36** — last successful DeepInfra DeepSeek V4 Flash serve.
   From 04:48:46, DeepInfra returned **402 (credits exhausted)** on every
   call. Flash is the cheapest paid rung and the spillway workhorse.
   **No alert existed for this** — it burned silently for ~7 hours.
2. The cascade moved up the ladder as designed, but the next rungs could
   not carry fleet payloads (median premium input 56,535 tokens; p90
   85,904; max 108,485):
   - Ollama gpt-oss-120b — daily token cap (TPD) from **05:11** (579 skips).
   - SiliconFlow GLM-5.3 — 40k TPM ceiling below payload size (570 skips).
   - Groq gpt-oss-120b — TPM wall + upstream 429s.
   - Free OpenRouter/Groq small models — below the L4 quality floor.
3. The spillway therefore served from premium: DeepSeek V4 Pro 210 serves
   ($18.19), Z.ai GLM-5.3 86 ($4.27), GPT-5.5 2 ($0.77). The 11:00 hour
   alone burned $13.01.
4. **12:29:36** — paid spend crossed $25.02 and the **emergency paid-lane
   switch** (owner decision 2026-11-01) switched ALL paid/premium lanes
   off. Hard (L3–L5) turns were left with only the TPM-walled Groq rung:
   zero candidates → gateway 429s → the agents' "rate limited" errors.
   32 gateway 429s on the day (29 in the 12:00 hour).

Day totals at diagnosis: 1,263 requests, $25.06 (OpenClaw $15.19,
Hermes $9.75, n8n $0.13, crm $0.00).

## Owner ruling (2026-11-09 12:48–12:54 MST)

Jordan restated the router's purpose: start free → best cheapest models →
on a bounce, move to the next set in succession (Groq, OpenRouter,
OpenAI…) — **warn, but never shut everything down**; escalating to a
better model for capability reasons stays (the level/quality system).
He topped up DeepInfra himself and approved:

1. Clear today's emergency trip immediately.
2. **The $25/day point becomes warn-only** — big alert, cascade keeps
   walking in cost order. This replaces the 2026-11-01 hard-switch
   decision.
3. **Dead-lane warning** — the moment a lane dies (402 / daily cap),
   Signal alert via the existing chain.

## Fix — gateway v0.5.7 (deployed 2026-11-09 ~13:02 MST)

- `SOVEREIGN_EMERGENCY_WARN_ONLY=1`: `Governor.paid_lanes_off()` records
  the emergency alert (dedup key `emergency_switch_warn`, message states
  lanes REMAIN ON) and never vetoes. The hard mechanism remains in code,
  available if the owner ever re-enables it.
- `lane_dead` alerts: first 401/402/403 upstream failure, or TPD
  exhaustion, per model per day (store-deduped), recorded at the
  failover points and the provider-limits skip. Delivered by the
  existing 15-minute alert chain — no new delivery machinery.
- Tests: 141 passed (new `tests/test_warnonly_lanedead.py`: 402 →
  lane_dead + cascade serves next rung + no duplicate alert; 429 is NOT
  a dead lane; warn-only gateway keeps serving paid past the level;
  hard switch still vetoes when warn-only is off; plus a governor-level
  warn-only test in `tests/test_governor.py`).
- Deploy safety: Proxmox snapshot `pre-v057-warnonly-20261109`; VM103
  backups `build/src-v0.5.6-backup-20261109`,
  `deploy/docker-compose.override.yml.bak-20261109-v057`,
  `deploy/.env.bak-20261109-v057`. Rollback = override image back to
  `sovereign-router:v0.5.6` + `compose up -d gateway` (and optionally
  remove the `.env` warn-only line).

## Verification

- Emergency cleared via `POST /admin/killswitch {"clear_emergency":
  true}` (temp n8n workflow using the stored admin credential; workflow
  deleted after use — the key was used, not rotated, per the owner's
  standing do-not-rotate order).
- DeepInfra post-top-up: pinned test as client `crm` → HTTP 200
  "FLASH-OK" (pre-deploy), "V057-OK" (post-deploy).
- `/health` post-deploy: `version 0.5.7`, emergency
  `{enabled: true, daily_limit_usd: 25.0, warn_only: true,
  tripped: false}` — with the day's paid spend already > $25, i.e. the
  configuration that hard-tripped at 12:29 now serves.
- The lane_dead mechanism fired live within a minute of deploy: an
  organic turn at 13:03:18 hit a single transient DeepInfra 402
  (alert #53) and Ollama TPD exhaustion (alert #52), and was still
  served (200, Groq). Flash verified serving immediately before and
  after; the 402 did not recur. Note: because today's trip had been
  manually cleared, the warn-only alert itself first fires on the next
  day the level is crossed (its code path is covered by tests).

## Lessons

- A credits-exhausted provider is indistinguishable from health in the
  old telemetry unless someone reads skip reasons — alert on
  account-class failures (401/402/403) and daily caps at first sight.
- The emergency switch was designed (2026-11-01) as a brake against a
  runaway loop; in a fleet whose normal hard turns have exactly one
  free rung, it converts a cost problem into an availability outage.
  Warn-only preserves the signal without the outage — matching the
  owner's standing no-caps rule for targets.
- Payload size is the fleet's binding constraint (median 56k+ tokens on
  escalated turns): TPM/TPD ceilings, not headline prices, decide
  which rungs are real.
