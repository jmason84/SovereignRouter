# Fix Package v0.8.2 — Decision Package: Stream Resilience (F-11)

- **Status:** BUILT, not deployed. Branch `fix/v0.8.2-stream-resilience`
  in `~/workspace/sr-build5`, based on main `a4640aff` (v0.8.1 records
  tip). No GitHub push (waits for the owner's egress-approval tap),
  no production contact beyond the read-only differential probes in §2.
- **Trigger:** failure **F-11** — Hermes executive-ops session
  `20261113_225914_0c6b568c` died on three consecutive router 502s,
  2026-11-13 16:26–16:33 MST.
- **Scope, one line:** streaming time bounds (first-chunk 45 s / stall
  60 s) instead of the flat 120 s read timeout; a bounded, self-healing
  600 s entry cooldown after any streaming abort, read by selection and
  sticky reuse; the `truncated_tool_call` attempt-ledger class carried
  from R3. **Code-only: the registry is unchanged.**

---

## 1. Incident timeline (all MST, 2026-11-13)

| Time | Event |
|---|---|
| ~02:18 | Same entry, different failure class: `zai/glm-4.5-flash` re-issues an identical defective tool call ×5 in an exec-ops session → guardrail halt (semantic defect; forensics closed it as model-side, router innocent). |
| 16:26–16:33 | Exec-ops session `20261113_225914_0c6b568c` (~71.5k tokens, 118 msgs): three consecutive turns 502. Every attempt served by `zai/glm-4.5-flash`, lane public-only-free, level 4, data_class internal. Route rows carry `stream_aborted_after_commit:zai/glm-4.5-flash`; `latency_upstream_ms` = 121,290 / 121,229 / 151,710. Hermes `errors.log`: "upstream stream interrupted after partial output", attempts 1/3, 2/3, 3/3 — turn dies. |
| ~16:45 | Parent verification: router otherwise healthy (349 turns / 0 failed turns in the surrounding window that morning on v0.8.1); incident isolated to this entry + session shape. F-11 opened; this package ordered. |

Root mechanics, verified in the v0.8.1 source:

1. Upstream transport is `httpx.AsyncClient(timeout=httpx.Timeout(120.0,
   connect=10.0))` — one flat read timeout. A stream that stalls hangs
   ~2 minutes before anything gives up (the two 121 s latencies sit
   directly on that bound).
2. Streaming text commits at the first content chunk (ADD-06 guard), so
   once partial output flowed, in-request failover was impossible *by
   design* — the turn could only terminate.
3. **The aggravating router fault:** Hermes' retries were NEW requests,
   and selection re-picked the same entry all three times. v0.8.0's
   attempt ledger records failures, but selection never reads them
   (J-2 observe-only), and sticky-session reuse pointed the same way.
   Nothing in the router remembered the entry had just aborted — twice.

---

## 2. Differential probes (route-health protocol) + verdict

Ten probes from VM103, 2026-11-13 ~17:0x–17:5x MST. Direct arms used
the production `zai` key read in-process on VM103 (never printed or
copied); the gateway arm ran inside the gateway container (loopback —
the nginx source allowlist 403s host-side requests to :6075, as
designed) with the hermes client key injected via `docker exec -e`,
pinned to `zai/glm-4.5-flash`, unique conversation per probe.

| Probe | Shape | TTFC | Total | Finish | Outcome |
|---|---|---|---|---|---|
| a1 canonical-direct | short chat, minimal body | 4.65 s | 9.72 s | stop (209 tok) | completed |
| a2 canonical-direct | long output (~2,900 tok) | 0.66 s | 65.82 s | stop (2,906 tok) | completed |
| a3 canonical-direct | tool-bearing | 0.64 s | 2.47 s | tool_calls | completed |
| b1 gateway-shaped direct | short (gateway budget + `include_usage`) | 0.56 s | 3.39 s | stop | completed |
| b2 gateway-shaped direct | long, same shaping | 0.53 s | 59.67 s | stop (2,467 tok) | completed |
| b3 gateway-shaped direct | tool, same shaping | 0.68 s | 3.23 s | tool_calls | completed |
| d1 sibling control | `glm-4.7-flash` short, same key/endpoint | **60.73 s** | 72.93 s | stop (705 tok, 698 reasoning) | completed |
| c1 through gateway | short, pinned | 0.74 s | 54.88 s | **length — 2,048 chunks, 0 visible chars** | completed (reasoning burn) |
| c2 through gateway | long, pinned | **33.11 s** | 107.96 s | stop (2,064 chunks) | completed — ~12 s inside the 120 s timeout |
| c3 through gateway | tool, pinned | 30.17 s | 33.06 s | tool_calls | completed |

**Verdict: provider (stall/latency characteristics) × ours-config
(aggravation). Gateway shaping is exonerated** — gateway-shaped direct
(a/b arms) matches canonical direct in outcomes on every shape; no
shaping variable changed completion. What the probes establish:

- **No probe reproduced a stall/abort.** Every stream that started
  flowing completed. The incident's >120 s gap is a draw the probes
  did not hit — but the free tier's demonstrated variance makes it
  credible: first-chunk times on this entry ran 0.5–33.1 s, the sibling
  took 60.7 s to first chunk, and long generations run ~45 tok/s
  (a through-gateway long turn finished at 108 s, ~12 s from the wall).
- The entry also shows the F-07 reasoning-burn pattern (c1: full 2,048
  budget, zero visible content) — a quality/behavior note, not this
  incident's mechanism.
- **Registry action: NONE.** The tasking allowed a quality-score
  adjustment only if the probes convicted the entry; they did not —
  it served all three shapes correctly in 9 of 10 probes (c1's burn
  aside). Cutting its scores on non-reproduced evidence would break
  the protocol's evidence standard, and the fixes below are
  entry-agnostic: they contain a stall from ANY route. The entry
  stays enabled, scores unchanged. **Consequence for rollback: this
  release is code-only — rollback is an image swap, with NO registry
  restore required** (contrast v0.9.0, whose registry changes make
  its rollback a registry + image restore).

---

## 3. What changed (vs main `a4640aff`)

### `src/gateway.py` (VERSION 0.8.1 → 0.8.2)

- **Stream bounds.** The streaming loop now drives
  `resp.aiter_lines()` manually, wrapping each read in
  `asyncio.wait_for`: the first line gets
  `stream_first_chunk_timeout_s` (default **45 s**), every later line
  `stream_stall_timeout_s` (default **60 s**); the non-200 error-body
  read is bounded by the first-chunk value. Expiry raises
  `TimeoutError` into the *existing* transport-error handler — the
  ADD-06 commit guard, pre-commit failover, and ledger writes are
  untouched and apply unchanged. A new route signal
  `stream_bound_timeout:<entry>` distinguishes bound-fired aborts
  from transport drops. The client-wide flat 120 s timeout remains
  as the outer bound; non-streaming calls are unchanged.
  *Why these defaults:* the incident's healthy sibling traffic starts
  in seconds; 45/60 s are stall-catchers, not latency discipline —
  but see the watch item in §5: they are within ~1.4× of the slowest
  observed healthy first chunk, so they are env-tunable without a
  code change if field evidence says they bite healthy turns.
- **Cooldown on stream abort.** The transport/bound handler now calls
  `note_stream_abort()` for EVERY streaming abort (committed or not,
  drop or bound): the entry cools for `stream_abort_cooldown_s`
  (default **600 s**). Ten minutes is long enough to cover a client
  retry burst and a transient provider wedge, short enough that a
  recovered free route rejoins the bench inside the same work
  session — and because the cooldown is failure-triggered (not
  schedule-based), a still-broken entry simply re-cools on its next
  post-expiry attempt, which is also its recovery probe.
- **Selection reads the cooldown.** `build_candidates` checks the
  cooldown LAST (after enabled / lane / key / sensitive wall / tools
  / quality / context / verification / provider limits): a cooling
  entry is skipped with reason `cooldown_stream_abort`. The sticky
  block no longer adds the generic `sticky_no_longer_eligible` when
  the sticky entry's skip is already the cooldown reason.
- **Only-candidate exception.** If a `build_candidates` pass ends
  with zero candidates but a non-empty cooled set (entries that
  passed every other gate), the cooled set is restored as the
  candidate list, each annotated
  `cooldown_bypassed_only_candidate` in the skip list. A cooldown
  therefore never manufactures a zero-candidate 429. (Judgment call,
  recorded: the restore is per-pass, so it protects the spillway and
  level-5-fallback passes identically.)
- **Pins:** unchanged. An explicit pin still force-inserts a cooling
  entry — J-1 ruled pins override everything softer than the two
  hard boundaries, and the cooldown is a soft, self-healing skip.
  The F-11 retries were auto-selection, which the cooldown now
  covers; an operator pinning a model for diagnosis keeps that power.
- **`truncated_tool_call` wiring.** `_record_attempt` gains a
  `truncated_tool_call` flag; the JSON path computes it from
  `finish_reason == "length"` + `message.tool_calls`, the streaming
  tool path from the assembler's finish reason + `has_tool_calls`,
  and both pass it at every served-attempt record site (final serve,
  validation escalation, same-model retry, `_finalize_stream`).

### `src/quota.py`

- `set_stream_cooldown` / `stream_cooldown_until` /
  `clear_stream_cooldown` on `QuotaStore`, persisted in the existing
  `meta` table (`stream_cooldown:<model_id>` → expiry timestamp).
  Chosen over an in-process dict: the meta table is the store's
  existing key-value path (no schema change), the cooldown survives
  gateway restarts (a restart during an incident must not resurrect
  the stalling entry), and expiry is a timestamp comparison — no
  reset tooling, no background job.

### `src/pools.py`

- `FAILURE_CLASSES` gains `truncated_tool_call`, with
  `classify_truncated_tool_call(entry)` (scope: model). The class is
  assigned by the gateway — the response WAS received, so
  `classify_failure` (status/transport-driven) never returns it. On
  the attempt row the outcome stays `served`; the class also feeds
  the observe-only `would_demote` annotation, so the defect accrues
  J-2 evidence like every other class.

### Config (all `SOVEREIGN_`-env overridable, per the existing pattern)

| Setting | Default | Env |
|---|---|---|
| First-chunk bound | 45 s | `SOVEREIGN_STREAM_FIRST_CHUNK_TIMEOUT_S` |
| Inter-chunk stall bound | 60 s | `SOVEREIGN_STREAM_STALL_TIMEOUT_S` |
| Abort cooldown | 600 s | `SOVEREIGN_STREAM_ABORT_COOLDOWN_S` |

### Tests — 627 → 636 (`tests/test_stream_resilience.py`, 9 new)

Stall aborts inside the bound (elapsed asserted against a 3 s drip,
not 120 s); first-chunk timeout fails over pre-commit and serves the
sibling; a transport abort sets the cooldown in the store; the next
new-conversation selection skips the cooled entry
(`cooldown_stream_abort`) and serves the sibling; sticky reuse
respects the cooldown with the precise reason; the only-candidate
case still serves with the bypass annotation; an expired cooldown
does not skip; `truncated_tool_call` lands on attempt rows via both
the JSON and streaming paths; a plain serve carries no failure class.
Plus the standing version-pin assertion updated to 0.8.2. The full
suite passes: **636 passed**.

Golden replay: **not run — the tool is not in this tree.** The golden
set + `tools/golden_replay.py` exist only on the unmerged R4 branch
(`feature/v0.9.0-quality-tooling`); main `a4640aff` has neither.
Running it here would have meant importing R4 work into this branch,
out of scope. When R4 lands, replay this branch's selection changes
(cooldown skip is a new skip reason) against the golden baseline
(0 new / 5 known) before deploying whichever ships second.

---

## 4. Behavior change, in plain terms

- A wedged stream now dies in at most ~45 s before its first chunk or
  ~60 s of mid-stream silence — not ~120 s — and after commit it
  still ends with the same typed interruption event (no behavior
  change for clients beyond dying sooner).
- After any streaming abort, that model sits out for 10 minutes.
  A client that retries (Hermes does, 3×) lands on a sibling model
  instead of the stalling one. If literally nothing else can serve,
  the cooling model serves anyway and the route row says so.
- A model whose tool call was cut off by the output budget now
  leaves a countable ledger trace (`truncated_tool_call`) instead of
  a clean-looking serve.
- Nothing else moves: no registry, routing-policy, lane, pin,
  spillway, governor, or sensitivity change. J-2 stays observe-only
  for every class except this one bounded, proven-harm cooldown.

## 5. What is NOT in this release

- **No registry change of any kind** (see §2): no disables (they stay
  proposal-only), no quality edits, no `verified_until` changes.
- **No cooldown for non-streaming failures, HTTP-status failures, or
  any other attempt class.** A non-streaming transport failure fails
  over inside the same request, so the retry-repick harm does not
  exist there; extending the cooldown is a J-2-shaped decision, not
  smuggled in here.
- **No change to the flat client timeout for non-streaming calls,**
  and no per-entry timeout overrides (the sibling's 60.7 s first
  chunk argues for studying per-entry bounds later; the env knobs
  are the interim lever).
- **No Hermes-side change** (retry policy, session handling) and no
  fix for the morning's semantic tool-call defect — that is the R4
  tooling-dimension question, not stream resilience.
- **No deploy, no push.** Both wait for the owner's gates.

## 6. Deploy gate (owner decisions)

1. **Push** `fix/v0.8.2-stream-resilience` (egress-approval tap) →
   PR → CI read-back.
2. **Deploy** at the standard gate (snapshot `pre-v082`, image build
   on VM103, recreate, /health = 0.8.2, smokes). Rollback: image swap
   back to v0.8.1 — **code-only, no registry restore** (contrast
   v0.9.0). Cooldown meta keys written by v0.8.2 are inert under
   v0.8.1 (never read) and expire on their own.
3. **Post-deploy watch (first days):** count `stream_bound_timeout`
   signals and check whether the turns they cut would have completed
   (the §2 evidence: healthy first chunks observed at 33.1 s on this
   entry, 60.7 s on its sibling). If the bound bites healthy free-tier
   turns, raise `SOVEREIGN_STREAM_FIRST_CHUNK_TIMEOUT_S` (60–90 s)
   via env — no rebuild. Likewise, `cooldown_bypassed_only_candidate`
   frequency is the signal for whether 600 s is too long for thin
   lanes.
