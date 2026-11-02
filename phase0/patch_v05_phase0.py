#!/usr/bin/env python3
"""
patch_v05_phase0.py — the ONE allowed Phase 0 patch revision to v0.3.3
(spec v0.5.3, Phase 0: "Limited to one patch revision of v0.3.3, with every
rule change behind a flag that can be switched off in the registry.")

STATUS: DESIGN + REVIEWABLE CODE ONLY. This file has NOT been applied to
any deployment, and must not be until Jordan approves the Phase 0 patch
window. Rollback for the real deployment is the previous patch file +
registry from /appdata backups (spec, Rollback).

WHAT IT CONTAINS
----------------
1. Three reference implementations (pure Python, unit-testable here without
   the v0.3.3 source):
     a) combined_keyword_verb_rule()  — replaces "infra keyword = complex"
     b) sensitivity_scan()            — known-format detectors, LOG-ONLY
     c) validate_output()             — malformed tool calls + length cutoffs
2. The registry flag block to add to the v0.3.3 registry (all default OFF).
3. An anchor-asserted patch table in the house style of patch_v033.py:
   every substitution names its anchor string, asserts the anchor occurs
   EXACTLY ONCE in the target source before replacing, and aborts the whole
   patch if any assertion fails (no partial patching). Anchors marked
   ANCHOR-TODO must be re-verified against the live v0.3.3 source at apply
   time — they are written from the v0.3.3 behaviour described in the spec
   and the 2026-10-31 action log, not copied from the running container.

FLAGS (registry, all default false — behaviour is identical to stock
v0.3.3 until a flag is switched on):
  phase0_combined_rule_enabled    (a) use combined keyword+verb complexity
  phase0_sensitivity_scan_logonly (b) run the scan, log COUNTS ONLY, never
                                      the matched text, never reroutes
  phase0_output_checks_enabled    (c) validate completions; on failure log
                                      an output_check_failed event (the
                                      escalation itself lands in v0.5 —
                                      Phase 0 only measures the rate)

Run `python3 patch_v05_phase0.py --self-test` to exercise the three
reference implementations against the inline cases.
Run `python3 patch_v05_phase0.py --dry-run TARGET.py` to check every
anchor against a copy of the v0.3.3 source WITHOUT writing anything.
Writing a patched copy requires --output PATH and is still a manual,
reviewed step — this script never edits a file in place.
"""

import argparse
import json
import re
import sys

# ==========================================================================
# (a) Combined keyword + verb rule — spec, Difficulty classifier / Phase 0:
# "an infrastructure term raises the level only when combined with a change
# verb (deploy, migrate, delete, restore, rotate) or a diagnosis verb
# (debug, fix, why, root cause)"
# ==========================================================================

INFRA_TERMS = {
    "docker", "container", "containers", "proxmox", "backup", "backups",
    "snapshot", "nginx", "dns", "firewall", "portainer", "n8n", "server",
    "stack", "systemd", "ssh", "vm", "database", "postgres", "tunnel",
    "gateway", "router", "cron", "disk", "network",
}
CHANGE_VERBS = {
    "deploy", "migrate", "delete", "remove", "restore", "rotate",
    "restart", "reboot", "upgrade", "install", "rebuild", "reconfigure",
    "move", "switch", "cutover", "rollback", "provision", "wipe",
}
DIAGNOSIS_VERBS = {
    "debug", "fix", "why", "diagnose", "troubleshoot", "failing",
    "failed", "failure", "error", "broken", "crash", "down", "leak",
    "investigate",
}


def combined_keyword_verb_rule(text: str, base_complex: bool = False) -> dict:
    """Return the complexity verdict for one user message.

    complex=True ONLY when an infra term co-occurs with a change verb or a
    diagnosis verb (or the caller already knows it is complex for another
    reason, e.g. size). An infra mention on its own — "is docker running?",
    "show me the proxmox backup status" — is NOT complex. That single change
    is the Phase 0 fix for the v0.3.3 over-classification that sends most
    Hermes traffic to paid models.
    """
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    infra = sorted(words & INFRA_TERMS)
    verbs = sorted(words & (CHANGE_VERBS | DIAGNOSIS_VERBS))
    complex_ = base_complex or bool(infra and verbs)
    return {
        "complex": complex_,
        "infra_terms": infra,
        "trigger_verbs": verbs,
        "reason": (
            "infra+verb" if infra and verbs
            else "base" if base_complex
            else "infra-mention-only" if infra
            else "no-infra-signal"
        ),
    }


# ==========================================================================
# (b) Sensitivity scan — LOG-ONLY in Phase 0 (spec, Request pipeline +
# Phase 0). Known-format detectors in the style of gitleaks rules, NOT raw
# entropy. Plain hex hashes, UUIDs, container IDs and commit SHAs are
# excluded (they are explicitly stripped before scanning and no detector
# matches them). Returns detector NAMES only — never the matched text —
# so the log line is safe to keep.
# ==========================================================================

