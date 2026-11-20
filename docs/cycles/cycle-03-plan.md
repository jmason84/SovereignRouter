# Cycle 03 — implementation plan: the borrow list

Date: 2026-11-19. Depends on PR #14 (F2/F3) and PR #15 (allowlist, tool health).
Survey: `docs/ROUTER-LANDSCAPE-2026-11-19.md`. This is a PLAN: no code, no
deploy, no registry or config change. Each item enters through the Improvement
Loop (plan -> build -> test -> deploy gate) separately.

## Principles
- Borrow patterns, not dependencies. No code from licenses that bar commercial use.
- Owner rulings bind: J-1 pins bounded; J-2 auto-demotion observe-only; J-4
  disabling proposal-only. Items touching enforcement are SHADOW first and
  ship enforcement only after an owner ruling.
- Free-first + sensitivity wall stay above any learned or borrowed logic.

## Workstreams (ordered by evidence value)

### W1 — Outcome-based tool scoring (from Switchyard "execution routing")
Replace the saturated one-shot tooling score as the ranking input with live
per-model tool-loop success from `tool_health.py`.
- Build: persist outcomes per (model, task_class); add Bayesian smoothing and
  a minimum-sample rule (reuse MIN_TURNS=50); expose in `/admin/tool-health`.
- Shadow: log `would_rank` using live rates next to the current rank in route
  signals; no selection change.
- Exit: 7 days of shadow data; compare shadow ranking vs actual outcome on
  turns served; owner decides enforcement (J-2).
- Risk: sticky attribution is exact but only covers sticky continuations;
  first turns of a loop are unmeasured. Mitigation: record first-turn model too.

### W2 — Whole-task success metric (Switchyard evaluation stance)
Per-turn cost misleads: a cheap model that needs 3 retries costs more.
- Build: define "loop outcome" (completed without tool-error break /
  escalation / client retry) per conversation key; ledger cost per completed loop.
- Exit: dashboard rows for cost-per-completed-loop by model and client.

### W3 — Verifier-gated cascade tuning (from NadirClaw, ideas only)
We already escalate on validation failure. Review cheap answer checks
(refusal, truncation, malformed JSON/tool args) against our `validation.py`
and add only the missing ones. No NadirClaw code (PolyForm Noncommercial).
- Exit: golden-set replay shows no new violations; truncated/bad-JSON tool
  calls caught before returning where they are not today.

### W4 — Learned per-key/provider limits and text-to-tool-call repair (from FreeLLMAPI)
- Learned limits: read provider rate-limit headers into the quota table so
  free-pool exhaustion is predicted, not discovered via 429.
- Repair: some free models emit tool calls as plain text; convert to
  `tool_calls` when unambiguous (strict schema match), otherwise treat as a
  bad tool call (existing `classify_truncated_tool_call` path).
- Exit: fewer `upstream_429` attempts per request; tests for repair edge cases
  (ambiguous text must NOT be converted).

### W5 — Cooldown and health audit (from LiteLLM/Bifrost patterns)
We have stream-abort cooldowns (v0.8.2) and pool demotion tracking (observe-only).
- Audit against: per-deployment cooldown on 429/5xx with TTL, half-open probing,
  failover diagnostics ("why was X skipped") already in skip reasons.
- Output: gap list; any enforcement goes to the J-2 review packet.

### W6 — Policy expression and observability (from agentgateway / Helicone)
- OpenTelemetry export of route rows (traces per request with skip reasons) so
  dashboards need no custom code.
- Evaluate expressing client policy (lanes, allowlists) declaratively; today it
  is code + clients.json. Design note only.

### W7 — Learned ranker in shadow (RouteLLM / semantic-router pattern), LAST
Only when W1/W2 yield labeled outcome volume. Shadow only, never above the
free-first and sensitivity constraints. Evaluate with a cost-vs-quality curve
(RouteLLM's methodology), not accuracy alone.

## Gateway bake-off (decision gate, not a build item)
Question: should LiteLLM or Bifrost sit underneath as transport?
- Set-up: replay the 180-item golden set plus one day of recorded Hermes
  traffic in shadow through a candidate with our policy expressed as config.
- Score: tool success, cost, policy violations (sensitivity, lane, floors),
  added latency, and what we could delete from our code.
- Pre-checks: LiteLLM license split and recent security advisories; Bifrost's
  open-vs-enterprise feature split; both versus our sensitivity wall.
- Decision rule: adopt only if policy violations = 0 and we can delete
  materially more code than the adapter adds. Otherwise stay.

## Explicitly not doing
Replacing the gateway wholesale; adopting RouteLLM (stale, no agent support);
NadirClaw code (license); TensorZero (archived); new-api/one-api (AGPL, different
problem); any hosted router for sensitive traffic.

## Sequencing
Deploy #14/#15 (owner go + VM103 snapshot) -> W1/W2 shadow (week 1) -> W3/W4
(week 2) -> W5/W6 audits -> bake-off -> W7 only if data supports.

## Addendum 2026-11-19 (deep research; see docs/RESEARCH-2026-11-19.md)
- **W8 — Failure-aware cost accounting:** charge full cost of failed/escalated
  loops to the routing choice that caused them; price cache loss on mid-loop model
  switches (TwinRouterBench).
- **W9 — kNN baseline:** evaluate a tuned kNN on our golden set before adding any
  learned router (Routing Plateau).
- **W10 — Step-level offline eval:** build a static replay set of real tool-loop
  prefixes with execution-verified labels from our own traffic.
- **Bake-off safety rule:** LiteLLM is shadow-only with NO production keys until a
  pinned, audited version and network isolation exist (2026 advisory record).
  Bifrost is the preferred transport candidate but its open-source feature edge must
  be verified against our needs (adaptive routing and several UI pages are enterprise).
- GUI work is planned separately in `cycle-04-gui-plan.md`.
