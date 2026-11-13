# Release v0.7.0 — Decision Package: Safety & Integrity (Cycle 1, R1)

- **Date:** 2026-11-12 (build run, evening MST)
- **Status:** **DEPLOYED 2026-11-12** (owner's order: "merge PR# and
  deploy 0.7.0", given after the UnoRouter consent re-check the same
  evening). PR #3 (`feature/v0.7.0-safety-integrity`) marked ready and
  merged to main as `71dd2b2f5b43504b32a9ca76115e9fd62f3ddd1d`; full
  suite **600 passed**; CI run #25 SUCCESS. Snapshot
  `snapshots/pre-v070-20261112` + `.bak-20261112-prev070` backups taken
  first. Image `192.168.0.28:5000/sovereign-router:v0.7.0`; gateway
  recreated via `compose up -d`; registry md5
  `96eaa3c99643e1016508cee16caf23de` identical across GitHub main,
  build, and state. `/health` reports 0.7.0 / 81 models / all four
  clients. Live J-1 proofs passed: `pin_blocked:disabled` (disabled
  Gemma pin did not serve), `pin_blocked:sensitive_public_lane`
  (sensitive turn kept off the public lane), sensitive×uncleared-
  premium pin refused HTTP 429 (`pin_blocked:sensitive_not_cleared`),
  eligible pin served, shadow == live on the disabled-pin case. Full
  evidence in `CHANGELOG.md` [0.7.0]. Rollback remains available
  (override → v0.6.1 image + `compose up -d`).
- **Origin:** `docs/SOVEREIGN-ROUTER-NEXT-PLAN.md` §3 (release R1) and
  `docs/cycles/cycle-01-plan.md` aspects A, B, G, under ruling **J-1**
  (2026-11-12): pins are bound to the two hard boundaries — entry
  enabled, and the sensitive wall — while quality floor, cost order,
  context estimates and limits remain pin-overridable.

---

## 1. What changed — diff vs GitHub main (v0.6.1)

### `src/gateway.py` (VERSION 0.6.1 → 0.7.0)

1. **Pin hardening (aspect G / J-1).** New `pin_safety_reason()` +
   `apply_pin()` closures inside `create_app`, called by BOTH the live
   handler and `/v1/shadow` (one implementation — the pre-0.7.0 shadow
   only reordered a pin already in candidates and diverged from live
   on every force-insert case; that divergence is closed). The old
   live force-insert checked only lane membership, the governor
   paid-off veto, provider-key presence, and the
   `spillway_sensitive_excluded` flag. It did **not** check
   `entry.enabled` (a pin resurrected disabled entries — latent;
   the v0.6.1 Gemma shaping test unknowingly relied on it) and did
   **not** check the rest of the sensitive wall (a sensitive pin to an
   uncleared paid/premium entry, or to a public-lane entry, was
   force-inserted). Both holes are closed. Blocked pins are annotated
   on the route row as `pin_blocked:<reason>` in live and shadow.
   Sticky reuse still outranks pins in both paths (unchanged).
2. **Streaming commit guard (ADD-06).** In `_stream_response.produce()`:
   `committed` is hoisted before the try so the transport-error
   handler can see it. On `httpx.TimeoutException`/`TransportError`
   with `committed == True`, the producer records
   `stream_failed_after_commit` + signal
   `stream_aborted_after_commit:<entry>`, writes the route record with
   status 502 / `upstream_failure`, yields one typed SSE error event
   (`code: stream_interrupted`), and returns — no second model is
   attempted. With `committed == False` the existing failover is
   unchanged. The tool-call path is safe by construction (chunks are
   buffered until validation, so `committed` stays False there).

### `src/registry.py`

3. **`Registry.get()` is two-pass and order-independent** (aspect B.3
   / F-02): exact id match across all entries first; a model string
   resolves only when exactly one entry carries it, else None.
4. **Loader rejects duplicate (provider, model) pairs** (previously
   only duplicate ids were rejected).
5. **ModelEntry gains four optional fields** — `price_source`,
   `price_verified_at`, `price_confidence`, `quota_pool_id` (all
   default None; the loader's known-fields filter means older code
   ignores newer registry files and vice versa — rollback-safe).

