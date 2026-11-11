#!/usr/bin/env python3
"""Check that the deployed registry is byte-identical to the repo copy.

Usage: registry_sync.py [STATE_DIR]   (default: $SOVEREIGN_STATE_DIR)

Exit 0 if identical (or if no state copy exists — the gateway then uses
the bundled file), 1 if they differ, 2 if the repo copy is not valid JSON.
Principles rule 9: src/registry.json is the source of truth and the
state-dir copy must not drift from it.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "src" / "registry.json"


def main(argv: list[str]) -> int:
    try:
        json.loads(REPO.read_text(encoding="utf-8"))
    except ValueError as e:
        print(f"repo registry.json is not valid JSON: {e}")
        return 2
    state = Path(argv[1] if len(argv) > 1 else os.environ.get("SOVEREIGN_STATE_DIR", "./state"))
    deployed = state / "registry.json"
    if not deployed.exists():
        print(f"no {deployed}; gateway uses the bundled registry — OK")
        return 0
    a = hashlib.sha256(REPO.read_bytes()).hexdigest()
    b = hashlib.sha256(deployed.read_bytes()).hexdigest()
    if a == b:
        print(f"registry in sync ({a[:12]})")
        return 0
    print(f"REGISTRY DRIFT: repo {a[:12]} != deployed {b[:12]} ({deployed})")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
