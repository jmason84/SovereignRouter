# Sovereign Router v0.5

One OpenAI-compatible endpoint for OpenClaw, Hermes, n8n and TwentyCRM that sends each request to the cheapest model able to do it well — spec v0.5.3 (Drive: "Sovereign Router v0.5.3: Architecture, Specification and Implementation Plan").

**Status: LIVE — v0.5.7 on :6075 serves all four clients;** production v0.2.1 (:6070) remains untouched as rollback. **2026-11-11:** the Hugging Face Cerebras route is disabled (hidden-reasoning fault — see `docs/incidents/2026-11-11-dots-3-reasoning-empty-responses.md`); the HF Flash route and everything else are unchanged. **The intent and the current routing rules live in [`docs/ROUTING-PRINCIPLES.md`](docs/ROUTING-PRINCIPLES.md)** — read that first; older documents in this repo describe earlier states.

## Layout

| Path | What |
|---|---|
| `src/` | The gateway (FastAPI): pipeline auth → scan → classify → sticky → select → governor → execute/failover → validate/escalate. Includes `/v1/chat/completions`, `/v1/shadow` (decision-only), `/admin/alerts` (pulled outbox), `/admin/costs`, killswitch. See `src/README.md`. |
| `classifier/` | Difficulty-classifier sidecar: rules → embedding nearest-match (150-example bank, 90% exact / 0% hard-task under-classification on the held-out set) → optional local LLM layer (Qwen2.5-1.5B-Instruct, Apache-2.0 — see `docs/LICENSE-REVIEW-arch-router.md` for why not Arch-Router). |
| `phase0/` | Phase 0 measurement tools for the v0.3.3 canary: usage analyzer, report, the one allowed flagged patch, alert-chain design. |
| `deploy/` | docker-compose stack (:6075 behind nginx, LAN allowlist), env templates, runbook, decisions record (with dated amendments), acceptance checklist. |
| `tests/` | `pytest` suite (141 tests) + `e2e_live.py`, a live harness that boots gateway + sidecar + a fake upstream over real HTTP. |
| `docs/` | **`ROUTING-PRINCIPLES.md` — the current intent and routing rules (start here).** `incidents/` — post-mortems. The build-out report, verification report, license review, convergence record and routing discussion are dated historical records, banner-marked as such. `deploy-checklist.md` and `risks.md` are the v0.5.3-era deployment checklist and risk register (Exec Ops, 2026-11-11), likewise banner-marked historical records. |
| `workflows/` | Export of the n8n Alert Chain workflow (the router's alert delivery), phone number redacted. |

## Key owner decisions (full record: `deploy/DECISIONS.md`; current rules: `docs/ROUTING-PRINCIPLES.md`)

No spending caps, ever. Daily steer targets (alert-only): Hermes $1.25 / OpenClaw $5.00 / n8n $2.50 / TwentyCRM $1.25. Premium tier kept with $20/mo and >20/day alerts. The $25/day emergency level is **warn-only** (2026-11-09 ruling, replacing the 2026-11-01 hard switch): it alerts and never cuts the paid lanes. SiliconFlow and Hugging Face are non-sensitive-only. Judged quality sample OFF.

## History

This tree is the canonical convergence of two parallel builds of the same spec (2026-11-02): this workspace build (sidecar, Phase 0, deploy package, live E2E verification) plus the package build previously on this repo's `main` — preserved on branch **`alt-build-2026-11-01`** — whose shadow endpoint, pulled-alerts outbox and resilience tests were ported here. See `docs/GITHUB-REPO-COMPARISON.md`.
