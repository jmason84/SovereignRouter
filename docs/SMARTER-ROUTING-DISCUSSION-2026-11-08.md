# Sovereign Router — Dealing With Lane Variance: Smarter Routing Discussion

**Date:** 2026-11-08 · **Status:** Discussion — no changes approved or built yet · **Origin:** the Nov 8, 2026 DeepSeek pin incident and rollback

---

## 1. Why this document exists

On Nov 8, 2026 the two Hermes voice profiles (default, broker-test) were pinned to a single premium model (DeepSeek V4 Pro) in an attempt to fix turn-to-turn quality variance on voice calls. Within ~80 minutes the pin:

- burned **$17.60 across 121 turns** (~$0.15–0.19/turn — full profile context re-billed every turn, zero cached tokens),
- pushed total router paid spend past the **$25/day emergency paid-lane switch**, which tripped and shut off all paid/premium lanes,
- left the pinned profiles with **zero eligible models** (a pinned premium request skips everything else by design), so the default agent died mid-conversation with a misleading "429 rate-limited" error,
- and was **rolled back in full** the same evening (configs restored, the temporary `hermes-voice` router client removed, switch cleared for the day).

The variance problem the pin aimed at is real. The pin was the wrong instrument. This document records the better directions, per Jordan's restated architecture, so the work proceeds from evidence instead of anecdotes.

## 2. The standing principle (Jordan, restated Nov 8, 2026)

> We should be able to switch to premium when necessary — but the vast majority of the time we should be using the cheaper models, and even when we do need premium, it should be the cheapest best model possible.

This is the architecture the router was already documented and built to (v0.5.5: free/private lanes first; paid spillway in strict cost order only on exhaustion; gpt-5.5 last). Any proposal below must preserve:

- **Free-first economics** — the pre-pin steady state ran near $0/day on these profiles.
- **The emergency paid-lane switch and per-client daily targets** — they worked exactly as designed on Nov 8; the failure was routing around them, not them.
- **No blanket pins** — a pin removes lane redundancy and converts any single-lane problem into a total outage.

## 3. The actual problem: variance

Same day, same profile, same system, twenty minutes apart (Nov 8, 2026):

- **13:12 call** — 14 sensible tool calls in a row; a genuinely capable agent.
- **13:17 call** — the agent rehearsed a draft greeting out loud, parroted itself and the caller for three turns, and never called the tool it was asked to test. No functionality whatsoever.

On `auto`, whichever lane/model picks up a turn effectively decides whether that turn is competent or broken. Voice is where this hurts most (live, no re-read, no quiet retry), but the same variance exists in chat. The v6/v7 voice-controller guards (repeat fillers, rehearsal/echo sanitizer, turn cap) shrink the blast radius of a bad turn; they do not reduce how often bad turns happen.

## 4. Lessons from the pin (recorded so they are not relearned)

1. **The router's premium block was a guardrail, not an obstacle.** The `hermes` client had `premium_allowed=false` by design. The pin "worked" only after creating a new client to bypass it.
2. **An unverified pin is a fictional pin.** A Nov 5 attempt at the same pin was applied but never verified — and, per the premium block, almost certainly never served a turn. Any routing change ships with an end-to-end proof or it doesn't count as shipped.
3. **Context size is the cost multiplier.** Per-token price mattered less than re-sending a very large profile context every turn at premium rates with no caching.
4. **Failure labels lie.** The router returned "all candidate models are rate-limited" when nothing had been rate-limited — the candidate set was empty by policy. Diagnostics should say "no eligible model under current policy," because the wrong label sends debugging in the wrong direction.

## 5. Direction A — Map it with data (the scorecard)

The router already logs every turn to `routes.jsonl`: client, model, lane, status, cost, token counts, task class, sensitivity level, classifier confidence, and signals including `tool_error_next_turn`.

**Proposal:** a recurring per-model scorecard, by task class:

- turns served, share of traffic
- error / 429 rate
- **tool-error-next-turn rate** — the best available proxy for "that turn was broken" (a bad turn surfaces as a tool error or a confused follow-up)
- cost per turn (and per successful turn)
- latency percentiles

With a week of this, "which lanes produce the 13:17 calls" is a lookup, not an argument. Tonight's incident turns are already labeled examples in the log.

