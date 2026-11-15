# Sovereign Router — Improvement Loop

Established 2026-11-12 on Jordan Mason's order, as the standing operating mode for
this project (the same discipline he set for Hermes Direct Voice earlier the same
day). This document is the charter and the cycle log. Per-cycle detail lives in
`docs/cycles/`.

## The cycle

Every cycle runs the same seven stages, in order. No stage is skipped; a stage
that produces nothing produces a dated note saying so.

1. **Research each aspect.** Primary sources only: provider docs, terms, pricing
   pages and live catalogs; the gateway's own logs and route records; published
   benchmarks treated as claims, not facts. Every finding cites its source.
2. **Identify the best tools to implement.** For each aspect: adopt an existing
   tool/pattern, build, or skip — with the reasoning recorded. "Best" is judged
   against this router's actual constraints (single VM, OpenAI-compatible
   surface, four clients, free-first cost-ordered lanes, privacy lanes).
3. **Document the planned update before building it.** Tools, code changes, and
   every other inclusion (registry fields, tests, docs) written down in the
   cycle file. If it isn't in the plan, it isn't in the build.
4. **Develop the development tests.** Tests encode the intended behavior before
   (or alongside) the change — including a regression test for every failure
   being fixed.
5. **Run tests, clear tests.** The full suite must be green locally, then green
   in CI. Failures are diagnosed, not bypassed; a re-based test carries a dated
   comment saying what changed and why.
6. **Live tests.** Probes, pinned serves, shadow selections, production-shaped
   traffic. A change is not "done" at green tests — it is done when it has been
   observed working live, or the cycle file says exactly why not.
7. **Document everything that went wrong.** Every failure — research dead-ends,
   test failures, live incidents, wrong assumptions — gets a dated failure-log
   entry with evidence. Stage 7 of one cycle is the research input to stage 1
   of the next. Then the loop starts over.

## Standing rules inside the loop

- **Production gates still hold.** Snapshot + verified backups before deploys;
  consequential changes stop at the deploy gate for Jordan's explicit go. The
  loop authorizes research, design, tests, and local builds — not silent
  production changes.
- **No silent fixes.** If it broke, it is in the failure log, even if it was
  fixed in the same hour.
- **Prices and claims come from the provider's own source**, or the entry says
  they didn't. (Origin: Cycle 0, F-04.)
- **Pinned verification uses a unique conversation per pin.** Sticky-session
  reuse outranks pins by design. (Origin: Cycle 0, F-03.)
- **Registry files on VM103 must land owned so the gateway (uid 10001) can
  read them** — verify ownership/permissions before any restart. (Origin:
  Cycle 0, F-01; same family as the 2026-11-09 provider-key incident.)

## Cycle 0 — 2026-11-12 (retroactive capture)

The two registry expansions of 2026-11-12 (22 → 42 → 81 models, 72 enabled)
were run under this discipline before the charter existed. Their failures are
the loop's founding input:

