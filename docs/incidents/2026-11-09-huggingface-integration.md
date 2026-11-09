# Hugging Face Integration — Probe, a Corrected Diagnosis, and Production Enable (2026-11-09)

**Status: CLOSED — both Hugging Face routes are ENABLED in production and verified end-to-end through the router.**

## Summary

Two Hugging Face Inference Providers routes were added to the Sovereign Router as a separately-billed second pool for models the cascade already depends on:

| Registry entry | Upstream model string | Lane | Price (per 1M in/out) | Notes |
|---|---|---|---|---|
| `huggingface/deepseek-v4-flash` | `deepseek-ai/DeepSeek-V4-Flash:deepinfra` | paid | $0.09 / $0.18 | Backend pinned to DeepInfra at enable time; same iron as the direct account, billed to the HF account instead |
| `huggingface/gpt-oss-120b-cerebras` | `openai/gpt-oss-120b:cerebras` | paid | $0.35 / $0.75 | Fastest host for this model per HF's catalog (~1,074 tok/s vs Groq ~421) |

Both are flagged `spillway_sensitive_excluded` (non-sensitive traffic only): HF routes prompts to third-party backends whose data posture varies by provider. Both are cost-ordered by the existing cascade; no routing-code changes were needed.

## Why this exists

The 2026-11-09 rate-limit cascade (see `2026-11-09-rate-limit-cascade.md`) began when the direct DeepInfra account's credits died silently at 04:48 MST and the spillway burned ~$22 on premium before alerting caught up. The HF Flash route bills a different account (prepaid, so it can run dry but never run up a bill), giving the spillway workhorse a second funding pool.

## Timeline (2026-11-09, America/Denver)

