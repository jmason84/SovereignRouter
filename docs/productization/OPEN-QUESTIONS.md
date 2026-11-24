# Sovereign Router — Productization: Open Questions

**Status:** Decisions reserved for Jordan. Each has a recommended default so
nothing here blocks the roadmap in [PLAN.md](PLAN.md); work proceeds on the
default until Jordan rules otherwise.

---

## Q1. Support posture for other operators

The repo is already public. Productizing raises the question of what
"public" promises: are outside installers *users we support*, or is this a
take-it-as-it-is release?

- **Recommended default:** publish as-is with an operator manual and a
  "best-effort, no SLA" note in the README; issues welcomed, no response
  commitment. Revisit only if real outside installs appear.
- *Decides:* how much Phase 4 polish (installer hand-holding, docs depth)
  is worth doing.

## Q2. License

`docs/LICENSE-REVIEW-arch-router.md` covers a component's licensing; the
repo as a whole has no settled license decision on record for a
productized release.

- **Recommended default:** MIT for the router + admin plane code, with the
  classifier sidecar's model/embedding licences documented separately in
  the bundle (they carry their own terms). Confirm before the first
  tagged product release, not before Phase 1.
- *Decides:* what the release tag says; nothing in the build depends on it.

## Q3. Install targets: Compose only, or also bare-metal?

The bundle is Docker Compose (PLAN §6). Bare-metal (systemd units, host
Python) is how some homelab operators prefer to run things, and Jordan's
own fleet is Proxmox-shaped.

- **Recommended default:** **Compose only for v1.** One packaging path,
  tested end-to-end, beats two half-tested ones. The component contracts
  (state dir, secrets dir, ports) are documented so a bare-metal install
  is *possible* as a community contribution, but it is not a supported
  path until someone maintains it.
- *Decides:* Phase 4 scope.

## Q4. GUI framework

The GUI is a static SPA served by the admin API (PLAN §3). The framework
choice is open: a small React/Vue app, or server-rendered pages from the
admin service.

- **Recommended default:** **server-rendered pages + progressive
  enhancement** from the admin service (no Node build chain in the
  product). Rationale: the supply-chain posture in SECURITY.md §7 —
  every build dependency is attack surface on a key-holding system, and
  the screens in GUI-SPEC.md are forms and tables, not a rich client
  app. Revisit if Phase 3's Test Route trace outgrows it.
- *Decides:* Phase 1 scaffolding. (PR #17 left the same question open;
  this default is the divergence — see PLAN §7.)

## Q5. Config source of truth, long-term

PLAN §4 keeps files + git authoritative for v1 and defers PR #17's
versioned config-store proposal.

- **Recommended default:** stay file-based through the roadmap; reopen
  only on evidence (drift incidents, multi-operator demand, revision
  queries the audit log can't answer).
- *Decides:* nothing in v1; keeps a known fork in the road marked.

## Q6. Alert channels for the watchdog

The watchdog (PLAN §5) needs at least one channel that does not depend
on the router's own fleet: webhook, SMTP email, or a local-only log +
GUI banner floor.

- **Recommended default:** webhook (generic JSON POST) + optional SMTP,
  both operator-configured in the GUI; Jordan's install keeps its n8n
  Signal chain as the richer path on top.
- *Decides:* Phase 1 watchdog scope (log + banner are the floor either
  way).

## Q7. Starter registry contents

Phase 4 finalizes the seeded starter registry: which providers/models
ship pre-configured (disabled until keyed) so a new operator's first
hour is productive.

- **Recommended default:** a small free-first set drawn from entries
  whose probes and prices are currently verified in the production
  registry (OpenRouter free bench, Groq, Google, Cloudflare, Z.ai
  free), each entry carrying its provenance fields; nothing premium,
  nothing sensitive-cleared by default.
- *Decides:* Phase 4 only; Jordan picks the final list from the
  tripwire/battery reports at the time.
