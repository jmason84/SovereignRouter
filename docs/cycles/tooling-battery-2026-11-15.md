# Tooling battery — completion run, 2026-11-15

Ordered by Jordan 2026-11-15 02:15 MST as part of closing the
v0.9.0 testing gaps: measure every currently-unmeasured ENABLED
registry entry (the 2026-11-13 run left 19 reachability-blocked;
the registry has since grown to 88 entries / 72 enabled, of which
17 enabled entries had no `quality.tooling`).

## Run shape

- `tools/tooling_battery.py` against the **live v0.9.0 gateway**,
  run inside the gateway container (nginx allowlist constraint, as
  every prior battery), battery key = the openclaw client key,
  `--provider-pace openrouter=4`, `--pace 2`. One runtime restart
  interrupted the monitoring session mid-run; the battery process
  itself survived in the container and completed — its incremental
  report is the evidence (no re-run, no double-measurement).
- Window: ~02:46–03:15 MST (09:46–10:15 UTC), 2026-11-15.
- **Result: 17 targeted · 11 measured · 0 served-but-unmeasured ·
  6 never served.** Measured coverage is now 66 of 72 enabled
  entries (was 55).

## Newly measured (scores derive from battery output only)

| entry | tooling | composite | T1 | T2 | T3 | T4 | T5 | env | esc |
|---|---|---|---|---|---|---|---|---|---|
| openrouter/north-mini-code-free | 4 | 0.81 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.86 | 1 |
| openrouter/dots-3-note-preview-free | 4 | 0.64 | 1.00 | 0.00 | 1.00 | 0.00 | 1.00 | 0.86 | 1 |
| openrouter/nemotron-3.5-lightning-free | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| openrouter/nemotron-3-nano-omni-free | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| openrouter/flint-free | 4 | 0.64 | 1.00 | 0.00 | 1.00 | 0.00 | 1.00 | 0.86 | 1 |
| openrouter/lfm-3-1.7b-free | 5 | 1.00 | — | — | 1.00 | 1.00 | — | 1.00 | 0 |
| openrouter/nemotron-3-ultra-free | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| openrouter/nemotron-3.5-super-vl-free | 4 | 0.83 | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 0 |
| openrouter/free-router | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| aion/aion-3.5-mini | 3 | 0.60 | 0.00 | 0.00 | 1.00 | 1.00 | — | 1.00 | 0 |
| aion/aion-2.0 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |

Notes: north-mini-code's T2 (large-payload) zero matches its known
Article Engine failure shape; dots-3's T4 zero likewise. lfm-3-1.7b
scored 5 on the three components it could serve (T3/T4/envelope) —
the battery's ≥3-component rule measures it; its chat/reasoning
floor seeds (1) are untouched, so it still cannot win non-tool
classes. The Aion pair measured cleanly tonight — the 2026-11-15
alert's cap incident was a single 67k-token request against the
20k/day account cap, not a reachability block for battery-sized
tasks.

## Still unmeasured — per-entry reasons (route-log evidence)

Every task for these entries came back `no_evidence` (the pin never
served). Skip reasons below are counted from `routes.jsonl` over
the battery window for the pin attempts (5 per entry):

| entry | reason |
|---|---|
| ollama/gpt-oss-120b | `upstream_402` ×5 — Ollama answered every pin attempt with HTTP 402 (account/quota wall on the paid route). |
| cloudflare/gpt-oss-120b | `upstream_429` ×5 — the shared Cloudflare neuron pool (10k/day) was rate-limiting at pin time. |
| cloudflare/glm-4.7-flash | `upstream_429` ×5 — same shared-pool 429. |
| cloudflare/mistral-small-3.1 | `upstream_429` ×5 — same shared-pool 429. |
| deepinfra/mistral-small-24b | structural — `supports_tools: false` (corrected 2026-11-13): tool-bearing pins can never select it (`tools_unsupported`); forced pins returned `upstream_400`. Unmeasurable by design. |
| novita/llama-3.1-8b | structural — same as above (`supports_tools: false`). |

The Cloudflare trio stays a pool-timing problem, not a route
problem: the pool was already hot in the small hours (production
traffic draws the same 10k neurons). A future battery run in a
verified-cold pool window is the remaining path for those three;
Ollama needs its account state resolved first. All six stay
unmeasured and rank by the classified-class fallback, by design.

## Registry application

- Snapshot first: `pre-battery-scores-20261115` on VM103
  (build-src + state + IMAGE.txt; state registry md5 verified
  `03df1665…` before and inside the snapshot).
- Applied ONLY the battery's emitted patch, placed per the
  registry's own convention (established by the ERNIE/Ling/Particle
  write earlier tonight): `quality.tooling` inside `quality`;
  `tooling_source` (`tooling-battery:2026-11-15`) and
  `verified_until` (`2026-12-15`) at entry level. Programmatic diff
  vs the pre-write registry: exactly those fields on exactly those
  11 entries, nothing else. (First application attempt put all
  three fields inside `quality` — the v0.9.0 loader rejected it,
  `unknown task class 'tooling_source'`; corrected before any push.
  The loader doing its job is part of the record.)
- Validation: v0.9.0 loader + full suite **701 passed, run twice**,
  after recalibrating three escalation tests whose premises the new
  rankings legitimately moved (see below).
- New registry md5: `bf40db3e7fa02abfa2aae12bc655c81d`.

## Test recalibrations shipped with this write (and why)

The new tooling scores change tool-turn rankings — that is their
purpose — and three tests were calibrated to the old first picks:

- `test_gateway.py::test_bad_tool_call_escalates` and
  `::test_unknown_tool_escalates`: the fake upstream emits bad tool
  calls only for its scripted FREE_MODELS set; auto-selection's
  first pick for a tool turn is no longer in that set, so no bad
  call occurred and nothing escalated. The first server is now
  pinned (`groq/gpt-oss-120b`, the same device the sibling test
  above them already used); the property under test — bad call in,
  escalation out — is unchanged.
- `test_sticky_streaming.py::test_streaming_bad_tool_call_escalates`:
  same pin; the escalation landing moved from
  `deepinfra/llama-3.1-8b-turbo` ($0.02/$0.04) to
  `openrouter/free-router` ($0, tooling 5 measured today) — the
  v0.9.0 escalation filter reads measured tooling, and free-router
  is now the cheapest qualifying route. Assertion updated with the
  dated note in-file.
