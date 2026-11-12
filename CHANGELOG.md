# Changelog

Gateway versions follow [SemVer](https://semver.org): `0.MINOR.PATCH` while the
router is pre-1.0. Behaviour changes bump MINOR-in-practice via the `0.5.x`
line; each deployed version gets an annotated tag `vX.Y.Z` on the commit that
was built into the image (see `docs/BRANCHING.md`).

## v0.5.9 — 2026-11-12 (operations review)
- **Stream empty-failover.** A streamed answer that ends with no text and no tool
  calls (empty or reasoning-only) now fails over to the next candidate and drops
  the sticky binding, instead of being served as a blank 200. Pre-content chunks
  (role header, reasoning deltas) are held up to 20 s / 400 chunks
  (`SOVEREIGN_PRECONTENT_HOLD_S`) so the failover is possible. Origin:
  2026-11-11 dots-3 incident.
- Scorecard `attributed_failures`: counts unusable answers / upstream errors per
  *failing* model (a model that is always failed-over-from never appeared before).
- nginx: `client_body_buffer_size 1m` (92% of fleet POSTs were spooling to tmpfs).
- Test fix: registry test updated for the Cerebras route disabled on 2026-11-11
  (main was red on it).

## v0.5.8 — 2026-11-11 (improvement plan)
- Emergency level defaults to warn-only; credential scan widened; malformed requests
  400 instead of 500; `no_eligible_model` vs `rate_limited`; `capacity_gap` and
  dead-lane visibility; model scorecard (`/admin/scorecard`, `tools/scorecard.py`);
  route-log rotation; WAL; throttled alert evaluation; failure step-up
  (`SOVEREIGN_FAILURE_STEPUP`, off by default); CI, pinned constraints.
  Details: `docs/IMPROVEMENT-PLAN-2026-11-11.md`.

## v0.5.7 — 2026-11-09 (deployed)
- Warn-only emergency level, `lane_dead` alerts. See
  `docs/incidents/2026-11-09-rate-limit-cascade.md`.
