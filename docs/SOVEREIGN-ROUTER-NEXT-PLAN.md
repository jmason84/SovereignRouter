# Sovereign Router — Next-Generation Implementation Plan

**Date:** 2026-11-12 (synthesized the night of 2026-11-12, America/Denver).
**Type:** PLAN ONLY — improvement-loop stages 1–3 work product. This document
authorizes nothing: no code, no registry edits, no deploys. Every release
below passes the loop's gates (research → tests → suite + CI green → live
proof → failure-log documentation) and stops at Jordan's deploy gate.
**Evidence base (read as one corpus):** the free-first routing research
handoff compilation v1 + v2 (§10) + v3 (§11) — one continuous body per
Jordan's 2026-11-12 ruling, never three competing documents; Jordan's
pasted summary review (the owner's own claims); the two evaluations of
that compilation (`docs/cycles/cycle-01-handoff-evaluation.md`,
`docs/cycles/cycle-01-handoff-v3-delta-evaluation.md`); the Cycle 1 plan
(`docs/cycles/cycle-01-plan.md`, aspects A–G, J-1 now RULED in its
Decisions log); the Route Health Investigation Protocol
(`docs/cycles/route-health-investigation-protocol.md`) with its two
differential diagnoses; the Improvement Loop charter; ROUTING-PRINCIPLES;
and the 2026-11-12 record (Jordan's restated routing spec, the Cloudflare
4006 diagnosis, the phantom-dollars correction). Where the corpus
contradicted itself, §6 of this plan records the contradiction and the
resolution. Code and registry facts were re-verified against
`src/gateway.py`, `src/quota.py`, `src/registry.py`, `src/registry.json`
(81 entries, 72 enabled, 17 providers) at synthesis time.

---

## 1. THE PRODUCT STATEMENT

The Sovereign Router is one OpenAI-compatible endpoint
(`192.168.0.166:6075`, VM103) that serves the whole fleet — OpenClaw,
Hermes, n8n, TwentyCRM — and sends every request to **the cheapest model
able to do it well**. The next generation is the same product with its
three proven weaknesses engineered out. In Jordan's own spec terms
(restatement, 2026-11-12 ~20:28 MST):

> Hop free tier to free tier — the second a free route 429s, jump to the
> NEXT free tier, until ALL free tiers are expended; only then paid.
> Priority within free is by classification of the request: a coding
> prompt gets the best free coding model; a tooling prompt gets the best
> free tooling model; when free is used up, the next best option is
> cheapest-best paid.

What "next generation" adds, point for point against that spec:

1. **Jump on 429 — and stay jumped.** Today the jump works inside one
   request and is forgotten by the next: a dead tier is re-attempted
   first on every subsequent request if it is still best-scored
   (Cloudflare took ~195 attempts after its pool died on 2026-11-12;
   DeepInfra took 369 during the 2026-11-09 credit death). The next
   router *remembers*: a failure is classified, scoped, and recorded,
   and the dead rung is skipped until its recovery condition — a reset,
   a Retry-After, a successful half-open probe — says otherwise.
2. **Pools understood as pools.** Free capacity is not per-model. Twenty-
   one OpenRouter entries draw on shared account pools (and only the
   `:free` variants share the free cap — §2a); six Cloudflare entries
   share one 10,000-neuron daily pool; Google's quotas are per-project
   with per-model buckets. The next router accounts in pools and in the
   units pools are actually denominated in — requests, tokens, *and
   neurons* — with windows that follow each provider's real reset rule,
   and reset times treated as **observed facts about this account**,
   never as documentation copied into code (Cloudflare documents a
   00:00 UTC reset; this account was still returning exhaustion errors
   at 00:01 and 03:25 UTC after it).
3. **Quality measured per task, including tooling.** "Best free model
   for this classification" is only as honest as the scores behind it.
   Today tool-calling quality does not exist as a measurement at all:
   tool support is a binary flag that defaults to true on 81 of 81
   entries. The next router measures tool use, coding, and the other
   classes with production-shaped probes of our own clients' real work,
   and every score carries its evidence — what was run, how many
   samples, when, and how much to trust it. External leaderboards
   nominate candidates; they never set scores.
4. **Reasoning under control.** Hidden reasoning tokens have emptied
   answers, leaked thought text into user-visible content, and burned
   budgets across at least six providers — each time handled as a
   one-off. The next router treats reasoning control as one framework:
   a per-entry profile of what control exists, what it defaults to,
   what minimum budget the entry needs, and which fields are *poison*
   for it — with reasoning tokens recorded on every route row so the
   burn is visible instead of inferred after an incident.
5. **Route health heals itself — and suspects us first.** A disable is
   a claim, and claims expire. Every disabled route is re-probed
   weekly; a disable with no fresh evidence in 30 days is flagged as
   stale, not left standing. And when a route misbehaves, the standing
   procedure is a differential diagnosis — canonical request vs.
   gateway-shaped request, working sibling as control — *before*
   blaming the provider. The night this plan was written, that
   procedure ran twice and both disables turned out to be partly our
   own fault (a control field our gateway silently dropped; a thinking
   control we never sent). The loop exists so that class of error gets
   found by routine, not by the owner's suspicion.
6. **Every failure feeds the loop.** Failed attempts are recorded, not
   just successful serves. Breaker decisions, demotions, probe results,
   price drift, and investigation verdicts all land in the same
   evidence base the next cycle's research starts from. Nothing about
   the fleet's money changes character: no spending caps ever, daily
   targets steer and alert only, the emergency level warns and never
   cuts, and sensitive data stays on cleared ground — with the pin
   override now bound to that wall by Jordan's ruling, so "disabled"
   and "not cleared" mean exactly that on every path into the router.

What the next router is *not*: a different gateway. The endpoint, the
clients, the classifier, the cost-ordered cascade, the SQLite state,
and the privacy lanes all stay. Every delta in §2 is an addition to
the running system, sequenced so each release is independently
shippable and independently reversible.

---

## 2. ARCHITECTURE DELTAS

Each delta names the evidence that forces it, the design, and what it
closes. Aspect letters refer to the Cycle 1 plan; criterion numbers
(TEST-xx from handoff v1 §7, FFC-xx from v2/v3 §§10.6/11.7, ADD-xx from
the two evaluations) are consolidated in §3 per release.

### 2a. Pool-aware quota and the attempt ledger (aspects B + E)

**Evidence.** `check_provider_limits` (quota.py:295) checks the
*evaluated entry's* limits dict against provider-aggregate usage —
per-entry declarations, provider-wide accounting. Consequences in the
live registry: OpenRouter's 21 entries carry two different limits
dicts (the shared free cap applies only when a free entry happens to
be evaluated); all Cloudflare entries declare `limits: {}` — the
10,000-neuron pool has no representation at all; day buckets are
server-local (`time.localtime()`, quota.py:107) though providers reset
on UTC days, Pacific days, and rolling windows. And the quota store
records only serves: failed attempts write no rows (gateway.py records
usage at L1142 estimated-first-candidate, L1306/L1550 on serve), so the
ledger holds *no failure memory* (NEW-03).

**Design.**
- **Pool declarations become registry data (aspect B).** Each entry
  gains `quota_pool_id` and `cost_mode`
  (`zero_price | free_allowance | promotional_credit | paid | local` —
  v2 §10.5, adopted). The initial taxonomy, from tonight's verified
  facts: `openrouter-free-variants` (the ten `:free` entries **plus**
  `openrouter/free-router`, which inherits this pool — NEW-01, ADD-10;
  it is never an independent pool); a distinct OpenRouter zero-price
  non-`:free` class (separate accounting if the CANDIDATE-03 sweep ever
  lands one — non-`:free` $0 does not share the `:free` cap);
  `cloudflare-neurons` (unit: neurons; reset: **observed**, §2b);
  `groq-org` (organization-level, header-fed); `google-project`
  (per-project enforcement, per-model buckets tracked for visibility —
  buckets are *not* modeled as independent daily pools);
  `aion-account` (15 RPM / 20k tokens/day allowance); `cerebras-trial`
  (`promotional_credit` with an expiry ≈2026-12-12, never counted as
  durable $0); per-provider paid pools for everything else;
  Vercel-style credit offers, if ever taken, are `promotional_credit`,
  never $0 capacity. B's per-provider limits-consistency invariant
  turns the OpenRouter split from an accident into this declared
  distinction.
- **Attempt ledger (aspect E).** An append-only `attempts` table in the
  existing SQLite QuotaStore — one row per upstream dispatch, success
  or failure, carrying `pool_id` and `outcome_class` (ADD-12:
  the ledger's attempt count must equal the gateway's actual HTTP call
  count — which is also the guard against hidden nested retries,
  FFC-37; the current stack is verified to have none).
- **Pool state** (merged field set — the review's six pool fields plus
  the handoff's breaker fields, per the v1 evaluation's merge ruling):
  `pool_id, provider, account_ref, credential_ref, quota_unit
  (requests | input_tokens | output_tokens | total_tokens | neurons),
  failure_scope, breaker_state, consecutive_failures,
  recent_failure_count, last_success, last_seen, retry_after_seconds,
  unavailable_until, reset_at, reset_rule`. Windows are UTC instants
  plus a per-pool reset rule (`calendar/UTC`,
  `calendar/America/Los_Angeles` via `zoneinfo`, rolling, or
  provider-reported) — NEW-04, FFC-18.
- **Reservations, scoped.** Atomic pre-dispatch reservations
  (`BEGIN IMMEDIATE`) exist *only* for deterministic, well-understood
  scarce limits (OpenRouter free RPD, the Cloudflare neuron proxy) —
  the delta evaluation's amendment to §10.5, not a universal dispatch
  gate (FFC-17 is scoped the same way).
- **Neurons as a unit.** Cloudflare's published per-model neuron
  weights (e.g. gpt-oss-120b 31,818/M input, 68,182/M output) feed a
  proxy counter per dispatch; if Workers AI responses report neuron
  usage at build-time verification, the reported figure wins. The pool
  also gets the protection Jordan was offered on diagnosis night: a
  **context ceiling** on Cloudflare entries so a handful of 20k-token
  agent turns cannot drain the day's pool before small turns use it —
  absorbed into this delta's design (§4).

**Closes:** gap 2 (shared pools invisible); NEW-01, NEW-03, NEW-04;
TEST-04, TEST-05; ADD-04, ADD-10, ADD-12; FFC-15, FFC-16, FFC-18,
FFC-34, FFC-41.

### 2b. Persistent failure memory: scoped breakers and demotion (aspect E)

**Evidence.** The gateway has failover but no memory: `note_lane_dead`
alerts (once per model per day) and changes no routing state. The
13,927-row census prices that amnesia: 369 attempts into dead
DeepInfra credit, 395 into a 429-ing dots-3, 270 into Groq
gpt-oss-120b at 429, 214 into north-mini-code, 169 into the exhausted
Cloudflare pool — and 105 DeepSeek V4 Pro 400s that are a
*request-fault* pattern, where punishing the provider would be wrong.
Upstream `Retry-After` and `x-ratelimit-*` headers are currently
ignored entirely.

**Design** (plan E.3, hardened by the evaluations):
- **Error taxonomy with scope.** `account_dead` (401/402/403),
  `pool_exhausted` (429 with pool/account evidence), `model_congested`
  (model-local 429), `entry_missing` (404 — ticks the *entry*, never
  the provider), `transient` (5xx/timeout/transport), `request_fault`
  (400/413/422 — never counts against provider health). Scope comes
  from the signal, never the status code alone (ADD-08): on
  OpenRouter, a 402 is subclassified by `error.metadata.limit_source`
  *before* any account-scope reaction — `openrouter_in_flight_budget`
  is a transient 402 that arrives with a Retry-After; treating it as
  account death is exactly the error TEST-06 guards.
  `output_invalid` stays with the validation layer (aspect D), not
  breaker accounting.
- **Circuit breaker per entry, provider rollup for `account_dead`.**
  Rolling 60 s window; open on ≥5 classified failures or >50% failure
  rate over ≥10 attempts; cooldown 30 s doubling to a 15 min cap;
  half-open admits exactly **one** probe (TEST-09); open entries skip
  in selection with reason `circuit_open` visible on the route row.
  Trusted `Retry-After` seeds `unavailable_until` (bounded against
  malformed values, FFC-20); Groq's remaining/reset headers feed the
  headroom signal. Pool-scoped exhaustion suppresses the pool's
  members while leaving the same provider's *paid* entries eligible
  (ADD-08, FFC-41).
- **Demotion near limits, never a dead end.** At 90% of a declared
  pool limit, member entries take a sort-order penalty so traffic
  bleeds to the next rung before the cliff (this is handoff D2's
  question, designed here; its *enforcement* is decision J-2). At
  100% the existing hard skip applies. Demotion is an ordering
  penalty only: if every candidate is demoted, the best demoted
  candidate still serves (ADD-07) — the breaker must never recreate
  the 2026-11-06 policy dead end. Sticky interaction is specified,
  not assumed (ADD-03): a circuit-open sticky model invalidates the
  stick with the reason on the route record; a demoted-but-open
  sticky model keeps its stick.
- **Bounded same-entry retry:** one retry, 250–500 ms jitter, only
  for transport errors/timeouts with no response received. A 429 is
  never retried on the same entry.
- **Observe first, enforce on ruling.** The breaker ships
  observe-only — decisions logged as signals on route rows, zero
  routing effect (FFC-39) — for a 7-day shadow week; enforcement
  requires decision J-2 (§4). A kill-switch env flag disables the
  breaker without a deploy.
- **Reset as observed state.** A pool whose documented reset passes
  while 429s continue stays in observed-unavailable/unknown state; it
  is never force-reopened by a calendar (FFC-34). For Cloudflare, the
  first successful serve timestamp and/or the account's own usage
  data (decision D4) resolves what the documentation could not.

**Closes:** gap 1 (no cross-request memory); TEST-03, TEST-06,
TEST-08, TEST-09, TEST-13 (Nov-9 replay: 369 dead attempts collapse
to a bounded handful *without increasing failed agent turns*);
ADD-03, ADD-07, ADD-08; FFC-25, FFC-26 (absorbed as ADD-04 fixtures),
FFC-39.

### 2c. The reasoning-control framework (aspect D; v0.6.1 is installment one)

**Evidence.** Tonight produced the third, fourth, and fifth instances
of one pattern. dots-3 was fixed in November with an OpenRouter
`reasoning: {enabled: false}` control. The Gemma diagnosis proved the
leakage was our shaping — Google's compat surface inlines thought
text, and `extra_body {"google": {"thinking_config":
{"thinking_level": "minimal"}}}` returns clean content (protocol arm
G9) — while the 500s and 24–96 s latency are the provider's, so the
disable stands reclassified. The Muse Spark 1.3 diagnosis found the
disable was `ours-config`: `reasoning_effort` is absent from the
gateway's `ALLOWED_FIELDS` (clients' effort is silently dropped), no
Meta entry sets it, and Meta's omitted-effort default is "high" on
every Spark version — with the control actually sent, 1.3's burn
collapses ~398 → 37 reasoning tokens. Around them: 49 of 54
`reasoning: true` entries carry no control shape at all; HF's relay
*ignores* controls (the deleted HF/Cerebras route); Cerebras honors
`reasoning_effort` but not below `low`; `openrouter/free-router`
*400s* if reasoning is disabled; Gemma *400s* on `reasoning_effort`
itself. And reasoning tokens are recorded nowhere —
`_usage_from_response` (gateway.py:237) never reads
`completion_tokens_details.reasoning_tokens` — so the burn is
invisible until it empties an answer (Cycle 0 F-07).