DETECTORS = {
    "openai-style-key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "anthropic-style-key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "google-api-key": re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    "pem-private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "bearer-token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "password-assignment": re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*\S{6,}"),
    "basic-auth-in-url": re.compile(r"://[^/\s:@]{2,}:[^/\s:@]{4,}@"),
}

# Shapes that must never trip the scan (spec): strip before scanning.
_BENIGN_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"  # UUID
    r"|\b[0-9a-f]{64}\b"          # sha256
    r"|\b[0-9a-f]{40}\b"          # sha1 / commit SHA
    r"|\b[0-9a-f]{12,64}\b(?=\s|$)"  # bare hex ids / container ids (conservative tail case)
)


def sensitivity_scan(text: str) -> dict:
    """Log-only scan. Returns {sensitive: bool, detectors: [names]}.

    Phase 0 use: increment a counter per detector name, sample-review for
    false positives, change NO routing. The v0.5 source tree turns a match
    into data class 'sensitive' once Jordan clears providers for it.
    """
    cleaned = _BENIGN_RE.sub(" ", text or "")
    hits = [name for name, rx in DETECTORS.items() if rx.search(cleaned)]
    return {"sensitive": bool(hits), "detectors": hits}


# ==========================================================================
# (c) Output checks (spec, Output validation) — Phase 0 logs the failure
# rate; v0.5 adds the one-level escalation retry.
# ==========================================================================

def validate_output(completion: dict, requested_tools=None, json_mode: bool = False) -> dict:
    """Validate one (non-streamed, or buffered-to-completion) completion.

    completion: {"content": str|None, "finish_reason": str,
                 "tool_calls": [{"name": str, "arguments": str|dict}]}
    Returns {"ok": bool, "failures": [check names]}.
    """
    failures = []
    content = completion.get("content") or ""
    tool_calls = completion.get("tool_calls") or []
    finish = completion.get("finish_reason")

    if not content.strip() and not tool_calls:
        failures.append("empty_answer")
    if finish == "length" and not content.strip() and not tool_calls:
        failures.append("length_cutoff_no_content")

    allowed = {t.get("name") for t in (requested_tools or []) if isinstance(t, dict)}
    for call in tool_calls:
        name = call.get("name")
        if allowed and name not in allowed:
            failures.append(f"tool_call_unknown_function:{name}")
        args = call.get("arguments")
        if isinstance(args, str):
            try:
                json.loads(args or "{}")
            except Exception:
                failures.append(f"tool_call_malformed_json:{name}")
        elif args is not None and not isinstance(args, dict):
            failures.append(f"tool_call_bad_arguments_type:{name}")

    if json_mode and content.strip():
        try:
            json.loads(content)
        except Exception:
            failures.append("json_mode_unparseable")

    return {"ok": not failures, "failures": failures}


# ==========================================================================
# Registry flag block — insert into the v0.3.3 registry (all OFF).
# ==========================================================================

REGISTRY_FLAGS = {
    "phase0_combined_rule_enabled": False,
    "phase0_sensitivity_scan_logonly": False,
    "phase0_output_checks_enabled": False,
}

# ==========================================================================
# Anchor-asserted patch table (house style of patch_v033.py).
# Each entry: (flag, anchor, replacement). ANCHOR-TODO: re-verify the
# anchor text against the live v0.3.3 source before applying — if an
# anchor does not occur exactly once, STOP; do not improvise a new anchor
# during the patch window.
# ==========================================================================

PATCHES = [
    (
        "phase0_combined_rule_enabled",
        # ANCHOR-TODO: v0.3.3 keyword complexity check (any infra keyword in
        # COMPLEX_KEYWORDS marks the request complex).
        'COMPLEX_KEYWORDS = ["docker", "proxmox", "backup", "container", "nginx", "firewall"]',
        'COMPLEX_KEYWORDS = ["docker", "proxmox", "backup", "container", "nginx", "firewall"]\n'
        '# Phase 0 (flag phase0_combined_rule_enabled): when the flag is on,\n'
        '# is_complex() must ALSO require a change/diagnosis verb — see\n'
        '# combined_keyword_verb_rule() in patch_v05_phase0.py. An infra\n'
        '# mention alone no longer marks a request complex.',
    ),
    (
        "phase0_sensitivity_scan_logonly",
        # ANCHOR-TODO: the point in the v0.3.3 request path just after the
        # body is normalised and before model selection.
        "    # --- model selection ---",
        "    # --- model selection ---\n"
        "    # Phase 0 (flag phase0_sensitivity_scan_logonly): run\n"
        "    # sensitivity_scan() from patch_v05_phase0.py over the last user\n"
        "    # message; log ONLY {sensitive, detectors} counts. No routing\n"
        "    # change, and the matched text is never logged.",
    ),
    (
        "phase0_output_checks_enabled",
        # ANCHOR-TODO: the point where a completed upstream response is
        # about to be returned to the client.
        "    return upstream_response",
        "    # Phase 0 (flag phase0_output_checks_enabled): run\n"
        "    # validate_output() from patch_v05_phase0.py; on failure log an\n"
        "    # output_check_failed event with the check names. Phase 0 does\n"
        "    # NOT escalate or retry — it measures the rate for v0.5.\n"
        "    return upstream_response",
    ),
]


