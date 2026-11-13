# Sovereign Router — Improvement Loop, Cycle 1 Plan (Stages 1–3)

**Status:** FOR JORDAN'S REVIEW — research, tool choices, and documented update
plans only. No code, registry, or production changes are authorized by this
document; builds start only on Jordan's go, per the charter's production gates.
**Date:** 2026-11-12. **Charter:** `docs/IMPROVEMENT-LOOP.md` (Cycle 0 failures
F-01…F-08; aspects A–G). **Evidence base:** the 2026-11-12 routing research
brief (ADOPT list), the gateway source as deployed at v0.6.0 (registry 81
models / 72 enabled, md5 `d859879d…`), the full production route log
(`routes.jsonl`, 13,927 rows, read-only census 2026-11-12), and fresh read-only
checks run for this plan (registry invariant audit; pin-path probe harness —
both reproduced in the appendix).

## Cross-cutting findings (read these first — they shape several aspects)

1. **The explicit-pin path bypasses most of selection's safety checks.**
   `chat_completions` force-inserts a pinned entry into the candidate list
   when it is not already there (gateway.py:1023–1045). The guard blocks
   resurrection only for keyless providers and — on sensitive turns — for
   `spillway_sensitive_excluded` entries. A live probe against the production
   registry with a mock upstream (appendix, probe P-1) shows a pin today
   **serves**: a sensitive turn pinned to a public-only-free entry, a
   sensitive turn pinned to uncleared paid and premium entries, and a
   **disabled** entry pinned on both plain and sensitive turns. The excluded
   flag is the only check that binds pins. No production route row shows this
   happening organically (0 of 2,429 sensitive rows served on public/excluded
   models) — it is a latent path, not an observed leak. It is also *the*
   aspect-G finding and needs a ruling from Jordan (Decision J-1), because
   `docs/ROUTING-PRINCIPLES.md` rule 3 ("an explicit model pin … is honored
   over the cascade") never scoped pins against the privacy wall or the
   `enabled` flag.
2. **`verified_until` is a vacuous gate.** The schema carries the field and
   selection skips expired entries (`is_verified`, registry.py:131), and
   every entry carries a `probe` block — but **0 of 81 entries set
   `verified_until`**, and nothing in the system ever writes it. Verification
   freshness is entirely manual today. Aspects B and F close this together.
3. **Reasoning tokens are invisible to the router's own records.**
   `_usage_from_response` (gateway.py:237) reads prompt/completion/cached
   tokens only; `RouteRecord` (telemetry.py) has no reasoning-token field.
   The brief's reasoning:visible alert (>~5×) and any per-model reasoning
   ranking are impossible until this is recorded. Aspect D.
4. **Limits are declared per entry but enforced per provider.**
   `check_provider_limits(provider, entry.limits, …)` (quota.py:295) checks
   the *evaluated entry's* limits dict against provider-wide usage. Provider
   `openrouter` carries 2 distinct limits dicts across its 21 entries — the
   shared-pool semantics work by accident of which entry is being evaluated.
   Aspects B (invariant) and E (ledger redesign).
5. **The route log already contains most of the evidence the brief asks us
   to collect** — per-attempt skip reasons (`upstream_429`, `upstream_402`, …),
   validation failure codes, escalation reasons, outcome signals — but
   nothing aggregates it except the never-scheduled `nightly_scoring` stub
   (telemetry.py:161). Aspects E and F are mostly *productizing* analysis of
   data we already write.

---

## Aspect A — Deploy safety

### A.1 Findings

- Both 2026-11-12 deploys moved the registry by hand: local edit → full
  pytest → file copy to VM103 `state/` and `build/src/` → md5 comparison →
  `docker compose … restart gateway` → manual `/health` read + pinned
  serves. Nothing scripted any of it. The phase-2 copy landed
  `state/registry.json` root-owned and unreadable by the gateway (uid
  10001), crash-looping it for ~2 minutes (F-01; CHANGELOG 2026-11-12:
  "an scp that preserved root ownership crash-looped the gateway"). The
  charter's standing rule (verify ownership before restart) is currently
  enforced by memory, not machinery.
- Three copies must agree — local `src/registry.json`, VM103
  `build/src/registry.json`, VM103 `state/registry.json` — and did at both
  deploys (md5-verified manually, after the fact in phase 2's case).
- The compose stack *has* a gateway healthcheck
  (deploy/docker-compose.v05.yml:82–89, polls `/health` every 30 s), but
  `docker compose restart` does not wait on it; the deploy flow never
  consults it. The health gate was a human reading `/health` output.
- Rollback today = dated `.bak` files in the state dir (18 present) +
  Proxmox snapshot; restoring is a manual copy + restart. It has never
  been drill-tested as a timed procedure.
- Two underused safety properties already exist in the code:
  `POST /admin/reload` (gateway.py:650) reloads registry + clients + keys
  with no container restart, and a malformed registry makes the reload
  raise *before* `app.state.registry` is reassigned — the old registry
  keeps serving. A restart with a bad file crash-loops instead.
  Conversely, `GatewayConfig.resolved_registry_path` (gateway.py:166)
  silently falls back to the image-bundled registry if
  `state/registry.json` is missing — a deleted state file would quietly
  serve a stale registry. Pre-flight must assert the state file exists.
- File facts on VM103 today: `state/registry.json` is `10001:10001 644`
  (correct); `state/clients.json` is `root:root 644` (works — the real
  invariant is *readable by uid 10001*, not a specific owner).

### A.2 Tool / approach decision

**Build** a small deploy tool, `tools/deploy_registry.py` (Python, stdlib +
ssh subprocess, same pattern as the soak tooling). **Adopt** two existing
primitives as its gates: `install(1)` for transfers (sets owner/mode
atomically at write time — the F-01 failure class becomes impossible by
construction) and the `/health` payload as the post-change gate.
*Alternatives considered:* Ansible/a config-management layer (skip — one
VM, one file class; machinery outweighs the problem); `docker compose up
-d --wait` as the health gate (evaluate during build — it gates container
health, but the deploy also needs registry-count and served-model
assertions the compose healthcheck can't express; keep as a complement,
not the gate); Portainer stack redeploys (skip — that's the image-deploy
path, not the registry path, and adds a UI dependency to an automatable
flow).

### A.3 Planned update

`tools/deploy_registry.py` with four subcommands, all operating on the
three-copy set (local `src/`, VM103 `build/src/`, VM103 `state/`):

- `preflight`: run the aspect-B invariant tests against the local
  registry (fail = stop); assert local suite-green marker is current;
  read remote state: md5 + owner/mode of both VM103 copies (assert
  readable-by-10001 and that `state/registry.json` **exists** — bundled
  fallback guard); assert the intended Proxmox snapshot name does not
  already exist; print the diff summary (entries added/removed/changed
  vs the live registry).
- `deploy --snapshot <name>`: verify snapshot exists (taken by the
  operator via the runbook step, or fail); write timestamped backups
  `registry.json.bak-<ts>-<tag>` in VM103 `state/` + `build/src/` and
  locally; transfer with `install -o 10001 -g 10001 -m 0644` (state copy)
  and root-owned 0644 (build copy); assert md5 equality across all three
  copies; apply: `docker compose restart gateway` for code+registry
  changes, `--reload-only` flag uses `POST /admin/reload` for
  registry-only diffs; **health gate**: poll `/health` up to 60 s for
  `status: ok` and `registry_models` == expected count; then run the
  aspect-F mini pin battery (changed entries only); **any gate failure
  triggers automatic rollback** (restore backups, restart, re-run health
  gate) and a non-zero exit with the failing gate named.
- `rollback --backup <name>`: scripted restore + the same health gate +
  mini battery on the restored set.
- `verify`: read-only — three-copy md5, `/health`, and a one-page deploy
  state report (current snapshot list, newest backups).

Docs: a registry-deploy addendum in deploy/deploy-runbook.md describing
the tool as *the* path (hand copies deprecated), plus a CHANGELOG entry
per use is replaced by the tool's own deploy log line appended to
`docs/cycles/` per cycle. No gateway code changes in this aspect.

### A.4 Test plan (stage 4 preview)

- Unit tests for the tool's pure parts (diff summary, md5/ownership
  assertion logic, gate state machine) with fixture registries.
- A **drill test** in the local e2e harness (tests/e2e_live.py pattern):
  boot a gateway on a temp state dir, run the tool's apply+health-gate+
  rollback functions against it with a deliberately bad registry
  (unreadable file; wrong model count) and assert rollback restores
  service. This is F-01 encoded as a regression test.
- The tool refuses (tested) when: snapshot missing, local invariants
  fail, state file absent on the target, backup name collides.

### A.5 Live-test plan (stage 6 preview)

- First live use is the next real registry change after the build
  (expected: Cycle 1's own aspect-B/C field backfills), executed at the
  normal deploy gate with Jordan's go.
- One deliberate rollback drill on a no-op registry change (comment-only
  diff) before the tool is trusted for a real change.

### A.6 Risks / open questions

- `--reload-only` changes failure semantics (no process restart; sticky
  store and quota state persist). For registry-only changes that's
  desirable, but the plan treats reload as opt-in per deploy until it
  has a track record. Open question for the build stage: does anything
  else cache registry-derived state that reload misses (provider alert
  levels are recomputed per call — verified in code; governor state is
  usage-based, not registry-based)?
- The tool needs ssh to VM103 and the pve host (snapshot check) using
  the existing key setup — no new credentials.

---

## Aspect B — Registry integrity invariants

### B.1 Findings

A read-only invariant audit was run against the live `src/registry.json`
(81 entries; script and full output in the appendix). What holds and what
doesn't:

**Violations / weaknesses found:**
1. **The F-02 collision class still exists, held together by file
   order.** `openrouter/gemini-3.1-flash-lite`'s upstream model string
   *is* `google/gemini-3.1-flash-lite` — the id of another entry.
   `Registry.get()` (registry.py:161) matches id *or* model string,
   first hit wins; `get(id)` is exact today only because the Google
   entry appears earlier in the file. The existing guard test
   (`test_registry_get_by_id_is_exact_for_every_entry`) asserts exactness
   for the current order, not order-independence. Any future reorder, or
   a second such pair, silently re-opens F-02.
2. **Price provenance is not machine-checkable.** Prices live as numbers;
   provenance lives as prose in `notes`. A heuristic scan (notes naming a
   recognizable source) flags 5 paid entries with no identifiable source
   in their notes: `deepinfra/deepseek-v4-flash`,
   `huggingface/deepseek-v4-flash`, `openrouter/glm-5.3-flash`,
   `huggingface/glm-5.3-flash`, `huggingface/qwen3.8-27b` (the last is
   F-06's unreconciled entry). The heuristic is crude — some of these
   notes may document provenance in other words — which is itself the
   finding: F-04 happened because provenance was prose; it still is.
3. **`verified_until` populated on 0 of 81 entries** (cross-cutting
   finding 2). The loader accepts it; selection honors it; nothing sets
   it. Related: every entry has a `probe` block, but the block is a
   description, not a schedule anything reads.
4. **Per-provider limits inconsistency:** provider `openrouter` has 2
   distinct `limits` dicts across 21 entries ({rpd: 1000} on the free
   entries, empty on the paid ones). Given enforcement reads the
   evaluated entry's dict against provider-wide usage (quota.py:295),
   the shared free-pool cap applies only when a free entry is being
   evaluated. Defensible outcome, accidental mechanism.
5. **Reasoning flag/control incoherence (feeds aspect D):**
   `openrouter/dots-3-note-preview-free` carries a reasoning *control*
   (`reasoning: {enabled: false}` in extra_body) but its `reasoning`
   flag is false; 49 of 54 `reasoning: true` entries carry no control
   shape at all (see D.1).
6. **Premium spend alerting is mostly unarmed:** only 1 of 7
   premium-lane entries sets `spend_alert_usd` (`premium/gpt-5.5`,
   $20). Provider alert levels take the max per provider
   (gateway.py:319), so the gap is masked at provider level — but the
   meta, deepseek, zai, and siliconflow providers have **no** premium
   alert level at all, meaning Muse Spark premium spend can grow
   without ever tripping a spend alert.

**Invariants that hold today (keep asserting them):** no duplicate ids
(loader enforces); no duplicate (provider, model) pairs; no
paid/premium entry priced $0/$0; `spillway_sensitive_excluded` never set
on a free-lane entry; no entry sets `sensitive_cleared` (the cleared set
is empty — sensitive traffic's only cleared lane is private-free, plus
the ruled spillway relaxation); all 9 disabled entries carry non-empty
notes; no enabled entry is past a `verified_until` (vacuously — see 3);
all quality maps cover all 5 task classes; the 4 non-zero-priced
private-free entries are exactly the Groq routes, i.e. the deliberate
owner decision (lane = data posture, prices kept for accounting) — an
invariant must whitelist this by provider + rationale, not blanket-fail;
both NVIDIA entries carry the trial-terms caveat (asserted today by a
one-off test, `test_phase2_nvidia_entries_carry_trial_terms_caveat`);
HF entries use `:deepinfra` backends only (one-off test exists);
upstream model strings shared across providers (4 strings, e.g.
`openai/gpt-oss-120b` × 3 providers) are *intended* (same weights,
different billing pools — ROUTING-PRINCIPLES rule 8) and must stay legal.

### B.2 Tool / approach decision

**Build** the invariants as data-driven pytest tests over the real
registry (the harness exists and CI already runs the suite on every
push), plus one small loader hardening in registry.py. *Alternatives:*
JSON Schema alone (skip — cross-entry invariants like collision classes
and per-provider consistency are not schema-expressible); a
data-quality framework (Great Expectations et al. — skip, heavyweight
for 81 rows); keep one-off tests per incident (status quo — that's how
we got a guard that tests file order instead of the invariant).

### B.3 Planned update

- **`Registry.get()` hardening** (src/registry.py): two-pass lookup —
  exact id match across all entries first; model-string match only when
  exactly one entry carries that string, else return None. Behavior for
  unambiguous pins is unchanged; the F-02 pair resolves deterministically
  regardless of file order.
- **Loader checks** (registry.py `load_registry`): reject duplicate
  (provider, model) pairs at load (today only duplicate ids are
  rejected); reject an entry whose model string equals *its own* id
  pattern anomalies as they are defined in the invariant list.
- **New structured provenance fields** on ModelEntry (loader stays
  backward-compatible; unknown keys already ignored):
  `price_source` (short locator string, e.g. `openrouter:/api/v1/models`
  or `mistral:docs-pricing-table`), `price_verified_at` (ISO date),
  `price_confidence` (`verified` | `observed` | `seeded`). Required by
  validation for paid/premium entries with non-zero prices; a one-time
  backfill populates all 81 entries from their notes (the 5 flagged in
  B.1 get re-researched during backfill, not copied forward on faith).
- **`tests/test_registry_invariants.py`** — the generalized battery,
  data-driven so new entries are checked automatically:
  (a) `get(id)` exactness under entry-order permutation (shuffled
  registry must resolve identically);
  (b) no entry's model string equals another entry's id;
  (c) (provider, model) uniqueness;
  (d) price posture by lane (paid/premium > 0 unless explicitly
  waivered; free lanes $0 except the whitelisted Groq posture, asserted
  by provider with a rationale marker);
  (e) provenance fields present and `price_verified_at` not older than
  the aspect-C cadence for machine-readable providers;
  (f) caveat table — data-driven required-note markers per condition
  (NVIDIA → trial-terms text; HF → backend `:deepinfra` only; Google
  free-tier entries → public-only-free lane; Cerebras entries → trial
  limits present while the trial runs; any future provider condition is
  a row in this table, not a new bespoke test);
  (g) lane/flag coherence — excluded flag only on paid/premium;
  `sensitive_cleared` requires a provenance marker in notes; premium
  entries must set `spend_alert_usd`;
  (h) per-provider limits consistency — one limits dict per provider,
  or an explicit per-entry override marker (resolves B.1.4 by making
  the OpenRouter split a declared pool distinction, not an accident);
  (i) disabled entries must carry a dated reason marker in notes
  (format `DISABLED 2026-11-12:` or equivalent — all 9 current entries
  get normalized during backfill);
  (j) quality maps complete over the 5 task classes (holds today).
- The existing one-off tests (NVIDIA caveat, HF backend, get-exactness,
  deleted-HF-Cerebras) fold into the battery; the smoke suite keeps its
  per-entry deployment-shape assertions.

### B.4 Test plan (stage 4 preview)

The invariant battery *is* the test plan; add negative fixtures: a
synthetic registry violating each invariant must fail its specific test
(one fixture per rule, so a future loosening is a deliberate test edit).
The `get()` change gets unit tests for: exact-id priority, ambiguous
model string → None, unambiguous model string still resolves (client
compat), permutation invariance.

### B.5 Live-test plan (stage 6 preview)

The `get()` change and field backfill deploy through the aspect-A tool
at the deploy gate. Live proof: `/health` registry count unchanged
(81), the aspect-F mini pin battery across a sample of entries from
every provider (including both halves of the F-02 pair, pinned by id),
and a 24 h watch of routes.jsonl for any change in per-model serve
distribution vs the pre-change day (the change must be behavior-neutral
for unambiguous traffic).

### B.6 Risks / open questions

- Backfilling provenance for the oldest entries (pre-Nov entries seeded
  under earlier discipline) may surface more F-04-class errors. That's
  the point, but it can expand scope; the cycle file should timebox
  backfill research and mark anything unresolved `price_confidence:
  seeded` rather than blocking.
- Tightening `get()` could break a client that pins by upstream model
  string relying on first-hit order. The route log shows production
  pins use registry ids (pins recorded by entry id); the build stage
  should grep routes.jsonl `override`/model fields once more before
  deploying the change.

---

## Aspect C — Price truth maintenance

### C.1 Findings

- Registry prices are point-in-time seeds. Two of the three price errors
  found on 2026-11-12 (SiliconFlow at half price, F-04; Novita's
  gpt-oss-120b seeded from HF's catalog, "unverified against Novita's
  own price list") came from trusting a *secondary* source. The charter
  now requires the provider's own source; nothing re-checks after
  deploy day.
- **Machine-readable pricing exists for most of the spend.** Verified
  2026-11-12 from the providers' own surfaces: OpenRouter
  `/api/v1/models` (pricing fields), DeepInfra `/models` (pricing
  fields; reconciled exactly with probe billing for gpt-oss-120b),
  Novita's API pricing (dual-verified against novita.ai/pricing),
  SiliconFlow's own model list, Aion `/v1/models` (public, pricing
  fields), Groq `/models` (pricing fields). Hugging Face exposes
  per-response `usage.estimated_cost` and per-provider published rates —
  but the gateway **discards** provider-reported cost:
  `_usage_from_response` recomputes cost from registry prices, so
  reconciliation data exists at the wire and is thrown away.
- **F-06 is the standing example:** HF `qwen3.8-27b` (`:deepinfra`)
  probe billing did not reconcile with the catalog price; resolution
  waits on the first HF invoice. Its registry price is currently
  indistinguishable from a verified one (see B.1.2).
- **OpenRouter billed rates float:** the registry seed is the catalog
  price, but OR bills at the landed endpoint's rate; phase 2 already
  hit this (`openrouter/glm-5.3-flash` seeded at the *observed billing
  ceiling* because the headline mixes endpoint minima). A drift tool
  that only reads catalog prices will both miss real drift and cry wolf
  on endpoint mix.
- **Page-only providers** (no machine-readable price surface found):
  Mistral (docs pricing table; the Medium 3.5 price exists only in a
  launch announcement — the weakest provenance in the registry), Z.ai
  (official pricing page), Cerebras (Developer rate card; trial terms
  until ≈2026-12-12 change what "price" even means), Meta (pricing
  page; 1.1/1.2/1.3 share one Standard table), DeepSeek direct, OpenAI,
  Google (pricing page; free-tier quotas are the operative numbers).
  Cloudflare is a special case: prices are per-model **Neuron** rates —
  drift means a neuron-rate or free-pool change, not a dollar figure.
  Ollama is plan-priced (no per-token truth to drift).
- Cadence evidence: prices moved under us twice in one day during
  expansion ( discovery, not drift — but the SiliconFlow seed had been
  wrong since 2026-11-07, five days, undetected). The soak-era record
  shows no other price changes in Nov 2–12. Weekly machine checks +
  monthly page checks fit the observed volatility.

### C.2 Tool / approach decision

**Build** `tools/price_check.py` with per-provider fetchers for the
machine-readable sources, comparing against the registry's (new,
aspect-B) `price_source` / `price_verified_at` / `price_confidence`
fields and emitting a drift report; page-only providers get a dated
manual checklist generated from the same tool (URL per entry, from
`price_source`). **Adopt** the existing alert chain for drift alerts
(new `add_alert` kind — no new notification machinery). *Alternatives:*
an aggregator (models.dev / LiteLLM price JSON) as the source of truth
(skip as authority — F-04 is precisely an aggregator being wrong;
aggregators may serve as a *tripwire* that prompts a provider-source
check, never as the value written); scheduled LLM summarization of
pricing pages (skip — unauditable for the one job whose entire point is
provenance).

### C.3 Planned update

- `tools/price_check.py`: fetchers for openrouter, deepinfra, novita,
  siliconflow, aion, groq (API/model-list pricing); HF mode = reconcile
  gateway-side (below) rather than fetch. Output: per-entry
  {registry price, provider price, delta, verdict} + catalog additions/
  removals among *already-registered model families* (new-model
  discovery stays a human/cycle decision). Writes a dated report to
  `docs/cycles/price-check-<date>.md` data (or the cycle workspace) and
  raises `price_drift` alerts through the quota store's alert table
  when a delta exceeds a per-entry threshold (default: any change on a
  top-10-by-volume entry; >5% elsewhere).
- **Provider-reported cost capture** (small gateway change, shared with
  aspect D's telemetry work): when an upstream response's usage carries
  a cost figure (HF `usage.estimated_cost`, OpenRouter `usage.cost`),
  record it on the route row as `provider_cost_usd` (nullable). This
  turns F-06-class reconciliation from "wait for an invoice" into a
  nightly comparison for HF and OR, and gives OpenRouter's floating
  rates an observed distribution (seed vs billed p50/p95) instead of a
  guess.
- F-06 disposition path: with `provider_cost_usd` flowing, one week of
  HF qwen3.8-27b rows settles catalog-vs-billed; until then the entry
  carries `price_confidence: observed` (aspect-B backfill) and the
  cycle file keeps F-06 open with the invoice as the tiebreaker.
- Cadence: weekly run for machine-readable providers (cron on VM103
  alongside the soak tooling pattern — `tools/soak_report.py` is the
  local precedent); monthly page-check list for the page-only set,
  executed as a cycle task, each check updating `price_verified_at`.
  Cerebras gets a dated pre-expiry check tied to the ≈2026-12-12 trial
  end (Jordan's funding decision — see Decisions).
- Registry edits from drift findings go through the normal gate
  (aspect-A tool + Jordan's go); the checker never writes the registry.

### C.4 Test plan (stage 4 preview)

- Fetcher unit tests against recorded provider payloads (fixtures
  captured at build time, keys never required in CI): parse, compare,
  threshold, and report-shape tests; a fixture with a halved price
  (the F-04 shape) must produce a drift verdict.
- Gateway: `_usage_from_response` tests — provider cost captured when
  present, absent → null, registry-recomputed cost unchanged.
- Report golden-file test: a known registry + known provider payloads
  produce the expected drift table.

### C.5 Live-test plan (stage 6 preview)

- First live run is read-only by construction (fetch + report): run it,
  hand-verify three findings against the providers' own pages (one per
  source class), file the report in the cycle folder.
- After `provider_cost_usd` deploys, a 7-day reconciliation report for
  HF + OpenRouter closes or escalates F-06 with data.

### C.6 Risks / open questions

- Provider list endpoints occasionally lag or omit models (NVIDIA's
  catalog lists models the account can't call — the same failure shape
  can appear in pricing lists). The checker treats "registered model
  missing from provider list" as a finding to report, never an
  auto-edit.
- OpenRouter endpoint-mix float may make thresholds noisy; start with
  report-only (no alerts) for OR until the observed distribution is
  known.


---

## Aspect D — Reasoning discipline

### D.1 Findings

- **What exists today is a single structural reflex, and it works where
  it reaches.** `validate_response` (validation.py) returns failure
  codes — `empty_response`, `length_no_content`,
  `tool_args_invalid_json`, `unknown_function:*`, `json_mode_parse_fail`.
  In the non-streamed path (`_json_response`, gateway.py:1245), a
  `length_no_content` failure on an entry flagged `reasoning: true`
  triggers one same-model retry with a doubled budget; any validation
  failure otherwise escalates **once** to the next candidate at
  quality+1 (fallback: ≥ current quality), gateway.py:1263. Production
  evidence from the route-log census: same-model retry fired 107 times;
  escalations by reason: `length_no_content` 88, `json_mode_parse_fail`
  52, `tool_args_invalid_json` 31, `unknown_function:*` ~46; 16 rows
  served a known-invalid answer because no stronger candidate existed
  (`validation_failed_no_escalation`) — logged, as designed.
- **The gaps are specific:**
  1. `empty_response` (finish ≠ length, no content) gets **no** budget
     retry even on reasoning entries — the retry is keyed to
     `length_no_content` only.
  2. The `reasoning` flag is a boolean. There is no effort dimension in
     selection, no per-entry minimum viable budget, and no record of
     how many tokens reasoning actually consumed (cross-cutting
     finding 3). The brief's items #2/#8 (structural escalation;
     reasoning-effort as a routing dimension; alert at
     reasoning:visible >~5×) are unimplementable until profiles and
     telemetry exist.
  3. **Streaming — 77% of production traffic (10,679 of 13,927 rows) —
     gets log-only treatment for text.** Tool calls are buffered and
     validated before commit (and can escalate), but streamed text
     commits at the first chunk; empty/length outcomes are recorded
     via `validate_streamed_text_outcome` and cannot be escalated
     (gateway.py:1501; a spec-stated limit). The census counts **444
     `stream_text_log_only:empty_response` rows** — real empty answers
     served to clients, invisible to the escalation machinery.
  4. Control shapes are a per-entry patchwork (extra_body census,
     appendix): only 6 of 81 entries carry any reasoning control —
     OpenRouter `reasoning: {enabled: false}` ×2, Cerebras
     `reasoning_effort: "low"` ×2, Aion `reasoning_effort: low/none`
     ×2. Provider truths established live in Nov 11–12 probes: HF's
     passthrough **ignores** reasoning controls entirely (the reason
     the HF/Cerebras route was deleted); Cerebras direct honors
     `reasoning_effort` but cannot go below `low`; Aion 3.5's reasoning
     cannot be disabled (low/high/max only) while Aion 2.0 accepts
     `none`; `openrouter/free-router` **400s** if reasoning is disabled
     (the endpoint mandates it); Groq honors `reasoning.enabled=false`
     per the Nov 11 incident doc but **no Groq entry carries the
     control** — and `groq/qwen3.6-27b` was noted at deploy leaking
     `<think>` blocks. 49 `reasoning: true` entries carry no control at
     all, including known pathological ones (NVIDIA Nemotron Lightning
     burned 3,172 reasoning tokens on a trivial probe; Muse Spark 1.3
     returned no content even at 2,048 tokens and ships disabled).

### D.2 Tool / approach decision

**Build in the gateway + registry** (profiles as registry data, policy
in gateway code). The classifier sidecar is the wrong layer (effort
policy is not classification) and per-client config is the wrong owner
(the same model serves all four clients). *Alternatives:* static
extra_body forever (status quo — produced F-07); a learned effort
predictor (skip — brief's evidence bar isn't met, and structural
triggers must land first); suppressing reasoning models from whole task
classes by quality-floor edits (blunt, already partially done by
seeding; keep as a stopgap only).

### D.3 Planned update

1. **Telemetry first** (shared with C.3): record reasoning tokens per
   route row — `completion_tokens_details.reasoning_tokens` where the
   provider returns it, plus provider variants encountered at build
   time — as `reasoning_tokens` on RouteRecord and the usage event;
   null when unreported (never estimated). Add a daily alert
   (`reasoning_ratio`) when an entry's reasoning:visible ratio exceeds
   5× over ≥20 serves, via the existing alert chain.
2. **Per-entry reasoning profiles** — replace the bare boolean's
   *operational* role with a `reasoning_profile` object (the boolean
   stays as a derived/compat field during migration):
   `{control: effort | enabled-flag | none-available | ignored-by-relay,
     effort_default: <provider-shaped value or null>,
     min_budget_tokens: <probe-derived floor>,
     think_leak: bool, evidence: <dated probe ref>}`.
   Seed from F-07 evidence for the known set (Aion pair, NVIDIA pair,
   Cerebras pair, OR pair, Groq gpt-oss pair + qwen3.6 think-leak,
   HF entries = `ignored-by-relay`, Muse Spark 1.1/1.2, free-router =
   reasoning-mandatory); remaining reasoning entries get profiles from
   one probe round (budget ladder 256/1024/4096 on a fixed trivial +
   one real-shaped prompt) run as part of the build stage.
3. **Budget floor at call time:** in `upstream_body`, for entries with
   a profile, the effective max_tokens is at least
   `min_budget_tokens` (still capped by context room). This kills the
   known empty-content class at the source instead of paying a retry
   for it.
4. **Retry symmetry:** the same-model doubled-budget retry extends to
   `empty_response` on profiled reasoning entries (bounded exactly as
   today: once per candidate, not counted as escalation).
5. **Effort as a routing dimension (bounded v1):** a small per-task-class
   effort policy in gateway config (e.g. chat L1–L2 → lowest available
   effort where a control exists; reasoning class → entry default;
   long-context → low) applied at `upstream_body` time by translating
   the entry's control shape; entries with `none-available` /
   `ignored-by-relay` are untouched. No change to candidate *selection*
   in v1 — effort shapes the call, quality floors keep shaping choice.
   (Selection-time use of profiles — e.g. preferring clean-at-budget
   entries as escalation targets — is listed as a v2 follow-up, to be
   judged on the telemetry this aspect starts collecting.)
6. **Streamed-text honesty:** no behavior change is possible post-commit
   (spec limit), but the 444-row class gets a nightly count in the
   aspect-E/F reporting so the size of the un-escalatable class is
   always visible; if it concentrates in specific entries, that's
   profile evidence (min-budget and quality seeding follow).

### D.4 Test plan (stage 4 preview)

- Validation/retry matrix tests on the mock upstream: each failure code
  × {profiled reasoning, unprofiled, non-reasoning} → expected
  retry/escalate/serve behavior, including the new `empty_response`
  budget retry and the one-escalation bound (never two).
- `upstream_body` tests: budget floor applied, capped by room, effort
  policy translation per control shape (three shapes + no-op shapes),
  client-supplied max_tokens below floor raised only for profiled
  entries.
- Telemetry tests: reasoning tokens parsed from each provider shape;
  ratio alert math (threshold + minimum samples).
- Regression: F-07 fixtures — a mock that burns its budget on reasoning
  at 512 and answers at 4096 must be served successfully via the floor
  without an escalation.

### D.5 Live-test plan (stage 6 preview)

- Deploy behind the aspect-A tool; live proof = pin battery (aspect F)
  with deliberately small client budgets against the known pathological
  entries (Aion 3.5-mini, NVIDIA Lightning, Cerebras pair): all must
  return content via the floor where they previously emptied.
- 7-day before/after from routes.jsonl: `length_no_content` +
  `empty_response` escalation counts, same-model retry counts, and the
  first reasoning:visible table per entry. Success = measurable drop in
  empty-content escalations with no latency regression on the chat
  class beyond the budget-floor cost.

### D.6 Risks / open questions

- Budget floors raise per-call token spend on reasoning entries
  (bounded by profile values; the alternative — retries and
  escalations — also costs, and delivers a worse answer later). The
  7-day comparison in D.5 is the arbiter.
- Provider usage shapes for reasoning tokens vary and some providers
  don't report them at all; nulls must stay nulls (no inference), or
  the ratio alert becomes fiction.
- Effort policy per task class is a quality/cost tradeoff Jordan may
  want a say in beyond v1's conservative defaults (see Decisions).

---

## Aspect E — Provider reliability

### E.1 Findings

- **Today the gateway has failover but no memory.** Execution walks the
  candidate list; `FAILOVER_STATUSES = {401, 403, 408, 413, 429, 500,
  502, 503, 504}` (gateway.py:103) — and in fact any status ≥ 400 —
  moves to the next candidate. Timeouts/transport errors skip with
  `upstream_unreachable`. There is **no same-provider retry, no
  backoff, no `Retry-After` handling, and no circuit breaker**: routing
  state is re-derived from scratch every turn, and the only cross-turn
  memory is (a) the quota store's limit windows and (b) `lane_dead`
  *alerts* for 401/402/403 and TPD exhaustion (gateway.py:268), deduped
  to one alert per model per day. **Alerting reacts; routing doesn't.**
- **The census quantifies the cost of no memory** (13,927 rows):
  369 attempts against DeepInfra while it was returning 402 (the
  Nov 9 credit death — each attempt a wasted round-trip before
  failover); 92 attempts against Ollama at 402; 395 attempts against
  dots-3 at 429; 270 against Groq gpt-oss-120b at 429; 214 against
  north-mini-code at 429; 169 against Cloudflare gpt-oss at 429 (the
  neuron-pool wall — a *pool* limit the token-based ledger cannot even
  express, since Cloudflare entries' usage isn't denominated in the
  tokens `window_usage` counts); 105 attempts returning 400 from
  DeepSeek V4 Pro — a *request-fault* class (oversized sensitive turns)
  where demoting the provider would punish the wrong layer.
  Meanwhile the ledger does gate real volume: 10,815 `rate_limited_tpm`
  skips, 2,965 TPD, 682 RPD — pre-emptive demotion has a working base
  to build on.
- **Outcome data for an evidence standard already exists** but is only
  stubbed: `nightly_scoring` (telemetry.py:161) computes per-model ×
  task-class success from route rows and *proposes* quality decrements
  at ≥20 samples / <85% success — never scheduled, never run against
  production data by any job. (Its raw rates need care: spillway
  models look bad by construction — they only serve after free fails —
  e.g. deepseek-v4-pro's 87.7% "bad" share is mostly escalation-context,
  not failure. Any standard must separate upstream-failure rate from
  escalation-context rate.)
- **Flaky-route disabling today is human judgment after the fact**
  (Gemma routes: multi-round probes → disabled with dated notes;
  HF/Cerebras: incident → disabled/deleted). It worked, but it depends
  on someone noticing. F-08's pattern — catalog claims masking live
  behavior (NVIDIA 404s and a 181 s hang→500; Google 500s; Cloudflare
  pool exhaustion) — is exactly what a breaker + evidence standard
  exists to catch mechanically.

### E.2 Tool / approach decision

**Build in the gateway**, extending the QuotaStore (SQLite, already the
durable state) with per-entry breaker state and rolling error counters;
**adopt the brief's patterns** — LiteLLM-style cooldown triggers and
OpenRouter's ~30 s deprioritization, error-classified reactions —
rather than any framework (the brief's supply-chain caution about
gateway-shaped dependencies applies; and our gateway is small enough
that the state machine is a few hundred lines). *Alternatives:*
alert-only status quo (disproven by the 369 dead attempts); adopting
LiteLLM as a sidecar router (skip — replaces the asset we're
improving); log-only offline demotion via nightly job editing the
registry (skip as the primary mechanism — a day is too slow for a dead
account; keep the nightly job for *disable proposals*, E.3.4).

### E.3 Planned update

1. **Error taxonomy** (new module or quota.py section): classify each
   upstream outcome as `account_dead` (401/402/403), `congested` (429),
   `transient` (5xx, timeout, transport), `request_fault` (400/413/422
   — never counts against the provider), `entry_missing` (404 —
   counts against the *entry*, not the provider: the NVIDIA lesson).
2. **Circuit breaker per entry** (provider-level rollup for
   `account_dead`, which is account-shaped): rolling 60 s window;
   open on ≥5 classified failures or >50% failure rate over ≥10
   attempts; cooldown 30 s doubling to a 15 min cap; half-open admits
   one probe request, success closes, failure re-opens. Open entries
   are skipped in `build_candidates` with reason `circuit_open` (and
   the skip is visible on the route row, like every other reason).
   `lane_dead` alerts keep firing as today (first account-dead event
   per model per day) — the breaker adds routing reaction, not a
   second alert path.
3. **Pre-emptive demotion near limits:** when a provider's window
   usage crosses 90% of a declared limit, its entries are demoted in
   sort order (a sort-key penalty, not a skip) so traffic bleeds to
   the next rung before the cliff; at 100% the existing hard skip
   applies. Requires aspect-B's limits-consistency fix so "the
   provider's limit" is well-defined; Cloudflare gets a neuron-proxy
   counter (per-model neuron weights in the registry notes → a small
   table in the tool; if Workers AI responses report neuron usage at
   build-time verification, prefer the reported figure).
4. **Bounded same-entry retry:** one retry, 250–500 ms jittered, only
   for transport errors/timeouts where no response was received (safe:
   nothing was billed or answered); 429 is *never* retried on the same
   entry (fail over + breaker tick, per the brief).
5. **Evidence standard for disable proposals** (nightly job,
   productizing `nightly_scoring`): a route becomes a *disable
   proposal* — written to the cycle log + alert chain, owner-approved,
   never auto-applied — when any of: production upstream-failure rate
   ≥15% over ≥50 serves in 7 days (request-faults excluded); pin
   battery failure in 2 rounds ≥24 h apart (aspect F); account-dead
   continuously >24 h. Proposals carry the underlying counts. Auto-
   disable is **not** proposed in this cycle (Decision J-4 covers the
   demotion authority question separately).

### E.4 Test plan (stage 4 preview)

- Breaker state-machine unit tests (open/half-open/close, backoff
  ladder, per-entry vs per-provider scope for account-dead) on a fake
  clock.
- Taxonomy tests per status class, including the traps: 400 does not
  tick the breaker; 404 ticks the entry only; 402 ticks entry +
  provider rollup.
- Selection integration: `circuit_open` skip appears in shadow output;
  demotion reorders but never empties a candidate list that would
  otherwise serve (demotion must not recreate the Nov 6 dead end —
  if everything is demoted, order still serves).
- Replay test: feed the Nov 9 incident window's outcome sequence
  (reconstructed from routes.jsonl skip reasons) through the breaker
  and assert DeepInfra attempts collapse from 369 to a bounded handful.

### E.5 Live-test plan (stage 6 preview)

- Shadow week: breaker runs in observe-only mode (decisions logged to
  the route row as signals, not enforced) for 7 days; compare would-
  have-opened events against actual outcomes; tune thresholds; then
  enforce at the deploy gate.
- Live proof after enforcement: the next natural provider incident is
  the test — attempts against the dead provider bounded, alert still
  fires once, cascade serves; documented in the cycle log either way.

### E.6 Risks / open questions

- Breakers add stateful behavior to a currently stateless selection;
  a buggy breaker can recreate a policy dead end (the Nov 6 lesson).
  Mitigations: half-open always admits a probe; demotion never skips;
  observe-only week; kill-switch env flag to disable the breaker
  without a deploy.
- Thresholds (5 failures / 50% / 30 s) are starting points from the
  brief's patterns, not measurements; the observe week exists to
  calibrate them against our traffic shape (≈2,000–2,700 rows/day).


---

## Aspect F — Verification harness

### F.1 Findings

- **What exists:** 554 offline tests (CI on every push). The smoke suite
  (tests/test_smoke_providers.py) pins registry *shape and intent* —
  per-entry lanes/prices/flags, client lane lists, shadow-selection
  proofs, pin-serves-that-entry tests through a mock transport.
  tests/e2e_live.py boots a real gateway + classifier sidecar + fake
  upstream over HTTP — but it is a standalone script, not collected by
  pytest and not run in CI. `tools/` contains only soak_report.py.
- **What doesn't exist:** the 2026-11-12 pin batteries (36/36 and 15/15
  pinned serves that verified both expansions) were **ad-hoc agent
  scripts, run once, not saved**. There is no golden set, no nightly
  replay, and no shadow-diff harness. The `/v1/shadow` endpoint
  (gateway.py:730) returns the routing decision with no upstream call —
  the substrate for shadow-before-promote — but nothing replays a
  prompt set through it, and shadow **diverges from live on exactly one
  path**: it replicates candidate selection and the pin *reorder*, but
  not the pin force-insert (cross-cutting finding 1), so shadow cannot
  currently be trusted as a complete pre-promotion oracle for pinned
  traffic.
- **Production traffic shape (for golden-set design), from the census:**
  levels L4 59% / L3 29% / L1 10% / L5 2% / L2 0.5%; task classes infra
  38% / long-context 36% / chat 18% / code 7% / reasoning 1%; 77%
  streamed; clients hermes 63% / openclaw 24% / n8n 12% / crm <1% /
  hermes-voice ~1%. Outcome signals available for scoring without any
  judging: validation failure codes, escalation rate, upstream failure,
  tool-error-next-turn — plus two caveats the harness must respect:
  `repeat_within_60s` fires on 62% of rows (agent tool loops share a
  fingerprint — the signal is too noisy to score with as-is), and
  spillway serves are escalation-context by construction (see E.1).

### F.2 Tool / approach decision

**Build** three repo tools on the existing substrate: the pin battery
as `tools/pin_battery.py`, the golden set as data + a replay runner
(`tools/golden_replay.py`) driving `/v1/shadow` for decision diffs and
a sampled live path for answer checks. **Adopt** the brief's cadence
(nightly replay; shadow-before-promote; canary thinking) without its
platforms — TensorZero-style eval infrastructure is on the brief's own
skip list, and our verifier needs are mostly *deterministic* (JSON
parse, tool-call validity via validation.py itself, exact-fact checks)
rather than judged. Judged scoring stays gated on Jordan's Decision #5
amendment (Decisions J-3).

### F.3 Planned update

1. **`tools/pin_battery.py`** — the Nov 12 methodology, productized:
   for each target entry (default: all enabled), send a pinned request
   with a **unique conversation per pin** (nonce in the first user
   message — the F-03 rule), a per-entry budget floor (from aspect-D
   profiles once they exist; a conservative default ladder until
   then), and assert: served model == pinned id (response header
   `X-Sovereign-Model` + the routes.jsonl row), content non-empty,
   latency recorded. Modes: `--changed-only` (deploy gate, used by
   aspect A), `--full` (weekly), `--entries a,b,c`. Output: dated
   report + exit code. A successful full run is also the
   **verification-freshness writer** for aspect B: results land in the
   quota store as probe records (automation never edits the registry —
   the telemetry module's standing principle); `is_verified` is
   extended at build time to consult probe records when
   `verified_until` is unset.
2. **Golden set** (`tests/golden/` data files, ~180 prompts v1):
   composed to the census mix across client × class × level, with
   deterministic verifiers per prompt: structured/JSON-mode drafting
   (the F-05 failure class, ~15%), tool-call turns including
   tool-error recovery (~20%), long-context extraction with embedded
   checkable facts (~15%), infra/ops Q&A with exact-answer checks
   (~20%), chat drafting (email/press shapes, structural checks only,
   ~15%), reasoning with verifiable answers (~10%), sensitive-marker
   prompts whose *routing* is the assertion (~5%, feeds aspect G).
   Prompts are synthetic (no production prompt text — telemetry never
   stored any, by design).
3. **Replay + shadow-before-promote:** nightly, the runner (a)
   replays the golden set through `/v1/shadow` against the *live*
   registry and diffs decisions against the pinned baseline
   (candidate sets, first choice, skip reasons) — any diff outside a
   declared-change manifest fails the run; (b) live-replays a rotating
   25% sample through the real gateway (cost-bounded: free lanes
   preferred by construction of auto routing; a hard daily token
   budget in the runner) and scores the deterministic verifiers.
   **Registry-change gate** = aspect-B invariants green + shadow diff
   clean (or manifest-declared) + pin battery on changed entries —
   wired into the aspect-A deploy tool as its preflight.
4. **Shadow/live parity fix:** the shadow endpoint gains the same
   pin force-insert semantics as live (or, if Jordan rules pins must
   not resurrect — Decision J-1 — live loses it and shadow stays as
   is). Either way, a parity test asserts shadow and live candidate
   sets match across the golden set's pinned cases. This item is
   explicitly decision-dependent.

### F.4 Test plan (stage 4 preview)

- Battery unit tests against the e2e fake upstream: exact-model
  assertion, empty-content detection, nonce uniqueness (two pins of
  the same entry must not share a conversation key), failure reporting.
- Golden-set self-tests: every prompt's verifier passes against a
  known-good canned response and fails against a canned broken one
  (the F-05 malformed-JSON shape is fixture #1).
- Shadow-diff tests: a fixture registry change with a manifest →
  clean; without → the diff is reported and the run fails.
- Parity test (decision-dependent, F.3.4).

### F.5 Live-test plan (stage 6 preview)

- Battery: first full live run at the next deploy gate; then weekly
  scheduled (cron on VM103, report into the cycle log + alert chain on
  failures).
- Golden replay: nightly shadow replay starts report-only for a week
  (baseline establishment), then becomes a gate input for registry
  changes; live-sample replay starts at 10% and grows to 25% after
  its first clean week.

### F.6 Risks / open questions

- Live replay spends real (mostly free-lane) tokens nightly; the
  runner's budget cap is a hard requirement, and Cloudflare/OpenRouter
  free-pool draws must be counted against the shared pools (the
  battery itself must never be the thing that exhausts a pool before
  production traffic — schedule it in the pool's low-traffic window
  and cap its share).
- Golden sets rot: prompts stop resembling production as clients
  evolve. The census mix should be re-derived quarterly (the census
  script pattern is in this plan's appendix).

---

## Aspect G — Privacy fail-closed audit

### G.1 Findings

- **The ruled behavior, stated precisely** (this is what the audit must
  encode — not an idealized wall):
  - *Normal pass, sensitive turn:* public-only-free entries are always
    skipped (`sensitive_public_lane`); paid/premium entries are skipped
    unless cleared (`sensitive_cleared` on the entry or the provider in
    the client's `sensitive_cleared_providers`) — **no entry and no
    client clears anyone today**, so the cleared set is exactly the
    private-free lane; `spillway_sensitive_excluded` entries are
    skipped in **every** pass (gateway.py:375).
  - *Spillway pass (Jordan's 2026-11-07 ruling):* the clearance wall
    and client premium/lane gates are **deliberately relaxed** — paid
    + premium, non-excluded entries may serve a sensitive turn as the
    last resort instead of a 429. Production record: 516 sensitive
    rows touched the spillway; 290 served paid, 226 premium.
  - *Escalation:* targets are drawn from the already-filtered
    candidate list, so escalation inherits the wall of whichever pass
    built the list.
  - *Sticky:* reuse only reorders within the filtered candidate list;
    a sticky model that is no longer eligible is invalidated.
  - *Shadow:* mirrors selection (minus the pin path — F.1).
  - *Judged sampling:* does not exist (Decision #5 OFF) — nothing to
    inherit, yet.
  - *Empirical baseline (census):* 2,429 sensitive rows, **zero**
    served by a public-only-free or sensitive-excluded model; skip
    reasons show the wall working at volume (`sensitive_not_cleared`
    4,954; `sensitive_spillway_excluded` 4,606;
    `sensitive_public_lane` throughout).
- **The one path the wall does not cover is the explicit pin**
  (cross-cutting finding 1; probe P-1 in the appendix): the
  force-insert guard (gateway.py:1033–1045) blocks only keyless
  providers and excluded entries. Probed live-in-test on 2026-11-12
  against the production registry: sensitive+pin served by a
  public-only-free entry, by uncleared paid, by uncleared premium —
  and a **disabled** entry served when pinned, on plain *and*
  sensitive turns. The existing test coverage proves the excluded-flag
  case only (`test_sensitive_pin_of_excluded_new_entry_never_serves_it`);
  shadow tests prove the auto-selection cases only. Whether a pin
  *should* be able to override is genuinely unruled: ROUTING-PRINCIPLES
  rule 3 honors pins "over the cascade" without addressing the wall or
  `enabled`. Note the practical stakes are asymmetric — pins are how
  the deploy batteries verify routes and how the voice profile pins
  its model, so the fix must preserve pinning *eligible* entries
  exactly as today.

### G.2 Tool / approach decision

**Build** the audit as (a) an offline fault-injection suite in pytest
(the probe harness, productized — MockTransport, real registry, real
selection code) running in CI on every push, and (b) a nightly
**route-log auditor** (the census pattern as a scheduled job) that
scans the day's sensitive rows for lane/model legality and alerts on
any violation — the empirical half that catches what tests can't
foresee. *Alternatives:* one-off manual audit (status quo — the pin
path survived two expansions unexamined); formal verification of the
selection code (skip — the code paths are few and testable; the risk
is unexamined paths, which fault injection addresses directly).

### G.3 Planned update

1. **Fault-injection suite** (`tests/test_privacy_audit.py`), cases
   enumerated as a matrix — client {hermes, openclaw, n8n, crm,
   + the live client set at build time} × data class {sensitive
   (marker payloads per detector family), internal} × mechanism:
   - auto selection (exists in shadow form — port to served-path
     assertions);
   - spillway under forced free exhaustion (mock all free entries
     failing) — non-excluded paid/premium allowed, excluded never,
     public-only-free never;
   - escalation after validation failure on a sensitive turn —
     target must stay inside the sensitive-eligible set;
   - sticky poisoning: internal turn sticks a conversation to a
     public-only-free entry; the same conversation's sensitive turn
     must not serve from it;
   - **pin matrix:** pin × {public-only-free, uncleared paid,
     uncleared premium, excluded, disabled, quality-ineligible,
     context-ineligible} × {sensitive, internal} — expected outcomes
     per Decision J-1's ruling (until ruled, the suite encodes the
     *recommended* ruling — wall and `enabled` bind pins; excluded
     already does — and the pin cases are expected-fail markers that
     turn green when the fix lands, so CI tells the truth about the
     gap instead of hiding it);
   - classifier/sensitivity edge: a sensitive payload at L5 with
     premium pressure; a sensitive payload from crm (whose lanes lack
     public entirely).
2. **Pin-path hardening** (gateway.py, decision-gated): extend the
   force-insert guard so a pinned entry is inserted only if it would
   survive the safety subset of selection for this request —
   `enabled`, key present (existing), not excluded on sensitive
   (existing), not public-only-free on sensitive, cleared-or-private
   on sensitive, governor paid-off (existing). Quality/context/limits
   remain *overridable* by a pin deliberately (an operator pinning a
   model for a big job shouldn't be fought by the floor) — the line
   drawn is safety vs suitability, and it's Jordan's line to confirm
   (J-1).
3. **Nightly route-log auditor** (`tools/privacy_log_audit.py` +
   cron): scans the day's routes.jsonl for sensitive rows; asserts
   served model ∉ public-only-free ∪ excluded (unless the row is a
   spillway serve of a non-excluded paid/premium entry — the ruled
   shape, checked against the registry *as of that day*); asserts no
   sensitive row's served model was disabled in that day's registry;
   any violation → `privacy_audit` alert through the chain + a cycle-
   log entry. Zero violations is the only passing state.
4. The audit's ruled-behavior matrix (G.1) is written into
   `docs/ROUTING-PRINCIPLES.md` as the pin/wall clarification once
   J-1 is ruled — the doc, not folklore, becomes the audit's source.

### G.4 Test plan (stage 4 preview)

The suite is the test plan. Add: a mutation check — deliberately
reordering the force-insert guard's conditions in a scratch copy must
fail specific cases (proves the cases bind to the guard, not to
incidental selection order); registry-fixture variants (a client with
a cleared provider; an entry with `sensitive_cleared: true`) so the
audit still passes when Jordan someday clears someone — the audit
encodes the *rules*, not today's empty cleared set.

### G.5 Live-test plan (stage 6 preview)

- The nightly auditor's first live run covers the full historical log
  (13,927 rows — expected result: zero violations, matching this
  plan's census) and then runs daily.
- After the pin hardening deploys, probe P-1 is re-run against
  production (via the pin battery's sensitive-marker cases): every
  wall case must now fail closed (pin refused → next eligible
  candidate or 429 with the skip reasons on the row), while eligible
  pins (the battery's normal cases) serve exactly as before.

### G.6 Risks / open questions

- If Jordan rules that pins are operator overrides that *should*
  bypass the wall, the audit flips to encoding that — but the
  `enabled` bypass is hard to defend under any ruling (a disabled
  route is the operator's own kill switch; a stale client pin
  resurrecting it silently is how a bad route comes back). The plan
  recommends splitting the ruling: wall = Jordan's call; `enabled`
  binds pins regardless.
- The auditor needs day-accurate registry snapshots to judge old rows
  (a model legal on Monday may be excluded on Friday). The aspect-A
  backup discipline already produces dated registry copies; the
  auditor consumes the newest backup ≤ the row's date.

---

## Recommended execution order

Dependencies first; safety before capability; behavior-changing items
last, on the most evidence.

| # | Work | Depends on | Effort (estimate) |
|---|---|---|---|
| 1 | **A — deploy tool** (preflight/deploy/rollback/verify) | — | Small–Medium |
| 2 | **B — invariants + `get()` hardening + provenance backfill** | A (for its deploy) | Medium |
| 3 | **G — fault-injection suite + nightly log auditor** (pin fix itself waits on J-1) | B's harness patterns | Small–Medium |
| 4 | **F1 — pin battery tool** (+ verification-freshness records) | A, B | Small–Medium |
| 5 | **D — reasoning telemetry, profiles, budget floor, effort v1** | B (fields), F1 (probe ladder) | Medium–Large |
| 6 | **C — price checker + provider-cost capture** | B (provenance fields), D's telemetry change (shared) | Medium |
| 7 | **F2 — golden set + nightly replay + shadow-before-promote gate** | B, D (signals), F1 | Medium |
| 8 | **E — taxonomy, breaker, demotion, disable-proposal job** | B (limits consistency), D/F telemetry, 7-day observe week | Large |

Rationale in one paragraph: A makes every later change safer to ship.
B defines the registry contract that C, D, and F all write into. G's
audit is cheap once B's patterns exist and it freezes the ruled
privacy behavior before D/E start moving execution code. F1's battery
is the verification instrument D's profiles are seeded with. D and C
share one telemetry change, so they sequence together. F2's golden set
scores best once D's reasoning signals exist. E changes live routing
behavior more than everything else combined — it goes last, behind
its observe-only week, with F2's replay as its regression net.

## Decisions that are Jordan's (not the loop's)

- **J-1 — Pin semantics vs the privacy wall and the `enabled` flag**
  (new, from aspect G): may an explicit model pin override (a) the
  sensitive clearance / public-lane wall, (b) `enabled: false`?
  Plan's recommendation: the wall and `enabled` bind pins absolutely —
  as the `spillway_sensitive_excluded` flag already does — while
  quality/context/limits remain pin-overridable. Until ruled, the
  audit suite carries the wall cases as expected-fail.
- **J-2 — Circuit-breaker / demotion authority** (brief decision #4):
  may routing *react* to failures automatically (bounded,
  self-healing cooldowns and demotion), or stay alert-only with
  humans reacting? Aspect E is designed for the bounded version but
  ships observe-only until ruled.
- **J-3 — Carried from the research brief, still open:** (i)
  effective-cost accounting, logging-only variant (record
  cached/reasoning/provider-reported cost fields; no displayed dollar
  figures change) — aspects C/D/F are designed to consume these
  fields if approved, and degrade gracefully if not; (ii) Decision #5
  amendment — a judged sample of **non-sensitive** traffic only (1–5%,
  cap ≈50/day, judge inherits the sensitivity floor) — only aspect
  F2's scoring depth depends on it; the golden set works without it.
- **J-4 — Disable-proposal standard** (aspect E.3.5): confirm that
  flaky-route disabling stays proposal-only (owner approves each),
  as this plan assumes — no auto-disable is built in Cycle 1.
- **Carried, unchanged from the charter:** Cerebras PAYG funding
  before the trial expires ≈2026-12-12 (aspect C's pre-expiry check
  assumes a decision either way); SiliconFlow $0-model probe
  (console check or explicit probe OK); "NROUTER" identification;
  per-task-class effort defaults beyond D's conservative v1 (only if
  Jordan wants a hand in them).

## Decisions log (addendum, appended 2026-11-12)

- **J-1 — RULED (Jordan, 2026-11-12 ~22:20 MST).** Pins are bound
  to the two hard boundaries: **a pin for a disabled model does not
  serve it, and a pin cannot carry sensitive data to an uncleared
  provider.** Everything softer — quality floor, cost order,
  context estimates — remains pin-overridable. Jordan's words: the
  override "stops overriding the two hard boundaries. A pin for a
  disabled model doesn't serve it. A pin can't carry sensitive data
  to an uncleared provider. Everything softer — quality floor, cost
  order, context estimates — a pin still overrides, which is what
  makes pins useful for testing and for the voice stack."
  Consequences: aspect G's fix is unblocked on its full scope (the
  G.6 sub-question — pulling `enabled`-binding forward into B — is
  moot; the whole boundary is ruled, not a subset); the aspect-G
  audit suite promotes its wall and disabled-pin cases from
  expected-fail to required-pass; aspect F.3.4's shadow/live pin
  divergence is now decidable against a ruled behavior. J-2, J-3
  and J-4 remain open. Evaluation record:
  `docs/cycles/cycle-01-handoff-v3-delta-evaluation.md`. (The Drive
  copy of this plan predates this addendum and is stale on J-1;
  this repo copy governs.)

## Appendix — evidence index

- **Registry invariant audit** (aspect B/D): read-only Python audit of
  `src/registry.json` run 2026-11-12 — counts, collision classes,
  price-posture and provenance heuristics, flag coherence, limits
  consistency, quality-map completeness, reasoning/extra_body census.
  Headline outputs are quoted in B.1/D.1; the audit is re-runnable and
  becomes the seed of `tests/test_registry_invariants.py`.
- **Route-log census** (aspects D/E/F/G): read-only aggregation of
  VM103 `state/routes.jsonl` (13,927 rows) run 2026-11-12 — status/
  client/level/class mix, sensitive-row served lanes (0 public/
  excluded serves; 516 spillway touches), per-model upstream-failure
  attempt counts, skip-reason histogram, signal and escalation-reason
  histograms, bad-outcome rates. Quoted throughout.
- **Probe P-1 (pin force-insert)** (aspects F/G): in-process gateway
  app (production registry + Nov-12-intent clients + recording mock
  upstream, the smoke suite's own builder) — the 6 pinned cases:
  (1) sensitive + pin `openrouter/nemotron-3-ultra-free`
  (public-only-free) → **served**; (2) sensitive + pin
  `deepinfra/gpt-oss-120b` (paid, uncleared, not excluded) →
  **served**; (3) sensitive + pin `meta/muse-spark-1.1` as openclaw
  (premium, uncleared, not excluded) → **served**; (4) sensitive +
  pin `siliconflow/glm-5.3` (excluded) → **not served** (failover to
  groq/gpt-oss-120b); (5) plain + pin `google/gemma-4-31b-it`
  (disabled) → **served**; (6) sensitive + pin
  `google/gemma-4-31b-it` (disabled, public-only-free) → **served**.
  Re-runnable; becomes `tests/test_privacy_audit.py`.
- **Code references:** gateway.py — build_candidates :339 (excluded
  check :375; sensitive wall :400), build_spillway_candidates :440,
  upstream_body :519, health :570, admin_reload :650, shadow :730,
  chat_completions :882 (pin force-insert :1023), _json_response :1173
  (reasoning retry :1245; escalation :1263), _stream_response :1340
  (streamed log-only outcomes :1501), FAILOVER_STATUSES :103,
  DEAD_LANE_STATUSES + note_lane_dead :261–277, _usage_from_response
  :237. registry.py — ModelEntry :80, get() :161, load_registry :181.
  quota.py — check_provider_limits :295. telemetry.py — RouteRecord
  :37, nightly_scoring :161. validation.py — validate_response,
  validate_streamed_text_outcome. clients.py — ClientPolicy (lanes,
  premium_allowed, sensitive_cleared_providers).
- **Documents:** charter `docs/IMPROVEMENT-LOOP.md`; research brief +
  full report (Drive project folder; local copies in the goal
  workspace `files/`); `docs/ROUTING-PRINCIPLES.md` (rule 3 = pin
  precedence; rule 8 = same weights, multiple pools); CHANGELOG
  2026-11-12 entries (F-01 deploy note); deploy/docker-compose.v05.yml
  (healthchecks :82); deploy/deploy-runbook.md (snapshot discipline).

*End of Cycle 1 plan — stages 1–3. Stages 4–7 execute per aspect, in
the order above, each closing with its own failure-log entries in this
cycle's record.*