| # | Failure | Evidence / disposition |
|---|---|---|
| F-01 | First phase-2 restart crash-looped the gateway ~2 min: the copied `state/registry.json` landed 640/root, gateway (uid 10001) PermissionError. | Fixed with chown 10001:10001 + 644; healthy on next restart. Standing rule above. |
| F-02 | `Registry.get()` matched id *or* upstream model string, first hit wins — a new OpenRouter entry's model string equaled a Google entry's id and would have hijacked its pins in production. | Caught by tests pre-deploy; file order fixed + guard test asserts `get(id)` exactness for all 81 entries. Generalization queued as Cycle 1 aspect B. |
| F-03 | First pinned-serve verification pass was invalid: identical prompts let sticky-session reuse hijack 13 serves onto one model. | Methodology fixed (unique conversation per pin); re-run clean. Standing rule above. |
| F-04 | SiliconFlow price seeds were wrong: seeded from an aggregator's endpoint listing at exactly half of SiliconFlow's own published prices ($0.70/$2.20 vs real $1.40/$4.40 for GLM-5.3). The "half of Z.ai direct" advantage never existed. | Corrected from the provider's own list; provenance conflicts now recorded in entry notes. Standing rule above; maintenance queued as Cycle 1 aspect C. |
| F-05 | `openrouter/north-mini-code-free` produced every sampled malformed-JSON article draft in the Launch AI Works Article Engine (16 reconciled failures; nemotron control 11/11 clean). Its registry quality scores let it win drafting-class work. | Quality corrected (chat 3→2, long-context 3→2; drafting classifies as chat at L2+, floor 3). Shadow tests prove exclusion from drafting, retention for simple chat/code. |
| F-06 | HF `qwen3.8-27b` (`:deepinfra`) probe billing did not reconcile with catalog price. | Open: verify against first HF invoice. Cycle 1 aspect C. |
| F-07 | Reasoning-token pathologies across providers: empty visible content when reasoning consumes small budgets (Aion 3.5-mini, NVIDIA Nemotron Lightning burned 3,172 reasoning tokens on a trivial prompt, OpenRouter Kimi K2.6 empty at 512, Muse Spark 1.3 empty at 2,048). | Entries seeded conservatively; two shipped disabled. Systemic fix = Cycle 1 aspect D. |
| F-08 | Provider flakiness masked as catalog truth: Google Gemma routes 500/slow/thought-leaking across repeated probes; NVIDIA catalog lists models that 404 for this account and one that hung 181s → 500; Cloudflare's shared neuron pool exhaustion made route-level verification impossible until the daily reset. | Flaky routes disabled with dated reasons; Cloudflare re-verification scheduled post-reset. Systemic handling = Cycle 1 aspect E. |
| F-09 | Hermes `write_file` stream stalls concentrated on `openrouter/dots-3-note-preview-free`: turns ended at exactly 2,048 output tokens, `finish=length`, tool calls discarded mid-payload (7 stalls 2026-11-12 evening, 3 more 2026-11-13; sessions at 93k–173k input tokens). First read as a model/provider stream defect. | Differential investigation 2026-11-13 (protocol §7): the gateway's `DEFAULT_MAX_TOKENS = 2048` binds every request whose client omits `max_tokens` — and Hermes omits it (its config carries no cap). Direct and through-gateway arms at 8,192 complete at 6,080 tokens; the unset replica through the gateway reproduces exactly 2,048 / `length`; a ~90k-context arm refutes the squeeze hypothesis. Provider exonerated (published max completion 460,800; its unset default does not bind). Classification **ours-config × interaction**. Fix proposed (raised default budget for tool-bearing requests + Hermes-side cap + ledger `finish_reason` capture), not applied. Secondary: dots-3 tool-argument degeneracy on fresh calls → R4 tooling evidence; entry stays enabled. **CLOSED 2026-11-13:** the gateway half shipped in **v0.8.1** (PR #5, merge `618a11c7b3a23dd983714ca209e38e2fd12a1c32`; tool-bearing default 8192 when the client omits `max_tokens`, plain chat keeps 2048) and was proven live at the deploy gate — the F-09 copy shape from an omitting client completed finish=stop / 8,001 tokens / 320 of 320 lines through the gateway, the plain-chat negative control still bound at exactly 2,048, and a client-sent 4096 bound exactly. The Hermes half (explicit 8192 stated via the base_url-matched provider `extra_body`) was already live from the build run. Still open from the original proposal: ledger `finish_reason` capture (the `truncated_tool_call` class) — carried in R3. |

| F-10 | Classifier sidecar fusion can override the rules layer's HARD size signal: the golden replay (2026-11-13, v0.9.0 stage 5) padded two synthetic prompts to ~65k tokens; the rules classifier forces `long-context` above 60k, but the fused live result was `chat` for both. | Mechanism read from `classifier.py`: when rules confidence < 0.8 the sidecar is consulted with only the first 1,500 chars of text, and the fusion takes the sidecar's `task_class` wholesale — the level gets a tie-rule guard, the class does not. The sidecar literally cannot see the size that triggered the hard signal. Practical blast radius is bounded (selection still enforces per-entry context fit against the true token estimate), but quality dimension and level floors for very large requests can be chosen by the wrong class. Classification **ours (fusion design)**. NOT fixed in v0.9.0 (found by its golden set; out of R4 scope) — proposed fix: hard signals (size > 60k, modality) pin the class before fusion, exactly as J-1 pinned the hard boundaries for explicit pins. Companion findings from the same replay: three sidecar/rules misclassifications on plain prompts (email draft -> code, press release -> infra, logic puzzle -> infra), recorded as known violations in `tests/golden/golden_set.json`. Status: OPEN, proposed for Cycle 1 aspect F/classifier follow-up. |