### `src/registry.json` (data only; no price, quality, lane, or enabled changes)

6. **Price provenance backfilled on all 49 priced entries.** Grades:
   **38 verified** (provider's own published source: DeepInfra's
   `/v1/openai/models` pricing fields — re-read live during this build
   for the DeepSeek-V4-Flash rate, $0.09/$0.18 exact; OpenRouter
   `/api/v1/models`; Mistral's docs table; Meta's pricing page;
   SiliconFlow's own list; Novita dual-verified; Z.ai's official page;
   Groq's docs/models fields), **2 observed** (`huggingface/
   deepseek-v4-flash` — pass-through billing observed by probe,
   rate re-confirmed against DeepInfra's listing; `openrouter/
   glm-5.3-flash` — registered figure is the billed ceiling, confirmed
   by two probe billings and OpenRouter's endpoint pricing), **9
   seeded** (graded honestly, NOT upgraded without evidence:
   `premium/gpt-5.5` — its own notes flag the price as a placeholder;
   `siliconflow/deepseek-v4-pro` — SF page vs probe conflict
   unresolved; `siliconflow/deepseek-v3.2` — endpoint-sourced, SF's
   own page unsurfaced; `huggingface/qwen3.8-27b` — F-06, probe
   billing did not reconcile; `novita/gpt-oss-120b` — HF-catalog
   seeded; `ollama/gpt-oss-120b`, `deepseek/deepseek-v4-pro`,
   `zai/glm-5.3`, `cerebras/gpt-oss-120b` — carried without a dated
   first-hand source read in the record). The aspect-C price checker
   (release R5) is the mechanism that re-verifies and upgrades these.
7. **`spend_alert_usd` armed on all 7 premium entries** (aspect B.3(e)
   gap was 1/7): $10 on the six unarmed entries; `premium/gpt-5.5`
   keeps its $20.
8. **OpenRouter pool normalization** (aspect B / NEW-01): the 14
   `:free`-pool entries (13 `:free` models + `openrouter/free-router`)
   carry `quota_pool_id: "openrouter-free"` and identical limits
   **{rpd: 1000, rpm: 20}**; the 7 paid entries carry
   `quota_pool_id: "openrouter-paid"` with limits untouched.

### `tools/preflight.py` (new; aspect A code-side basics)

9. Stdlib-only, read-only preflight CLI: `check-file` (registry copy
   exists, loads via the gateway's own loader, and is readable by
   gateway uid 10001 — the crash-loop invariant), `compare` (md5
   byte-identity across the three registry copies), `health` (poll
   /health until status ok AND `registry_models` == expected),
   `rollback-notes` (the standing snapshot/.bak/compose procedure).
   **Deliberately staged:** the Cycle 1 plan's full
   `tools/deploy_registry.py` (deploy/rollback automation) is broader
   than this release's scope; the preflight checks ship first and are
   run by hand at the deploy gate. No production use in this build.

### `docs/ROUTING-PRINCIPLES.md`

10. Rule 3 now states the J-1 pin boundaries (G.3.4 doc clarification).

### Tests (566 → 600)

- `tests/test_pin_hardening.py` (new, 10): the full pin matrix —
  disabled pin plain + sensitive, sensitive×excluded, sensitive×
  uncleared paid, sensitive×uncleared premium (refuses 429 rather
  than leaking or downgrading — explicit-premium semantics),
  sensitive×public-lane, eligible pins serving (paid plain, private-
  free sensitive, public plain), quality-ineligible pin still serving
  (overridability). Every case asserts shadow would_route_to == live
  served model (or both agree on no-route).
- `tests/test_stream_commit_guard.py` (new, 2): mocked mid-stream
  transport failure AFTER text chunks (client keeps model A's partial
  output + the typed error event; model B is never called; route row
  carries the signal and status 502) and BEFORE any chunk (failover
  serves the next candidate in full).
- `tests/test_registry_invariants.py` (new, 12): get() two-pass
  semantics incl. the live F-02 pair under permutation; loader
  duplicate-pair rejection; the live-file battery (unique ids/pairs,
  price posture by lane with the Groq metered exception, provenance
  rule, premium alerts, exclusion-lane sanity, OpenRouter pool shape,
  disabled entries carry dated DISABLED notes, complete quality maps,
  NVIDIA/HF posture caveats).
- `tests/test_preflight.py` (new, 10): pure-part coverage of the
  preflight tool (hash compare, file report incl. the gateway-uid
  readability failure shape, health verdict, poll loop with fake
  clock/fetcher).
- `tests/test_smoke_providers.py`: the FREE-20 OpenRouter limits
  assertion updated to the normalized pool shape {rpd: 1000, rpm: 20}.
- `tests/test_effort_and_gemma_shaping.py`: the Gemma
  `reasoning_effort` test now enables Gemma in a variant registry —
  its original vehicle (pinning the disabled entry) is exactly what
  J-1 removes; the shaping assertions are unchanged.
- `tests/test_paid_spillway.py`: version-shape assertion 0.6.1 →
  0.7.0 per that test's convention.

## 2. Behavior change in plain terms

- A client pinning a **disabled** model by name now gets normal
  auto-routing (or a 429 if nothing else is eligible) instead of the
  disabled model. Nothing in the fleet pins disabled models in
  production traffic (route-log census: zero organic occurrences).
- A **sensitive** request pinning an uncleared, excluded, or
  public-lane model no longer sends the sensitive prompt to it; the
  turn routes as if unpinned (premium pins refuse outright under
  explicit-premium semantics). Blocked pins are visible on the route
  row as `pin_blocked:<reason>`.
- A stream whose upstream dies **after** visible output now ends with
  an explicit interruption error instead of silently continuing in a
  second model's voice. Streams that die before output fail over as
  before.
- **OpenRouter `:free` pool:** the shared 20 RPM ceiling is now
  enforced at selection (previously only the 1,000 RPD was). At
  free-pool saturation the router will skip to the next candidate
  sooner rather than eat a 429 upstream. This is the one change that
  can alter ordinary (unpinned, non-streaming) routing.
- `/v1/shadow` answers for pinned requests now match live exactly,
  including force-insert cases.

## 3. Where the code contradicted the plan documents

1. **Plan invariant B.3(b) cannot hold as written.** "No entry's
   model string equals another entry's id" is violated BY DESIGN by
   the F-02 pair (`openrouter/gemini-3.1-flash-lite`'s model string
   is the Google entry's id) — and that pair is legitimate. The
   battery therefore enforces **deterministic resolution under
   permutation** (plus the known pair pinned in a test), not absence
   of collision. The plan text should be amended to match.
2. **The aspect-A deliverable is staged, not complete.** The plan
   specifies `tools/deploy_registry.py` with deploy/rollback
   automation; this release ships the read-only preflight checks
   only (the brief's scope). The automation remains Cycle 1 work.
3. **The v0.6.1 shaping test encoded the pre-J-1 pin behavior** (it
   relied on force-inserting a disabled entry and said so). J-1
   supersedes it; the test was re-vehicled onto an enabled-variant
   registry rather than deleted, and the interaction is recorded
   here and in the test itself.

## 4. Deploy gate (owner decisions)

Deploy is NOT part of this build. At the gate, the decisions are:

1. **Deploy v0.7.0 as the next production version** (image build on
   VM103, snapshot `pre-v070-<date>`, .bak copies, three-copy registry
   compare via the new preflight tool, health gate on 81 models).
   Rollback: compose override back to v0.6.1 + restore .bak registry
   copies; the registry's new fields are ignored by v0.6.1 code, so a
   code rollback does not require a registry rollback.
2. **Accept the OpenRouter 20 RPM selection enforcement** knowing it
   can shift free-pool routing at saturation (it only ever moves a
   turn to the next eligible candidate; it never refuses a turn).
3. Nothing else is required: no key changes, no client changes, no
   price changes, no enable/disable changes in this release.

**Sequencing note:** release R2 (v0.8.0, pool ledger) is built on its
own branch from the same main. **This release merges first**; R2's
package states the merge order and its own follow-on decisions.
