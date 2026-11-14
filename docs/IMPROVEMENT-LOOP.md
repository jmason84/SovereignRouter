# Sovereign Router — Improvement Loop

Established 2026-11-12 on Jordan Mason's order, as the standing operating mode for
this project (the same discipline he set for Hermes Direct Voice earlier the same
day). This document is the charter and the cycle log. Per-cycle detail lives in
`docs/cycles/`.

## The cycle

Every cycle runs the same seven stages, in order. No stage is skipped; a stage
that produces nothing produces a dated note saying so.

1. **Research each aspect.** Primary sources only: provider docs, terms, pricing
   pages and live catalogs; the gateway's own logs and route records; published
   benchmarks treated as claims, not facts. Every finding cites its source.
2. **Identify the best tools to implement.** For each aspect: adopt an existing
   tool/pattern, build, or skip — with the reasoning recorded. "Best" is judged
   against this router's actual constraints (single VM, OpenAI-compatible
   surface, four clients, free-first cost-ordered lanes, privacy lanes).
3. **Document the planned update before building it.** Tools, code changes, and
   every other inclusion (registry fields, tests, docs) written down in the
   cycle file. If it isn't in the plan, it isn't in the build.
4. **Develop the development tests.** Tests encode the intended behavior before
   (or alongside) the change — including a regression test for every failure
   being fixed.
5. **Run tests, clear tests.** The full suite must be green locally, then green
   in CI. Failures are diagnosed, not bypassed; a re-based test carries a dated
   comment saying what changed and why.
6. **Live tests.** Probes, pinned serves, shadow selections, production-shaped
   traffic. A change is not "done" at green tests — it is done when it has been
   observed working live, or the cycle file says exactly why not.
7. **Document everything that went wrong.** Every failure — research dead-ends,
   test failures, live incidents, wrong assumptions — gets a dated failure-log
   entry with evidence. Stage 7 of one cycle is the research input to stage 1
   of the next. Then the loop starts over.

## Standing rules inside the loop

- **Production gates still hold.** Snapshot + verified backups before deploys;
  consequential changes stop at the deploy gate for Jordan's explicit go. The
  loop authorizes research, design, tests, and local builds — not silent
  production changes.
- **No silent fixes.** If it broke, it is in the failure log, even if it was
  fixed in the same hour.
- **Prices and claims come from the provider's own source**, or the entry says
  they didn't. (Origin: Cycle 0, F-04.)
- **Pinned verification uses a unique conversation per pin.** Sticky-session
  reuse outranks pins by design. (Origin: Cycle 0, F-03.)
- **Registry files on VM103 must land owned so the gateway (uid 10001) can
  read them** — verify ownership/permissions before any restart. (Origin:
  Cycle 0, F-01; same family as the 2026-11-09 provider-key incident.)

## Cycle 0 — 2026-11-12 (retroactive capture)

The two registry expansions of 2026-11-12 (22 → 42 → 81 models, 72 enabled)
were run under this discipline before the charter existed. Their failures are
the loop's founding input:

| # | Failure | Evidence / disposition |
|---|---|---|
| F-01 | First phase-2 restart crash-looped the gateway ~2 min: the copied `state/registry.json` landed 640/root, gateway (uid 10001) PermissionError. | Fixed with chown 10001:10001 + 644; healthy on next restart. Standing rule above. |
| F-02 | `Registry.get()` matched id *or* upstream model string, first hit wins — a new OpenRouter entry's model string equaled a Google entry's id and would have hijacked its pins in production. | Caught by tests pre-deploy; file order fixed + guard test asserts `get(id)` exactness for all 81 entries. Generalization queued as Cycle 1 aspect B. |
| F-03 | First pinned-serve verification pass was invalid: identical prompts let sticky-session reuse hijack 13 serves onto one model. | Methodology fixed (unique conversation per pin); re-run clean. Standing rule above. |
| F-04 | SiliconFlow price seeds were wrong: seeded from an aggregator's endpoint listing at exactly half of SiliconFlow's own published prices ($0.70/$2.20 vs real $1.40/$4.40 for GLM-5.3). The "half of Z.ai direct" advantage never existed. | Corrected from the provider's own list; provenance conflicts now recorded in entry notes. Standing rule above; maintenance queued as Cycle 1 aspect C. |
| F-05 | `openrouter/north-mini-code-free` produced every sampled malformed-JSON article draft in the Launch AI Works Article Engine (16 reconciled failures; nemotron control 11/11 clean). Its registry quality scores let it win drafting-class work. | Quality corrected (chat 3→2, long-context 3→2; drafting classifies as chat at L2+, floor 3). Shadow tests prove exclusion from drafting, retention for simple chat/code. |
| F-06 | HF `qwen3.8-27b` (`:deepinfra`) probe billing did not reconcile with catalog price. | Open: verify against first HF invoice. Cycle 1 aspect C. |
| F-07 | Reasoning-token pathologies across providers: empty visible content when reasoning consumes small budgets (Aion 3.5-mini, NVIDIA Nemotron Lightning burned 3,172 reasoning tokens on a trivial prompt, OpenRouter Kimi K2.6 empty at 512, Muse Spark 1.3 empty at 2,048). | Entries seeded conservatively; two shipped disabled. Systemic fix = Cycle 1 aspect D. |
| F-08 | Provider flakiness masked as catalog truth: Google Gemma routes 500/slow/thought-leaking across repeated probes; NVIDIA catalog lists models that 404 for this account and one that hung 181s → 500; Cloudflare's shared neuron pool exhaustion made route-level verification impossible until the daily reset. | Flaky routes disabled with dated reasons; Cloudflare re-verification scheduled post-reset. Systemic handling = Cycle 1 aspect E. |
| F-09 | Hermes `write_file` stream stalls concentrated on `openrouter/dots-3-note-preview-free`: turns ended at exactly 2,048 output tokens, `finish=length`, tool calls discarded mid-payload (7 stalls 2026-11-12 evening, 3 more 2026-11-13; sessions at 93k–173k input tokens). First read as a model/provider stream defect. | Differential investigation 2026-11-13 (protocol §7): the gateway's `DEFAULT_MAX_TOKENS = 2048` binds every request whose client omits `max_tokens` — and Hermes omits it (its config carries no cap). Direct and through-gateway arms at 8,192 complete at 6,080 tokens; the unset replica through the gateway reproduces exactly 2,048 / `length`; a ~90k-context arm refutes the squeeze hypothesis. Provider exonerated (published max completion 460,800; its unset default does not bind). Classification **ours-config × interaction**. Fix proposed (raised default budget for tool-bearing requests + Hermes-side cap + ledger `finish_reason` capture), not applied. Secondary: dots-3 tool-argument degeneracy on fresh calls → R4 tooling evidence; entry stays enabled. **CLOSED 2026-11-13:** the gateway half shipped in **v0.8.1** (PR #5, merge `618a11c7b3a23dd983714ca209e38e2fd12a1c32`; tool-bearing default 8192 when the client omits `max_tokens`, plain chat keeps 2048) and was proven live at the deploy gate — the F-09 copy shape from an omitting client completed finish=stop / 8,001 tokens / 320 of 320 lines through the gateway, the plain-chat negative control still bound at exactly 2,048, and a client-sent 4096 bound exactly. The Hermes half (explicit 8192 stated via the base_url-matched provider `extra_body`) was already live from the build run. Still open from the original proposal: ledger `finish_reason` capture (the `truncated_tool_call` class) — carried in R3. |

