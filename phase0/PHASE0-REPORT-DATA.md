# Phase 0 Report — Baseline Data

**Status:** PARTIAL — baseline snapshot measured live 2026-11-01 from the
production v0.2.1 usage logs. The 3–5 day v0.3.3 collection window (spec
Phase 0 acceptance) has **not** run yet; everything in §3 is still to be
collected before targets are reset. Do not treat §1 as the Phase 0 report —
it is the pre-Phase-0 baseline the collection will be compared against.

Tooling: `analyze_usage.py` in this folder reproduces every table below
from raw jsonl (v0.2.1 usage logs and v0.3.3 route logs, Markdown or JSON).

---

## 1. Measured baseline — v0.2.1 production (verified live, 2026-11-01)

### Volume by client — 656 requests total

| Client | Requests | Share |
|---|---|---|
| OpenClaw | 508 | 77.4% |
| Hermes | 142 | 21.6% |
| admin | 6 | 0.9% |
| **Total** | **656** | |

### Tier distribution (all clients)

| Tier | Requests | Share |
|---|---|---|
| cheap | 402 | 61.3% |
| strong | 82 | 12.5% |
| code | 68 | 10.4% |
| premium | 104 | **15.9%** |

### Model distribution

| Model | Requests |
|---|---|
| gpt-oss-120b (DeepInfra) | 373 |
| openai/gpt-5.5 | 82 |
| DeepSeek-V4-Flash | 77 |
| Qwen3-Coder-480B | 67 |
| Llama-3.3-70B | 27 |
| claude-sonnet-4-6 | 22 |
| gemini-2.5-flash | 8 |

### Cost by client

| Client | Cost |
|---|---|
| OpenClaw | **$22.36** |
| Hermes | **$0.13** |

**≈99% of measured spend is OpenClaw.** The driver is premium: gpt-5.5 at
roughly **$0.25–0.38 per request on 49k–74k-token prompts** — agent turns
carry huge system prompts/tool definitions, and premium pricing multiplies
that on every turn. 104 premium requests (15.9% of traffic) dominate the bill
while Hermes, on the free-first v0.3.3 canary pattern, costs almost nothing.

---

## 2. Implications (read before quoting any target)

1. **The provisional targets cannot be judged yet.** The spec's working
   figures — 50% blended cost cut, OpenClaw 60% free, Hermes 30–40% free —
   are explicitly provisional until Phase 0 collects the **v0.3.3 difficulty
   (level) split**. The v0.2.1 logs have tiers but no levels, and they
   describe different traffic from the canary; comparing v0.5 against them
   directly would repeat the baseline error the v0.5.3 review flagged.
2. **Premium discipline for OpenClaw is the single largest lever.** Nothing
   else is close: ~99% of spend sits in one client, and premium turns at
   49k–74k tokens are its engine. Concretely: premium only on explicit
   request or true L5, the proposed $20/month premium alert and
   >20-premium-requests/day alert (set before OpenClaw moves, Phase 3 entry
   condition), and prompt-size reduction for OpenClaw agent turns.
3. **Hermes is already nearly free** ($0.13) under free-first routing; its
   Phase 0 value is the *level distribution and failed-turn rate*, not cost.
   The Hermes question for v0.5 is quality (are cheap answers failing and
   retrying?), not spend.
4. **The classifier is the second lever.** v0.3.3's keyword rule marks most
   Hermes-style traffic complex; the combined keyword+verb rule (flagged,
   `patch_v05_phase0.py`) plus the embedding classifier exist to move the
   L1/L2 share to free models without under-serving real L4/L5 work
   (acceptance: true L4/L5 → ≤L3 under 5%).

---

## 3. Still to be collected (3–5 days of v0.3.3 route logs)

Per spec Phase 0 acceptance — **none of this exists yet**:

- [ ] Per-client free/paid split on v0.3.3 (Hermes canary), 3–5 consecutive days
- [ ] Difficulty-level distribution per client from v0.3.3 route logs
      (this is the dataset that resets the provisional targets)
- [ ] Escalation rate and failed-turn rate vs. the v0.2.1 baseline
      (acceptance: paid share falls with **no rise in failed turns**)
- [ ] Cost per request by client/level on the canary
- [ ] Sensitivity-scan false-positive rate (log-only flag; sample review)
- [ ] Output-check failure rate (malformed tool calls, length cutoffs)
- [ ] Alert chain: at least **3 consecutive daily all-clear** messages
      received (see `alert_chain.md`)
- [ ] Key hygiene: Groq + Ollama key rotation (open since 2026-10-22,
      Jordan's action at the providers); separate canary keys for
      OpenRouter + DeepInfra so the canary stops using production copies

When the window closes: run `analyze_usage.py` over the collected route
logs, append the tables here as §4, reset the targets from the measured
level split, and only then judge the 50% / 60% / 30–40% provisional figures.
