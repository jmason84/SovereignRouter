# Cycle 04 — plan: Sovereign Router admin console (GUI)

Date: 2026-11-19. Planning only; nothing built or deployed.
Request: a GUI for the Sovereign Router, to hook up inference providers and free
LLMs easily, and to manage routing rules and classifiers.
Inputs: `RESEARCH-2026-11-19.md` section 1.5, `COMPONENT-REVIEW-2026-11-19.md`.

## Goals
1. Add a provider (including free ones) in minutes, with a connection probe and
   automatic model discovery, without editing JSON.
2. See every model's state: lane, scores, tool health, quota pool, data policy.
3. Edit routing rules and client policy safely, and dry-run any change against a
   request or the golden set before it applies.
4. Manage classifiers: rules layer, sidecar status, golden-set accuracy.
5. Never weaken the sensitivity wall or an owner ruling through the UI.

## Non-goals
Replacing the gateway data plane; multi-tenant SaaS features; auto-enabling or
auto-disabling models (J-4); enforcing demotion (J-2); any deploy from the UI.

## Architecture
- **Control plane separate from data plane.** A new `/admin/v2/*` JSON API (the
  existing `/admin/*` endpoints stay) and a static single-page console served at
  `/console`. The gateway's request path does not depend on the UI.
- **Config store.** SQLite tables for providers, registry entries, clients, rules,
  with every change a row in a versioned `config_revisions` log. A command exports
  `registry.json`/`clients.json` so git remains the reviewable record. The gateway
  loads the active revision; an invalid revision never loads.
- **Change control.** Edits create a *draft*. Pipeline: validate (schema + invariant
  battery) -> dry-run (shadow replay) -> apply -> snapshot -> one-click rollback.
  Edits that touch owner rulings (enable/disable a model, demotion, sensitive
  clearance, lane membership of a client) become *proposals* that only the owner
  role can approve (J-4).
- **Secrets.** Provider keys are write-only: stored encrypted or as references to
  the existing env/secret mechanism, never returned by any API, never logged.
  Client keys shown once, stored as SHA-256 hashes (existing behavior).
- **Frontend.** A small SPA (React or similar) or server-rendered pages; choose in
  G0. Keep it dependency-light and build offline-capable on the homelab.

## Screens
1. **Setup wizard** (first run): create owner, add first provider, add first client,
   run a test request.
2. **Providers:** choose from a *free-catalog* of templates (Groq, Cerebras,
   OpenRouter, Cloudflare Workers AI, Google Gemini, Mistral, NVIDIA API Catalog,
   GitHub Models, Z.ai, Cohere...) or a generic OpenAI-compatible form (base URL,
   key). Each template carries known limits, quota unit, signup link, and a
   **data-policy badge** (may train / retains / zero-retention) and ToS note.
   Buttons: *Check connection* (says which URL/path failed), *Discover models*,
   *Probe tools* (runs the tooling battery on selected models).
3. **Models:** table of entries with lane, tooling and class scores with
   `verified_until`, live tool-health, pool and quota, enabled flag, sensitivity
   flags. Bulk probe. New models discovered from a provider arrive *disabled*
   (existing rule FFC-28/29).
4. **Routing:** lane order and free-first/paid-fallback settings; quality floors;
   pins (bounded by J-1); per-client allowlists; ordered rules (condition ->
   action) with a clear split between *locked* constraints (sensitivity wall,
   lane membership) and *editable* preferences. **Test Route** shows the step-by-step
   trace: signals, classifier output, candidates, skip reasons, chosen model.
5. **Classifiers:** rules-layer thresholds, sidecar status/latency, golden-set run
   with confusion matrix and diff against the previous revision.
6. **Clients:** keys (show once), lanes, allowlist, budgets, alert levels.
7. **Observability:** route explorer with skip reasons, tool-health, cost per
   completed loop, pool/quota health matrix, alerts feed.
8. **Change control:** drafts, proposals awaiting owner, audit log, snapshots,
   rollback.

## Security requirements (lessons from 2026 gateway incidents)
LiteLLM's advisories (low-privilege account reaching `/config/update`; role
escalation via a reused signing key; MCP preview endpoints allowing command
execution; a default admin key on exposed instances) are the failure list we design
against:
- Bind the console to LAN/Tailscale or behind Cloudflare Access; never public.
- No default or shared master key; first-run owner creation; strong session cookies,
  CSRF protection, rate limits, lockout.
- Roles: viewer, operator (drafts, probes), owner (approvals, secrets). Every
  mutating endpoint checks role server-side; add a test that enumerates all routes
  and fails if one lacks a role check.
- No endpoint that executes commands or spawns processes from the UI.
- Audit log is append-only; exports contain no secrets.
- Independent recovery: owner can reset the console and revert to the last good
  revision from the Proxmox console without the UI (infrastructure recovery rule).

## Phases (each enters through the Improvement Loop)
- **G0 (read-only):** dashboard, models table, route explorer, tool-health,
  Test Route using `/v1/shadow`. Lowest risk; delivers visibility first.
- **G1 providers:** catalog templates, connection check, model discovery, key vault.
- **G2 config store + drafts:** versioned revisions, validate/dry-run/apply/rollback,
  model and client editors, proposal queue for owner-ruled changes.
- **G3 rules editor:** declarative rules, locked vs editable split, golden-set diff.
- **G4 classifier lab:** golden run UI, synthetic prompt tests (inspired by
  open-model-prism), threshold tuning.
Exit for each phase: tests (including the route-role enumeration test), golden-set
replay with zero new violations, owner sign-off, VM103 snapshot before any deploy.

## Open decisions for the owner
1. Frontend stack and whether to serve from the gateway process or a sidecar.
2. Who gets owner vs operator roles.
3. Whether the free-catalog is hand-curated or synced from a community list
   (cheahjs/free-llm-api-resources); synced data needs review before it enables anything.
4. Where config lives long-term: SQLite as source of truth with JSON export, or
   JSON in git as source of truth with the GUI writing pull requests.
