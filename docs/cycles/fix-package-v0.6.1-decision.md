# Fix Package v0.6.1 — Decision Package: reasoning_effort control + Gemma shaping

- **Date:** 2026-11-12 (build run ~22:40–23:00 MST)
- **Status:** BUILT AND TESTED on branch `fix/v0.6.1-effort-and-gemma-shaping`
  (commit `fa36f523`), held as draft PR #2. **NOT deployed. Production is
  untouched** — VM103 still runs v0.6.0 with registry md5
  `d859879dd9ce177a55818fc170629d0b`. The owner approved the BUILD
  (2026-11-12 ~22:35 MST); the deploy decision is what this package asks for.
- **Origin:** the route-health differential diagnosis
  (`docs/cycles/route-health-investigation-protocol.md` §5–§6), which proved
  two of our disabled routes were partly our own fault.

---

## 1. What changed — exact diff vs GitHub main

### `src/gateway.py` (VERSION 0.6.0 → 0.6.1; +46/−2 lines)

1. **`reasoning_effort` added to `ALLOWED_FIELDS`.** Before this, a
   client-sent effort was silently dropped at the allowlist, and no Meta
   entry set one either — Meta's documented control was unexercisable
   through the gateway while Meta defaulted every omitted effort to
   "high".
2. **Poison-field guard** — new `_reasoning_effort_stripped(entry)`,
   applied in `upstream_body` AFTER the extra_body merge (so neither a
   client nor a registry entry can leak the field upstream):
   - provider `google` → always strip (proven HTTP 400, probe G3);
   - provider `mistral` and entry not reasoning-flagged → strip
     (per-model validation, probed — see §3).
   `upstream_body` is the single body-building path for the JSON serve,
   the streaming serve, and shadow, so one guard covers all three.

### `src/registry.json` (semantic diff vs main = exactly 5 entries, `notes` + `extra_body` only)

| Entry | Change | Enabled |
|---|---|---|
| `meta/muse-spark-1.1` | `extra_body: {"reasoning_effort": "minimal"}` + dated note | stays **enabled** |
| `meta/muse-spark-1.2` | same | stays **enabled** |
| `meta/muse-spark-1.3` | same | stays **DISABLED** (decision 2 below) |
| `google/gemma-4-31b-it` | `extra_body: {"google": {"thinking_config": {"thinking_level": "minimal"}}}` + dated note | stays **DISABLED** |
| `google/gemma-4-26b-it` | same | stays **DISABLED** |

No other entry changed in any field (verified by deep-diff against main's
registry, blob md5 `d859879d…`).

### Tests

- New `tests/test_effort_and_gemma_shaping.py` — 12 tests (recording
  MockTransport asserts on the exact upstream bodies).
- `tests/test_paid_spillway.py` — the version-shape assertion updated
  0.6.0 → 0.6.1 with the narrated comment, per that test's convention.
- **Full suite: 566 passed** (554 baseline + 12 new), locally and in CI:
  PR #2 check run "pytest (Python 3.12)" = **success**
  (Actions run 43374105152, head `fa36f523`).

---

## 2. Probe evidence — the effort value: **minimal**

Direct calls from VM103 to `api.meta.ai` (production key, gateway
bypassed), max_tokens 2048. Trivial prompt = "capital of France";
reasoning-shaped prompt = two-step discount/change problem (answer $42).

| Model | Prompt | Effort | Reasoning tokens | Latency | Correct |
|---|---|---|---|---|---|
| 1.3 | trivial | default (high) | 256 | 4.6s | — |
| 1.3 | trivial | **minimal** | **28** | 1.6s | — |
| 1.3 | trivial | low | 274 | 3.9s | — |
| 1.3 | trivial | medium | 370 | 16.7s | — |
| 1.3 | reasoning | default (high) | 546 | 6.8s | ✓ |
| 1.3 | reasoning | **minimal** | **85** | 2.9s | ✓ |
| 1.3 | reasoning | low | 295 | 10.8s | ✓ |
| 1.3 | reasoning | medium | 514 | 7.4s | ✓ |
| 1.1 | trivial | default (high) | 346 | 2.3s | — |
| 1.1 | trivial | **minimal** | **51** | 0.6s | — |
| 1.1 | trivial | low | 83 | 0.8s | — |
| 1.1 | reasoning | default (high) | 1,027 | 5.0s | ✓ |
| 1.1 | reasoning | **minimal** | **408** | 2.4s | ✓ |
| 1.1 | reasoning | low | 373 | 2.3s | ✓ |
| 1.1 | reasoning | medium | 478 | 2.7s | ✗ (no $42 in answer) |

