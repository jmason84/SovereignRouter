# Release v0.9.0 — decision package (R4: measured tooling quality)

**Status: BUILD COMPLETE — awaiting Jordan's deploy-gate decisions (§7).**
Branch `feature/v0.9.0-quality-tooling`. Suite: **654 passed** (627 at
v0.8.1 → +27 net). Production remains v0.8.1 throughout; nothing in
this package has touched the live fleet's code or registry.

## 0. Renumber note (per the no-silent-fixes rule)

The NEXT plan labels this release **R4** and pencilled it as
"v0.10.0", with R3 (reasoning framework) as "v0.9.0". Jordan opened
R4 first (2026-11-13, after the tool-call incident forensics), so by
ship order this release is **v0.9.0**. R3 remains unbuilt; the plan's
R-labels are unchanged. Anyone reading the plan should map:
plan-R4 == shipped-v0.9.0; plan-R3 == unbuilt.

## 1. What this release is

Tonight's incident forensics found three production tool-call
defects on free models that selection had ranked by chat/code
quality, because no tooling dimension existed (gap 3 of the
2026-11-12 routing-spec analysis). R4 closes that gap with
*measurement*, per the handoff evaluations' rule: leaderboards
nominate, our own probes score — no external benchmark numbers were
imported as scores anywhere in this release.

## 2. The tooling dimension (stage 1)

- `quality` gains an optional sixth class, `tooling`, written ONLY
  by the battery (`tooling_source` provenance is a registry
  invariant: a score without `tooling-battery:` provenance fails
  the suite).
- Selection reads it through one helper used by every ranking site
  (candidate floor + sort, both quality-steering filters, shadow
  reporting, both validation-escalation paths): for a tool-bearing
  request, a measured entry ranks by its tooling score.
- **Fallback rule (the contract): an entry with NO measured tooling
  score ranks for tooling exactly as it did before the dimension
  existed** — its current effective quality input, unchanged. The
  dimension re-ranks only what it measured; 19 unmeasured entries
  are provably unaffected (a dedicated trap test pins this: the
  fallback is the entry's own `quality_for` input, not the map's
  minimum).
- The binary `supports_tools` gate is unchanged and applies first.
- **Invariant amendment, named:** `test_quality_maps_complete`
  previously required every quality class on every entry. Tooling
  is *optional by design* (absence is meaningful), so the invariant
  now requires the five hand-rated classes complete and tooling
  optional-with-provenance. Deliberate contract change, not a
  loosened check.

## 3. The battery (stage 2) and the fleet run (stage 3)

`tools/tooling_battery.py` productizes the pin battery: every
enabled entry is pinned (unique conversation per pin; J-1 bounds
respected) through five fixed synthetic tasks — T1 exact-args,
T2 160-line payload integrity, T3 parallel calls, T4 tool-result
follow-through, T5 error recovery (identical re-issue = 0, the GLM
failure mode) — plus V, envelope validity across the entry's own
calls. Scores derive mechanically: composite of available
components → 1..5, zero-envelope gate floors at 1, ≥3 components
required or the entry stays unmeasured. Full report:
`docs/cycles/tooling-battery-2026-11-13.md`.

**Fleet result: 54 of 73 enabled entries measured** (two runs; the
second with a corrected request shape — see §5). Distribution:
**5 × 50, 4 × 3, 2 × 1**. The three 4s (groq/qwen3.8-27b,
mistral/codestral, openrouter/llama-4-maverick) all lost exactly
the T2 large-payload component. The single 2 (zai/glm-4.6v-flash)
rests on thin evidence (3 components, incl. one failed gateway
validation) and is flagged provisional in its registry note.

**The 19 unmeasured** are reachability findings, not quality
verdicts: 11 OpenRouter free entries + dots-3/nemotron-ultra among
them (shared pool 429-congested in both runs), 3 Cloudflare (dead
account pool), 2 Aion (429s), 1 Ollama (402, the standing
account_dead fault), 2 upstreams that do not support tool calling
at all (§5). All remain governed by the fallback rule.

### The four incident models — battery vs production evidence

| entry | battery | production evidence | disposition |
|---|---|---|---|
| zai/glm-4.5-flash | **5** (T5 = 1.0) | identical-reissue fixation, guardrail halt | **TENSION, named in the entry's notes.** Fresh single-turn probes did not reproduce the in-session fixation. Both records stand; neither overwrites the other. |
| groq/gpt-oss-120b | **5** (T3 = 0.5) | 1-token degenerate completion breaking tool JSON | **TENSION, named.** The battery caught only a minor parallel-call imperfection; the degeneracy appears mid-agent-run, not on fresh turns. Also the calendar-incident model (below). |
| openrouter/nemotron-3-ultra-free | unmeasured | invalid-JSON tool envelope ×2 | **Neither corroborated nor contradicted** — pool congestion blocked every probe. Incident stands alone; priority re-probe target. |
| openrouter/dots-3-note-preview-free | unmeasured | degenerate fresh-call tool args (protocol §7) | Same as above. Entry stays enabled; F-09 already exonerated its route. |

### The fifth case — the calendar incident (harvested 2026-11-13)

