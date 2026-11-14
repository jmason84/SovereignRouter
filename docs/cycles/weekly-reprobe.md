# Weekly re-probe — invocation & cadence proposal (v0.9.0)

The route-health investigation protocol requires every route's
evidence to stay fresh: a disable goes stale at 30 days without
reconfirming evidence, and (since v0.9.0) `verified_until` stamps
expire on the same horizon. The tooling battery
(`tools/tooling_battery.py`) is the re-probe instrument: it is the
only writer of `verified_until`, and the only source of `tooling`
scores.

## Manual run (proven 2026-11-13, the v0.9.0 fleet run)

On VM103 as root:

```sh
sh tools/reprobe.sh            # full fleet, ~511 calls, paced
sh tools/reprobe.sh --entries zai/glm-4.5-flash,groq/gpt-oss-120b
```

Outputs land in VM103 `state/battery-runs/`:
`tooling-battery-<date>.md` (report), `.json` (full evidence),
`-registry-patch.json` (proposed registry changes). The runner is
checkpointed and resumable; per-provider pacing respects pool caps
(Cerebras trial 5 RPM by default; Cloudflare's dead pool yields
no_evidence, never forced).

## What happens to the patch

Nothing automatic. The patch is reviewed like any registry change:

1. Diff the proposed scores/stamps against the live registry.
2. Any score *drop* of 2+ points, or a previously-measured entry
   coming back unmeasured, gets a failure-log entry and a note in
   the entry's registry `notes` before merge.
3. Merge via the normal path (PR → CI → deploy gate). The registry
   never changes from the runner directly.

## Cadence proposal — DECISION FOR JORDAN AT THE DEPLOY GATE

- **Weekly**, one full battery run (suggested window: the fleet's
  quietest hour; a run takes roughly 25–40 minutes at safe pacing
  and costs well under $1 in paid probes).
- The golden replay (`tools/golden_replay.py` against the committed
  baseline) runs in the same window — it is shadow-only (free) and
  catches selection drift the battery cannot see.
- **No cron is created by this release.** Activation (a VM103 cron
  or an n8n schedule invoking `tools/reprobe.sh`, with the report
  filed to the router Drive folder) is a separate, explicit
  deploy-gate decision.
- Consequence to weigh at the gate: `verified_until` stamps written
  by the v0.9.0 run expire 2026-12-13. If the weekly cadence is not
  activated, the freshness gate aspect B built goes vacuous again
  on that date — the stamps are honest either way, but someone must
  own the refresh.
