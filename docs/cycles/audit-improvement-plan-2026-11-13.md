# Sovereign Router — Broader Audit & Improvement/Implementation Plan

**Date:** 2026-11-13 (~17:00 MST) · **Ordered by:** Jordan Mason
**Trigger:** F-11 — Launch Exec Ops (Hermes executive-ops) session killed by
three consecutive mid-stream aborts on zai/glm-4.5-flash, 16:26–16:33 MST.
**Scope of this plan:** router logs + Hermes executive-ops logs forensics,
the full open-items register (failure log, NEXT plan, decisions ledger,
research compilation v1–v3), and the unparked Prometheus/Grafana item.

---

## 1. The incident (F-11) — what the logs prove

| Time (MST) | Event |
|---|---|
| 15:59 | Exec-ops session 20261113_225914_0c6b568c running (~71.5k tok, 118 msgs) |
| 16:26:49 | Router 502 #1 — glm-4.5-flash, public-only-free, upstream 121,290 ms, `stream_aborted_after_commit` |
| 16:28:53 | Router 502 #2 — same entry, 121,229 ms |
| 16:31:00 | Router 502 #3 — same entry, 151,710 ms; Hermes exhausts 3/3 retries, turn dies |

Mechanics: the upstream stream stalled; the gateway's flat 120 s read
timeout (`httpx.Timeout(120.0)`) let each stall burn ~2 minutes; text
streams commit at the first content chunk (ADD-06), so no in-request
failover was possible; Hermes' retries were new requests and selection
**re-picked the same entry three times** — the v0.8.0 attempt ledger
records failures but selection never reads them (the J-2 gap, in the wild).

Router health otherwise that day: 2,314 turns, only 4 non-200 (the 3
above + 1 OpenClaw sensitive/premium-pin 429 at 00:04).

**Differential probes (route-health protocol, 10 probes, VM103):**
no stall reproduced; gateway shaping exonerated (gateway-shaped ≡
canonical-direct outcomes); the z.ai free tier is slow and
high-variance (time-to-first-chunk 0.5–33.1 s on glm-4.5-flash, 60.7 s
on the glm-4.7-flash sibling; one through-gateway long turn finished
at 108.0 s — ~12 s inside the flat timeout). Classification:
**provider × ours-config aggravation**. Registry action: **none** — the
entry served correctly in 9/10 probes; no score change met the
evidence standard (disables remain proposal-only regardless).

## 2. Audit — status of everything on the register

**Already done (verified live, do not re-litigate):** free-first
spillway (v0.5.5); warn-only emergency switch; J-1 pin hardening +
registry integrity + price provenance (v0.7.0); pool model + attempt
ledger, observe-only (v0.8.0); F-09 tool-output budget (v0.8.1);
alert chain (Signal-primary, 15-min poll); routing spec recorded
verbatim (`docs/ROUTING-PRINCIPLES.md`); phantom-dollars ruling
(ledger = ceiling, cache-pricing fix OFF the table); Cloudflare 429
diagnosed (err 4006, account neuron pool; `reset_at` is observed
data, never documentation).

