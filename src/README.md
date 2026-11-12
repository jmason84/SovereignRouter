# Sovereign Router — Gateway

One OpenAI-compatible endpoint for OpenClaw, Hermes, n8n and TwentyCRM
that sends every request to the cheapest model able to do it well. The
current intent and routing rules live in `docs/ROUTING-PRINCIPLES.md`;
the system-level overview is the repo-root `README.md`. Implements spec
**Sovereign Router v0.5.3** (Launch Collective, owner Jordan Mason):
request pipeline, difficulty levels 1–5, client policies, cost governor
(steering + alerts, *never* a budget refusal), sticky routing, output
validation/escalation, and JSON route-line telemetry. The running
gateway reports its version (`VERSION` in `gateway.py`, currently
**0.6.0**) at `GET /health`.

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

### `gateway.py` map (~1,560 lines, single module)

- **Top section — helpers and config.** `estimate_input_tokens` (local
  token estimate for the pre-call ledger entry), `GatewayConfig` (a
  dataclass populated from the `SOVEREIGN_*` environment variables —
  see the table below), `_route_headers` (the `X-Sovereign-*` response
  metadata), `_usage_from_response` (post-call usage correction), and
  `note_lane_dead` (records the once-per-model-per-day `lane_dead`
  alert on an upstream 401/402/403 or daily-cap exhaustion).
- **`create_app()` — the factory and the pipeline.** Wires the registry,
  client policies, `QuotaStore`, `Governor`, `StickyStore` and
  `Telemetry`; loads provider keys from the state dir; builds the
  upstream httpx client (`trust_env=False` — provider traffic carrying
  API keys never goes through a proxy picked up from ambient env vars;
  120 s timeout, 10 s connect). The request path lives in closures
  inside the factory: `auth_client` / `auth_admin` (bearer or
  `x-api-key`, hash-compared), `build_candidates` (lane, premium gate,
  sensitive clearance, rate-limit state, cheapest-first sort),
  `build_spillway_candidates` (the paid-spillway re-selection),
  `upstream_body` / `upstream_headers` / `call_upstream`, then the
  route handlers — `health`, `models`, `quota`, `usage`,
  `admin_reload` / `admin_routes` / `admin_costs` / `admin_killswitch`
  / `admin_alerts`, `shadow`, and `chat_completions` (the pipeline
  itself, with its own spillway builder).
- **Response paths (module level).** `_json_response` for buffered
  replies, `_stream_response` for SSE (an inner `produce()` assembles
  streamed tool calls), `_finalize_stream` to write the usage event,
  sticky binding and route line when a stream ends.
- **Dev entry point.** `app = create_app()` only when
  `SOVEREIGN_DEV_SERVE=1`; production serves via
  `uvicorn gateway:create_app --factory` in the container.

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
| `SOVEREIGN_EMERGENCY_SWITCH` | `0` | `1` enables the emergency paid-lane switch (stays **off** — owner ruling 2026-11-09) |
| `SOVEREIGN_EMERGENCY_DAILY_USD` | `25` | Daily paid-spend level for the emergency alert/switch |
| `SOVEREIGN_EMERGENCY_WARN_ONLY` | `0` | `1` = warn-only (PRODUCTION setting): at the level, alert but never switch the paid lanes off |
| `SOVEREIGN_DEV_SERVE` | unset | `1` = build a module-level `app` for `uvicorn gateway:app` (local dev only) |
| `<PROVIDER>_API_KEY` | unset | Local-dev convenience only; production uses key files |

## State dir layout

```
state/
  clients.json        # client policies + key sha256 hashes (+ admin hash)
  registry.json       # optional override; the bundled registry.json is the default
  keys/               # mode-600 files owned by the gateway uid (10001), read once at startup, never logged
    # one <provider>.key per registry provider — currently: cerebras,
    # cloudflare, deepinfra, deepseek, google, groq, huggingface, meta,
    # mistral, novita, ollama, openai, openrouter, siliconflow, zai
    # (an nvidia.key is also installed on the production host; NIM was
    # probed 2026-11-12 and not added to the registry)
  quota.db            # sqlite: usage ledger, provider windows, alerts, killswitch
  routes.jsonl        # one JSON route line per request (no prompt/response text)
```

Secrets rule (spec): provider keys live **only** in `keys/*.key`
(mode 600) — never in compose files, the registry, Drive, or logs.
Separate keys per environment; changing a key = overwrite the file + restart
(the mechanism only — there is no standing rotation program; the planned
rotation was cancelled by the owner 2026-11-03, see `deploy/DECISIONS.md`).

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
# from the repo root — 248 tests, mock upstreams only, no real provider calls
src/.venv/bin/python -m pytest tests/
```

CI (`.github/workflows/ci.yml`) runs the same suite on Python 3.12 for
every push and pull request to `main`; it loads the real
`registry.json`, so a registry edit that breaks a premise fails there
before it can deploy. `tests/e2e_live.py` additionally boots the real
gateway + classifier sidecar + a fake upstream over HTTP and exercises
the full pipeline end to end.
