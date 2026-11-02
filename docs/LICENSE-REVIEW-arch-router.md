# License Review — Katanemo Arch-Router-1.5B (Sovereign Router v0.5, Decision #6)

**Date:** 2026-11-01 · **Reviewer:** Muse (research pass) · **Status:** COMPLETE
**Decision under review (spec v0.5.3, Decision #6):** accept the Arch-Router (Katanemo) license, or use the Qwen 1.5B–3B instruct fallback. Jordan accepted the proposed default on 2026-11-01: *review the license first; if unsuitable, fall back to Qwen.*

## Verdict

**Arch-Router-1.5B: NOT SUITABLE** for Sovereign Router v0.5 as licensed today — not without a separate written commercial license from DigitalOcean, LLC.
**Fallback: CONFIRMED, with one correction** — use **Qwen2.5-1.5B-Instruct (Apache-2.0, verified)** as the drop-in classifier, or **Qwen3-1.7B / Qwen3-4B (Apache-2.0, verified)** for more capability. **Do NOT use Qwen2.5-3B-Instruct** — despite widespread "Qwen2.5 = Apache" claims, its LICENSE file is the **Qwen Research License Agreement (non-commercial only)**.

This is a license-terms review, not legal advice.

---

## 1. What license is Arch-Router-1.5B actually released under?

A **custom, proprietary community license** — not Apache-2.0, not MIT, not any OSI license.

- The Hugging Face model card's License section says only: *"Katanemo Arch-Router model is distributed under the [Katanemo license]."*
  Source: https://huggingface.co/katanemo/Arch-Router-1.5B
- The repo's LICENSE file (main branch, **Version Release Date: April 2, 2026**) is titled **"KATANEMO COMMUNITY LICENSE AGREEMENT"**, *"based on the Llama 3.2 Community License Agreement … adapted for terms specific to the distribution and use of proprietary Katanemo Materials … provided by DigitalOcean, LLC."* The licensor is now **DigitalOcean, LLC** (the license header and definitions name DigitalOcean throughout; a third-party research note reports Katanemo Labs was acquired by DigitalOcean in April 2026, consistent with the license's April 2026 date and change of licensor).
  Source: https://huggingface.co/katanemo/Arch-Router-1.5B/blob/main/LICENSE

The license is explicit that the materials are **proprietary**:

> "e. 'Katanemo Materials': Collectively, Katanemo's proprietary models (including Katanemo Models) and Documentation."

Note: the Arch gateway *software* (katanemo/archgw, now "Plano") is reported Apache-2.0 — that covers the proxy code, **not** the model weights. The weights are what Sovereign Router would ship, and they carry the license above.

## 2. Does it permit Jordan's uses?

### The decisive clause — §4, Additional Commercial Terms

> "If you use, reproduce, modify, distribute, and create derivative works from the Katanemo Materials **for commercial purposes, you must obtain a separate commercial license from DigitalOcean**, which DigitalOcean may grant to you in its sole discretion, **and you are not authorized to exercise any of the rights under this Agreement unless or until DigitalOcean otherwise expressly grants you such rights**."

Applied to the three uses in question:

