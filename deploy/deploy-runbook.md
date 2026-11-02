# Sovereign Router v0.5 — Deploy Runbook

**Status: DEPLOY GATE — this runbook has NOT been executed.** Prepared 2026-11-01 from spec v0.5.3. Execute only after (a) the Phase 1 source build and test suite pass, (b) the classifier build exists, and (c) Jordan has answered the decisions in `DECISIONS.md` that gate the phase being deployed. Production **v0.2.1 on :6070 stays running and untouched through every step below** — it is the rollback path.

**Target host:** VM103 `routellm`, 192.168.0.166 — verified 2026-11-01: 16 GB RAM / ~13 GB available, 4 cores, 37 GB disk free.
**Already on VM103 (do not disturb):** `sovereign-router-v1` prod v0.2.1 :6070 (OpenClaw), v0.3.3 canary :6073 loopback via `hermes-canary-proxy` nginx :6074 (Hermes), v0.4 canary (idle, no host port), Portainer edge agent, `routellm` :6060.
**Registry:** local, on CT100 — `192.168.0.28:5000`.
**This stack:** `docker-compose.v05.yml` in this directory — gateway + classifier sidecar + nginx, one published port: **192.168.0.166:6075**.

> Operator note: commands below are the intended commands for the operator at deploy time (Proxmox host / VM103 shell / Portainer). Record the execution — date, snapshot name, image digest, and each check result — in an execution log appended to this file's deployment copy. Never paste a key value into that log.

---

## 1. Pre-flight

Do all of this **before** anything is built or deployed. Any failure = stop, stay at the gate.

### 1.1 Confirm the gate conditions
- [ ] `../src/Dockerfile` and `../classifier/Dockerfile` exist and are the reviewed builds from the parallel build pass (this package was written while those trees were still empty — their arrival is a hard precondition, not a formality).
- [ ] Phase 1 test suite green (see `acceptance-checklist.md` §C) and Phase 2 classifier benchmarks recorded (§D), including which classifier mode ships: in-path (p95 < 1.2 s) or offline labeler.
- [ ] `DECISIONS.md`: #1, #3, #4 answered (Phase 1 config); #6 answered (Phase 2); #2 answered before Phase 3 Week 2; #7 before Phase 3 sequencing is final; #5 any time before Phase 4.
- [ ] Confirm the gateway's real internal listen port and health command from the final `../src` image and reconcile them with `GATEWAY_INTERNAL_PORT` / the compose healthcheck. Same for the classifier's port/health endpoint.

### 1.2 Proxmox snapshot of VM103 (spec: Rollback — before *each* phase deployment)
On the Proxmox host (via the established key-based access path):
```bash
qm snapshot 103 pre-v05-<phase>-<YYYYMMDD> --description "Sovereign Router v0.5 <phase>, pre-deploy"
qm listsnapshot 103   # verify the snapshot exists before proceeding
```
Repeat with a fresh snapshot before Phase 2's sidecar deploy, before Phase 3 Week 1, and before Phase 3 Week 2. A snapshot from an earlier phase is not a substitute.

### 1.3 Back up current production state
- [ ] Production router state (rollback insurance for v0.2.1, untouched by this deploy but backed up while you are here):
  ```bash
  # on VM103
  tar -czf /root/backups/sovereign-router-data-<YYYYMMDD-HHMM>.tar.gz /root/sovereign-router/data
  ```
  (Adjust the source path if the live v0.2.1 state dir differs — verify with `docker inspect sovereign-router-v1` mounts first. Also copy the current stack compose files for stacks 10/12/13/14 from Portainer / their on-host locations into the same backup set.)
- [ ] Phase 0 rollback material: current v0.3.3 patch file + registry from the `/appdata` backups (spec: Phase 0 rollback) — confirm they exist and are dated.
- [ ] Registry backup (CT100): back up the registry's storage directory on CT100 (verify its actual path from the registry container's mounts on CT100 before tarring — do not assume):
  ```bash
  # on CT100, after confirming <registry-data-dir> from docker inspect
  tar -czf /root/backups/registry-<YYYYMMDD-HHMM>.tar.gz <registry-data-dir>
  ```
- [ ] Copy this `deploy/` directory as-deployed (compose + nginx.conf + filled non-secret `.env`) into the backup set. **Never** include the secrets directory or `clients.txt` in any backup that leaves VM103.

