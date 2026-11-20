# LLM router landscape — GitHub survey, 2026-11-19

Method: GitHub search API for metadata (stars, last push, license, archived)
plus each repo's README. Star counts and feature claims are the projects'
own and were NOT independently benchmarked. Nothing here was run against
Sovereign Router traffic; that is the cycle-03 bake-off (see plan).

## Verdict in one line
No candidate encodes our policy (free-first lanes + spend fallback,
sensitivity wall, per-client lanes, J-1..J-4 rulings). Borrow patterns;
re-evaluate a gateway underneath only if the bake-off shows it wins.

## Candidates
| Project | Stars | Last push | License | What it is | Relevance |
|---|---|---|---|---|---|
| NVIDIA-NeMo/Switchyard | 3.7k | 2026-11-20 | Apache-2.0 | Embeddable Rust routing library (libsy). Routes per request from recent tool activity and outcome signals ("execution" routing), plus task/composite/escalation/advisor strategies. Pre-1.0; libsy Beta, server is a demo. | **Highest.** Closest to our tool-turn problem; stresses evaluating whole-agent success vs a single-model baseline. Borrow ideas; not a drop-in. |
| vllm-project/semantic-router | 6.5k | 2026-11-20 | Apache-2.0 | "Decision layer for models and compute": signal/decision routing, guardrails, semantic cache research. v0.4, early. Docs thin on specifics. | High for design ideas (signal -> decision separation); verify depth in docs before relying. |
| katanemo/plano | 7.1k | 2026-10-24 | Apache-2.0 (code) | Envoy-based proxy with preference routing via a 4B router model. | Medium. Router-model license not stated on the page; our own review rejected Arch-Router (Katanemo license). Re-check before any use. |
| maximhq/bifrost | 9.8k | 2026-11-20 | Apache-2.0 + enterprise tier | Go gateway: weighted key balancing, fallbacks, virtual-key budgets, semantic cache, MCP gateway. Perf claims (11-59 us overhead) are self-reported. | Medium-high as a transport layer candidate. Unclear which features are enterprise-only. |
| BerriAI/litellm | 63.6k | 2026-11-20 | Not detected by GitHub (LICENSE file; enterprise tier exists) | De-facto gateway: router with retries/fallbacks/load balancing, virtual keys, spend tracking. | High as transport candidate; confirm license split and recent security history first. |
| agentgateway/agentgateway | 6.3k | 2026-11-20 | Apache-2.0 | Linux Foundation MCP/A2A-aware proxy: LLM failover, budgets, CEL policy, OTel. | Medium. Strong policy/observability patterns (CEL rules, OTel). |
| NadirRouter/NadirClaw | 0.7k | 2026-11-20 | PolyForm Noncommercial | Embedding-classifier router (~10 ms), verifier-gated cascade (rule-based answer checks, escalate below 0.80), tool/agent signals force complex tier, 30-min session persistence. | Ideas only: we already have most of it. **License forbids commercial use**: do not adopt code. |
| tashfeenahmed/freellmapi | 55k | 2026-11-19 | MIT | Self-hosted aggregator of free tiers: per-(provider,model,key) RPM/RPD/token counters, cooldown on 429/5xx, strict failover within model groups, sticky 30 min, converts text tool calls to `tool_calls`. "Personal experimentation only"; no privacy wall. | Directly on our free-first path. Borrow: learned per-key limits, text-to-tool-call repair. Not for sensitive data. |
| lm-sys/RouteLLM | 5.7k | 2024-08-10 | Apache-2.0 | Strong/weak binary routers trained on Chatbot Arena preferences. No tool/agent support documented. **Stale (no push since Aug 2024).** | Low. Only the "evaluate routers by cost vs quality curve" methodology is worth borrowing. |
| tensorzero/tensorzero | 11.7k | 2026-06-11 | Apache-2.0 | LLMOps gateway + observability + optimization. **Archived.** | Do not adopt. |
| Helicone/helicone | 6.3k | 2026-11-17 | Apache-2.0 | Observability platform. | Patterns for per-model dashboards only. |
| Portkey-AI/gateway | 13.4k | 2026-11-20 | MIT | Gateway with guardrails, 1,600+ models. | Reference for guardrail/fallback config schema. |
| QuantumNous/new-api, songquanpeng/one-api | 52k / 37k | 2026-11-18 / 2026-01-09 | AGPL-3.0 / MIT | Key-management/distribution portals (account-resale oriented). | Not a fit (AGPL; different problem). |
| musistudio/claude-code-router | 38k | 2026-11-16 | MIT | Per-agent model routing for coding CLIs. | Not a fit (client-side). |
| aurelio-labs/semantic-router | 4.0k | 2026-11-09 | MIT | Embedding-based intent routing library. | Low; overlaps our classifier. |
| ENTERPILOT/GoModel, smg-project/smg | 1.3k / 0.7k | 2026-11 | MIT / Apache-2.0 | Smaller gateways (Go / Rust). | Watch; too small to depend on. |

## Takeaways
1. Switchyard's "route from recent tool outcomes and judge success at the whole-task level" is the same insight as our F2 and tool-health work, from a team with real agent evals.
2. FreeLLMAPI shows the free-tier layer is commoditizing; our differentiator is the policy and privacy wall, not the rotation.
3. License traps: NadirClaw (noncommercial), Arch-Router (reviewed, rejected), LiteLLM (confirm), new-api (AGPL).
4. RouteLLM is stale and not agent-aware; TensorZero is archived.
