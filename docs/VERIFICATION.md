# Sovereign Router v0.5 — End-to-End Verification Report

> **Historical record (2026-11-01/02).** This document describes its moment and is kept unedited as evidence. The current intent and routing rules live in `docs/ROUTING-PRINCIPLES.md`.

**Date:** 2026-11-01/02 (America/Denver) · **Directive:** Jordan — "test, verify, and correct any errors and then do it again until we have end to end verification."
**Result: VERIFIED.** Two consecutive clean full runs at the end: **88/88 unit tests + 27/27 live E2E checks, exit 0.**

## What "end to end" meant here

`tests/e2e_live.py` boots three real HTTP servers — a marker-driven fake OpenAI upstream, the classifier sidecar (real process), and the v0.5 gateway (real uvicorn) pointed at both, with a real state dir (registry with per-model base_url overrides, clients.json with real sha256 key hashes, provider key files). 27 checks over the wire: boot/health, emergency-switch state from decision #1, alert-chain degraded→ok heartbeat, auth matrix, routing levels (trivial / infra-easy / infra change+diagnosis), premium discipline (Hermes 403, OpenClaw premium lane), sensitive-lane confinement, streaming text, streaming tool-call reassembly, bad-tool-call escalation, empty-answer escalation, 3-step sticky tool loop, admin costs + spend recording, caller-scoped usage, killswitch stop/restore, and route-log hygiene (no prompt text, no secrets).

## Errors found and corrected during the loop

1. **Sidecar silent-exit bug (product bug, fixed in `classifier/sidecar.py`).** The `__main__` guard lived inside the stdlib fallback branch, so with FastAPI installed `python3 sidecar.py` defined the app and exited without serving — connection refused on `/classify`. This is exactly the failure mode the spec warns about (a component that looks built but serves nothing). Fixed: a top-level entry point now serves via uvicorn on the FastAPI path and via the stdlib server otherwise. Verified in BOTH modes after the fix.
2. **Validation-escalation dead end (product bug, fixed in `src/gateway.py`, both JSON and streaming paths).** Escalation required a candidate at quality+1. When the best lane-allowed model was already the ceiling for that task class (e.g. Hermes level-1 chat: best chat quality is 4, premium forbidden), a failed answer — empty response or malformed tool-call arguments — was served as-is. The streaming path didn't even log it. Fixed: if no quality+1 candidate exists, retry once on the next candidate at ≥ current quality (a different model rarely fails the same way); the streaming path now records the no-escalation signal too, matching the JSON path.
3. **Midnight-flaky governor tests (test bug, fixed in `tests/test_governor.py`).** Two tests seeded spend relative to `now` (300s / 7200s ago); run shortly after midnight local time, the seeds land yesterday and the governor's calendar-day windows (by design) correctly ignore them. `_spend` now clamps seeded timestamps into today. The governor's behavior was correct; the tests were time-fragile.
4. **Deploy contract mismatch (fixed earlier in the build, re-verified).** Compose/nginx/env were reconciled to the built images (gateway 6075 + `SOVEREIGN_STATE_DIR`/`SOVEREIGN_SIDECAR_URL`; classifier `PORT` 8080; nginx upstream `gateway:6075`; python-based healthchecks).
5. **Harness defects (test harness only, fixed in `tests/e2e_live.py`).** Case-sensitive header lookups against lowercased header dicts; a stream check that didn't reassemble argument fragments the way a real client does; an unguarded usage-chunk (`choices: []`) parse. Also one deliberate behavior confirmed and kept: `GET /v1/models` is public by design (public model names only, unit-tested contract, OpenAI clients probe it pre-auth) — all data/admin endpoints enforce keys.

## Regression gate (final, run twice)

| Gate | Result |
|---|---|
| Unit suite (`pytest`) | **88 passed** |
| Live E2E (`e2e_live.py`) | **27/27 passed, exit 0** |
| Classifier benchmark (TF-IDF floor, 80 held-out) | exact **90.0%** (bar ≥70%), within-one 96.2%, **L4/L5 under-classified 0.0%** (bar <5%), p50 ~36ms |
| Phase 0 analyzer | runs clean on v0.2.1-schema logs, Markdown + JSON |
| Phase 0 patch self-test | **ALL PASS** (patch still NOT applied anywhere) |
| Compose | YAML valid, 3 services, nginx upstream contract verified |
| Sidecar smoke | serves in stdlib AND FastAPI modes; "is docker running?" → L2/infra |

## Known limits (honest edges)

- Streaming response headers are emitted before validation completes, so on a streamed escalation the `X-Sovereign-*` headers describe the *initial* route; the route log carries the true final model/escalation. Agents should trust the log for accounting, headers for the common case.
- The benchmark ran on the TF-IDF fallback embedder (no sentence-transformers on this box). It clears both Phase 2 bars with margin, but the formal Phase 2 judgment should re-run with bge-small installed, per the spec.
- Docker image builds were not run (no Docker on this verification VM); images build on VM103 from the same Dockerfiles at deploy time, per the runbook.
- Premium pricing in the registry is still the marked placeholder ($5/$30 per 1M for gpt-5.5) — cost *estimates* only; confirm before Phase 3, as flagged.
- No live provider was called and nothing was deployed to VM103. The deploy gate state is unchanged: decisions answered, key rotation (Groq/Ollama, Jordan's provider-side action) and a separate explicit deploy go remain.
