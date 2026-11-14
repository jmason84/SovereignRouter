#!/bin/sh
# Weekly re-probe runner (route-health investigation protocol §cadence;
# v0.9.0 release, stage: re-probe packaging).
#
# Runs the tooling battery against the production gateway from inside
# the gateway container (the only place 127.0.0.1:6075 answers), with
# checkpoint/resume, and stages the emitted registry patch for HUMAN
# review. It never edits the registry: the patch becomes a normal
# registry PR through the gated deploy path, per the protocol
# (disable/re-enable and score changes are reviewed acts).
#
# Run ON VM103 as root:
#   sh tools/reprobe.sh [extra battery args...]
#
# Environment overrides:
#   SR_PACE           global seconds between calls (default 2)
#   SR_PROVIDER_PACE  e.g. "cerebras=13" (default: cerebras trial cap)
#
# Activation as a scheduled job is a DEPLOY-GATE decision (see
# docs/cycles/weekly-reprobe.md) — this script is the manual runner;
# the v0.9.0 manual full run (2026-11-13) is its proof.
set -eu

DATE=$(date +%F)
STATE=/root/sovereign-router-v05/state
RUN_DIR=$STATE/battery-runs
OUT=$RUN_DIR/tooling-battery-$DATE
mkdir -p "$RUN_DIR"

# battery script rides the state volume (container rootfs is read-only)
cp "$(dirname "$0")/tooling_battery.py" "$RUN_DIR/tooling_battery.py"
chown -R 10001:10001 "$RUN_DIR"

KEY=$(grep '^openclaw=' "$STATE/../secrets/client-keys.txt" | cut -d= -f2-)
exec docker exec -e SR_BATTERY_KEY="$KEY" sovereign-router-v05-gateway \
  sh -c "cd /app/state/battery-runs && python3 tooling_battery.py \
    --base-url http://127.0.0.1:6075/v1 --registry /app/registry.json \
    --out /app/state/battery-runs/tooling-battery-$DATE \
    --pace ${SR_PACE:-2} \
    --provider-pace ${SR_PROVIDER_PACE:-cerebras=13} \
    --resume --emit-registry-patch $*"
# afterwards: review $OUT.md, and turn $OUT-registry-patch.json into a
# registry PR (scores + verified_until only ever move through review).
