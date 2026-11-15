# Golden replay — FULL set (180 items) against live v0.9.0

Date: 2026-11-15 (America/Denver), ~02:40 MST. Ordered by Jordan
2026-11-15 02:15 MST ("DO SO") to close the v0.9.0 testing gaps named
at the deploy report: the golden set was a 24-item starter; the full
~180-item set was designed (cycle-01-plan F.3.2) but not built.

## Method

- `tests/golden/golden_set.json` **v2**: items g01–g24 are the v1
  starter, byte-identical; g25–g180 composed to the F.3.2 design —
  verifier categories: infra/ops 36, tool turns 36, long-context 27,
  JSON drafting 27, chat+code drafting 27, reasoning 18,
  sensitive-marker 9; clients to the census mix (hermes 113 /
  openclaw 43 / n8n 22 / crm 2). All prompts synthetic,
  non-sensitive (sensitive-marker items carry FAKE credential
  shapes). Decision-level (shadow) verifiers only — the starter's
  honesty rule: answer-level verifiers belong to the live-replay
  sample, which stays designed-but-report-only (Decision #5's
  judged sample remains OFF).
- Replay: `tools/golden_replay.py` against the **live production
  v0.9.0 gateway** `/v1/shadow` (run inside the gateway container;
  nginx allowlists by source and 403s VM103's own IP — same
  constraint as every prior smoke). Shadow makes no upstream calls.
  Capture committed as `tests/golden/baseline-full-2026-11-15.json`.
- Extrapolation disclosure: F.3.2 specifies category percentages,
  census shares and verifier kinds, not per-item prompts. Item
  shapes follow the starter's established patterns (task_class /
  task_class_in / data_class / first_lane_in /
  forbidden_first_lanes / pad_to_tokens). Expectations were NOT
  loosened after seeing results — the set stays a measuring
  instrument; analysis lives here, not in edited expectations.

## Verdict

**180 items run · the 5 known violations reproduce exactly
(g07, g08, g15, g17, g19) · 69 NEW violation strings across 63 new
items · 112 items fully clean.**

Diffs on the original 24 vs `capture-2026-11-14-v090.json`: **6
items, all `candidates`-only** — the sole membership change is
`openrouter/lfm-2.5-2.6b-free` leaving candidate lists (disabled by
tonight's consolidated registry update). First choices, classes,
levels unchanged. Attributed and expected; no other drift.

The 69 new violations fall into four groups. Only Group S is a
genuinely new defect class; Groups L and M are known mechanisms
measured at scale for the first time; Group P is spec-conformant
behavior whose driver is classification calibration.

### Group L — long-context is a >60k-token size class; below it, size earns nothing (21 items)

Every new long item padded to ≤65k chars-target (g134–g154)
classified **chat** (18) or **infra** (3), level 3 — including the
45k and 65k pads. The three 90k pads (g155–g157) classified
**long-context, level 4**, correctly. Mechanism, from
`src/classifier.py`: the rules layer assigns `long-context` ONLY
from the hard rule `est_input_tokens > 60_000`; the 20k tier raises
level only. Production token estimation is real BPE, under which
the replay tool's char-based pads land lower than nominal (the 65k
pads estimate ≈47k — measured 2026-11-14), so the effective
engagement threshold sits between ≈47k and ≈65k estimated tokens,
exactly where the code puts it. Below the threshold the class is
purely lexical (sidecar fusion over the first 1,500 chars — the
F-10 pathway).

Reading: an 8k-token prompt is not "long" in this system's
semantics, and the smallest pads' expectations were the design's
category label applied below the system's size floor — calibration
notes, not defects. The substantive measurement is the 25k–65k
band (15 items): production-shaped extraction asks over
≈20–47k-token contexts receive **no size credit at all** — they
route on lexical class (chat/infra) at level 3. Whether the class
threshold should stay at 60k is a design question for the F-10 fix
(hard signals pin class pre-fusion); this run supplies the
before-picture across the whole size range.

### Group M — short-prompt task_class misclassification, the g07/g08/g15 family at scale (39 items)

The sidecar's lexical class on short prompts is systematically
unreliable in the same directions as the three non-F-10 known
violations (email→code, press→infra, puzzle→infra):

- **infra set (6 class misses):** g040 storage-migration plan →
  long-context; g041 backup-permissions diagnosis → chat; g043
  port-check how-to → chat; g050 mail-queue causes → chat; g052
  fail2ban configuration → chat; g054 image-pull error → reasoning.