The "calendar check failed again" report was harvested from VM112
logs during this build (separate evidence pass). Classification:
**primary = tool-side failure** (the Google shim did not pass the
time window to its backend; past-day queries filtered to a false
"0 events" — fixed on VM112 at 09:44/09:48 UTC by another actor,
verified by re-check); **secondary = model tool-call defect** on
the pinned voice model, groq/gpt-oss-120b: it twice emitted
camelCase Google-API argument names that schema validation
rejected (recovered in-turn), plus two stream validation errors —
the same tool-use quality class the battery measures, and further
reason this model's battery-5 must be read as "fresh synthetic
turns only" (the tension above). The router was innocent:
76/76 voice-window routes 200, no escalations, token counts
identical between Hermes and router records. No registry action
arises from this case beyond the tension note already carried.

## 4. Golden set + shadow replay (stage 5) — and F-10

`tests/golden/golden_set.json` (24 production-shaped synthetic
prompts, census mix in miniature) + `tools/golden_replay.py`
(decision-level verifiers + baseline diff with declared-change
manifest). Live baseline captured from production shadow and
committed (`tests/golden/baseline-2026-11-13.json`): **0 new
violations, 5 known** — the knowns are findings, recorded per-item
in the set rather than edited away:

- **F-10 (new failure-log entry): the classifier's sidecar fusion
  takes the sidecar's task_class wholesale and can override the
  rules layer's hard >60k-token long-context signal** — the
  sidecar sees only 1,500 chars, so it cannot perceive the size
  that triggered the signal. Both padded golden items classified
  `chat` live. Bounded blast radius (selection still enforces
  context fit on the true estimate) but real; fix proposed in the
  failure log (hard signals pin the class pre-fusion, the J-1
  pattern). **Not fixed in this release** — out of R4 scope.
- Three plain-prompt misclassifications (email draft → code,
  press release → infra, logic puzzle → infra).

**Aspect F2 scope honesty:** the plan's full golden set is ~180
prompts by census with deterministic verifiers per class plus a
live-replay judged sample. This release ships the 24-item starter
with decision-level verifiers and the diff machinery — the part
that runs free against shadow, nightly-style. The live-replay
sample remains report-only design (and Decision #5's judged
sample stays OFF, untouched). Growing the set toward 180 is
follow-up work, named here rather than silently truncated.

## 5. What the battery caught about ourselves (registry-truth fixes)

Two entries' `supports_tools` flags were simply wrong, proven by
verbatim direct upstream errors: **novita/llama-3.1-8b** ("model
features function calling not support") and
**deepinfra/mistral-small-24b** ("Tool calling is not supported
for model: …"). Both corrected to `false` with evidence in their
notes — they had been eligible for tool turns they could only fail.

And one battery self-fault, fixed mid-stage: run 1 sent
`temperature: 0.0`, which is a request fault on OpenAI's gpt-5.5
(rejects non-default temperatures verbatim) and Mistral (greedy
sampling requires top_p = 1). The shape was corrected, the
affected entries re-probed, and three controls reproduced their
scores exactly. (Production-side observation, not fixed here:
clients sending temperature 0 to gpt-5.5 through the gateway hit
the same wall today.)

## 6. Re-probe cadence (stage 4) — proposal, not activation

`tools/reprobe.sh` + `docs/cycles/weekly-reprobe.md`: a weekly
manual runner (proven by the stage-3 runs), patch reviewed as a
normal registry PR, golden replay in the same window. **No cron
was created.** The run also made the battery the `verified_until`
writer, closing aspect B's vacuous freshness gate for measured
entries — stamps written by this release expire **2026-12-13**.

## 7. Deploy gate — decisions for Jordan

1. **Merge + deploy v0.9.0** (PR, CI, then the standard gated
   deploy: snapshot, image build, three-copy registry md5 check,
   live smokes incl. a tooling-ranked shadow diff vs the committed
   baseline).
2. **Re-probe cadence activation**: approve a weekly scheduled
   run (cron/n8n — mechanism your choice), or keep it manual and
   accept that `verified_until` goes stale on 2026-12-13.
3. **F-10**: schedule the classifier-fusion fix (hard signals pin
   class pre-fusion) as its own small release, or carry it into
   R3's build.
4. Noted, no action requested: the two `supports_tools`
   corrections ride with this registry; the glm-4.6v-flash
   provisional 2 re-probes first in the weekly queue.

## 8. Rollback (verified, not asserted)

Code-only rollback is **NOT** safe with this registry: the v0.8.1
loader rejects the `tooling` quality key (`unknown task class`,
registry.py at `a4640aff`) and the `tooling_source` field. Rollback
= restore the pre-v0.9.0 registry from the deploy snapshot
**together with** the v0.8.1 image — the standard pair, called out
because the registry half is load-bearing this time.

## 9. Evidence index

- Battery report (both runs + merged table):
  `docs/cycles/tooling-battery-2026-11-13.md`
- Golden baseline: `tests/golden/baseline-2026-11-13.json` +
  `baseline-report.md` (0 new / 5 known violations)
- Attempt-ledger forensics for every unmeasured entry: VM103
  `state/quota.db` `attempt_events`, battery windows
  2026-11-13 16:55–18:25 UTC and the re-run window; raw battery
  JSON retained in VM103 `state/battery-runs/`.
- F-10: `docs/IMPROVEMENT-LOOP.md` failure table.
