#!/usr/bin/env python3
"""Deploy preflight checks for the Sovereign Router (Cycle 1 aspect A).

Stdlib-only, read-only, and safe to run anywhere: it never writes,
restarts, or deploys anything. It encodes the checks that past deploys
learned the hard way, so they run BEFORE a registry or image change
reaches the gateway instead of being rediscovered from a crash loop:

  check-file   A registry copy must exist, parse through the gateway's
               own loader, and be READABLE BY THE GATEWAY USER. The
               gateway container runs as uid 10001; a state-dir file it
               cannot read crash-loops it (learned twice: key files and
               the state registry copy, 2026-11-09 / 2026-11-12).
  compare      The registry lives in three copies (repo, build tree,
               state dir) that must be byte-identical at deploy time;
               drift between them has shipped stale registries before.
  health       Post-deploy gate: poll /health until it reports ok AND
               the expected registry size — a gateway that answers but
               loaded the wrong registry is not healthy.
  rollback-notes
               Print the standing rollback procedure (snapshot tag,
               .bak files, compose image pin) so it is in front of the
               operator before, not after, something goes wrong.

Usage:
  python3 tools/preflight.py check-file /root/sovereign-router-v05/state/registry.json
  python3 tools/preflight.py compare src/registry.json build/src/registry.json state/registry.json
  python3 tools/preflight.py health --url http://192.168.0.166:6075 --expect-models 81 --timeout 60
  python3 tools/preflight.py rollback-notes

Exit code 0 = all checks pass, 1 = at least one failed, 2 = usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

# The gateway container's uid (docker-compose user). State-dir files the
# gateway must read — registry copy, provider key files — must be
# readable by this uid; root-owned 600 files crash-loop the gateway.
GATEWAY_UID = 10001

REPO_SRC = Path(__file__).resolve().parent.parent / "src"


def file_report(path: str | Path, expected_uid: int = GATEWAY_UID) -> dict[str, Any]:
    """Existence, loader-parse, and gateway-readability of one registry
    copy. Readability is judged from the mode bits against the gateway
    uid (owner match, group, or other-read) rather than by attempting
    the read as the current user — the checker usually runs as root,
    for whom everything is readable, which is exactly how the failure
    this guards against slipped through."""
    p = Path(path)
    report: dict[str, Any] = {"path": str(p), "ok": False, "checks": {}}
    if not p.is_file():
        report["checks"]["exists"] = "FAIL: not a file"
        return report
    report["checks"]["exists"] = "ok"
    st = p.stat()
    mode = stat.S_IMODE(st.st_mode)
    readable = bool(
        (st.st_uid == expected_uid and mode & stat.S_IRUSR)
        or (mode & stat.S_IRGRP)
        or (mode & stat.S_IROTH)
    )
    report["owner_uid"] = st.st_uid
    report["mode"] = oct(mode)
    report["checks"]["readable_by_gateway_uid"] = (
        "ok" if readable else
        f"FAIL: uid {st.st_uid} mode {oct(mode)} is not readable by "
        f"gateway uid {expected_uid} (owner 10001 or a read bit for "
        "group/other is required; root-owned 600 crash-loops the gateway)"
    )
    try:
        sys.path.insert(0, str(REPO_SRC))
        from registry import load_registry  # noqa: PLC0415

        reg = load_registry(p)
        report["entries"] = len(reg.entries)
        report["checks"]["loads_via_gateway_loader"] = "ok"
    except Exception as exc:  # noqa: BLE001 — report, don't raise
        report["checks"]["loads_via_gateway_loader"] = f"FAIL: {exc}"
    report["ok"] = all(v == "ok" for v in report["checks"].values())
    return report


def md5_of(path: str | Path) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def compare_hashes(paths: list[str | Path]) -> dict[str, Any]:
    """Byte-identity verdict across N registry copies."""
    hashes = {str(p): (md5_of(p) if Path(p).is_file() else None) for p in paths}
    present = [h for h in hashes.values() if h is not None]
    identical = len(present) == len(hashes) and len(set(present)) == 1
    return {"hashes": hashes, "identical": identical,
            "md5": present[0] if identical else None}


def health_verdict(payload: dict[str, Any], expected_models: Optional[int]) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if payload.get("status") != "ok":
        problems.append(f"status is {payload.get('status')!r}, expected 'ok'")
    if expected_models is not None and payload.get("registry_models") != expected_models:
        problems.append(
            f"registry_models is {payload.get('registry_models')!r}, "
            f"expected {expected_models} (gateway loaded a different registry)")
    return (not problems, problems)


def fetch_health(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def poll_health(url: str, expected_models: Optional[int], timeout_s: float,
                interval_s: float = 2.0,
                fetch: Callable[[str], dict[str, Any]] = fetch_health,
                sleep: Callable[[float], None] = time.sleep,
                now: Callable[[], float] = time.monotonic) -> tuple[bool, list[str]]:
    """Poll /health until the verdict passes or the timeout expires."""
    deadline = now() + timeout_s
    problems = ["no response yet"]
    while True:
        try:
            ok, problems = health_verdict(fetch(url), expected_models)
            if ok:
                return True, []
        except Exception as exc:  # noqa: BLE001 — keep polling until deadline
            problems = [f"fetch failed: {exc}"]
        if now() >= deadline:
            return False, problems
        sleep(interval_s)


ROLLBACK_NOTES = """\
Standing rollback procedure (Sovereign Router deploys):

  Before any deploy:
    1. Snapshot the VM (Proxmox snapshot named pre-v<VER>-<YYYYMMDD>).
    2. Copy the files being replaced to <name>.bak-<YYYYMMDD>-prev<VER>.
    3. Run: preflight.py check-file <state registry> and
       preflight.py compare <repo> <build> <state> registry copies.

  Registry-only rollback:
    Restore the .bak registry into ALL THREE copies (repo src,
    build/src, state dir), verify compare is identical, then restart
    the gateway container. State-dir files must stay owned by / readable
    to uid 10001 or the gateway crash-loops.

  Image rollback:
    Point docker-compose.override.yml back to the previous image tag
    and run `docker compose up -d` (an image swap needs `up -d`, not
    `restart`), then gate on:
       preflight.py health --expect-models <N from the restored registry>

  Rollback is complete only when /health reports ok AND
  registry_models matches the restored registry's entry count.
"""


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check-file", help="one registry copy: exists, loads, gateway-readable")
    p_check.add_argument("path")
    p_check.add_argument("--expected-uid", type=int, default=GATEWAY_UID)

    p_cmp = sub.add_parser("compare", help="md5 byte-identity across registry copies")
    p_cmp.add_argument("paths", nargs="+")

    p_health = sub.add_parser("health", help="poll /health until ok + expected registry size")
    p_health.add_argument("--url", required=True)
    p_health.add_argument("--expect-models", type=int, default=None)
    p_health.add_argument("--timeout", type=float, default=60.0)

    sub.add_parser("rollback-notes", help="print the standing rollback procedure")

    args = parser.parse_args(argv)

    if args.cmd == "check-file":
        report = file_report(args.path, args.expected_uid)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    if args.cmd == "compare":
        result = compare_hashes(args.paths)
        print(json.dumps(result, indent=2))
        return 0 if result["identical"] else 1
    if args.cmd == "health":
        ok, problems = poll_health(args.url, args.expect_models, args.timeout)
        print(json.dumps({"healthy": ok, "problems": problems}, indent=2))
        return 0 if ok else 1
    if args.cmd == "rollback-notes":
        print(ROLLBACK_NOTES)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
