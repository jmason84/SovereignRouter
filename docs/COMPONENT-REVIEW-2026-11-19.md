# Component review: what to keep, change, or drop — 2026-11-19

Basis: the repo at `feat/router-hardening-cycle2` (about 5,200 lines in `src/`),
this week's diagnosis, and `RESEARCH-2026-11-19.md`. Verdicts are judgments from
that evidence; none was load-tested against alternatives. "Adopt" means use an
external project; none is recommended today.

## KEEP — this is the product
| Component | Why keep | Evidence |
|---|---|---|
| Sensitivity wall (`sensitivity.py`, spillway-excluded flags, F-16/F-18) | No surveyed router has one. OpenRouter's own free tier has hosts that may train on prompts. | Research 1.4; no candidate offers an equivalent |
| Lanes + per-client policy (`clients.py`, incl. new allowlist) | Encodes your free-first/paid-fallback and client rulings; others offer budgets/keys, not lane semantics | Gateways surveyed expose virtual keys and weights only |
| Pool model + failure taxonomy (`pools.py`) | More precise than the cooldowns in FreeLLMAPI/LiteLLM: scope (model/pool/account/request) and observed reset times; caught the 2026-11-09 dead-account case | `pools.py` docstring; FreeLLMAPI cools per key on 429/5xx |
| Cost-ordered failover with free-first paid spillway | Core policy; Switchyard/RouteLLM do not do spend-ordered failover | Cycle-02 plan; ROUTING-PRINCIPLES |
| Sticky routing in tool loops (+F2 break on errors) | Same idea Switchyard calls execution routing; TwinRouterBench shows per-step switching can fail later and costs cache | Research 1.3, 1.6 |
| Validation + escalation (`validation.py`) | This is the verifier-gated cascade the literature favors (execution-verified labels) | Research 1.3 |
| True-cost / pool ledger, telemetry, `/v1/shadow` | Needed for failure-aware cost accounting; shadow is how every change gets tested | TwinRouterBench cost lessons |
| Registry with provenance, probe dates, `verified_until` | Free models rotate out within months; provenance keeps scores honest | Research 1.4 |
| Golden set (180 items) + Improvement Loop + J-rulings | The safety rail that lets us change routing without regressions | TwinRouterBench argues for execution-verified evaluation |
| Observe-only demotion tracker + `tool_health.py` | Evidence for the open J-2 ruling | This week's incident |

## CHANGE — keep the idea, replace the mechanism
| Component | Problem | Change |
|---|---|---|
| One-shot tooling battery as a ranking input | Saturated (60 of 85 entries scored 5); caused the Hermes tool failures | Already demoted to a ceiling (F3). Next: rank on live per-model tool-loop success (W1), keep battery only as an entry gate |
| `has_tool_error` last-3-messages heuristic | Double-counts one error | Retire from analysis; use `tool_health` (F-19) |
| TF-IDF/embedding sidecar for difficulty | Routing Plateau: a tuned kNN is as good as complex routers and query-only signals cap out | Keep as cheap baseline, stop investing; put effort into trajectory/outcome signals |
| Rules-layer difficulty classification | TwinRouterBench: rule-based routers had high row accuracy but failed trajectories | Keep for the sensitivity/long-context hard rules; do not trust it for quality tier without outcome feedback |
| `registry.json` / `clients.json` hand-edited files | 98 entries, easy to drift (stale notes on 15 entries, F-23) | Move to a versioned config store managed through the GUI (see cycle-04 plan), export to JSON for review |
| `repeat_within_60s` | Flagged tool loops as retries | Fixed in v0.9.3 candidate |
| Docs that describe old facts | Stale rollback note, F-10 mis-description | Corrected / flagged (F-21, F-22) |

## DROP or DO NOT ADOPT
| Item | Reason |
|---|---|
| RouteLLM as a dependency | Strong/weak pair routers trained on chat preferences; no tool-calling support documented; not a gateway |
| NadirClaw code | PolyForm Noncommercial license |
| Arch-Router / Plano router model | Previously reviewed as unsuitable (Katanemo license); Plano's router-model license not stated on its page |
| LiteLLM in front of production keys | 2026 record of critical proxy advisories and supply-chain incidents (Research 1.1). Shadow-only evaluation |
| TensorZero, open-model-prism | Archived upstream; ideas only |
| new-api / one-api / llmgateway.io | Different problem; AGPL for the first and third |
| Learned router before outcome data exists | Plateau paper: little gain; wait for W1/W2 volume |
| FreeLLMAPI as a service | "Personal experimentation only", no privacy wall; borrow limit-learning and tool-call repair only |

## Net
Keep roughly all of the policy layer and the evaluation discipline (that is the
differentiator). Replace the weak signals (saturated battery, error heuristic,
query-only difficulty). Add a management plane so the config is not a pile of
hand-edited JSON. Revisit a transport-layer gateway only after the bake-off, and
only a hardened, pinned one.
