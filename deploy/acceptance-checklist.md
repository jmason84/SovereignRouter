# Sovereign Router v0.5 — Acceptance Checklist

Every Testing/acceptance criterion from spec v0.5.3, as a checkable item with its verification method. Nothing here is checked yet: the stack is packaged, not deployed (deploy gate, 2026-11-01). "Test name" entries refer to the Phase 1 test suite in `../tests/` / `../src/` (parallel build — names to be reconciled against the final suite at Phase 1 acceptance; a renamed test is fine, a missing one is not).

---

## A. Spec § "Testing and acceptance criteria" (verbatim criteria)

- [ ] **Auth, validation and lane-permission matrix tested live.**
  Verify: live test matrix against the deployed canary — each client key × each lane (private-free / paid / premium) × valid/invalid/missing key; expect success only where the client policy allows, `403` for a lane the client may not use. Covers Hermes (no premium), OpenClaw (premium), n8n (no premium) from `clients.txt` policies.
- [ ] **Failover against a real second provider for 429, 5xx and timeout.**
  Verify: integration test with a real second provider (not only mocks) — force/induce each of 429, 5xx, and a timeout on the first candidate; request completes on the next candidate and the route log records the skip reason.
- [ ] **Provider rate limits never exceeded under concurrent load.**
  Verify: concurrency / rate-limit test — parallel agent-shaped load against the quota store; assert per-provider per-minute and per-day windows (requests + tokens) are never exceeded; oversize-for-window models are skipped, not waited on (e.g. Groq 8k tokens/min vs Hermes-size prompts).
