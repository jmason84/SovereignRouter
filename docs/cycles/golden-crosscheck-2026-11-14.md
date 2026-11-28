# Golden replay cross-check — v0.9.0 (R4) rebased onto v0.8.3

Date: 2026-11-14 (America/Denver). Owed since v0.8.2: the golden-replay
cross-check of the R4 branch against the v0.8.2/v0.8.3 release line.
Jordan approved the full R4 gate 2026-11-14 22:58 MST ("rebase, replay,
then deploy if clean").

## Method

- Rebased `feature/v0.9.0-quality-tooling` onto main @ 9c5290f (v0.8.3
  records). Registry merged field-wise (3-way, base a4640af): merged
  registry differs from main ONLY by R4's fields and from R4 ONLY by
  v0.8.3's pricing fields — both directions verified programmatically.
  Merged registry md5 `460b6df48f1d8217eb2c11622b70cb4f` (81 entries;
  54 tooling-measured, verified_until 2026-12-13; v0.8.3 cached pricing
  intact on all 7 entries).
- Full suite on the rebased tree: **681 passed** (654 pre-rebase + 27
  added by v0.8.2/v0.8.3).
- Replay ran against a throwaway v0.9.0 gateway on VM103 (probe
  container, merged registry, production classifier sidecar, shadow
  only — no upstream calls), diffed against the committed baseline
  `tests/golden/baseline-2026-11-13.json` (captured from production
  v0.8.1). A second capture was taken from CURRENT production v0.8.3
  shadow for attribution.

## Environment lesson (probe fidelity)

First probe run misclassified the two padded long-context items as
correctly routed: the fresh probe container could not download the
tiktoken o200k_base encoding (internal network only), so the gateway
fell back to chars/4 estimation (68,255 est. tokens) while production
estimates with real BPE (47,024). Attaching the probe to the proxy
network as well — the production gateway's own topology — restored
identical estimation and the F-10 behavior reproduced exactly. Any
future throwaway gateway MUST mirror production's network attach or
its token estimates (and therefore classification, levels and context
fit) are not comparable.

## Verdict

**0 NEW verifier violations · 5 known (unchanged) · 19 diffs, all
declared** (`tests/golden/declared-changes-2026-11-14.json`); replay
exit code 0 with the manifest.

- Known violations reproduce exactly: g07 (email->code), g08
  (press->infra), g15 (puzzle->infra), g17+g19 (F-10: sidecar fusion
  overrides the >60k rules signal — still OPEN, not fixed by v0.9.0).
- 16 diffs are candidate-ORDER only (first choice, class, level and
  membership unchanged). Attribution: the v0.9.0 candidate's shadow
  output is IDENTICAL to current production v0.8.3 on 21 of 24 items,
  including all 16 — these diffs belong to the v0.8.2/v0.8.3 line
  (v0.8.3 repricing of siliconflow/deepseek-v3.2 and the
  ollama/gpt-oss-120b cached rate reorders cost-ranked lists); the
  2026-11-13 baseline simply predates them.
- 3 diffs are R4's intended effect (g20/g21/g22, tool-bearing):
  first choice openrouter/nemotron-3-ultra-free ->
  cloudflare/llama-4-scout (battery tooling 5); candidate sets change
  as the tooling ranking applies and the supports_tools=false entries
  (novita/llama-3.1-8b, deepinfra/mistral-small-24b) leave tool lists.
  Production v0.8.3 keeps the old first choice on all three.
- v0.8.2's `cooldown_stream_abort` skip reason: no shadow diff
  attributable — no cooldown was active in either store during the
  captures, and all non-tool items match production exactly.

Capture of the v0.9.0 candidate's shadow decisions:
`tests/golden/capture-2026-11-14-v090.json` (candidate future
baseline; not swapped in by this release).
