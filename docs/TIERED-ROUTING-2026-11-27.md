# Tiered routing — Jobs B, C, D (2026-11-27)

Owner go: Jordan, 2026-11-27 20:25 MST — "Do B C D" (Jobs B, C and D of the plan in the Drive doc *GROK - Sovereign Router Classification, Tiers & Self-Correction - 2026-11-27*). Job E (agent-side hooks) is **not** approved. Job A (the v0.10.4 deploy) waits on the owner opening its PR.

Policy reading (owner, 2026-11-27 17:26 MST): "best" means best for the request type and the tools or capability it needs. Per task class: best free model for that class, then best cheap, then premium only when the request requires it. The sensitivity wall stays hard.

Owner defaults for this round: free coder = `unorouter/codestral-latest`, cheap coder = `nrouter/qwen3-coder-480b-a35b`; no image-generation models yet.

## Job B — v0.10.5 (branch `feat/v0.10.5-tool-error-depth-cap`)

| Change | Why | Where |
|---|---|---|
| Tool-error detector parses JSON; `{"error": null}` and similar success payloads are not errors | False positives fed `tool_error_next_turn`, the F2 sticky break and the v0.10.2 ledger | `gateway._tool_text_has_error` |
| Max 6 upstream calls per turn; the free pass hands to the paid spillway with 2 left | 72h census: fallback depths up to 21 on congested free pools | `gateway._DepthBudget`, `MAX_UPSTREAM_ATTEMPTS`, `SOVEREIGN_MAX_UPSTREAM_ATTEMPTS` |
| Premium is never reached because free/paid pools are congested | Owner: premium only when the request requires it | `build_spillway_candidates` (`premium_not_required_congestion`) |

Behaviour change to watch after deploy: a turn whose free and paid pools are all congested now gets a 429 (Retry-After 60) instead of a premium rescue.

Rollback: redeploy the previous image tag (see the deploy record for the job).