### 1.4 Host preparation on VM103
- [ ] Create the directories: `mkdir -p /root/sovereign-router-v05/{state,secrets,models}` — `secrets` mode `700`, owned by root.
- [ ] Confirm ports: `6075` free on 192.168.0.166; `6070/6073/6074/6060` untouched.
- [ ] Confirm headroom again at deploy time (RAM available ≥ ~8 GB after the sidecar's 3 GB and gateway's 4 GB limits are notionally reserved; disk free ≥ 20 GB after the ~1.7 GB Arch-Router GGUF + images).

---

## 2. Build and push the images

No startup patches (spec: Deployment). Images are built from the source trees and pushed to the local registry; the stack pulls by tag from there.

```bash
# from this deploy/ directory, on the build host with the workspace tree
docker build -t 192.168.0.28:5000/sovereign-router:v0.5.0          ../src
docker build -t 192.168.0.28:5000/sovereign-router-classifier:v0.5.0 ../classifier

docker push 192.168.0.28:5000/sovereign-router:v0.5.0
docker push 192.168.0.28:5000/sovereign-router-classifier:v0.5.0

# record the digests in the execution log
docker image inspect 192.168.0.28:5000/sovereign-router:v0.5.0          --format '{{index .RepoDigests 0}}'
docker image inspect 192.168.0.28:5000/sovereign-router-classifier:v0.5.0 --format '{{index .RepoDigests 0}}'
```
- [ ] Classifier model files staged into `/root/sovereign-router-v05/models` on VM103: Arch-Router-1.5B Q8 GGUF (~1.7 GB) **only if Decision #6 cleared the Katanemo license**, otherwise the Qwen fallback model; plus the embedding model (bge-small / all-MiniLM class) and the example bank (embeddings + labels only — 150 examples, §5 of the build; the held-out 80 are *not* deployed).
- [ ] Secret-scan the built images before push: confirm no key material was baked in (build args, copied `.env`, test fixtures): `docker history` review + grep the image filesystem for a known canary string planted in the build context. Any hit = rebuild, do not push.

---

## 3. Per-environment keys — creation checklist

One rule throughout (spec: Deployment / Risks): **keys per environment.** v0.5 gets its own provider keys and its own client keys. Production keys stay only in production; canary keys stay only in the canary. Keys live as individual **mode-600 files** in `/root/sovereign-router-v05/secrets/` on VM103, read by the gateway at start. Never in compose, `.env`, the image, the registry, Drive, chat, or logs. Rotation = overwrite the file + restart the gateway service.

