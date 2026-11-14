# Release v0.8.3 — Decision Package: True-Cost Ledger

- **Status:** BUILT, not deployed. Branch `fix/v0.8.3-true-cost-ledger`
  in `~/workspace/sr-build6`, based on main `09579cdf` (v0.8.2 merge
  tree). No GitHub push (waits for the owner's egress-approval tap),
  no production contact beyond the read-only censuses and the
  throwaway-instance probe in §4.
- **Trigger:** the owner's order of 2026-11-13 (~17:48 MST):
  **"ADJUST THE LEDGER TO THE ACTUAL AMOUNT"** — issued after he
  challenged the new Grafana dashboard's premium MTD panel
  ("I'M 100% CONVINCED THIS IS INCORRECT"). The panel was faithfully
  summing the ledger; the ledger dollars were the phantom ones he had
  already disproved on 2026-11-11/12. **This order supersedes his
  2026-11-12 ruling** ("NOT NECESSARY — YOUR SOLVING A PROBLEM THAT
  ISNT AND WASN'T ACTUALLY A PROBLEM") that took the cache-pricing fix
  off the table. Failure-log entry **F-12**.
- **Scope, one line:** parse cached-token counts from every upstream
  usage shape on both the JSON and streaming paths, price them at
  each registry entry's verified cached-input rate, and add token
  totals + free-share-by-tokens to the soak report.

---

## 1. The defect (recap, proven 2026-11-12)

The gateway priced every input token at the full input rate. Route
rows carried `cached_tokens=0` on effectively every turn: the
non-streaming parser read only the OpenAI details field and the
Anthropic field (a trickle of production rows — 91k cached tokens
recorded across Nov 12 — came through that one shape), the streaming
parser never passed cached counts at all, and `_finalize_stream`
hard-coded `cached_tokens=0` in the usage event. Meanwhile agent
sessions resend 300–530k of context per turn at ~90%+ cache hits
billed by providers at ~1/10 price. Result, from the owner's own
console screenshots (2026-11-12): DeepSeek's 30-day actual was $9.34
against the ledger's $109.32 for V4 Pro on Nov 11 alone (~12×
overstatement); DeepInfra's console showed $7.19/mo against the
ledger's $18.75. Token volumes were always real — only dollars were
phantom.

## 2. What changed

**Gateway (`src/gateway.py`, VERSION 0.8.3).** One shared parser,
`_usage_counts` / `_cached_tokens_from_usage`, used by the JSON path
(`_usage_from_response`) and all streaming finalize sites:

| Shape | Fields | Providers it serves |
|---|---|---|
| OpenAI-style | `usage.prompt_tokens_details.cached_tokens` | OpenAI, OpenRouter (normalised), Groq, DeepInfra, SiliconFlow, Novita, Z.ai, Meta, Mistral, Cloudflare, HF router, Cerebras, NVIDIA, Ollama, Aion |
| DeepSeek | `usage.prompt_cache_hit_tokens` (complement: `prompt_cache_miss_tokens`) | DeepSeek direct |
| Anthropic aggregator | `usage.cache_read_input_tokens` (alongside the details field; the parser takes the MAX of recognised fields, never a sum — aggregators report the same tokens under both names) | Anthropic models via OpenRouter |
| Anthropic native | `input_tokens` **excludes** reads: reads + `cache_creation_input_tokens` fold into the input total so cached ⊆ input holds (creations price as fresh input — the registry carries no cache-write rate) | Anthropic-format endpoints |
| Google native | `usageMetadata.{promptTokenCount, candidatesTokenCount, cachedContentTokenCount}`, top-level or nested | Gemini pass-throughs (the OpenAI-compat endpoint's standard shape is also covered) |

Guarantees: cached is clamped to [0, input]; absent or malformed
usage yields cached=0 — byte-for-byte the old behaviour — and parsing
is wrapped so it can never break a response. The streaming path also
captures chunk-level `usageMetadata`. `stream_options.include_usage`
was already requested on streams (v0.5-era), so no request-shaping
change was needed anywhere.

**One pricing path.** `registry.ModelEntry.estimate_cost` already
implemented (input − cached) × input + cached × cached_rate + output
× output with the cached>input clamp and the full-rate fallback; the
gateway was simply never feeding it counts. A codebase sweep confirms
no second cost implementation exists (gateway call sites only;
governor/quota/telemetry consume the recorded `cost_usd`; the
exporter and soak report read route-row `cost_usd`).

**Registry (`src/registry.json`)** — §3. **Soak report
(`tools/soak_report.py`)** — per-scope token totals, free share by
tokens beside free share by requests, cached-token totals with the
pre-v0.8.3 caveat printed on the line. Census check against
production routes (America/Denver): **Nov 12 = 57,556,861 tokens,
free-lane 80.9% of tokens vs 74.4% of requests; Nov 13 = 84.1% of
tokens vs 70.4% of requests** (125,063,180 tokens at census time —
the day was still running; the share matches the owner's figures
exactly, and Nov 12 matches to the token).

**Tests: 636 → 654.** New `tests/test_true_cost_ledger.py` (parser
fixtures per shape, pricing math incl. clamp / absent rate / 0.0
rate, JSON + streaming e2e with canned provider usage asserting the
route row AND the quota usage event) and a cached-price battery in
`tests/test_registry_invariants.py` (0 ≤ cached ≤ input; cached
inherits the entry's provenance grading).

## 3. Per-provider cached-rate table (2026-11-13 primary-source reads)

**Set or re-verified in this build:**

| Entry | Cached $/1M | Action | Source |
|---|---|---|---|
| `premium/gpt-5.5` | null → **0.50** | added; whole set verified | OpenAI's GPT-5.5 model page: $5.00 / $0.50 / $30.00 — the carried "placeholder" was exact |
| `ollama/gpt-oss-120b` | null → **0.014** | added; seeded → verified | Ollama's pricing table: gpt-oss:120b $0.15 / $0.014 / $0.60 |
| `mistral/mistral-medium` | null → **0.15** | added | Mistral API price sheet, Medium 3.5 (cached = 10% of input; the 2026-11-12 sources read for this entry carried no cached rate) |
| `deepseek/deepseek-v4-pro` | 0.044 | confirmed; seeded → verified | DeepSeek pricing page (peak accounting; off-peak half) |
| `zai/glm-5.3` | 0.26 | confirmed; seeded → verified | Z.ai pricing page |
| `siliconflow/deepseek-v4-pro` | 0.135 | confirmed; seeded → verified | SiliconFlow models listing (entry stays disabled — deliberate duplicate) |
| `siliconflow/deepseek-v3.2` | 0.14 → **0.135** | **price correction**: in/out $0.259/$0.80 → **$0.27/$0.42** | SiliconFlow's own listing (F-04 pattern: the provider's own source supersedes the OpenRouter endpoint seed, which overstated output ~2×) |

**Already carried a cached rate; unchanged** (verified/observed in
prior passes): DeepInfra V4 Flash 0.018 / V4 Flash 0731 0.015 /
Qwen3.8-27B 0.05; SiliconFlow GLM-5.3 0.26 / V4 Flash 0.028; HF
DeepSeek V4 Flash 0.018 (observed pass-through); Meta Muse Spark
1.1/1.2/1.3 0.15; OpenRouter ×7 (0.01–0.17); Novita ×7 (0.01–0.11 —
field semantics cross-validated against Novita's API pricing in this
build: kimi-k2.5 0.1, glm-4.6 0.11, glm-4.7-flash 0.01 all match);
Z.ai GLM-4.6 0.11 / GLM-5.3-Flash 0.03; Mistral Small 0.015 /
Ministral 0.015; Groq 0.0 ×3 + Qwen3.6-27B 0.3; Mistral Large /
Codestral 0.0 (see follow-ups).

**No published cached rate — cached stays null** (cached tokens price
at the FULL input rate; never assumed free, never assumed discounted):

| Entry | Evidence |
|---|---|
| `cerebras/gpt-oss-120b`, `cerebras/qwen-3.8-27b` | Cerebras operates prompt caching (rate-limit concept) but publishes no cached-input *price*; independent catalog reads of Cerebras's own pages show cached "not published" |
| `deepinfra/gpt-oss-120b`, `deepinfra/llama-3.1-8b-turbo`, `deepinfra/mistral-small-24b` | DeepInfra's pricing page lists cached rates for the DeepSeek/Qwen families but none for these models ($0.037/—, $0.02/—, $0.05/—) |
| `novita/gpt-oss-120b` | Novita's API pricing for this model has prompt + completion only — no `input_cache_read` field (the field exists on other models, so the absence is a real no-rate signal) |
| `huggingface/glm-5.3-flash`, `huggingface/qwen3.8-27b` | HF's catalog publishes no cached rate for these pass-throughs. The :deepinfra backend publishes $0.03 cached for GLM-5.3-Flash direct — flagged as a price-truth-cycle candidate, not assumed (qwen3.8-27b also carries open F-06) |
| `siliconflow/qwen3-32b` | SiliconFlow's own listing for Qwen3-32B ($0.14 / $0.57) shows no cached rate |

**Flagged follow-ups for Cycle 1 aspect C (price-truth maintenance)
— recorded, NOT changed in this build:**

- `mistral/mistral-large` (0.0) and `mistral/codestral` (0.0) were
  recorded "verified" in a prior pass, but Mistral's currently
  published rule bills cached input at 10% of the standard input rate
  (which would imply $0.20 / $0.03). F-04 discipline: no repricing on
  an inferred rule — the per-model docs table needs a direct re-read
  first.
- Groq's three cached=0.0 plan prices: third-party trackers read
  Groq's docs as $0.075 cached for gpt-oss-120b. Free-lane accounting
  only; flagged for re-read.
- **DeepSeek peak accounting (pre-existing, deliberate):** the
  registry prices DeepSeek direct at PEAK rates; off-peak (weekday
  hours outside 01:00–04:00 / 06:00–10:00 UTC, and all weekend) bills
  exactly half. Post-v0.8.3 DeepSeek-direct ledger dollars are
  therefore the peak-basis figure — bounded at ≤2×, and the probe's
  true console deduction was exactly half its ledger figure (§4).
  Time-of-day pricing is a policy change for the owner to order, not
  a defect this build silently "fixes".
- `premium/gpt-5.5`: OpenAI prices >272K-input sessions at 2× input /
  1.5× output for the full session; not modelled (now noted in the
  entry). Very long premium turns can still understate.

## 4. Reconciliation probe (the proof)

Throwaway v0.8.3 gateway on VM103 (scratch container, port
127.0.0.1:6085, own temp state dir, the NEW registry, production
DeepSeek key copied to the temp state as a mode-600 file and shredded
after; production gateway/registry/state untouched — verified healthy
on v0.8.2 before and after). A repeated ~31.3k-token context, 6 turns
per arm, temperature 0:

| Arm | Result |
|---|---|
| A — direct to DeepSeek (context A) | Turn 0: hit 0 (cold). Turns 1–5: hit **31,232 / 31,295 = 99.8%**, miss 63. Warm-turn cost (peak basis) ~$0.0016 vs $0.0415 cold |
| B — via throwaway gateway, JSON (context B) | Identical provider numbers. routes.jsonl `cached_tokens` == provider-reported hit on **6/6 turns exactly**; ledger cost == hand-computed cost on 6/6 turns (to $10⁻⁶) |
| B2 — via throwaway gateway, streaming | **2/2 turns exact** on both counts and cost — the streaming parse path proven live |

Session totals (gateway arms): **ledger $0.053186 = hand-computed
$0.053186** at the registry's peak-rate basis; the actual off-peak
deduction (Saturday) is $0.026593. Contrast: at the pre-v0.8.3
formula the 7 warm gateway turns would have ledgered ≈ $0.29 instead
of $0.0117 (~25× on warm turns; the Nov 11 production day measured
~12× across its mixed traffic). The gateway instance and all copied
key material were destroyed after the run.

## 5. Honest history (cutover rule)

`state/routes.jsonl` is NOT rewritten: upstream cache data was never
stored for past turns, so the counts needed to recompute history do
not exist. **Dollars in route rows before the v0.8.3 deploy are
ceiling estimates** (one partial exception: JSON-path turns whose
provider reported the OpenAI details shape were already cache-priced
when reported — a small minority). **From the deploy timestamp
onward, dollars are actuals at registry rates**, with the DeepSeek
peak-accounting caveat of §3. Soak reports and dashboards must be
read with that date line; the soak report now prints the caveat on
its cached-token line.

## 6. Deploy & rollback notes (for the deploy gate)

- Standard gate: snapshot → sync registry (all three copies
  byte-identical, owned 10001:10001) → build image
  `sovereign-router:v0.8.3` → recreate gateway → /health (expect
  version 0.8.3) → smokes (pinned DeepSeek JSON + stream turns must
  show cached_tokens > 0 on the second turn of a repeated context).
- **Rollback = image swap only.** The registry changes are value-only
  (no new keys, no schema change); the v0.8.2 loader was verified in
  this build loading the new registry (81 entries). Under a rolled-
  back v0.8.2 the JSON path would still price OpenAI-details-shaped
  cache and everything else reverts to ceiling pricing — degraded
  accounting, never an error.
- Deploy must not race the Grafana exporter change freeze: the
  exporter consumes `cost_usd` only, so no exporter change is needed;
  its "estimated" naming becomes *more* accurate post-cutover for the
  DeepSeek peak-basis reason above.
