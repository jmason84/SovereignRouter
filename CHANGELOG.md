# Changelog

All notable changes to the Sovereign Router gateway are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) — the gateway
reports its version at `GET /health` (`version` field) and in the
`X-Sovereign-*` response metadata. The routing *policy* history lives in
`docs/ROUTING-PRINCIPLES.md`; incident detail in `docs/incidents/`.

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
