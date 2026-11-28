# PLAN ONLY: embeddings + rerank for the router (Bounded Job Card)

**Status: PLAN. Nothing in this document is built, deployed or approved.** Jordan approved a plan only (2026-11-27, 11:18 PM MT). Every build step below needs his separate go.
The card layout (id, objective, bounds, steps, acceptance, tests, cost, rollback, gates) is my reading of "Bounded Job Card format". There is no template in the repo. Say so if you want it reshaped.

Evidence grades: **V** = verified from the provider's public page or API, 2026-11-27. **R** = reported. **A** = assumed.

---

## JOB CARD: ROUTER-EMB-20261127

| Field | Value |
|---|---|
| Objective | Give the LAIW Article Engine and route-log search an embeddings + rerank service with a vendor failover chain, behind the router's keys and spend alerts. |
| Owner / approver | Jordan Mason approves each phase. |
| Timebox | Phase 0: 1 session. Phase 1: 1 session. Phase 2: 1 session. |
| Hard bounds | Ship as a separate service on VM103, not in the chat gateway's request path. No VM102/Hermes/n8n workflow edits without a separate go. No new signups: jina, voyage, deepinfra and huggingface keys already exist on VM103. |
| Spend cap | Phase 0: $0.05. Phases 1-2: under $1 total for the eval. Production cost: see below. |

### Why a separate class, not a chat route
The router today is chat-completions only. Embeddings and rerank have different request/response shapes, no tools or streaming, and need a model-consistency rule (an index built with model X must be queried with model X). Folding them into the chat registry would confuse quality classes, the tiering ranker and the spillway. They are a **sibling service** that reuses the router's pieces: key files, spend ledger, quota pools, the sensitive wall and telemetry.

### Provider options (all keys present on VM103: jina, voyage, deepinfra, huggingface)

| Role | Candidate | Price per 1M tokens | Notes |
|---|---|---|---|
| Embed (primary) | DeepInfra `Qwen/Qwen3-Embedding-8B` | **$0.01** (V, DeepInfra model list) | 32k ctx. Also available: 4B at $0.02, 0.6B at $0.01. |
| Embed (alt) | Voyage `voyage-4-lite` / `voyage-4` | $0.02 / $0.06; **first 200M tokens free per account** (V, Voyage pricing page) | Free allowance covers the whole Article Engine corpus at any plausible volume (A). |
| Embed (alt) | Jina `jina-embeddings-v5-text` (small 677M, nano 239M), 32k ctx on small | prepaid tokens (price table not captured; R) | Key exists. |
| Embed (cheap local-ish) | HF `hf-inference` `BAAI/bge-m3` | compute-time billed, CPU (V live mapping, price not captured) | Backup only. |
| Rerank (primary) | DeepInfra `Qwen/Qwen3-Reranker-4B` | **$0.025** (8B $0.05, 0.6B $0.01) (V) | |
| Rerank (alt) | Voyage `rerank-3-lite` / `rerank-3` | $0.02 / $0.05; first 200M free (V) | Token count is (query tokens x docs) + doc tokens. |
| Rerank (alt) | HF `BAAI/bge-reranker-v2-m3` via hf-inference | compute-time | Backup. |

**Data posture (A, pending a terms read in Phase 0):** embeddings of the Article Engine's drafts are non-sensitive; route-log text must be scrubbed to the router's sensitive wall first (embed only the classified, non-sensitive and redacted fields, never raw prompts). Voyage, Jina and DeepInfra retention terms must be read and recorded before any route-log content is sent. Until then, route-log search uses **only metadata fields** (class, model, outcome, error class), not prompt text.

### Design (small and boring)

**Service:** `src/embedding_service.py` (new, separate FastAPI app or router module on its own port, default OFF).
- `POST /v1/embeddings` (OpenAI-compatible) and `POST /v1/rerank` (Cohere/Voyage-style: `{query, documents, top_n}`).
- A small registry `src/embed_registry.json`: id, provider, model, kind (embed|rerank), dim, max_input_tokens, price per M, lane, enabled, `index_family` (which vectors are interchangeable), probe.
- Each request names an `index_family` (for example `laiw-articles-v1`). A family is pinned to ONE model for writes. Failover applies only to rerank, or to embeds against a family explicitly marked "multi-model with re-embed on switch". This avoids silently mixing vector spaces.
- Cost: per-request cost computed from provider usage and logged to the existing ledger (new `kind` field). Spend alert per provider.
- Sensitive wall: a request marked sensitive may only use entries whose lane is cleared (none today), so it returns 403 `no_cleared_embedder`. This mirrors the chat router's fail-closed behavior.