| Use | Permitted? |
|---|---|
| **Commercial use** | **No — not under this license.** Any commercial-purpose use requires a separate commercial license from DigitalOcean, granted at its sole discretion. There is no fee schedule, no self-serve grant, and no scale threshold (see §3 below). |
| **Self-hosting in a private homelab router serving internal agents** | **Not cleanly.** Self-hosting is only authorized within the §3 grant, and §3 is expressly "Subject to Section 4." Sovereign Router is not a hobby experiment: it is production infrastructure for Launch Collective, routing work for OpenClaw, Hermes, and n8n in a commercial business's operations. Use "for commercial purposes" is not limited to reselling the model; embedding it as a working component of a business's infrastructure is commercial use in the ordinary sense. The only clearly covered use is genuinely non-commercial/personal — Jordan's deployment does not fit that reading. |
| **Redistribution inside a Docker image** | **No for this deployment.** Pushing an image containing the weights to the CT100 registry and deploying it is distribution/making-available of the Katanemo Materials. That triggers §3.b duties (below) *and* the §4 commercial gate. A GGUF quantization (e.g. katanemo/Arch-Router-1.5B.gguf) is the same Materials in another format — format conversion does not change the license. (The .gguf repo's LICENSE was not separately pulled in this review; it is the same licensor and materials.) |

### The §3 grant (what non-commercial licensees get)

> "Subject to Section 4, you are granted a non-exclusive, worldwide, non-transferable, and royalty-free license … to: Use, reproduce, distribute, copy, creative derivative works of, and make modifications to the Katanemo Materials, and Create derivative works based on the Katanemo Materials."

### Corroboration: the predecessor Katanemo template was worse for this exact use

The earlier Katanemo Labs Community License (v. April 11, 2025, read from the sibling repo katanemo/Arch-Function-Chat-1.5B) allowed commercial use **except** the one thing Sovereign Router is:

> "You may use, reproduce, modify, distribute, and create derivative works from the Katanemo Materials for any purpose, including commercial use, EXCEPT in the following cases: **You may NOT package, distribute, or make available the Katanemo Materials as part of: A framework, A proxy server, Middleware, A gateway infrastructure product, Or any product substantially similar in function or purpose to the above, unless you obtain a separate commercial license from Katanemo Labs.**"
> Source: https://huggingface.co/katanemo/Arch-Function-1.5B/blob/0f254e8231da17472caa6fb30a92dbf34be8fa24/LICENSE (same license family, §4)

Caveat: this review pulled the current Arch-Router LICENSE at `main`, not the repo's full revision history. The sibling-model license is offered as corroboration of the licensor's consistent position — under **either** version, a gateway/router product is the carved-out case. The current version doesn't need the carve-out: it gates *all* commercial use.

## 3. Restrictions inventory (current license, April 2, 2026)

- **Commercial use:** barred without a separate DigitalOcean commercial license (§4, quoted above).
- **MAU / revenue thresholds:** **none.** Unlike the Llama Community License (700M MAU) or the Tongyi Qianwen License (100M MAU), §4 contains no scale-based safe harbor — the gate applies from the first commercial use.
- **Non-compete:** no express proxy/gateway non-compete in the *current* text (the predecessor template had one — see §2). §4's blanket commercial gate makes it moot for Jordan.
- **Research-only?** No — non-commercial use is granted (§3). The restriction is commercial, not research-only.
- **Attribution / branding (on any distribution, §3.b):**
  - Provide a copy of the license with the materials.
  - *"prominently display 'Built with DigitalOcean' on a related website, user interface, blogpost, about page, or product documentation."*
  - If the materials or their outputs are used to create/train/improve an AI model that is distributed, that model's name must begin with "DigitalOcean".
  - Retain in a "Notice" text file: *"Katanemo Models are licensed under the DigitalOcean Community License, Copyright 2026 DigitalOcean, LLC. All Rights Reserved."*
- **Acceptable Use Policy:** use must *"adhere to the Acceptable Use Policy for the Katanemo Materials (available at https://www.digitalocean.com/legal/acceptable-use-policy), which is hereby incorporated by reference"* (§3.b.iv).
- **Trademarks:** none granted, except a limited license to use the "Katanemo" mark solely as required for the attribution above (§7.a).
- **Termination:** DigitalOcean may terminate on any breach; on termination the licensee must *"delete and cease use of the Katanemo Materials"* (§8).
- **Litigation clause:** suing DigitalOcean (or any entity) alleging the materials/outputs infringe your IP terminates the license (§7.c).
- **Governing law:** Colorado, USA; exclusive jurisdiction Denver County, Colorado (§9).

## 4. Verdict for Jordan's use

**NOT SUITABLE.** Jordan is a single operator and the router is internal, not resold — but neither fact cures §4:

1. The deployment serves a commercial business (Launch Collective) and its client-facing agent stack. That is use "for commercial purposes," for which the license states the licensee is **"not authorized to exercise any of the rights under this Agreement"** until DigitalOcean grants a separate commercial license.
2. There is no de-minimis, internal-use, or small-operator exception and no MAU/revenue threshold to fall under.
3. The licensor's predecessor terms named this exact product category — proxy server / gateway infrastructure — as requiring a separate license, so there is no plausible "they didn't mean routers" reading.
4. Shipping the weights in the classifier image also imports the "Built with DigitalOcean" display duty and Notice requirements into a private stack, for no benefit over the fallback.

**Only cure:** a written commercial license from DigitalOcean covering this use. Not pursued — the fallback is license-clean and technically equivalent for a route-description classifier (the spec already designs for it: *"a Qwen 1.5B to 3B instruct model with the same prompt, as in alexrudloff/llmrouter"*).

## 5. Qwen fallback — license status per size (verified from LICENSE files)

Qwen licensing is **per-checkpoint, not per-family.** All three files below were read verbatim in this review.

| Model | License | Commercial use | Source |
|---|---|---|---|
| **Qwen/Qwen2.5-1.5B-Instruct** | **Apache License 2.0** (LICENSE file is the verbatim Apache-2.0 text, Copyright 2024 Alibaba Cloud) | ✅ Yes — use, self-host, modify, redistribute (incl. in a Docker image) with license/NOTICE retention | https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct/blob/main/LICENSE |
| **Qwen/Qwen2.5-3B-Instruct** | **Qwen RESEARCH LICENSE AGREEMENT** (Release Date: September 19, 2024) | ❌ **No** — see quotes below | https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE |
| **Qwen/Qwen3-4B** (and Qwen3-1.7B) | **Apache License 2.0** (LICENSE file is the verbatim Apache-2.0 text; the whole Qwen3 open-weight family shipped under Apache-2.0) | ✅ Yes | https://huggingface.co/Qwen/Qwen3-4B/blob/main/LICENSE |

### Why the 3B in the "Qwen 1.5B–3B" fallback range fails

Qwen2.5-3B-Instruct's own LICENSE file:

> §1.i — *"'Non-Commercial' shall mean for research or evaluation purposes only."*
> §2.a — *"You are granted a non-exclusive, worldwide, non-transferable and royalty-free limited license … to use, reproduce, distribute, copy, create derivative works of, and make modifications to the Materials **FOR NON-COMMERCIAL PURPOSES ONLY**."*
> §2.b — *"If you are commercially using the Materials, you shall request a license from us."*

This matches Qwen's own model-card statement (reproduced on the official Ollama library pages): *"all models except the 3B and 72B are released under the Apache 2.0 license, while the 3B and 72B models are under the Qwen license"* — and the 3B's "Qwen license" is specifically the Research License. (The 72B's is the different Tongyi Qianwen License, with its 100M-MAU trigger.) An independent 2026 audit that read the license files of the 300 most-downloaded HF text models reached the same conclusion: *"Qwen2.5-3B-Instruct is research-only and non-commercial, while the 0.5B, 1.5B, 7B, 14B and 32B in the same family are Apache-2.0."* (https://reptile.haus/journal/hugging-face-model-licence-audit-300-models-2026/)

Many secondary blogs state "Qwen2.5-3B-Instruct is Apache-2.0." They are wrong against the primary file. Do not rely on them.

### Fallback ruling

- **Adopt Qwen2.5-1.5B-Instruct** as the classifier-sidecar model: same 1.5B class as Arch-Router, clean Apache-2.0, drop-in for the route-description prompt design. Apache-2.0 duties are minimal: include the license (and NOTICE if present) in the image; no branding display, no use restrictions, no thresholds.
- **If more classifier headroom is wanted, benchmark Qwen3-1.7B or Qwen3-4B** (both Apache-2.0) — **not** Qwen2.5-3B-Instruct.
- The Phase 2 gate is otherwise unchanged: the model runs in the request path only if the VM103 benchmark shows p95 < 1.2s, otherwise offline labeling.

---

## Sources

1. Arch-Router model card (license statement): https://huggingface.co/katanemo/Arch-Router-1.5B
2. Arch-Router LICENSE, main, v. April 2, 2026 (Katanemo Community License Agreement / DigitalOcean): https://huggingface.co/katanemo/Arch-Router-1.5B/blob/main/LICENSE
3. Katanemo Labs Community License, v. April 11, 2025 (§4 gateway/proxy carve-out): https://huggingface.co/katanemo/Arch-Function-1.5B/blob/0f254e8231da17472caa6fb30a92dbf34be8fa24/LICENSE
4. Qwen2.5-1.5B-Instruct LICENSE (Apache-2.0): https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct/blob/main/LICENSE
5. Qwen2.5-3B-Instruct LICENSE (Qwen Research License Agreement): https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE
6. Qwen3-4B LICENSE (Apache-2.0): https://huggingface.co/Qwen/Qwen3-4B/blob/main/LICENSE
7. Qwen model-card license summary (via official Ollama library listing): https://ollama.com/library/qwen2.5:1.5b-instruct-q3_K_S
8. Independent HF license-file audit (corroboration on Qwen2.5-3B): https://reptile.haus/journal/hugging-face-model-licence-audit-300-models-2026/

**Effect on DECISIONS.md #6:** the accepted default's condition is met — the license was reviewed and is unsuitable → **the Qwen fallback is in force**, narrowed to Qwen2.5-1.5B-Instruct / Qwen3-1.7B / Qwen3-4B (Apache-2.0). Arch-Router-1.5B does not ship in Phase 2.
