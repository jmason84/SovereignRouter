# Branch Archive — SovereignRouter

This repository keeps its historical branches as a permanent archive, including
work that was never merged. Do not delete archive branches. This document is the
record of what each branch was, what worked, what didn't, and why it is kept.

## Naming standard

Branch names follow one structure, readable without opening the branch:

```
<lifecycle>/v<semver>-<kebab-descriptor>-<disposition>
```

- `lifecycle`: `archive` (frozen history), `feature` (active work), `release`
- `semver`: the gateway version the branch's tree carries or was built against
- `descriptor`: what the branch is, in a few kebab-case words
- `disposition`: `merged`, `unmerged`, or `superseded`

`main` is the canonical branch and carries the current release (v0.6.0 at the
time of writing). Active work follows the same pattern — e.g.
`feature/v0.5.9-ops-hardening`.

## The branches

| Branch | Version | What it was | What happened | Disposition |
|---|---|---|---|---|
| `main` | v0.6.0 (current) | Canonical converged tree; the code running in production on the v0.5 gateway (:6075) | Every shipped release from v0.5.3 through v0.6.0 landed here | Active — default branch |
| `release/v0.6.0-provider-expansion` | v0.6.0 | Snapshot branch created 2026-11-12 at Jordan's order, pointing at `main` @ ea249a0 — the exact tree of the Nov 11–12 work: the v0.6.0 release plus the provider expansion (Cerebras, Novita, Z.ai, Gemini, Cloudflare ×2, Mistral ×2, Meta Muse Spark enabled; Hugging Face Cerebras route deleted), CI, and the branch-organization docs | **Worked** — verified live on VM103 the same morning (gateway healthy, version 0.6.0, registry 22 models / 21 enabled, checksum-identical to the repo) | Release snapshot — frozen; kept so the shipped state is recoverable as a branch, not just a commit |
| `archive/v0.5.3-ccr-build-superseded` (was `ccr-302db870-066n3h`) | v0.5.3 | The original hosted-session build of the v0.5.3 spec; sat at the same base commit as the parallel build (3e41a3d), plus one unique commit adding the deploy checklist, risk register, and a README deployment-status fix | Its code did not become canonical (main is the locally verified converged tree), and it accidentally remained the repo's default branch for weeks. Its one unique commit is the only committed home of the deploy-checklist and risk-register docs | Archived — superseded; kept for the unique docs commit |
| `archive/v0.5.3-parallel-build-superseded` (was `alt-build-2026-11-01`) | v0.5.3 | A second, parallel build of the same v0.5.3 specification, produced independently of the canonical tree | Did not win: when Jordan found two builds of the same spec, the verified local tree became canonical and this build was preserved, with its genuine extras ported into main | Archived — superseded; kept as the record of the parallel build |
| `archive/v0.5.3-review-response-superseded` (was `v0.5.3`) | v0.5.3 | The v0.5.3 build plus its review-response commit (1 commit, 19 files) responding to the v0.5.3 design review | The response was overtaken by the shipped v0.5.4–v0.6.0 line on main | Archived — superseded |
| `archive/v0.5.4-improvements-merged` (was `improvements-2026-11-05`) | v0.5.4 | Improvement work of Nov 5, 2026, including the premium-lane expansion | **Worked** — fully merged: the branch is 0 commits ahead of main; its content shipped as v0.5.4 | Archived — merged in full |
| `archive/v0.5.7-improvements-unmerged` (was `improvements-2026-11-11`) | v0.5.7 base | The Phases 0–5 improvement program of Nov 11, 2026 (9 commits, 23 files), built against the v0.5.7 tree from the Nov 11 operations review | Never landed on main; some items may still be worth mining. Kept unmerged rather than discarded so the work and its reasoning survive | Archived — unmerged |
| `archive/v0.5.7-optimization-unmerged` (was `claude/sovereign-router-optimization-lmwryx`) | v0.5.7 (verified from the branch itself: `VERSION = "0.5.7"` in its `src/gateway.py`) | An optimization pass on the v0.5.7 tree (2 commits, 1 file); its tip carries the v0.5.7 warn-only emergency-switch work | The v0.5.7 release shipped on main through the production line; these branch commits themselves (2 ahead, 14 behind) never merged | Archived — unmerged |
| `feature/v0.5.9-ops-hardening` | v0.5.9 (target) | Active operations-hardening work by another session | In progress — not part of this archive and not renamed | Active — do not touch without its owner's word |

## Rules

- Archive branches are frozen: no new commits, no deletion.
- A branch earns a version number from its own tree (the gateway `VERSION`
  constant or its CHANGELOG), never a guessed one.
- When a feature branch lands or is abandoned, rename it into `archive/` with
  its disposition and add its row here in the same change.
