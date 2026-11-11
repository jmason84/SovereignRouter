# Sovereign Router v0.5 — Owner Decision Sheet

**For:** Jordan Mason · **Spec:** v0.5.3 (2026-10-31) · **Prepared:** 2026-11-01
**How to use:** one line per decision — circle/write your answer, or reply with the numbers (e.g. "1: ON $25, 2: defaults, 3: …"). Defaults are the spec's proposals. Nothing deploys past the phase each decision gates until you answer it.
> **STATUS: ANSWERED — Jordan accepted the proposed defaults on all 7 decisions, 2026-11-01 23:58 MST.**
> 1. Emergency switch **ON at $25/day** · 2. Premium tier **kept as-is**, alerts **$20/mo** and **>20/day** · 3. Sensitive-data providers cleared: **none** · 4. Targets **Hermes $1 / OpenClaw $3 / n8n $0.50**, runaway defaults as proposed · 5. Judged sample **OFF** · 6. **Review Katanemo license first** (review commissioned 2026-11-01) · 7. n8n joins **Phase 3, last**.
> Configs updated to match (compose emergency default ON, clients/registry already at defaults). Deploy still requires a separate explicit go.
> **AMENDED SINCE — see the Amendments section at the end of this file.** Current rules: `docs/ROUTING-PRINCIPLES.md`.

**Already decided (not on this sheet):** spending is *not* capped — no request is ever refused for budget reasons (Jordan, 2026-10-31). Costs are steered, targets are alerts, not caps.

---

