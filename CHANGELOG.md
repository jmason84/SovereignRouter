# Changelog

All notable changes to the Sovereign Router gateway are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) — the gateway
reports its version at `GET /health` (`version` field) and in the
`X-Sovereign-*` response metadata. The routing *policy* history lives in
`docs/ROUTING-PRINCIPLES.md`; incident detail in `docs/incidents/`.

## [Unreleased]

### Added — v0.10.10 PII detectors, log-only by default (2026-11-28; gateway VERSION 0.10.10) — OWNER POLICY GATE

Job ROUTER-STRESS-20261128. The stress matrix confirmed OPERATIONS-REVIEW-2026-11-11 item 8 ("Sensitivity scan has no SSN / payment-card detectors — owner policy decision") on the live v0.10.3 gateway and the v0.10.8 branch: a fake SSN plus a test card number classified `internal` and was first-routed to a **public-only-free** lane (`cloudflare/gpt-oss-120b` in the local stack).

- `sensitivity.scan_pii_messages`: `pii_ssn` (hyphenated NNN-NN-NNNN, SSA never-issued blocks excluded) and `pii_payment_card` (13–19 digits, Luhn-valid AND a Visa/Mastercard/Amex/Discover prefix). Names only are ever logged.
- **Default OFF (log-only):** routing is unchanged; the route row gains a `pii_log_only:<names>` signal so the false-positive rate on real Hermes/OpenClaw/n8n traffic can be measured first.
- **`SOVEREIGN_PII_DETECTORS=1`:** a match promotes the turn to `data_class=sensitive` (public lanes refused, pins included). Trade-off: those turns leave the free public lanes and may use the paid spillway. Turning it on is Jordan's decision.
- Tests: `tests/test_v01010_pii_detectors.py` (26, incl. 16 negative shapes: phone, order IDs, dates, IPs, SHAs, UUIDs, never-issued SSN blocks, Luhn-invalid cards). Scan cost ≈0.15 s per 2.6 MB of digit-heavy text. Full suite 1091 passed (on top of v0.10.9).

### Fixed — v0.10.9 stress-test ingress hardening (2026-11-28; gateway VERSION 0.10.9)

- Full stress-test report (live 0.10.3 vs branch vs fix matrices, Hermes/OpenClaw runs, live load ramp 1-40, provider-down scenarios, open owner decisions): `docs/STRESS-TEST-2026-11-28.md`.

Job ROUTER-STRESS-20261128 (Jordan: "Test, retest, and keep testing until solid … Try to break it and fix it" / "Stress test it"). Every item below was reproduced against the LIVE v0.10.3 gateway (fingerprint-matched to commit 1133f38) and against the v0.10.8 branch in a local stack; full results in `docs/STRESS-TEST-2026-11-28.md`. Not deployed.

- **500s on malformed bodies are now 400s.** A non-object `messages` item (`["hi"]`, `[null]`) raised AttributeError; a non-numeric `max_tokens` raised ValueError; a lone UTF-16 surrogate (`"\ud800"`, legal JSON) raised UnicodeEncodeError in the sticky hash and would have crashed every upstream call. `_normalize_chat_body` (both `/v1/chat/completions` and `/v1/shadow`) now validates message objects/roles/content types, `max_tokens`/`max_completion_tokens` (positive integers), and scrubs lone surrogates to U+FFFD. Text parts whose `text` is not a string are ignored instead of crashing the text join.
- **Negative `max_tokens` no longer routes to PAID.** `max_tokens: -5` produced a negative cost estimate, so a paid model (live: `mistral/mistral-medium`) looked cheaper than every free one. Now a 400.
- **Wrongly-typed params no longer walk the fleet onto paid/premium.** `temperature: "hot"` 400'd at every upstream until the premium last resort (whose temperature guard strips the field) served it: live, the **crm client (premium_allowed=false) was served by premium/gpt-5.5** after ~14 s. `response_format: "json"` ended on a paid Claude; `content: 12345`, `content: [1, null]` and bad `tools` ended in a misleading 429 after 11–23 s. Now 400 at ingress with no upstream call: `temperature`/`top_p`/`presence_penalty`/`frequency_penalty` must be numbers, `tools` a list of `{function: {name}}` objects, `response_format` an object, `content` a string, a list of part objects, or null (null not allowed for user/system/developer), and `role` one of system/developer/user/assistant/tool/function.
- **Unknown `reasoning_effort` values are dropped, not forwarded.** `"bogus"` 400'd at the free upstreams and was served by paid `mistral/ministral-8b`. Known values (none/minimal/low/medium/high/xhigh/max) are lower-cased and kept; anything else is removed so the provider default applies.
- **`stream: "false"` (a string) no longer means streaming on.** Strings are read as `"true"`/other; non-bool values are coerced.
- **Streaming: an in-stream error before any content now fails over.** Live (OpenClaw JSON-only prompt, `groq/gpt-oss-120b`): Groq sends `tool_use_failed` as a `data: {"error": …}` event after a role-only chunk. The role chunk "committed" the stream (the code committed at the first non-tool chunk, not the first *content* chunk as the spec says), so OpenClaw got "LLM request rejected: Tool call validation failed" instead of the next model's answer. Pre-content chunks are now held until the first content/tool chunk; an error event before that is a failed attempt (`upstream_stream_error`, signal `stream_error_before_commit:<model>`) and the turn fails over. After content has been sent, behaviour is unchanged (no second model spliced into one stream).
- **Diagnostics: `sensitive_match_shapes` on route rows.** Each assignment-style credential hit records `<role>:<noun>=<shape>`, where the shape is the value's first 12 characters reduced to character classes with run lengths (`agent:main:main` → `a5:a4:a~`). Never the value. Reason: on live v0.10.3, which already carries the v0.10.4 session-key exclusion, **227 of 243 OpenClaw turns in the last 24 h were still flagged `credential_assignment`**, which restricts OpenClaw to `groq` private-free. OpenClaw's workspace prompt files, skills and memory files do not trip the detector (checked on VM102 with shapes only), so the trigger is in runtime-injected text. One day of route rows with this field will name it.
- Tests: `tests/test_v0109_stress_hardening.py` (63; 47 fail on the v0.10.8 base, the stream and shape tests fail before their fixes). Full suite 1065 passed. `tests/conftest.py`'s fake upstream ignores non-string text parts.

### Added — v0.10.8 OrcaRouter + Kilo keys, models switched off (2026-11-27; registry/config only, gateway VERSION stays 0.10.7)

Job ROUTER-KEYS-20261127. Jordan's go 2026-11-27 23:39 MST: "store both keys in the vault and VM103, add the models switched off, turn on OrcaRouter zero data retention, Kilo non-sensitive only".

