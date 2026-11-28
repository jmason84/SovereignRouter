# Tiered routing — Jobs B, C, D (2026-11-27)

Owner go: Jordan, 2026-11-27 20:25 MST — "Do B C D" (Jobs B, C and D of the plan in the Drive doc *GROK - Sovereign Router Classification, Tiers & Self-Correction - 2026-11-27*). Job E (agent-side hooks) is **not** approved. Job A (the v0.10.4 deploy) waits on the owner opening its PR.

Policy reading (owner, 2026-11-27 17:26 MST): "best" means best for the request type and the tools or capability it needs. Per task class: best free model for that class, then best cheap, then premium only when the request requires it. The sensitivity wall stays hard.

Owner defaults for this round: free coder = `unorouter/codestral-latest`, cheap coder = `nrouter/qwen3-coder-480b-a35b`; no image-generation models yet.

## Job B — v0.10.5 (branch `feat/v0.10.5-tool-error-depth-cap`)

| Change | Why | Where |
|---|---|---|
| Tool-error detector parses JSON; `{"error": null}` and similar success payloads are not errors | False positives fed `tool_error_next_turn`, the F2 sticky break and the v0.10.2 ledger | `gateway._tool_text_has_error` |
| Max 6 upstream calls per turn; the free pass hands to the paid spillway with 2 left | 72h census: fallback depths up to 21 on congested free pools | `gateway._DepthBudget`, `MAX_UPSTREAM_ATTEMPTS`, `SOVEREIGN_MAX_UPSTREAM_ATTEMPTS` |
| Premium is never reached because free/paid pools are congested | Owner: premium only when the request requires it | `build_spillway_candidates` (`premium_not_required_congestion`) |

Behaviour change to watch after deploy: a turn whose free and paid pools are all congested now gets a 429 (Retry-After 60) instead of a premium rescue.

Rollback: redeploy the previous image tag (see the deploy record for the job).

## Job C — v0.10.6 (branch `feat/v0.10.6-tier-shadow`) — SHADOW ONLY

`src/tiering.py` ranks the live, already-walled candidate list:

```
tier (free < cheap < premium)  ->  owner-preferred specialist for the class
  ->  score (class quality; tool turns min(tooling, class) minus production tool-error penalty)
  ->  estimated price  ->  p50 latency
```

- free = $0 entry on a free lane; cheap = paid lane or priced private-free (e.g. Groq); premium = premium lane (present only when the live premium gate allows it).
- Tags: tools, vision (requires `supports_vision`), structured-output (recorded).
- Code class defaults: free `unorouter/codestral-latest`, cheap `nrouter/qwen3-coder-480b-a35b`.
- Output: `tier_shadow` on every route row, `tiered_shadow` on `/v1/shadow`. Live routing is not touched (tests prove identical upstream call sequences with the shadow on/off).

How to read it after deploy: the share of rows with `agree=false`, split by class and by `pick_tier` vs `live_tier`, is the size of the policy change. Job D's scorecard reports it nightly.

## Job D — nightly scorecard, proposals only (branch `feat/job-d-nightly-scorecard`)

`tools/scorecard.py` turns the router's own logs into a scorecard and a numbered list of **proposed** changes. It cannot change anything: the DB is opened read-only, the registry is only read, and output goes to `--out` or stdout. The schedule (`deploy/cron/scorecard.cron`) ships commented out. Thresholds are constants at the top of the tool:

| Proposal | Fires when |
|---|---|
| tool_quality | ≥20% tool errors on ≥30 judged turns |
| dead_entry | ≥10 entry_missing / account_dead attempts on an entry still enabled |
| pool_pressure | one pool is ≥50% of failed attempts |
| depth | max fallback depth ≥6 |
| cost | average or worst-day cost over the client's target |
| sensitivity | OpenClaw >50% flagged sensitive |
| tier_policy | tier shadow disagrees with live on ≥30% of ≥50 rows of a class |

First live run (read-only, 72h to 2026-11-27 20:43 MST): `reports/scorecard/scorecard-2026-11-27-72h.md`.