**Why minimal, not low:** "low" does not restrain the model that
motivated the fix — on 1.3's trivial prompt it burned 274 reasoning
tokens, statistically the default's 256. Minimal cut burn 6.4–9× vs
default on three of four arms (2.5× on the fourth) and **kept the
correct answer on both reasoning-shaped arms**; medium was worse than
default on burn and missed the answer once. The diagnosis-day probes
agree (1.3 @2048 + minimal → 37 reasoning tokens). Meta's scale, as
probed: minimal < low < medium < (default) high — all four values are
accepted by the API.

**Precedence, documented and tested:** when an entry pins effort via
extra_body, the entry wins over a client-sent value — that is the
gateway's existing merge semantics, and it is load-bearing elsewhere
(it is what makes OpenRouter's `data_collection=deny` unoverridable).
So on the muse-spark entries the profile is an operator guardrail:
clients cannot raise effort above it there. On entries without a
profile, a client-sent effort now passes through (subject to the
poison guard). If per-request client override on Meta entries is ever
wanted, that is a deliberate follow-up semantic change, not a bug.

---

## 3. Probe evidence — the poison-field audit (all 17 providers)

One live call per provider from VM103 with `reasoning_effort: "low"`
set (trivial prompt), plus retries noted. Only an HTTP 400 whose error
names the field counts as poison.

| Provider (probe model) | Result |
|---|---|
| google (gemma-4-31b-it, diagnosis G3) | **400 "Thinking budget is not supported for this model" → STRIP, all entries** |
| mistral ministral-8b | **400 "reasoning_effort is not enabled for this model" → strip** |
| mistral-small / mistral-medium | **400 — graded values rejected; only `none`/`high` accepted → strip** |
| mistral-large (reasoning-flagged) | 200, accepted (returned structured thinking blocks) → pass |
| meta | native field — the point of the fix |
| cerebras, deepinfra, deepseek, huggingface, novita, nvidia, openai, openrouter, siliconflow | 200 — accepted or ignored |
| groq (gpt-oss-20b), aion (aion-3.5-mini) | 200 on retry — first pass drew Cloudflare error-1010 WAF blocks on the Python client's default user-agent; with a curl user-agent both answered normally. Not field rejections. |
| cloudflare | 429 — the known exhausted daily neuron pool; error does not implicate the field (inconclusive, low risk) |
| ollama | 402 — the known account-credit state; field not implicated |
| zai | 429 — overload (code 1305); field not implicated |

Residual risk, stated plainly: for cloudflare/ollama/zai the account
errors fired before field validation could be observed, and per-model
validation like Mistral's could exist elsewhere unprobed (e.g. Groq's
non-reasoning models). The exposure is narrow — the field only flows
when a client explicitly sends it (none of our clients do today) or an
entry pins it (cerebras/aion pins pre-date this package and probed
clean) — and a rejection surfaces as a visible 400 in the route log,
which the investigation protocol now treats as a trigger.

---

## 4. Production behavior change, in plain terms

If deployed, three things change and nothing else:

1. **Muse Spark stops over-thinking by default.** Every muse-spark turn
   (1.1/1.2 live today; 1.3 if re-enabled) carries effort "minimal":
   on the probe evidence, expect hidden reasoning burn per turn to drop
   from hundreds–thousands of tokens to tens, with answers on
   reasoning-shaped prompts unchanged. Premium-lane cost per completed
   answer drops accordingly.
2. **Clients gain a control they never actually had.** A client that
   sends `reasoning_effort` (e.g. a future Hermes/OpenClaw setting) now
   has it honored on unpinned entries instead of silently dropped.
