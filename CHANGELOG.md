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

## [0.7.0] — 2026-11-12 (built on branch; NOT deployed — awaiting the owner's deploy decision)

Cycle 1 release R1 (safety & integrity), per `docs/SOVEREIGN-ROUTER-NEXT-PLAN.md` §3 and `docs/cycles/release-v0.7.0-decision.md`:

- **Pin hardening (aspect G, ruling J-1).** The pin force-insert is bound to the two hard boundaries: a pin for a **disabled** entry no longer serves it (pre-0.7.0 the force-insert resurrected disabled entries — latent, proven by the v0.6.1 Gemma shaping test's reliance on it), and a pin cannot carry **sensitive** data past the sensitive wall (per-model exclusion, public-lane bar, provider clearance — previously only the exclusion flag was checked). Quality floor, cost order, context fit and provider limits remain pin-overridable. Blocked pins are annotated on the route row as `pin_blocked:<reason>`. Live and `/v1/shadow` now share ONE pin implementation (`apply_pin`), closing the known shadow/live divergence on force-insert cases; the pin test matrix asserts shadow == live on every case.
- **Streaming commit guard (ADD-06).** Once a stream has committed visible output, a transport failure terminates the stream with a typed `stream_interrupted` SSE error event instead of failing over and appending a second model's output to the same stream; the route row records `stream_aborted_after_commit:<entry>` and status 502. Pre-commit failover is unchanged, and the buffered tool-call path is unaffected by construction.
- **Registry resolution integrity (aspect B).** `Registry.get()` is now two-pass and order-independent: an exact id match anywhere beats a model-string match anywhere, and an ambiguous model string resolves to None instead of the first file entry. The loader additionally rejects duplicate (provider, model) pairs. A new invariant battery (`tests/test_registry_invariants.py`) enforces the live registry file's invariants in CI.
- **Registry data.** Price provenance backfilled on all 49 priced entries (`price_source` / `price_verified_at` / `price_confidence`: 38 verified, 2 observed, 9 honestly graded seeded — including `premium/gpt-5.5`, whose notes flag its price as a placeholder). `spend_alert_usd` armed on all 7 premium entries ($10; gpt-5.5 keeps $20). OpenRouter pool facts normalized: the 14 `:free`-pool entries carry `quota_pool_id: openrouter-free` and the shared limits `{rpd: 1000, rpm: 20}` (the 20 RPM pool ceiling is now enforced at selection — a real behavior change, flagged in the decision package); the 7 paid entries carry `quota_pool_id: openrouter-paid`.
- **Deploy preflight tool (aspect A, code-side).** `tools/preflight.py` (stdlib-only, read-only): registry check-file (exists / loads via the gateway's own loader / readable by gateway uid 10001), three-copy md5 compare, /health gate with expected registry size, and the standing rollback notes. No production use yet — it is run by hand at the next deploy gate.
- **Docs.** `docs/ROUTING-PRINCIPLES.md` rule 3 now carries the J-1 pin boundaries.
- Tests: 566 → 600. One existing test changed deliberately: the v0.6.1 Gemma shaping test now enables Gemma in a variant registry (its disabled-pin vehicle is exactly what J-1 removed); the FREE-20 OpenRouter limits assertion follows the normalized pool shape.

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
