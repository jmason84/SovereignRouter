# Cycle 02 — plan (written before build, per the Improvement Loop)

Date: 2026-11-19. Stacked on PR #14 (v0.9.2 candidate: F2 + F3).
Owner request 2026-11-19 19:46 MT: review everything, push a branch with
optimized settings and architecture. NOTHING in this cycle is deployed.

## Constraints (owner rulings that bind this cycle)
- J-1: pins stay bounded by the disabled-model and sensitive boundaries.
- J-2 (OPEN): automatic demotion stays observe-only.
- J-4: flaky-route disabling is proposal-only.
- Registry scores are measured or owner-ruled: no score or enabled flag is
  edited here. The 15 candidate entries were enabled by the owner 2026-11-17.

## Built (code, behaviour-neutral by default)
| ID | Change | Default effect |
|---|---|---|
| F4 | Per-client `model_allowlist` (ids or fnmatch globs) in `ClientPolicy`; enforced in `build_candidates` (every pass, spillway included), the `apply_pin` force-insert, and the 413 lane pre-check. | None: empty list = unrestricted. The owner opts in per client in clients.json. |
| F5-obs | `tool_health.py` + `GET /admin/tool-health`: per-model tool-loop error rate attributed to the model that made the call (window 200, trusted at 50 turns); reports the cap an enforcing version WOULD apply (>=25% -> 3, >=40% -> 2). | Observe-only. Selection never reads it (J-2). |
| F19 | `repeat_within_60s` no longer set on tool-loop continuations (identical fingerprint by construction; 74% of Hermes rows were flagged). | Telemetry only. |
| F21 | Stale rollback comment in `deploy/docker-compose.v05.yml` corrected. | Docs only. |

## Deliberately NOT done
- F1 (re-disable the 15 entries): contradicts the owner's 2026-11-17 ruling.
- F6 (loosen quality floors): would worsen tool-turn routing.
- Enforcing F5 / auto-demotion: J-2 is open; the new endpoint produces the
  evidence the owner needs to rule on it.
- F-10 (level-4 mis-classification): does not reproduce on current head
  (`classify_rules` assigns long-context / L4 above 60k tokens at 0.85
  confidence); no no-op fix written. The docs description should be corrected.
- Any live registry or client-config edit, and any deploy to VM103.

## Proposed settings — NOT applied, need the owner's go
Grounded in the 2026-11-19 Grok findings (Hermes tool-error rate 0/105
before Nov 3, ~8% after Nov 4, ~11% after Nov 12; free traffic share 83-85%):
1. Pin Hermes tool turns to a proven tool model (gemini-3.1-flash-lite
   measured 1.5% error; gpt-oss-120b 8-13%) via the existing pin map, or
   use F4 allowlist on Hermes once the paid route inside it is chosen.
2. Move Hermes' compression model off `dots-3-note-preview-free` (retires
   2026-12-31, 30-50% error class).
3. Drop `public-only-free` from the hermes/openclaw lane lists if the free
   share is not intended to be this high for tool traffic.
Note: the per-model error figures are inflated by the `has_tool_error`
3-message window double-count (see F-19); re-measure from
`/admin/tool-health` after deploy before judging.

## Test plan
`tests/test_cycle2_allowlist_toolhealth.py` (13 tests) + full suite
(baseline 763 on PR #14). Rollback if ever deployed: VM103 snapshot +
redeploy v0.9.1 image.
