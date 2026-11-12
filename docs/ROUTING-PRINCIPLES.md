# Sovereign Router — Intent and Routing Principles

**This is the authoritative statement of what the router is for and the rules it routes by, as they stand today (v0.5.7, 2026-11-10).** The v0.5.3 spec (Drive; extract in `docs/spec-v0.5.3-extracted.txt`) is the design record, `deploy/DECISIONS.md` is the decision log, and `docs/incidents/` holds the post-mortems. Where any older document disagrees with this one — the emergency switch is the known case — this document wins, and the ruling that changed it is cited inline.

## Intent

One OpenAI-compatible endpoint (`192.168.0.166:6075`, VM103) serves the whole fleet — OpenClaw (VM102), Hermes (VM112), n8n (CT100) and TwentyCRM (CT107), each with its own key and policy — and sends every request to **the cheapest model able to do it well**.

The router exists so that no single provider's failure — a dead API key, an exhausted credit balance, a rate-limit wall, an outage — can stall the fleet. When something dies, the router moves to the next rung in cost order and *says so*. Production v0.2.1 on :6070 remains untouched as the rollback path.

## The rules

1. **Cheapest capable first.** A local classifier (rules → TF-IDF nearest-match, on VM103) assigns each request a difficulty level (1–5) and a data class. Selection walks the eligible models in strict cost order. Inside an agent's tool loop, routing is sticky (30-minute hash-keyed window) so prompt-cache pricing is preserved.

2. **Free and private lanes first; paid only on exhaustion.** The paid spillway (v0.5.5, 2026-11-07) fires only when no free candidate is eligible or every free attempt has failed: it re-selects from the paid and premium lanes in strict cost order, GPT-5.5 last. Spillway serves are marked `free_exhausted_paid_fallthrough` in the route log so the cost is always attributable.

3. **Capability escalation is not exhaustion.** If a cheap model's answer fails output validation, the request escalates one level and retries. An explicit model pin in the request is honored over the cascade.

4. **No spending caps, ever** (owner ruling, 2026-10-31). No request is refused for budget reasons — zero budget refusals is a hard target. Cost is controlled by routing and by **per-client daily targets that steer and alert, never block**: Hermes $1.25, OpenClaw $5.00, n8n $2.50, TwentyCRM $1.25. Above target, levels 1–3 steer cheaper. Premium use alerts at $20/month and >20 premium requests/day per client; an hourly runaway alarm watches for loops.

5. **The $25/day emergency level warns — it never cuts** (owner ruling, 2026-11-09, replacing the 2026-11-01 hard-switch decision). The router's job is succession, not shutdown: free first, then the next cheapest set, and the next, with a warning when a rung dies. The one time the hard switch tripped (2026-11-09, 12:29 MST) it removed every paid rung at once and 429-walled both fleets; warn-only exists so that cannot happen by design again. The hard-switch mechanism remains in code behind a flag that stays off unless the owner orders otherwise.

6. **Dead lanes announce themselves** (v0.5.7, 2026-11-09). The first upstream 401/402/403 — or daily-quota exhaustion — for a model each day raises a `lane_dead` alert, delivered by the n8n alert chain (Signal primary, email fallback, daily 08:10 MT all-clear). A 429 is congestion, not death, and does not raise it. This rule exists because DeepInfra's credits died at 04:48 on 2026-11-09 and burned silently for seven hours.

7. **Sensitive data stays on cleared ground.** A local scan flags credential-shaped and sensitive content; flagged requests route only to cleared providers. Two providers are **non-sensitive-only** by registry flag (`spillway_sensitive_excluded`): SiliconFlow and Hugging Face — both send prompts to third-party backends whose data posture varies. OpenRouter free models run with `data_collection=deny`. Route logs never contain prompt or response text.

8. **Same model, more than one billing pool, where it matters.** The spillway workhorse, DeepSeek V4 Flash, exists twice: direct from DeepInfra and via Hugging Face billed to the HF account (prepaid, so it can run dry but never run up a bill). Either pool can die without taking the model down. See `docs/incidents/2026-11-09-huggingface-integration.md`.

9. **The registry is the source of truth.** Models, prices, contexts and provider rate limits live in `src/registry.json`; the gateway reads a state-dir copy that is kept byte-identical to this repo. Rate limits are seeded from each provider's *actual plan*, verified against the provider — the 2026-11-09 corrections (Groq free-tier leftovers, an Ollama daily cap that turned out to be our own seed) are the standing lesson.

10. **Change discipline.** Snapshot and backup first, tests green, deploy, then verify end-to-end *through the router itself* and loop until clean. Provider keys are mode-600 files on VM103 owned by the gateway's uid (10001:10001); raw keys — provider or client — never enter this repository. Client identity is a key hash in `clients.json`; `src/clients.example.json` mirrors its shape with placeholder hashes.

## How a request flows

Client key (hash-checked) → sensitivity scan → classifier (level + data class) → sticky-loop check → candidate selection (client's lanes, premium gate, sensitive clearance, per-model rate-limit state) → cheapest first, failing over with every skip reason recorded → output validation, escalating one level if unusable → the governor updates per-client spend vs target, premium counters and the emergency level → one line in `routes.jsonl` (model, lane, level, cost, latency, escalation reasons — never content).

## Current shape (2026-11-10)

15 registry models across Groq, OpenRouter, Ollama, DeepInfra, SiliconFlow, Hugging Face, DeepSeek, Z.ai, OpenAI and Meta. Free/private lanes carry the majority of traffic; the paid spillway and premium lane absorb exhaustion and capability escalation. Week-1 soak (Nov 2–9) closed 2026-11-10; its final day is the incident record in `docs/incidents/2026-11-09-rate-limit-cascade.md`. **Update 2026-11-11:** the Hugging Face Cerebras route to gpt-oss-120b is disabled — it burns ~2.2k hidden reasoning tokens per turn and ignores reasoning-off controls (`docs/incidents/2026-11-11-dots-3-reasoning-empty-responses.md`); the model remains available via Groq, and the HF Flash route is unaffected.
