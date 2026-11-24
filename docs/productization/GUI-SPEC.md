# Sovereign Router — GUI Specification

**Status:** Planned implementation. Documentation only.
**Date:** 2026-11-24. Companion to [PLAN.md](PLAN.md); security requirements in
[SECURITY.md](SECURITY.md) bind every screen below.

Conventions: "the API" is the admin API service (PLAN §3). Anything the GUI shows
comes from the API; the GUI holds no state of its own beyond the session. Every
mutating screen follows the same contract: **validate → show the diff → apply →
snapshot kept → reload confirmed**, and the result banner states what the gateway
actually loaded (read back), never just what was submitted.

---

## 1. Global frame

- **Status header (always visible):** gateway version + health dot; models
  enabled/total; alert-chain state with **seconds since last poll** (green
  ≤ 30 min, red beyond, with the watchdog's own state beside it); emergency
  level state (warn-only — displayed as *warning*, never as "blocked"); today's
  spend vs the sum of client targets, labelled **targets, not caps**.
- **Navigation:** Setup (first run only) · Dashboard · Providers · Models ·
  Clients · Rules · Observability · Changes (audit/snapshots) · Backup.
- **No content anywhere:** no screen can display prompt or response text,
  because the router never stores any. Route detail shows metadata only.

## 2. Setup wizard (first run)

Runs when the admin API finds no admin credential and an empty/minimal state.

1. **Create admin** — set the admin password (SECURITY.md §2). No default
   credential exists at any point; the wizard is reachable only from the
   install's local/LAN address until completed, then it locks permanently.
2. **Install identity** — install name, timezone (drives daily-target windows
   and digest times), host address the nginx allowlist will publish on.
3. **First provider** — pick from the template list (§3); paste the key; the
   wizard runs the **live validation probe** (a minimal authenticated call to
   the provider, then a 4-token chat probe through the gateway once wired) and
   shows pass/fail with the failing step named. Key is written via the secrets
   path before the probe; a failed probe keeps the key and says so.
4. **First client** — name the client; the API generates its key, displays it
   **exactly once** with a copy button and the connection snippet
   (`base_url`, model `auto`), and stores only the SHA-256 hash. Set its lanes
   (sensible default: private-free + paid) and daily target (labelled
   alert-only at the field).
5. **Test request** — the wizard sends one real request through the published
   endpoint with the new client key and shows the route it took (model, lane,
   cost, latency). Wizard completes only on a served test request.

## 3. Providers

One card per provider known to the registry, plus "Add provider".

- **Template or generic:** templates for the providers the fleet runs today
  (OpenRouter, Groq, DeepInfra, DeepSeek, Google, Cloudflare Workers AI,
  Mistral, Z.ai, Hugging Face, SiliconFlow, Novita, Cerebras, NVIDIA, Meta,
  OpenAI, Aion, nRouter, UnoRouter, Ollama) carrying: base URL, key-file name,
  known quota pool shape (per-model / shared account pool / per-model buckets),
  and a **data-posture badge** (trains on data / retains / zero-retention —
  informational; the binding assignment is per-model in Rules §6). "Generic
  OpenAI-compatible" covers anything else by base URL + key.
- **Key entry:** password field, write-only. On save: the API writes
  `secrets/<provider>.key` mode 600, `chown 10001:10001`, fsync — the ownership
  rule is in the write path, not in instructions to the operator (a root-owned
  key file crash-loops the gateway; the installer learned this on 2026-11-09).
  After save the field is never repopulated. The card shows **fingerprint +
  length only** (e.g. `sha256: 632021ee… · 52 chars`) so the operator can verify
  *which* key is installed without seeing it.
- **Test key** button per provider: runs the provider probe (auth check +
  minimal chat call direct to the provider, bypassing selection) and reports
  latency and the exact failure class (auth / quota / network / model) —
  mirroring the gateway's failure taxonomy.
- **Rotate / remove:** rotate overwrites via the same write path and restarts
  the gateway (keys are read at start); remove deletes the file and lists the
  registry entries that will fail until re-keyed or disabled. Both actions are
  audit-logged (no values).
- **Pool health strip:** for shared-pool providers, today's pool usage vs the
  pool's known cap (e.g. OpenRouter free 1,000 req/day) from the quota DB.

## 4. Models / Registry editor

Table of registry entries: id, provider, upstream model, lane, enabled,
context, prices (with `price_confidence` badge), limits, quality by class,
`verified_until`, sensitive-exclusion flag.

- **Create/edit** covers the mechanical fields: upstream model id, lane,
  prices + price source, context, rpm/tpm limits, reasoning flags, probe
  config, notes, enabled.
- **Quality scores are read-only.** The cell shows the score, its
  `tooling_source`/measurement provenance, and `verified_until`; an expired
  score is badged *stale — re-measurement due*. There is no input control.
  Tooltip text states the invariant plainly: *"Scores are earned by the
  measurement battery, never typed in. Run the battery to change a score."*
  A "Run battery" action (Phase 3) queues the existing tooling battery /
  re-probe tooling and lands results through the same review flow as any
  other change.
- **Enable/disable** is a change like any other: diff, snapshot, reload,
  read-back. Newly discovered entries arrive **disabled** (standing rule).
- **Price drift:** rows whose prices the LiteLLM cost-map tripwire (PR #18
  tooling) flags show a *drift suspected* badge linking to the tripwire
  report. The badge nominates; the operator verifies against the provider's
  own published source before editing (rule F-04). Nothing auto-writes.

## 5. Clients

Table of clients: id, lanes, daily target, premium allowed, premium alert
thresholds, sensitive-cleared providers, key fingerprint (hash prefix).

- **Create:** as in the wizard — key shown once, hash stored.
- **Lanes editor:** checkboxes over the four lanes (private-free, paid,
  premium, public-only-free) with one-line plain-language descriptions of
  what each lane *means* (data posture + cost), e.g. public-only-free:
  *"Free tiers whose providers may train on prompts. Never receives
  sensitive-flagged traffic from any client."*
- **Daily target field is labelled "Daily target (alert only — never a
  cap)"**, with helper text: above target the router steers levels 1–3
  cheaper and alerts; it never refuses a request for budget reasons. There
  is deliberately **no "hard limit" control anywhere on this screen** —
  its absence is the feature (owner ruling 2026-10-31).
