# Sovereign Router v0.5 — Classifier Sidecar

Difficulty classifier for the v0.5 gateway, per spec v0.5.3 ("Difficulty
classifier", Phase 2). The gateway calls `POST /classify` with a **1.5 s
timeout** and falls back to its own rules layer on timeout or error — this
sidecar improves level quality, it is never a single point of failure.

## Layers

| Layer | Where | Behaviour |
|---|---|---|
| 1. Rules | Canonical copy in the **gateway**; mirrored in `sidecar.py` for benchmarking | Clear cases only (<1 ms): greetings/formatting → L1, short read-only questions → L1/L2 (**an infra mention alone never raises the level** — infra terms count only with a change verb or a diagnosis verb), scope-word + action → L4, critical term + action/plan context → L5 |
| 2. Embedding nearest-match | `sidecar.py` | Last user message truncated to 1,500 chars, embedded, similarity-weighted majority vote over the closest **7** of `examples.jsonl` → level + task class + confidence. Adjacent near-ties at L3+ resolve to the higher level; low-confidence L2/L3 verdicts bump one level (spec uncertainty rule) |
| 3. Arch-Router-1.5B | llama.cpp server, HTTP stub in `sidecar.py` | **Disabled by default.** In-path only if the VM103 benchmark shows p95 < 1.2 s on 2 CPUs for ~400-token inputs; otherwise offline labeler via `POST /label` |

### Embedder backends (identical interface, `/health` reports the active one)

- **Preferred:** sentence-transformers, `BAAI/bge-small-en-v1.5` (~30M params,
  set `EMBED_MODEL` to swap in all-MiniLM-L6-v2). Target 20–50 ms CPU.
- **Fallback:** dependency-free TF-IDF + hashed n-gram embedding (pure stdlib).
  Runs anywhere, no downloads. Its benchmark numbers are a **floor** for the
  transformer path, not the target — do not judge Phase 2 acceptance on it
  alone; re-run `benchmark.py` with sentence-transformers installed.

## Files

- `sidecar.py` — the service (FastAPI if installed, else stdlib HTTP server; same routes)
- `examples.jsonl` — seed bank: **150** hand-written labeled examples
  (L1 30 / L2 34 / L3 34 / L4 30 / L5 22; task classes chat 34, infra 59,
  code 32, reasoning 19, long-context 6). Deliberately heavy on
  infra-mentioning *easy* examples ("is docker running?", "show me the
  proxmox backup status" → L2) — that is the population the old keyword
  rules over-classified
- `held_out.jsonl` — **80** held-out examples (L1 16 / L2 20 / L3 18 /
  L4 16 / L5 10), different phrasings, zero overlap with the bank
- `route_descriptions.json` — plain-English route descriptions for Arch-Router, from the spec difficulty table
- `benchmark.py` — Phase 2 acceptance runner; writes `benchmark-results.json` (served at `GET /benchmark`)
- `Dockerfile`, `requirements.txt`

## API

```
POST /classify {"text": "...", "client": "hermes"}
  -> {"level": 3, "task_class": "infra", "confidence": 0.71,
      "layer": "embedding", "latency_ms": 34.2}
GET  /health     -> embedder in use, bank size, arch-router state
GET  /benchmark  -> last benchmark.py results (stub object until first run)
POST /label      -> offline Arch-Router labeling (501 while disabled)
```

No prompt text is logged. Clients are informational only in v0.5.0 (client
policies live in the gateway).

## Run locally

```bash
python3 sidecar.py                 # stdlib server on :8080, zero deps
# or, with requirements installed:
uvicorn sidecar:app --host 0.0.0.0 --port 8080
python3 benchmark.py               # acceptance report + benchmark-results.json
```

## VM103 deployment notes

- One stack with the gateway on the internal network; sidecar gets **no host
  port** (gateway is the only caller). Limits: **3 GB RAM, 2 CPUs** (compose
  stanza in the Dockerfile header).
- VM103: 4-core Xeon E-2246G (AVX2, no AVX-512, no GPU), 16 GB RAM, ~14 GB
  free (checked 2026-10-31 per spec). bge-small CPU inference is fine;
  keep `--workers 1` so the model loads once.
- Pre-pull / bake the embedding model into the image or a named volume —
  first start otherwise downloads ~130 MB and `/health` lags.
- Snapshot VM103 in Proxmox before deploying (spec rollback section).

### Arch-Router via llama.cpp (Phase 2 benchmark step)

```bash
# Separate container/process in the same stack, sharing the 3 GB / 2 CPU budget:
llama-server \
  -m /models/arch-router-1.5b-q8.gguf \   # exact GGUF filename: confirm at download time
  --host 0.0.0.0 --port 8081 \
  -c 1024 -t 2 --threads-batch 2 \
  --alias arch-router-1.5b
# then: ARCH_ROUTER_ENABLED=1 LLAMA_URL=http://arch-router:8081
```

Q8 GGUF is ~1.7 GB on disk. Benchmark protocol before enabling in-path:
400-token inputs (last user message + `route_descriptions.json`), 2 CPUs,
report p50/p95 — **in-path only if p95 < 1.2 s**, else it stays an offline
labeler for the Phase 4 learning loop. Expectation from the spec is 1–3 s,
i.e. offline is the likely outcome; plan for it.

> **LICENSE VERDICT (2026-11-02, docs/LICENSE-REVIEW-arch-router.md): Arch-Router-1.5B is NOT SUITABLE** — its Katanemo Community License (DigitalOcean) bars commercial use without a separate license, with no internal-use exception. **Phase 2 uses the fallback: Qwen2.5-1.5B-Instruct (Apache-2.0)** via the same llama.cpp stub and route descriptions (or Qwen3-1.7B/4B, also Apache-2.0). **Do NOT use Qwen2.5-3B-Instruct — Research License, non-commercial only.** The stub below stays model-agnostic; only the GGUF changes. Original TODO text:
> Katanemo model. Review its license terms for self-hosted commercial use
> before pulling the GGUF onto VM103 (spec Phase 2 acceptance step, owner:
> Jordan). **Fallback if unsuitable:** a Qwen 1.5B–3B instruct GGUF
> (e.g. Qwen2.5-1.5B/3B-Instruct) behind the same llama.cpp endpoint and the
> same `route_descriptions.json` prompt — the stub client in `sidecar.py`
> does not care which model answers, only `LLAMA_URL` changes (pattern per
> alexrudloff/llmrouter, cited in the spec).