3. **Two landmines are defused.** A client-sent effort can no longer
   400 an entire turn by landing on a Google entry (this mattered the
   moment the field was allowlisted — failover makes the serving entry
   unpredictable), nor on Mistral's non-reasoning models.

The Gemma entries change nothing in production behavior: they stay
disabled; the thinking shape simply sits in the registry, proven and
ready, so a future re-enable (if Google's 500s clear on the weekly
re-probes) doesn't re-ship the thought leakage.

Not in this package, deliberately: the J-1 pin-binding fix (Cycle 1
aspect G) — pins still force-insert disabled entries until that build;
the Gemma test in this package relies on that current behavior to
prove the guard on the exact latent path.

---

## 5. The two decisions

### Decision 1 — Deploy v0.6.1?

Merge PR #2 to main, then the standing deploy gates, unchanged:
Proxmox snapshot `pre-v061-<date>` on VM103 → sync the three registry
copies byte-identical (repo / VM103 build tree / VM103 state) with
gateway-readable ownership (10001:10001, the Nov 9/Nov 12 lesson) →
build the image from the merged tree on VM103 → restart the gateway
container only → verify `/health` (version 0.6.1, 81 models) → pinned
smoke serves (muse-spark-1.1 shows the minimal profile in effect;
an ordinary hermes turn end-to-end) → watch the alert chain for a day.

### Decision 2 — If deployed, re-enable `meta/muse-spark-1.3` at the minimal profile?

**Recommendation: yes.** Reasoning:

- The disable was earned by one specific behavior — >2,046 hidden
  reasoning tokens and no content at a 2,048 budget on a trivial
  prompt, at Meta's *default* (high) effort. At the minimal profile
  the same class of prompt costs 44–162 completion tokens total and
  answers correctly; the object being re-enabled is economically a
  different model from the one disabled.
- It is a premium-lane entry: it only serves level-5/explicit-premium
  turns and the paid spillway in cost order, so its blast radius is
  bounded by design.
- The protocol's weekly re-probe cadence applies from day one; if the
  burn regresses, the failure log gets the evidence and the entry can
  be re-disabled with a dated note — the loop working as intended.
- 1.2's registry note shows the same appetite (1,032 reasoning tokens
  on a one-sentence answer), so 1.3-at-minimal also de-risks the
  version line we already run.

**Gemma entries: no re-enable proposed.** The 500 INTERNAL instability
is provider-side (reproduced on canonical direct requests); the
disable stands on reliability, with weekly re-probes under the
protocol. The shaping fix waits in the registry for the day Google's
side clears.

### Rollback (either decision)

No schema or state changes — rollback is a pure code/registry revert:
close/revert PR #2 (or `git revert` the merge), restore the registry
to md5 `d859879dd9ce177a55818fc170629d0b` in all three copies, rebuild
from the v0.6.0 tree, restart the gateway. The v0.2.1 stack on :6070
remains untouched throughout, as always.

---

## 6. Surprises from the build

1. **The extra_body effort pattern already existed.** Cerebras
   (gpt-oss-120b, qwen-3.8-27b) and Aion (3.5-mini, 2.0) entries have
   pinned `reasoning_effort` via extra_body since the v0.5.8 staging —
   extra_body was always merged; what was missing was (a) any Meta
   entry using it and (b) clients' ability to send the field at all.
   The diagnosis's framing holds for Meta specifically.
2. **Mistral validates the field per model AND per value** —
   small/medium accept only `none`/`high`. A provider-level assumption
   in either direction would have been wrong; the guard keys on the
   registry's existing `reasoning` flag, which matched all five probed
   Mistral models.
3. **Probe methodology landmine:** aion and groq 403'd the first audit
   pass with Cloudflare error 1010 — their edges WAF-block the Python
   urllib user-agent from VM103's IP. A naive read would have recorded
   two false "rejections". (The gateway's own HTTP stack is unaffected;
   this bites hand-rolled probe scripts.)
4. The local repo's git HEAD is stale relative to GitHub main (the
   tree is the source of truth; pushes go through `push-files`). All
   diffs in this package were therefore verified semantically against
   GitHub main's blobs, not the local HEAD.