| F-10 | Classifier sidecar fusion can override the rules layer's HARD size signal: the golden replay (2026-11-13, v0.9.0 stage 5) padded two synthetic prompts to ~65k tokens; the rules classifier forces `long-context` above 60k, but the fused live result was `chat` for both. | Mechanism read from `classifier.py`: when rules confidence < 0.8 the sidecar is consulted with only the first 1,500 chars of text, and the fusion takes the sidecar's `task_class` wholesale — the level gets a tie-rule guard, the class does not. The sidecar literally cannot see the size that triggered the hard signal. Practical blast radius is bounded (selection still enforces per-entry context fit against the true token estimate), but quality dimension and level floors for very large requests can be chosen by the wrong class. Classification **ours (fusion design)**. NOT fixed in v0.9.0 (found by its golden set; out of R4 scope) — proposed fix: hard signals (size > 60k, modality) pin the class before fusion, exactly as J-1 pinned the hard boundaries for explicit pins. Companion findings from the same replay: three sidecar/rules misclassifications on plain prompts (email draft -> code, press release -> infra, logic puzzle -> infra), recorded as known violations in `tests/golden/golden_set.json`. Status: OPEN, proposed for Cycle 1 aspect F/classifier follow-up. |

Carried decisions (Jordan's, not the loop's): effective-cost accounting
(logging-only variant) — undecided; Decision #5 judged sample narrowed to
non-sensitive traffic — undecided; Cerebras PAYG funding before the trial
expires ≈ 2026-12-12; SiliconFlow $0-model probe (console check or explicit
probe OK); "NROUTER" — unidentified service, asked 2026-11-12.

## Cycle 1 — opened 2026-11-12

Aspects (each maps to a Cycle 0 failure cluster and/or an ADOPT item from the
2026-11-12 routing research brief):

- **A. Deploy safety.** Pre-flight assertions (file ownership/permissions,
  registry checksum agreement across the three copies), post-restart health
  gate, scripted rollback. (F-01)
- **B. Registry integrity invariants.** Generalize the collision guard:
  id↔model-string uniqueness classes, required price-source provenance,
  mandatory caveat assertions (e.g. NVIDIA trial terms), lane/flag coherence.
  (F-02, F-04)
- **C. Price truth maintenance.** Periodic re-verification of registry prices
  against provider sources; HF reconciliation (F-06); OpenRouter floating
  billed rates; drift alerts when a provider's published price moves.
- **D. Reasoning discipline.** Reasoning-effort as a routing dimension;
  empty-content detection with one bounded structural step-up (never
  self-reported confidence); per-entry reasoning behavior profiles from probe
  evidence. (F-07; brief: structural escalation)
- **E. Provider reliability.** Rate-limit ledger + pre-emptive demotion;
  error-classified retry + per-provider circuit breaker; evidence standard
  for auto-disabling flaky routes. (F-08; brief ADOPT items)
- **F. Verification harness.** Standing pin battery (unique conversations) as
  a repeatable tool; golden set (100–300 prompts) + shadow-before-promote as
  the gate for registry changes. (F-03; brief ADOPT items)
- **G. Privacy fail-closed audit.** Fault-injection tests proving sensitive
  traffic can never land on public-only-free or sensitive-excluded routes,
  run continuously rather than once. (brief ADOPT item)

Cycle 1 stage 1–3 output (research, tool choices, documented update plans)
goes to `docs/cycles/cycle-01-plan.md` for Jordan's review before any build.