### 3.1 Provider keys for v0.5 (create new, at each provider)
- [ ] **Groq** — new v0.5 key → `secrets/groq.key`
- [ ] **Ollama Cloud** — new v0.5 key → `secrets/ollama.key`
- [ ] **OpenRouter** — new v0.5 key → `secrets/openrouter.key`, **with a provider-side credit limit set on the key** (OpenRouter supports per-key credit limits — set one; it is a provider-side guardrail, not a router cap)
- [ ] **DeepInfra** — new v0.5 key → `secrets/deepinfra.key`
- [ ] **Premium-lane providers** (per Decision #2 — only those Jordan keeps): new v0.5 keys → `secrets/anthropic.key` / `openai.key` / `gemini.key` as applicable
- [ ] **Provider-side limits/alerts set where the provider offers them**, set high (they notify/guard at the account level; the router itself never caps spend): DeepInfra monthly spend alert **$10**, Ollama monthly spend alert **$5** (carried from the v0.3.3 canary), OpenRouter credit limit per above, premium providers per Decision #2.
- [ ] Every file verified: `stat -c '%a %n' /root/sovereign-router-v05/secrets/*` → all `600`.

### 3.2 ⚠ Phase 0 rotation still open since 2026-10-22 — do this in the same sitting
- [ ] **Rotate the Groq key(s)** at the provider (owner action at the provider console) — overdue since **2026-10-22**.
- [ ] **Rotate the Ollama key(s)** at the provider — overdue since **2026-10-22**.
- [ ] After rotation, update **every** environment that legitimately uses those providers (production / canary / v0.5 each with its *own* new key where the provider allows multiple keys) and restart only the affected services. Confirm the old keys are revoked at the provider, not merely replaced locally.
- [ ] Confirm the v0.3.3 canary no longer runs on copies of production keys for OpenRouter and DeepInfra (Phase 0 item): separate canary keys in place there too.

### 3.3 Client keys for v0.5 (one per client, hashes only in the router)
Per `clients.example.txt`:
- [ ] Generate a fresh random key for each of **hermes**, **openclaw**, **n8n** (e.g. `openssl rand -hex 32` on the client host).
- [ ] Write only the **sha256 hash** of each into `secrets/clients.txt` with its policy line (data class `internal`; lanes and premium per `DECISIONS.md` #4 / spec Client policies; daily targets per Decision #4).
- [ ] Generate the **admin key** the same way → `secrets/admin.key` (never a client line; unlocks `/admin/*` only).
- [ ] Deliver each raw key only to its own client's configuration (VM112 Hermes, VM102 OpenClaw, CT100 n8n) at its Phase 3 step — not before, and not all three at once. Delete intermediate copies.
- [ ] `clients.txt` and `admin.key` verified mode `600`.

---

## 4. Stack deploy

- [ ] Fresh Proxmox snapshot (§1.2) — taken for *this* phase, today.
- [ ] Copy `.env.example` → `.env` **on VM103**, fill in host paths/ports only (no secrets — there is nothing secret in `.env` by design). Place `nginx.conf` beside the compose file.
- [ ] In Portainer: create stack **sovereign-router-v05** from `docker-compose.v05.yml` (or `docker compose -f docker-compose.v05.yml up -d` on VM103 if Portainer's edge agent path is unavailable — record which was used).
- [ ] Verify the running stack:
  - All three containers up and (after start periods) healthy.
  - `docker ps`: the **only** published port is `192.168.0.166:6075` (nginx). Gateway and classifier publish nothing.
  - `docker inspect` each container: `ReadonlyRootfs=true`, `CapDrop=[ALL]`, `no-new-privileges`, memory/CPU/PID limits present (classifier: 3 GB / 2 CPU), no `/var/run/docker.sock` mount anywhere.
  - From a **non-allowlisted** LAN address: connection to 192.168.0.166:6075 is denied. From VM102/VM112/CT100: reachable.
  - Production check: OpenClaw still answering on :6070 (v0.2.1), Hermes still on the canary path (:6074 → :6073).

---

## 5. Smoke tests (run immediately after deploy, before any client moves)

Run from an allowlisted host (CT100 is convenient). `<key>` = that client's v0.5 raw key, typed at the terminal — never written into a script file or this log.

### 5.1 Health and discovery
- [ ] `curl -s http://192.168.0.166:6075/health` → healthy; classifier reported up (or, if the sidecar is intentionally offline-mode, rules-fallback state is reported, not silent); alert chain not degraded *or* degraded correctly (no n8n poller exists yet at first deploy — degraded is the honest expected state until §6's workflow polls).
- [ ] `curl -s -H "Authorization: Bearer <key>" http://192.168.0.166:6075/v1/models` → alias list (`auto, cheap, strong, premium, code, long`) for each client key; premium aliases absent/denied per policy for hermes and n8n keys.
- [ ] `GET /v1/quota` and `GET /v1/usage` with each key return only that client's scope.

### 5.2 Chat — per client key (hermes, openclaw, n8n), all four shapes
For **each** key:
- [ ] **Non-stream chat:** `POST /v1/chat/completions`, small prompt → 200, sane answer, and headers present: `X-Sovereign-Model`, `X-Sovereign-Level`, `X-Sovereign-Task`, `X-Sovereign-Lane`, `X-Sovereign-Cost-Estimate`, `X-Sovereign-Escalated`.
- [ ] **Stream chat:** same request with `"stream": true` → SSE chunks arrive incrementally (not one buffered blob — verifies nginx `proxy_buffering off`), clean `[DONE]`, no mid-stream cut on a long answer.
- [ ] **Tool call:** request with a tool definition → well-formed tool call returned; arguments are valid JSON and the function name is in the request's tool list (streamed variant too: arguments arrive complete, having been buffered and validated).
- [ ] **JSON mode:** request with JSON mode → response parses as JSON.
- [ ] Route log line exists for each request with level, task class, classifier layer + confidence, chosen model, skipped models + reasons, latency split, tokens, cost — and **no prompt or response text** (eyeball + grep for a unique string sent in the test prompt).

### 5.3 Admin endpoints
- [ ] With the **admin key**: `GET /admin/costs` (spend by client/provider/level), `GET /admin/routes` (24 h decision stats), `POST /admin/reload` (after a trivial registry comment change), `POST /admin/killswitch` — verify it exists and is **off by default**; do not leave any client killed.
- [ ] With a **client key**: all `/admin/*` → refused. With **no key**: everything except `/health` → refused.
- [ ] Error contract spot checks: disallowed lane → `403`; an over-context request no model can fit → `413`.

### 5.4 Alert chain (Phase 0 deliverable, exercised here)
- [ ] n8n polling workflow live: `GET /admin/costs` every 15 min; router `/health` shows a recent last-poll time (< 30 min).
- [ ] Force a test alert → arrives on Signal via the workflow. Force Signal failure → same alert arrives by **email fallback**.
- [ ] Daily all-clear scheduled for **08:10 America/Denver** with the cost summary (spend by client/provider/level, free share, alerts raised).

---

## 6. Phase 3 canary sequence (2 weeks — only after §§1–5 pass for the relevant step)

### Week 1 — Hermes moves; OpenClaw shadow mode starts
- [ ] Fresh Proxmox snapshot of VM103.
- [ ] **Hermes (VM112):** point Hermes at `http://192.168.0.166:6075` with its **new v0.5 client key** (Hermes policy: internal, private-free + paid, **no premium**, daily target per Decision #4 — proposed $1). v0.3.3 canary **stays running** on :6073/:6074 as Hermes' rollback.
- [ ] Watch the first hours of route logs: level distribution, free share, escalations, failed turns vs the Phase 0 v0.3.3 baseline. Hermes prompts (12k–28k tokens) must be skipping Groq (8k tokens/min) cleanly, not queueing on it.
- [ ] **OpenClaw shadow mode (design — build/confirm this in Week 1):** OpenClaw keeps using production v0.2.1 on :6070 for every real request. In parallel, v0.5 receives a **decision-only copy of each request profile** — the profiling features only (estimated tokens, tools present, images, JSON mode, tool-loop position, last-user-message *features* needed to classify) — **no upstream model call is made, no answer is generated, nothing is returned to OpenClaw.** v0.5 logs the model/level/lane it *would* have chosen and its estimated cost. Purpose: an apples-to-apples cost comparison on identical traffic (v0.5 decisions vs v0.2.1 actuals) before OpenClaw is exposed to v0.5 at all. Confirm in the shadow logs: zero upstream calls, zero client-visible effect, no prompt text stored.
  - Implementation note for the builder: shadow intake is an admin/internal path, not the client key path; rate-limit it so a shadow flood can never starve the gateway, and tag every shadow route-log line `shadow=true` so soak metrics exclude it from served-traffic counts (it counts only in the cost-comparison measure).

### Week 2 — OpenClaw moves (gated); n8n last
**Entry conditions — all must hold, no exceptions:**
- [ ] **Premium alerts set** (Decision #2: monthly premium alert — proposed $20 — and daily premium-request alert — proposed >20/day) and verified firing in test. *This is a spec-stated Phase 3 entry condition.*
- [ ] Shadow mode shows **lower cost on the same traffic** than v0.2.1 actuals.
- [ ] Cost/free-share targets reset from Phase 0 data; Week 1 Hermes soak rows (§7) clean.
- [ ] Fresh Proxmox snapshot of VM103.
- [ ] **OpenClaw (VM102):** point OpenClaw at :6075 with its v0.5 key (policy: internal, private-free + paid + **premium**, premium only at level 5 or explicit request, daily target per Decision #4 — proposed $3). Production v0.2.1 stays up on :6070, idle-for-OpenClaw but available.
- [ ] **n8n (CT100) last** (per Decision #7): point n8n's router calls at :6075 with its v0.5 key (internal, private-free + paid, no premium, proposed $0.50/day target). The alert-polling workflow is already talking to `/admin/costs` — confirm it now also appears correctly as a *client* in the route logs.

### Phase 4 / Phase 5 (for completeness — not part of this canary)
- Phase 4 (after 2–4 weeks of data): nightly quality-score job proposes registry changes for owner approval; example bank grows from logged embeddings; judged sample **only if Decision #5 = ON**.
- Phase 5: production cutover **only on Jordan's written go/no-go** after the soak report. v0.2.1 stays deployed but idle for **30 days** as rollback, then is retired.

---

## 7. Seven-day soak checklist — every spec success measure

Review route logs daily. Targets marked (provisional) are judged against the Phase 0 reset values, per spec.

| # | Success measure (spec) | Target | Source | Day-by-day result |
|---|---|---|---|---|
| 1 | Cost on the same traffic — OpenClaw: v0.5 (shadow) decisions vs v0.2.1 actual; Hermes: v0.5 vs v0.3.3 Phase 0 baseline | **≥ 50% lower** (provisional) | Shadow log + route logs | ☐ D1 ☐ D2 ☐ D3 ☐ D4 ☐ D5 ☐ D6 ☐ D7 |
| 2 | Share of requests served free, per client | Set from Phase 0 data (working figures: OpenClaw 60%, Hermes 30–40%) | Route log | ☐ D1 ☐ D2 ☐ D3 ☐ D4 ☐ D5 ☐ D6 ☐ D7 |
| 3 | Quality escalations (cheap answer rejected) | **Under 8%** of requests | Route log escalation field | ☐ D1 ☐ D2 ☐ D3 ☐ D4 ☐ D5 ☐ D6 ☐ D7 |
| 4 | Failed requests returned to clients (4xx/5xx other than auth) | **Under 1%** | Route log | ☐ D1 ☐ D2 ☐ D3 ☐ D4 ☐ D5 ☐ D6 ☐ D7 |
| 5 | Added routing latency | **p50 < 300 ms, p95 < 1.5 s** | Route log timing fields | ☐ D1 ☐ D2 ☐ D3 ☐ D4 ☐ D5 ☐ D6 ☐ D7 |
| 6 | Days over the daily cost target | **Fewer than 2 in 7**, with an alert each time | Route log + daily cost summary | ☐ D1 ☐ D2 ☐ D3 ☐ D4 ☐ D5 ☐ D6 ☐ D7 |
| 7 | Requests refused for budget reasons | **Zero** (emergency switch, if ON, may 429 only requests no free model can serve) | Route log | ☐ D1 ☐ D2 ☐ D3 ☐ D4 ☐ D5 ☐ D6 ☐ D7 |
| 8 | True level 4/5 requests classified at level 3 or below | **Under 5%** on the labeled test set and in sampled review | Classifier evaluation | ☐ D1 ☐ D2 ☐ D3 ☐ D4 ☐ D5 ☐ D6 ☐ D7 |
| 9 | Alert chain health | **Daily all-clear received every day; no missed poll longer than 30 min** | n8n heartbeat + router `/health` | ☐ D1 ☐ D2 ☐ D3 ☐ D4 ☐ D5 ☐ D6 ☐ D7 |

Daily extras (not spec measures, check anyway): level-mix — alert if level 4+5 > 40% of a client's day; any premium request carries its `X-Sovereign-Cost-Estimate`; no failed agent turns attributable to routing; v0.2.1 on :6070 still healthy and untouched.

**Soak verdict:** ☐ Pass — write the soak report, Phase 5 becomes Jordan's go/no-go decision. ☐ Fail — name the failed rows, roll back the affected client per §8, keep the data.

---

## 8. Rollback — exactly per spec § Rollback

- **Phase 0:** restore the previous registry and patch file from the `/appdata` backups and redeploy the previous stack 12 compose.
- **Phases 1–3:** point the client back at **v0.3.3 (Hermes)** or **v0.2.1 (OpenClaw)** by changing its **base URL and key**; **delete or stop the v0.5 stack.** (n8n rolls back the same way, to whatever endpoint it used before Phase 3.) Roll back one client at a time — a Hermes problem is not an OpenClaw rollback.
- **Phase 5:** switch the production endpoint back to **v0.2.1**, which stays deployed for **30 days**.
- **VM level:** a **Proxmox snapshot of VM103 is taken before each phase deployment** (§1.2). Snapshot restore is the last resort, not the first — client-level rollback above is faster and doesn't touch the other stacks on VM103. If a snapshot restore is ever used, re-verify **all** VM103 stacks afterwards (:6070 prod, :6073/:6074 canary, :6060 routellm, Portainer edge agent), not just v0.5.

After any rollback: keep the v0.5 route logs and state directory intact for diagnosis (copy them off before stopping/deleting the stack), and record what failed against §7 / `acceptance-checklist.md` before any re-attempt.
