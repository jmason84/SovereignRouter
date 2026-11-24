# LiteLLM secondary borrows — design notes (no code)

**Date:** 2026-11-24. **Status:** DESIGN ONLY. These three borrows
from the LiteLLM comparison (`litellm-vs-sovereign` analysis,
2026-11-24) were scoped as design spikes in the borrow package
(PR: `feature/litellm-borrows`). Nothing here is implemented; each
note states the design, the gate it waits on, and the evidence that
would justify building it. The primary borrows (price-drift
tripwire, per-attempt telemetry contract, discovery tripwire)
shipped as code in the same package.

---

## S-1 — Presidio-class second detector behind the sensitivity scanner

**Borrow:** LiteLLM's guardrail catalogue uses Microsoft Presidio
(NER + checksum-validated pattern recognizers) for PII/PHI. Sovereign's
sensitivity tripwire is pattern-based; the golden set proved a prose
dialect could slip it once (F-18, fixed by adding prose-assignment
patterns). A second, *independent* detector class would harden the
wall's input.

**Design:**
- Run a Presidio analyzer pass in the classifier sidecar, detection
  only. Sovereign's response to a hit stays what it is today —
  routing (clearance), never masking.
- Fusion shape mirrors F-10's documented approach: the Presidio pass
  can only RAISE the sensitivity floor, never lower the pattern
  scanner's verdict. Disagreements are logged as signals for the
  golden-set corpus.
- Latency discipline: run the pass only when the pattern scanner's
  confidence is low or the message shape is prose-heavy — the same
  conditional-fusion shape the sidecar already uses — so the hot
  path for agent/tool traffic is untouched.
- Regression gate: the full 180-item golden set plus a new
  Presidio-targeted slice (NER-shaped names/addresses/IDs in prose)
  must show zero L4/L5 under-classification before it can influence
  a live verdict; until then it runs observe-only, exactly like the
  demotion tracker did under J-2's posture.

**Gate:** the next time the classifier opens for other work, plus
owner approval of a second container on VM103 (sidecar grows a
dependency). **Effort:** M. **Risk:** low-medium (latency, false-
positive floors pushing traffic to paid lanes — bounded by the
raise-only fusion and the observe-only runway).

## S-2 — Exact-match response cache for the non-agentic slice

**Borrow:** LiteLLM's response cache design — and its own warning
that semantic caching is unsafe for multi-turn/agentic traffic.

**Design:**
- Exact-match ONLY, keyed on a hash of the full canonical request
  (client, model selection inputs, messages, tools). No semantic
  similarity, ever.
- Eligible slice, all conditions required: classified non-sensitive
  (L1–L2, data_class public/internal with no detectors fired),
  non-tool-bearing, single-shot (no conversation/sticky key),
  non-streaming. The candidate shape is n8n's deterministic
  transforms, not agent loops.
- In-memory only, short TTL (minutes), per-client opt-out header.
  Never persisted to disk: a cache is a content store, and
  Sovereign's posture is no-content-anywhere — an in-memory cache
  with a short TTL is the furthest this design may go without a
  separate owner ruling on that posture.
- Cache hits are recorded on the route record (`cache_hit` signal)
  and priced $0 in the ledger with the original serve's cost kept
  attributable — the ledger stays honest about what was spent.

**Gate:** a concrete, measured non-agentic repeat-traffic case
(same-request repeats visible in routes.jsonl at a rate that makes
the saved upstream calls worth a content store's review cost).
Absent that evidence, this stays unbuilt — expected value at
current volume is modest. **Effort:** M. **Risk:** medium (the
privacy review is the real cost, not the code).

## S-3 — Breaker thresholds as registry fields (at the J-2 gate)

**Borrow:** LiteLLM's per-deployment `allowed_fails` /
`cooldown_time` — cruder than R2's scoped taxonomy, but its
per-entry config surface (two numbers on the entry, visible in one
place) is cleaner than ours.

**Design:**
- When J-2 is ruled and breaker enforcement leaves observe-only,
  expose per-entry optional registry fields in that shape, e.g.
  `breaker: {allowed_fails: N, cooldown_s: S}`, layered UNDER the
  existing taxonomy: the fields tune the model-scoped breaker only;
  pool/account scopes keep their derived rules (they encode pool
  facts, not per-entry taste).
- Absent fields = current derived behaviour, unchanged. The fields
  are configuration, so they inherit the registry's discipline:
  git-reviewed, deploy-gated, never runtime-edited.
- The would_demote annotations already emitted by the tracker are
  the rehearsal data: before enforcement, a replay over the attempt
  ledger shows exactly which entries the field defaults would have
  broken and for how long — that replay is part of the J-2 packet.

**Gate:** J-2 itself. This note must not be implemented ahead of
the owner's J-2 ruling — enforcement authority is the decision,
the config shape is a detail that follows it. **Effort:** XS at
J-2 time. **Risk:** none as a note.
