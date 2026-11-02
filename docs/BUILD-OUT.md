# Sovereign Router v0.5 — Build-Out Report

**Date:** 2026-11-01 · **Spec:** v0.5.3 (drafted 2026-10-31, owner Jordan Mason) · **Status: AT THE DEPLOY GATE — packaged, NOT deployed.**
Nothing in this report describes a running v0.5. Production is unchanged: v0.2.1 on :6070 is the live router and the rollback path.

---

## 1. What the spec calls for

One OpenAI-compatible router for OpenClaw (VM102), Hermes (VM112) and n8n (CT100), each with its own key and policy, that sends every request to the cheapest model able to do it well:

- **Merges** v0.3.3 (free-first ordering, paid fallback, per-model rate limits) and v0.4 alpha (proper source tree, failover rules, privacy lanes), shipped as a **built image from a source tree with tests — no startup patches**.
- **Adds four things:** a **local difficulty classifier** on VM103 (rules → embedding nearest-match → Arch-Router-1.5B sidecar, levels 1–5), **output checks** that escalate one quality level when a cheap answer is unusable, **sticky routing** inside an agent's tool loop (30-min window, hash-keyed, preserves prompt-cache pricing), and a **feedback/learning loop** from production outcome signals.
- **No spending caps, by owner's decision (2026-10-31):** no request is ever refused for budget reasons. Cost is controlled by routing, per-client **daily targets that steer and alert** (above target: cheaper models for levels 1–3, level 4 held to the cheapest quality-4 model, premium explicit-only), per-provider monthly spend alerts (DeepInfra $10, Ollama $5), an hourly **runaway alarm** (fixed thresholds for the first 14 days), projected-month and 3-day-trend alerts, a level-mix alert (>40% level 4+5), and an optional **emergency paid-lane switch** (owner decision).
- **Alerts are pull-based:** n8n polls `GET /admin/costs` every 15 min → Signal, email fallback, and a **daily all-clear at 08:10 MT** as a dead-man check; the router flags a >30-min missed poll as degraded in `/health`.
- **Privacy by design:** classification runs locally; logs never hold prompt or response text; a known-format **sensitivity scan** (gitleaks-style detectors; hashes/UUIDs excluded) forces flagged requests to cleared/private lanes only; free models that train on prompts stay off; OpenRouter free runs with `data_collection=deny`.
- **Headline targets (provisional until Phase 0 resets them):** ≥50% lower blended cost per request than production v0.2.1 **on the same traffic**, no increase in failed agent turns, added routing latency p50 < 300 ms / p95 < 1.5 s, escalations < 8%, failed requests < 1%, zero budget refusals, true level 4/5 under-classified < 5%.

## 2. Verified live state (audit, 2026-11-01)

| Item | Verified state |
|---|---|
| Host | VM103 `routellm`, **192.168.0.166** — 16 GB RAM / **~13 GB available**, 4 cores, **37 GB disk free** |
| Prod router | `sovereign-router-v1`, **v0.2.1, :6070** — OpenClaw. **Rollback path. Must stay, untouched.** |
| Canary | **v0.3.3 on :6073** (loopback) via `hermes-canary-proxy` nginx **:6074** — Hermes |
| v0.4 | canary stack present, **idle**, no host port |
| Also on VM103 | Portainer edge agent; `routellm` service on **:6060** |
| Registry | Local registry on CT100, **192.168.0.28:5000** |
| Clients | VM102 OpenClaw **192.168.0.188** · VM112 Hermes **192.168.0.114** · CT100 n8n **192.168.0.28** |

Capacity note: the spec's 2026-10-31 check said ~14 GB RAM available; the 2026-11-01 audit says ~13 GB. Either way the v0.5 footprint (classifier capped at 3 GB / 2 CPU, gateway capped at 4 GB / 2 CPU in the packaged compose) fits alongside the existing stacks, with the runbook requiring a fresh headroom check at deploy time.

### Production baseline (audited traffic — the numbers v0.5 is judged against)

- **656 requests** in the baseline window: **OpenClaw 508, Hermes 142** (6 unattributed/other).
- **Premium: 104 requests = $22.36 of spend** — premium, overwhelmingly OpenClaw's, is where the production money goes.
- **Hermes total spend: $0.13** — Hermes is already nearly free on the canary path; its v0.5 win is quality/reliability and correct level assignment, not raw dollars. The cost fight is OpenClaw's premium share, which is why the premium alerts (Decision #2) gate OpenClaw's move and why shadow mode compares v0.5 decisions against v0.2.1 actuals on identical traffic.

## 3. What is being built in this pass