| F-11 | Hermes executive-ops session `20261113_225914_0c6b568c` (~71.5k tokens, 118 msgs) died 2026-11-13 16:26–16:33 MST: three consecutive router 502s, all attempts on `zai/glm-4.5-flash` (public-only-free, L4, internal), route signal `stream_aborted_after_commit:zai/glm-4.5-flash`, upstream latencies 121,290 / 121,229 / 151,710 ms; Hermes logged "upstream stream interrupted after partial output" on attempts 1/3, 2/3, 3/3. (Same entry caused the morning's exec-ops guardrail halt — a semantic tool defect, a different failure class.) | Differential investigation 2026-11-13 (route-health protocol): 10 probes — canonical-direct, gateway-shaped direct, and through-gateway arms × short / long (~2–2.9k-token) / tool shapes, plus a `glm-4.7-flash` sibling control. **No probe reproduced a stall or abort** — every stream completed (direct long: 59.7–65.8 s; through-gateway long: finish=stop at 108.0 s, ~12 s inside the flat timeout). The free tier is slow and high-variance: time-to-first-chunk 0.5–33.1 s on the subject entry, **60.7 s on the sibling**, and one through-gateway short draw burned its full 2,048 budget on hidden reasoning with zero visible content (F-07 family). Incident latencies cluster at the 120 s read bound (121.2 / 121.3 s), consistent with the flat `httpx.Timeout(120.0)` being the abort mechanism on a stall draw the probes didn't happen to hit. Gateway shaping EXONERATED (gateway-shaped direct ≡ canonical direct in outcomes). Classification: **provider** (free-tier stall/latency characteristics) × **ours-config** aggravation (flat 120 s timeout; selection had no failure memory, so client retries re-picked the same entry three times). **No registry action** — probes completed every shape on the entry, so no quality adjustment met the evidence standard; the entry stays enabled and unscored-down. **Fix built in v0.8.2** (branch `fix/v0.8.2-stream-resilience`, NOT yet deployed): streaming first-chunk (45 s) + inter-chunk stall (60 s) bounds replacing reliance on the flat read timeout; a 600 s per-entry cooldown on any streaming abort, persisted in the quota store and read by selection + sticky reuse (skip reason `cooldown_stream_abort`, only-candidate bypass annotated); `truncated_tool_call` attempt-ledger class (F-09's carried remainder). Watch item recorded in the package: the 45 s first-chunk bound is within ~1.4× of an observed healthy first-chunk time (33.1 s subject / 60.7 s sibling) — post-deploy, `stream_bound_timeout` signals on turns that would have completed are the evidence for raising it via env (no code change needed). |

| F-12 | **Phantom dollars:** the router ledger priced every input token at the full input rate — cached-token counts were never parsed (streaming path never; JSON path only the OpenAI-details shape), so cache-heavy sessions (300–530k contexts resent per turn, ~90%+ cache hits at ~1/10 price) ledgered as full-price misses. Nov 9 Day-8 "$28.23" and Nov 11 "$117 Hermes day" were ceilings, not spend; alerts fired on them. | Proven by the OWNER against his own provider consoles, 2026-11-11/12: DeepSeek 30-day actual **$9.34** vs ledger $109.32 for V4 Pro on Nov 11 alone (~12×); DeepInfra console $7.19/mo vs ledger $18.75; Groq $1.55/30d. Token volumes real; only dollars phantom. The owner then ruled the fix **"NOT NECESSARY — YOUR SOLVING A PROBLEM THAT ISNT AND WASN'T ACTUALLY A PROBLEM"** (2026-11-12) and ledger dollars were carried as labelled ceilings. On 2026-11-13 the new Grafana dashboard headlined the disproved premium MTD figure ($124.80 Hermes) and the owner challenged it ("I'M 100% CONVINCED THIS IS INCORRECT"), then **reversed the ruling verbatim: "ADJUST THE LEDGER TO THE ACTUAL AMOUNT"** (2026-11-13 ~17:48 MST). **Fix built in v0.8.3** (branch `fix/v0.8.3-true-cost-ledger`; **DEPLOYED 2026-11-13 21:33:54 MST** — PR #8 merged `a240bc4`, CI 43556738063 SUCCESS, image `sovereign-router:v0.8.3`, registry md5 `a915a4d1c0d5237534feba2d562358a0`; production proof: pinned DeepSeek V4 Pro session's recorded cost equalled the hand-computed cache-aware cost on every warm turn, and genuine fleet rows began recording cached_tokens > 0 within minutes; the Grafana premium panel now plots actuals since the cutover and the phantom MTD totals survive only as labelled static history): one shared usage parser (OpenAI details / DeepSeek hit / Anthropic reads incl. native fold / Google usageMetadata) on JSON + streaming paths; cached rates verified from primary sources for every priced entry that publishes one (7 set or re-verified incl. premium gpt-5.5 $0.50; SF V3.2 price correction per F-04 pattern); soak report gains token totals + free-share-by-tokens (Nov 12: 80.9% of tokens vs 74.4% of requests). Reconciliation probe (throwaway gateway, VM103): parsed cached_tokens matched DeepSeek's reported hits on 8/8 turns (99.8% warm), ledger cost == hand-computed on every turn. History NOT rewritten (upstream cache data was never stored): pre-deploy dollars stay ceilings; actuals only from the deploy. Residual known gap, recorded in the package: DeepSeek entries price at peak rates by deliberate convention — off-peak bills half (the probe's true deduction was exactly half its ledger figure); time-of-day pricing is the owner's call. |

Carried decisions (Jordan's, not the loop's): effective-cost accounting
(logging-only variant) — undecided; Decision #5 judged sample narrowed to
non-sensitive traffic — undecided; Cerebras PAYG funding before the trial
expires ≈ 2026-12-12; SiliconFlow $0-model probe (console check or explicit
probe OK); "NROUTER" — unidentified service, asked 2026-11-12.

## Cycle 1 — opened 2026-11-12

Aspects (each maps to a Cycle 0 failure cluster and/or an ADOPT item from the
2026-11-12 routing research brief):

- **A. Deploy safety.** Pre-flight assertions (file ownership/permissions,
  registry checksum agreement across the three copies), post-restart health
  gate, scripted rollback. (F-01)
- **B. Registry integrity invariants.** Generalize the collision guard:
  id↔model-string uniqueness classes, required price-source provenance,
  mandatory caveat assertions (e.g. NVIDIA trial terms), lane/flag coherence.
  (F-02, F-04)
- **C. Price truth maintenance.** Periodic re-verification of registry prices
  against provider sources; HF reconciliation (F-06); OpenRouter floating
  billed rates; drift alerts when a provider's published price moves.
- **D. Reasoning discipline.** Reasoning-effort as a routing dimension;
  empty-content detection with one bounded structural step-up (never
  self-reported confidence); per-entry reasoning behavior profiles from probe
  evidence. (F-07; brief: structural escalation)
- **E. Provider reliability.** Rate-limit ledger + pre-emptive demotion;
  error-classified retry + per-provider circuit breaker; evidence standard
  for auto-disabling flaky routes. (F-08; brief ADOPT items)
- **F. Verification harness.** Standing pin battery (unique conversations) as
  a repeatable tool; golden set (100–300 prompts) + shadow-before-promote as
  the gate for registry changes. (F-03; brief ADOPT items)
- **G. Privacy fail-closed audit.** Fault-injection tests proving sensitive
  traffic can never land on public-only-free or sensitive-excluded routes,
  run continuously rather than once. (brief ADOPT item)

Cycle 1 stage 1–3 output (research, tool choices, documented update plans)
goes to `docs/cycles/cycle-01-plan.md` for Jordan's review before any build.

## Cycle 1 — failure log additions, 2026-11-15 (v0.9.1)

Source: the 2026-11-15 alert diagnosis (the 00:00–00:30 MT batch: three
spend alerts, one level_mix, one sensitive alert, two Aion lane_dead) and
Group S of the 2026-11-15 full golden replay. The owner approved the full
fix set (F-13–F-17 as recommended, F-18 per the parent chat's framing)
2026-11-15 03:19 MST. All six ship together as patch release **v0.9.1**.

| # | Failure | Evidence / disposition |
|---|---|---|
| F-13 | **Spend alerts computed on blended dollars:** `premium_monthly_spend` and `provider_spend` summed month-to-date ledger figures that blend ~13 days of pre-v0.8.3 cache-blind CEILING with post-cutover actuals, so November paged phantom spend (OpenClaw premium $20.22 vs $0.0849 actual; DeepInfra $20.56 vs ≈$7.5–9 true; DeepSeek $150.05 vs ≈$9–11 true). | Diagnosis A1–A3 (census at the 2026-11-13 21:33:54 MST cutover epoch 1794630834, cross-checked against the exporter's split series — exact match). **In v0.9.1:** the governor computes both alerts (and the projected-month slow-leak figure, same blended root) on the ACTUALS window — month start clipped at the cutover epoch for November 2026; from Dec 1 the window is the plain month by construction. Levels UNCHANGED ($20 premium / $10 provider — on actuals they are sound tripwires). Any November alert that still fires annotates the excluded pre-cutover ceiling share in its message. |
| F-14 | **level_mix fired on noise:** a static 40% threshold with a 20-request sample floor paged at 57% on a 23-request morning when Hermes' trailing-7-day L4–5 share runs 62–92% (mean ≈78%) — the alert day was BELOW baseline. Levels track context size coherently (L4–5 median input 19k tokens vs 471 for L1–3); F-10 not implicated (112/113 from the rules layer). | Diagnosis A4 (full-file census, Denver days). **In v0.9.1:** minimum sample n ≥ 200 before level_mix can fire, and the threshold is baseline-relative — fire only when today's L4–5 share exceeds the trailing-7-day mean of daily shares by >10 points. NO classifier change. |
| F-15 | **sensitive_no_cleared_provider was guaranteed to fire daily:** its condition — "no owner-cleared candidate in the free-first set" — is permanently true (sensitive_cleared_providers is [] for all four clients; zero of 88 entries carry sensitive_cleared), while its wording warned of a paid fallthrough that did not occur (all 8 sensitive serves that day landed private-free, all 200, ≈$0). | Diagnosis A5 (gateway mechanism confirmed against source). **In v0.9.1:** the alert fires ONLY on outcomes — a sensitive request actually serving on a paid/premium lane, or finding no candidate at all. The empty-clearance condition is demoted to a daily `sensitive_posture_digest` alert record (sensitive serves by entry/lane/cost over the trailing 24h + cleared-set status), delivered by the existing alert chain alongside the daily all-clear. Usage rows now carry `data_class` so the digest aggregates from the ledger. |
| F-16 | **Two OpenRouter free routes sat INSIDE the sensitive wall:** `openrouter/dots-3-note-preview-free` and `openrouter/north-mini-code-free` hold private-free lane assignments, so the wall treated them as its trusted side — a 348,543-token sensitive context was served by dots-3 on 2026-11-15, and north-mini-code took two more sensitive serves — while 7 sibling OpenRouter entries are `spillway_sensitive_excluded` and ERNIE-free sits in public-only-free. Posture within one provider was inconsistent, with no recorded clearance decision. | Diagnosis A5. **In v0.9.1 (owner's ruling 2026-11-15):** both entries get `spillway_sensitive_excluded=true` with dated rationale notes — the surgical mechanism the siblings already use (dropped for sensitive turns in EVERY pass incl. pins; non-sensitive routing untouched; lane stays private-free). **Cleared-set consideration (the deliverable, decided NOT to populate in this pass):** populating `sensitive_cleared_providers` would create a designed trusted path for sensitive traffic, but no provider's data terms have been owner-reviewed for clearance, and inventing a cleared set inside a patch release would convert a lane default into an unreviewed data-posture decision. The wall's private-free default + the F-15 outcome alerts + the daily posture digest carry the posture until the owner reviews and names a cleared set as its own ruling. |
| F-17 | **Aion lane_dead wording asserted consumption that never happened, over a structurally unusable rung:** the account cap is 20,000 tokens/day while one ordinary fleet request estimates 67k+ — on 2026-11-15 the alert fired at ZERO usage ("TPD exhausted"), and the entries absorbed ~350 skip evaluations/day for ≈21 lifetime serves. | Diagnosis A6/A7. **In v0.9.1:** (a) the TPD lane-dead cause string states counted usage vs cap AND the triggering request's estimate; (b) BOTH Aion entries disabled with a dated reason note (owner's ruling), their 2026-11-15 tooling-battery scores preserved on the disabled entries; (c) the Nov 13 dataset — 165 attempts, 21 served, 144 failed (143× upstream 429 `congested` + 1 transient), every failure `would_demote`-stamped, pulled from the production attempt ledger — is Exhibit 1 of the J-2 review packet (`docs/cycles/j2-review-packet.md`). |
| F-18 | **The sensitivity tripwire missed prose-stated credential values:** detectors keyed on key/token shapes and tight `noun=value` assignments, so a database password, an OAuth client secret, a user password and a session-cookie value stated in plain sentences classified `internal` — and at internal, public-only-free is policy-eligible: golden g175 + g180 first-routed to `cloudflare/gpt-oss-120b` (public). Explicit shapes (sk- token, PEM block, AWS assignment) classified sensitive correctly. | Full golden replay 2026-11-15, Group S (items g175/g177/g179/g180; record in `docs/cycles/golden-full-run-2026-11-15.md`). Adjacent to F-16 but distinct: F-16 is which entries sit inside the wall; this is the wall's tripwire missing a credential dialect. **In v0.9.1:** tight prose-assignment patterns (credential noun + bounded filler gap + `is`/`:`/`=` separator + token-shaped value, with the existing placeholder/UUID/hex exclusions plus a value-shape gate) and `session` added to the assignment nouns; negative tests prove policy discussion, valueless mentions and redacted placeholders do NOT flip. The four golden items are the regression set. |
