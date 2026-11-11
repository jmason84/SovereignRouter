# Incident 2026-11-11 — dots-3 hidden reasoning empties Hermes agent turns

**Status:** FIXED live 2026-11-11 ~13:47 MST (registry edit +
`/admin/reload`, no restart). Hermes-side record: Hermes-agent
repo, KNOWN-ISSUES E-09 (+ E-01 root-cause update).

## Symptoms (Hermes, executive-ops profile)

Same Telegram session (`20261111_130401_95059474`) all day:

- Morning (~07:38–07:51 MST): malformed tool calls looping to the
  loop-guardrail halt (`memory` without `action`, `tool_call`
  without `calls`, a guessed deferred tool name) — logged at the
  time as a model-discipline problem (Hermes-agent E-01).
- 12:57 PM MST: the session completed a 21-row spreadsheet append
  cleanly.
- 1:11–1:18 PM MST: a research turn ran 32 API calls, then Hermes
  logged "Empty response (no content or reasoning)" on three
  retries and a 939-token "Reasoning-only response", ending the
  turn `empty_response_exhausted`. Jordan got: "⚠️ No reply: auto
  didn't produce a reply this time, even after retries."

## Root cause

The private-free lane holds exactly one model whose declared
context fits a large agent session: `openrouter/dots-3-note-
preview-free` (524,288; the Groq entries cap at 131,072,
north-mini-code at 262,144). Dots-3 is a *preview note-taking*
model, seeded deliberately as "the long-context free option"
(long-context quality 4, everything else flat 3) — and it is also
the model Hermes' compression auxiliary names, so it was
load-bearing twice over.

Once the session's prompts outgrew the other free models, lane
fit landed on dots-3 and stickiness kept it there. Router log
(`state/routes.jsonl`): 68 of 80 big-prompt hermes rows that
morning, and every row of the afternoon turn, `model=
openrouter/dots-3-note-preview-free, lane=private-free, lvl 4,
sticky=True`, inputs climbing 150k → 195k, all status 200,
`upstream_failure=False` — from the router's seat nothing was
failing. (Hermes logs the same calls as `upstream=AtlasCloud`,
OpenRouter's underlying host for this model.)

Dots-3's hidden reasoning then reproduced the north-mini failure
of 2026-11-04 exactly: on final answers it spent the entire
output budget on reasoning and returned zero content
(out=123/78/75 on the "empty" calls; out=939 reasoning-only).
Hermes' retries re-hit the same sticky model, so they could not
save the turn.

Contributing context: the session could only grow past 131k
after that morning's Hermes-side fix raised the exec-ops
profile's declared window from 131,072 to 524,288 (Hermes-agent
E-06). The lane gap was latent until sessions actually used the
room.

## Fix

The 2026-11-04 north-mini fix, mirrored: the dots-3 registry
entry's `extra_body` gained `"reasoning": {"enabled": false}`
(the entry already carried `provider.data_collection: deny`).
Edited in the live `state/registry.json` on VM103 (backup
`registry.json.bak-20261111-dots3-reasoning`) and activated with
`POST /admin/reload` — the gateway reloaded 15 models, all four
clients, no restart, no dropped turns. The entry's `notes` carry
the dated explanation, and `src/registry.json` in this repo is
mirrored to match.

## Verification

- Named-model probes ×2 through the live path (client `hermes`,
  reasoning-bait prompt, max_tokens 400): `finish_reason=stop`,
  content 520/591 chars, `reasoning_len 0`, correct worked answer.
- Production-shaped probe (`model=auto`, ~190k-token prompt, a
  tool offered): served by dots-3, `finish=stop`, 426 chars of
  clean formatted content, reasoning 0, in ~8s.
- Pre-fix comparison is the incident itself: same shapes
  returned empty/reasoning-only at 20:17–20:18 UTC.

## Follow-ups (open)

- Dots-3 remains a flat-3 tool user; sessions it carries will
  still show weaker tool discipline than DeepSeek-class models.
  Watch the Hermes E-01 rate now that reasoning is off.
- Router backlog: a sticky-fitness guard — break session
  stickiness when a model returns empty/reasoning-only finals,
  so a retry can land on a different model instead of re-hitting
  the same one three times.
- The compression auxiliary naming dots-3 is now safer (it
  answers directly), but any future preview model added as a
  "long-context free option" should get the reasoning probe
  BEFORE it can serve agent sessions.
