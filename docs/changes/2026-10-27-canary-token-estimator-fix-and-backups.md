# 2026-10-27: v0.3.x canary token-estimate fix, paid-lane cap, and pre-cutover backups

| | |
|---|---|
| **Status** | APPLIED on 2026-10-27, to the **legacy v0.3 canary only**. That canary and the legacy v0.2.1 router were both **retired on 2026-11-15**. |
| **Made by** | Grok Foreman (executor) |
| **Approved by** | Jordan Mason, 2026-10-27 (about 1:30 AM MT). Approved in order: A) backup, B) temporary test client plus the full test suite, C) estimator fix on the canary only, D) registry re-verification. Step E (Ollama paid lane with a monthly cap, canary only) was approved separately the same night. |
| **Full record** | Private Drive: "Sovereign Router Cutover Log (Grok) - 2026-10-27". It holds exact paths, hashes and backup locations, deliberately left out here because this repo is public. |

> This repo is public, so this note leaves out hostnames, IPs, file paths, backup locations and credentials on purpose.

## What changed

- **A. Backups.**
  - A hypervisor snapshot of the router VM, taken before the cutover.
  - An online SQLite backup of the canary quota DB, integrity-checked.
  - A root-only archive of the router host's config files.
  - A full vzdump was **not** taken because there wasn't enough free backup storage.
- **B. Test suite.**
  - A temporary synthetic client key was added, the canary suite was run, then the key was removed.
  - Afterwards the key was confirmed to return 401.
- **C. Token-estimate fix (v0.3.2-canary).**
  - The old estimator used `bytes / 2`, about 2.3× too high for English. That caused false 503s on large agent prompts: an ~85k-token prompt was estimated at 272k.
  - It was replaced with a tiktoken `o200k_base` count × 1.05 plus per-message overhead (falling back to 4 bytes/token).
  - Oversized prompts now get an honest 413.
  - **Before:** the ~85k-token prompt got a 503. **After:** 200.
- **D. Registry re-verification.** Groq entries were re-verified against provider docs and extended. The Ollama entry was not extended pending a billing check.
- **E. Paid lane.**
  - One allowlisted paid entry (Ollama gpt-oss:120b), eligible only with `paid_approved`, numeric prices and a monthly cap.
  - Spend is reserved up front and refunded to the actual cost afterwards. The cap was $5 per UTC month.
  - Tested with one paid call of about 12.4k tokens.

## Verification

- The canary was healthy after each restart.
- The production router of the time (v0.2.1) was confirmed untouched by its unchanged container start time.
- The quota DB passed its integrity check.

## Rollback (historical)

The pre-change files were kept, and rollback was to copy them back and restart the canary container only. This no longer applies: the canary was retired on 2026-11-15, and its source and state were archived to private Drive per "2026-11-15 — Sovereign OS Legacy Router and AerSol PostgreSQL Retirement — Verified Checkpoint".

## Not done (as of that date)

- The Hermes switch to the canary was approved, but it paused on a port-binding change. Hermes later moved straight to the v0.5 stack on 2026-11-02 (see `docs/BUILD-OUT.md`).
