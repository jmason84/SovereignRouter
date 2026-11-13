# Fix Package v0.8.1 — Decision Package: tool-bearing default output budget (F-09)

- **Date:** 2026-11-13 (build run, ~01:15 MST)
- **Status:** **DEPLOYED 2026-11-13** (owner's order at the standard
  gate). PR #5 merged cleanly via the connector — merge commit
  `618a11c7b3a23dd983714ca209e38e2fd12a1c32`; production runs image
  `192.168.0.28:5000/sovereign-router:v0.8.1` (snapshot
  `snapshots/pre-v081-20261113`; registry unchanged, md5
  `96eaa3c99643e1016508cee16caf23de` across all three copies). The
  CI read-back gap recorded at build time is CLOSED: PR run #32
  (`43393991009`) and the synchronize run (`43394342150`) both read
  back **SUCCESS** via the GitHub API, as did run `43396867354` on
  main for the merge commit. Live verification at the gate
  (omitting-client smokes): the F-09 copy shape, tool-bearing with
  `max_tokens` unset, completed finish=stop at 8,001 tokens with
  320/320 lines; the plain-chat negative control still bound at
  exactly 2,048; a client-sent 4096 bound exactly; the streaming
  arm bound at 8,192 and an identical re-run completed the full
  SSE (stop, 8,001 tokens, 320/320 lines); unpinned auto served
  200 on the free lane. Full deploy record: CHANGELOG [0.8.1].
  Note: the original deploy agent was interrupted after the deploy
  proper; verification and records were completed in a separate
  pass (no re-merge, no re-deploy). The deploy was a **code-only
  swap** — this package changed no registry content.
