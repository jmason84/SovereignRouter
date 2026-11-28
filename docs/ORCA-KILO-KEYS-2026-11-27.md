# OrcaRouter + Kilo: keys stored, models switched off (ROUTER-KEYS-20261127)

**Order.** Jordan, 2026-11-27 11:39 PM MT: "store both keys in the vault and VM103, add the models switched off, turn on OrcaRouter zero data retention, Kilo non-sensitive only."

**Scope.** This change touches only the registry and provider config. Gateway code paths are unchanged, VERSION stays 0.10.7, and nothing was deployed. Every OrcaRouter and Kilo entry is `enabled: false`.

## Keys

| Provider | VM103 path | Mode / owner | Verified |
|---|---|---|---|
| OrcaRouter | `/root/sovereign-router-v05/secrets/orcarouter.key` | 600, uid 10001 (gateway) | name, length 51, sha256 equal to the box copy |
| Kilo | `/root/sovereign-router-v05/secrets/kilo.key` | 600, uid 10001 | name, length 313, sha256 equal to the box copy |

- Convention: `deploy/deploy-runbook.md` puts provider keys in individual mode-600 files in `secrets/`, mounted read-only at `/app/state/keys`, and never in `.env` or compose. The gateway loads `keys/<provider>.key`. There is no env file, so no `*_API_KEY` variables were added.
- Backup taken first: `/root/sovereign-router-v05/secrets.grok-bak-20261127-keys/` (a `cp -a` of the 21 existing key files, mode 700).
- The values went from the box files straight to private temp files on VM103 (shredded after install). They were never printed or logged.
- Box copies: `/home/box/secrets/orcarouter.key`, `/home/box/secrets/kilo.key` (mode 600). The vault cannot hold API keys.
- **Pick-up:** the running gateway reads keys at start and on `POST /admin/reload` (admin key). Neither was done. No restart is needed while the entries are disabled. When Jordan enables one, the registry change already needs a reload or deploy, and that same reload loads the keys.

## Provider config

- `orcarouter` → `https://api.orcarouter.ai/v1`: the public `/v1/models` endpoint answered with 214 models, 6 of them free.
- `kilo` → `https://ai-gateway.kilo.ai/api/v1`: this is the base Kilo's docs name. `https://api.kilo.ai/api/openrouter` serves the same 464-model catalog.

## Entries (all disabled, all `public-only-free`)

| Entry | Upstream model | $0 test call 2026-11-27 | Status |
|---|---|---|---|
| orcarouter/deepseek-v4.1-flash-free | deepseek/deepseek-v4.1-flash-free | 429 `free_rate_limited` | NOT-READY |
| orcarouter/glm-5.3-flash-free | z-ai/glm-5.3-flash-free | 429 `free_rate_limited` | NOT-READY |
| orcarouter/deepseek-v4-flash-free (new) | deepseek/deepseek-v4-flash-free | 429 `free_rate_limited` | NOT-READY |
| kilo/beam-free | reflection/beam-free | 200, 1.2s, finish=stop, correct reply, cost 0 | READY-TO-ENABLE (non-sensitive) |
| kilo/auto-free | kilo-auto/free | not probed | disabled |

**OrcaRouter error body (verbatim, request id omitted):** "Free models are not available to this account yet. They require the workspace owner to link a GitHub account that has been registered for some time — a newly created GitHub account does not qualify yet. Link an established GitHub account in your profile settings, or add credits to use paid access." (`reason: err_free_access_denied`, `retryable: false`). Fixing this is an account step for Jordan. No credits were bought.

## Sensitive wall

- The non-sensitive marking is the `public-only-free` lane. The gateway skips public-lane entries for sensitive turns (`sensitive_public_lane`), and that includes explicit pins (`pin_blocked:sensitive_public_lane`). The paid spillway only considers paid and premium entries, so it can never relax this.
- `spillway_sensitive_excluded` is not set. The registry invariant reserves that flag for paid and private lanes, because on a public lane it would only add noise.
- **OrcaRouter ZDR is NOT on.** It requires a DPA signature that Jordan has not approved. OrcaRouter stays non-sensitive. Its entries may move to a sensitive-capable lane only after ZDR is confirmed on for this account and the free upstream's retention is known, and only by Jordan's ruling.
- Kilo stays non-sensitive by Jordan's order. `reflection/beam-free` is listed with `mayTrainOnYourPrompts: true`.
- Tests: `tests/test_v0108_orca_kilo.py` enables all five entries in a registry variant. With them on, sensitive turns, pinned or not, still never reach them, while a non-sensitive pin to `kilo/beam-free` serves.
