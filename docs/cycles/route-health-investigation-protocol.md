# Route Health Investigation Protocol — "loop back engineering" for disabled and anomalous routes

- **Established:** 2026-11-12, on Jordan Mason's directive (~22:20 MST). Jordan's
  premise: when a route misbehaves, the fault may be **ours** — request
  construction, shaping, or configuration — not the provider's. A disable note
  that says "provider broken" without a differential test is an unproven claim,
  and unproven claims do not get to keep routes off the bench. This protocol is
  the standing procedure that proves or refutes them.
- **Relationship to the Improvement Loop:** this protocol is the loop's
  stage-7 → stage-1 feedback path made concrete for route health. Every
  investigation run under it produces a failure-log entry with evidence; the
  findings feed the next cycle's research. It authorizes **investigation
  only** — probes and documents. Registry changes, re-enables, and gateway
  code changes still pass the normal gates (plan, tests, Jordan's deploy go).
- **First application:** the two worked examples in §5–§6 were run the same
  evening the protocol was ordered (2026-11-12 ~22:22–22:45 MST), probes
  direct to the providers from VM103 with the production keys read in place.
  Both disables turned out to be partly our fault. That is the point of the
  protocol.

## 1. Trigger

An investigation opens when any of these fires:

1. **A route is disabled** — for any reason, by anyone. A disable without a
   completed differential investigation is provisional by definition.
2. **An anomalous probe pattern** — e.g. empty content with `finish=length`,
   thought/reasoning text in the user-visible content, latency an order of
   magnitude off the entry's seed, intermittent 5xx, or a usage block that
   doesn't add up (thought tokens invisible to the counters).
3. **A route fails its scheduled re-probe** (§4).
4. **A client-visible incident** is traced to a route whose registry note
   blames the provider.

## 2. Owner

Whichever agent is working the router thread owns the investigation end to
end. Hermes (or another fleet agent) may be tasked with the probe runs via
the shared board (`ai_shared_notes` / `ai_commons`), but the owning agent
verifies the evidence and writes the verdict. An investigation is not closed
by "the other agent is looking at it."

## 3. Steps

1. **Capture the exact failing pair.** The literal request body the gateway
   (or probe harness) sent and the literal response — status, headers of
   interest, finish reason, full usage block, field inventory of the message
   object. No paraphrase; if the pair wasn't captured, re-run the failing
   shape once to capture it.
2. **Differential test — one variable at a time.** Three arms minimum:
   (a) **canonical direct**: the provider's own documented minimal request,
   sent direct to the provider, bypassing the gateway entirely;
   (b) **gateway-shaped direct**: byte-for-byte what our gateway would send
   (read `upstream_body` in `gateway.py` — allowlisted client fields, budget
   clamp, entry `extra_body` merged), also sent direct;
   (c) **control**: a sibling route known to work through the same key,
   endpoint family, and shaping (e.g. muse-spark-1.1 for 1.3; a Gemini entry
   for a Gemma entry). If the sibling works and the subject doesn't under
   identical shaping, shaping is exonerated for the shared variables.
   Then bisect: change exactly one variable per call (budget, one parameter,
   endpoint variant — native vs compatibility surface) until the failing
   variable is isolated or the failure proves shape-independent.
3. **Primary sources.** The provider's own docs for the exact behavior in
   question (parameter semantics, defaults, documented controls), status
   page, changelog, and credible community reports. Record what the docs
   promise. **The absence of wider reports is itself evidence**: if a model
   "only fails for us," weight our-shaping hypotheses higher — that was
   Jordan's original observation, and both founding cases bore it out.
4. **Classify the root cause**, exactly one primary class:
   - `ours-shaping` — the request we construct is wrong or incomplete
     (a needed control field never sent, a poison field forwarded, the
     wrong endpoint surface for the behavior we need);
   - `ours-config` — the registry entry or gateway configuration around the
     route is wrong (budget defaults, missing `extra_body`, a field the
     gateway silently drops so clients can't control it);
   - `provider` — a canonical direct request reproduces the failure with
     the provider's own documented shape;
   - `terms-limits` — the route behaves as its terms/quota say it should
     and the mismatch is in our expectations or accounting.
   Mixed verdicts are allowed and expected; name each component and its
   class separately (see §5).
5. **Fix proposal or provider-fault note.** For ours-* classes: the concrete
   change (file, field, value) that the differential proved out — stated as
   a proposal for the next gated change, never applied inside the
   investigation. For `provider`: the evidence pack (canonical request +
   response) that any future re-probe must beat.
6. **Re-probe to confirm.** Re-run the failing shape plus the proposed-fix
   shape. A verdict isn't final until the fix shape (or the provider's
   recovery) is observed working, or the log says exactly why it couldn't
   be.
7. **Log everything.** A dated entry in the Improvement Loop failure log
   (charter cycle log in `docs/IMPROVEMENT-LOOP.md`, or the active cycle
   file) with: the trigger, the captured pair summary, the differential
   matrix results, the classification, the fix proposal, and the re-probe
   outcome. Registry notes for the entry are updated to cite the
   investigation instead of the bare symptom — as part of the next gated
   registry change, not silently.

## 4. Cadence — disables expire

- Every disabled entry is re-probed **weekly** (matching the per-entry
  `probe.cadence` already carried in the registry), using the entry's
  recorded failing shape plus its canonical shape.
- A re-probe that reproduces the failure **renews** the disable with fresh
  dated evidence appended to the entry's investigation record.
- A re-probe that passes clean opens a re-enable proposal (with any
  ours-* fix the investigation identified) for the normal gate.
- **A disable that goes 30 days without a reconfirming re-probe is stale**:
  it is flagged to Jordan in the next cycle report as an unproven claim,
  and the route returns to the bench or gets a real investigation — it does
  not sit out on an old note.

## 5. Worked example 1 — `google/gemma-4-31b-it` (and sibling `gemma-4-26b-it`)

**Disable rationale on record (2026-11-12):** Google's OpenAI-compatible
endpoint returned HTTP 500 INTERNAL on deploy probes; re-probes gave two slow
200s (114s, 64s) with visible `<thought>` leakage and `finish=length`, then
two more 500s. Registry note concluded "Google-side instability, not a
registry defect." Jordan challenged exactly that conclusion.

**Differential matrix (2026-11-12, direct from VM103, production Google key;
prompt in every arm: "What is the capital of France? Answer in one short
sentence."):**

| Arm | Shape | Result |
|---|---|---|
| Control | compat, `gemini-3.1-flash-lite`, mt 512 | 200, 0.6s, `finish=stop`, clean content — key/endpoint healthy |
| G0 | `GET /v1beta/models` | `gemma-4-31b-it` and `gemma-4-26b-a4b-it` both listed for this key (50 models) — model id correct, servable on this account |
| G1 | compat minimal, mt 512 | 200, 24.7s, `finish=stop` — but `content` **begins with literal `<thought>…`**; usage `completion_tokens: 7` against `total_tokens: 76` (thoughts invisible to the completion counter) |
| G2/G2r | compat gateway shape, mt 2048 | **500 INTERNAL twice** (45.0s, 26.9s) |
| G3 | compat + `reasoning_effort: "none"` | **400** — "Thinking budget is not supported for this model." The Gemini control is a poison field for Gemma on compat |
| G4 | native `generateContent`, default | 200, 37.5s, `finish=STOP`; parts cleanly separated: `thought:true` part (251 chars) + answer part "The capital of France is Paris."; `thoughtsTokenCount: 62` |
| G5/G5r | native + `thinkingConfig.thinkingLevel: "minimal"` | 200 twice (33.7s, 24.4s); thought part **empty**, answer clean, `totalTokenCount: 20` — no thought tokens spent |
| G6 | native + `thinkingLevel: "high"` | 500 INTERNAL (instant, 0.3s) — provider flakiness is not shape-specific |
| G7 | compat, `gemma-4-26b-a4b-it`, mt 512 | 200, 3.3s — same `<thought>` inlining into `content` |
| G8 | native, 26b, default | 200, 4.0s — parts separated, `thoughtsTokenCount: 157` |
| **G9** | compat + `extra_body.google.thinking_config.thinking_level: "minimal"`, mt 512 | 200, 96.1s — **`content` is exactly "The capital of France is Paris." (31 chars, zero thought text)**, `total_tokens: 21` |

**Classification (mixed):**

- *Thought leakage* — **`ours-shaping`**. The gateway routes Gemma through
  the OpenAI-compat surface with no thinking control. On that surface,
  Google's translation inlines Gemma's thought text into `content` (G1/G7);
  on the native surface the same output arrives as a separate
  `thought:true` part (G4/G8). Google's own Gemma docs name the control —
  `thinkingConfig.thinkingLevel` `"minimal"` disables thinking — and G9
  proves the compat surface honors it via `extra_body`, producing a clean
  answer. The leakage was never a model malfunction; it was the predictable
  result of our endpoint choice plus a control we never sent.
- *HTTP 500s and latency* — **`provider`**. Canonical direct requests to
  Google's own endpoints reproduce 500 INTERNAL with zero gateway
  involvement (G2/G2r/G6), while the same key serves Gemini models in
  0.6s. The 31B's success latency (24–96s vs the 26B sibling's 3–4s) is a
  free-tier serving property. Public reports corroborate a Gemma-on-Gemini-API
  serving stack with recent regressions (the Nov 1 image-400 incident).
  Model-id/tier suspicion cleared: the id matches Google's docs exactly
  and is listed for this key; Gemma on the Gemini API is free-tier-only
  (no paid tier exists).

**Fix proposal (not applied; for the next gated registry change):** set the
Gemma entries' `extra_body` to
`{"extra_body": {"google": {"thinking_config": {"thinking_level": "minimal"}}}}`
— the exact shape G9 proved. Do **not** use `reasoning_effort` (G3: 400).
**Disposition:** the disable stands on the provider component alone (500
rate + latency), reclassified from "Google-side instability, not a registry
defect" to *provider reliability + our-shaping leakage, fix proven*. Both
Gemma entries enter the §4 weekly re-probe; the 26B (fast, 0/2 failures in
this run) is the nearer-term re-enable candidate once its re-probes confirm.

## 6. Worked example 2 — `meta/muse-spark-1.3` (control: `muse-spark-1.1`)

**Disable rationale on record (2026-11-12):** probes returned 200s but no
content at max_tokens 512 (510 reasoning tokens) and none at 2048 (2,046
reasoning tokens, `finish=length`, 32.3s). "Do not enable until a
budget/latency profile is chosen."

**What the primary sources say (dev.meta.ai, Reasoning — Meta Model API):**
Muse Spark is a reasoning model; reasoning tokens count toward `max_tokens`
and are billed as output; the control is a top-level `reasoning_effort`;
**when the parameter is omitted, every Muse Spark version reasons at its
default level `"high"`**; usage reports the split as
`completion_tokens_details.reasoning_tokens`. Meta's own cookbook adds that
the default effort "is still being finalized."

**What our gateway does with that control: nothing.** `reasoning_effort` is
not in the gateway's `ALLOWED_FIELDS` (`gateway.py`), so a client that sends
it has it silently dropped; and no Meta registry entry carries an
`extra_body`, so the gateway never sets it either. Through the gateway,
every Muse Spark call runs at Meta's default `"high"` effort, unchangeably.

**Differential matrix (2026-11-12, direct from VM103, production Meta key;
same trivial prompt in every arm):**

| Arm | Result |
|---|---|
| 1.1 control @ 512 / 2048 / 8192 | 200, content, `finish=stop`, reasoning 273 / 272 / 355, 1.9–4.1s |
| 1.2 @ 512 / 2048 | 200, content, reasoning 400 / 187 |
| 1.3 @ 512 | 200, **content present**, `finish=stop`, reasoning 348, 13.2s |
| 1.3 @ 2048 (×4 total) | 200, content every time, reasoning 270–398, 7.0–10.4s |
| 1.3 @ 8192 / 16384 | 200, content, reasoning 251 / 397 |
| 1.3 agent shape (system msg + temperature) @ 2048 | 200, content, reasoning 247, 10.0s |
| 1.3 @ 2048 + `reasoning_effort: "minimal"` | 200, content, **reasoning 37**, 5.1s |
| 1.3 @ 2048 + `reasoning_effort: "low"` | 200, content, reasoning 283, 11.2s |
| 1.1 @ 2048 + `reasoning_effort: "minimal"` | 200, content, reasoning 154 (vs 272 default), 1.2s |
| 1.3 @ 512 + `reasoning_effort: "minimal"` | one 503 `service_overloaded` (transient provider overload; the same control at 2048 succeeded minutes later) |

The phase-2 observation (>2,046 reasoning tokens, `finish=length`, no
content at 2048) did **not** reproduce in ten attempts today: at default
effort, 1.3's reasoning burn on a trivial prompt is 247–398 tokens —
the same order as the working 1.1/1.2 controls. (The sibling 1.2 shows the
same day-to-day spread: 1,032 reasoning tokens recorded on 2026-11-12's
earlier probes, 187 in this run.) Default-effort depth on this API is
variable — Meta says the default is still being finalized — so the
phase-2 probes caught a genuine bad draw, not a stable property of the
model. What *is* stable: 1.3's latency at default effort (7–13s on a
one-sentence answer vs 1.1's ~2s), and the fact that the documented
control collapses its reasoning burn to 37 tokens when actually sent.

**Classification: `ours-config`** (primary). The disable treated a
*controllable, default-driven* behavior as a model defect, and the gateway
is configured so the control cannot be exercised: `reasoning_effort` is
dropped from client requests by the allowlist and set by no registry
entry. Secondary: `provider` variance in default-effort depth (documented
by Meta as not yet final).

**Fix proposal (not applied; for the next gated change):**
1. Add `reasoning_effort` to the gateway `ALLOWED_FIELDS` so clients can
   control reasoning depth on the providers that document it.
2. Set `extra_body: {"reasoning_effort": "minimal"}` on the muse-spark
   entries (per-entry effort profiles can be revisited per model; the
   gateway already has this pattern — OpenRouter entries carry
   `extra_body.reasoning` controls). Alternatively or additionally,
   note that the gateway's default budget (2048) plus its
   length/no-content escalation retry partially mask this failure mode
   for through-gateway calls — direct probes have no such net, which is
   part of why the probe battery saw the worst case.
3. Re-probe 1.3 under §4 with the effort profile applied; on a clean
   battery, propose re-enable. Its premium-lane cost profile at
   `minimal` effort (53 completion tokens for the test prompt) is a
   different economic object than the one that was disabled.

**Probe cost for this investigation:** Meta ≈ $0.02 total (≈5,100
completion tokens across 18 calls at the $4.25/M rate, plus negligible
input). Google: $0 (free tier).

## 7. Worked example 3 — `openrouter/dots-3-note-preview-free` stream stalls (2026-11-13)

**Symptom on record:** the Hermes Smoke Signal session stalled repeatedly
on `write_file` — seven stalls 2026-11-12 21:55–22:01 MST (session
`20261112_191400_8b28ce`, context 93k→116k tokens), three more 2026-11-13
~00:35–00:47 MST. Hermes reports "Stream stalled mid tool-call
(write_file); the action was not executed." The route records show every
stalled turn served by `openrouter/dots-3-note-preview-free` with
`output_tokens` at exactly 2048 and `finish=length`; the v0.8.0
`attempt_events` ledger records them as clean serves — no transport
failure: the stream completes, and the tool call inside it is truncated.
The owner's question: is it the model, or the way we interact with it?

**What the primary source says (OpenRouter models API):**
`dots-studio/dots-3-note-preview:free` — `context_length` 512,000,
`top_provider.max_completion_tokens` **460,800**. No 2,048 output cap
exists on the provider side. (Probe B below confirms it behaviorally:
direct with `max_tokens` unset, the model generated 6,080 tokens and
stopped naturally — OpenRouter imposes no 2,048 default either.)

**What our side does (the audit, before any probe):**

- **Registry entry:** `extra_body` = `{provider.data_collection: deny,
  reasoning.enabled: false}` (reasoning disabled 2026-11-11 for a
  different incident — empty responses, not stalls). Nothing in the entry
  touches the output budget. Lane `private-free`; it is the free lane's
  only 512k-context entry.
- **Gateway (v0.8.0):** `DEFAULT_MAX_TOKENS = 2048` (`gateway.py:107`).
  `upstream_body()` computes `requested` = the client's `max_tokens` /
  `max_completion_tokens` when present, else the default, clamps only to
  the model's remaining context room, and **always writes the budget
  explicitly into the upstream body**. A client that sends `max_tokens`
  gets it passed through; a client that sends none gets exactly 2,048.
- **Hermes:** sends no `max_tokens`. Its config (VM112,
  `/opt/data/config.yaml`) sets no cap — the only match is a commented-out
  example (`# max_tokens_cap: 4096`). Every Hermes turn therefore
  inherits the gateway's 2,048 default.
- **Why dots-3:** sticky sessions bind Hermes' longest sessions to the
  only free entry whose context fits their 93k–173k-token inputs, and
  `write_file` payloads are the longest outputs Hermes produces — so the
  default binds exactly where outputs are longest. Route-log census for
  dots-3: 3,308 serves < 2,048 output tokens, 141 at exactly 2,048, and
  **38 above 2,048** — the route exceeds the cap whenever the budget on
  the request allows it.

**Differential matrix (2026-11-13, from VM103; copy task — reproduce 320
provided lines ≈ 6,080 tokens; production `extra_body` on every dots-3
arm; $0, free tier):**

| Arm | Result |
|---|---|
| Direct, plain, `max_tokens=8192` (A) | 200, `finish=stop`, 6,080 completion tokens, 320/320 lines |
| Direct, plain, `max_tokens` UNSET (B) | 200, `finish=stop`, 6,080 tokens, 320/320 — provider default is not 2,048 |
| Direct, plain, 8192, ~90k-token context (D) | 200, `finish=stop`, 6,080 tokens, 320/320 — **no context squeeze**: output identical at 6k and 90k context |
| Gateway (hermes client, pinned), plain, `max_tokens=8192` (C) | 200, `finish=stop`, 6,080 tokens, 320/320 |
| Gateway (hermes client, pinned), plain, `max_tokens` UNSET (C2 — the Hermes replica) | 200, **`finish=length`, exactly 2,048 tokens, 108/320 lines** — the production stall reproduced with one variable changed |
| Tool-shaped arms (direct + gateway) | dots-3's tool arguments on fresh single-turn calls are degenerate (`{"content": ": ", "path": ": "}`); through the gateway that shape is classified `empty_response` and escalates to nemotron (attempt-ledger rows 213–215). A tooling-quality defect — **not** the stall mechanism: production payloads truncate on length, not degeneracy. |

**Classification: `ours-config`** (primary) **× interaction.** The binding
ceiling is the gateway's own `DEFAULT_MAX_TOKENS = 2048`, applied because
the client omits `max_tokens`. The model is exonerated: whenever a budget
above 2,048 reaches it, by any path, it completes at 6,080 tokens. The
provider is exonerated: published max completion is 460,800 and its unset
default does not bind at 2,048. The squeeze hypothesis is refuted by arm
D. **The single discriminating variable is the `max_tokens` on the
request as it leaves the gateway.**

**Secondary finding (feeds R4, not this verdict):** dots-3's
tool-argument fidelity on fresh single-turn calls is poor — degenerate
arguments, gateway-classified `empty_response` with failover; the entry's
own 2026-11-11 note records "malformed-tool-call loops" in production.
Measured evidence against dots-3 for tool-class work in the R4 tooling
dimension; its long-context chat standing is unchanged.

**Fix proposal (not applied; gated fix package):**

1. **Gateway (primary):** give tool-bearing requests a raised default
   budget (proposed 8,192) while plain chat turns keep the 2,048 default
   — the truncation victims are tool payloads, and free-lane cost
   discipline is preserved where outputs are naturally short. (The
   one-line alternative — raising `DEFAULT_MAX_TOKENS` globally — is
   rejected in this proposal: it lengthens every free-lane generation,
   including runaway ones, against shared pool RPM.)
2. **Client (complementary):** set Hermes' `max_tokens_cap` — its config
   already documents the knob, commented at 4096 — so Hermes states its
   own budget explicitly. This protects Hermes even if gateway defaults
   change again, but not other omitting clients, which is why the gateway
   fix is primary.
3. **Ledger follow-through (R3 taxonomy):** record `finish_reason` on
   route records / attempt rows so a length-truncated tool call is
   visible as its own class (`truncated_tool_call`) instead of a clean
   serve; v0.8.0's ledger cannot currently see this failure shape.

**Standing outputs:** failure log **F-09**; registry-note correction
queued for the next gated registry change (append this verdict and the
tooling evidence to the dots-3 entry's notes); the entry remains
**enabled** — no disable: the route is sound, the budget was ours;
weekly re-probe cadence unchanged, with the next re-probe to include one
tool-shaped arm scored on argument completeness.

**Probe cost for this investigation:** $0 (OpenRouter free tier
throughout; ~50k completion tokens across all arms).

## 8. Standing outputs

Every investigation closes with all four: (1) the failure-log entry, (2) the
registry-note correction queued for the next gated registry change, (3) the
fix proposal or provider evidence pack, (4) the entry's next re-probe date
under §4. If any of the four is missing, the investigation is still open.
