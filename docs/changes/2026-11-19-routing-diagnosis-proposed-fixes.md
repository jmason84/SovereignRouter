# 2026-11-19: Routing diagnosis for tool-heavy clients, with PROPOSED fixes (not applied)

| | |
|---|---|
| **Status** | **PROPOSED, NOT APPLIED.** Diagnosis was read-only: no registry edit, no reload, no restart, no config write. Each fix below needs Jordan's explicit go. |
| **Prepared by** | Grok Foreman (executor), 2026-11-19, at Jordan's request (3:39 PM MT) |
| **Applies to** | Gateway v0.9.1 (the only router running; legacy v0.2.1 retired 2026-11-15) |
| **Full record** | Private Drive: "GROK - Sovereign Router Routing Diagnosis - 2026-11-19". It has the evidence tables, the replay method and the per-model numbers. |

> This repo is public, so the note leaves out hosts, IPs, paths, client keys and prompt content on purpose.

## Finding (plain words)

For tool-bearing requests, which is almost all agent traffic, `auto` doesn't really select on capability:

1. **Tool ranking uses only the measured `tooling` score, and it's saturated.** See `_selection_quality` in `src/gateway.py`. 60 of 85 enabled entries score 5; the one-shot pass/fail battery even scores a 1.7B model 5. A free model that fails roughly a third of real tool turns ties with strong models.
2. **The sort is cost first:** `(estimated_cost, -quality, latency_p50)` in `build_candidates`. Any free entry above the floor beats any paid one, and ties are broken by a static latency figure. The paid spillway runs only when free is exhausted.
3. **Stickiness locks a session to its first pick.** The key is client + first user message + system prompt, with a 30-minute sliding TTL, and tool errors are explicitly not a reason to move.
4. **`tool_error_next_turn` is logged but never fed back** into selection or scores.
5. **Secondary: the classifier reads only the last user message.** For agent clients, the size thresholds (20k / 60k tokens) dominate, because the fixed tool-schema and system overhead alone is near 20k tokens. So level means prompt size, not difficulty.
6. **Hygiene: registry entries whose notes say "disabled candidate" are `enabled`.** One of them, with unknown provenance, began winning agent turns after the 2026-11-19 rescore.

An offline replay of about 800 recent tool-bearing turns checked the selection reconstruction: it matched the live pick in 61 of 62 non-sticky cases. In that replay:
- A registry-only blocklist just moves traffic to the next saturated free model.
- Ranking tool turns on `min(tooling, quality_for(task_class))`, or a per-client allowlist, moves traffic to stronger free models at about $0 extra.

## PROPOSED fixes (smallest first; none applied)

| ID | Change | Tier | Backup / rollback |
|---|---|---|---|
| F1 | Set `enabled: false` on the entries whose notes say "disabled candidate" | Registry data, hot reload | Copy registry before; restore + reload |
| F2 | Break stickiness on a tool-error turn or a validation failure, and skip that model for the conversation's TTL | Small code change near the sticky check in `chat_completions` | Redeploy previous image tag |
| F3 | Tool turns rank and floor on `min(tooling, quality_for(task_class))` instead of `tooling` alone | About 3 lines in `_selection_quality` | Redeploy previous image tag |
| F4 | Per-client `model_allowlist` in `ClientPolicy`, plus one skip in `build_candidates` | Small code + clients data | Redeploy previous tag; restore clients file |
| F5 | Feed production `tool_error_next_turn` into scores: cap tooling for entries with >25% over ≥50 turns; multi-sample battery; parse tool JSON instead of a substring match | Tooling / ops | Restore registry snapshot |
| F6 | Classifier: leave fixed tool/system overhead out of the size signal; treat "tools present" as at least L2 | Code | Redeploy previous tag |
| F7 | Docs: drop the stale "v0.2.1 must stay running" comment in the compose file | Docs | n/a |

## Acceptance tests (for any fix)

1. **Offline replay** of the last 48 h of tool-bearing turns, before vs after. Record the model distribution and the estimated cost.
2. **Optional:** `/v1/shadow` with a synthetic agent-shaped request (no real content).
3. **24 h live:**
   - agent next-turn tool-error rate under 10% (baseline about 30%)
   - 0 picks of disabled or excluded entries
   - the scheduled triage run completes cleanly
   - client cost within its daily target
   - voice pin unaffected
