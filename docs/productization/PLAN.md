# Sovereign Router — Productization Plan

**Status:** Planned implementation. Documentation only — no product code in this change.
**Date:** 2026-11-24. **Base:** `main` @ `7e5e8ab` (v0.9.2 + PR #18 merged).
**Companion docs:** [GUI-SPEC.md](GUI-SPEC.md) · [SECURITY.md](SECURITY.md) · [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md)

This plan supersedes the admin-console plan in PR #17 (`docs/cycles/cycle-04-gui-plan.md`,
Claude Code, 2026-11-19, open/unmerged). Where the two agree and where they diverge is
stated in §7 — the short version: PR #17 designed a console for operating *Jordan's*
instance; this plan designs a **product other people can install**. That difference
drives most of the divergence.

---

## 1. Vision

Sovereign Router today is a working system and a pile of operator knowledge: which
files to edit, which ownership a key file needs, which order to restart things in,
which numbers in the registry are earned and which are seeded. Productization means
turning that knowledge into an installable bundle with a graphical admin surface, so
that a second operator — on their own hardware, with their own provider accounts —
can stand up the same router without reading this repo's incident history.

**Product shape, v1:**

- **Self-hosted, single-operator.** One install serves one operator's fleet of
  agents/apps (the pattern Jordan's fleet runs: Hermes, OpenClaw, n8n, TwentyCRM as
  *clients*). Each install has exactly one admin. **Multi-tenancy is explicitly out
  of scope for v1** — no teams, no per-user roles, no self-service client signup.
  (PR #17's viewer/operator/owner role model is deferred with it; see §7.)
- **The gateway stays the runtime.** The GUI is a control plane. It reads and writes
  the same config and state surfaces the gateway already uses; it never sits in the
  request path, and the router must run correctly with the GUI stopped.
- **Owner rulings are product invariants, not preferences.** The rulings that make
  Sovereign Router what it is are encoded in the GUI's design, not left as
  documentation the operator is trusted to follow:
  - **No spending caps, ever** (2026-10-31). Client daily targets steer and alert;
    no screen may present a target as a limit, and no code path may refuse a request
    for budget reasons.
  - **Quality scores are earned by measurement, never entered by hand.** Scores come
    from the tooling battery / golden set and carry `tooling_source` +
    `verified_until`. The GUI displays them read-only.
  - **Free first, paid on exhaustion.** The free→free spillway, then paid
    cheapest-best, is the selection order; the GUI exposes it, it does not offer
    alternatives that violate it.
  - **Sensitive data stays on cleared ground.** The sensitive wall and per-entry
    `spillway_sensitive_excluded` flags bind selection *and* pins (J-1); the GUI
    must not offer a path around them.
  - **Route logs never contain prompt or response text.** Any GUI surface that shows
    request detail shows metadata only, because that is all that exists.

## 2. What a Sovereign Router install is — component map

An install is the following components. The productization work packages them; it
does not redesign them.

| # | Component | Today (Jordan's install) | Product form |
|---|-----------|--------------------------|--------------|
| 1 | **Gateway** | `src/gateway.py` (FastAPI), container `sovereign-router-v05-gateway`; OpenAI-compatible `/v1/*`, `/health`, admin-key `/admin/*` (`reload`, `routes`, `costs`, `killswitch`, `alerts`) | Same container, version-pinned image |
| 2 | **Classifier sidecar** | `classifier/sidecar.py` — rules layer + TF-IDF/embedding nearest-match; internal network only; gateway falls back to rules on timeout | Same container; model weights volume |
| 3 | **Registry** | `src/registry.json` (v2; 98 entries) — models, lanes, prices + provenance (`price_source`, `price_verified_at`, `price_confidence`), limits, measured quality, probe config | Seeded starter registry shipped in the bundle; edited via GUI/admin API thereafter |
| 4 | **Clients config** | `clients.json` — per-client key **hash** (raw keys never stored), lanes, daily target, premium flags, `sensitive_cleared_providers` | Created/managed via GUI; raw client key shown exactly once at creation |
| 5 | **Secrets store** | Directory of per-provider key files, mode 600, owned `10001:10001` (the gateway's uid), mounted read-only into the gateway | Same on-disk contract; the GUI's write path is the component that must honour mode/ownership (see SECURITY.md) |
| 6 | **Quota / ledger DB** | SQLite in the state dir: `usage_events`, `attempt_events`, `alerts`, `meta`, `killswitch`; plus `routes.jsonl` (metadata only) | Same; part of backup/restore scope |
| 7 | **Governor** | In-gateway: per-client steering vs target, premium counters, warn-only emergency level, alert generation | Unchanged; the GUI displays its state |
| 8 | **Ingress** | nginx, the only published port, source allowlist in front of the gateway | Same; the installer generates the allowlist from the operator's client hosts |
| 9 | **Observability** | Prometheus exporter + Grafana dashboards (Overview, Models & Systems); `/health` carries `alert_chain` freshness; n8n alert chain polls `/admin/costs` + `/admin/alerts` every 15 min (Signal/email) | Grafana bundle ships in the Compose stack; alert chain ships as an optional n8n workflow export **plus** a built-in staleness watchdog (§5) so alerting does not depend on n8n |
| 10 | **Admin plane (NEW)** | CLI + file edits + SSH today | Admin API service + GUI (§3) |

State layout on the host (unchanged contract): a **state dir** (registry copy,
clients.json, quota DB, route logs) and a **secrets dir** (provider key files),
both mounted into the gateway; the state-dir registry copy is kept byte-identical
to the reviewed record (see §4, source of truth).

## 3. Architecture — the admin plane

```
                    ┌──────────────────────── host ─────────────────────────┐
 operator browser ─►│ admin GUI (static SPA) ─► admin API service            │
                    │                              │        │        │      │
                    │              secrets writer ─┘        │        └─► gateway /admin/*
                    │              (mode-600 files,           │            (reload, costs,
                    │               chown 10001:10001)       │             alerts, health)
                    │                                       ▼
                    │                          state dir (registry.json,
                    │                          clients.json, quota.db)
                    └───────────────────────────────────────────────────────┘
 clients (agents) ─► nginx (allowlist) ─► gateway ─► classifier sidecar ─► providers
```

- **Admin API service** — a new, small service in the Compose stack. It is the only
  component allowed to write config/state on the GUI's behalf. It:
  - reads registry/clients/quota state and gateway `/admin/*` + `/health` for display;
  - writes `registry.json` / `clients.json` **atomically** (write temp, validate
    against the gateway's own loader, rename), then calls `/admin/reload`;
  - writes provider keys through a dedicated secrets-writer path that creates the
    file mode 600, `chown 10001:10001`, fsyncs, and never returns the value;
  - appends every mutation to an **audit log** (who/when/what — never values);
  - holds **no provider keys in memory beyond the write**, and keeps no config
    database of its own in v1 (see §4).
- **GUI** — a static single-page app served by the admin API service (no separate
  web tier). It talks only to the admin API. If the admin service is down, routing
  is unaffected.
- **Gateway changes required: none for Phases 1–2.** The existing `/admin/*`
  surface (admin-key auth) is sufficient for reload/costs/alerts/routes/health.
  Phase 3 (rules editor) may need a read endpoint for the effective classifier
  config; that is a gateway change and goes through the normal deploy gate.

## 4. Source of truth — files and git, not a config database

PR #17 proposed moving config into SQLite tables with JSON export. **This plan
diverges:** in v1 the files remain the source of truth, because the fleet's whole
change discipline — snapshot, diff review, CI on the registry, byte-identical
state copy — is built on them, and it works.

- The GUI is the **only writer** of the state-dir copies during normal operation.
- Every GUI-applied change produces a **committable diff**: the admin API keeps the
  pre-change file as a snapshot (see §6) and can export the current
  `registry.json`/`clients.json` pair for the operator to commit to their own git
  repo. Drift between the state copy and the operator's repo is displayed, not
  hidden.
- A versioned config store remains a legitimate Phase 4+ question
  (OPEN-QUESTIONS.md), to be decided on evidence of file-based pain, not upfront.

## 5. Alerting without n8n — the staleness watchdog

Jordan's install alerts through an n8n workflow that polls the gateway. That
dependency must not ship to other operators. The product design therefore includes:

- `/health` already exposes `alert_chain.seconds_since_poll` and flags the chain
  degraded after 30 min without a poll. (That signal sat unobserved for 67 hours
  during the Nov 20–23 canary incident — the gateway *knew*; nothing watched.)
- The admin API runs a **watchdog**: it checks `/health` on an interval through a
  path independent of the alert chain itself, and raises its own alert (webhook /
  email / local log, operator-configured) when the chain is stale, the gateway is
  down, or the admin API cannot reach it.
- The GUI's status header shows poll freshness at all times (GUI-SPEC §2).
- The n8n workflow export remains available as an optional richer chain
  (Signal delivery), but the watchdog is the floor every install gets.

## 6. Packaging, install, upgrade, rollback

- **Bundle:** one Docker Compose file (gateway, classifier, nginx, admin,
  Prometheus, Grafana) + `.env` for non-secret settings + seeded starter registry
  (a small, probe-verified free-first set — not Jordan's 98-entry production
  registry) + provider templates (GUI-SPEC §3).
- **Install:** `git clone` → copy `.env.example` → `docker compose up -d` → open
  the GUI → first-run wizard (GUI-SPEC §2). Target: a working router with one
  provider and one client in under 30 minutes, no file editing.
- **Volumes:** `state/` and `secrets/` as host directories with documented
  ownership (state root-owned; secrets files 600 / `10001:10001`). The installer
  script checks and fixes modes, and refuses to start the gateway with a
  world-readable key file.
- **Upgrade:** pull new images → snapshot (below) → `docker compose up -d` →
  GUI shows `/health` version + post-upgrade smoke result. Registry/client config
  is *not* touched by upgrades; schema migrations, when they exist, are versioned,
  run on a copy, and are reversible.
- **Rollback = the existing snapshot discipline, productized:** before any
  GUI-applied config change and before any upgrade, the admin API writes a
  timestamped snapshot (registry + clients + image tags; secrets are *referenced*,
  never copied into snapshots in plaintext — see SECURITY.md). Rollback restores
  the snapshot and recreates containers on the recorded image tags. This is the
  same mechanism that has carried every production deploy since v0.5; the GUI
  makes it a button instead of a runbook.

## 7. Relationship to PR #17

**Agrees with PR #17:** control plane separate from data plane; setup wizard;
provider templates carrying data-policy badges; keys write-only, never returned
by any API; change pipeline of validate → dry-run (shadow) → apply → snapshot →
rollback; Test Route via `/v1/shadow`; golden-set gates before routing-rule
changes; the UI must never weaken the sensitivity wall or an owner ruling;
phased delivery with owner sign-off per phase.

**Diverges from PR #17:**

1. **Purpose.** PR #17 is a console for Jordan's instance. This plan is a
   *product*: installer, packaging, seeded starter registry, upgrade/rollback,
   backup/restore — none of which PR #17 scopes.
2. **Source of truth.** PR #17: SQLite config store, JSON as export. Here:
   files + git stay authoritative in v1 (§4).
3. **Admin topology.** PR #17 serves `/console` from the gateway and adds
   `/admin/v2/*` to it. Here: a separate admin service; the gateway's request
   path and binary stay untouched through Phase 2.
4. **Identity.** PR #17: viewer/operator/owner roles with proposal queues.
   Here: one admin per install in v1 (SECURITY.md); roles are a v2 question.
5. **Phase order.** PR #17's G1 (providers) precedes its config-store phase;
   key entry here is in **Phase 1** alongside the read-only dashboard, because
   "enter keys without the CLI" is the headline ask.
6. **Alerting.** PR #17 does not address the alert chain's n8n dependency or
   the staleness failure mode. Here the watchdog is a designed component (§5).
7. **Catalog data.** PR #17 leaves open syncing a community free-model list.
   Here: provider templates are curated and shipped in-repo; the LiteLLM cost
   map is used only as a *drift tripwire* over prices (the PR #18 tooling),
   never as a source that writes the registry (rule F-04).

## 8. Roadmap

Effort is in focused build sessions (the unit this project has actually shipped
in), excluding Jordan's review time. Each phase exits through the standard gate:
tests green ×2, CI green, snapshot, deploy, live smoke, records.

| Phase | Scope | Exit proof | Rough effort |
|-------|-------|------------|--------------|
| **1 — See it, key it** | Admin API (read surfaces + secrets writer); GUI: status dashboard (incl. poll freshness + watchdog), Providers screen with key entry + per-provider test probe, first-run setup wizard; Compose bundle + installer script | Fresh install on a clean VM reaches a served test request using only the GUI after `compose up` | 3–4 sessions |
| **2 — Edit config safely** | Registry editor + Clients editor over the admin API; atomic write + validate + `/admin/reload`; snapshot-before-change + one-click rollback; audit log viewer; committable-diff export | A model and a client created, edited, and rolled back entirely from the GUI on the staging install; production untouched | 3–4 sessions |
| **3 — Rules editor** | Classification surfaces: sensitivity detectors/patterns, task-class rules, lane definitions + provider data-posture assignment, spillway order display, pins with J-1 semantics; Test Route (shadow) trace; golden-set run + diff before apply | A rule change dry-run against the golden set with zero new violations, applied and rolled back from the GUI | 4–5 sessions |
| **4 — Installer polish** | Seeded starter registry finalization, upgrade flow in GUI, backup/restore export-import, docs (operator manual), watchdog alert channels, hardening pass against SECURITY.md | A second operator (or a clean-room VM) installs from the repo alone and files no questions | 2–3 sessions |

**Explicitly not in this roadmap:** multi-tenancy/roles, auto-enable or
auto-demote of models (J-2/J-4 remain proposal-only), hand-editing of quality
scores, budget caps in any form, a public hosted service.

## 9. What this plan does not change

Production keeps running exactly as it does today while this is built. The
productization work happens on branches and a staging install; Jordan's VM103
deployment adopts the admin plane only when a phase's exit proof has passed and
Jordan gives the deploy go, per phase.
