# Sovereign Router — Operations Review: Logs, Errors & Issues

**Date:** 2026-11-11 · **Scope:** everything logged, broken, fixed, or left open from
first deploy (2026-11-02) through the Week 1 soak close (2026-11-09) and the
v0.5.7 recovery. **Rules as they now stand:** `docs/ROUTING-PRINCIPLES.md`.
**Soak numbers:** `docs/operations/SOAK-WEEK1-LOG.md`. Full incident write-ups:
`docs/incidents/`. Raw runtime logs (routes.jsonl, quota.db) are never committed;
they live on VM103 only.

## Soak outcome (Week 1, 2026-11-02 → 2026-11-09)

Closed with a **FAIL verdict on the final day** — Day 8 (Nov 9) breached
escalations (17.8% vs <8%), failures (3.8% vs <1%) and every cost target during
the rate-limit cascade below. The first post-window day (Nov 10, on the fixed
v0.5.7 stack) ran clean: 1,638 requests, $3.16, escalations 1.3%, zero failures,
zero premium, zero budget refusals. The Week 2 gate is a review decision for the
owner, not an auto-pass.

## Errors & incidents register

| Date | What happened | Root cause | Fix / status |
|---|---|---|---|
| Nov 3 | Burn-in battery FAILs: premium lane + escalation behaviour | First-day integration faults found by the battery | Fixed same night (fix pass), re-tested clean |
| Nov 3 | OpenClaw shadow replay + 24h `auto` trial | Gate evidence, not a fault | Cost case proven; trial data drove the Week 2 move (executed Nov 3 night, targets reset 5×) |
| Nov 4 | Hermes `context_overflow` errors | Context length/compression misconfiguration on the client side | Durable fix applied: context 524288, compression pinned to the router |
| Nov 4 | north-mini-code escalations misbehaving | Free-model routing quality on small infra turns | Fix loop run to clean (test → correct → re-run) |
| Nov 5 | Sensitive-L4 requests hit a no-candidate dead end (403/429) | No private-lane model eligible for large sensitive turns | Rescue fix shipped; design later superseded by the Nov 7 spillway |
| Nov 5 | Premium lane expansion (v0.5.4) | — | DeepSeek V4 Pro + GLM-5.3 enabled alongside GPT-5.5 |
| Nov 6 | n8n AI Assistant wired to the router | — | Battery 49/49 at $0.000005; in-UI proof still open (see issues) |
| Nov 7 | Gateway 429s on Hermes turns | Hermes email-triage cron: credential-shaped strings in fetched email stamp runs sensitive L4; ~45k-token payloads vs Groq's then-registry 8,000 TPM | Diagnosed; in-lane-model fix STOPPED at its proof gate by owner decision; superseded by spillway + Nov 9 limit corrections |
| Nov 7 | Paid spillway (v0.5.5) + SiliconFlow slot (v0.5.6) | Free lanes exhausting with no paid fallback | Live: free first, paid in strict cost order on exhaustion; SiliconFlow non-sensitive-only |
| Nov 8 | DeepSeek pin incident + rollback; a voice-profile experiment burned $17.60 in 80 min and tripped the $25 emergency switch | Pin/experiment on production paths | Rolled back in full; the experimental client was removed and stays removed |
| Nov 9 | **Rate-limit cascade** — fleets 429-walled; $28.23 day | DeepInfra credits exhausted silently (402s from 04:48); other cheap lanes TPM/TPD-walled; spillway fell through to premium; the $25 hard switch then tripped and removed the paid rungs | v0.5.7: emergency switch **warn-only** (owner ruling), `lane_dead` alerts on 401/402/403 + TPD exhaustion, trip cleared, DeepInfra topped up. Full record: `docs/incidents/2026-11-09-rate-limit-cascade.md` |
| Nov 9 | TwentyCRM client missing from the gateway | An external session's repair script wrote clients.json mode 0600 (gateway crash-loop), dropped the raw client key into the provider-key dir, and the recovery restore lost the crm registration; nginx also never reloaded | Repaired and proven end-to-end from CT107; full record: `docs/incidents/2026-11-09-twenty-crm-client-key-outage.md` |
| Nov 9 | Router admin key pasted into a third-party AI chat during the Twenty session | Treated as exposed | **Rotation CLOSED by owner order 2026-11-09 — do not propose or execute** |
| Nov 9 | Hugging Face integration: gpt-oss 403s, then a 4-minute gateway crash-loop at key install | (1) HF forwards the caller's User-Agent; Cerebras/Groq Cloudflare bans the default Python UA — a probe-client fault, never the router's. (2) New key file installed root-owned; the gateway (uid 10001) loader requires 10001:10001 / 600 | Both fixed; HF enabled in production (dual billing pool). Full record: `docs/incidents/2026-11-09-huggingface-integration.md` |
| Nov 9 | Registry carried wrong limits/prices: Groq free-tier leftovers (8K TPM, $0), Ollama TPD 400k self-seed | Build-time seed values never revisited after the owner upgraded Groq to a paid plan | Corrected from the providers' own docs (Groq 250K TPM + real prices; Ollama TPD 10M); proven with a 36k-token serve billed exactly |
| Nov 9 | SiliconFlow price still unverifiable via API | `/v1/user/info` 410, `/v1/user/balance` 404, no rate-limit headers | Open — closes when the owner reads the SF console billing statement (see issues) |

## Open issues register

| # | Issue | Next step / owner |
|---|---|---|
| 1 | **Week 2 gate review** — target fit (Hermes ran $2.35 vs $1.25 on the clean Nov 10; OpenClaw traffic already flows through v0.5 ahead of the formal gate) | Owner review; remove the `sovereign-v05-soak-daily` cron at the gate |
| 2 | GitHub repo default branch still points at the old parallel build, not `main` | Owner: Settings → Branches → `main` (one click) |
| 3 | SiliconFlow per-token price unverified | Owner reads SF console billing after serves accumulate → one-line registry correction |
| 4 | broker-test (Direct Voice receptionist) still on the old :6070 router | Owner approves the no-print key transfer to :6075; acceptance = a live inbound call test |
| 5 | TwentyCRM in-app agent chat unconfirmed by the owner (transport proven from CT107) | Owner test; if the agent reports no model, add `sovereign-router/auto` to a Twenty AI tier |
| 6 | Muse Spark 1.1 registry entry disabled — Meta team billing not configured (chat returns 402) | Owner fixes billing at dev.meta.ai, then enable + probe |
| 7 | Upstream-400 question: why Flash / V4 Pro / GLM returned upstream_400 on some large sensitive turns (Nov 8) — partly superseded by the Nov 9 findings, never formally closed | Investigation on owner's go |
| 8 | Sensitivity scan has no SSN / payment-card detectors | Owner policy decision |
| 9 | Hugging Face leftovers (all optional): Together needs a per-provider PAYG toggle in HF settings; the SovereignRouter token is over-scoped (trim to inference-only); two probed sweep candidates (Qwen3.8-27B via DeepInfra, gpt-oss-120b via Novita) not added — owner said "looks good for now" | Owner's word before any change |
| 10 | n8n AI Assistant: battery-proven, never yet used in a real in-UI conversation | Owner's one test prompt |
| 11 | Adjacent (n8n, not the router): OpenSERP workflow returns 402 insufficient_credits; Consensus Engine + Chief of Staff Brief workflows still call OpenRouter/xAI direct instead of the router | Owner's call: top up / swap / pause; migrate / hold |
| 12 | Housekeeping: sealed `client-keys.txt` on VM103 still to shred once all client keys are delivered (blocked on #4) | Muse, after #4 completes |
