# Workflow exports

Off-box copies of the n8n workflows that are part of the Sovereign Router system (the workflows themselves run on the homelab n8n instance, CT100).

- `sovereign-router-alert-chain.n8n.json` — "Sovereign Router — Alert Chain (Phase 0)" (id `nkYJEHhqEQ95Arkb`, ACTIVE): 15-minute poll of the router's /admin/alerts + /admin/costs, Signal-primary delivery with email fallback, daily 08:10 MT all-clear. Exported 2026-11-10.

Redactions: the Signal account/recipient phone number is replaced with `+1XXXXXXXXXX` (12 occurrences) — this repo is public. Credential references are n8n credential ids/names only (n8n never exports credential values), so re-importing requires the named credentials to exist on the target instance: "Sovereign Router v0.5 — Admin Key" plus the Signal bridge and email credentials.