def check_anchors(source: str) -> list:
    """Return a list of problems; empty list means every anchor is unique."""
    problems = []
    for flag, anchor, _ in PATCHES:
        count = source.count(anchor)
        if count != 1:
            problems.append(f"{flag}: anchor occurs {count}x (need exactly 1): {anchor[:70]!r}")
    return problems


def apply_patches(source: str, enabled_flags: set) -> str:
    """Apply ONLY the patches whose flag is enabled, asserting anchors.

    Aborts (ValueError) before changing anything if any enabled patch's
    anchor is not unique — the caller writes nothing in that case.
    """
    selected = [(f, a, r) for f, a, r in PATCHES if f in enabled_flags]
    for flag, anchor, _ in selected:
        if source.count(anchor) != 1:
            raise ValueError(f"ABORT: anchor for {flag} not unique; nothing applied")
    for flag, anchor, replacement in selected:
        source = source.replace(anchor, replacement, 1)
    return source


# ==========================================================================
# Self-test for the reference implementations
# ==========================================================================

SELF_TESTS = [
    # (case, fn result check)
    ("is docker running?", lambda r: r["complex"] is False),                       # (a) infra mention only
    ("show me the proxmox backup status", lambda r: r["complex"] is False),        # (a) infra mention only
    ("restart the docker container", lambda r: r["complex"] is True),              # (a) infra + change verb
    ("why does the nginx container keep failing?", lambda r: r["complex"] is True),# (a) infra + diagnosis
    ("summarize this email", lambda r: r["complex"] is False),                      # (a) no infra signal
]


def self_test() -> int:
    ok = True
    for text, check in SELF_TESTS:
        result = combined_keyword_verb_rule(text)
        passed = check(result)
        ok &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] rule {text!r} -> {result['reason']}")
    scan_cases = [
        ("my key is sk-abcdefghijklmnopqrstuvwxyz0123456789", True),
        ("commit a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2 landed", False),  # sha1 excluded
        ("container 3f9a1b2c77d1 is up", False),                               # hex id excluded
        ("id 550e8400-e29b-41d4-a716-446655440000 done", False),               # UUID excluded
        ("password: hunter2-hunter2", True),
        ("-----BEGIN PRIVATE KEY-----", True),
    ]
    for text, expected in scan_cases:
        result = sensitivity_scan(text)
        passed = result["sensitive"] is expected
        ok &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] scan {text[:40]!r} -> {result['detectors']}")
    out_cases = [
        ({"content": "", "finish_reason": "stop", "tool_calls": []}, None, False, False),
        ({"content": "", "finish_reason": "length", "tool_calls": []}, None, False, False),
        ({"content": "hi", "finish_reason": "stop", "tool_calls": []}, None, False, True),
        ({"content": "", "finish_reason": "stop",
          "tool_calls": [{"name": "run", "arguments": "{not json"}]},
         [{"name": "run"}], False, False),
        ({"content": "", "finish_reason": "stop",
          "tool_calls": [{"name": "nope", "arguments": "{}"}]},
         [{"name": "run"}], False, False),
        ({"content": "{bad json", "finish_reason": "stop", "tool_calls": []}, None, True, False),
    ]
    for completion, tools, json_mode, expected in out_cases:
        result = validate_output(completion, tools, json_mode)
        passed = result["ok"] is expected
        ok &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] output -> {result['failures'] or 'ok'}")
    print("SELF-TEST:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dry-run", metavar="TARGET", help="check anchors against TARGET, write nothing")
    ap.add_argument("--output", metavar="PATH", help="with --dry-run off: write patched copy here (manual step)")
    ap.add_argument("--enable", action="append", default=[], choices=list(REGISTRY_FLAGS),
                    help="flag to enable (repeatable); default none")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.dry_run:
        source = open(args.dry_run).read()
        problems = check_anchors(source)
        if problems:
            print("ANCHOR CHECK FAILED:")
            [print(" -", p) for p in problems]
            return 1
        print("All anchors unique. Dry run only — nothing written.")
        return 0
    if args.output:
        print("Refusing to guess a target: pass the v0.3.3 source copy explicitly "
              "via --dry-run first, review, then apply in the patch window.")
        return 2
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