| Piece | Location | State at this report |
|---|---|---|
| **Phase 0 tools** (on v0.3.3: flagged combined keyword+verb rule, log-only sensitivity scan, tool-call/length output checks, n8n→Signal alert chain with daily all-clear, measurement reporting) | `../phase0/` | In this pass — one patch revision only, everything flag-controlled, per spec |
| **v0.5 gateway source tree** (registry v2, client policies, cost governor, sticky routing, route headers, admin endpoints, full test suite) | `../src/` | **Being built in parallel** — tree was still empty when this package was written; the compose file builds against `../src` and does not touch its Dockerfile |
| **Classifier** (embedding layer + Arch-Router/Qwen sidecar, route descriptions) | `../classifier/` | **Being built in parallel** — same note as above |
| **Example bank** | with the classifier | **150** hand-written labeled examples for the bank + **80 held-out** examples from Phase 0 traffic patterns for evaluation (acceptance: ≥70% exact level match; true 4/5 → ≤3 under 5%) |
| **Deploy package** | `../deploy/` | **Done in this pass** — see §4 |
| **Analysis / tests / source-pull** | `../analysis/`, `../tests/`, `../source-pull/`, `../build/` | Supporting workspaces for this pass |

### 4. The deploy package (`deploy/`)

| File | Contents |
|---|---|
| `docker-compose.v05.yml` | Portainer-ready stack: gateway (build `../src`, image `192.168.0.28:5000/sovereign-router:v0.5.0`) + classifier sidecar (build `../classifier`) + nginx. Internal network for gateway↔classifier; gateway publishes **no** port; nginx publishes the only port, `192.168.0.166:6075`. Hardening on all services: read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, memory/CPU/PID limits (sidecar exactly **3 GB / 2 CPU** per spec), no Docker socket. State via host state directory; keys as a read-only mount of individual mode-600 files; healthchecks on all three services. |
| `nginx.conf` | Allow **192.168.0.188, 192.168.0.114, 192.168.0.28 only; deny all**. Proxies to the gateway with full SSE support: buffering off, caching off, 3600 s read/send timeouts, `X-Accel-Buffering: no`. |
| `.env.example` | Non-secret configuration template only — host paths, internal ports, classifier timeout. States explicitly that no key value ever goes in it. |
| `clients.example.txt` | clients-file format template with non-functional placeholder hashes, the three proposed client policies, and the key-generation pattern. No real keys or hashes. |
| `deploy-runbook.md` | Pre-flight (Proxmox snapshot of VM103 before each phase, backup of `/root/sovereign-router/data` + the CT100 registry), build+push to the local registry, the per-environment key checklist (including the **Groq + Ollama rotation open since 2026-10-22**, provider-side limits, OpenRouter per-key credit limit), stack deploy, full smoke tests (health, models, chat non-stream + stream + tool call **per client key**, admin endpoints, alert chain), the Phase 3 canary sequence (Week 1 Hermes 6073→6075 and the OpenClaw **shadow-mode design** — decision-only copies, no upstream call; Week 2 OpenClaw gated on premium alerts being set; n8n last), the **7-day soak checklist mapped to all 9 spec success measures**, and rollback per phase copied from the spec's Rollback section. |
| `DECISIONS.md` | One-page decision sheet: the spec's 7 owner decisions, each with the proposed default, the cost/risk of each option, and the phase it gates. |
| `acceptance-checklist.md` | Every Testing/acceptance criterion in the spec as a checkable item with its verification method, plus the Phase 0/1/2 acceptance items, deployment/hardening checks, and the Phase 3/5 owner gates. |

## 5. Phase-by-phase status