## 6. Direction B — Prune and set floors; don't pin

Acting on the scorecard, inside the existing router mechanisms:

- **Demote or remove proven-bad models** from the `auto` pool for the task classes where they fail (voice/chat especially), instead of forcing one model for everything.
- **Use quality floors per task class** — the mechanism already exists (tonight's logs show models skipped as `quality_below_required`). A floor is a property of the pool: any of several good-enough models can serve, so no single lane's bad day is an outage.
- Keep the free-first order *within* the floor: cheapest model that clears the quality bar wins the turn.

## 7. Direction C — A small model to route more intelligently (Jordan's item)

**Where classification stands today:**

- Live routing classification is the **rules layer** (route logs show `classifier_layer: rules`, confidence 0.85).
- An **embedding sidecar** was built and benchmarked Nov 1–2, 2026 (bge-small embeddings when available, TF-IDF fallback): **90.0% exact sensitivity-level match, 96.2% within-one, 0.0% of true L4/L5 under-classified, ~35 ms p50** on the held-out set. It classifies *sensitivity* and *task class*.
- **Arch-Router was evaluated and rejected on licensing** (Katanemo Community License, now held by DigitalOcean — commercial use needs a separate written license; see `LICENSE-REVIEW-arch-router.md`).

**The gap:** today's classifier answers "how sensitive is this, and what kind of task is it?" It does **not** answer the question the variance problem actually poses: **"how strong a model does this specific turn need?"**

**Proposal:** extend the classification step with a small, local model that scores each turn's *difficulty / quality-need*, and route on that score:

| Score | Route |
|---|---|
| Low (routine, factual, short) | Free lane |
| Medium (multi-step, tools, synthesis) | Cheapest paid model that clears the task-class floor |
| High (judgment, long context, high stakes, prior-turn failure) | Cheapest **best** model — premium, scoped to that turn |

Design constraints:

- **Local only.** The classifier sees request content, including L4-sensitive material; it must run on our hardware (the sidecar pattern already fits: small model, ~35 ms budget, fail-safe to the rules layer).
- **Cheap enough to be free.** A routing decision that costs real money per turn defeats the purpose; embeddings or a small local LLM keep the decision at ~$0.
- **Evaluable offline before it touches live traffic.** Held-out labeled examples exist for sensitivity; difficulty labels can be mined from `routes.jsonl` outcomes (turns whose model failed vs. succeeded). The Nov 8 calls are labeled seed examples: same opening, one success, one collapse.
- **Rules stay as the floor.** The model advises; hard policy (sensitivity walls, client lanes, emergency switch) still governs.

## 8. Direction D — Premium as a scalpel (failure-triggered escalation)

Complement to Direction C (prediction) with reaction:

- The voice controller and router already detect failure signatures: sanitizer/echo-guard hits, tool errors, `tool_error_next_turn`.
- On a detected failure, **the next turn escalates one tier** — cheapest capable model first — and steps back down after a clean turn.
- Premium spend then concentrates on turns that *demonstrably* need it, which the Nov 8 data suggests is a small minority of turns.

## 9. How these fit together

- **A** (scorecard) is the evidence base — it feeds B's pruning and C's training/eval data.
- **B** is policy tuning inside mechanisms that already exist — lowest risk, immediate.
- **C** is the structural upgrade Jordan has pointed at before: routing decided by a small intelligent model instead of static rules alone.
- **D** catches what prediction misses.

All four preserve the standing principle. None requires a pin, a new paid commitment, or weakening a guardrail.

## 10. Decisions for Jordan

1. **Sequence:** recommendation is A first (it makes every later decision cheaper and more accurate), then C, with B applied as the scorecard justifies it and D last. Or does C jump the queue?
2. **Difficulty-score scope:** voice turns only at first (worst variance pain, cleanest failure labels), or all Hermes traffic from the start?
3. **Eval bar:** what accuracy must the difficulty classifier hit on the offline set before it earns a live trial — and should its first live mode be shadow (log what it *would* route, change nothing) for a week?

---

*Prepared by Muse, Nov 8, 2026, from the evening's verified router/profile logs and the Nov 1–2 classifier build records. No routing changes are made by this document.*
