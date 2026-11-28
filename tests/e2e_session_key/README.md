# E2E: v0.10.4 session-key detector fix

Local end-to-end harness built for Job ROUTER-J1C-20261127 (2026-11-27). It is not collected by pytest/CI, and it makes no real provider calls and uses no real keys.

- `run_e2e.py` boots the real gateway (uvicorn, this tree's `src/`), the real classifier sidecar (tfidf, as deployed) and `mock_upstream.py`, which is an OpenAI-compatible stub supporting success, 402/403/429/500, a first-attempt 429, streaming stalls and tool calls.
  - The registry is `src/registry.json` with `live_overlay.json` applied (the VM103 live lane/enabled/quality state as of 2026-11-27). Every entry routes to its own mock path, so upstream exposure is attributable per entry.
  - Clients follow the live `clients.json` shape, using test keys.
  - Run: `python3 tests/e2e_session_key/run_e2e.py` (set `SR_PY` to a venv python with `src/requirements.txt`).
- `fuzz_and_fp.py` runs a differential fuzz against the v0.10.2 detector (`baseline_v0102_sensitivity.py`). It checks four properties:
  - The new detector never flags anything the old one didn't.
  - Every difference is explained by an OpenClaw session key outside a cookie context.
  - Non-agent session values are flagged identically by both.
  - Agent-shaped keys never flag.
  It also reports FP/FN rates on synthetic corpora.
- `corpus.py` holds the synthetic corpora. Secret fixtures are generated randomly at run time, and the harness prints case names only, never values.

## Section E — routing self-correction (added v0.10.5, Job B)

`run_e2e.py` section "E routing" (22 checks) drives congested free pools (`MOCK_STATUS::429::private-free` / `::public-only-free`) and congested free+paid pools for n8n, hermes and openclaw, JSON and streaming. It asserts at most 6 upstream calls per turn, a paid serve when only free is congested, and that premium is never called on congestion. It also checks that tool results like `{"error": null}` / `{"ok": true, "error": ""}` do not set `tool_error_next_turn`, while real error payloads do. Run against v0.10.4, the section fails 11/22 (17 and 66 upstream calls, premium served, two false tool errors).

## Section F — tiered shadow (added v0.10.6, Job C)

Four checks over the whole run's route log: every served row carries `tier_shadow`; on sensitive rows the shadow pick is never public-only-free; the shadow never picks premium while a free/cheap candidate exists; and `/v1/shadow` carries `tiered_shadow` whose `live_pick` equals `would_route_to`. The boot check now reads this tree's `VERSION` instead of a hard-coded string.
