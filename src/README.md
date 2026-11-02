# Sovereign Router v0.5 — Gateway

One OpenAI-compatible endpoint for OpenClaw, Hermes and n8n that sends
every request to the cheapest model able to do it well. Implements spec
**Sovereign Router v0.5.3** (Launch Collective, owner Jordan Mason):
request pipeline, difficulty levels 1–5, client policies, cost governor
(steering + alerts, *never* a budget refusal), sticky routing, output
validation/escalation, and JSON route-line telemetry.

## Layout

| File | What it does |
|---|---|
| `gateway.py` | FastAPI app: `/v1/chat/completions` (JSON + SSE), `/v1/models`, `/health`, `/v1/quota`, `/v1/usage`, `/admin/*` |
| `registry.py` + `registry.json` | Registry v2: lanes, per-task-class quality map, cached-input pricing, spend-alert levels, probe + `verified_until`, provider limits |
| `clients.py` + `clients.example.json` | Client policies (lanes, premium permission, daily targets) + sha256-hash auth (v0.3 pattern) |
| `classifier.py` | Layer-1 rules in-process; layers 2/3 (embedding / Arch-Router) are the sidecar, called over HTTP with a 1.5 s timeout and rules fallback |
| `sensitivity.py` | Known-format credential detectors (gitleaks-style); hex hashes / UUIDs / SHAs explicitly excluded |
| `governor.py` | Daily-target steering, level-mix + runaway + slow-leak + premium alerts, optional emergency paid-lane switch (default **off**) |
| `sticky.py` | Conversation stickiness: client + sha256(first user msg + system prompt), 30 min sliding TTL |
| `validation.py` | Output checks (empty, length, tool-arg JSON, unknown function, JSON mode) + streamed tool-call assembly |
| `telemetry.py` | One JSON route line per request (never prompt/response text), outcome signals, nightly-scoring stub |
| `quota.py` | SQLite store: provider rate-limit windows, usage ledger (estimate pre-call, corrected post-call), alerts, poll heartbeat, killswitch |

## Run locally

```bash
cd src
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# state dir: clients.json + provider key files
mkdir -p /tmp/sr-state/keys
cp clients.example.json /tmp/sr-state/clients.json   # replace the sha256 hashes!
printf %s 'sk-...'  > /tmp/sr-state/keys/groq.key        && chmod 600 /tmp/sr-state/keys/groq.key
printf %s 'sk-...'  > /tmp/sr-state/keys/deepinfra.key   && chmod 600 /tmp/sr-state/keys/deepinfra.key
SOVEREIGN_STATE_DIR=/tmp/sr-state \
  .venv/bin/uvicorn gateway:create_app --factory --port 6075
curl localhost:6075/health
```

Client key hashes: `printf %s 'THE-RAW-KEY' | sha256sum`. The raw key is
shown to the client once and never stored anywhere.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `SOVEREIGN_STATE_DIR` | `./state` | State dir (see layout below) |
| `SOVEREIGN_SIDECAR_URL` | unset | Classifier sidecar base URL; unset = rules only |
| `SOVEREIGN_SENSITIVITY_LOG_ONLY` | `0` | `1` = Phase-0 trial: scan logs matches but does not change routing |
| `SOVEREIGN_EMERGENCY_SWITCH` | `0` | `1` enables the emergency paid-lane switch (owner decision; spec proposes on) |
| `SOVEREIGN_EMERGENCY_DAILY_USD` | `25` | Daily paid-spend level that trips the switch when enabled |
| `<PROVIDER>_API_KEY` | unset | Local-dev convenience only; production uses key files |

## State dir layout

```
state/
  clients.json        # client policies + key sha256 hashes (+ admin hash)
  registry.json       # optional override; the bundled registry.json is the default
  keys/               # mode-600 files, read once at startup, never logged
    groq.key  openrouter.key  deepinfra.key  ollama.key  openai.key
  quota.db            # sqlite: usage ledger, provider windows, alerts, killswitch
  routes.jsonl        # one JSON route line per request (no prompt/response text)
```

Secrets rule (spec): provider keys live **only** in `keys/*.key`
(mode 600) — never in compose files, the registry, Drive, or logs.
Separate keys per environment; rotation = overwrite the file + restart.

## Interfaces

- `POST /v1/chat/completions` — OpenAI-compatible, JSON or SSE streaming.
  Aliases: `auto`, `cheap`, `strong`, `premium`, `code`, `long`.
  Header override: `X-Sovereign-Level: 1..5`.
  Response headers: `X-Sovereign-Model`, `X-Sovereign-Level`,
  `X-Sovereign-Task`, `X-Sovereign-Lane`, `X-Sovereign-Cost-Estimate`,
  `X-Sovereign-Escalated`.
- `GET /v1/models`, `GET /health` (alert chain degraded when no
  `/admin/costs` poll for 30 min; classifier sidecar health),
  `GET /v1/quota`, `GET /v1/usage` (both scoped to the caller's key).
- Admin key only: `POST /admin/reload`, `GET /admin/routes` (24 h
  decision stats), `GET /admin/costs` (spend by client/provider/level +
  pending alerts; also the alert-chain heartbeat poll),
  `POST /admin/killswitch` (`{"client_id": "...", "active": true}` or
  `{"clear_emergency": true}`).
- Error contract: **429** (+ `Retry-After`, skip reasons) only when every
  candidate is rate-limited/failing; **413** only when no eligible model
  can ever fit; **403** for lanes the client may not use. Budget never
  refuses a request.

## Tests

```bash
cd ../tests && ../src/.venv/bin/python -m pytest -q   # mock upstreams only, no real provider calls
```
