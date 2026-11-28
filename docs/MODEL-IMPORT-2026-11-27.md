# Model import from the free-claude-code provider list (v0.10.7, 2026-11-27)

Jordan's order, 2026-11-27 23:06 MST: "Lets pull the relevant models both free and not into ours."

**Scope.** This is a registry-only change. Gateway code and VERSION are unchanged (0.10.6). Every imported entry ships `enabled: false`, so routing does not change until a probe passes and Jordan rules. The free-claude-code (FCC, AGPL-3.0) README was used only as a list of candidate provider and model IDs. No FCC code was copied.

**Evidence grades.**
- **VERIFIED** means read on 2026-11-27 from the provider's own public page or public API:
  - DeepInfra `/models/list`
  - HF router `/v1/models`
  - NVIDIA `/v1/models`
  - OrcaRouter `/v1/models`
  - Kilo `/api/v1/models` plus its docs
  - Cloudflare and ZenMux model pages
- **REPORTED** means the claim appears only in FCC or a third-party catalog.
- **ASSUMED** means it is our inference.

## Imported (12, all disabled)

| id | lane | price in/out $/M | ctx | tools | status | why |
|---|---|---|---|---|---|---|
| deepinfra/qwen3-coder-480b-turbo | paid | 0.30 / 1.00 (V) | 262k (V) | yes (V) | PENDING-PROBE | Direct-keyed cheap coder. Output costs 45% less than nrouter's $1.80. |
| deepinfra/qwen3-coder-30b | paid | 0.07 / 0.26 (V) | 262k (V) | yes (V) | PENDING-PROBE | Near-free small coder for L1 code. |
| deepinfra/deepseek-v4.1-flash | paid | 0.20 / 0.60 list, 0.14 / 0.42 effective (V) | 1M (V) | yes (V) | PENDING-PROBE | Successor to the spillway workhorse. Adds vision. |
| deepinfra/nemotron-3-ultra | paid | 0.50 / 2.20 (V) | 262k (V) | yes (V) | PENDING-PROBE | Private paid twin of the public OR free 550B. |
| deepinfra/kimi-k2.6 | paid | 0.75 / 3.50 (V) | 262k (V) | yes (V) | PENDING-PROBE | Strong reasoner. Probe with reasoning off (OR copy burned reasoning). |
| huggingface/qwen3-coder-480b | paid | 0.30 / 1.00 (V) | 262k (V) | yes (V) | PENDING-PROBE | Coder on the HF billing pool, pinned `:deepinfra`. Non-sensitive only. |
| nvidia/nemotron-3-ultra | public-only-free | 0 | 262k (seed) | yes (R) | PENDING-PROBE | Second free pool for the 550B. NIM trial terms: evaluation only, and NIM logs. |
| nvidia/kimi-k3.1-pro | public-only-free | 0 | 262k (seed) | R | PENDING-PROBE | Frontier reasoner, free on NIM if the account can reach it. A model can be listed without the account having access. |
| orcarouter/deepseek-v4.1-flash-free | public-only-free | 0 (V) | 1M (V) | R | PENDING-KEY | Free V4.1 Flash. Upstream retention unknown. |
| orcarouter/glm-5.3-flash-free | public-only-free | 0 (V) | 1M (V) | R | PENDING-KEY | Free GLM 5.3 Flash. |
| kilo/auto-free | public-only-free | 0 (V) | 256k (V) | yes (V) | PENDING-KEY | Kilo says it may log. It mostly reaches models we already route. |
| kilo/beam-free | public-only-free | 0 (V) | 1M (V) | yes (V) | PENDING-KEY | New free coding reasoner. Retention unknown. |

V = verified, R = reported.

**Key coverage (names only, read-only on VM103).**
- Existing key files cover deepinfra, huggingface and nvidia, so 8 entries are pending a probe only.
- orcarouter and kilo have no key. Those 4 entries are pending Jordan's signup/key approval.
- `jina` and `voyage` keys exist on VM103, but nothing in the registry uses them (embeddings/rerank).

## Considered and NOT imported (with reason)

- **Free Qwen coder**: no verified free Qwen coder exists today.
  - OpenRouter's and Kilo's live `:free` catalogs contain no Qwen coder (verified).
  - xKiro's `qwen3.7-flash:free` is reported only: no public API docs or data policy, and payment is VN bank/crypto.
  - Ollama Cloud has no free tier (pay as you go, Pro $20/month). `qwen3-coder:480b-cloud` exists but is absent from Ollama's price table, and our Ollama account has returned 402 `account_dead` since 2026-11-26.
  - The cheapest verified Qwen3-Coder-480B is $0.30/$1.00 (DeepInfra Turbo, direct or via HF).
- **Cloudflare `@cf/moonshotai/kimi-k2.6`**: paid-plan only and Deprecated 11/20/2026 (verified on the CF model page).
- **ZenMux `deepseek-v4-flash-free`**: marked "Sunset" on ZenMux's page (verified).
- **TokenRouter / NaraRouter `kimi-k3-free`**: reported by FCC only.
  - The TokenRouter catalog requires auth.
  - NaraRouter's public `/v1/models` lists no kimi-k3-free (verified absent).
  - Both claim ZDR (verified on their homepages).
- **Kilo `stealth/glyph-cluster`**: Kilo's listing says the provider retains prompts and outputs (verified).
- **HF-only backends** (Qwen3-Coder-Next via parasail, Kimi-K2.7-Code via coreweave): these would break the HF `:deepinfra`-only posture invariant. They would need Jordan's ruling to widen it.

## Image-generation shortlist (docs only; no image class in the router)

| candidate | cost | posture |
|---|---|---|
| Cloudflare `@cf/black-forest-labs/flux-1-schnell` | Inside the CF Workers AI neuron pool. Per-image neuron cost not confirmed. | ZDR "Yes" (verified on CF page). Key exists. |
| DeepInfra `FLUX-1-schnell` | $0.0005/image unit (verified) | Direct account; key exists. |
| DeepInfra `FLUX-1-dev` / `FLUX-2-dev` | $0.009 / $0.010 per unit (verified) | Same. |
| HF `black-forest-labs/FLUX.1-schnell` | Via fal-ai or wavespeed (live, verified). Price per provider. | Third-party backends. |

## Enable path

1. Run a chat probe and a tool probe per entry (max_tokens 4 / get_weather).
2. Run the tooling battery, then set the `tooling` score and `tooling_source`.
3. Jordan rules on enabling.

PENDING-KEY entries need Jordan's signup first.