- **Premium controls:** allowed toggle + monthly/count alert thresholds
  (alerts, again — the word "limit" does not appear).
- **Sensitive-cleared providers:** multi-select of providers this client's
  sensitive-flagged traffic may use; empty is a valid, common state and is
  presented as such ("sensitive traffic stays on the private lanes").

## 6. Rules editor

The screen that exposes *how requests are wrapped and classified*. Four
panels, one change pipeline.

1. **Sensitivity** — the detector list from `src/sensitivity.py` (credential
   prefix patterns, prose-credential detection F-18, exclusions): viewable,
   with per-detector test box ("paste sample text → shows what would be
   flagged"; sample text is never stored or logged). Adding/editing a
   pattern is a Phase 3 change requiring a golden-set run before apply.
   The wall itself — *flagged traffic only reaches cleared providers* — is
   displayed as a locked invariant, not a toggle.
2. **Task classification** — the class set (chat, code, infra, reasoning,
   long-context, tooling-as-dimension) and how each is decided: rules-layer
   signals (e.g. the >60k-token long-context hard signal), sidecar fusion,
   sidecar status/latency. Editable: rules-layer thresholds and signal
   weights. Every edit runs the golden set and shows the diff in violations
   before apply is enabled (F-10's lesson: fusion behaviour is verified,
   not assumed).
3. **Lanes & data posture** — lane definitions; per-entry
   `spillway_sensitive_excluded` assignment with the provider-posture
   reasoning shown from the entry's notes; the shared-pool map (which
   entries share an account pool, so route counts are never read as
   capacity).
4. **Selection & pins** — the selection order rendered as a readable
   waterfall (client lanes → sensitive clearance → pool/limit state →
   cost asc, quality desc, latency asc → spillway on exhaustion), plus the
   pin rules in plain language:
   - *A pin asks for one specific model. It is honoured over cost order,
     quality floor, and context fit — that's what makes pins useful for
     testing.*
   - *A pin never serves a disabled model.*
   - *A pin never carries sensitive-flagged traffic to a provider that
     isn't cleared for it. That one refuses (HTTP 429) instead of silently
     rerouting, so the caller knows its data rule was hit.*
   **Test Route** (available from this panel and the Dashboard): paste a
   request shape (client, class signals, pin, sensitive flag) → the API
   runs `/v1/shadow` and renders the trace: classification, candidates,
   skip reasons, the model that would serve. No live traffic is touched.

## 7. Observability

- **Embedded Grafana** — the existing Overview and Models & Systems
  dashboards, iframed/linked from the bundle's Grafana; the GUI does not
  rebuild charting.
- **Health & chain panel** — `/health` rendered: version, sidecar state,
  emergency state, **alert-chain poll age with the watchdog's independent
  check beside it** (two numbers, so a rerouted or stalled chain is visible
  as divergence, not silence — the Nov 20–23 incident pattern).
- **Costs** — `/admin/costs` rendered per client/provider/model with the
  standing annotation from the existing dashboard: pre-true-cost-era
  figures are ceilings, not spend. Free share shown **by tokens with daily
  totals**, per the standing reporting rule.
- **Alerts feed** — `/admin/alerts`: lane-dead, sensitive-posture digest,
  level-mix, budget-target alerts, with delivery state.

## 8. Changes (audit & snapshots)

- **Audit log:** every mutation through the API — timestamp, actor (the
  admin session), surface, object, field-level diff **without values**
  (key changes appear as "key rotated, fingerprint a1b2…→c3d4…").
- **Snapshots list:** every pre-change and pre-upgrade snapshot with its
  trigger (which change, which screen), contents summary, and **Restore**
  (confirm → restore files → recreate gateway → read back health →
  report). Restore is itself audit-logged and snapshotted (you can roll
  back a rollback).

## 9. Backup / restore

- **Export:** one archive containing state dir (registry, clients, quota
  DB, route logs) + config (compose, .env) + a manifest with versions and
  checksums. **Secrets are excluded by default**; including them requires
  an explicit, separately-confirmed action and produces an archive the UI
  labels as sensitive (see SECURITY.md §4).
- **Import:** into a fresh install (wizard offers "restore from backup"
  as an alternative to first-run setup) or over an existing one (snapshot
  first, always). Import validates every file against the gateway loader
  before anything is applied; a bad archive changes nothing.