- [ ] **No request refused for budget reasons.**
  Verify: test + soak log audit — drive a client above its daily target and confirm requests still complete (steering changes model choice only); grep the soak route log for budget refusals: expected count **zero**. Sole exception by design: requests no free model can serve while the emergency paid-lane switch (Decision #1, if ON) is tripped return `429` with the reason.
- [ ] **Spend and runaway alerts reach Signal.**
  Verify: manual step — trigger a spend alert and a runaway-threshold alert in the canary; confirm the n8n polling workflow (`GET /admin/costs`, 15-min poll) delivers both to Signal; repeat with Signal delivery forced to fail and confirm the same message arrives by **email fallback**.
- [ ] **Escalation path proven with a model forced to return empty or malformed tool calls.**
  Verify: test with a stub/mock upstream forced to return (a) an empty answer with no tool call, (b) a tool call with invalid-JSON arguments, (c) a tool name not in the request's tool list, (d) `finish_reason=length` with no usable content, (e) unparseable content in JSON mode — each retries **once** on the next candidate one quality level higher and logs an escalation.
- [ ] **Sticky routing keeps one model across a 10-step tool loop, and switches when that model is rate-limited or failing.**
  Verify: test — 10 sequential tool-round-trip requests for one conversation (client + hash of first user message + system prompt) stay on one model inside the 30-minute activity window; then rate-limit (and separately, fail) that model and confirm the conversation switches; also confirm a new user message classified at a higher level may switch.
- [ ] **No prompt or response text in any log, ledger or database.**
  Verify: grep check — send requests containing unique canary strings in prompt and forced response; then `grep -r` those strings over the state directory, route logs, usage ledger, quota store and `docker logs` for all three containers. Expected: **zero matches**. (Sensitivity-scan matches are likewise never logged.)
- [ ] **Real OpenClaw, Hermes and n8n traffic through the canary.**
  Verify: Phase 3 sequence in the runbook — Hermes on :6075 (Week 1), OpenClaw shadow then live (Week 2), n8n last (per Decision #7); route logs show real traffic from all three client keys.
- [ ] **7-day soak meets the success measures.**
  Verify: the soak checklist in `deploy-runbook.md` §7, which maps every spec success measure to its target and source. All rows must be measured; provisional cost/free-share targets are judged against the Phase 0 reset values.

## B. Phase 0 acceptance (spec § Phase 0)

- [ ] Phase 0 report exists with the measured per-client free/paid split, difficulty-level distribution, escalations and cost per request (3–5 days of v0.3.3 route logs).
- [ ] Paid share of Hermes requests falls with **no rise in failed turns** after the flagged rule changes.
- [ ] At least **3 consecutive daily all-clear messages** received (08:10 MT cost summary via the n8n → Signal chain).
- [ ] Sensitivity scan ran log-only; match counts and a false-positive sample review recorded (hashes/UUIDs/container IDs/SHAs excluded by the known-format detectors).
- [ ] Key hygiene done: Groq and Ollama keys rotated (open since 2026-10-22); separate canary keys created for OpenRouter and DeepInfra (canary no longer uses copies of production keys).

## C. Phase 1 acceptance (spec § Phase 1)

- [ ] Full test suite passes: v0.2.1-style end-to-end tests against mock upstreams; v0.4 failover matrix (401/403/408/413/429/5xx/timeout/connection error); concurrency and rate-limit tests.
- [ ] Agent-shaped request tests pass: no custom headers, 8–150 KB prompts, tool round trips, streaming tool calls (buffered to completion, validated before sending), JSON mode, model aliases (`auto/cheap/strong/premium/code/long`) and `X-Sovereign-Level` override (logged).
- [ ] Streaming text behaviour matches spec: checks run before the first content chunk is committed; after commit, only empty-answer/length conditions are logged, not escalated.
- [ ] Response headers present: `X-Sovereign-Model`, `X-Sovereign-Level`, `X-Sovereign-Task`, `X-Sovereign-Lane`, `X-Sovereign-Cost-Estimate`, `X-Sovereign-Escalated`.
- [ ] Error contract verified: `429` (+`Retry-After`, skip reasons) only when every candidate is provider-rate-limited or failing; `413` only when no eligible model can ever fit; `403` for disallowed lanes.
- [ ] Admin endpoints work with the admin key only: `POST /admin/reload`, `GET /admin/routes`, `GET /admin/costs`, `POST /admin/killswitch` (off by default); client keys cannot reach them.
- [ ] `GET /v1/models`, `GET /health`, `GET /v1/quota`, `GET /v1/usage` respond and are scoped to the caller's key; `/health` reports alert-chain degraded after 30 min without an n8n poll, and classifier-sidecar health.
- [ ] Cost governor verified in test: above-target steering (levels 1–3 cheaper, level 4 held to cheapest quality-4, premium explicit-only), level-mix alert at >40% level 4+5 in a day, projected-month alert, 3-day trend alert, estimated cost corrected to actual (incl. cached-token pricing) after the call.
- [ ] Image built from the source tree (no startup patches) and **stored in the local registry** as `192.168.0.28:5000/sovereign-router:v0.5.0`.

## D. Phase 2 acceptance (spec § Phase 2 — classifier)

- [ ] Katanemo / Arch-Router license reviewed and Decision #6 answered, **or** the Qwen fallback is the shipped model.
- [ ] 150 hand-written labeled examples written; held-out set of 80 drawn from Phase 0 traffic patterns kept separate.
- [ ] Embedding path benchmark on VM103 (sidecar at 2 CPUs): **p50 under 50 ms**.
- [ ] Arch-Router in request path **only if p95 under 1.2 s** on ~400-token inputs; otherwise it is configured offline-only (labeler/auditor). Record which mode shipped: __________
- [ ] Classifier accuracy on the held-out set: **exact level match ≥ 70%**.
- [ ] **True level 4 and 5 requests classified at level 3 or below: under 5%** (held-out set; also the soak success measure, §A).
- [ ] Classifier down/slow (>1.5 s) test: gateway falls back to the rules layer, logs the fallback, and keeps serving.

## E. Deployment / hardening acceptance (spec § Deployment)

- [ ] Only published port in the stack is `192.168.0.166:6075` (nginx); gateway and classifier publish nothing (`docker ps` / Portainer check).
- [ ] nginx allowlist verified live: requests from 192.168.0.188, 192.168.0.114, 192.168.0.28 succeed; a request from any other LAN address is denied.
- [ ] All three containers: read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, memory/CPU/PID limits set (classifier exactly 3 GB / 2 CPU); **no Docker socket mount anywhere**.
- [ ] All key files in the secrets directory are mode `600`; no key or hash value appears in the compose file, `.env`, image layers, registry, Drive, or any log (grep check with a canary key fragment, handled per runbook §3).
- [ ] v0.5 uses its **own** provider keys per environment, with provider-side limits set where offered — including an **OpenRouter credit limit** on the v0.5 key; production keys exist only in production.
- [ ] Proxmox snapshot of VM103 taken immediately before this phase's deployment (snapshot name recorded in the runbook execution log).
- [ ] Production v0.2.1 on :6070 untouched and still serving OpenClaw throughout.

## F. Phase 3 / Phase 5 gates (not pass/fail tests — owner gates)

- [ ] Premium alert levels set (Decision #2) **before** OpenClaw moves — Phase 3 Week 2 entry condition.
- [ ] OpenClaw shadow mode shows lower cost on the same traffic than v0.2.1 actual before OpenClaw moves.
- [ ] Cost / free-share targets reset from Phase 0 data before Phase 3.
- [ ] Phase 5: written go/no-go from Jordan after the soak report; v0.2.1 stays deployed but idle for 30 days after cutover, then is retired.