**Design.**
- **`reasoning_profile` per entry** (registry data, plan D.3.2):
  `{control: effort | enabled-flag | none-available | ignored-by-relay,
  effort_default, min_budget_tokens, think_leak, poison_fields: [],
  evidence: <dated probe ref>}`. The bare `reasoning` boolean remains
  as a derived compat field during migration. The **poison-field
  registry** is a first-class part of the profile: Gemma ×
  `reasoning_effort` → 400; free-router × reasoning-disable → 400;
  HF-relay entries → controls ignored (never route reasoning-sensitive
  work there expecting control). B's invariant battery asserts
  profile/control coherence (fixing B.1.5's flag-without-control and
  control-without-flag cases).
- **Telemetry first.** Route rows gain `reasoning_tokens` (null when
  the provider doesn't report it — never estimated; ADD-05), and a
  daily `reasoning_ratio` alert fires when an entry's
  reasoning:visible ratio exceeds ~5× over ≥20 serves.
- **Budget floor at call time:** effective `max_tokens` is at least
  the profile's `min_budget_tokens` (capped by context room), killing
  the known empty-content class at the source instead of paying a
  retry for it.
- **Retry symmetry:** the same-model doubled-budget retry (today keyed
  to `length_no_content` only, fired 107× in the census) extends to
  `empty_response` on profiled reasoning entries — once per candidate,
  never counted as escalation.
- **Effort policy v1 (bounded):** per-task-class effort translation
  at `upstream_body` time (chat L1–L2 → lowest available effort where
  a control exists; reasoning class → entry default; long-context →
  low). No selection change in v1; selection-time use of profiles is
  a v2 follow-up judged on the telemetry this delta starts collecting.
- **v0.6.1 (in flight, Release 0)** is this framework's first
  installment in its narrow form: the `reasoning_effort` allowlist
  fix plus effort profiles on the muse-spark entries, and the Gemma
  thinking shape — built on branch
  `fix/v0.6.1-effort-and-gemma-shaping`, deploy decision returning to
  Jordan separately. R3 generalizes it; R3 does not re-litigate it.

**Closes:** F-07 as a system (not per-model); ADD-05; FFC-37
(attempt-count integrity, shared with §2a); the D.4 retry/validation
matrix.

### 2d. The quality measurement program (aspects F1/F2 + the tooling dimension)

**Evidence.** `TASK_CLASSES = (chat, infra, code, reasoning,
long-context)` — no tooling class (registry.py). `supports_tools` is
true on 81/81 entries *by default*: asserted, never measured (§2.10 of
the v1 evaluation). `quality_for` falls back to an entry's minimum
known quality for an unrated class, so a tooling score cannot be
improvised from existing data. The standing counterexample is F-05:
north-mini-code's seeded scores let it win drafting-class work while
it produced 16/16 malformed-JSON article drafts. Meanwhile
`verified_until` is set on 0 of 81 entries and nothing writes it —
the freshness gate in selection is vacuous — and `nightly_scoring`
(telemetry.py:161) already computes per-model×class success and
proposes decrements, but has never been scheduled.

**Design.**
- **A sixth quality dimension: tooling.** Seeded *only* from measured
  evidence (FFC-40: tool support is earned by fixture-backed valid
  tool-call evidence, not a boolean default). Entries with no tooling
  measurement carry no tooling score — never an inherited one — and
  the selection behavior for unmeasured entries on tool turns is
  specified in the build (measured entries rank on the dimension;
  unmeasured entries are measurable-debt, surfaced in reports, not
  silently trusted). First evidence comes from the golden set's tool
  slice and the pin battery's tool fixtures; existing probe knowledge
  (Z.ai tool-calling probe-proven; dots-3 a mediocre tool user) seeds
  the first entries with their probe refs.
- **The measuring instruments are ours.** BFCL V4 and tau2-bench are
  methodology references (what to probe: tool selection, argument
  validity, schema adherence, multi-turn loops, recovery after a
  failed call); LiveCodeBench informs the coding slice (generation +
  self-repair with executable checks). Scores are set by
  production-shaped probes — Hermes/OpenClaw/n8n-shaped turns through
  our own gateway — per the review's rule and the project's standing
  production-shaped-tests rule. Leaderboards nominate; they never
  score (the v1 evaluation's ruling where the two texts differed).
- **The golden set + pin battery (aspect F).** ~180 synthetic prompts
  composed to the census mix (levels L4 59% / L3 29%; classes infra
  38% / long-context 36% / chat 18% / code 7% / reasoning 1%; ~20%
  tool turns, ~15% JSON-mode drafting — the F-05 class), each with a
  deterministic verifier. The pin battery (unique conversation per
  pin — the F-03 rule) runs changed-entries at deploy gates and full
  weekly. Nightly shadow replay diffs routing decisions against the
  pinned baseline; live replay of a rotating sample scores the
  verifiers. Registry-change gate = B invariants green + shadow diff
  clean (or manifest-declared) + battery on changed entries.
- **Score provenance.** Every quality score — all six dimensions —
  carries `{evidence, sample_size, measured_at, confidence}`.
  `nightly_scoring` is productized into the reporting feed: per-entry
  success by class, reasoning-ratio outliers (§2c), streamed-empty
  counts (§2f note), and decrement/**disable proposals** under the
  E.3.5 evidence standard (≥15% upstream-failure over ≥50 serves/7d
  with request-faults excluded; battery failure in 2 rounds ≥24 h
  apart; account-dead >24 h) — proposals only, never auto-applied
  (decision J-4).
- **`verified_until` gets a writer.** Battery results land in the
  quota store as probe records (automation never edits the registry);
  `is_verified` consults probe records when `verified_until` is
  unset. Freshness stops being vacuous without making the registry a
  write target for automation.

**Closes:** gap 3 (no tool-use quality dimension); TEST-01 (code-class
variant), TEST-02; FFC-40; the F-03/F-05 recurrence classes.

### 2e. The route-health loop, operationalized (protocol → machinery)

**Evidence.** The Route Health Investigation Protocol (2026-11-12,
Jordan's "loop back engineering" directive) is a document; its
cadence is currently human memory. Its first two runs overturned two
disables' root causes in one evening, and its third subject is already
queued: Gemini 3.8 Flash, which v3 §11.1 called "unprobed" while the
project's own registry note records 60 s and 120 s probe timeouts —
probe-failed, not unprobed (ADD-09).

**Design.**
- **Triggers wired to the alert chain:** any new disable, any
  anomalous probe pattern (empty content at `finish=length`, thought
  leakage, order-of-magnitude latency, a usage block that doesn't add
  up), any failed scheduled re-probe, and any client-visible incident
  traced to a route whose note blames the provider — each opens an
  investigation with the protocol's seven steps (capture the pair →
  differential, one variable at a time, working sibling as control →
  primary sources, absence of wider reports treated as evidence →
  classify `ours-shaping | ours-config | provider | terms-limits` →
  fix proposal or provider evidence pack → confirmatory re-probe →
  failure-log entry). A disable without a completed differential is
  *provisional* by definition.
- **Cadence as a job:** every disabled entry is re-probed weekly on
  its recorded failing shape plus its canonical shape (the battery's
  `--entries` mode over the disabled set). A passing re-probe opens a
  re-enable proposal through the normal gate; a reproducing failure
  renews the disable with fresh dated evidence; **30 days without a
  reconfirming re-probe marks the disable stale** and flags it in the
  cycle report. Gemma 31B/26B are already on this track (26B is the
  nearer candidate; the proven thinking shape ships in R0 so re-probes
  test the fixed configuration); Gemini 3.8 Flash joins it as a
  differential subject, not a catalog registration.
- **Registry notes cite investigations**, not bare symptoms — updated
  as part of the next gated registry change, never silently
  (protocol §7's four standing outputs: failure-log entry,
  note correction queued, fix proposal or evidence pack, next
  re-probe date).

**Closes:** ADD-09; the F-08 class (flakiness masked as catalog truth)
as a recurring process rather than a one-time cleanup.

### 2f. Pin hardening and stream integrity (aspect G + ADD-06)

**Evidence.** The pin force-insert (gateway.py:1023–1045) checks lane
authorization, key presence, the governor veto, and the sensitive
excluded flag — and nothing else: probe P-1 served a disabled entry
on plain *and* sensitive turns, and sensitive turns on public-only-free,
uncleared paid, and uncleared premium entries. Census: 0 organic
occurrences in 13,927 rows — a latent path, not a leak. Separately,
the streaming producer's transport-error handler has no
committed/finalized guard: a transport failure *after* text chunks
were yielded falls through to the next candidate and would append a
second model's output to a committed stream (v1 evaluation §2.6; v2
NEW-05 found the same edge independently). 77% of traffic streams.

**Design.**
- **J-1 implemented as ruled** (Jordan, 2026-11-12 ~22:20 MST): the
  force-insert admits a pinned entry only if it survives selection's
  safety subset for this request — `enabled`, provider key present,
  not sensitive-excluded on sensitive turns, not public-only-free on
  sensitive turns, cleared-or-private on sensitive turns, governor
  paid-off respected. Quality floor, context fit, and rate limits
  remain pin-overridable, deliberately — that overridability is what
  makes pins useful for testing and for the voice stack. Pins still
  never engage the spillway (ADD-02). The aspect-G fault-injection
  suite (client × data class × mechanism matrix, including sticky
  poisoning and the full pin matrix) encodes the *ruled* behavior —
  its wall/disabled cases are required-pass now, not expected-fail —
  and the nightly route-log auditor asserts zero sensitive-row
  violations against day-accurate registry snapshots, forever, not
  once. ROUTING-PRINCIPLES rule 3 gains the clarification in the doc,
  so the ruling lives in the principles, not in folklore.
- **Shadow/live parity:** with J-1 ruled, the shadow endpoint gains
  the same pin semantics as live (plan F.3.4), with a parity test
  over the golden set's pinned cases — shadow becomes a trustworthy
  pre-promotion oracle for pinned traffic.
- **Streaming commit guard:** a deterministic mocked-SSE reproducer
  test runs *first* (v3 §11.6's condition); then the producer's
  transport-error path checks the committed flag — post-commit, the
  stream terminates with a typed error and no second model is
  dispatched (ADD-06; FFC-19/FFC-38 are the same criterion). Pre-commit
  failover and buffered tool-call validation/escalation are unchanged.
  The 444-row `stream_text_log_only:empty_response` class stays
  log-only by design (post-commit text cannot be un-sent), but its
  nightly count becomes a standing report line (§2d) so the size of
  the un-escalatable class is always visible.

**Closes:** TEST-10, TEST-11; ADD-01, ADD-02, ADD-06; FFC-42
(normal-pass + spillway semantics regression lock).

### 2g. Catalog and price truth, and the candidate pipeline (aspect C)

**Evidence.** Prices are point-in-time seeds and provenance is prose:
F-04 (SiliconFlow seeded at exactly half its real price, from an
aggregator's listing) survived five days; five paid entries carry no
identifiable price source in their notes (plan B.1.2); F-06 (HF
qwen3.8 billing vs catalog) waits on an invoice because the gateway
discards provider-reported cost (§2h). v3 added two structural
lessons: a $0 catalog listing is not a chat-completions contract
(endpoint families now include decisions/rerank products at $0 —
§11.3), and nominal price is not predicted cash (fees, overage,
credits — §11.4). The rejected-leads list is also evidence: GitHub
Models retired, OpenCode free models 403 to external clients,
SambaNova "free" requires purchased credits, Mistral's Free mode is
not an API tier (§11.2) — catalog claims rot in both directions.

**Design.**
- **Structured truth fields (aspect B schema, aspect C maintenance):**
  `price_source, price_verified_at, price_confidence
  (verified | observed | seeded)` — backfilled once for all 81
  entries, the five unsourced ones re-researched, not copied forward
  — plus the handoff's adopted additions: `quota_source,
  usage_terms_url, trial_only, production_allowed, model_verified_at,
  endpoint_family` and the §11.3 capability-proof fields
  (`supports_tools_verified, supports_json_schema_verified,
  resolved_upstream_model, source_last_checked_at,
  owner_approved_for_prod`). `privacy_posture` is a *derived check*
  against the lane, never a free-text field that can drift from it.
  `trial_only`/`production_allowed` make the NVIDIA posture
  machine-checkable — which ruling they encode is decision D3 (§4);
  the fields land either way, and no entry is auto-disabled by them
  (J-4 discipline; FFC-21 is written against the field, not an
  outcome).
- **`tools/price_check.py`:** weekly fetchers for the machine-readable
  sources (OpenRouter, DeepInfra, Novita, SiliconFlow, Aion, Groq),
  a generated monthly page-check list for page-only providers,
  `price_drift` alerts through the existing chain. The checker never
  writes the registry; drift findings go through the aspect-A deploy
  gate like every other registry change (FFC-28). Aggregators
  (models.dev et al.) may serve as tripwires that prompt a
  provider-source check — never as the value written (the F-04 rule).
- **Real-zero-cash records:** each entry's price record distinguishes
  `nominal_model_price_usd` from `predicted_cash_spend_usd`, with fee
  components (tools/grounding surcharges, request fees, billable
  overage past a free allowance) either accounted or the route marked
  unverified-for-cash (FFC-35, FFC-36). A Workers Paid overage is
  never labeled $0; a promotional credit balance is never a $0 model.
- **Discovery with an endpoint-family gate (ADD-11):** the discovery
  job emits chat candidates only on contract evidence
  (`endpoint_family = chat_completions` plus capability proof);
  everything else is recorded as a non-chat candidate, disabled,
  regardless of a $0 price (FFC-32). Discovered routes are
  disabled until probed and approved — always (FFC-29).
- **The candidate pipeline** (stages: documented → terms/consent
  reviewed → synthetic public-prompt probe → registered disabled →
  battery → enable decision at Jordan's gate):
  **UnoRouter** (CANDIDATE-01) and **Kilo Gateway** (CANDIDATE-02)
  sit at stage 1–2: public-only posture, per-endpoint terms review,
  **no account creation and no consent acceptance without Jordan's
  explicit go** (FFC-23/FFC-24 are conditional criteria, written if
  the candidates reach probe stage). UnoRouter's per-model free shape
  (~1 req/min/model, if it verifies) is a genuinely different pool
  shape; Kilo's anonymous quota is not a production entitlement and
  its auto-free route is a privacy-crossing risk unless the resolved
  model is logged and pinned. Also in the queue: the OpenRouter
  non-`:free` $0 sweep (CANDIDATE-03 — account entitlement unknown;
  FFC-33: no auto-purchase, ever), Gemini 3.8 Flash (via §2e's
  differential track, `public-only-free` permanently if it ever
  enables), and the Cerebras pre-expiry price/terms check tied to
  the ≈2026-12-12 funding decision.

**Closes:** TEST-14 (commit-side secret scanning assigned to the
A/CI layer); ADD-11; FFC-22, FFC-27, FFC-28, FFC-29, FFC-30,
FFC-31 (recorded, no action), FFC-32, FFC-33, FFC-35, FFC-36.

### 2h. Spend and usage truth (aspects B + C, bounded by Jordan's ruling)

**Evidence.** Premium spend alerting is armed on 1 of 7 premium
entries (`premium/gpt-5.5`, $20) — four enabled premium entries,
including both Muse Spark routes, have no `spend_alert_usd` at all,
and provider alert levels mask the gap. `_usage_from_response`
discards provider-reported cost (`usage.cost` /
`usage.estimated_cost`) that OpenRouter and HF already send. And the
phantom-dollars correction stands as the permanent caution: the
gateway never reads providers' cached-token counts and premium prices
carry no cached rate, so **every ledger-dollar figure is a ceiling
estimate, not spend** (DeepSeek's 30-day console truth was $9.34
against a $109.32 single-day ledger figure).

**Design — and its explicit boundary.**
- **Premium alert coverage:** B's invariant (g) requires every
  premium entry to set `spend_alert_usd`; the backfill arms all
  seven. (Alerting only — targets steer and alert, never block;
  no-caps rule unchanged.)
- **Provider-reported cost capture:** route rows gain
  `provider_cost_usd` (nullable) when the upstream reports one. This
  turns F-06 from "wait for the invoice" into a nightly
  catalog-vs-billed comparison for HF and OpenRouter, and gives
  OpenRouter's floating endpoint rates an observed distribution
  instead of a guess. It is capture-for-reconciliation only.
- **What this delta is NOT:** Jordan ruled the cache-pricing
  correction **"NOT NECESSARY"** (2026-11-12 ~01:49 MST) — it is off
  the table and nothing here re-opens it. The narrower J-3(i)
  question — *logging-only* capture of cached/reasoning/
  provider-reported cost fields, with no displayed dollar figure
  changing — remains **undecided** and is listed as such in §4.
  `provider_cost_usd` and `reasoning_tokens` are designed to land
  under whichever way J-3(i) resolves: they are new evidence fields,
  not a repricing. Ledger dollars remain ceiling estimates; provider
  consoles remain the truth for money.

**Closes:** the B.1.6 alerting gap; F-06's evidence path.

---

## 3. RELEASE PLAN

Sequencing logic: safety and integrity first (they make every later
change cheaper to ship and safer to roll back); failure memory second
(Jordan's restated spec makes it the product's core gap, and its
observe-only half is safe to land early); reasoning third (it shares
one telemetry change with the cost capture); measurement fourth (the
golden set scores best once reasoning signals exist); catalog last
(it consumes the battery and the truth fields). This grouping departs
from the Cycle 1 plan's execution table in two deliberate ways, both
forced by rulings made after that table was written: **G moves into
R1** (J-1 is ruled — the only thing the pin fix waited on — and both
external texts rank pin safety first), and **E moves up to R2** in its
observe-first form (Jordan's re-sequencing of 2026-11-12), with
enforcement still last in effect because it cannot ship before J-2.
Every release: **entry gate** = this plan approved for that release +
tests written before/alongside code (loop stage 4); **exit gate** =
full suite green locally and in CI, live proof through the router
itself (stage 6), failure-log entries for everything that went wrong
(stage 7), and Jordan's explicit deploy go. Criterion lists are
references, not re-listings — the texts live in the evaluations.

### Release 0 — v0.6.1 fix package (IN FLIGHT)

- **Scope:** `reasoning_effort` added to the gateway allowlist; effort
  profiles (`extra_body`) on the muse-spark entries; Gemma entries
  gain the proven `thinking_config` extra_body shape. Branch
  `fix/v0.6.1-effort-and-gemma-shaping`. Built and tested separately;
  its deploy decision returns to Jordan with the package.
- **Dependencies:** none. (If R1 is ready first, R0 still deploys
  first — both touch gateway.py, and R0 is smaller.)
- **Effort:** S (build in flight).
- **Acceptance:** the protocol §6 fix proposals 1–2 as built; gateway
  tests for allowlist passthrough + per-entry effort; post-deploy pin
  battery on the Meta and Gemma entries; Muse Spark 1.3 re-enable
  decided on the re-probe evidence at the same gate (its economics at
  minimal effort are a different object than the one disabled).
  Gemma entries stay disabled (provider component stands) and enter
  §2e's weekly re-probe with the fixed shape.
- **Rollback:** pre-deploy snapshot + registry `.bak` per standing
  discipline; the change is one allowlist entry + registry extra_body
  fields — revert is a registry restore + restart.

### Release 1 — Safety & integrity (aspects A + B + G + stream guard) → v0.7.0

- **Scope:** the deploy tool (`tools/deploy_registry.py`: preflight /
  deploy / rollback / verify, `install(1)` transfers, /health +
  model-count gate, automatic rollback, F-01 as a drill regression);
  `Registry.get()` two-pass hardening + loader duplicate checks; the
  invariant battery `tests/test_registry_invariants.py` (collision
  classes, price posture by lane with the Groq whitelist, caveat
  table, lane/flag coherence, per-provider pool declarations from
  §2a, dated disable reasons); structured fields backfilled for all
  81 entries (`price_source/verified_at/confidence`, `quota_pool_id`,
  `cost_mode`, terms/endpoint fields from §2g, premium
  `spend_alert_usd` armed on all 7); the J-1 pin hardening (gateway)
  + the aspect-G fault-injection suite + nightly route-log auditor;
  shadow/live pin parity; the streaming commit guard (reproducer
  test first); ROUTING-PRINCIPLES rule-3 clarification.
- **Dependencies:** none beyond R0 ordering.
- **Effort:** L (A: S–M; B: M; G: S–M; guard: S).
- **Acceptance:** TEST-10, TEST-14, ADD-01, ADD-02, ADD-06, ADD-11
  (schema side), FFC-19, FFC-21 (written field-first, passes under
  any D3 ruling), FFC-27, FFC-38, FFC-42; A.4's refusal cases;
  G.4's mutation check; the F-02 pair pinned by id, live; a timed
  rollback drill on a no-op diff before the tool is trusted with a
  real change. Exit includes: /health registry count unchanged (81),
  24 h serve-distribution watch after the `get()` change.
- **Rollback:** the release *is* the rollback machinery — its own
  deploy uses snapshot + `.bak` + manual restore (the drill proves
  the scripted path for every later release). Pin hardening reverts
  as a gateway image rollback to v0.6.1; registry field backfill is
  additive (old code ignores unknown fields).

### Release 2 — Pool ledger & failure memory (aspect E) → v0.8.0

- **Scope:** attempt ledger + pool-state tables in the QuotaStore
  (§2a); error taxonomy with `limit_source` subclassification;
  per-entry circuit breaker + account-dead provider rollup;
  Retry-After and rate-limit-header ingestion; 90% demotion as a
  sort penalty; scoped atomic reservations (OpenRouter free RPD,
  Cloudflare neuron proxy); the Cloudflare neuron-proxy counter,
  observed-reset pool state machine, and entry context ceilings;
  bounded transport-only retry; breaker kill-switch flag. Ships
  **observe-only**; enforcement flips only on decision J-2, as a
  configuration change at its own gate, not a new build.
- **Dependencies:** R1 (pool declarations, deploy tool).
- **Effort:** L.
- **Acceptance:** TEST-03, TEST-04, TEST-05, TEST-06, TEST-08,
  TEST-09, TEST-13 (Nov-9 replay, with the handoff's added clause:
  no increase in failed agent turns), ADD-03, ADD-04, ADD-07,
  ADD-08, ADD-10, ADD-12, FFC-15, FFC-16, FFC-17 (scoped form),
  FFC-18, FFC-20, FFC-25, FFC-34, FFC-39, FFC-41. Exit gate 1 =
  7-day observe week reviewed (would-have-opened events vs. actual
  outcomes; thresholds calibrated to ~2,000–2,700 rows/day). Exit
  gate 2 (enforcement, only if J-2 rules so) = the next natural
  provider incident bounded, alert still firing once, cascade
  serving — documented either way.
- **Rollback:** kill-switch env flag reverts selection to
  pre-breaker behavior without a deploy; ledger/pool tables are
  additive and ignored by older code; registry context ceilings
  revert via the deploy tool.

### Release 3 — Reasoning framework & telemetry (aspect D) → v0.9.0

- **Scope:** `reasoning_tokens` + `provider_cost_usd` capture in
  `_usage_from_response` and RouteRecord (§2c/§2h — one shared
  telemetry change, landing here so R5's reconciliation has data);
  `reasoning_profile` schema + seeding for the known set + one probe
  round (budget ladder) for the remaining reasoning entries; budget
  floor in `upstream_body`; `empty_response` retry symmetry; effort
  policy v1 per task class; `reasoning_ratio` daily alert.
- **Dependencies:** R1 (profile fields ride B's schema discipline);
  R2 not required.
- **Effort:** M–L.
- **Acceptance:** ADD-05, FFC-37 (with §2a), the D.4 validation/retry
  matrix (every failure code × profiled/unprofiled/non-reasoning),
  F-07 fixtures (a mock that burns its budget at 512 and answers at
  4096 must serve via the floor without escalation). Exit = pin
  battery with deliberately small budgets against the known
  pathological entries (Aion 3.5-mini, NVIDIA Lightning, Cerebras
  pair) returning content via the floor, plus a 7-day before/after
  on empty-content escalation counts.
- **Rollback:** profiles are registry data (revert via deploy tool);
  telemetry fields are additive; effort policy is config — one flag
  restores entry-default behavior.

### Release 4 — Quality measurement program (aspects F1 + F2 + tooling dimension) → v0.10.0

- **Scope:** `tools/pin_battery.py` productized (unique conversation
  per pin; changed-only / full / entries modes) and wired as the
  deploy tool's battery + the §2e re-probe engine; probe records in
  the quota store making `verified_until`/`is_verified` real (§2d);
  the golden set (~180 prompts, census-mixed, deterministic
  verifiers) + nightly shadow replay with manifest diffs + rotating
  live sample; the tooling quality dimension seeded from battery +
  golden evidence; coding scores re-based on the golden coding slice;
  `nightly_scoring` productized into the reporting/proposal feed
  (quality decrements + E.3.5 disable proposals — proposals only).
- **Dependencies:** R3 (reasoning signals for scoring), R1 (battery
  is A's gate instrument — a minimal battery mode ships in R1; R4
  productizes it).
- **Effort:** L (F1: S–M; F2: M; tooling seeding: M).
- **Acceptance:** TEST-01 (code-class variant), TEST-02, TEST-12
  (process gate — noting `tests/e2e_live.py` must be named
  explicitly until CI collects it), FFC-40, ADD-09 (evidence-carry
  as process), F.4's self-tests (every verifier passes a known-good
  canned response and fails the F-05-shaped broken one). Exit =
  first full live battery run + one clean week of nightly shadow
  replay in report-only mode before it becomes a gate input; the
  battery's own pool draw is capped and scheduled in low-traffic
  windows so measurement never starves production of free capacity.
- **Rollback:** scores and profiles are registry data; replay and
  reporting are read-only by construction; the registry-change gate
  can be loosened back to invariants-only by configuration.

### Release 5 — Catalog, price truth & candidates (aspect C + discovery) → v0.11.0

- **Scope:** `tools/price_check.py` (weekly machine-readable
  fetchers; monthly page-check list; `price_drift` alerts); the
  endpoint-family discovery job (disabled-only emission, ADD-11
  enforced); real-zero-cash record maintenance (§2g); F-06
  reconciliation closed or escalated from a week of
  `provider_cost_usd` rows; Cerebras pre-expiry check (≈2026-12-12);
  candidate pipeline runs — UnoRouter and Kilo terms/consent review
  and, **only with Jordan's enrollment/consent go**, synthetic
  public-prompt probes; OpenRouter non-`:free` $0 sweep; Gemini 3.8
  Flash disposition from §2e's differential track.
- **Dependencies:** R1 (truth fields), R4 (battery as the probe
  instrument).
- **Effort:** M.
- **Acceptance:** FFC-22, FFC-28, FFC-29, FFC-30, FFC-31 (recorded),
  FFC-32, FFC-33, FFC-35, FFC-36, ADD-11 (enforcement side);
  FFC-23/FFC-24 if and only if the candidates reach probe stage.
  Exit = first live price-check run hand-verified against three
  provider sources (one per source class), report filed.
- **Rollback:** the tooling is read-only; every registry consequence
  travels the normal deploy gate individually. There is nothing to
  roll back except individual gated changes.

**After R5:** Cycle 2 candidates, in the order the evidence earns
them — selection-time use of reasoning profiles (D v2); a learned
ranker in shadow (RouteLLM pattern) once outcome telemetry has
labeled, task-specific volume worth learning from; multi-instance
state only if a second gateway instance ever exists. None is
scheduled; each enters through the loop's stage 1 like everything
else.

---

## 4. DECISIONS LEDGER

Every open decision in one place. "Needed by" names the release that
first consumes the ruling; releases are designed to ship without the
ruling wherever the table says so.

| # | Decision | Status | Needed by / design posture |
|---|---|---|---|
| J-1 | Pin semantics vs. the privacy wall + `enabled` | **RULED 2026-11-12 ~22:20 MST** — pins bound to the two hard boundaries (disabled never serves when pinned; sensitive never rides a pin to an uncleared provider); quality/cost/context stay pin-overridable | Closed. Implemented in R1 (§2f) |
| J-2 | Circuit-breaker / demotion authority: may routing react automatically (bounded, self-healing), or stay alert-only? | **OPEN** | R2 ships observe-only without it (FFC-39 encodes the guard); enforcement is a config flip at its own gate after the observe week. Handoff D2's 90%-demotion question is absorbed here — demotion is *designed* in R2; this ruling decides whether it ever *acts* |
| J-3(i) | Effective-cost accounting, **logging-only** variant (capture cached/reasoning/provider-reported fields; no displayed figure changes) | **OPEN — undecided** | Consumed by R3/R5 design (§2h); fields land as evidence either way. Explicitly NOT the cache-pricing correction, which Jordan ruled "NOT NECESSARY" — that build stays off the table |
| J-3(ii) | Decision #5 amendment: judged sample of **non-sensitive** traffic only (1–5%, cap ≈50/day, judge inherits the sensitivity floor) | **OPEN** | Only R4's scoring depth depends on it; the golden set and deterministic verifiers work without it |
| J-4 | Flaky-route disabling stays **proposal-only** (owner approves each; E.3.5 evidence standard) | **OPEN — confirm** | Plan assumes proposal-only throughout; R4's proposal feed is built to that assumption. No auto-disable is built anywhere in this plan |
| D3 | NVIDIA evaluation-only entries in production: keep serving under the recorded caveat / isolate to test paths / disable | **OPEN** | R1 lands `trial_only` + `production_allowed` fields so any ruling is machine-checkable (FFC-21 written against the field). The two entries carry production traffic today under the caveat |
| D4 | Quota source of truth: local counters only, or hybrid (headers on the hot path + authenticated endpoints — OpenRouter `GET /api/v1/key`, Groq headers — for nightly reconciliation)? | **OPEN** | R2 is designed hybrid-ready but fully functional local-only. The hybrid is also the resolver for the Cloudflare reset discrepancy (§2b) |
| D5 | Persist the handoff compilation itself to GitHub | **OPEN — Jordan's, reserved by the handoff itself** | No release depends on it. The evaluations and this plan are pushed; the handoff texts remain in Drive + the goal workspace |
| CF-$5 | Cloudflare **$5/mo Workers Paid** (removes the daily neuron cap as a binding constraint; unlocks the disabled glm-5.3-flash route; makes the reset discrepancy moot) | **OPEN — money call** | No release depends on it. R2's neuron accounting + context ceilings are the no-spend path and ship regardless |
| CF-ceiling | Context-ceiling the Cloudflare entries so large turns can't drain the pool | **ABSORBED** into R2 design (§2a/§2b) | No longer a standalone decision; Jordan may still strike it at R2's gate |
| CER-fund | Cerebras PAYG funding before the trial expires ≈**2026-12-12** (expiry also kills the existing `cerebras/gpt-oss-120b` entry's terms) | **OPEN — time-bound** | R5's pre-expiry check assumes a decision either way; the entry's `cost_mode: promotional_credit` + expiry are recorded in R1's backfill |
| UNO/KILO | UnoRouter + Kilo Gateway: approve account enrollment / per-model consent for probe stage | **OPEN — consent gate** | R5 stops at terms review without it. No enrollment, no consent acceptance, no account creation before this ruling (FFC-23/24 conditional) |
| SOAK-2 | Week-2 soak gate review (Week 1 closed 2026-11-10 with Nov 9 as the incident day; Week-2 gate is Jordan's review, not an auto-pass) + removal of the `sovereign-v05-soak-daily` cron at the gate | **OPEN** | Independent of the releases; the ledger-dollar figures in soak reports are ceiling estimates per the phantom-dollars correction — the gate should be read against provider consoles |
| Carried | Hermes client-key rotation (after the 2026-11-12 tool-output exposure — Jordan's call); SiliconFlow $0-model probe (console check or explicit probe OK — ambiguous pricing means a probe could charge); "NROUTER" identification (still unidentified; **not** UnoRouter); per-task-class effort defaults beyond D's conservative v1 (only if Jordan wants a hand in them); Gemini 3.8 Flash bounded re-probe (already routed to the §2e differential track; synthetic public prompts only) | **OPEN (small)** | None blocks a release; each is a one-line ruling when Jordan wants it |

D6 (deploy approval) is not a decision row: it restates the standing
gate that already governs every release above.

---

## 5. RISKS AND NON-GOALS

### Risks

- **A buggy breaker recreates a policy dead end.** The 2026-11-06
  lesson: stateful selection can wall the fleet worse than the
  failure it reacts to. Mitigations are designed in, not hoped for:
  demotion never empties a candidate list (ADD-07), half-open always
  admits a probe, observe-only week before any enforcement,
  kill-switch flag, Nov-9 replay as a regression test (TEST-13).
- **Pool coupling is partly unknowable.** Aggregator routes can share
  underlying capacity invisibly (Kilo's auto-free landing on the same
  NVIDIA endpoints; a virtual router inheriting a pool). The design
  treats unknown coupling as conservative uncertainty, never as
  assumed independence — but some pool boundaries will only be
  learned from production evidence, which is why §2e's loop is part
  of the architecture and not an afterthought.
- **Provider facts rot.** Free tiers are retired (GitHub Models),
  redefined (Mistral Free mode), and re-priced without notice; a
  documented reset already disagrees with one account's observed
  behavior. Every provider fact in the registry now carries a source
  and a date (§2g) precisely so rot is detectable — but detection
  cadence (weekly/monthly) bounds how fresh truth can be.
- **Measurement can consume what it measures.** The pin battery and
  nightly replay draw on the same free pools production needs —
  worst case, the battery itself exhausts the Cloudflare pool.
  Budget caps, low-traffic scheduling, and pool-share limits are
  hard requirements in R4, not tuning.
- **Effort defaults are moving targets.** Meta's default effort is,
  in its own cookbook's words, "still being finalized"; the Spark
  burn that justified a disable did not reproduce ten times the same
  evening. Reasoning profiles therefore carry dated evidence and sit
  inside §2e's re-probe cadence — a profile is a claim, and claims
  expire like disables do.
- **Single-process state grows up.** The attempt ledger and pool
  tables deepen the SQLite store's role. The store is already
  lock-serialized and the write volume is ~2,700 route rows/day; the
  risk is accepted knowingly, with ADD-12's completeness test as the
  tripwire — but if write contention ever shows in latency, that is
  a Cycle 2 design problem, not a reason to reach for Redis now.
- **Score confidence theater.** A measurement program can manufacture
  false precision (small samples, stale evidence, benchmark-shaped
  prompts). The provenance fields (§2d) exist to keep confidence
  honest; any score whose sample is thin must *look* thin wherever
  it is displayed.

### Non-goals — what we are deliberately NOT doing

- **No gateway replacement.** LiteLLM, Bifrost, and Portkey are
  pattern references (cooldowns, pool separation, diagnosable
  failover). None becomes a dependency; the endpoint, clients, and
  SQLite state stay. (Both external texts and both evaluations
  converge here: improve, don't replace.)
- **No Redis, no multi-instance routing state.** One process, one
  VM. Revisit only if a second gateway instance is ever deployed.
- **No learned router yet.** RouteLLM-style ranking is deferred until
  outcome telemetry has labeled, task-specific volume worth learning
  from — and even then it enters in shadow, inside the zero-cost-first
  constraints, never above them.
- **No auto-consent, no auto-enrollment, no auto-purchase.** No
  account creation, terms acceptance, or billing action for any
  candidate provider without Jordan's explicit go (FFC-23, FFC-33).
- **No auto-disable and no auto-enable.** Disablement is
  proposal-only (J-4); catalog discoveries are disabled until probed
  and approved (FFC-28, FFC-29). Automation writes evidence; humans
  flip eligibility.
- **No cache-pricing correction.** Ruled "NOT NECESSARY" by Jordan
  (2026-11-12); ledger dollars remain ceiling estimates and provider
  consoles remain the money truth. (J-3(i)'s logging-only capture is
  a separate, still-open question — §2h draws the line.)
- **No response caching of agent traffic.** Exact-match caching is
  privacy- and side-effect-sensitive; the handoff's own cautions are
  the minimum bar and no current need is demonstrated. Deferred, not
  adopted.
- **No quota evasion.** No provider-key cycling, no multiple accounts,
  no throttle workarounds. New capacity is vendor-permitted and
  authenticated under genuine, approved accounts, or it isn't
  capacity (§11.6's line, adopted).
- **No sensitive traffic to public-only ground — ever, on any path,**
  including pins (§2f) and including candidate providers (§2g).

---

## 6. APPENDIX — CORPUS CONTRADICTIONS AND HOW THIS PLAN RESOLVES THEM

The synthesis instruction was to read the corpus as one body. Where
the body disagreed with itself, the resolutions below govern; each
names the winner and why.

1. **Spillway mechanics.** Handoff v1 §1: "a paid spillway is invoked
   after the free/regular attempt path is depleted." Code + v1
   evaluation §2.9: paid/premium entries already sit in the normal
   cost-sorted list; the spillway is a distinct second selection
   whose function is the ruled relaxation of the sensitive wall.
   **Winner: the code (and Jordan's own review wording).** No
   two-pass free-vs-paid engine is built anywhere in this plan.
2. **Cloudflare reset.** Provider documentation: 00:00 UTC. This
   account's route log: 429s at 00:01 and 03:25 UTC after that reset,
   zero intervening serves, error 4006. **Winner: observed behavior,
   as a design rule** — `reset_at` is observed data (§2a/§2b, FFC-34);
   the fact itself is resolved by D4's hybrid source or the first
   successful serve, or made moot by the $5 Paid decision.
3. **Breaker authority.** Handoff §3/§6 design breakers as acting on
   routing; the Cycle 1 plan and loop charter ship E observe-only
   until J-2. **Winner: the plan and charter** (Jordan's own project
   documents; the handoff itself defers in its §9). R2 is built to
   that posture.
4. **Build order.** Three sequences exist: the Cycle 1 plan table
   (A→B→G→F1→D→C→F2→E-last), the evaluations' re-sequence
   (A→B→E→G→F1→D→C→F2), and handoff §6's phases. **Resolution:**
   §3's release grouping — it honors the plan's dependency logic,
   Jordan's re-sequencing (E's observe-first half lands second, not
   last), and the post-table J-1 ruling (G unblocked, into R1). The
   handoff's phases are retained as a checklist inside the releases,
   not as a sequence.
5. **Ledger schema.** Review: 6 pool fields. Handoff §3: 13 fields.
   v2 §10.5: four normalized tables. **Resolution: merged** (§2a) —
   the review's pool core + the handoff's breaker fields, stored in
   §10.5's table shape, minus its universal-reservation weight
   (scoped amendment).
6. **Benchmark authority.** Handoff §3 P2: benchmarks "establish
   initial scores." Review §4: leaderboards nominate; our own
   regression tests decide. **Winner: the review** — it matches the
   project's standing production-shaped-tests rule (§2d).
7. **Gemini 3.8 Flash status.** v3 §11.1: "MODEL RESPONSES
   UNPROBED." Project records: probed 2026-11-12, timeouts at 60 s
   and 120 s. **Winner: project records** — status PROBE-FAILED,
   routed to the differential track (§2e), ADD-09 written so the
   next research text cannot repeat the error.
8. **OpenRouter pool shape.** v1 evaluation §2.13 and
   ROUTING-PRINCIPLES describe one monolithic free pool; v2 NEW-01
   distinguishes the `:free` cap from other $0 models. **Both are
   true at different scopes:** monolithic for every entry currently
   enabled; NEW-01's boundary is the correct general rule and is the
   taxonomy §2a encodes (virtual router inherits the `:free` pool).
9. **The Gemma and Spark disables.** Registry notes (pre-diagnosis)
   blamed the providers. The differential diagnoses: Gemma leakage =
   ours-shaping (500s/latency = provider); Spark 1.3 = ours-config.
   **Winner: the diagnoses** — the disables stand or fall on their
   surviving components only, the notes are corrected through the
   gated path, and the protocol (§2e) exists so the next disable is
   diagnosed before it is believed.
10. **NVIDIA presence.** ROUTING-PRINCIPLES' Nov-12 text says both
    "NOT added" (early probe) and, in a later update, "added under
    the owner's expansion order" with the trial-terms caveat.
    **Winner: the later update** — the entries are live, carry
    production traffic, and their posture is decision D3.
11. **"NROUTER" vs. UnoRouter.** The charter's carried list has an
    unidentified "NROUTER"; tonight identified **UnoRouter** as the
    "uno" of Jordan's instruction. **They are different threads** —
    UnoRouter is CANDIDATE-01 in §2g; "NROUTER" remains unidentified
    in the ledger (§4, carried).
12. **J-1's status.** Handoff D1 and v3 §11 still list pin semantics
    as open; both predate Jordan's ~22:20 MST ruling. **Winner: the
    ruling** — J-1 is closed, recorded in the Cycle 1 plan's
    Decisions log, and implemented in R1.

---

*Plan ends. Companion record: the v1 evaluation
(`docs/cycles/cycle-01-handoff-evaluation.md`), the v3 delta
evaluation (`docs/cycles/cycle-01-handoff-v3-delta-evaluation.md`),
the Cycle 1 plan (`docs/cycles/cycle-01-plan.md`), and the Route
Health Investigation Protocol
(`docs/cycles/route-health-investigation-protocol.md`) carry the
underlying evidence; this document is the synthesis Jordan ordered,
not a replacement for any of them. No code, registry, or production
state was changed in its production.*