| Phase | Scope | Status |
|---|---|---|
| **0** — Measure & fix the obvious (v0.3.3, 3–5 days) | Route-log measurement (free/paid split, level distribution, cost/request) to reset the provisional targets; flagged rule fixes; log-only secret scan; alert chain + daily all-clear; **key hygiene incl. the overdue Groq/Ollama rotation** | Tools in this pass; measurement window and rotation **not yet executed** — the rotation is an owner action at the providers |
| **1** — v0.5 core (~1 week) | Source tree, registry v2, policies, cost governor, sticky routing, admin endpoints, full test suite; image into the local registry | **In build (parallel)** — source tree not yet landed at packaging time |
| **2** — Classifier sidecar (3–5 days) | llama.cpp + Arch-Router-1.5B + embedding model; benchmarks (embedding p50 < 50 ms; router in-path only if p95 < 1.2 s, else offline labeler); 150 + 80 examples; Katanemo license review | In build (parallel); **license decision (#6) open** |
| **3** — Canary, real traffic (2 weeks) | Week 1: Hermes → :6075, OpenClaw shadow mode. Week 2: OpenClaw live (gated on premium alerts + shadow savings), n8n last. 7-day soak vs the success measures | **Not started — at the deploy gate** |
| **4** — Learning loop (after 2–4 weeks of data) | Nightly quality scores → proposed registry changes (owner-approved); example bank grows; judged sample only if approved | Not started; Decision #5 open |
| **5** — Production cutover | Jordan's written go/no-go after the soak report; v0.2.1 idle 30 days, then retired | Not started; owner decision by design |

## 6. What is NOT deployed, and why

**Nothing is deployed.** Specifically: no v0.5 stack exists on VM103, port **6075 is not listening**, no image has been built or pushed to `192.168.0.28:5000`, no v0.5 keys (provider or client) have been created, no client has been pointed at v0.5, and VM103 has not been snapshotted for this work. Production v0.2.1 (:6070), the v0.3.3 canary (:6073/:6074) and everything else on VM103 are exactly as audited.

Two independent reasons, either of which is sufficient:

1. **The deploy gate.** This pass was commissioned as *package only, at the deploy gate*. The runbook's own pre-flight also cannot pass yet: the gateway and classifier source trees were still empty when the package was written, so there are no Dockerfiles to build, no test suite to pass, and no internal port/health contract to reconcile against the compose file.
2. **Seven owner decisions are open** (`deploy/DECISIONS.md`). Three of them hard-gate early phases: the emergency paid-lane switch and the sensitive-provider list and client targets are Phase 1 configuration; the Arch-Router license gates Phase 2; the premium alert levels are a spec-stated entry condition for OpenClaw's Phase 3 Week 2 move. Deploying before they are answered would mean shipping someone else's guesses as Jordan's policy on the only mechanism that can limit spend, on which providers may see secrets, and on the license for a core component.

Also outstanding outside the deploy itself: the **Groq and Ollama key rotation, open since 2026-10-22**, is an owner action at the providers and is scheduled into Phase 0 / runbook §3.2 — v0.5 should not launch on un-rotated providers, and the canary should stop running on copies of production keys (separate OpenRouter/DeepInfra canary keys, Phase 0).

## 7. Exact next step for Jordan

**Answer the decision sheet: `deploy/DECISIONS.md` — seven one-line answers.** That single pass unblocks everything downstream:

- **#1, #3, #4** → Phase 1 configuration can be finalised as the source build lands.
- **#6** → the classifier model choice (Arch-Router vs Qwen) can be locked for Phase 2.
- **#2** → can be set any time before Phase 3 Week 2, but the baseline says premium is $22.36 of the spend, so sooner is better.
- **#7** → fixes the Phase 3 sequence; **#5** can wait until Phase 4.

In parallel, one action only Jordan can do: **rotate the Groq and Ollama keys at the providers** (open since 2026-10-22, runbook §3.2) — the runbook turns that into the per-environment key set v0.5 launches with.

Once decisions are in and the parallel source/classifier builds land with green tests, the next execution step is runbook §1–§2 (snapshot VM103, back up state + registry, build and push `sovereign-router:v0.5.0`) — **and that step still requires a separate, explicit go from Jordan. This report is not that go.**

---

## Addendum — build completion, 2026-11-01 night (Muse verification)

All three parallel builds have now landed and were independently verified by Muse:

- **Gateway source tree** (`src/`, ~2,900 lines): complete pipeline per spec. **88 pytest tests pass** (re-run by Muse: 88 passed in 1.61s), plus a live uvicorn smoke test (boots, /health correct, unauthenticated request → 401).
- **Classifier sidecar** (`classifier/`): 150 labeled examples (L1 30 / L2 34 / L3 34 / L4 30 / L5 22) + 80 held-out with zero overlap. **Benchmark on the TF-IDF fallback (the floor — no sentence-transformers on this box): exact level match 90.0% (bar ≥70%), true L4/L5 under-classified 0.0% (bar <5%), p50 ~35ms.** Re-run with bge-small installed before the formal Phase 2 judgment.
- **Phase 0** (`phase0/`): usage analyzer tested on both log schemas; Phase 0 report filled with the verified prod baseline; the single flagged patch revision written but NOT applied (anchors marked ANCHOR-TODO — must be re-verified against live v0.3.3 source in the patch window); n8n alert-chain build spec complete.
- **Deploy contract reconciled by Muse:** the compose file was written before the images existed and guessed wrong ports/env. Fixed to the built reality — gateway serves 6075 (`SOVEREIGN_STATE_DIR`, `SOVEREIGN_SIDECAR_URL`, state/keys/*.key), classifier serves `PORT` 8080, nginx upstream gateway:6075, healthchecks use the images' own python. Compose YAML re-validated.

**Gate state unchanged: CLOSED.** Nothing deployed. Remaining blockers are owner-side only: the 7 decisions in `deploy/DECISIONS.md` and the Groq/Ollama key rotation (open since 2026-10-22). Runbook execution still requires a separate explicit go.
