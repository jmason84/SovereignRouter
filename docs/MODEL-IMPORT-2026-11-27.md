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

## Probe results (added 2026-11-27, approved by Jordan 11:18 PM MT)

Minimal direct calls from VM103 using the existing key files (read in-process, never printed or logged), against each provider's own endpoint. The live router was not touched. Each model got three calls: a one-word chat, one tool-call turn (`get_weather`) and a code prompt (`is_prime`), where the returned code was extracted and executed against assertions. Costs are estimated from each response's reported token usage times the registry price. They are not invoices.

| Entry | chat | tool call (get_weather{city:Denver}) | code (executed) | latency chat/tool/code | est. cost | result |
|---|---|---|---|---|---|---|
| deepinfra/qwen3-coder-480b-turbo | PASS | PASS | PASS | 0.4s / 0.8s / 1.0s | $0.00021 | READY |
| deepinfra/qwen3-coder-30b | PASS | PASS | FAIL (length at 1500 tok) then PASS on retry (1332 tok) | 10.7s / 2.3s / 24.3s, retry 3.5s / 1.2s / 10.7s | $0.0005 x 2 runs | NOT READY: verbose/flaky |
| deepinfra/deepseek-v4.1-flash | PASS | PASS | PASS | 0.7s / 0.8s / 1.0s | $0.00015 | READY |
| deepinfra/nemotron-3-ultra | PASS | PASS | PASS | 0.9s / 0.7s / 1.4s | $0.00087 | READY |
| deepinfra/kimi-k2.6 | PASS (91 tok) | PASS | PASS | 1.0s / 0.8s / 2.3s | $0.0022 | READY |
| huggingface/qwen3-coder-480b | PASS | PASS | PASS | 0.7s / 0.9s / 1.1s | $0.00021 | READY (backup coder) |
| nvidia/nemotron-3-ultra | PASS | PASS | PASS | 1.5s / 1.3s / 2.8s | $0 | READY (public lane, eval terms) |
| nvidia/kimi-k3.1-pro | PASS | PASS | PASS | 49s / 54s / 76s | $0 | READY but very slow (batch only) |

- No HTTP errors or error bodies on any call. Total spend for the full run and the retry was about $0.005 (cap was $0.50).
- Seven entries are marked READY-TO-ENABLE in their registry notes, and one NOT-READY. **All eight remain `enabled: false`** until Jordan says go. This does not change routing.
- The NVIDIA Kimi latency is 8x the seed, so its `latency_p50_ms` is now 50000. Check the gateway's upstream timeout before any enable.
- The probe covers one sample per test. It is not the tooling battery, so no `tooling` score was set.

## Backup coder order (code class)

`CLASS_PREFERRED["code"]` (shadow ranking, `src/tiering.py`): the free coder stays `unorouter/codestral-latest`. For cheap, the order is `nrouter/qwen3-coder-480b-a35b`, then `deepinfra/qwen3-coder-480b-turbo`, then `huggingface/qwen3-coder-480b` as the backup coder. The HF entry is non-sensitive only (`spillway_sensitive_excluded`) and, like the DeepInfra one, still disabled, so the shadow ranking never sees it until it is enabled.

## HF router catalog as a discovery and price-drift source

`tools/hf_catalog.py` reads HF's public `https://router.huggingface.co/v1/models`. It sends no credential, writes nothing and is report-only. Both existing tripwires opt in:

- `python3 tools/price_check.py --map-file <litellm.json> --hf-url https://router.huggingface.co/v1/models` adds a "Hugging Face router catalog cross-check". It compares each HF-pinned entry (and any direct entry whose provider is also an HF backend for the same model) against the HF listing for that backend, and flags drift or a backend that is no longer `live`. `--strict` also exits 1 on HF drift.
- `python3 tools/discovery_tripwire.py --hf-url https://router.huggingface.co/v1/models [--hf-tools-only]` nominates HF-listed models the registry does not carry, with the cheapest live provider, price, context and tool flag as provenance.
- Both take `--hf-file` for an offline snapshot.

First live run (2026-11-27): 106 catalog models; 12 registry entries compared; 4 flagged for price drift (verification tasks only, not corrected):
- deepinfra/qwen3.8-27b and huggingface/qwen3.8-27b: registry $0.20/$2.50, HF lists the deepinfra backend at $0.15/$1.875.
- huggingface/glm-5.3-flash: registry $0.15/$0.50, HF lists the deepinfra backend at $0.09/$0.30.
- deepinfra/deepseek-v4.1-flash: registry carries the $0.20/$0.60 list rate, while HF passes through the discounted $0.14/$0.42. This is deliberate and noted in the entry.
- 63 tool-capable models are not in the registry (nominations).