- **~13:20** — Registry probe entries created DISABLED alongside the Groq/Ollama registry corrections. HF token ("SovereignRouter", fine-grained, `inference.serverless.write`) submitted by the owner through a secure capture page into Muse's connector store.
- **~13:47** — First probe: token valid, but chat calls returned 402 — the HF account held no prepaid credits.
- **~14:05** — Owner topped up. Flash route verified serving (HTTP 200; HF's usage block priced the call at DeepInfra parity — pass-through confirmed firsthand). All gpt-oss routes returned an empty-body 403, initially (wrongly) recorded as an HF-side block.
- **~14:12** — Owner asked for an honest evaluation and then a root-cause investigation of the 403.
- **~14:15** — Root cause found (below); records corrected the same hour.
- **~14:19** — Fresh verification calls to both models passed; snapshot `pre-hf-enable-20261109` and registry backup taken; enable staged (141 tests pass).
- **~14:25** — Owner pasted the token in chat at his explicit choice; installed as VM103 `secrets/huggingface.key` (37 bytes, mode 600, sha256 prefix `773f69de01fc`; the value is recorded nowhere else).
- **~14:26–14:30** — Deploy incident (below), recovered; both models proven serving through the router itself.
- **~14:33** — Candidate sweep of the wider HF catalog (below).

## The 403 investigation — and a correction

**First (wrong) conclusion:** gpt-oss models are blocked on HF's side for this account.

**How it was disproven:** reading the raw 403 body instead of the parsed JSON showed a Cloudflare block page naming the *provider's* domain (`api.cerebras.ai` / `api.groq.com`, error 1010). Controlled tests then isolated the variable:

- **HF forwards the caller's `User-Agent` header to the serving provider.** Cerebras's and Groq's Cloudflare configurations ban the `Python-urllib` default user-agent — which is what the probe script sent. DeepInfra runs no such rule, which is why Flash worked throughout.
- With an SDK or browser user-agent, `openai/gpt-oss-120b:cerebras` served immediately: small turn 200 (0.16 s at provider), tool-call turn 200 with well-formed arguments, 36,477-token turn 200.
- The gateway's own httpx user-agent was tested separately and passes — **the Sovereign Router was never affected.** Only the probe client was.

The connector helper (`custom.huggingface/bin/hf.py`) now sends an SDK-style user-agent, and its doc records the failure mode: a 403 with an HTML body naming a provider domain + Cloudflare 1010 is a forwarded-UA ban, not a token problem.

## Deploy incident: key-file ownership

The owner's token was first installed root-owned. Every working provider key file on VM103 is owned by the gateway's uid, **10001:10001** (mode 600); the gateway's startup key loader died on `PermissionError` and the container crash-looped for ~4 minutes. `chown 10001:10001` + restart recovered it (healthy, 15 models). Standing rule recorded in AGENTS.md: new key files get `chown 10001:10001; chmod 600`, then a restart and a /health check before declaring done.

## End-to-end verification (through the router, client `crm`)

- Pinned `huggingface/deepseek-v4-flash` → HTTP 200, exact reply; routes.jsonl: lane `paid`, cost $0.0000036, upstream 1,117 ms.
- Pinned `huggingface/gpt-oss-120b-cerebras` → HTTP 200, exact reply; cost $0.0000636, upstream 356 ms.

## Candidate sweep (owner's challenge: "did you try ALL the models?")

HF's chat router lists 125 servable models (the Hub browser counts 6,430 repos with any inference-provider support — mostly base models, fine-tunes and community variants). Beyond the two enabled targets, 12 credible cascade candidates were probed; 10 serve:

| Model:provider | Catalog price (per 1M) | Result |
|---|---|---|
| `openai/gpt-oss-120b:novita` | $0.05 / $0.25 | 200 — cheapest 120B route seen anywhere |
| `Qwen/Qwen3.8-27B:deepinfra` | $0.20 / $2.50 | 200 — vs $0.80 / $4.00 on the direct Groq route |
| `openai/gpt-oss-20b:deepinfra` | $0.03 / $0.14 | 200 — undercuts Groq direct ($0.075 / $0.30) |
| `Qwen/Qwen3.8-27B:cerebras` | $0.99 / $1.49 | 200 — fastest; 65k context cap |
| `zai-org/GLM-5.3:deepinfra` | $0.90 / $4.00 | 200 |
| `deepseek-ai/DeepSeek-V4-Pro:deepinfra` | $1.30 / $2.60 | 200 |
| `meta-llama/Llama-3.3-70B-Instruct:novita` | $0.14 / $0.40 | 200 |
| `google/gemma-4-31B-it:novita` | $0.14 / $0.40 | 200 |
| `moonshotai/Kimi-K3:deepinfra` | $2.85 / $14.25 | 200 (premium class) |
| `meta-models/Muse-Spark-Open-816B-A42B:baseten` | $1.25 / $4.25 | 200 |
| `openai/gpt-oss-120b:together` | $0.15 / $0.60 | **402 — "Pay-as-you go is not enabled for provider together yet"**: some providers need a per-provider PAYG toggle in the owner's HF settings before they serve. Cerebras, Groq, DeepInfra, Novita and Baseten needed no toggle. |
| `deepseek-ai/DeepSeek-V4-Flash:novita` | unlisted | 404 — stale catalog row |

No sweep entries were added to the registry. Findings stand for the owner's next call; the standouts on price are Qwen3.8-27B via DeepInfra and gpt-oss-120b via Novita.

## Current state and open items

- Both HF entries ENABLED in production (registry on GitHub `main`, VM103 state, and the running gateway all in sync; version 0.5.7, 15 models).
- HF billing is prepaid on the owner's HF account; HF spend shows in the owner's HF Inference Providers overview (day total for all probing ≈ $0.01).
- Open (owner's settings, optional): enable pay-as-you-go for Together in HF settings if Together routes are ever wanted.
- Open (owner's call): whether any sweep candidates earn registry probe entries.
- The HF token in Muse's connector store remains over-scoped (repo/job permissions beyond inference); trimming it to inference-only was flagged to the owner as optional tidy-up.