**Built, awaiting publication:** R4 / v0.9.0 quality+tooling
dimension (sr-build4, 654 tests, branch local-only — GitHub push
blocked on Jordan's egress-approval tap since 2026-11-13 ~05:55).
Deploy note: v0.9.0 rollback must restore registry + image together.

**Fixed in this pass (built, §3):** F-11 stream resilience (v0.8.2);
observability (Prometheus/Grafana, live §4).

**Open failure-log items:** F-10 (classifier sidecar fusion can
override the rules layer's hard >60k long-context signal; fix
designed, scheduling is Jordan's — standalone vs carried into R3);
F-01..F-08 (Cycle 0, dispositioned in the loop doc); weekly re-probe
track (Gemma 4 pair, Gemini 3.8 Flash, muse-spark-1.1 empty streams,
nemotron-3-ultra + dots-3 tooling re-probes from the R4 battery's
unmeasured set).

**Open decisions (Jordan's, unchanged by this pass):** J-2 general
demotion enforcement (v0.8.2's cooldown is a bounded, single-class
memory — it does not settle J-2); J-3(i) logging-only provider-cost
capture; J-3(ii) judged-sample amendment; J-4 flaky-disable
proposal-only confirmation; D3 NVIDIA posture; D4 quota
source-of-truth; D5 handoff compilation to GitHub; Cloudflare $5/mo
Workers Paid; **Cerebras PAYG funding before ≈2026-12-12 trial
expiry (time-bound)**; UnoRouter consents (Nemotron 3 Super is one
provider — `cfp` — from clearing); Week-2 soak gate review +
`sovereign-v05-soak-daily` cron removal at the gate; **Hermes
client-key rotation (key value exposed in worker tool output on
2026-11-12 AND again on 2026-11-13 — rotation now recommended
twice-over; Jordan's call)**; broker-test sealed-key transfer +
shredding VM103 `secrets/client-keys.txt` after delivery; Ollama
account 402 (lane dead, account-level); SiliconFlow $0-model probe;
embeddings wiring (Voyage/Jina).

**Noted, not router faults:** exec-ops Telegram network flapping
(Hermes container, dual-stack issue — Hermes-side); Hermes auxiliary
client warning 2026-11-13 23:34 UTC (aux fallback can engage a paid
OpenRouter model — Hermes config, potential real spend, Hermes-side
fix); OpenClaw memory-sync "unknown embedding provider" faults
(OpenClaw-side).

## 3. Implementation A — v0.8.2 "Stream Resilience" (BUILT)

Branch `fix/v0.8.2-stream-resilience` (sr-build5, off main
`a4640aff`), commits `42f46e1` (code) + `637b80c` (docs).
**Tests 627 → 636 passed.** Code-only — rollback is an image swap.

1. **Stream bounds:** first chunk 45 s, inter-chunk stall 60 s
   (module constants, env-overridable:
   `SOVEREIGN_STREAM_FIRST_CHUNK_TIMEOUT_S`,
   `SOVEREIGN_STREAM_STALL_TIMEOUT_S`); expiry flows through the
   existing transport handler — ADD-06 commit guard and pre-commit
   failover untouched; new route signal `stream_bound_timeout:<entry>`.
   *Watch item:* 45 s is within ~1.4× of the slowest observed healthy
   first chunk (33.1 s); if `stream_bound_timeout` signals bite
   healthy turns post-deploy, raise via env — no redeploy of code
   needed beyond a restart with the env set.
2. **Bounded entry cooldown:** any streaming abort cools the entry
   600 s, persisted as an expiry in the quota store `meta` table
   (no schema change, survives restarts). Selection and sticky reuse
   skip it (`cooldown_stream_abort`) — **except** when it is the only
   candidate (annotated `cooldown_bypassed_only_candidate`; a
   cooldown can never manufacture a zero-candidate 429). Pins still
   override per J-1. This is a bounded memory for one proven failure
   class, explicitly **not** general J-2 enforcement.
3. **`truncated_tool_call` failure class** (carried R3 item):
   finish_reason=length + tool calls present, JSON + streaming
   paths; feeds the observe-only `would_demote` annotation.
4. **Docs:** CHANGELOG [0.8.2]; F-11 in `docs/IMPROVEMENT-LOOP.md`;
   full package in `docs/cycles/release-v0.8.2-decision.md`.
5. **Known gap:** golden replay not run — the golden tool/set exist
   only on the R4 branch. Replay v0.8.2's selection change against
   the R4 baseline (0 new / 5 known) when R4 lands, before whichever
   release ships second deploys.

**Rollout (standard gate):** Jordan's tap → push branch → PR → CI
read-back via GitHub API → snapshot `pre-v082` on VM103 → build
image `sovereign-router:v0.8.2` on VM103 → compose override swap +
recreate → /health + live smokes (incl. a stall-shaped probe and a
cooldown demonstration) → records (CHANGELOG/package on main, Drive
progress §28, sync record).

## 4. Implementation B — Observability (LIVE 2026-11-13 ~16:51 MST)

Unparked by Jordan 16:42 MST, including remote access.

- VM103 `/root/sovereign-router-v05/observability/` (own compose,
  production untouched): exporter :9465 (read-only state mounts,
  stdlib), Prometheus :9090 (15 s scrape, 7 d / 512 MB), Grafana
  :3001 (LAN-bound, anonymous OFF) — dashboard "Sovereign Router —
  Overview", 12 panels, provisioned as code.
- Remote: **https://grafana.launchcollective.org** — tunnel
  75586bde config v27→v28 via API (all 17 prior rules preserved,
  GET-verified), DNS CNAME (record 8567c0b0f871084ded2428bd44847a28),
  **Cloudflare Access app provisioned + verified** (Jordan-only,
  jordan.mason@launchcollective.org) in front of Grafana's own login.
- Verified: Prometheus targets UP; Grafana anon 401 / login 200;
  datasource query live (free_share 0.6975); premium MTD OpenClaw
  $20.1886 matches the route census.
- Grafana admin password: only in
  `observability/grafana-admin.txt` (mode 600, root) — deliverable
  to Jordan via one-time secret link on request.
- Caveat carried on the dashboard: ledger dollars are estimates;
  Hermes premium MTD ($124.80) includes the Nov 11 ceiling day.
- Rollback: restore tunnel v27 config → delete DNS + Access app →
  `docker compose down` in observability/. Router unaffected.

## 5. Recommended sequence from here

1. **Publish + deploy v0.8.2** (§3 rollout) — the F-11 fix.
2. **Publish R4 / v0.9.0** (push tap) → rebase onto v0.8.2 → golden
   replay cross-check (§3.5) → deploy at its own gate (registry +
   image rollback caveat) → decide re-probe cadence + F-10 scheduling.
3. **Hermes client-key rotation** — recommended now (two exposures).
4. **Week-2 soak gate** — one sitting: shadow comparison status,
   premium-alert test-fire, OpenClaw early-move question, remove
   the soak cron.
5. **Time-bound:** Cerebras funding decision before ≈2026-12-12;
   UnoRouter Nemotron consent is one click (`cfp`).
6. R3 (reasoning framework & telemetry) and R5 (catalog/price
   truth/candidates) proceed per the NEXT plan; J-2's general
   question gets its 1–2 weeks of `would_demote` data (clock
   started 2026-11-13) plus v0.8.2's cooldown telemetry as evidence.
