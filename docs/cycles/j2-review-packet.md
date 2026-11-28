# J-2 Review Packet — breaker / demotion enforcement authority

**Decision J-2 (OPEN, owner's):** whether the router's observe-only
failure memory (`would_demote` annotations in the v0.8.0 attempt ledger)
gains bounded enforcement authority — demoting or cooling entries on
classified failure evidence without a human in the loop. The v0.8.2
stream-abort cooldown is the one bounded exception already shipped
(single failure class, 600 s, only-candidate bypass). Everything else
remains observe-only until this review rules.

Review clock started 2026-11-13 (1–2 weeks of `would_demote` data);
review window ≈ 2026-11-20–27. This packet collects the exhibits.

## Exhibit 1 — Aion, 2026-11-13: 144 failed attempts, all would_demote

Attached 2026-11-15 under F-17(c). Source: production `attempt_events`
ledger on VM103, Denver day 2026-11-13, provider `aion` (both entries
share one account: 15 RPM / 20,000 tokens/day free allowance).

| model | outcome | status | failure class | attempts | would_demote |
|---|---|---|---|---|---|
| aion/aion-2.0 | failed | 429 | congested | 133 | 133 |
| aion/aion-3.5-mini | failed | 429 | congested | 10 | 10 |
| aion/aion-2.0 | failed | — | transient | 1 | 1 |
| aion/aion-2.0 | served | — | — | 21 | — |
| **total** | | | | **165** | **144 of 144 failures** |

By client: hermes 9 failed / 9 served · n8n 69 failed / 11 served ·
openclaw 66 failed / 1 served. Served usage that day: 17 usage rows,
3,626 tokens (aion-2.0) — the account's 20k/day token allowance was
never the binding constraint; the 15 RPM account ceiling was, and
selection kept offering the rung because observe-only policy logs
demotion evidence without acting on it.

**What it proves for J-2:** a provider can absorb 144 classified
failures in a day — every one stamped with the demotion the system
would have applied — while the cascade keeps paying the attempt cost
(latency, log noise, upstream hammering of a 15 RPM account). It is
the strongest single-day evidence yet for bounded demotion
enforcement: a per-entry cooldown on repeated `congested` failures
(the v0.8.2 stream-abort shape, generalized) would have silenced
~140 of these attempts with zero served-traffic impact (the 21
serves all landed before/around the failure mass, and every failed
turn was served by the next rung anyway).

**Disposition of the entries themselves:** both Aion entries were
DISABLED 2026-11-15 in v0.9.1 (F-17(b)) — the rung is structurally
unusable at fleet request sizes (20k tokens/day < one ordinary
request's estimate), so the exhibit stands as evidence about the
*policy*, not as a request to re-enable anything.