### 1. Emergency paid-lane switch — on/off, and at what daily level?
- **Proposed default: ON at $25/day** across all paid lanes (~8× today's production spend). When tripped: paid + premium lanes off until you clear it or the day ends; traffic moves to free models; requests no free model can serve get `429` with the reason.
- **Options / cost / risk:** OFF = zero risk of a legitimate heavy day being forced onto free models, but a looping agent or leaked key burns paid credit until the hourly runaway alarm (alert only, stops nothing) gets your attention. ON at $25 = worst-case paid exposure ≈ $25/day plus free-lane load; risk is a false trip on a genuinely busy day degrading large requests to 429. ON higher (e.g. $50) = more headroom, proportionally more runaway exposure.
- **Gates:** Phase 1 build (the switch is built/configured to this answer; can be changed later in config, but the default ships with the image config).

**Your answer:** ☐ ON $25/day ☐ ON $______/day ☐ OFF

### 2. Which premium models does OpenClaw keep, and what are the premium alert levels?
- **Proposed default:** keep the v0.2.1 premium tier as-is; **premium monthly alert $20/month; daily premium-request alert at >20 requests/day.** (Context: premium was 104 of 656 production requests and $22.36 of spend in the audited baseline — roughly one month's alert budget at current pace, so $20 will alert in a normal month. That is the point of an alert, but expect it.)
- **Options / cost / risk:** Trim the premium tier = lower ceiling on level-5 cost, risk of weaker answers on critical/production work. Higher alerts (e.g. $40/mo, >40/day) = fewer interruptions, slower warning on premium creep. Lower = earlier warning, more alert noise. Every premium response already carries its cost in `X-Sovereign-Cost-Estimate`.
- **Gates:** **Phase 3, Week 2 entry condition** — OpenClaw does not move to v0.5 until these levels are set (spec, explicit).

**Your answer:** Keep tier: ☐ as-is ☐ trim (note which): __________ · Monthly alert: $______ · Daily count alert: ______

### 3. Which providers are cleared for sensitive data (secrets detected in a prompt)?
- **Proposed default (spec's interim position): NONE cleared.** Until you clear one, a request flagged sensitive goes only to the private lane with the strongest verified no-retention terms, and the event is alerted. The scan match itself is never logged.
- **Options / cost / risk:** Clearing a provider (name it explicitly) = sensitive requests get that provider's full model choice; risk is exactly that provider's retention/terms being wrong for real secrets. Clearing none = safest privacy posture; cost is sensitive requests confined to one lane (possible queueing / weaker model / 403 if no eligible lane). Free *public* lanes are never eligible for sensitive data either way.
- **Gates:** Phase 1 configuration (the cleared list is a policy input); routing behaviour from first canary traffic in Phase 3. The scan runs log-only in Phase 0 regardless.

**Your answer:** Cleared providers: ☐ none (default) ☐ these: ______________________________

### 4. Client daily cost targets and runaway thresholds
- **Proposed defaults:** daily targets (steer + alert, never a cap) — **Hermes $1/day, OpenClaw $3/day, n8n $0.50/day.** Runaway alarm, first 14 days (fixed, no history yet): paid spend >$0.50 in an hour, or a client >2× its daily target; after 14 days: an hour >5× the trailing 7-day hourly average, $1 minimum. Above target: levels 1–3 prefer cheaper models, level 4 held to the cheapest quality-4 model, premium only on explicit request, one alert that day.
- **Options / cost / risk:** Targets too low = constant above-target steering and daily alerts become noise you ignore (baseline note: Hermes spent only $0.13 in the audited window, OpenClaw is the real spender — the $3 OpenClaw target will be crossed on premium-heavy days by design). Targets too high = steering rarely engages and slow leaks stay invisible longer. These are **provisional by spec** — Phase 0 measures the real split and the targets are reset from that data before Phase 3, so a wrong number here is cheap to fix later.
- **Gates:** Phase 1 configuration; **reset from Phase 0 data is a Phase 3 entry condition.**

**Your answer:** ☐ Defaults ☐ Changed: Hermes $______ OpenClaw $______ n8n $______ · Runaway: ☐ defaults ☐ changed: __________

### 5. Allow the 2% judged quality sample?
- **Proposed default (spec): OFF unless approved.** A strong model judges a 2% sample of internal-class requests for answer quality, feeding the learning loop.
- **Options / cost / risk:** ON = ≈ **$1/month** and materially better quality scores for Phase 4 registry tuning. The real cost is privacy, not money: **the judge sees prompt text** — the one place in the design where prompt text leaves for evaluation. OFF = zero prompt-text exposure, and the learning loop relies on outcome signals only (escalations, retries, tool errors).
- **Gates:** Phase 4 only. Not needed for launch; can be approved later without any rebuild.

**Your answer:** ☐ OFF (default) ☐ ON — 2% internal-class sample

### 6. Arch-Router (Katanemo) license — accept, or use the Qwen fallback?
- **Proposed default:** review the Katanemo license for Arch-Router-1.5B **before Phase 2 deployment**; if unsuitable, fall back to a **Qwen 1.5B–3B instruct model** with the same route-description prompt (the alexrudloff/llmrouter approach). No license answer = no classifier model ships.
- **Options / cost / risk:** Accept (if terms allow this use) = the purpose-built router model, best expected classification quality; the risk is purely the license terms, which is why the review is the gate, not the benchmark. Qwen fallback = permissive-license certainty, likely weaker zero-shot routing (mitigated by the rules + embedding layers in front of it, and by the benchmark gate: Arch-Router/Qwen runs in the request path only if p95 < 1.2 s on VM103, otherwise it labels offline).
- **Gates:** **Phase 2 entry** — the sidecar cannot be finalised without it.

**Your answer:** ☐ Review license first (default) — decision after review ☐ Go straight to Qwen fallback

> **OUTCOME (2026-11-02):** License reviewed (docs/LICENSE-REVIEW-arch-router.md). Arch-Router-1.5B is under the Katanemo Community License (DigitalOcean, LLC) — commercial use requires a separate written license, no internal-use exception. **NOT SUITABLE → Qwen fallback in force: Qwen2.5-1.5B-Instruct (Apache-2.0)** (or Qwen3-1.7B/4B). Qwen2.5-3B-Instruct is excluded — Qwen Research License, non-commercial only.

### 7. Does n8n join in Phase 3, or later?
- **Proposed default (spec sequence): n8n joins in Phase 3, last** — after Hermes (Week 1) and OpenClaw (Week 2), on its own $0.50/day target, premium off.
- **Options / cost / risk:** In Phase 3 = one canary period validates all three clients and the alert-polling workflow (which itself runs on n8n) is exercised as a client too; risk is a third client's traffic complicating soak diagnosis. Later = cleaner soak attribution for Hermes/OpenClaw; cost is n8n stays on its current path longer and gets its own mini-canary afterwards.
- **Gates:** Phase 3 sequencing only. Does not block Hermes or OpenClaw.

**Your answer:** ☐ Phase 3, last (default) ☐ Later (after soak)

---

*Answers return to the deploy runbook as configuration. Until then the stack stays at the deploy gate: packaged, not deployed — production v0.2.1 on :6070 remains the live router and the rollback path.*

---

## Amendments (the sheet above is the 2026-11-01 record, kept unedited)

- **2026-11-02 — Decision #6 closed.** License review complete: Arch-Router NOT suitable (Katanemo Community License); Qwen2.5-1.5B-Instruct fallback in force (see the outcome note under #6).
- **2026-11-03 — Decision #4 targets amended.** From Phase 0 data the reset targets were bumped 5x by the owner: **Hermes $1.25 / OpenClaw $5.00 / n8n $2.50 per day** (steer + alert, still never caps).
- **2026-11-03 — Key rotation CANCELLED.** Provider/client key rotation, a precondition in the early deploy documents, was skipped by the owner's direct order and is closed permanently. Do not resurrect it.
- **2026-11-07 — Spillway supersedes the rescue design (v0.5.5/v0.5.6).** Paid lanes are reached by the free-exhaustion spillway in strict cost order, not by the earlier sensitive-rescue pin. SiliconFlow joins as a non-sensitive-only provider.
- **2026-11-09 — Decision #1 SUPERSEDED: the emergency switch is WARN-ONLY.** After the hard switch tripped that morning and 429-walled the fleets, the owner ruled the $25/day level alerts but never cuts the paid lanes (`SOVEREIGN_EMERGENCY_WARN_ONLY=1`; v0.5.7). The hard switch stays off unless the owner reorders it.
- **2026-11-09 — Decision #4 extended: a fourth client.** TwentyCRM (`crm`) joined at $1.25/day, lanes private-free + paid, premium off. The same day, Hugging Face joined as a second billing pool for DeepSeek V4 Flash plus a Cerebras route to gpt-oss-120b, non-sensitive-only like SiliconFlow.