- **Keys:** installed on VM103 as `secrets/orcarouter.key` and `secrets/kilo.key` (mode 600, gateway uid), following the key-file convention in `deploy/deploy-runbook.md`; the gateway does not use an env file. The secrets dir was backed up first to `secrets.grok-bak-20261127-keys/`. The running gateway reads keys at start and on `POST /admin/reload`; neither was done, and the entries are disabled anyway.
- **Provider config:** `PROVIDER_BASE_URLS` gains `orcarouter` → `https://api.orcarouter.ai/v1` and `kilo` → `https://ai-gateway.kilo.ai/api/v1` (verified 2026-11-27 against each public `/models` endpoint and Kilo's docs; `https://api.kilo.ai/api/openrouter` serves the same catalog).
- **Registry:** new DISABLED `orcarouter/deepseek-v4-flash-free`. The existing disabled `orcarouter/deepseek-v4.1-flash-free`, `orcarouter/glm-5.3-flash-free`, `kilo/beam-free` (`reflection/beam-free`) and `kilo/auto-free` move from PENDING-KEY to key-installed. 111 entries, 78 enabled (unchanged).
- **Sensitive wall:** every OrcaRouter and Kilo entry is `public-only-free`, which is how the registry marks non-sensitive only. The gateway refuses public-lane entries for sensitive turns (`sensitive_public_lane`), pins included, and the paid spillway never considers them. `spillway_sensitive_excluded` is not set, because the registry invariant reserves it for paid and private lanes. **OrcaRouter ZDR is NOT on** (it needs a DPA signature that has not been approved), so OrcaRouter stays non-sensitive and can be relaxed only after ZDR is confirmed on, by Jordan's ruling. Kilo `reflection/beam-free` has `mayTrainOnYourPrompts: true`.
- **$0 test calls (2026-11-27):** `kilo/beam-free` is READY-TO-ENABLE (200, 1.2s, correct reply, cost 0). All three OrcaRouter free routes are NOT-READY: HTTP 429 `free_rate_limited` / `err_free_access_denied`, because free models need the workspace owner to link an established GitHub account in OrcaRouter settings (or buy credits). `kilo/auto-free` was not probed. Everything stays disabled.
- Tests: `tests/test_v0108_orca_kilo.py` (19). These enable the five entries in a registry variant and prove sensitive turns never reach them, pinned or unpinned, while a non-sensitive pin still serves. Count asserts 110 → 111. Doc: `docs/ORCA-KILO-KEYS-2026-11-27.md`.

## [0.10.7] — 2026-11-27

**Consolidated release onto main** (Jordan's go 2026-11-27 23:33 MST: "build one branch off main with B, C, D and the model import, fix conflicts, test, open one PR"). PRs #23–#25 were merged into their stacked base branches rather than into `main`, so `main` had v0.10.4 (#22) and Muse's v0.10.3 (#26) but not Jobs B/C/D. This release brings v0.10.5 (Job B), v0.10.6 (Job C), Job D and the v0.10.7 model import onto `main` in one branch, and moves `VERSION` to 0.10.7. Before this, `main` reported 0.10.3 even though v0.10.4 had shipped.

Conflict resolutions:
- `src/gateway.py`: the only conflict was the `VERSION` line, now `0.10.7`. Muse's `_shape_reasoning_effort` / `_temperature_stripped` and B/C's depth cap, tool-error detector and tier shadow merged cleanly and all remain.
- `tests/test_paid_spillway.py`: the version assert is `0.10.7`, and its comment carries the full v0.10.3 to v0.10.7 history.
- `tests/test_smoke_providers.py`: both `V0103_DISABLED_IDS` (Muse's five dead lanes) and `V0107_IMPORT_DISABLED_IDS` are kept, and both are in the disabled-set union.
- `CHANGELOG.md`: entries are ordered by version (0.10.7, 0.10.6, 0.10.5, 0.10.4, 0.10.3, 0.10.2).
- Enabled count 78 (v0.10.3's five disables apply; all 12 imports remain disabled), total entries 110.

### Added — v0.10.7 model import (registry, tools and docs; 2026-11-27)

Jordan's order 2026-11-27 23:06 MST: "pull the relevant models both free and not into ours". The 12 new entries all ship **disabled**, so routing is unchanged (enabled count stays 78 after v0.10.3's five disables; total entries 98 + 12 = 110).

- **PENDING-PROBE** (the key already exists on VM103):
  - `deepinfra/qwen3-coder-480b-turbo` ($0.30/$1.00)
  - `deepinfra/qwen3-coder-30b` ($0.07/$0.26)
  - `deepinfra/deepseek-v4.1-flash` (list $0.20/$0.60, 1M ctx)
  - `deepinfra/nemotron-3-ultra` ($0.50/$2.20)
  - `deepinfra/kimi-k2.6` ($0.75/$3.50)
  - `huggingface/qwen3-coder-480b` (`:deepinfra`, $0.30/$1.00)
  - `nvidia/nemotron-3-ultra` (free; evaluation terms, logged)
  - `nvidia/kimi-k3.1-pro` (free; evaluation terms, logged)
- **PENDING-KEY** (needs Jordan's signup):
  - `orcarouter/deepseek-v4.1-flash-free`
  - `orcarouter/glm-5.3-flash-free`
  - `kilo/auto-free`
  - `kilo/beam-free`
- Every paid price was verified 2026-11-27 from the provider's public pricing API. Quality figures are seeds; no tooling score is set before the battery runs.
- Tests: `tests/test_v0107_model_import.py` (new). The registry count and disabled/sensitive sets in the existing tests are updated.
- **Probe results (Jordan's go 2026-11-27 11:18 PM MT):** minimal direct chat + tool + code calls from VM103 to all 8 keyed entries. Seven passed (READY-TO-ENABLE); `deepinfra/qwen3-coder-30b` failed its first code test and is verbose (NOT-READY). All stay disabled. `nvidia/kimi-k3.1-pro` latency measured at 49-76s, so its `latency_p50_ms` is now 50000. Total test spend about $0.005.
- **Backup coder preference:** `CLASS_PREFERRED["code"]["cheap"]` is now nrouter qwen3-coder, then deepinfra qwen3-coder-480b-turbo, then huggingface/qwen3-coder-480b (non-sensitive only). Shadow-only; the last two are disabled.
- **HF router catalog source:** new `tools/hf_catalog.py`, wired into `tools/price_check.py` and `tools/discovery_tripwire.py` via `--hf-file/--hf-url` (public endpoint, no credential, report-only). Tests in `tests/test_hf_catalog_tools.py`.
- **Plan only:** `docs/EMBEDDINGS-RERANK-PLAN-2026-11-27.md` (Bounded Job Card for an embeddings + rerank service; nothing built).
- Docs: `docs/MODEL-IMPORT-2026-11-27.md` covers the evidence grades, the candidates rejected and why (CF kimi-k2.6 deprecated, ZenMux free sunset, no verified free Qwen coder), and the image-gen shortlist.

### Added — Job D: nightly scorecard, proposals only (2026-11-27; tools-only)

Owner go 2026-11-27 20:25 MST, "Do B C D".

- **`tools/scorecard.py`** reads `routes.jsonl` as text and `quota.db` read-only (sqlite `mode=ro`), and names models from `registry.json` without ever writing it. It reports for a window (default 24h):
  - Per client: requests, non-200, average and worst-day cost vs target, sensitive share, tool-error-next-turn rate, spillway/premium serves, depth-cap hits, `premium_not_required_congestion` skips.
  - Production tool outcomes per model.
  - Failed attempts by class, by congested pool and by dead entry, plus fallback depth.
  - Top model × class serves.
  - The v0.10.6 `tier_shadow` agreement by class, with tier moves and top swaps.
- **Proposals.** Each has an id, evidence, proposed change, risk and status "PROPOSED — needs Jordan's approval; nothing applied". Kinds:
  - tool_quality: ≥20% errors on ≥30 judged turns.
  - dead_entry: ≥10 entry_missing/account_dead; skipped when the registry already has the entry disabled.
  - pool_pressure: one pool ≥50% of failed attempts.
  - depth: max depth ≥6.
  - cost: average or worst day over target.
  - sensitivity: OpenClaw >50% flagged.
  - tier_policy: shadow disagreement ≥30% on ≥50 rows of a class.
- **Output.** `--out DIR` writes `scorecard-<date>.md/.json`, and `--stdout` prints. Nothing else is written.
- **Schedule.** `deploy/cron/scorecard.cron` ships commented out (inactive). Activating it is a separate owner decision. No n8n workflow was created.
- **First run.** On VM103 live logs (72h to 2026-11-27 20:43 MST), run read-only from a RAM temp copy that was then deleted. Report: `reports/scorecard/scorecard-2026-11-27-72h.md`. It made 6 proposals: free-router tooling 4→3 (29% on 52), ling-3.2-flash-free re-probe/disable (10 entry_missing), Cloudflare pool pressure (50% of failures), depth (max 21), Hermes worst day $1.41 vs $1.25, and OpenClaw 98% sensitive → v0.10.4.
- Tests: `tests/test_scorecard_job_d.py` (7): read-only proof (DB/routes/registry hashes unchanged, only `--out` written), `mode=ro` refuses writes, card contents and every proposal kind, already-disabled entries not proposed, worst-day cost, empty state, cron inactive.

## [0.10.6] — 2026-11-27

**v0.10.6 — Job C of the tiered-routing plan: per-class tiered ranking in SHADOW mode** (owner go 2026-11-27 20:25 MST, "Do B C D"; policy reading 2026-11-27 17:26 MST — "best" = best for the request type and the tools/capability it needs; per class: best free, then best cheap, then premium only when required). Built on v0.10.5 (Job B). **Routing is unchanged.**

- **New `src/tiering.py`.** It ranks the live candidate list, which already has the sensitive wall, lanes, premium gate, tools support, quality floor, context, limits and cooldowns applied by `build_candidates`. The ranking key is tier (free = $0 free-lane entry < cheap = paid or priced private-free < premium), then the owner-preferred specialist for the class, then measured score, then estimated price, then p50 latency.
  - Score is `_selection_quality`: on tool turns that is min(tooling, class quality), the live definition, minus a production tool-error penalty (−1 at ≥15%, −2 at ≥30% on ≥20 judged turns in the 72h `tool_outcomes` window, cached 60s).
  - Classes: chat / infra / code / reasoning / long-context.
  - Capability tags: `tools`, `vision` (entries without `supports_vision` are ineligible), `structured-output` (recorded only; no registry flag yet).
  - Owner defaults: the free coder is `unorouter/codestral-latest` and the cheap coder is `nrouter/qwen3-coder-480b-a35b`. Each is preferred only within its own tier and only when already eligible. Codestral is public-only-free, so it is never a pick for a sensitive turn.
  - No image-generation class yet.
- **Shadow logging.** Every route row gains `tier_shadow`: `{pick, pick_tier, live_pick, live_tier, agree, task_class, tags, low_confidence (<0.6), reason (pinned/sticky/spillway), top: [3 × {model, tier, score, preferred, cost_usd}]}`. `/v1/shadow` gains `tiered_shadow`. `SOVEREIGN_TIER_SHADOW=0` disables it. A shadow exception only adds the `tier_shadow_error` signal and never affects serving.
- **Proof that routing is unchanged.** For five prompts (hermes/openclaw/n8n, L1–L5, sensitive, failover with deterministic 429s), status, served model, the full upstream call sequence and the response body are identical with the shadow on and off.
- Tests: `tests/test_v0106_tier_shadow.py` (18). E2E harness gains section "F tier-shadow" (4 checks: every served row carries it; a sensitive row's shadow pick is never public-only-free; the shadow never picks premium while a free/cheap candidate exists; `/v1/shadow` live_pick matches would_route_to). The harness boot check now reads this tree's VERSION. Unit suite 905.

## [0.10.5] — 2026-11-27

**v0.10.5 — Job B of the tiered-routing plan: honest tool-error detector, fallback-depth cap, no premium on congestion** (owner go 2026-11-27 20:25 MST, "Do B C D"; plan in the Drive doc *GROK - Sovereign Router Classification, Tiers & Self-Correction - 2026-11-27*). Built on top of v0.10.4 (not yet merged/deployed) so the two ship together; 0.10.3 stays reserved for the parked reasoning_effort shaping build.

- **Tool-error detector (`_tool_text_has_error`).** Pre-v0.10.5 any tool result containing the substring `"error"` counted as an error, so success payloads such as `{"error": null, ...}`, `{"ok": true, "error": ""}` or `{"errors": []}` were counted. That signal feeds `tool_error_next_turn` on the route row, the F2 sticky break and the v0.10.2 production tool-error ledger, so false positives could demote healthy models. The detector now parses JSON and counts an error only when an error-ish key carries a real value, an `is_error`/`isError` flag is true, `ok`/`success` is false, or `status` is in an error vocabulary (one level of `result`/`data`/`content` wrappers and MCP content arrays included). Plain text counts when it starts with Error/Traceback/Exception/fatal/failed, except zero-count lines like `errors: 0`. Unparseable JSON still counts a non-empty `"error": "…"` field. The telemetry window check now uses the same function.
- **Fallback-depth cap.** At most `MAX_UPSTREAM_ATTEMPTS = 6` upstream calls per turn across the free-first pass, the same-model length retry, validation escalation and the paid spillway (`SOVEREIGN_MAX_UPSTREAM_ATTEMPTS` overrides). The 72h census to 2026-11-27 saw depths of up to 21. When a spillway can still engage, the free pass hands over after 4 calls (`SPILLWAY_RESERVED_ATTEMPTS = 2`), so a congested free pool cannot consume the whole budget. If no spillway candidate exists the free pass continues to the cap. Route rows carry `fallback_depth_cap:6` / `fallback_depth_reserve_spillway:<n>` signals.
- **No premium on congestion.** In the spillway pass, premium entries are kept only when the request itself permits premium (client allows it AND level 5 or an explicit premium ask), or when no paid entry can meet the quality floor at all (capability, judged without momentary rate limits). Otherwise they are skipped with reason `premium_not_required_congestion`. **Behaviour change:** a turn whose free AND paid candidates are all congested now returns 429 instead of being rescued by premium. In the local E2E, v0.10.4 made 66 upstream calls and served premium in that scenario; v0.10.5 makes at most 6 and never calls premium.
- Tests: `tests/test_v0105_tool_error_depth_cap.py` (54 — 18 not-error and 18 error payloads, route-row signal, F2 helper, cap JSON/stream, reserve handover, env override, no-premium for hermes/openclaw JSON/stream, premium still serves at L5 and when no paid clears the floor). E2E harness gains section "E routing" (22 checks); the same section against v0.10.4 fails 11/22. Unit suite 887 (833 + 54).

## [0.10.4] — 2026-11-27

**v0.10.4 — OpenClaw session-key false positive fixed in the credential detector, 2026-11-27** (owner go 2026-11-27 14:23 MST, Tier 2, exactly this one change; Job ROUTER-J1B-20261127, following the read-only Job 1 check ROUTER-J1-20261127). Version label 0.10.4 chosen deliberately: 0.10.3 is reserved for the parked reasoning_effort shaping build (built, not pushed — it must rebase onto this release).

- **Root cause.** OpenClaw's system prompt carries a runtime line on every request — `Runtime: agent=main | session=agent:main:main | host=… | model=sovereign/auto` — and its `/status` output prints `Session: agent:<id>:<scope>`. The bare `session` noun that v0.9.1 (F-18) added to `credential_assignment` matched that value, a colon-delimited routing identifier, not a credential. Effect: OpenClaw's sensitive-flag rate went from ~0% to ~100% at the v0.9.1 deploy (2026-11-15 04:00 MST) and stayed ~97% (1,173 of 1,207 OpenClaw requests in the 7 days to 2026-11-27 were flagged, every one by `credential_assignment` alone), holding OpenClaw to the sensitive wall (private-free + paid spillway) instead of the full registry. No known-format key/token detector fired for OpenClaw in that window. Detail: `docs/incidents/2026-11-27-openclaw-session-key-false-positive.md`.
- **Fix.** For the `session` noun only, a value of the exact shape `agent:<agentId>:<scope>[:…]` (`_OPENCLAW_SESSION_KEY_RE`) is skipped. Every other noun is unchanged (`password=agent:main:main` still flags), and every random cookie/token shape still flags: `session=<random>`, `session: <random>`, `Cookie: session=…`, `Set-Cookie: session=…`, express-signed `session=s:<id>.<sig>`, golden g180 `session=FAKESESSIONVALUE000`, and the prose `session cookie is …` form. Prefix-format detectors untouched. The scan can only flag less than before, never more.
- **Not in this release (deliberately).** `sessionid=` and `session_token=` were NOT flagged before this release and are still not flagged (the noun list does not include them; `Set-Cookie: sessionid=…` is caught by the prose detector). Widening the noun list is a separate detector change for the owner. No registry, lane, client, free-router or OpenClaw change.
- **E2E hardening loop (Job ROUTER-J1C-20261127, same day, before deploy).** A local end-to-end harness was added at `tests/e2e_session_key/`. It runs the real gateway and classifier sidecar against mock upstreams, using the live registry and client shape. It found three issues in the first cut, all fixed with tests:
  - (1) Markdown or prose wrapping around the key (`` `session=agent:main:main` ``, `**…**`, `(…)`) still flagged. Wrapping characters are now stripped before the shape check.
  - (2) Differential fuzz showed that `Cookie: session=agent:…` would have been exempted. Cookie context on the same line is now never exempt.
  - (3) A 900k-character single-line prompt with 20k session keys took 34 s per scan, because the unbounded per-match look-back was quadratic. The cookie offsets are now computed once per scan, giving O(n log n) and about 0.2 s. The v0.10.2 baseline was about 0.16 s.
- **E2E results.**
  - 252/252 checks pass across OpenClaw runtime-line routing (full candidate set preserved), real secrets (38 must-flag shapes, served only off public lanes, no canary in logs or at any public or excluded upstream), Hermes tool turns, n8n bulk, streaming, 402/403/429/500 and stall failover, and edge cases.
  - Differential fuzz over 60,000 cases found 0 superset violations and 0 unexplained differences.
  - The same harness against v0.10.2 fails 59/59 OpenClaw runtime-line checks.
- Tests: `tests/test_session_key_exclusion.py` (25). Suite 833 (808 + 25).

## [0.10.3] — 2026-11-27

**v0.10.3 — last-resort request shaping + dead-lane registry cleanup, 2026-11-27** (from the owner's cleanup loop over the Nov 26 soak breaches; every remedy below is quoted from the upstream's own captured error body, persisted since v0.10.1. Rebased onto v0.10.4 — merged earlier the same day, see its entry below; the label order follows the reservation recorded there, and this build ships both changes):

- **reasoning_effort value shaping (`_shape_reasoning_effort`).** Two premium last resorts reject specific reasoning_effort *values* with hard 400s — and 400 is not in FAILOVER_STATUSES, so the poisoned value fails the whole turn when the cascade reaches these entries with nothing behind them. Narrow by provider+model, on the per-attempt upstream body: **premium/gpt-5.5 with tools → reasoning_effort forced to "none"** (OpenAI: function tools with a graded reasoning_effort are not supported for gpt-5.5 in /v1/chat/completions; an omitted field defaults server-side to a graded effort, so the value is set even when absent — tool-free turns keep the client's effort); **zai/glm-5.3 "none" → "low"** (Z.ai error 1210: the model always thinks and accepts only low/high/max; an absent field stays absent). Tests: `tests/test_reasoning_effort_shaping.py`.
- **Temperature guard for premium/gpt-5.5 (`_temperature_stripped`).** The same entry also rejects a client *temperature*: "Unsupported value: 'temperature' does not support 0.2 with this model" (error_body, 2026-11-27 19:22 UTC — three n8n L5 turns died end-to-end when the free pass exhausted on the OpenRouter daily cap + Cloudflare neuron wall and the premium step then 400'd on the forwarded 0.2). GPT-5-class reasoning models run at their default temperature only, so for direct-OpenAI gpt-5.5 the gateway omits the field and lets the upstream default apply; every other entry keeps the client's temperature exactly as sent. Folded into this release as its third commit on the owner's ruling. Tests: `tests/test_reasoning_effort_shaping.py`.
- **Registry: five proven-dead lanes disabled.** `meta/muse-spark-1.1`, `meta/muse-spark-1.2`, `meta/muse-spark-1.3` (402 `billing_not_configured`, account_dead), `ollama/gpt-oss-120b` (402) and `novita/gpt-oss-120b` (403 ACCESS_DENY) — each burned cascade attempts on every turn that reached it; disabled on error-body proof, not deleted, each entry's note records the evidence. Enabled count 83 → 78; coupled test premises (pin maps, disabled-set unions, the siliconflow slot and effort/gemma mechanism tests) updated in the same pass via the `registry_variant(enable=…)` knob.
- **Registry: long-context scores measured, not seeded (second commit).** The Nov 27 escalation master cause was sensitive L4 long-context turns with no qualified private-free candidate: the private-free lane's long-context scores were seeds. A purpose-built long-context battery (~76–111k-token synthetic tasks, calibrator-controlled) measured them: `groq/qwen3.6-27b` and `groq/qwen3.8-27b` long-context 3 → 4, `groq/gpt-oss-20b` 2 → 3, `groq/gpt-oss-120b` stamped at 3 — all `long_context_source: long-context-battery:2026-11-27`, `verified_until: 2026-12-27`. Live-proven before this release shipped: an unpinned hermes sensitive L4 turn at 74,714 tokens served on `groq/qwen3.6-27b`, private-free, escalated:false. `openrouter/free-router` and `openrouter/nemotron-3-ultra` remain UNMEASURED (every battery pin hit the OpenRouter free-pool upstream 429) — re-run in a quieter window.

## [0.10.2] — 2026-11-27

**v0.10.2 — production tool-error avoidance + quick wins, 2026-11-27** (owner ruling 2026-11-27, from the same-day Hermes/OpenClaw tool-error diagnosis: Hermes' production tool_error_next_turn rate is ~24% pooled over 72h and unmoved since F2/F3, because the battery tooling scores cannot discriminate — every major entry carries tooling 5 while measured production rates span 12.4% to 34.5%):

- **Production tool-error telemetry feeds tool-bearing selection (the J-2-adjacent ruling).** The owner ruled that measured production tool-error rates may feed selection for tool-bearing turns; the battery scores are NOT edited — this is a separate production signal layered on selection, in the spirit of the v0.8.2 stream-abort cooldown. Every tool-loop continuation request now judges the previous serve (its trailing tool results, the F2 signal, counted once per continuation) and records the outcome against the serving entry in a new `tool_outcomes` ledger. An entry at **≥25% error rate on ≥50 judged turns over a rolling 72h** is demoted for tool-bearing selection: reordered behind every non-flagged entry *in its cost tier* — never removed, never disabled. Free-first is absolute (a flagged free entry still precedes every paid candidate), non-tool turns are untouched, pins still reach flagged entries (J-1 unchanged), and a flagged entry still serves when it is all that is left. Gates basis: the bar sits just above the pooled Hermes rate (~24%) and below the offending cluster (26.9%–34.5% census method; 29.6%–40.0% on the sticky-continuation method the tracker measures), leaving the 12.4% best major entry untouched; the 50-sample floor mirrors the governor's level_mix n-gating scaled to per-entry tool turns, so a sporadic entry (cloudflare/gpt-oss-120b: 27.3% on 22 serves) stays unflagged until the evidence exists. Tests: `tests/test_tool_error_avoidance.py` (11).
- **DeepSeek thinking-mode reasoning_content pad.** `deepseek/deepseek-v4-pro` 400'd when a tool-shaped request replayed assistant history missing `reasoning_content` ("The `reasoning_content` in the thinking mode must be passed back to the API" — the error body captured since v0.10.1; same family as the Gemini thought-signature bug). DeepSeek's Thinking Mode documentation requires the reasoning_content of all previous turns on requests carrying `tools`, even turns that made no tool call. For direct-DeepSeek reasoning entries only, and only on tool-shaped requests, `upstream_body` now pads `reasoning_content=""` (the defensive shape Hermes itself shipped for this error) on assistant messages lacking a real value — copy-on-write, real values preserved verbatim, nothing leaks into fallback attempts. Tests: `tests/test_deepseek_reasoning_content.py` (7).
- **Registry: the two proven-dead OpenRouter entries are disabled.** `openrouter/north-mini-code-free` and `openrouter/dots-3-note-preview-free` — v0.10.1 deliberately left them enabled pending evidence; the captured error bodies now read "no longer available on OpenRouter: no provider serves this model anymore" for both (the 404s were not intermittent endpoint churn), and the pair burned 713 attempts in the prior 72h. Disabled, not deleted; each entry's note records the proof. dots-3 was Hermes' compression/auxiliary pin target: under J-1 a pin for a disabled entry drops and the turn routes normally, so compression traffic fails over rather than failing (live-proven at deploy). Registry-premise updates ride in the same pass for the tests that exercise these entries' mechanisms (F-09 budgets, F-16 wall, reasoning-flag retry, shadow quality correction — via the `registry_variant(enable=…)` knob).

## [0.10.1] — 2026-11-25

**v0.10.1 — error-visibility fixes, 2026-11-25** (from the owner's past-week error census order, 2026-11-25; census of Denver days Nov 18–24 + Nov 25 so-far: 8,065/8,066 finals HTTP 200, but 5,667 failed attempts underneath, dominated by classes no alert kind covered):

- **Upstream error-body capture.** Failed attempts now persist the upstream error body in `attempt_events.error_body` — bounded to 500 chars, scrubbed of credential-shaped material before persistence, additive schema migration; served attempts store nothing, and classification/selection never read the column. Before this, the body was classified in memory and discarded, leaving every 400-class failure undiagnosable from stored data (the census's capture-gap finding). Tests: `tests/test_error_body_capture.py` (5).
- **Gemini 3 thought-signature bypass.** The chronic `google/gemini-3.1-flash-lite` 400s (112 request_fault attempts in the census week) are root-caused: Gemini 3 rejects replayed function calls that lack a `thought_signature`, and a model-switching router replays tool calls originated by other models. For direct-Google Gemini 3 entries only, `upstream_body` now attaches Google's documented bypass value (`skip_thought_signature_validator`, per Google's thought-signatures documentation for history transferred from models without signatures) as `extra_content.google.thought_signature` on assistant tool calls that lack one — copy-on-write, real signatures never overwritten, no other provider touched. Tests: `tests/test_gemini_thought_signature.py` (6).
- **Governor failure-rollup digest.** New daily digest kind `failure_rollup_digest` (same once-per-day dedup as the F-15 posture digest): trailing-24h counts for entry_missing (with top models), pool-scope congested (with top pools), and request_fault. The census week's two dominant failure classes previously generated no alerts at all — entry_missing ran six days unnoticed. Silent on a clean day. Tests: `tests/test_failure_rollup_digest.py` (4).
- **Not in this release (census findings, deliberately):** no registry entries were disabled — live probes proved `openrouter/north-mini-code-free` and `openrouter/dots-3-note-preview-free` still serve; their 404s are intermittent (OpenRouter free-endpoint availability), and with error bodies now captured, the 404 split (no-endpoints vs truly-gone) can be classified from evidence before any demote/registry action. That follow-up rides the J-2 observation data, not a guess.

## [0.10.0] — 2026-11-24

**v0.10.0 — LiteLLM borrow package, 2026-11-24** (PR #18, merged and deployed 2026-11-24 on the owner's order to incorporate the LiteLLM borrows to GitHub and live; from the 2026-11-24 LiteLLM-vs-Sovereign comparison — verdict: stay on Sovereign, borrow the commodity patterns. Version label 0.10.0 chosen deliberately: 0.9.3 is burned by the removed PR15 canary):

- **Borrow #1 — price-drift tripwire (`tools/price_check.py`).** Compares priced registry entries against LiteLLM's `model_prices_and_context_window.json` and reports drift; cache/tiered field schema adopted for the comparison. Report-only by construction (tests assert the registry file is byte-identical across runs): F-04 stands — aggregators nominate, providers decide.
- **Borrow #2 — per-attempt telemetry contract.** Attempt rows gain `fallback_depth` (0-based attempt ordinal within the turn), `provider_cost_usd` (provider-reported cost captured verbatim when sent — J-3(i) logging only, never repriced, cost of record unchanged) and `reasoning_tokens` (R3). Additive schema migration on the data_class precedent; selection reads none of it.
- **Borrow #3 — discovery tripwire (`tools/discovery_tripwire.py`).** Diffs the LiteLLM cost map (and optionally the changelog feed) against the registry and emits candidate nominations into the existing gated pipeline. Report-only; fleet-provider filter by default.
- **Secondary borrows as design notes only** (`docs/litellm-secondary-borrows-design-notes.md`): Presidio-class second detector, exact-match cache for the non-agentic slice, breaker thresholds as registry fields at the J-2 gate. No code — S-3 in particular waits on the owner's J-2 ruling.
- Tests: `tests/test_attempt_telemetry.py` (6), `tests/test_litellm_borrows_tools.py` (6).

## [0.9.2] — 2026-11-24

**v0.9.2 — tool-turn routing fixes F2 + F3, 2026-11-24** (PR #14, merged and deployed 2026-11-24 on the owner's "Do all 3" order + merge retry tap; from the 2026-11-19 routing diagnosis; F1 deliberately not applied — the 15 "DISABLED candidate" entries were enabled on the owner's 2026-11-17 ruling, see each entry's notes):

- **F3 — tool turns rank on `min(tooling, class quality)`.** The measured tooling score is now a ceiling, not a replacement (`gateway._selection_quality`). The one-shot battery saturates (60/85 enabled entries scored 5, including a 1.7B model), so ranking on it alone made every free model tie and let the fastest weak one win. A tool turn must now be good at tools AND at the request's task class. Unmeasured entries are unchanged. Offline shadow of Hermes-shaped requests: infra/chat tool turns no longer lead with particle-1 / north-mini / llama-4-scout and lead with cloudflare/gpt-oss-120b and nemotron-3-ultra-free; long-context tool turns lead with gemini-3.1-flash-lite. This REVERSES the v0.9.0 "inversion" premise (a weak-chat, tooling-5 model outranking a strong one on tool turns): `test_tool_turns_rank_on_tooling_chat_turns_do_not` and `test_tooling_floor_bites_only_on_tool_turns` were rewritten for the new rule, and `test_streaming_bad_tool_call_escalates` now escalates to deepinfra/deepseek-v4-flash-0731 (an 8B model is no longer a valid "stronger" target).
- **F2 — tool errors break stickiness.** After `TOOL_ERROR_BREAK_AFTER` (2) consecutive tool-loop requests whose latest tool result(s) carry an error, the sticky model is dropped and the conversation is not routed back to it for the TTL (`StickyStore.record_tool_outcome/avoid/avoided`; skip reason `conversation_tool_errors`; telemetry signal `sticky_broken_tool_errors`). The signal is `gateway._latest_tool_error` (trailing tool results only): the existing `has_tool_error` telemetry heuristic scans the last three messages, so one error is counted on two consecutive requests — driving the break from it would abandon a model after a single isolated error. The avoided model is never removed if it is the last candidate; an explicit pin still wins.
- Tests: new `tests/test_f2_sticky_tool_errors.py` (8), new saturated-tooling regression in `tests/test_tooling_dimension.py`; suite 761 passed.

**v0.9.0 test completion, 2026-11-15** (owner's order 02:15 MST + CI directive re-issued 02:48 MST — "run CI tests again until you get green, then deploy it live"; gateway code unchanged, v0.9.0):

- **CI restored to green.** The consolidated registry update below landed without updating the registry-premise tests (it had been validated with the invariant battery only) and CI #45/#46 went red on main — 13 failures, all premises pinned to the 81-entry shape. Fixed in `b431bd1` (counts 88/72-enabled, NOV15_DISABLED_IDS cohort, OpenRouter free pool at 16 members, enabled-aware skip reasons in the shadow tests, lfm-2.5 out of the pin map): CI #47 SUCCESS. No production code or registry content changed by the fix.
- **Golden set completed and merged.** The 24-item starter is now the full 180-item set designed in cycle-01-plan F.3.2 (starter items verbatim; client mix = census mix), replayed in shadow against live v0.9.0: the 5 known violations reproduce; 69 new violation strings across 63 items, analyzed in `docs/cycles/golden-full-run-2026-11-15.md` (long-context engages only above the >60k-token hard rule; short-prompt task_class misclassification priced at scale; L5-driven paid-first is spec-conformant; NEW — prose-stated credential values classify `internal`, not `sensitive`, and two items first-routed public: proposed as Improvement Loop candidate F-18, no posture change made). Landed via PR #9 (branch CI #48 SUCCESS, merge CI #49 SUCCESS, main `625b4be`).
- **Tooling battery completed and deployed.** All 17 enabled-unmeasured entries targeted against live v0.9.0: **11 newly measured** (north-mini-code 4, dots-3-note 4, nemotron-3.5-lightning 5, nemotron-3-nano-omni 5, flint 4, lfm-3-1.7b 5, nemotron-3-ultra 5, nemotron-3.5-super-vl 4, free-router 5, aion-3.5-mini 3, aion-2.0 5 — measured coverage 66 of 72 enabled); 6 never served (ollama `upstream_402`; the Cloudflare trio on shared-pool `upstream_429`; deepinfra/mistral-small-24b + novita/llama-3.1-8b structural `supports_tools: false`). Scores written via the established path — snapshot `pre-battery-scores-20261115`, battery patch applied field-for-field (programmatic diff: only `quality.tooling` + entry-level `tooling_source`/`verified_until` on the 11), suite 701 passed ×2 after recalibrating three escalation tests the new rankings legitimately moved, CI #50 (run 43702684456) SUCCESS on `dee1a2a`, synced to VM103 build/src + state and gateway restarted 10:11 UTC. **Live registry md5 `bf40db3e7fa02abfa2aae12bc655c81d`** (was `03df1665…`); `/health` ok, 88 models; a shadow tool turn now first-routes to newly-measured `aion/aion-2.0`. Record: `docs/cycles/tooling-battery-2026-11-15.md`.
- **Extended live battery + early-soak census.** Per client (hermes/openclaw/n8n/crm) chat + tool-bearing + streaming turns all PASS (14/14, real tool calls returned; the CRM tool turn served by `openrouter/free-router`); a 96,619-token long-context turn served 200 by `google/gemini-3.1-flash-lite` (class long-context, L4); a sensitive-shaped turn classified `internal` (no detector fires on a redacted placeholder) — observation only, no posture change. Census from the v0.9.0 cutover (2026-11-15 01:58 MST) to 03:13 MST, production + tonight's test traffic: 288 requests (hermes 126, openclaw 129, n8n 30, crm 3), **100% HTTP 200**, escalations 12 (4.2%, all recovered), 110 streaming rows with **zero `stream_bound_timeout`**, 7,704,887 tokens with **98.6% on free lanes** (by tokens), ledger cost $0.08.

**Consolidated free-model registry update, 2026-11-15** (owner's ruling \u2014 package approved as built 2026-11-15 02:03 MST; gateway code unchanged, v0.9.0; registry 81 \u2192 88 models, enabled count unchanged):

- **Added disabled (7), all public-only-free:** `openrouter/ling-3.2-flash-free` (tooling battery 5 on re-probe; chat latency normalized from 38s to ~1.3\u20132.3s), `openrouter/particle-1-free` (battery 4; provenance unknown), and five UnoRouter routes probe-proven 2026-11-12, none requiring provider consent (`unorouter/gemini-3.5-flash-lite`, `unorouter/mistral-small-3.2`, `unorouter/codestral-latest`, `unorouter/qwen3.5-397b-a17b`, `unorouter/claude-haiku-4-5-20251001` \u2014 the last chat-only, `supports_tools: false` load-bearing). Every add ships disabled; enabling is a separate decision.
- **Disabled** `openrouter/lfm-2.5-2.6b-free`: the upstream model was deleted from the OpenRouter catalog (verified in the 2026-11-15 OpenRouter assessment); the entry is retained disabled, never deleted.
- **Annotated** `openrouter/dots-3-note-preview-free` with its published 2026-12-31 retirement date (notes-only).
- **Battery tooling scores written** via the battery patch mechanism (run date 2026-11-15, `verified_until` 2026-12-15): `openrouter/ernie-5.5-flash-free` tooling 4, `openrouter/ling-3.2-flash-free` tooling 5, `openrouter/particle-1-free` tooling 4.
- The package was assembled on the v0.8.3 registry and re-based onto the live v0.9.0 registry (md5 `460b6df4\u2026`) at deploy; final registry md5 `03df1665a6b2e99c53d50bd865d52b31`, byte-identical on GitHub main, VM103 build/src and VM103 state. Invariant battery 15/15 on the final file. Snapshot `pre-registry-update-20261115` precedes the change; rollback = restore that registry to all copies + gateway restart. Live verification: `/health` reports 88 models; a pin test against the disabled Ling entry dropped the pin (`pin_blocked:disabled`) and served 200 via `openrouter/nemotron-3-ultra-free`; 85 routes in the first 15 minutes post-restart, all 200, zero serves by any disabled entry.

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

## [0.9.1] — 2026-11-15 (DEPLOYED 2026-11-15 03:51 MST)

Alert truth + sensitive wall — the full fix set F-13…F-18 from the 2026-11-15 alert diagnosis and the golden full-set run, approved by the owner 2026-11-15 03:19 MST ("apply the full fix set as recommended"). PR #10 (branch CI run 43704831192 SUCCESS; merged to main `9fb48c7c`, main CI #53 run 43705294364 SUCCESS). Suite 701 → 721, green ×2 locally before push. Snapshot `pre-v091-20261115`; image `192.168.0.28:5000/sovereign-router:v0.9.1` sha256:38c6bc41a341dad95c593bc22a4bb40f4ad455f176135c132661d4c7bf419a9b; compose backup `bak-20261115-prev090`. **Final live registry md5 `d9a1f9994f6c4048fde7e7eba16c4488`** (88 entries, 70 enabled), byte-identical on GitHub main, VM103 build/src and VM103 state.

- **F-13 — spend alerts on the actuals basis (governor).** `premium_monthly_spend` and `provider_spend` now sum the actuals window (month start clipped at the true-cost cutover epoch 1794630834 for November 2026; unclipped from Dec 1 by construction). Levels UNCHANGED ($20 premium / $10 provider). When the clipped window still fires, the message annotates the excluded pre-cutover ceiling. The slow-leak projection uses the same window. Live dry-run verdict: zero spend alerts since the recreate — the three pasted alerts ($20.22 / $20.56 / $150.05) were $20.19 / $20.37 / $150.01 of pre-cutover ceiling over $0.08 / $0.19 / $0.06 of actuals.
- **F-14 — level_mix baseline-relative (governor).** Fires only with today's sample n ≥ 200 AND today's L4–5 share above the trailing-7-day mean of daily shares by >10 points. No classifier change. Silent in the live dry-run.
- **F-15 — sensitive alert on outcomes (gateway + quota).** `sensitive_no_cleared_provider` now fires ONLY when a sensitive request serves on a paid/premium lane (post-serve, both JSON and streaming paths) or finds no candidate at all. The permanently-true empty-clearance condition is demoted to a daily `sensitive_posture_digest` (trailing-24h sensitive serves by entry/lane/cost + cleared-set status); usage rows carry `data_class` (migrated column) to feed it. Live: exactly ONE alert created since the recreate — the digest itself (10:54:26 UTC, rendering the smoke serves). The n8n alert workflow needed no edit (it passes gateway alerts through).
- **F-16 — sensitive wall (registry).** `openrouter/dots-3-note-preview-free` and `openrouter/north-mini-code-free` gain `spillway_sensitive_excluded: true` with dated notes (lane stays private-free) — the 348,543-token sensitive serve by dots-3 on 2026-11-14 cannot recur: both entries now skip for sensitive turns in every pass, pins included. `sensitive_cleared_providers` deliberately NOT populated (no owner-reviewed cleared set exists; separate future ruling — consideration recorded in the loop doc). Live proof: sensitive smokes show both entries skipped `sensitive_spillway_excluded`; a body-pin to dots-3 on a sensitive turn dropped `pin_blocked:sensitive_spillway_excluded` and served inside the wall.
- **F-17 — Aion (gateway + registry + J-2 packet).** (a) The lane-dead TPD cause now states counted usage vs cap and the triggering request's estimate ("0 / 1,000 tokens used today; this request's estimate N tokens exceeds the cap"); the dedup key stays on the stable cause class so distinct estimates don't mint fresh alerts. (b) Both Aion entries disabled with dated notes (20k/day account cap below one ordinary fleet request — a 67,274-token request tripped it at zero usage; ~350 skip evaluations/day; ~21 lifetime serves); their 2026-11-15 tooling scores (3 / 5) are preserved on the disabled entries. Live: Aion skips as `disabled`; a body-pin drops `pin_blocked:disabled`. (c) `docs/cycles/j2-review-packet.md` created; Exhibit 1 = the Nov 13 Aion dataset pulled from production `attempt_events` (165 attempts: 144 failed — 143× 429 congested + 1 transient — ALL `would_demote`-stamped; 21 served, 3,626 tokens).
- **F-18 — prose-stated credentials (sensitivity).** New detector `credential_prose_assignment`: credential noun (incl. multiword `client secret` / `session cookie`) + ≤6 filler words + separator (`:` / `=` / "is") + a value token passing a credential-shape gate (digit, hyphen, underscore, or mixed case; UUID/hex/placeholder/ellipsis exclusions; an idiom guard keeps "the secret to … is …" clean). Golden g175/g177/g179/g180 are the regression set. Negative-tested: policy discussion, value-less mentions, redacted placeholders (`sk-…`, `password=...`) do NOT flip. Live proof: the g175-shaped smoke classified sensitive on this detector ALONE; the password-policy smoke stayed internal.
- **Golden replay (post-deploy, vs `baseline-full-2026-11-15`):** 180 items; **Group S CLEAN** — g175/g177/g179/g180 flip `internal → sensitive` and re-route inside the wall (free-router / groq, private-free); g176 re-routes off north-mini (F-16). 82 selection diffs, **0 undeclared** (`tests/golden/declared-changes-2026-11-15-v091.json`; capture `tests/golden/capture-2026-11-15-v091.json`): F-17b candidate removals, F-16/F-18 sensitive-set changes, the 2026-11-15 battery write (which landed after that baseline was captured — tool lists only), and live-state ordering noise. Remaining verifier findings (63) are exactly the known Groups L/M/P (F-10 family + ranking + spec-conformant paid-first) — unchanged in character, NOT addressed here by design. The 5 starter-known violations reproduce.
- **Live smokes (all PASS):** sensitive turn cannot land on dots-3/north-mini (route log `sensitive_spillway_excluded`); pins drop per J-1 with exact reasons; prose-credential turn classifies sensitive; password-discussion turn does not; non-sensitive chat / tool / stream unaffected (200s, sensible serves); alert dry-run as above. Post-recreate traffic watch: 22 requests in the first 15.9 minutes (production + smoke traffic), all HTTP 200, zero `stream_bound_timeout`.

## [0.9.0] — 2026-11-15 (DEPLOYED 2026-11-15 01:58:08 MST)

**Measured tooling dimension (Cycle 1 R4)** — owner's go 2026-11-14
22:58 MST ("rebase, replay, then deploy if clean"); the first gate
run halted at the push-approval timeout and the owner resumed it
2026-11-15 01:43 MST. Decision package:
`docs/cycles/release-v0.9.0-decision.md`. Battery:
`docs/cycles/tooling-battery-2026-11-13.md`. Golden cross-check:
`docs/cycles/golden-crosscheck-2026-11-14.md`.

- **`quality.tooling`, written only by the battery.** Optional
  per-entry tooling score (T1 exact-args / T2 160-line payload /
  T3 parallel / T4 follow-through / T5 error-recovery); 54 of 73
  enabled entries measured (50 scored 5, three scored 4, one
  scored 2 provisional), `verified_until` 2026-12-13 on the 54.
  Provenance is a registry invariant — no hand-entered scores.
- **One selection helper at every ranking site.** Tool-classified
  turns rank by the tooling score; the fallback rule is load-
  bearing: an entry with no measured score ranks for tooling
  exactly as it did before R4.
- **`supports_tools` corrected to false** for
  `novita/llama-3.1-8b` and `deepinfra/mistral-small-24b` on
  verbatim upstream errors.
- **Registry merged at the rebase:** v0.8.3's cached-input
  pricing and R4's tooling fields both survive, proven
  field-for-field in both directions against both parents. New
  registry md5 `460b6df48f1d8217eb2c11622b70cb4f`, byte-identical
  across GitHub main, VM103 build/src and VM103 state
  (10001:10001, 644).
- **Golden replay cross-check** (the check v0.8.2 owed R4): **0
  new violations, exactly the 5 known** (g07/g08/g15 + g17/g19 =
  F-10, still open); 19 diffs all declared — 16 belong to the
  v0.8.2/v0.8.3 line (the baseline predates them), 3 are R4's
  intended tooling effect (g20–g22: first choice
  nemotron-3-ultra-free → cloudflare/llama-4-scout, tooling 5).
  v0.8.2's `cooldown_stream_abort` produced no shadow diff.
- **DEPLOY RECORD (2026-11-15):** branch rebased onto main
  `9c5290f`; suite **681 passed** on the final tree (run twice).
  PR #7's merge path was blocked — the push-files branch history
  leaves the PR conflicted (`mergeable_state: dirty`) and no CI
  fired on its synchronize / ready-for-review / reopen events —
  so the resolved tree landed direct to main as `e7372a98` per
  the v0.8.0 precedent (PR #4 → `9f811cb6`), byte-identical to
  PR #7 head `3392c5ac`. **CI #43, run 43696746550, SUCCESS**
  (pytest, Python 3.12) on main. PR #7 closed with an
  explanation comment; the branch is retained as archive.
  Image `192.168.0.28:5000/sovereign-router:v0.9.0`
  (sha256:281ba4898ea832fc5b9ea0048ef4104f7d439fc189a2393c03e274d1f0b316f1);
  gateway recreated 01:58:08 MST; /health version **0.9.0**,
  81 models, clients [crm, hermes, n8n, openclaw], sidecar ok,
  alert chain ok; prod :6070 still v0.2.1. Snapshot
  `snapshots/pre-v090-20261114` (v0.8.3 image + pre-merge
  registry, the pair rollback needs).
- **Smokes all pass:** (a) tool-bearing request served by a
  battery-5 entry (`google/gemini-3.1-flash-lite`, tooling 5)
  with a well-formed tool call; (b) plain chat exact echo; (c)
  **v0.8.3 regression check** — pinned `deepseek/deepseek-v4-pro`
  session ×3: cold turn cached 0, warm turns cached 8,832 of
  8,969 (98.5%), recorded cost equal to the hand-computed
  cache-aware cost on every turn; (d) streaming 8 chunks,
  finish=stop; (e) attempt ledger flowing (25 events in the
  smoke window), zero `stream_bound_timeout`. A first (c)
  attempt capped at 120 output tokens escalated two turns to
  `meta/muse-spark-1.1` (`length_no_content` — the reasoning
  model ate the cap; correct validation behavior, re-run at
  2,000). **Traffic watch:** 76 rows in the first 20 minutes
  post-cutover, ALL 200 (hermes 60, openclaw 9, n8n 7).
- Tests: 654 → **681** (the +27 are the v0.8.2/v0.8.3 suites
  pulled in at the rebase; R4's own were already in the 654).
- **Rollback:** image → v0.8.3 AND restore `state/registry.json`
  from the pre-v090 snapshot **together** (the v0.8.x loader
  rejects the `tooling` key), then compose up -d gateway.
- **NOT in this deploy:** re-probe cadence activation (owner
  decision still open; `verified_until` expires 2026-12-13),
  the F-10 classifier-fusion fix, and the ERNIE tooling-score
  write (battery 4 measured 2026-11-15 — a follow-up registry
  task, deliberately not folded into the gated tree).

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
  out 52 → $0.00121132). Traffic watch (CORRECTED 2026-11-13 —
  the watch summary first circulated in the deploy session
  misstated this window's counts; the figures here are re-derived
  from routes.jsonl): 17 rows in the post-cutover window (6
  deploy smokes, 5 proof turns, 6 genuine OpenClaw turns), ALL
  200, no `stream_bound_timeout`; the only signals were the
  proof session's own repeat_within_60s ×5; attempt ledger 17
  served + 7 failed congested (OpenRouter free-pool 429s absorbed
  by failover — every client row still 200). Genuine fleet rows
  recorded cached_tokens > 0 within minutes: OpenClaw on
  openrouter/nemotron-3-ultra-free (13,152 / 17,536 cached) and
  groq/gpt-oss-120b (17,152 cached of 19,913 in → $0.000568). An
  evening lull followed (no rows after 21:38 MST; gateway
  verified healthy). **Dashboard:** exporter v1.2 adds
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
