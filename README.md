# Sovereign Router

[![CI](https://github.com/jmason84/SovereignRouter/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jmason84/SovereignRouter/actions/workflows/ci.yml)

One OpenAI-compatible endpoint for OpenClaw, Hermes, n8n and TwentyCRM that sends each request to the cheapest model able to do it well — spec v0.5.3 (Drive: "Sovereign Router v0.5.3: Architecture, Specification and Implementation Plan").

**Status: LIVE — v0.6.0 on :6075 serves all four clients;** production v0.2.1 (:6070) remains untouched as rollback. **Registry: 22 models** (21 enabled; the one disabled entry is a deliberate duplicate route). Version history: [`CHANGELOG.md`](CHANGELOG.md). **The intent and the current routing rules live in [`docs/ROUTING-PRINCIPLES.md`](docs/ROUTING-PRINCIPLES.md)** — read that first; older documents in this repo describe earlier states.

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

No spending caps, ever. Daily steer targets (alert-only): Hermes $1.25 / OpenClaw $5.00 / n8n $2.50 / TwentyCRM $1.25. Premium tier kept with $20/mo and >20/day alerts. The $25/day emergency level is **warn-only** (2026-11-09 ruling, replacing the 2026-11-01 hard switch): it alerts and never cuts the paid lanes. SiliconFlow, Hugging Face, Cerebras (direct) and Novita are non-sensitive-only. Google Gemini (free tier), Z.ai's free Flash, Mistral (free tier, staged) and Cloudflare Workers AI (staged) sit in the public-only-free lane — never eligible for sensitive traffic. Judged quality sample OFF.

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

## History

This tree is the canonical convergence of two parallel builds of the same spec (2026-11-02): this workspace build (sidecar, Phase 0, deploy package, live E2E verification) plus the package build previously on this repo's `main` — preserved on branch **`archive/v0.5.3-parallel-build-superseded`** (formerly `alt-build-2026-11-01`) — whose shadow endpoint, pulled-alerts outbox and resilience tests were ported here. See `docs/GITHUB-REPO-COMPARISON.md`.
