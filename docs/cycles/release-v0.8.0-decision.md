# Release v0.8.0 — Decision Package: Pool Ledger & Failure Memory (Cycle 1, R2)

- **Date:** 2026-11-12 (build run, evening MST)
- **Status:** **BUILT ON BRANCH — NOT DEPLOYED.** Branch
  `feature/v0.8.0-pool-ledger`, draft PR (see PR description). Full
  suite **582 passed** locally (566 baseline + 16 new); CI result
  recorded in the PR. Deploy waits for the owner's decision at the
  standard gate (snapshot + verified backup first).
- **Origin:** `docs/SOVEREIGN-ROUTER-NEXT-PLAN.md` §3 (release R2) and
  `docs/cycles/cycle-01-plan.md` aspect E. **Data layer, observe-only:
  this release changes NO selection behavior.** Enforcement authority
  is decision **J-2**, which remains open and is the follow-on
  decision this package exists to inform.

---

## 1. Sequencing — read first

This branch and v0.7.0 (`feature/v0.7.0-safety-integrity`, PR #3) both
branch from the same v0.6.1 main. **v0.7.0 merges first**, then this
release merges on top. Expected merge friction, named in advance:

- `src/gateway.py` streaming transport-error handler: v0.7.0 rewrites
  it (commit guard); this release adds the attempt recording as the
  handler's first statement. On merge, keep BOTH — the recording must
  stay ahead of the guard's post-commit return, or post-commit stream
  failures would bypass the ledger.
- `tests/test_paid_spillway.py` version-shape assertion: v0.7.0 sets
  "0.7.0"; this branch sets "0.8.0". Take "0.8.0".
- `CHANGELOG.md`: both branches insert a section above [0.6.1]; order
  [0.8.0] above [0.7.0] on merge.
- `src/registry.json` is touched ONLY by v0.7.0 in this pair (this
  release derives pools programmatically precisely so it has no
  registry dependency); v0.7.0's `quota_pool_id` fields and this
  release's derivation agree on the OpenRouter pools.

## 2. What changed — diff vs GitHub main (v0.6.1)

### `src/pools.py` (new)

1. **Pool model.** `pool_for(entry)` maps every registry entry to
   exactly one pool, derived from provider + model string:
   OpenRouter `:free` models **and** `openrouter/free-router`
   (NEW-01 inheritance) → `openrouter-free` (unit: requests);
   other OpenRouter → `openrouter-paid` (account class); Cloudflare →
   `cloudflare-workers-ai` (unit: **neurons**, budget 10,000/day);
   Google → per-model buckets; Groq → per-model token ceilings
   (unit: tokens); everything else → its own per-model pool.
2. **Failure taxonomy** (`classify_failure`): `account_dead`
   (401/403, and 402 — with the evaluation amendment applied FIRST:
   an OpenRouter 402 carrying `limit_source` = in-flight budget is
   **transient**, model scope, Retry-After honored), `congested`
   (429; pool scope only when the body carries a pool-exhaustion
   signal — Cloudflare error 4006 or OpenRouter per-day phrasing —
   otherwise model scope), `transient` (5xx / timeout / transport /
   unparseable body), `request_fault` (400/413/422, scope `request`),
   `entry_missing` (404, model scope). The classifier reads a bounded
   error-body excerpt for signal phrases only; no response text is
   stored anywhere.
3. **DemotionTracker (observe-only).** Consumes classified attempts
   and computes `would_demote` annotations {scope, target,
   failure_class, until, reset_source}. Cooldowns: account 1h, pool
   15m (or pool reset policy), model 60s (Retry-After when present),
   transient 30s, entry-missing 1h. **Reset policy is observed data:**
   OpenRouter's free pool demotes until the documented next 00:00 UTC
   reset; Cloudflare's pool demotes **until a successful probe**
   (`reset_source: observed_divergent`, until=None) because the
   documented reset demonstrably did not restore it on 2026-11-12/13.
   Neuron accounting: served Cloudflare attempts accumulate
   input+output tokens as the neuron estimate; crossing 10,000/day
   produces the pool annotation (class `congested` — never mislabeled
   account death; there is no paid overage on the free plan to
   confuse it with). Half-open bookkeeping: a demoted scope admits
   exactly one probe at its probe point; success closes it, failure
   re-demotes with a doubled (capped) cooldown. Tracker state is
   in-memory; the ledger is the durable record.

### `src/quota.py`

4. **Attempt ledger.** New append-only `attempt_events` table
   (CREATE TABLE IF NOT EXISTS — existing databases gain it on first
   boot, no migration) + `AttemptEvent` + `record_attempt` /
   `attempts_since` / `attempt_counts`. `usage_events` and every
   existing behavior are untouched: serves keep flowing as today.

### `src/gateway.py` (VERSION 0.6.1 → 0.8.0)

5. **`_record_attempt()`** — the single hook every attempt conclusion
   flows through: classification, tracker observation, ledger write,
   annotation merge onto the route record. Wrapped so a ledger error
   can never break serving (it folds into a route-record signal
   instead). Hooked at: JSON path transport error, HTTP error status,
   unparseable body, both validation-escalation continues, final
   serve; stream path non-200, transport error (first statement of
   the handler — see §1), tool-escalation continue, and
   `_finalize_stream` (covers text and tool serves).
   `app.state.demotions` holds the tracker; **nothing in selection
   reads it**.

### `src/telemetry.py`

6. `RouteRecord` gains `would_demote: list` (default empty; older
   readers ignore the new JSON key).

### Tests (566 → 582) — `tests/test_pool_ledger.py` (new, 16)

- Pool derivation: all 81 entries map to exactly one pool; the
  OpenRouter free pool is exactly the 14 expected members incl. the
  virtual router; Cloudflare unit = neurons; Google buckets distinct;
  Groq unit = tokens.
- Taxonomy: the full status map, and the OpenRouter in-flight 402
  subclassification (transient) vs a plain 402 (account death).
- **TEST-03 shape:** a bare 429 on an OpenRouter `:free` entry demotes
  that model only — pool siblings unaffected.
- **TEST-04 shape:** a per-day exhaustion 429 annotates the whole
  `openrouter-free` pool; expansion covers all 14 members incl.
  `openrouter/free-router`; the annotation persists until the
  documented daily reset.
- **TEST-05 shape:** Cloudflare serves accumulate neurons; crossing
  10,000 annotates the pool as `congested` with
  `reset_source: observed_divergent`, until=None.
- **TEST-09 shape:** half-open lifecycle at the ledger level —
  demote → probe admitted once at the probe point → success closes /
  failure re-demotes with doubled cooldown.
- **TEST-13 shape (Nov 9 census replay):** 369 repeated DeepInfra
  402s through the classifier + tracker: the annotation scopes to
  ACCOUNT from the **first** attempt and the active set never grows
  past it (convergence in 1, not 369).
- Request faults never demote. Store roundtrip. Gateway integration:
  a 429 failover writes failed+served attempt rows; a 402 writes an
  account-scoped annotation onto the route record; and the
  observe-only proof — a just-429'd, would-demoted model serves the
  next clean pinned request.

## 3. Behavior change in plain terms

**None for routing.** Every request is selected, served, and failed
over exactly as in v0.6.1 (plus whatever v0.7.0 changes once merged).
The differences are all in what the system REMEMBERS: every attempt
now leaves a classified ledger row, route records carry
`would_demote` annotations, and an operator (or the nightly review)
can finally answer "what has been failing, at what scope, since
when" from data instead of log archaeology. Storage cost: one small
SQLite row per upstream attempt; no text stored.

## 4. Where the code or plans needed judgment calls

1. **Scope of this build vs the NEXT plan's broader R2.** The plan's
   R2 also describes enforcement machinery (breakers skipping in
   `build_candidates`, demotion sort penalties, reservations). The
   brief for this build scopes it to the data layer, observe-only —
   that scoping governs, and the enforcement machinery is NOT built.
   It is the J-2 follow-on (§5).
2. **Pool derivation is programmatic** because this branch's registry
   (v0.6.1 main) predates explicit pool ids. v0.7.0 adds
   `quota_pool_id` to the registry for the OpenRouter pools; the two
   agree. A later release can switch derivation to the field once
   both are merged.
3. **Neuron accounting is an estimate.** Workers AI's true neuron
   formula is per-model; the ledger accumulates input+output tokens
   as the estimate and labels the pool annotation accordingly. The
   exhaustion annotation fires at the same 10,000 threshold the
   provider enforces; small estimation error shifts WHEN it fires,
   never what it means (free-pool congestion, no paid overage).
4. **A validation-escalated attempt records as `served`** (the
   upstream attempt succeeded; validation is a gateway-side
   judgment). Its units count toward pool accounting; its failure —
   if any — is already on the route record via escalation reasons.

## 5. Deploy gate (owner decisions)

Deploy is NOT part of this build. At the gate:

1. **Merge order**: v0.7.0 (PR #3) merges first; this release merges
   second (see §1 for the three named merge points).
2. **Deploy v0.8.0** after merge, under the standard gate (snapshot,
   .bak copies, preflight compare + health gate from the v0.7.0
   tool). The quota DB gains `attempt_events` automatically on first
   boot; rollback to v0.7.0/v0.6.1 code ignores the new table.
3. **J-2 — THE follow-on decision this release tees up:** may
   demotion ever ENFORCE (selection skipping demoted scopes until
   their reset/probe point)? This build only observes. Recommended
   path when J-2 is taken up: review 1–2 weeks of `would_demote`
   annotations against actual outcomes first, then decide scope by
   scope (account-scope enforcement is the least controversial —
   it is what the lane_dead alert already approximates by hand).