- **drafting set (9):** thank-you note, product description,
  meeting agenda, speaker bio → **code** (g056/g057/g059/g061);
  newsletter summary, dinner invitation → infra (g058/g067);
  headline options → infra (g063); FAQ answer → reasoning (g065);
  regex help (expected code) → chat (g070).
- **JSON set (9):** API-ticket body, primes-as-JSON, log-line
  parse, triangle JSON, email extraction → infra
  (g082/g085/g087/g091/g096); JSON restructure, café menu,
  weather-API shape → reasoning (g083/g088/g097); status-code
  reference (expected chat) → code (g094).
- **reasoning set (12 of 16 new items):** sheep trap, balance
  puzzle, bridge crossing, truth-tellers, rollout decision,
  pipes/rates → infra (g158/g162/g164/g167/g170/g171); switches,
  percentages, coin fallacy, overtake trap → chat
  (g159/g163/g168/g173); machines-rate trap → long-context
  (g165). Only 4 of 16 new reasoning prompts classified reasoning.

Consequence assessment: misclassification among chat/code/infra/
reasoning on short prompts mostly costs ranking quality (the
quality map differs by class), not safety — selection still
enforces context fit and lane policy on the true request. It is
the same defect family the starter recorded on 3 items; the full
set prices it: **39 of 117 short-prompt items (33%)** in the
affected categories carry a wrong class.

### Group P — paid-first via L5 + internal: spec-conformant, calibration-driven (5 items)

g035, g047, g063, g067, g167 each drew **level 5 + data_class
internal**, which under the spec (cf. starter g16) restricts first
choice to the paid lane — all five first-routed to
`deepinfra/deepseek-v4-flash-0731` (paid). The lane behavior is
correct GIVEN the classification; the driver is L5 assignment on
short business/ops-vocabulary prompts (a runbook draft, a
health-check script outline, blog headline options, a dinner
invitation, a backup-policy question). This is the golden-set
counterpart of the level_mix alert theme (diagnosed 2026-11-15:
production levels track context size coherently; on short
synthetic prompts the lexical L5 triggers are coarse). Recorded
as calibration evidence — not a routing defect.

### Group S — NEW: prose-stated credentials classify `internal`, and `internal` may first-route public (4 items)

The sensitive set split cleanly on credential SHAPE:

- Explicit shapes classified **sensitive** and routed
  private-free: `sk-FAKE…` token (g174), `-----BEGIN RSA PRIVATE
  KEY-----` mention (g176), `AWS_SECRET_ACCESS_KEY=FAKE…` (g178).
- Credential VALUES stated in prose classified **internal**:
  a database password (g175), an OAuth client secret (g177), a
  user-shared account password (g179), a session-cookie value
  (g180). At `internal`, public-only-free is policy-eligible —
  and g175 + g180 **first-routed to `cloudflare/gpt-oss-120b`
  (public-only-free)**: had these been live turns, credential
  content would have been offered first to a third-party free
  endpoint.

The sensitivity patterns evidently key on key/token shapes and
miss password/secret/cookie values stated in prose. This is a
new finding, adjacent to but distinct from F-16 (dots-3/
north-mini-code inside the sensitive wall): F-16 is about which
entries sit inside the wall; this is about the wall's tripwire
missing a credential dialect. **Recommended as a new Improvement
Loop entry (candidate F-18):** extend sensitivity detection to
prose-stated credential assignments (password/secret/cookie =
value shapes), with these golden items as the regression set.
No production change was made in this pass.

## Itemized NEW violations (expected → observed; route = would-route first choice)

