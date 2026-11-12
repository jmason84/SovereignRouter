# Branching, tagging and versioning standard

Adopted 2026-11-12 (proposal; the rename plan below executes only on the owner's go).

## Branch names

`<type>/<version-or-date>-<short-topic>`, lowercase, hyphen-separated.

| Prefix | Use | Example |
|---|---|---|
| `main` | Trunk. What is (or is about to be) deployed. Always green. | `main` |
| `release/v0.5` | Maintenance line for a deployed minor, if hotfixes must not wait for trunk work. Optional. | `release/v0.5` |
| `feature/<ver>-<topic>` | New behaviour for a target version. | `feature/v0.5.9-ops-hardening` |
| `fix/<ver>-<topic>` | Bug / incident fixes. | `fix/v0.5.10-sticky-fitness` |
| `docs/<date>-<topic>` | Docs-only. | `docs/2026-11-12-ops-review` |
| `archive/<name>` | Frozen history kept for reference; never merged, never deleted. | `archive/alt-build-2026-11-01` |
| `claude/...` | Tool-managed session branches; leave names as generated, delete after merge. | — |

## Versions and tags
- Gateway version = `VERSION` in `src/gateway.py`, recorded in `CHANGELOG.md`.
- Every version that is built into an image and deployed gets an **annotated tag**
  `vMAJOR.MINOR.PATCH` on the exact built commit (the repo currently has **no tags**,
  so "what is deployed" cannot be answered from git; v0.5.7 is the live image).
- Image tags mirror git tags (`sovereign-router:v0.5.9`).
- Commits: imperative summary; mention the incident/issue id when there is one.

## Pull requests
Feature/fix branches merge to `main` by PR; CI (`.github/workflows/ci.yml`) must pass.
Deploy follows `deploy/deploy-runbook.md` (snapshot → backup → deploy → verify).

## Rename plan (needs owner go; nothing below has been executed)

| Current | State | Proposed |
|---|---|---|
| `ccr-302db870-066n3h` | **GitHub default branch**; fully merged into `main`; base of PR #1 | First make `main` the default (Settings → Branches), then `archive/v0.5.3-review-base` |
| `alt-build-2026-11-01` | Fully merged; same commit as above | `archive/alt-build-2026-11-01` |
| `v0.5.3` | Head of open PR #1 (1 commit not in `main`) | Close PR #1 deliberately, then `archive/v0.5.3-review-response` |
| `improvements-2026-11-05` | Fully merged into `main` | `archive/improvements-2026-11-05` (or delete after tagging) |
| `improvements-2026-11-11` | v0.5.8 work, not merged | `feature/v0.5.8-improvement-plan` |
| `claude/sovereign-router-optimization-lmwryx` | Session branch holding the plan doc (also in the branches above) | leave; delete once merged |

Cautions: GitHub retargets/closes PRs when their branches are renamed, so close PR #1
first; do not rename the default branch before `main` is made the default; add tags
(`v0.5.7` and later) before archiving so history stays addressable.
