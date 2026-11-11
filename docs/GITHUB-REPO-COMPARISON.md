# Double-check: GitHub `jmason84/SovereignRouter` vs the local verified build

> **Historical record (2026-11-02).** This document describes its moment and is kept unedited as evidence. The current intent and routing rules live in `docs/ROUTING-PRINCIPLES.md`.

**Date:** 2026-11-02 · **Requested by:** Jordan ("Check our github.com account. I think we have some stuff on this in there. Please double check")

## What the GitHub repo is

`jmason84/SovereignRouter` — a **parallel, independent build of the same spec**, pushed by another session working from the Drive documents:

| Commit | When (MST) | What |
|---|---|---|
| `244c876` | Oct 31, 21:25 | "Sovereign Router v0.5.2 Phase 1 source tree" |
| `7e897c6` | Oct 31, 21:39 | "Add v0.5.2 plan review" (`docs/REVIEW.md` — a solid design review of the v0.5.2 plan) |
| `3e41a3d` | **Nov 1, 23:41** | "Implement v0.5.3 governor, pulled alerts, secret-scan changes, shadow mode; add resilience tests" |

The last commit landed **4 minutes before** Jordan handed Muse the v0.5.3 spec (23:45) — so two full builds of v0.5 now exist. Nothing in the repo is deployed; its README says so explicitly.

## Head to head

| Area | GitHub build (`3e41a3d`) | Local build (this workspace, verified Nov 2) |
|---|---|---|
| Layout | Python package `src/sovereign_router/` (14 modules, incl. `engine.py`, `selector.py`, `upstream.py`, `policy.py`) | Flat modules in `src/` (~2,900 lines) |
| Gateway pipeline | Complete Phase 1 pipeline | Complete Phase 1 pipeline |
| Tests | **66 passing** (their claim, mock providers) incl. `test_resilience.py` — concurrent bursts, 150 KB tool-heavy requests, cooldown recovery | **88 passing + 27 live-HTTP E2E checks**, run twice clean; no concurrency/resilience tests |
| Alerts | **Pulled outbox**: `GET /admin/alerts?after=<id>`, poll-freshness on `/health`, `POST /admin/daily-summary` | Heartbeat via `/admin/costs` poll freshness on `/health`; alert chain is the n8n-side doc |
| Shadow mode | **`POST /v1/shadow` endpoint** (decision + cost estimate, no upstream call, no ledger entry) | Shadow mode only as a Phase 3 runbook procedure for OpenClaw, no endpoint |
| Emergency switch | Env `SOVEREIGN_EMERGENCY_PAID_SWITCH_USD` or `POST /admin/emergency-switch`; **off until set** | Env `SOVEREIGN_EMERGENCY_SWITCH`/`_DAILY_USD`; **ON at $25/day** per Jordan's accepted decision #1 |
| Sensitive data | Scan modes enforce/log-only/off; falls back to a registry entry flagged `no_retention: true`, else **403** | Known-format scan; private lane only + alert; no 403 path |
| Classifier | Rules layer + sidecar **hook only** (1.2 s timeout) — no sidecar, no examples, no benchmark | Full sidecar + 150/80 example banks + benchmark (90% exact, 0% L4/L5 under) + Qwen fallback after license review |
| Registry example | 6 models — **no premium model, no Groq Qwen entry** | 8 models per spec table incl. `premium/gpt-5.5` |
| Container | Dockerfile, port **8080**, package install | Dockerfile port 6075 + **compose + nginx allowlist + runbook + .env** (full deploy package) |
| Phase 0 | Not present (their REVIEW.md recommends running it) | Analyzer + report data + flagged patch + alert-chain doc |
| Decisions | Own list of 6 implementation choices "please confirm"; REVIEW.md asks owner to set backstop + premium alert values — **the values Jordan set on Nov 1 ($25/day ON; $20/mo + >20/day) are not in that tree** | All 7 of Jordan's decisions applied; license review done (Arch-Router rejected, Qwen2.5-1.5B fallback) |
| Verification | Unit tests against mocks; "not verified: live providers, real traffic, soak" (their words) | Live E2E over real HTTP (fake upstream), 3 defects found + fixed, `docs/VERIFICATION.md` |

