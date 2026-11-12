# Sovereign Router

[![CI](https://github.com/jmason84/SovereignRouter/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jmason84/SovereignRouter/actions/workflows/ci.yml)

One OpenAI-compatible endpoint for OpenClaw, Hermes, n8n and TwentyCRM that sends each request to the cheapest model able to do it well — spec v0.5.3 (Drive: "Sovereign Router v0.5.3: Architecture, Specification and Implementation Plan").

**Status: LIVE — v0.6.0 on :6075 serves all four clients;** production v0.2.1 (:6070) remains untouched as rollback. **Registry: 22 models** (21 enabled; the one disabled entry is a deliberate duplicate route). Version history: [`CHANGELOG.md`](CHANGELOG.md). **The intent and the current routing rules live in [`docs/ROUTING-PRINCIPLES.md`](docs/ROUTING-PRINCIPLES.md)** — read that first; where any older document disagrees with it, it wins. This README is the map; that document is the law.

The router exists so that no single provider's failure — a dead API key, an exhausted credit balance, a rate-limit wall, an outage — can stall the fleet. When something dies, the router moves to the next rung in cost order and *says so* (a `lane_dead` alert, §Operations).

## Architecture

One gateway process on **VM103 "routellm" (192.168.0.166), port 6075**, behind nginx with a source allowlist. Production v0.2.1 on :6070 runs untouched alongside it as the rollback path.

| Component | What it is |
|---|---|
| Gateway (`src/gateway.py`, FastAPI) | The request pipeline (below). Single module, `create_app` factory; internals in [`src/README.md`](src/README.md) |
| Classifier sidecar (`classifier/`) | Assigns each request a difficulty level (1–5) and task class: rules layer → embedding nearest-match over a 150-example bank → optional local LLM layer. The gateway calls it with a 1.5 s timeout and falls back to its in-process rules on any failure — the sidecar is never a single point of failure |
| Sensitivity scan (`src/sensitivity.py`) | Local, in-gateway: gitleaks-style known-format credential detectors (hex hashes / UUIDs / SHAs explicitly excluded). A match flags the request sensitive; the match itself is never logged |
| nginx | TLS/ingress in front of :6075 with a LAN source allowlist |
| State dir (on VM103) | `registry.json` (the model registry — the gateway reads this copy, kept byte-identical to `src/registry.json` in this repo), `clients.json` (client policies + key **sha256 hashes only**), `keys/` (provider key files), `quota.db` (SQLite: usage ledger, provider rate-limit windows, alerts, killswitch), `routes.jsonl` (one route line per request — model, lane, level, cost, latency, escalation reasons; **never prompt or response text**) |

### Request pipeline

```
client key (hash-checked)
  → sensitivity scan
  → classify (level 1–5 + data class + task class)
  → sticky-session check (30-min window; see Routing policy)
  → candidate selection (client's lanes, premium gate, sensitive clearance, per-model rate-limit state)
  → governor check (daily target steering, emergency level)
  → execute cheapest-first, failing over with every skip reason recorded
  → output validation — failure escalates one level and retries
  → route line written; governor counters updated
```

Surfaces: `POST /v1/chat/completions` (JSON + SSE streaming; model aliases `auto`, `cheap`, `strong`, `premium`, `code`, `long`; header override `X-Sovereign-Level: 1..5`), `POST /v1/shadow` (decision-only — returns what *would* route where, no upstream call), `GET /v1/models`, `GET /health`, `GET /v1/quota`, `GET /v1/usage`, and admin-key-only `/admin/costs`, `/admin/routes`, `/admin/reload`, `/admin/killswitch`. Every response carries `X-Sovereign-Model / -Level / -Task / -Lane / -Cost-Estimate / -Escalated` headers. Error contract: **429** only when every candidate is rate-limited/failing, **413** only when no eligible model can ever fit the request, **403** for lanes the client may not use. **Budget never refuses a request.**

### Clients

| Client | Runs on | Lanes | Premium | Daily target (steer + alert, never a cap) |
|---|---|---|---|---|
| `hermes` | Hermes, VM112 (192.168.0.114) | private-free, paid, public-only-free | No | $1.25 |
| `openclaw` | OpenClaw, VM102 (192.168.0.188) | private-free, paid, premium, public-only-free | Yes | $5.00 |
| `n8n` | n8n, CT100 (192.168.0.28) | private-free, paid, public-only-free | No | $2.50 |
| `crm` | TwentyCRM, CT107 (192.168.0.92) | private-free, paid | No | $1.25 |

Each client authenticates with its own key; only the key's sha256 hash is stored. The `public-only-free` lane was added to hermes/n8n/openclaw on 2026-11-12 so the new free tiers are reachable; `crm` was deliberately left unchanged and still gets 403 on that lane.

## Routing policy

Full rules: `docs/ROUTING-PRINCIPLES.md`. The load-bearing ones:

### Lanes

| Lane | Meaning |
|---|---|
| `private-free` | Providers whose data posture is no-training. Price can be non-zero — Groq sits here on its paid Developer plan because the lane marks the *data posture*, not the price; its prices are carried so spend accounting is real |
| `public-only-free` | Genuinely $0 tiers whose terms let the provider use submitted data (Google's free tier is marked "used to improve our products: Yes" in Google's own pricing table). Eligible **only** for requests whose data class allows public handling — never for sensitive traffic |
| `paid` | Pay-as-you-go mid tier. Reached via the spillway (below), not by default |
| `premium` | The strongest models. Gated per client (`premium_allowed`), used at level 5, on explicit request, or last in the spillway |

### Order of selection

1. **Cheapest capable first.** Selection walks eligible models in strict cost order, filtered by the classified level, the client's lanes, sensitive clearance, and live per-model rate-limit state.
2. **Free and private lanes first; paid only on exhaustion** — the **paid spillway** (v0.5.5, 2026-11-07). It fires only when no free candidate is eligible or every free attempt has failed, and re-selects from the paid and premium lanes in strict cost order, GPT-5.5 last. Spillway serves are marked `free_exhausted_paid_fallthrough` in `routes.jsonl` so the cost is always attributable.
3. **Sensitive traffic in the spillway:** a sensitive request may cross to paid providers, but registry entries flagged `spillway_sensitive_excluded` are never selected for a sensitive turn **in any pass**. No provider is owner-cleared for sensitive data (Decision #3, `deploy/DECISIONS.md`); free *public* lanes are never eligible for sensitive data either way.
4. **Capability escalation is not exhaustion.** If a cheap model's answer fails output validation (empty, malformed tool arguments, unknown function, bad JSON), the request escalates one level and retries.
5. **Sticky sessions.** Inside an agent's tool loop, routing sticks to the serving model for a 30-minute sliding window (key: client + sha256 of first user message + system prompt), so prompt-cache pricing is preserved across the loop's turns.
6. **Model pins.** An explicit model in the request is honored over the cascade, subject to the client's lane/premium permissions.

### Money rules

- **No spending caps, ever** (owner ruling, 2026-10-31). Zero budget refusals is a hard target. Cost is controlled by routing and by the per-client daily targets above: they steer (above target, levels 1–3 prefer cheaper models) and alert, never block. An hourly runaway alarm watches for loops.
- **Premium alerts:** $20/month and >20 premium requests/day per client.
- **The $25/day emergency level warns — it never cuts** (owner ruling, 2026-11-09, superseding the 2026-11-01 hard-switch decision; `SOVEREIGN_EMERGENCY_WARN_ONLY=1` in production). The one time the hard switch tripped (2026-11-09 12:29 MST) it removed every paid rung at once and 429-walled both fleets. The hard-switch mechanism remains in code behind a flag that stays off unless the owner orders otherwise.
- **Ledger dollars are upper-bound estimates.** Route-log costs do not credit providers' cached-token discounts, so cache-heavy premium traffic is materially overstated (~12× on one audited day). Token volumes and counts are exact; **provider consoles are the source of truth for spend** (CHANGELOG, 2026-11-12; owner ruled no pricing-code change).

### Judged quality sample: OFF

Decision #5 (`deploy/DECISIONS.md`, accepted 2026-11-01, still off). The spec proposed letting a strong model judge a 2% sample of internal-class requests for answer quality, feeding the Phase 4 learning loop. It stays off because the judge would see prompt text — the one place in the design where prompt text leaves for evaluation. The learning loop therefore relies on outcome signals only (escalations, retries, tool errors — all already in `routes.jsonl`). It can be approved later without a rebuild; nobody has.

## Registry — the 22 models

`src/registry.json` is the source of truth (models, prices, contexts, per-provider rate limits seeded from each provider's *actual plan*). Prices are USD per 1M tokens, in / out.

| Model id | Provider | Lane | Context | $ in / out | Enabled | Notes |
|---|---|---|---|---|---|---|
| `groq/gpt-oss-20b` | groq | private-free | 131,072 | $0.075 / $0.30 | yes | |
| `groq/gpt-oss-120b` | groq | private-free | 131,072 | $0.15 / $0.60 | yes | |
| `groq/qwen3.8-27b` | groq | private-free | 131,072 | $0.80 / $4.00 | yes | Preview model on Groq |
| `openrouter/north-mini-code-free` | openrouter | private-free | 262,144 | $0 / $0 | yes | `data_collection=deny` enforced on every call |
| `openrouter/dots-3-note-preview-free` | openrouter | private-free | 524,288 | $0 / $0 | yes | The free lane's long-context option |
| `deepinfra/deepseek-v4-flash` | deepinfra | paid | 262,144 | $0.09 / $0.18 | yes | The spillway workhorse (direct pool) |
| `huggingface/deepseek-v4-flash` | huggingface | paid | 262,144 | $0.09 / $0.18 | yes | Same model, second (prepaid HF) billing pool; non-sensitive-only |
| `ollama/gpt-oss-120b` | ollama | paid | 131,072 | $0.15 / $0.60 | yes | |
| `novita/gpt-oss-120b` | novita | paid | 131,072 | $0.05 / $0.25 | yes | Cheapest gpt-oss-120b route; non-sensitive-only |
| `cerebras/gpt-oss-120b` | cerebras | paid | 65,536 | $0.35 / $0.75 | yes | Fastest route (~2,500 tok/s); trial-tier limits; non-sensitive-only |
| `siliconflow/glm-5.3` | siliconflow | paid | 1,049,000 | $0.70 / $2.20 | yes | Non-sensitive-only (data posture graded unclear) |
| `mistral/codestral` | mistral | paid | 262,144 | $0.30 / $0.90 | yes | Code specialist; non-sensitive-only |
| `mistral/mistral-large` | mistral | paid | 262,144 | $2.00 / $6.00 | yes | Non-sensitive-only |
| `meta/muse-spark-1.1` | meta | premium | 262,144 | $1.25 / $4.25 | yes | Standard tier only (see provider notes) |
| `deepseek/deepseek-v4-pro` | deepseek | premium | 1,000,000 | $1.32 / $3.96 | yes | Registry carries peak rates; off-peak is half |
| `zai/glm-5.3` | zai | premium | 1,000,000 | $1.40 / $4.40 | yes | |
| `premium/gpt-5.5` | openai | premium | 262,144 | $5.00 / $30.00 | yes | Carried from v0.2.1; registry price is a placeholder (see notes) |
| `siliconflow/deepseek-v4-pro` | siliconflow | premium | 1,049,000 | $1.00 / $1.50 | **no** | Duplicate route, deployed disabled; non-sensitive-only |
| `zai/glm-4.7-flash` | zai | public-only-free | 200,000 | $0 / $0 | yes | Free tier throttles hard under load |
| `google/gemini-3.5-flash-lite` | google | public-only-free | 1,048,576 | $0 / $0 | yes | Free tier trains on data → public-only permanently |
| `cloudflare/gpt-oss-120b` | cloudflare | public-only-free | 131,072 | $0 / $0 | yes | Draws on the shared Neuron pool (see notes) |
| `cloudflare/glm-4.7-flash` | cloudflare | public-only-free | 131,072 | $0 / $0 | yes | Tool-calling proven on this route |

Price caveats carried in the registry entries themselves: `novita` prices are seeded from Hugging Face's catalog listing, not yet verified against Novita's own price list; `premium/gpt-5.5` pricing is a placeholder carried from the v0.2.1 audit; `meta/muse-spark-1.1`'s context figure is a seed not yet confirmed against Meta's docs; DeepSeek direct is accounted at peak rates (off-peak is exactly half). Registry prices are seeds to correct on first console/invoice check — per-entry `notes` in `registry.json` carry the provenance.

### Provider notes (standing gotchas)

- **Cerebras: call direct, never via the Hugging Face passthrough.** The HF relay ignored reasoning controls, burned ~2.2k hidden reasoning tokens per turn, and leaked deliberation into a reply (2026-11-11 incident, `docs/incidents/2026-11-11-dots-3-reasoning-empty-responses.md`); the route was deleted 2026-11-12. The direct API honors `reasoning_effort` (registry pins `low`; `none`/off is not supported — it's a 400). The key is on trial terms ($5 credit, 1M tokens/day, ~65K context — Cerebras retired its permanent free tier in July 2026); funding the account lifts the limits and the context.
- **Mistral is paid, not free.** Its Free plan's API Keys page is locked ("Upgrade to activate your API keys") — the free-tier premise in older docs and roundups is stale. Live since 2026-11-12 on an owner-approved $10 pay-as-you-go activation, at published PAYG rates.
- **Google Gemini free tier trains on submitted data** ("used to improve our products: Yes", human review included) — public-only permanently, by posture not by price. The entry runs the Lite model: the flagship `gemini-3.8-flash` timed out at 60 s and 120 s on trivial probes, and `gemini-2.5-flash` is closed to new users (retires 2026-12-01).
- **Cloudflare Workers AI free pool is a bonus rung, not a lane:** 10,000 Neurons/day account-wide (resets 00:00 UTC) — roughly 150–270 typical gpt-oss-120b turns or ~700 small GLM turns a day at observed per-turn costs.
- **Meta Muse Spark:** STANDARD tier only — the contributor tier lets Meta train on traffic. Watch token budgets: 190 of 202 completion tokens were reasoning on a trivial prompt in probes. Team limits (3,000 RPM / 4M TPM) are documented but not enforced in the registry entry.
- **Z.ai GLM-4.7-Flash throttles** (HTTP 429 code 1305, "temporarily overloaded") on most probe attempts under load; the cascade skips past it. Its tool-calling is unverified (every probe attempt hit the throttle; the same model via Cloudflare has proven tool calls).
- **Reasoning models can eat the whole output budget.** Two OpenRouter free entries (`north-mini-code-free`, `dots-3-note-preview-free`) and the deleted HF Cerebras route all returned empty/reasoning-only answers until reasoning was disabled or damped via registry `extra_body`. Any new reasoning entry gets probed for this before it is enabled.
- **DeepSeek V4 Flash exists twice on purpose** — DeepInfra direct and via Hugging Face (backend pinned `:deepinfra`, billed to the prepaid HF account). Either pool can die without taking the model down; that redundancy is what carried the fleet through the 2026-11-09 DeepInfra credit outage.
- **NVIDIA NIM was probed 2026-11-12 and NOT added** — the free endpoint did not answer a trivial request within 90 s. Its key is installed on VM103 with the other provider keys if that ever changes.

## Operations

- **Health:** `GET /health` returns the gateway version (`0.6.0`) and component health; the alert chain treats the gateway as degraded when no `/admin/costs` poll has landed for 30 minutes.
- **Alert chain:** the gateway records alerts in `quota.db` (premium thresholds, runaway, `lane_dead`, emergency warn). An n8n workflow ("Sovereign Router — Alert Chain", export in `workflows/`, design in `phase0/alert_chain.md`) polls `GET /admin/costs` **every 15 minutes** and delivers new alerts to **Signal** (email fallback if Signal fails). A **daily all-clear at 08:10 America/Denver** sends the cost summary even when nothing fired — silence means the chain is broken, never that nothing happened.
- **Dead lanes announce themselves** (v0.5.7): the first upstream 401/402/403 — or daily-quota exhaustion — for a model each day raises a `lane_dead` alert. A 429 is congestion, not death, and does not raise it. This exists because DeepInfra's credits died at 04:48 on 2026-11-09 and burned silently for seven hours.
- **Keys:** provider keys live **only** as mode-600 files on VM103 owned by the gateway's uid (`10001:10001`) — a root-owned key file crash-loops the gateway at startup. Client keys exist as sha256 hashes in `clients.json`; raw keys (provider or client) never enter this repo, Drive, chat, or logs, and docs record key *names and fingerprints only*. Changing a key = overwrite the file + restart. There is **no standing rotation program** — the planned rotation was cancelled by the owner 2026-11-03 (`deploy/DECISIONS.md`).
- **Deploy / rollback:** procedure in `deploy/deploy-runbook.md`. Discipline: Proxmox snapshot of VM103 + verified backup before every deploy, tests green, deploy, then verify end-to-end *through the router itself* and loop until clean. Images are built on VM103, never pushed to a registry. Rollback is client-level first (point the client back at v0.2.1 :6070); a snapshot restore is the last resort.

## Testing & CI

- **Suite:** 248 pytest tests under `tests/`, mock upstreams only — no real provider calls. Coverage includes the registry premises (the suite loads the real `src/registry.json`, so a registry edit that breaks a premise fails before it can deploy), the gateway pipeline, classifier, sensitivity scan, governor, paid spillway, warn-only + lane-dead behavior, sticky/streaming, output validation, resilience, and shadow/alert paths.
- **Run it** (from the repo root): `src/.venv/bin/python -m pytest tests/`
- **CI:** `.github/workflows/ci.yml` runs the full suite on Python 3.12 on every push and pull request to `main` (badge at the top of this file).
- **Live harness:** `tests/e2e_live.py` boots a fake OpenAI-compatible upstream, the real classifier sidecar, and the real gateway over HTTP and exercises the full pipeline; exit code 0 only if every check passes. Run: `cd tests && ../src/.venv/bin/python e2e_live.py`.

## Layout

| Path | What |
|---|---|
| `src/` | The gateway (FastAPI): pipeline auth → scan → classify → sticky → select → governor → execute/failover → validate/escalate. Includes `/v1/chat/completions`, `/v1/shadow` (decision-only), `/admin/alerts` (pulled outbox), `/admin/costs`, killswitch. See `src/README.md`. |
| `classifier/` | Difficulty-classifier sidecar: rules → embedding nearest-match (150-example bank, 90% exact / 0% hard-task under-classification on the held-out set) → optional local LLM layer (Qwen2.5-1.5B-Instruct, Apache-2.0 — see `docs/LICENSE-REVIEW-arch-router.md` for why not Arch-Router). |
| `phase0/` | Phase 0 measurement tools for the v0.3.3 canary: usage analyzer, report, the one allowed flagged patch, alert-chain design. |
| `deploy/` | docker-compose stack (:6075 behind nginx, LAN allowlist), env templates, runbook, decisions record (with dated amendments), acceptance checklist. |
| `tests/` | `pytest` suite (248 tests) + `e2e_live.py`, a live harness that boots gateway + sidecar + a fake upstream over real HTTP. |
| `docs/` | **`ROUTING-PRINCIPLES.md` — the current intent and routing rules (start here).** `incidents/` — post-mortems. The build-out report, verification report, license review, convergence record and routing discussion are dated historical records, banner-marked as such. `deploy-checklist.md` and `risks.md` are the v0.5.3-era deployment checklist and risk register (Exec Ops, 2026-11-11), likewise banner-marked historical records. |
| `workflows/` | Export of the n8n Alert Chain workflow (the router's alert delivery), phone number redacted. |

## Key owner decisions (full record: `deploy/DECISIONS.md`; current rules: `docs/ROUTING-PRINCIPLES.md`)

The seven spec decisions were answered 2026-11-01 (defaults accepted) and amended since; the current state: no spending caps, ever · daily targets Hermes $1.25 / OpenClaw $5.00 / n8n $2.50 / TwentyCRM $1.25 (steer + alert only) · premium kept, alerts $20/mo and >20/day · no provider cleared for sensitive data · the $25/day emergency level is **warn-only** (2026-11-09 ruling) · judged quality sample **OFF** · Arch-Router rejected on license (Katanemo Community License) → Qwen2.5-1.5B-Instruct fallback · n8n joined Phase 3, last; TwentyCRM joined 2026-11-09 as the fourth client. Non-sensitive-only providers (registry flag, §Routing policy): SiliconFlow, Hugging Face, Cerebras (direct), Novita, Mistral. Public-only-free: Google Gemini, Z.ai GLM-4.7-Flash, Cloudflare Workers AI.

## Branch guide

Branch names follow one standard so any developer can read version, purpose,
and disposition off the name: `<lifecycle>/v<semver>-<descriptor>-<disposition>`
(`archive` = frozen history, `feature` = active work; disposition is `merged`,
`unmerged`, or `superseded`). Historical branches are kept as a permanent
archive — never deleted. Full record, including what worked and what didn't on
each branch: [`docs/BRANCH-ARCHIVE.md`](docs/BRANCH-ARCHIVE.md).

| Branch | What it is |
|---|---|
| `main` | Canonical tree, current release — the default branch |
| `archive/v0.5.3-ccr-build-superseded` | Original hosted-session v0.5.3 build (+ the deploy-checklist/risk-register commit) |
| `archive/v0.5.3-parallel-build-superseded` | The parallel v0.5.3 build that did not become canonical |
| `archive/v0.5.3-review-response-superseded` | v0.5.3 build plus its review-response commit |
| `archive/v0.5.4-improvements-merged` | Nov 5 improvement work — shipped as v0.5.4, merged in full |
| `archive/v0.5.7-improvements-unmerged` | Nov 11 Phases 0–5 improvement program — never landed |
| `archive/v0.5.7-optimization-unmerged` | v0.5.7 optimization pass — never landed |
| `feature/v0.5.9-ops-hardening` | Active work — do not touch without its owner's word |

## Project history — where things stand

This tree is the canonical convergence of two parallel builds of the same spec (2026-11-02): this workspace build (sidecar, Phase 0, deploy package, live E2E verification) plus the package build previously on this repo's `main` — preserved on branch **`archive/v0.5.3-parallel-build-superseded`** (formerly `alt-build-2026-11-01`) — whose shadow endpoint, pulled-alerts outbox and resilience tests were ported here. See `docs/GITHUB-REPO-COMPARISON.md`.

Milestones (detail in `CHANGELOG.md` and `docs/incidents/`): v0.5.3 spec and Phase 0 baseline (Oct 31–Nov 2) → v0.5 live on :6075 (Nov 2) → free-first order (v0.5.5) and the paid spillway + SiliconFlow (v0.5.6, Nov 7) → warn-only emergency level, dead-lane alerts, Hugging Face second pool (v0.5.7, Nov 9, out of that morning's rate-limit cascade) → Week-1 soak closed (Nov 10) → **v0.6.0 provider expansion (Nov 12): Cerebras direct, Novita, Z.ai Flash, then Gemini, Mistral and Cloudflare live, Meta Muse Spark enabled, the HF Cerebras route deleted — registry 15 → 22 models**, and this repo's branch organization completed the same day (default branch → `main`, six branches renamed to the archive standard, none deleted).

Open items, honestly stated: the Phase 4 learning loop has not started (its judged sample is off, Decision #5); Cerebras runs on trial terms until the owner funds it; the Week-2 soak gate awaits the owner's review; NVIDIA NIM is probed and parked.
