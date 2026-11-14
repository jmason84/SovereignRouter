# Changelog

All notable changes to the Sovereign Router gateway are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) — the gateway
reports its version at `GET /health` (`version` field) and in the
`X-Sovereign-*` response metadata. The routing *policy* history lives in
`docs/ROUTING-PRINCIPLES.md`; incident detail in `docs/incidents/`.

## [Unreleased]

**Expansion phase 2, 2026-11-12** (owner's order — "expand the options, with the understanding of the price"; gateway code unchanged; registry 42 → 81 models, 72 enabled):

- **Added 36 enabled routes, every price verified against the provider's own published source:** DeepInfra ×5 (`gpt-oss-120b` $0.037/$0.17 — the cheapest route to those weights in the registry; `deepseek-v4-flash-0731` $0.06/$0.18 at 1M context; `llama-3.1-8b-turbo` $0.02/$0.04 — the cheapest chat route; `mistral-small-24b`; `qwen3.8-27b` at list, catalog discount observed live); Novita ×7 (Llama 3.1 8B, GLM-4.7-Flash, Qwen3.8-Flash, V4 Flash 0731, Qwen3.8-27B, Kimi K2.5, GLM-4.6 — prices dual-verified on Novita's API pricing fields and pricing pages); OpenRouter paid ×6 (DeepSeek V4 Flash, GLM-5.3-Flash seeded at its observed billing ceiling, Qwen3.8-Flash, Gemini 3.1 Flash-Lite, Llama 4 Maverick, Claude Haiku 5.5 — the first Anthropic route; all `spillway_sensitive_excluded`: their `data_collection=deny` probes passed, but OpenRouter exposes no auditable data-policy metadata, so the conservative aggregator posture stands); SiliconFlow ×3 (Qwen3-32B, DeepSeek V3.2, DeepSeek V4 Flash); Hugging Face ×2 via `:deepinfra` backends ONLY (GLM-5.3-Flash, Qwen3.8-27B — the standing no-`:cerebras` rule held); Mistral ×3 (Small, Ministral 8B, Medium — Medium's price rests on Mistral's own product announcement); Z.ai paid ×2 (GLM-4.6, GLM-5.3-Flash at Z.ai's official pricing page; tool-calling probe-proven on Z.ai direct); `groq/qwen3.6-27b` (private-free lane = data posture, metered Developer price); `cerebras/qwen-3.8-27b` (paid, trial terms, reasoning pinned low); `meta/muse-spark-1.2` (premium, same published price group as 1.1); `google/gemini-3.1-flash-lite` (public-only-free; per-model bucket = real capacity); Aion ×2 (public-only-free on the free tier's account-wide 15 RPM / 20k tokens/day allowance; 3.5-mini's reasoning cannot be disabled — pinned `low`; 2.0 takes reasoning off); NVIDIA ×2 (public-only-free; **trial terms are evaluation/testing only, not production** — the caveat is carried verbatim in both entries' notes, per the owner's expansion order).
- **Added disabled (3), each with a dated reason in the registry:** `openrouter/kimi-k2.6` (plain probe returned empty content — reasoning ate the budget on the landed provider), `meta/muse-spark-1.3` (no content even at 2,048 tokens), `google/gemma-4-26b-it` (inconsistent probes: one slow 200 with thought leakage, one 500 INTERNAL). `google/gemma-4-31b-it` was re-probed (2× 200 with thought leakage, 2× 500 INTERNAL) and **stays disabled**.
- **Price corrections (SiliconFlow's own published list governs):** `siliconflow/glm-5.3` $0.70/$2.20 → **$1.40/$4.40** (identical to Z.ai direct — the v0.5.6 "half of Z.ai" reading was wrong) and `siliconflow/deepseek-v4-pro` $1.00/$1.50 → **$1.50/$3.135**.
- **Quality correction (owner-directed, cross-project evidence):** `openrouter/north-mini-code-free` chat and long-context quality 3 → 2. Every sampled malformed-JSON draft from the Launch AI Works Article Engine (16 reconciled failures) came from this model while a control model went 11/11 clean on the same node; long-form structured drafting classifies as chat at L2+ (floor 3), so auto-selection no longer picks it for drafting work. Simple chat (floor 2) and code (unchanged at 4) still reach it. Smoke tests prove both directions via `/v1/shadow`.
- **Seeding implements the owner's routing philosophy** ("best free first, then next cheapest best"): among $0 entries cost ties, so quality scores carry the order — `openrouter/nemotron-3-ultra-free` now leads the free bench for chat/reasoning (5/5/5); among paid entries, verified prices carry it.
- **Registry hygiene:** one id/model-string collision was created and caught by the test suite (`google/gemini-3.1-flash-lite` is both an entry id and the OpenRouter entry's upstream model string; `Registry.get()` matches either) — the Google entry now sits earlier in the file and a smoke test asserts `get(id)` is exact for every entry.
- All 36 new enabled routes were probe-verified directly and then pinned-served through the production router (36/36 exact-model 200s, unique conversation per pin), plus a crm 403 spot-check on a new public-only-free entry. Tests: 347 → 554. Deploy note: the registry copies must land owned by the gateway uid (10001) — an scp that preserved root ownership crash-looped the gateway for ~2 minutes before the fix; snapshot `pre-expand2-20261112` and `registry.json.bak-20261112-preexpand2` backups precede the change.

**Free-catalog expansion, 2026-11-12** (owner's order; gateway code unchanged; registry 22 → 42 models, 36 enabled):

- **Added 15 enabled $0 routes:** `zai/glm-4.5-flash` (tool-calling probe-proven) and `zai/glm-4.6v-flash` (vision) — Z.ai's free Flash family; `cloudflare/llama-4-scout`, `cloudflare/qwen3.8-27b`, `cloudflare/gpt-oss-20b`, `cloudflare/mistral-small-3.1` on the shared Workers AI neuron pool (deploy probes were pool-blocked — the day's 10,000-Neuron allocation was already exhausted; routes are catalog-verified on the same endpoint/key path as the two pre-existing Cloudflare entries, content-serve verification pending the 00:00 UTC reset); eight OpenRouter `:free` routes (`nemotron-3-ultra`, `nemotron-3.5-super-vl`, `nemotron-3.5-lightning`, `nemotron-3-nano-omni`, `ernie-5.5-flash-preview`, `flint`, `lfm-3-1.7b`, `lfm-2.5-2.6b`) on the shared OpenRouter free pool; and `openrouter/free-router` in **private-free** — its `data_collection=deny` probe passed, but only *without* the reasoning-disable the other private OpenRouter entries carry (the endpoint mandates reasoning), so its `extra_body` carries the deny alone.
- **Added disabled (5), each with a dated probe-failure note in the registry:** `google/gemma-4-31b-it` (Google's OpenAI-compatible endpoint returned 500 INTERNAL on every probe shape), `cloudflare/glm-5.3-flash` (403 — not available on the Workers Free plan; structural), `openrouter/inkling-2-small-free` (403 — gated to "agentic harnesses"), `openrouter/gemma-4-31b-free` and `openrouter/gemma-4-26b-free` (upstream 429 rate-limit wall on every probe round).
- **Pool truth recorded:** OpenRouter `:free` routes share one account pool (20 RPM / 1,000 req/day, verified against the account) and Cloudflare routes share 10,000 Neurons/day — added routes buy choice and failover, not requests. Google's free quotas are per-model buckets (real capacity); Z.ai's throttle scope is unverified. The deny conditionals for nemotron-ultra and super-vl resolved **negative** (404, no deny-honouring endpoint) — both are public-only-free.
- Tests: 248 → 347 (smoke suite extended to all 42 entries, the 20 newcomers' lanes/flags/limits, deny-probe outcomes, and 15 new pin cases).

Post-0.6.0 registry changes, 2026-11-12 (gateway code unchanged):

- **Enabled** `google/gemini-3.5-flash-lite` (public-only-free), `cloudflare/gpt-oss-120b` and `cloudflare/glm-4.7-flash` (public-only-free, owner-supplied Workers AI token), `mistral/mistral-large` and `mistral/codestral` (reclassified to the paid lane at published pay-as-you-go rates after an owner-approved $10 activation — Mistral's free tier issues no API keys), and `meta/muse-spark-1.1` (premium; owner resolved the Meta team billing issue). Every enable was probe-verified directly and through the router.
- **Removed** `huggingface/gpt-oss-120b-cerebras` (owner's order): the HF passthrough ignored reasoning controls and leaked deliberation (2026-11-11); the direct `cerebras/gpt-oss-120b` route replaces it. Standing rule recorded in `docs/ROUTING-PRINCIPLES.md`: Cerebras is called direct, never via HF.
- Registry now 22 models, 21 enabled (only `siliconflow/deepseek-v4-pro` disabled, a deliberate duplicate).

## [0.8.3] — 2026-11-13 (DEPLOYED 2026-11-13 21:33:54 MST)

**True-cost ledger** (owner's order, 2026-11-13: "ADJUST THE LEDGER TO
THE ACTUAL AMOUNT" — reversing his 2026-11-12 ruling that the
cache-pricing fix was "NOT NECESSARY"). Decision package:
`docs/cycles/release-v0.8.3-true-cost-ledger.md`. Failure-log entry
F-12.

- **Cached-token parsing, all shapes, both paths.** The gateway now
  reads cached-input counts from every upstream usage shape —
  OpenAI-style `prompt_tokens_details.cached_tokens`, DeepSeek
  `prompt_cache_hit_tokens`, Anthropic `cache_read_input_tokens`
  (aggregator shape, and the native shape where input_tokens excludes
  cache reads), Google `usageMetadata.cachedContentTokenCount` — via
  one shared parser used by the JSON path and, for the first time, the
  streaming path (whose finalize previously hard-coded
  cached_tokens=0). Parsed counts land on the route row and the quota
  usage event and are priced by `estimate_cost` at the entry's
  cached-input rate. Absent/garbage usage degrades to cached=0,
  exactly the old behaviour; parsing can never break a response.
  Before this fix, cache-heavy turns (300–530k contexts resent per
  turn, ~90%+ cache hits at ~1/10 price) were priced as full-price
  misses — the phantom-dollar overstatement proven against the
  provider consoles on 2026-11-12 (DeepSeek 30-day actual $9.34 vs
  the ledger's $109.32 for V4 Pro on Nov 11 alone).
- **Registry: cached rates verified from primary sources**
  (2026-11-13 reads). Added: `premium/gpt-5.5` $0.50 (OpenAI's own
  model page — the carried "placeholder" price set was exact and is
  now graded verified), `ollama/gpt-oss-120b` $0.014 (Ollama's pricing
  table), `mistral/mistral-medium` $0.15 (Mistral's API price sheet).
  Confirmed and upgraded seeded → verified: `deepseek/deepseek-v4-pro`
  $0.044 (DeepSeek pricing page, peak accounting),
  `zai/glm-5.3` $0.26 (Z.ai pricing page),
  `siliconflow/deepseek-v4-pro` $0.135 (SiliconFlow listing).
  **Price correction** (F-04 pattern): `siliconflow/deepseek-v3.2`
  $0.259/$0.14/$0.80 → **$0.27/$0.135/$0.42** per SiliconFlow's own
  listing. Entries whose providers publish no cached rate keep
  cached=null — cached tokens price at the full input rate, never
  assumed free (Cerebras, three DeepInfra models, Novita gpt-oss-120b,
  both Hugging Face pass-throughs, SiliconFlow Qwen3-32B). New
  registry invariant: a cached rate must lie in [0, input] and
  inherits the entry's provenance grading.
- **Soak report:** per-day token totals and free share BY TOKENS
  alongside free share by requests (owner's order, 2026-11-13).
  Census-verified against production routes: Nov 12 = 57,556,861
  tokens, free-lane 80.9% of tokens vs 74.4% of requests; Nov 13 =
  84.1% of tokens vs 70.4% of requests.
- **Reconciliation probe** (throwaway v0.8.3 gateway on VM103:6085,
  torn down after): repeated ~31.3k-token context ×6 turns against
  DeepSeek direct (JSON + streaming arms). Provider reported 99.8%
  cache hits on warm turns; the gateway's parsed cached_tokens
  matched the provider-reported hit count on **8/8 turns exactly**,
  and ledger cost equalled the hand-computed console-style cost on
  every turn (session ledger $0.053186 = hand total at the registry's
  peak-rate basis; the probe ran on a Saturday, so DeepSeek's actual
  off-peak deduction is half that — the recorded peak-accounting
  convention, flagged in the package).
- **History is not rewritten.** Upstream cache data was never stored
  for past turns, so pre-v0.8.3 route rows remain ceiling estimates;
  dollars are actuals only from this deploy onward.
- **DEPLOY RECORD (2026-11-13):** PR #8 merged to main as
  `a240bc4cf8bd6f02d1972fb15c3e1af1bd9615dd`; CI run 43556738063
  SUCCESS (pytest, Python 3.12); suite 654. Snapshot
  `snapshots/pre-v083-20261113`. Image
  `192.168.0.28:5000/sovereign-router:v0.8.3`
  (sha256:0e0b0ed786fd1334b588913b93cf33a2548930264e3b3658798403015591f010);
  gateway recreated 21:33:54 MST (cutover epoch 1794630834);
  /health version 0.8.3, 81 models, clients
  [crm, hermes, n8n, openclaw], sidecar ok, alert chain ok;
  prod :6070 still v0.2.1. **Registry changed:** new md5
  `a915a4d1c0d5237534feba2d562358a0` byte-identical across GitHub
  main, VM103 build/src and VM103 state (10001:10001, 644).
  Smokes all pass (non-streaming exact echo; streaming 12 chunks
  finish=stop; tool call well-formed). **Live true-cost proof:**
  pinned openclaw session on `deepseek/deepseek-v4-pro`, ~20.4k
  context ×5 turns — turn 1 cold (cached 0, $0.02731872); warm
  turns cached 20,352–20,480 (~99.4% hits) with recorded cost
  equal to the hand-computed (in−cached)×$1.32 + cached×$0.044 +
  out×$3.96 per 1M on every turn (e.g. in 20,559 / cached 20,480 /
  out 52 → $0.00121132). Traffic watch: 63 rows since cutover, all
  200, no `stream_bound_timeout`; genuine fleet rows began
  recording cached_tokens > 0 within minutes (Hermes on
  openrouter/nemotron-3-ultra-free: 4,234 / 9,314 / 45,091
  cached). **Dashboard:** exporter v1.2 adds
  `sovereign_premium_spend_since_truecost_usd`; the Overview
  premium panel now shows ACTUAL dollars since the cutover
  (first read: openclaw $0.032772, the proof session exactly) and
  the pre-fix MTD ceiling totals moved into the READ BEFORE
  annotation panel as static historical text. Rollback: image →
  v0.8.2 AND restore state/registry.json from the pre-v083
  snapshot, then compose up -d gateway.
- Tests: 636 → 654. Rollback: image swap only — the registry changes
  are value-only (no new keys); the v0.8.2 loader was verified loading
  the new registry (81 entries) in this build.

## [0.8.2] — 2026-11-13 (built 2026-11-13; NOT yet deployed)

Fix package for failure **F-11** (Hermes executive-ops stream aborts on
`zai/glm-4.5-flash`, 2026-11-13 16:26–16:33 MST): three consecutive
router 502s at 121–152 s upstream latency. A stalled upstream stream
hung against the gateway's flat 120 s read timeout; partial output had
committed, so in-request failover was impossible by design (ADD-06);
and Hermes' automatic retries — new requests — re-picked the SAME
entry all three times, because v0.8.0's attempt ledger records
failures but selection never read them (J-2 observe-only). Decision
package: `docs/cycles/release-v0.8.2-decision.md`. **Code-only
change; no registry change** (the F-11 differential probes completed
every shape on the entry — see the package — so no quality adjustment
was justified; rollback is an image swap only).

- **Streaming time bounds.** Streaming upstream reads are driven
  per-line with two configurable bounds in place of reliance on the
  flat 120 s read timeout: time-to-first-chunk **45 s** and
  inter-chunk stall **60 s** (`SOVEREIGN_STREAM_FIRST_CHUNK_TIMEOUT_S`
  / `SOVEREIGN_STREAM_STALL_TIMEOUT_S`; the non-200 error-body read is
  bounded by the first-chunk value too). A bound expiry enters the
  existing transport-error handler, so the ADD-06 commit guard
  (post-commit: terminate with the typed error event, route signal
  `stream_aborted_after_commit:<entry>`) and pre-commit failover apply
  unchanged; a new route signal `stream_bound_timeout:<entry>` marks
  bound-fired aborts. Non-streaming calls are untouched.
- **Stream-abort entry cooldown (the incident fix).** Any streaming
  abort — transport drop or bound expiry, committed or not — cools
  the entry for **600 s** (`SOVEREIGN_STREAM_ABORT_COOLDOWN_S`),
  persisted as an expiry timestamp in the quota store's meta table
  (durable across restarts, self-healing, no schema change).
  `build_candidates` skips a cooling entry with reason
  `cooldown_stream_abort` (checked last, after every other gate), and
  sticky reuse inherits the skip with the precise reason instead of
  the generic `sticky_no_longer_eligible`. **Only-candidate
  exception:** if every otherwise-eligible entry is cooling, the
  cooled set serves, annotated `cooldown_bypassed_only_candidate` —
  a cooldown never manufactures a zero-candidate 429. Explicit pins
  still override the cooldown, per J-1 (pins override everything
  softer than the two hard boundaries). This is a bounded memory for
  ONE proven failure class, not general demotion enforcement: J-2's
  observe-only posture stands for all other classes.
- **`truncated_tool_call` ledger class** (carried R3 item, F-09's
  open remainder). When a completion's `finish_reason` is `length`
  and it carried tool calls, the attempt row now records failure
  class `truncated_tool_call` (scope: model) instead of masquerading
  as a clean serve — JSON and streaming tool paths both. The
  attempt's outcome stays `served` (the response was received); the
  class also feeds the observe-only `would_demote` annotation.
- **Tests:** 627 → 636 (9 new in `tests/test_stream_resilience.py`:
  stall aborts inside the bound, first-chunk timeout fails over
  pre-commit, abort sets the cooldown, next selection skips and
  serves the sibling, sticky reuse respects the cooldown,
  only-candidate still serves, cooldown expiry, and
  `truncated_tool_call` on both paths plus a clean-serve control).

## [0.8.1] — 2026-11-13 (deployed 2026-11-13)

Fix package for failure **F-09** (route-health investigation, `docs/cycles/route-health-investigation-protocol.md` §7): the Hermes write_file stream stalls were ours-config — the gateway's 2048 default output budget bound every client that omits `max_tokens`, Hermes omits it on every turn, and tool-call payloads truncated mid-stream at exactly 2048 (finish=length) against a model whose published completion ceiling is 460,800. Decision package: `docs/cycles/fix-package-v0.8.1-decision.md`. **Code-only change; no registry change.**

- **Tool-bearing default budget.** When the client omits `max_tokens` (and no internal override applies), `upstream_body` now falls back to `_default_max_tokens(body)`: **8192** for tool-bearing requests, **2048** (unchanged) for plain chat. Tool-bearing is read from the request's own shape, the same way the handlers read it for selection: a non-empty `tools` array, or an explicit `tool_choice`.
- **Invariants held:** a client-sent `max_tokens`/`max_completion_tokens` passes through untouched in both shapes; the remaining-context clamp applies identically on top of either default; the budget is still written explicitly upstream (as `max_completion_tokens` for `completion_tokens_param` entries); the streaming path shares the body builder and gets the same budgets. Raising the global default was rejected in §7: it would lengthen every free-lane generation, runaway ones included.
- **Tests:** 616 → 627 (new `tests/test_tool_output_budget.py`, 11 tests: predicate matrix, both defaults on the wire pinned to the F-09 entry itself, client-value passthrough in both shapes, tight-context clamp parity, the gpt-5.5 completion-tokens shape, streaming parity in both shapes).
- **Complementary client fix (owner ordered both, gateway first):** Hermes now states an 8192 budget explicitly on its router requests (VM112, default + executive-ops profiles) instead of omitting `max_tokens` and inheriting whatever a gateway default happens to be. The mechanism is a base_url-matched provider `extra_body` — **not** the `max_tokens_cap` the §7 proposal named: in this Hermes build that key exists only in the MCP-sampling path and does not govern the main loop (the deviation and the full evidence are in the decision package §4). The gateway fix remains primary: it protects every omitting client, not just Hermes.

**Deploy record (2026-11-13, owner's order at the standard gate):** PR #5 merged cleanly via the connector — merge commit `618a11c7b3a23dd983714ca209e38e2fd12a1c32`. CI read back at this gate via the GitHub API, closing the build run's one open item: PR run #32 (`43393991009`, head `a5d3326a`) **SUCCESS**, the PR synchronize run (`43394342150`, final head `d425feaa`) **SUCCESS**, and run `43396867354` on main for the merge commit **SUCCESS**. Snapshot `snapshots/pre-v081-20261113` precedes the change. Image `192.168.0.28:5000/sovereign-router:v0.8.1` built on VM103; gateway recreated via `compose up -d` (the v0.2.1 `:6070` rollback stack untouched). Registry UNCHANGED (code-only): md5 `96eaa3c99643e1016508cee16caf23de` byte-identical across GitHub main, VM103 `build/src`, and VM103 `state`. `/health`: ok, version 0.8.1, `registry_models` 81, clients crm/hermes/n8n/openclaw. **Live smokes** (raw on-box requests as the hermes client with `max_tokens` OMITTED — a Hermes session now states 8192 itself and would mask the gateway default; unique conversation per case): (a) tool-bearing + the F-09 copy shape (reproduce 320 provided lines) pinned to `openrouter/dots-3-note-preview-free` → **finish=stop, 8,001 completion tokens, 320/320 lines** — the F-09 truncation is dead through the gateway; (b) **negative control** — plain chat, no tools, same demand → finish=length at **exactly 2,048** (the chat default is unchanged; this is not a global raise); (c) tool-bearing + client-sent `max_tokens=4096` → bound at exactly 4,096 (a client-sent value wins over the new default); (d) streaming, tool-bearing, unset → the stream-path budget bound at exactly 8,192 on the first run (finish=length against the ceiling on a verbose rendition), and an identical re-run completed the full SSE at finish=stop, 8,001 tokens, 320/320 lines; (e) unpinned auto → 200 on the free lane (`openrouter/nemotron-3-ultra-free`, $0.00). **Process note:** the original deploy agent was interrupted after the deploy proper (merge, image, recreate, and health were already complete); verification and records were finished in a separate completion pass — nothing was re-merged or re-deployed. **Rollback:** point the compose override back to the v0.8.0 image and `compose up -d gateway`.

## [0.8.0] — 2026-11-12 (deployed 2026-11-13)

Cycle 1 release R2 (pool ledger & failure memory), per `docs/SOVEREIGN-ROUTER-NEXT-PLAN.md` §3 and `docs/cycles/release-v0.8.0-decision.md`. **Data layer, observe-only: no selection behavior changes.**

- **Attempt ledger.** The quota store gains an append-only `attempt_events` table: EVERY upstream attempt (serves included) is recorded with entry, pool, outcome, HTTP status, failure class, scope, and the would_demote annotation it produced. Until now failed attempts wrote no rows at all — the fleet had no failure memory (2026-11-09 census: 369 attempts against one dead account, invisible in the ledger). Serves keep flowing through `usage_events` exactly as before.
- **Pool model** (`src/pools.py`). Every registry entry maps to exactly one pool, derived programmatically: the OpenRouter `:free` account pool (13 `:free` models + the virtual router inheriting it, NEW-01), OpenRouter paid as a separate class, the Cloudflare Workers AI neuron pool (unit=neurons, 10,000/day budget), Google per-model buckets, Groq per-model token ceilings, per-model pools elsewhere. `quota_unit` per pool: requests / tokens / neurons.
- **Failure taxonomy.** Failures classify as `account_dead` (401/402/403), `congested` (429), `transient` (5xx/timeout/transport), `request_fault` (400/413/422 — never counted against the provider), or `entry_missing` (404), scoped to model / pool / account / request. A bare 429 stays model-scoped; a pool-exhaustion signal (Cloudflare error 4006, OpenRouter per-day phrasing) scopes to the pool. OpenRouter's in-flight-budget 402 (`limit_source`) is subclassified TRANSIENT before any account-scope reaction, per the handoff-evaluation amendment.
- **Demotion, observe-only.** A demotion tracker computes what selection WOULD demote (scope, target, until when) and writes it to the attempt ledger and the route record's new `would_demote` field. `reset_at` is observed data where observation exists: Cloudflare demotes "until a successful probe" (`observed_divergent` — its documented 00:00 UTC reset demonstrably did not restore the pool on 2026-11-12/13), OpenRouter's free pool uses the documented daily reset. Half-open bookkeeping (single probe admission, close on success, doubled cooldown on failure) exists at the ledger level. **Selection never reads the tracker in this release** — a test proves a just-429'd, would-demoted model serves the next clean request. Whether demotion may ever enforce is decision **J-2**, still the owner's.
- Tests: 566 → 582, including the TEST-03/04/05/09/13 shapes from the handoff evaluations (TEST-13 replays the Nov 9 census: the first 402 already annotates account scope — convergence in 1 attempt instead of 369).
- **Sequencing:** this branch and v0.7.0 both branch from v0.6.1 main. **v0.7.0 merges first**; this release then merges on top (expected small conflict in the streaming transport-error handler, where v0.7.0's commit guard and this release's attempt recording compose — the recording must stay ahead of the guard's return).
- **Deploy record (2026-11-13, owner's order at the standard gate):** PR #4's content merged to main as `9f811cb6a7e141876da14bc1b0b4f2adbf015281`. The three merge points pre-named in the decision package (\u00a71) were resolved in a local merge of the branch into post-v0.7.0 main — the streaming transport-error handler composed with the attempt recording as its first statement, ahead of the commit guard's return (both behaviors preserved); the version assertion took "0.8.0"; this section sits above [0.7.0]. The merged tree passed the full suite at **616** (600 + 16, no dedup loss). GitHub's own 3-way merge still flagged the pre-resolved hunks (both sides insert at the same anchors relative to the merge base), so the resolved content was committed to main directly and PR #4 was closed with the full explanation in a comment; the branch is retained as archive. Main's tree was verified byte-identical to the tested merge before deploy. CI: run #26 on the PR (pytest, Python 3.12) **SUCCESS** \u2014 read back at this gate, closing the build run's one open verification item \u2014 and run #29 on main **SUCCESS**. Snapshot `snapshots/pre-v080-20261113` (build/src + state + deploy) plus `registry.json.bak-20261113-prev080` and `docker-compose.override.yml.bak-20261113-prev080` precede the change. Image `192.168.0.28:5000/sovereign-router:v0.8.0` (sha256 `61c1f99be7a1...`) built on VM103; gateway recreated via `compose up -d` (the v0.2.1 `:6070` rollback stack untouched). Registry UNCHANGED by this release: md5 `96eaa3c99643e1016508cee16caf23de` byte-identical across GitHub main, VM103 `build/src`, and VM103 `state` (state copy 10001:10001, mode 644). `/health`: ok, version 0.8.0, `registry_models` 81, clients crm/hermes/n8n/openclaw, warn-only emergency switch intact \u2014 and still healthy after the smoke battery. **Live smokes (unique conversation per case, corroborated in `state/routes.jsonl`):** hermes unpinned auto \u2192 200 on the free lane (`openrouter/nemotron-3-ultra-free`, $0.00); openclaw pin of `meta/muse-spark-1.3` \u2192 200 premium (eligible pin still serves post-merge); **J-1 regression** \u2014 an openclaw pin of the DISABLED `google/gemma-4-31b-it` was blocked (`pin_blocked:disabled` in the route row's skipped list), the disabled model did not serve, and the turn fell through to normal selection; a streamed pin of spark-1.3 completed the full SSE with content through the composed handler. **Ledger proof:** `state/quota.db` gained `attempt_events` on first boot; minutes after deploy it already held the smoke attempts as served rows with pool ids (`openrouter-free`, per-model pools) alongside failed attempts recorded from live traffic (outcome counts at check time: 20 served / 2 failed) \u2014 the failure memory is live; `usage_events` keeps flowing as before (29,124 rows total); route rows carry the new `would_demote` field (empty on clean serves, as designed). **Rollback:** point the compose override back to the v0.7.0 image and `compose up -d gateway` (the `attempt_events` table is additive; the old code ignores it). Decision J-2 (whether demotion may ever enforce) remains the owner's, now informed by live `would_demote` data.

## [0.7.0] — 2026-11-12 (deployed 2026-11-12)

Cycle 1 release R1 (safety & integrity), per `docs/SOVEREIGN-ROUTER-NEXT-PLAN.md` §3 and `docs/cycles/release-v0.7.0-decision.md`:

- **Pin hardening (aspect G, ruling J-1).** The pin force-insert is bound to the two hard boundaries: a pin for a **disabled** entry no longer serves it (pre-0.7.0 the force-insert resurrected disabled entries — latent, proven by the v0.6.1 Gemma shaping test's reliance on it), and a pin cannot carry **sensitive** data past the sensitive wall (per-model exclusion, public-lane bar, provider clearance — previously only the exclusion flag was checked). Quality floor, cost order, context fit and provider limits remain pin-overridable. Blocked pins are annotated on the route row as `pin_blocked:<reason>`. Live and `/v1/shadow` now share ONE pin implementation (`apply_pin`), closing the known shadow/live divergence on force-insert cases; the pin test matrix asserts shadow == live on every case.
- **Streaming commit guard (ADD-06).** Once a stream has committed visible output, a transport failure terminates the stream with a typed `stream_interrupted` SSE error event instead of failing over and appending a second model's output to the same stream; the route row records `stream_aborted_after_commit:<entry>` and status 502. Pre-commit failover is unchanged, and the buffered tool-call path is unaffected by construction.
- **Registry resolution integrity (aspect B).** `Registry.get()` is now two-pass and order-independent: an exact id match anywhere beats a model-string match anywhere, and an ambiguous model string resolves to None instead of the first file entry. The loader additionally rejects duplicate (provider, model) pairs. A new invariant battery (`tests/test_registry_invariants.py`) enforces the live registry file's invariants in CI.
- **Registry data.** Price provenance backfilled on all 49 priced entries (`price_source` / `price_verified_at` / `price_confidence`: 38 verified, 2 observed, 9 honestly graded seeded — including `premium/gpt-5.5`, whose notes flag its price as a placeholder). `spend_alert_usd` armed on all 7 premium entries ($10; gpt-5.5 keeps $20). OpenRouter pool facts normalized: the 14 `:free`-pool entries carry `quota_pool_id: openrouter-free` and the shared limits `{rpd: 1000, rpm: 20}` (the 20 RPM pool ceiling is now enforced at selection — a real behavior change, flagged in the decision package); the 7 paid entries carry `quota_pool_id: openrouter-paid`.
- **Deploy preflight tool (aspect A, code-side).** `tools/preflight.py` (stdlib-only, read-only): registry check-file (exists / loads via the gateway's own loader / readable by gateway uid 10001), three-copy md5 compare, /health gate with expected registry size, and the standing rollback notes. No production use yet — it is run by hand at the next deploy gate.
- **Docs.** `docs/ROUTING-PRINCIPLES.md` rule 3 now carries the J-1 pin boundaries.
- Tests: 566 → 600. One existing test changed deliberately: the v0.6.1 Gemma shaping test now enables Gemma in a variant registry (its disabled-pin vehicle is exactly what J-1 removed); the FREE-20 OpenRouter limits assertion follows the normalized pool shape.
- **Deploy record (2026-11-12, owner's order at the standard gate):** PR #3 merged to main as `71dd2b2f5b43504b32a9ca76115e9fd62f3ddd1d` (merge tree identical to the CI-tested branch head; CI run #25 SUCCESS; suite 600). Snapshot `snapshots/pre-v070-20261112` (build/src + state + deploy) plus `registry.json.bak-20261112-prev070` and `docker-compose.override.yml.bak-20261112-prev070` precede the change. Image `192.168.0.28:5000/sovereign-router:v0.7.0` built on VM103; gateway recreated via `compose up -d` (the v0.2.1 `:6070` rollback stack untouched). Registry md5 `96eaa3c99643e1016508cee16caf23de` byte-identical across GitHub main, VM103 `build/src`, and VM103 `state` (state copy owned 10001:10001, mode 644). `/health`: version 0.7.0, `registry_models` 81, clients crm/hermes/n8n/openclaw, warn-only emergency switch intact. **Live smokes (unique conversation per case):** hermes unpinned auto → 200 on the free lane; **J-1 headline proofs** — an openclaw pin of the DISABLED `google/gemma-4-31b-it` did not serve it (route row `pin_blocked:disabled`; the turn fell through to normal selection, per this package's semantics — only sensitive×uncleared-premium pins refuse outright); an openclaw pin of `openrouter/nemotron-3-ultra-free` on a detector-sensitive turn was blocked (`pin_blocked:sensitive_public_lane`; served instead by `groq/gpt-oss-120b`, private-free); a sensitive pin of the uncleared premium `meta/muse-spark-1.3` refused with HTTP 429 (`pin_blocked:sensitive_not_cleared`); an eligible pin of `meta/muse-spark-1.3` served 200; `/v1/shadow` on the disabled-pin case predicted the exact model that served live (shadow == live). Streaming serves intact (spark-1.3 and free-lane auto streamed with content through the new commit guard). One provider-side note: `meta/muse-spark-1.1`'s upstream returned empty 1-token streams twice during the battery — the gateway delivered them faithfully (route rows `output_tokens` 1, no errors, no escalation); 1.1 joins the route-health re-probe radar. **Rollback:** point the compose override back to the v0.6.1 image and `compose up -d gateway` (the old code ignores the new registry fields; no registry rollback required).

## [0.6.1] — 2026-11-12 (deployed)

**Reasoning-effort control + Gemma shaping; `meta/muse-spark-1.3` re-enabled.** Owner approved both the deploy and the re-enable 2026-11-12 ~22:58 MST. Origin: the route-health differential diagnosis (`docs/cycles/route-health-investigation-protocol.md` §5–§6) proved two disabled routes were partly our own fault; decision package in `docs/cycles/fix-package-v0.6.1-decision.md`.

- **Gateway:** `reasoning_effort` added to the client-field allowlist (before v0.6.1 the gateway silently dropped client-sent effort, and Meta defaults an omitted effort to `high` — the documented control was unexercisable). A poison-field guard strips `reasoning_effort` for Google entries (proven HTTP 400 upstream) and for non-reasoning Mistral entries (per-model validation, probed live). Registry `extra_body` profiles win over client-sent values on pinned entries (same merge rule that protects `data_collection=deny`).
- **Registry:** the three `meta/muse-spark-*` entries pin `extra_body.reasoning_effort = "minimal"` (probe-chosen: on 1.3, minimal cut hidden reasoning burn 546 → 85 on a reasoning-shaped prompt and 256 → 28 on a trivial one, answers intact; `low` did not restrain 1.3). The two `google/gemma-4-*-it` entries carry the proven `thinking_config` extra_body shape and **stay disabled** (provider-side 500s/latency). **`meta/muse-spark-1.3` re-enabled** at the minimal-effort profile with a dated registry note — its no-content behavior was a default-effort artifact, not a model fault. Registry: 81 models, 73 enabled (was 72).
- **Landing:** PR #2 (`fix/v0.6.1-effort-and-gemma-shaping`) merged to main as `a50c1c0bb50c7b1da574d262ee3e2496328b2af5`; the re-enable landed as a separate registry commit `b781d569ddf394751116f0595a20195ce2175b34` with the test suite synced in the same commit (enabled-count 72 → 73; disabled-set assertions updated in `test_smoke_providers.py` and `test_effort_and_gemma_shaping.py`).
- **Tests / CI:** full suite on the final main tree: **566 passed** locally (554 baseline + 12 new). GitHub CI run #22 (`43376596679`) on `b781d56`: **SUCCESS** (pytest, 27s).
- **Deploy evidence (VM103):** Proxmox snapshot `pre-v061-20261112` taken before any change; backups `state/registry.json.bak-20261112-prev061`, `build/src/{registry.json,gateway.py}.bak-20261112-prev061`, `deploy/docker-compose.override.yml.bak-20261112-prev061`. Image `192.168.0.28:5000/sovereign-router:v0.6.1` built on VM103; the override's image line points at it (rollback: point the line back to `v0.6.0` and `docker compose up -d gateway`). **Registry md5 `2a5cfdb4f158ed50149d7d780dfa62a9` — byte-identical** in GitHub main `src/registry.json`, VM103 `build/src/registry.json`, and VM103 `state/registry.json` (state copy mode 644, owner 10001:10001).
- **`/health` after deploy:** `status ok`, `version 0.6.1`, `registry_models 81`, clients `crm/hermes/n8n/openclaw`, classifier sidecar ok, alert chain ok.
- **Live smokes** (through the gateway post-deploy; unique conversation per pin; keys injected from the VM103 host via `docker exec -e`, never printed — nginx publishes only on the LAN IP with a client-IP allowlist that excludes VM103 itself, so the unchanged nginx hop was not re-exercised from the box):
  - hermes unpinned (`sovereign-router/auto`): **HTTP 200**, served `openrouter/nemotron-3-ultra-free` (public-only-free) — normal free path intact; routes.jsonl status 200.
  - hermes pinned `meta/muse-spark-1.3`: **HTTP 403 `lane_not_allowed`** — the premium lane is correctly refused for hermes (`premium_allowed: false`); the re-enabled entry sits behind the premium gate exactly as designed.
  - openclaw pinned `meta/muse-spark-1.3`: **HTTP 200**, content "Paris is the capital of France.", **81 completion tokens** — the minimal-effort profile working (vs 256 at Meta's default and 2,046 in the probes that originally disabled it); routes.jsonl premium/200.
  - openclaw pinned `meta/muse-spark-1.1`: **HTTP 200**, content "The capital of Italy is Rome.", 197 completion tokens; routes.jsonl premium/200.
  - `/v1/shadow` for `google/gemma-4-31b-it` and `google/gemma-4-26b-it`: both skipped with reason `disabled` — the Gemma entries remain disabled.

## [0.6.0] — 2026-11-12

The provider-expansion release: registry 15 → 23 models, four new live
providers in one day, two more staged behind owner-held keys.

### Added
- **Cerebras direct** (`cerebras/gpt-oss-120b`, paid, non-sensitive-only):
  ~2,500 tok/s, registry pins `reasoning_effort: low` (the direct API
  honours it; the Hugging Face passthrough did not — that fault closed the
  Nov 11 incident). Trial-tier key limits recorded in the entry.
- **Novita** (`novita/gpt-oss-120b`, paid, non-sensitive-only): cheapest
  gpt-oss-120b route in the registry ($0.05/$0.25 per 1M).
- **Z.ai GLM-4.7-Flash** (`zai/glm-4.7-flash`, public-only-free): genuinely
  $0 per token on Z.ai's pricing page, 200K context, existing key.
- **Google Gemini** (`google/gemini-3.5-flash-lite`, public-only-free):
  AI Studio free-tier key minted in the owner's account; 1M context.
  Runs the Lite model — the flagship `gemini-3.8-flash` timed out on
  probes and `gemini-2.5-flash` is closed to new users.
- Staged (disabled) entries awaiting owner steps: `mistral/mistral-large`,
  `mistral/codestral`, `cloudflare/gpt-oss-120b`,
  `cloudflare/glm-4.7-flash`.
- Client lane policy: hermes, n8n and openclaw gained the
  `public-only-free` lane (crm deliberately unchanged).
- `CHANGELOG.md` (this file) and README/documentation refresh.

### Changed
- Registry test premises updated throughout (counts, the HF Cerebras
  entry's disabled state, the sensitive-exclusion invariant extended to
  cerebras/novita). Suite: 141 tests, all passing.

### Notes
- NVIDIA NIM was probed and not added (free endpoint timed out at 90s);
  its key is stored on the gateway host with the other provider keys.
- Ledger-dollar caveat (2026-11-12): route-log costs are upper-bound
  estimates — the gateway does not read providers' cached-token counts,
  so cache-heavy premium traffic is overstated (~12× on one audited day).
  Provider consoles are the source of truth for spend. Per the owner's
  ruling, no pricing-code change was made.

## [0.5.7] — 2026-11-09

### Added
- Emergency paid-lane switch is now **warn-only** (owner ruling):
  `SOVEREIGN_EMERGENCY_WARN_ONLY=1` — at the $25/day level the gateway
  records an `emergency_switch_warn` alert and never vetoes paid lanes.
  The hard-switch code remains behind the flag.
- Dead-lane alerts: first 401/402/403 (or daily-cap exhaustion) per model
  per day records a `lane_dead` alert through the existing chain.
- Hugging Face provider: `huggingface/deepseek-v4-flash` enabled
  (DeepInfra-parity price, separate prepaid billing pool);
  `huggingface/gpt-oss-120b-cerebras` added (disabled 2026-11-11 after
  the hidden-reasoning fault — see the dots-3 incident doc).

### Fixed
- Groq and Ollama registry corrections (Groq Developer-plan limits and
  real prices; the "Ollama daily cap" was the router's own seed value).

## [0.5.6] — 2026-11-07

### Added
- Paid spillway: free/private lanes first; on exhaustion the cascade
  re-selects paid and premium in strict cost order (gpt-5.5 last),
  relaxing only the sensitive-clearance wall and the per-client premium
  gate.
- SiliconFlow provider (GLM-5.3 enabled, DeepSeek V4 Pro disabled),
  non-sensitive-only.

## [0.5.5] — 2026-11-07

### Added
- Free-first routing order fleet-wide (supersedes the earlier
  premium-rescue design).

[0.6.0]: https://github.com/jmason84/SovereignRouter/releases/tag/v0.6.0
[0.5.7]: https://github.com/jmason84/SovereignRouter/releases/tag/v0.5.7