## Conflicts that must be resolved before any deploy

1. **Two source trees, one name.** Deploying either without converging invites the v0.3.3 patch-drift problem the spec exists to kill.
2. **Env/config contracts differ** (emergency-switch vars, port 8080 vs 6075+nginx, registry model IDs `groq-gpt-oss-120b` vs `groq/gpt-oss-120b` — slash vs dash naming differs too).
3. **Behavioral divergences:** sensitive-data 403-vs-serve-private; quality fallback ("best lower-quality serves" vs spec-strict selection); escalation cost accounting (they ledger escalated attempts' real cost).
4. **The GitHub tree doesn't carry Jordan's Nov 1 decisions** (its emergency switch sits off, premium alert values unset).

## Recommendation

Make the **local verified tree canonical** — it's the superset (sidecar, Phase 0, deploy package, decisions applied, license resolved, E2E-verified) — and **port the GitHub build's three genuine advantages** before pushing:

1. `POST /v1/shadow` endpoint (cleaner than the runbook-only shadow procedure).
2. Pulled-alerts outbox (`/admin/alerts`) — a better n8n contract than inferring alerts from `/admin/costs`.
3. The resilience test scenarios (concurrency, 150 KB requests, cooldown recovery) added to the local suite.

Then push the converged tree to `jmason84/SovereignRouter`, preserving the other build's history on a branch (e.g. `alt-build-2026-11-01`) — nothing is deleted, and the push satisfies the deploy discipline (no deploys from an unpushed tree). Convergence happens only on Jordan's say-so.

---

## Outcome (2026-11-02, Jordan approved convergence)

Executed as recommended: `/v1/shadow` endpoint, `/admin/alerts` pulled outbox (id-checkpointed), and the resilience scenarios (concurrency burst, ~150 KB tool-heavy request, failover+recovery, all-fail 429) were ported into this tree and adapted to its semantics. Verification after the port: **100 unit tests pass** (88 + 12 new) and the live E2E harness passes **31/31** (27 + 4 new shadow/alerts checks, run over real HTTP). This tree was then pushed to `main` as canonical; the parallel build's history is preserved on branch `alt-build-2026-11-01`. Files unique to the old tree that were superseded (the `src/sovereign_router` package, its pyproject/Dockerfile/examples, `tests/test_units.py`, `tests/test_v053.py`) were removed from `main`; its `docs/REVIEW.md` is kept — the design review is still valuable.

## Push record (2026-11-02 ~01:13–02:41 MST)

Jordan installed/selected the Meta Muse AI app and the push completed the same night. Discoveries along the way: the repo never had a `main` branch — the parallel build lived on `ccr-302db870-066n3h` (tip `3e41a3de`, the tree compared above) and a `v0.5.3` branch that carries a fourth commit (`84776fae`, "review response") implementing that build's own review items; both branches are untouched. Preservation branch `alt-build-2026-11-01` was created at `3e41a3de`, a new `main` branch was created from the same tip, the converged tree was pushed in 6 batch commits (root/docs, src ×2, classifier, deploy+phase0, tests), and the 20 superseded files (the `src/sovereign_router` package, root `pyproject.toml`/`Dockerfile`, `examples/*`, `tests/test_units.py`, `tests/test_v053.py`) were deleted from `main` in individual commits. **Verified byte-identical**: blob SHAs of README.md, src/gateway.py, tests/test_shadow_alerts.py and classifier/sidecar.py on `main` match the local repo's `git hash-object` exactly; `src/sovereign_router` no longer exists on `main`; branch tips confirmed by listing. One manual step remains for Jordan if wanted: the repo's *default* branch still points at `ccr-302db870-066n3h` — switch it to `main` under repo Settings → Branches so the canonical tree is what visitors land on.