**Phase 0: evidence (no code in the router).** Run a tiny eval from VM103 with the existing keys: 30 real Article Engine snippets + 20 route-log metadata strings; measure recall@5 on a hand-labelled set for Qwen3-Embedding-8B vs voyage-4-lite vs jina v5-small; rerank-3-lite vs Qwen3-Reranker-4B on 20 queries. Cost estimate: under $0.05 (about 50k tokens embed + 100k tokens rerank = $0.002 at $0.01-0.05/M, A). Output: a table of recall, latency, cost, plus the retention terms read per provider.

**Phase 1: service + tests, OFF by default.** Implement, with unit tests only (fake upstreams), E2E in the existing harness (new section G), CHANGELOG, docs. No traffic. Deployable only after the v0.10.4-v0.10.7 PR chain merges (the live image is v0.10.2).

**Phase 2: wire consumers (each needs its own go).**
- LAIW Article Engine: n8n HTTP node to the service for dedup / related-article search. That is an n8n workflow edit, so it requires Jordan's explicit approval; the plan only names it.
- Route-log search: a read-only tool that embeds scorecard/route metadata nightly into a local sqlite (sqlite-vec or brute-force cosine; 50k rows fits in memory) and answers "find similar failures". Local only; nothing leaves the box except the text sent to the embedder.

### Cost estimate (A, to be replaced by Phase 0 measurements)

| Workload | Volume | Provider | Cost |
|---|---|---|---|
| Article Engine: embed each draft + a rerank pass | about 100 articles/month x 3k tokens = 0.3M tokens embed; 100 queries x 20 docs x 500 tokens = 1M rerank tokens | Qwen3-Embedding-8B $0.01 + Qwen3-Reranker-4B $0.025 | about $0.03/month |
| Same on Voyage | same | within free 200M tokens | $0 |
| Route-log search (metadata only) | 50k rows x 60 tokens = 3M tokens one-time, +0.2M/day | Qwen3-Embedding-8B | about $0.03 one-time, under $0.10/month |
| Worst case x100 | | | about $3-10/month |

Spend alert proposed at $2/month per provider for this class.

### Acceptance criteria
1. Phase 0 table exists with recall@5 >= 0.8 on the labelled set for the chosen embedder, and a chosen primary + backup per kind.
2. `POST /v1/embeddings` returns OpenAI-shaped vectors with the pinned `index_family` model; wrong-family queries return 409.
3. Rerank returns ordered `top_n` with scores; failover to the alt provider on 5xx/429 within 2 attempts.
4. Cost per request is recorded and matches provider usage x registry price within 1%.
5. Sensitive-marked requests fail closed (403) with a logged reason.
6. The chat gateway's behavior and test suite are unchanged (hash of `src/registry.json` and the gateway request path untouched).

### Tests (planned)
- Unit: registry validation (price provenance, dims, family pinning), request shapes, cost math, failover order, sensitive fail-closed, 409 on family mismatch, key never logged.
- E2E (harness section G): boot, embed -> store -> query round trip with a fake upstream, rerank ordering, failover, spend alert.
- Live smoke (Phase 0/1 only, capped at $0.05): one real embed and one real rerank per provider.
- CI: run on the branch manually, as for #22-#26.

### Rollback
- Service is a separate process/compose service, default OFF: rollback = `docker compose stop embeddings` and remove the compose override; the chat gateway is untouched. 
- No schema change to quota.db (new ledger rows carry a new `kind`; old readers ignore it). Feature flag env `SOVEREIGN_EMBED_SERVICE=0` disables the consumers.
- n8n consumers (Phase 2) are reverted by restoring the pre-change workflow version via n8n's workflow versions, taken before any edit.

### Risks
- Vector-space mixing (mitigated by index_family pinning).
- Provider retention for route-log text (mitigated: metadata only until terms are read).
- DeepInfra price drift (mitigated: the new HF/LiteLLM drift feeds plus the registry price provenance rules).
- Scope creep into reranking chat candidates for the router's own model selection (explicitly out of scope).

### Approval gates
- G0: approve Phase 0 eval (cost under $0.05).
- G1: approve Phase 1 build (code on a branch, no deploy).
- G2: approve each consumer wiring (Article Engine n8n edit; route-log tool).
- G3: approve deploy (after the PR chain merges).