| item | expected | observed class / level / data | route |
|---|---|---|---|
| g035-infra-x11 | infra, free first | infra / L5 / internal (paid-first, Group P) | deepinfra/deepseek-v4-flash-0731 |
| g040-infra-x16 | infra | long-context / L4 / internal | google/gemini-3.1-flash-lite |
| g041-infra-x17 | infra | chat / L1 / internal | openrouter/nemotron-3-ultra-free |
| g043-infra-x19 | infra | chat / L1 / internal | openrouter/nemotron-3-ultra-free |
| g047-infra-x23 | infra, free first | code / L5 / internal (paid-first, Group P) | deepinfra/deepseek-v4-flash-0731 |
| g050-infra-x26 | infra | chat / L1 / internal | openrouter/nemotron-3-ultra-free |
| g052-infra-x28 | infra | chat / L3 / internal | openrouter/nemotron-3-ultra-free |
| g054-infra-x30 | infra | reasoning / L2 / internal | openrouter/north-mini-code-free |
| g056-draft-x02 | chat | code / L4 / internal | openrouter/north-mini-code-free |
| g057-draft-x03 | chat | code / L3 / internal | openrouter/north-mini-code-free |
| g058-draft-x04 | chat | infra / L2 / internal | cloudflare/gpt-oss-120b |
| g059-draft-x05 | chat | code / L4 / internal | openrouter/north-mini-code-free |
| g061-draft-x07 | chat | code / L4 / internal | openrouter/north-mini-code-free |
| g063-draft-x09 | chat, free first | infra / L5 / internal (paid-first, Group P) | deepinfra/deepseek-v4-flash-0731 |
| g065-draft-x11 | chat | reasoning / L4 / internal | openrouter/nemotron-3-ultra-free |
| g067-draft-x13 | chat, free first | infra / L5 / internal (paid-first, Group P) | deepinfra/deepseek-v4-flash-0731 |
| g070-draft-x16 | code | chat / L1 / internal | openrouter/nemotron-3-ultra-free |
| g082-json-x09 | chat/code | infra / L3 / internal | cloudflare/gpt-oss-120b |
| g083-json-x10 | chat/code | reasoning / L5 / internal | openrouter/nemotron-3-ultra-free |
| g085-json-x12 | chat/code | infra / L4 / internal | cloudflare/gpt-oss-120b |
| g087-json-x14 | chat/code | infra / L2 / internal | cloudflare/gpt-oss-120b |
| g088-json-x15 | chat | reasoning / L4 / internal | openrouter/nemotron-3-ultra-free |
| g091-json-x18 | chat/code | infra / L2 / internal | cloudflare/gpt-oss-120b |
| g094-json-x21 | chat | code / L4 / internal | openrouter/north-mini-code-free |
| g096-json-x23 | chat/code | infra / L4 / internal | cloudflare/gpt-oss-120b |
| g097-json-x24 | code/chat | reasoning / L5 / internal | openrouter/nemotron-3-ultra-free |
| g134–g154-long (21 items) | long-context | chat ×18 / infra ×3, all L3 (Group L) | openrouter/nemotron-3-ultra-free (chat) / cloudflare/gpt-oss-120b (infra) |
| g158-reas-x01 | reasoning | infra / L4 / internal | cloudflare/gpt-oss-120b |
| g159-reas-x02 | reasoning | chat / L3 / internal | openrouter/nemotron-3-ultra-free |
| g162-reas-x05 | reasoning | infra / L4 / internal | cloudflare/gpt-oss-120b |
| g163-reas-x06 | reasoning | chat / L1 / internal | openrouter/nemotron-3-ultra-free |
| g164-reas-x07 | reasoning | infra / L4 / internal | cloudflare/gpt-oss-120b |
| g165-reas-x08 | reasoning | long-context / L4 / internal | google/gemini-3.1-flash-lite |
| g167-reas-x10 | reasoning, free first | infra / L5 / internal (paid-first, Group P) | deepinfra/deepseek-v4-flash-0731 |
| g168-reas-x11 | reasoning | chat / L1 / internal | openrouter/nemotron-3-ultra-free |
| g170-reas-x13 | reasoning | infra / L3 / internal | cloudflare/gpt-oss-120b |
| g171-reas-x14 | reasoning | infra / L2 / internal | cloudflare/gpt-oss-120b |
| g172-reas-x15 | reasoning | chat / L4 / internal | openrouter/nemotron-3-ultra-free |
| g173-reas-x16 | reasoning | chat / L1 / internal | openrouter/nemotron-3-ultra-free |
| g175-sens-x02 | sensitive, no public first | internal / L3 (Group S) | cloudflare/gpt-oss-120b ⚠ public |
| g177-sens-x04 | sensitive | internal / L4 (Group S) | openrouter/north-mini-code-free |
| g179-sens-x06 | sensitive | internal / L2 (Group S) | openrouter/north-mini-code-free |
| g180-sens-x07 | sensitive, no public first | internal / L3 (Group S) | cloudflare/gpt-oss-120b ⚠ public |

## What this run does NOT claim

- Shadow is decision-level: nothing here scores answer quality
  (the judged/live sample remains OFF per Decision #5).
- The baseline committed today is a CAPTURE of live v0.9.0
  behavior, not a claim that behavior is correct — Groups L/M/S
  are precisely where it isn't.
- No classifier, registry, or production change was made in this
  pass. Group S is written up for Jordan's ruling alongside F-16.
