# Sovereign Router — Admin Plane Security Model

**Status:** Planned implementation. Documentation only.
**Date:** 2026-11-24. Companion to [PLAN.md](PLAN.md) and [GUI-SPEC.md](GUI-SPEC.md).

The admin plane concentrates exactly the things an attacker wants: every
provider key the operator owns, the client key hashes, and write access to
routing policy. It is therefore designed as the most conservative component
in the stack — more conservative than the gateway it manages. The governing
lesson is recent and specific: **LiteLLM, the closest comparable project,
was supply-chain compromised twice in 2026** (March: PyPI 1.82.7/1.82.8 via
the Trivy chain; October: 1.107.0 credential-stealer + RAT via stolen
maintainer tokens, riding even the Docker `latest` tags) — and the October
payload *specifically harvested the provider keys stored by LiteLLM
proxies*. A router's admin surface is a key-harvesting target by design of
the attackers, not by accident. Everything below follows from that.

## 1. Position in the system

- The admin API + GUI are **not in the request path**. Stopping, crashing,
  or deleting the admin plane leaves routing untouched. No code is shared
  between the admin service and the gateway beyond file-format contracts.
- The admin service is the **only** GUI-side writer of config/state. The
  gateway container's existing hardening (read-only rootfs, `cap_drop:
  ALL`, no Docker socket anywhere in the stack) is inherited as a floor,
  not renegotiated.
- The admin service runs with the minimum filesystem reach it needs: the
  state dir, the secrets dir, and its own audit/snapshot dir. It has no
  Docker socket, no shell-out endpoints, and no generic "run command"
  surface of any kind.

## 2. Authentication — one admin

v1 has exactly one identity: **the install's admin**.

- **First-run only:** the setup wizard creates the admin credential. Until
  it exists, the wizard is reachable and nothing else is; afterwards the
  wizard endpoint is permanently disabled. There is **no default password,
  no shipped credential, no recovery backdoor**.
- **Password storage:** salted, memory-hard hash (argon2id or bcrypt) in
  the admin service's own store — never in the state dir, never in the
  registry/clients files, never in git.
- **Sessions:** server-side sessions with a random 256-bit id in an
  `HttpOnly`, `SameSite=Strict` cookie; idle timeout 30 min, absolute
  12 h; login rate-limited with lockout backoff; CSRF token on every
  mutating call.
- **Optional TOTP** second factor (recommended default-on at wizard time,
  operator may defer). Recovery codes shown once, stored hashed.
- **Gateway admin key:** the admin API's calls to gateway `/admin/*` use
  the existing admin key, which lives in the secrets store like any other
  key — entered through the wizard/Providers flow, never baked into the
  admin service's image or config files.
- Forgotten-password recovery is an **operator-at-the-console** procedure
  (a documented CLI reset against the host), deliberately not a web flow.

## 3. Key handling — the rules

These bind the write path, the API, the GUI, and the logs:

1. **Write-only.** A provider key can be created/rotated via the API; no
   endpoint returns it, in whole, in part beyond the fingerprint, or
   "for verification". The GUI never repopulates a key field.
2. **Fingerprint, not value.** After entry the system shows only
   `sha256-prefix + length`. Rotation is verified by fingerprint change,
   never by display.
3. **On-disk contract enforced by the writer.** Key files are created
   mode 600, owned `10001:10001` (the gateway uid), in the secrets dir,
   fsynced before the API reports success. The writer verifies mode and
   ownership after writing and fails loudly if the platform cannot honour
   them — a root-owned key file crash-loops the gateway (learned
   2026-11-09), so this is a correctness rule as much as a security one.
4. **Never logged.** Key values are excluded from: admin API logs, audit
   log (fingerprints only), gateway logs (already true), snapshots,
   backups by default (§4), error reports, and test output. The write
   handler takes the value from the request body into the file write
   without intermediate logging frameworks seeing it.
5. **Never in the config DB or git.** Keys do not enter `registry.json`,
   `clients.json` (hashes only — existing contract), the audit log, or
   any exported diff. The committable-diff export (PLAN §4) is scanned
   for key-shaped strings before it is produced; a match blocks the
   export.
6. **Client keys** follow the existing contract: generated server-side,
   shown once, stored as SHA-256 hashes only.
7. **Memory hygiene:** the admin service does not cache key values; the
   secrets writer reads the request body, writes, and drops the
   reference. Probes that need a key (Providers "test key") read it from
   the file at call time, the same way the gateway does.

## 4. Snapshots and backups

- Config snapshots (PLAN §6) contain registry + clients + image tags —
  **never key files**. A snapshot can restore routing policy; it cannot
  leak or restore credentials.
- Backup export excludes secrets by default. The opt-in "include secrets"
  export requires re-authentication, is labelled sensitive in the UI and
  in the archive manifest, and is audit-logged. Operators are told in the
  UI to treat that archive like the keys themselves.
- Snapshot/restore actions are authenticated, CSRF-protected, and
  audit-logged like any other mutation.

## 5. Network posture

- **Bind:** the admin plane binds to the install host's LAN address (or
  localhost + operator's tunnel), never `0.0.0.0` by default, and is
  **never published to the internet by the bundle**. The Compose file
  ships it unpublished; exposing it is an explicit operator act.
- **Recommended access paths** (documented in the operator manual):
  LAN-only, Tailscale/Headscale, or behind an authenticating reverse
  proxy (e.g. Cloudflare Access). The gateway's own nginx source
  allowlist pattern is the model: defence in depth in front of auth,
  not instead of it.
- **TLS:** when exposed beyond localhost, TLS is required; the bundle
  documents termination at the operator's proxy. Session cookies are
  `Secure` whenever the service is reached over HTTPS.
- **Separation:** the admin plane must not share a published port or a
  proxy server block with the gateway's client endpoint; a compromise or
  misconfiguration of one must not silently open the other. (The Nov
  20–23 incident — an nginx source-IP split silently rerouting a whole
  client to a canary for 67 hours — is the standing example of why
  ingress changes are reviewed as security changes.)

## 6. Audit

- Append-only audit log (admin service's own dir): timestamp, session,
  action, object, field-level diff without values, result. Covers config
  changes, key rotations (fingerprints), snapshot/restore, backup
  exports, logins and failed logins.
- The audit log is exportable and is included in backups (it contains no
  secrets by construction, §3.4).

## 7. Supply chain

The LiteLLM compromises are the design input, not a footnote:

- **Pinned everything:** the bundle pins image digests and dependency
  versions; no `latest` tags anywhere in the Compose file or Dockerfiles.
  Updates are deliberate, changelog-reviewed acts — the same posture as
  the gateway's on-box image builds.
- **Minimal dependency surface:** the admin service is small on purpose.
  Every dependency is a liability a key-harvesting payload can ride in
  on; the GUI ships as static files with no runtime package fetching,
  and the build is reproducible from the repo.
- **No telemetry, no phone-home:** the admin plane makes no outbound
  calls except operator-configured alert webhooks and the provider
  probes the operator explicitly triggers. (The price-drift tripwire
  fetches a public cost map only when run, and writes nothing.)
- **Update path:** upgrades follow PLAN §6 (snapshot → pinned images →
  health read-back). A compromised or broken admin-plane release can be
  rolled back without touching the gateway, because the planes are
  separate (§1).

## 8. What the GUI must never be able to do

A short deny-list, testable in CI:

- Return a stored provider key or any substring beyond the fingerprint.
- Write a key file with any mode other than 600 or owner other than
  `10001:10001`.
- Refuse or block a client request for budget reasons (no-caps ruling).
- Set a quality score by hand.
- Disable or bypass the sensitive wall, or clear a provider for
  sensitive traffic without the change going through the full
  validate → golden-set → snapshot pipeline.
- Serve prompt/response content (none exists to serve).
- Execute host commands, mount the Docker socket, or reach the
  classifier's internal network.
