# Sovereign Router v0.5

One OpenAI-compatible endpoint for OpenClaw, Hermes and n8n that sends each request to the cheapest model able to do it well — spec v0.5.3 (Drive: "Sovereign Router v0.5.3: Architecture, Specification and Implementation Plan").

**Status: built and end-to-end verified 2026-11-02 (100 unit tests + 31 live-HTTP E2E checks, two consecutive clean runs). NOT deployed.** Production v0.2.1 (:6070) and the v0.3.3 canary remain untouched as rollback. Deployment follows `deploy/deploy-runbook.md` and requires the owner's separate explicit go plus provider key rotation (Groq/Ollama).

## Layout

| Path | What |
|---|---|
| `src/` | The gateway (FastAPI): pipeline auth → scan → classify → sticky → select → governor → execute/failover → validate/escalate. Includes `/v1/chat/completions`, `/v1/shadow` (decision-only), `/admin/alerts` (pulled outbox), `/admin/costs`, killswitch. See `src/README.md`. |
| `classifier/` | Difficulty-classifier sidecar: rules → embedding nearest-match (150-example bank, 90% exact / 0% hard-task under-classification on the held-out set) → optional local LLM layer (Qwen2.5-1.5B-Instruct, Apache-2.0 — see `docs/LICENSE-REVIEW-arch-router.md` for why not Arch-Router). |
| `phase0/` | Phase 0 measurement tools for the v0.3.3 canary: usage analyzer, report, the one allowed flagged patch, alert-chain design. |
| `deploy/` | docker-compose stack (:6075 behind nginx, LAN allowlist), env templates, runbook, decisions record, acceptance checklist. |
| `tests/` | `pytest` suite (100 tests) + `e2e_live.py`, a live harness that boots gateway + sidecar + a fake upstream over real HTTP (31 checks). |
| `docs/` | Build-out report, verification report, license review, GitHub convergence record. |

## Key owner decisions (2026-11-01, `deploy/DECISIONS.md`)

No spending caps, ever. Emergency paid-lane switch ON at $25/day. Premium tier kept with $20/mo and >20/day alerts. No providers cleared for sensitive data (private lane only). Daily steer targets: Hermes $1 / OpenClaw $3 / n8n $0.50. Judged quality sample OFF.

## History

This tree is the canonical convergence of two parallel builds of the same spec (2026-11-02): this workspace build (sidecar, Phase 0, deploy package, live E2E verification) plus the package build previously on this repo's `main` — preserved on branch **`alt-build-2026-11-01`** — whose shadow endpoint, pulled-alerts outbox and resilience tests were ported here. See `docs/GITHUB-REPO-COMPARISON.md`.