- **Origin:** failure log **F-09** and the route-health differential
  investigation (`docs/cycles/route-health-investigation-protocol.md`
  §7, worked example 3). Classification there: **ours-config ×
  interaction** — the gateway's `DEFAULT_MAX_TOKENS = 2048` bound
  every client that omits `max_tokens`; Hermes omits it on every
  turn; sticky long-context sessions concentrate on
  `openrouter/dots-3-note-preview-free`, whose tool-call payloads
  (Hermes' longest outputs) truncated mid-stream at exactly 2048
  (`finish=length`) while the model's published completion ceiling
  is 460,800 and direct probes completed 6,080 tokens whenever a
  larger budget reached it. The owner ordered both halves of the
  §7 fix proposal, gateway first (2026-11-13 ~01:13 MST): this
  package is the gateway half; §4 records the Hermes client half.

---

## 1. What changed — exact diff vs GitHub main (v0.8.0)

### `src/gateway.py` (VERSION 0.8.0 → 0.8.1)

1. **New constant** `TOOL_DEFAULT_MAX_TOKENS = 8192`, beside
   `DEFAULT_MAX_TOKENS = 2048` (unchanged), with the F-09 rationale
   in the comment block — including why the global default was NOT
   raised (it would lengthen every free-lane generation, runaway
   ones included, against shared pool limits).
2. **New predicate/helper** `_default_max_tokens(body)` — the
   output budget a request gets when neither the client nor an
   internal override states one. It returns
   `TOOL_DEFAULT_MAX_TOKENS` for a tool-bearing request and
   `DEFAULT_MAX_TOKENS` otherwise.
3. **`upstream_body` fallback** — the single change to the budget
   computation: the last term of the `requested` chain changes from
   `DEFAULT_MAX_TOKENS` to `_default_max_tokens(body)`. Everything
   upstream of that term is untouched: an internal
   `max_tokens_override` (validation-escalation retries) still wins
   first, then a client-sent `max_tokens`, then a client-sent
   `max_completion_tokens`. The remaining-context clamp
   (`max(64, min(requested, room))`) still applies on top of the
   result, and the budget is still written explicitly into the
   upstream body — as `max_completion_tokens` for
   `completion_tokens_param` entries (the gpt-5.5 shape), as
   `max_tokens` for everything else.

### The predicate, stated exactly

A request is **tool-bearing** iff

- `body["tools"]` is a **non-empty** array, **or**
- `body["tool_choice"]` is **present** (any explicit value,
  including `"none"` and `"auto"`).

The first clause mirrors how the codebase's own handlers read the
request for selection (`tools = body.get("tools") or []`,
`has_tools=bool(tools)`). The second clause is deliberate: a client
that sets `tool_choice` has stated a tool-calling shape for the
request, and the raised default is a *ceiling*, not a target — a
turn that emits no tool call simply never approaches it, while the
tool calls that shape produces are protected. An empty `tools`
array is NOT tool-bearing and keeps the 2048 default (asserted in
the tests).

### What did NOT change

- `src/registry.json` — untouched (no registry step at deploy).
- Selection-side estimates: the handlers still compute their
  internal `max_tokens` estimate as `body.get("max_tokens") or
  DEFAULT_MAX_TOKENS` for candidate fit/cost estimation
  (`est_out = max_tokens // 2`). That is a ranking estimate, not
  the upstream budget; changing it was out of this package's scope
  and is recorded here so a future reader does not mistake it for
  an oversight.
- Client-sent values, the clamp, the extra_body merge, the
  reasoning-effort poison guard, pins, spillway, the attempt
  ledger — all untouched.

### Tests

- New `tests/test_tool_output_budget.py` — 11 tests, recording
  MockTransport asserting exact upstream bodies (the
  `test_effort_and_gemma_shaping.py` pattern), most of them pinned
  to the F-09 entry itself (`openrouter/dots-3-note-preview-free`)
  as the `hermes` client:
  - predicate matrix at unit level (empty/absent tools → 2048;
    non-empty tools → 8192; explicit tool_choice incl. `"none"` →
    8192);
  - tool-bearing + unset → upstream `max_tokens` **8192**;
    plain chat + unset → **2048**; empty `tools: []` → 2048;
    explicit `tool_choice` alone → 8192;
  - client-sent passthrough: tool-bearing + 4096 → 4096;
    plain chat + 8192 → 8192;
  - **clamp parity:** in a tight-context variant (the entry's
    `context_tokens` shrunk to 4096), tool-bearing + unset lands on
    exactly the budget tool-bearing + client-sent 8192 lands on,
    and it is < 8192 — the clamp applies identically on top of the
    new default;
  - completion-tokens shape: tool-bearing + unset pinned to
    `premium/gpt-5.5` writes `max_completion_tokens: 8192` and no
    `max_tokens` key;
  - **streaming parity:** stream=true tool-bearing + unset → 8192;
    stream=true plain chat + unset → 2048 (the budget logic lives
    in the shared body builder both paths call).
- `tests/test_paid_spillway.py` — the version-shape assertion
  updated 0.8.0 → 0.8.1 with the narrated comment, per that test's
  convention.
- **Full suite: 627 passed** (616 baseline + 11 new), locally; CI
  on the PR (recorded in the PR).

---

## 2. Behavior change, in plain terms

For any client that states its own `max_tokens`: **none.** For a
client that omits it: plain chat turns are byte-identical to
v0.8.0; tool-bearing turns now leave the gateway with an 8192
output budget instead of 2048. A tool call whose payload needs
more than 2048 tokens — the F-09 stall shape — can now complete
instead of truncating mid-stream. Costs: the budget is a ceiling;
a turn only spends it if it actually generates that much, and the
turns that do are precisely the ones that were failing.

## 3. Deploy gate (owner decision)

Standard gate, code-only: snapshot (`pre-v081-*`) + verified
backup, build image `sovereign-router:v0.8.1` on VM103, compose
override → v0.8.1, `docker compose up -d` (recreate), /health
(version 0.8.1, 81 models, four clients), smokes: an unpinned
hermes serve; a pinned dots-3 tool-shaped turn through the gateway
with `max_tokens` omitted whose tool payload exceeds 2048 tokens
(the C2 replica from §7 — it must now complete, finish=stop);
a plain-chat omitted-budget turn (route record unchanged in
shape). **Rollback:** override → v0.8.0 image + `compose up -d`;
no registry or DB rollback exists to perform (the v0.8.0 attempt
ledger is untouched by this change).

## 4. Complementary client fix — Hermes `max_tokens_cap` (VM112)

§7's fix proposal, second half: Hermes states its own budget
explicitly, so it is protected even if gateway defaults change
again. Ordered by the owner together with the gateway fix
(gateway first). Applied 2026-11-13 after this package was built:

**What the evidence actually showed (read before the diff):** the
knob the §7 proposal named — `max_tokens_cap` — does **not** govern
the main agent loop in this Hermes build. In the shipped code it
exists only in `tools/mcp_tool_sampling.py` (the per-MCP-server
`sampling:` block, default 4096): it caps MCP server-initiated
sampling requests, which already send an explicit `max_tokens`. The
main loop has no user-facing output cap at all — the `model:`
section's own documentation states it outright: *"Output-token
limits are provider-owned, not user configuration."* Setting the
sampling cap would have changed nothing about the stall. The
mechanism that **does** make Hermes state a budget on its main-loop
requests is the provider `extra_body` surface: a `providers:` /
`custom_providers:` entry matched by `base_url` has its
`extra_body` lifted into the runtime (`runtime_provider_custom.py`)
and merged into the agent's `request_overrides` at init
(`agent_init.py: _merge_custom_provider_extra_body`), from where
the transport merges it into every request body. That is the
surface used here; the deviation from the proposal's letter is
recorded deliberately, per the loop's no-silent-fixes rule.

**Change (VM112, both files backed up first as
`config.yaml.bak-20261113-f09budget`, hermes:hermes):** appended a
top-level block to `/opt/data/config.yaml` (default profile) and
`/opt/data/profiles/executive-ops/config.yaml` (same config
surface: bare `provider: custom`, same base_url — applied per the
brief):

```yaml
providers:
  sovereign-router:
    base_url: "http://192.168.0.166:6075/v1"
    extra_body:
      max_tokens: 8192
```

The entry is deliberately named (`sovereign-router`, not
`custom`): bare-`custom` named resolution never selects it, so
runtime resolution and credentials are untouched — only the
base_url-matched `extra_body` merge consumes it. broker-test (the
retired-router receptionist) and the voice profile were **not**
touched, per the brief. Both files parse under Hermes' own YAML
parser (ruamel) with the entry in place and the model route
unchanged.

**Verification (fresh processes; the production gateway still runs
v0.8.0, whose omitted-budget default is 2048 — so any serve above
2048 output tokens is only possible if the client stated a larger
budget):**

- Default profile, `hermes -z "What is 2+2?"` → `4`, exit 0
  (config loads, routing and auth intact).
- Default profile, the F-09 shape itself — a fresh `-z` turn
  asked to `write_file` 320 provided lines verbatim: the file
  landed **complete, 320/320 lines, sequential** (the §7 C2
  replica of this shape truncated at 108/320). The router's route
  record for the serving turn shows **5,239 output tokens**,
  `escalated: false`, no escalation reasons — the gateway did not
  raise the budget internally; Hermes stated it. (A second
  >2048 serve, 2,303 tokens, appears in the same window.)
- Executive-ops, `hermes -p executive-ops -z "What is 2+2?"` →
  `4`, exit 0 (identical config shape; the deep budget proof was
  run on the default profile).

**Fresh-session caveat (standing lesson, live-demonstrated):**
config is read when an agent is constructed. Sessions already
open — including the owner's desktop session, whose turns were
still landing at exactly 2048 output tokens in the same route-log
window — keep the old behavior until they are restarted /
reconstructed. Nothing was restarted in this step.
