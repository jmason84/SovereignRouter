"""Sensitivity scan — known-format credential detectors (gitleaks style).

Spec ("Request pipeline", step 3): detectors for *known formats* —
provider key prefixes, cloud access keys, PEM private-key blocks, bearer
tokens, password/secret assignments — NOT raw entropy. Plain hex hashes,
UUIDs, container IDs and commit SHAs are explicitly EXCLUDED: they are
normal infrastructure text for Hermes/OpenClaw and must not reroute.
v0.9.1 (F-18) adds tight prose-stated assignments ("the database
password … is <value>") after the 2026-11-15 golden replay showed that
dialect classifying internal and first-routing to public lanes.

A match raises the request to ``data_class=sensitive``. The match text
itself is never logged or stored — only detector names are returned, and
only the names may appear in logs/telemetry.

Log-only mode (``log_only=True``, the Phase 0 trial): matches are counted
and returned for logging but the caller must not change routing on them,
so the false-positive rate can be measured before the scan affects lanes.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

# ----------------------------------------------------------------------
# Exclusions first: if an assignment *value* is exactly one of these
# shapes it is an identifier, not a credential.
# ----------------------------------------------------------------------
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX_RE = re.compile(r"^[0-9a-fA-F]{12,64}$")  # container IDs, SHAs, hashes

# ----------------------------------------------------------------------
# Known-format detectors. Each pattern is anchored to a distinctive
# prefix/format so ordinary prose, hashes and IDs cannot match.
# ----------------------------------------------------------------------
_PREFIX_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # OpenAI-style keys (also covers sk-proj-, sk-ant- via the sk- branch)
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9]{16,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret_context", re.compile(r"\baws_secret_access_key\s*[:=]", re.IGNORECASE)),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("gitlab_pat", re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("pem_private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE)),
    ("basic_auth_url", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s/@]{4,}@")),
]

# password=/secret=/api_key= style assignments. The value is checked
# against the exclusion shapes before it counts. ``session`` joined the
# noun list in v0.9.1 (F-18): a session-cookie value IS a credential
# (golden g180), and identifier-shaped session values (UUID/hex) stay
# excluded by the value checks below.
_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(password|passwd|pwd|secret|client_secret|api[_-]?key|"
    r"access[_-]?token|auth[_-]?token|session)"
    r"\s*[:=]\s*[\"']?([^\s\"',;]{4,})",
    re.IGNORECASE,
)

# Prose-stated credential assignments (v0.9.1, F-18). The golden replay
# of 2026-11-15 (Group S) proved the shape-keyed detectors miss
# credential VALUES stated in plain sentences — "the database password
# for the reporting user is Hunter2-…", "the oauth client secret for
# the test app: FAKE-…", "a user shared their account password in
# chat: …" all classified internal and two first-routed PUBLIC. The
# pattern is deliberately tight: a credential noun, a bounded filler
# gap of at most 6 plain words, a separator (``:``, ``=`` or the word
# ``is``), then a value token that must ALSO pass the token-shape gate
# below — a plain lowercase English word ("never", "stored",
# "required") is a sentence, not a credential.
_PROSE_CREDENTIAL_RE = re.compile(
    r"\b(?P<noun>client[\s_-]?secret|session[\s_-]?cookie|password|passwd|pwd|"
    r"secret|cookie|api[\s_-]?key|access[\s_-]?token|auth[\s_-]?token)\b"
    r"(?P<filler>(?:\s+[A-Za-z][A-Za-z\-']*){0,6}?)"
    r"\s*(?P<sep>:|=|\bis\b)\s*[\"']?(?P<value>[^\s\"',;]{4,})",
    re.IGNORECASE,
)

# OpenClaw session keys (v0.10.4, 2026-11-27). OpenClaw's system prompt
# carries a runtime line on EVERY request -- "Runtime: agent=main |
# session=agent:main:main | host=... | model=sovereign/auto" -- and its
# /status output prints "Session: agent:devops-agent:main". The value is
# a colon-delimited routing identifier (agent:<agentId>:<scope>[:...]),
# not a credential, but the bare ``session`` noun (added in v0.9.1, F-18)
# matched it, flagging ~97% of OpenClaw traffic sensitive from
# 2026-11-15 04:00 MST (Job ROUTER-J1-20261127: 1,173/1,207 requests in
# 7 days, every one credential_assignment). The exclusion is applied to
# the ``session`` noun ONLY and only to this exact shape: random cookie
# or token values (session=<random>, session=s:<id>.<sig>, Cookie:
# session=...) do not start with "agent:" and keep flagging (g180).
_OPENCLAW_SESSION_KEY_RE = re.compile(
    r"^agent:[A-Za-z0-9_.\-]+(?::[A-Za-z0-9_.@+\-]+)+$"
)

# Markdown/prose wrapping around the value ("`session=agent:main:main`",
# "**…**", "(…)", a sentence-final period) is not part of the key.
_SESSION_KEY_WRAP_LEFT = "`*([{<\"'"
_SESSION_KEY_WRAP_RIGHT = "`*)]}>\"'.!?|"
# Cookie headers are never exempt: "Cookie: session=agent:…" keeps
# flagging, whatever the value looks like (E2E fuzz finding, 2026-11-27).
_COOKIE_CONTEXT_RE = re.compile(r"cookie", re.IGNORECASE)


def _is_openclaw_session_key(text: str, m: "re.Match[str]",
                             cookie_starts: list[int]) -> bool:
    """True only for an OpenClaw session key (agent:<id>:<scope>[:…]) in
    a non-cookie context. Applies to the bare ``session`` noun only.
    ``cookie_starts`` is the sorted list of every "cookie" offset in
    ``text`` (computed once per scan, so many matches on one huge line
    stay O(n log n) -- an unbounded per-match search was quadratic: 34 s
    for a 900k-char single-line prompt in the E2E perf run, 2026-11-27)."""
    value = m.group(2).lstrip(_SESSION_KEY_WRAP_LEFT).rstrip(_SESSION_KEY_WRAP_RIGHT)
    if not _OPENCLAW_SESSION_KEY_RE.match(value):
        return False
    i = bisect.bisect_right(cookie_starts, m.start()) - 1
    if i >= 0 and text.find("\n", cookie_starts[i], m.start()) == -1:
        return False   # a "cookie" earlier on the same line: never exempt
    return True


# Hyphenated English that follows credential nouns in ordinary prose
# but is never a credential value.
_PROSE_VALUE_STOPLIST = {"case-sensitive", "case-insensitive"}


def _looks_like_credential_value(value: str) -> bool:
    """Token-shape gate for prose captures: credential values are
    token-like (a digit, a hyphen/underscore join, or mixed case).
    A single plain lowercase word is prose, not a credential."""
    v = value.strip().strip("\"'")
    if v.lower() in _PROSE_VALUE_STOPLIST:
        return False
    if any(c.isdigit() for c in v):
        return True
    if "-" in v or "_" in v:
        return True
    return any(c.isupper() for c in v) and any(c.islower() for c in v)

# Placeholder values that show up in docs/examples are not credentials.
_PLACEHOLDERS = {
    "xxx", "xxxx", "changeme", "example", "password", "secret", "redacted",
    "none", "null", "true", "false", "your_password", "your-password",
}


@dataclass
class ScanResult:
    sensitive: bool
    detectors: list[str] = field(default_factory=list)   # names only, never the match
    # v0.10.9 diagnostics: for assignment-style hits, "<noun>=<shape>"
    # where shape is the value's first 12 chars reduced to character
    # classes with run lengths ("agent:main:main" -> "a5:a4:a3"). Never
    # the value itself. Lets a false-positive source be found from the
    # route log alone (ROUTER-STRESS-20261128: 227/243 OpenClaw turns in
    # 24 h still flagged credential_assignment on live v0.10.3).
    match_shapes: list[str] = field(default_factory=list)


def value_shape(value: str, limit: int = 12) -> str:
    out: list[str] = []
    prev, run = "", 0
    for ch in value[:limit]:
        c = "9" if ch.isdigit() else "A" if ch.isupper() else "a" if ch.isalpha() \
            else ch if ch in ":=._-/@+" else "?"
        if c == prev and c in "9Aa":
            run += 1
            continue
        if prev:
            out.append(prev + (str(run) if prev in "9Aa" and run > 1 else ""))
        prev, run = c, 1
    if prev:
        out.append(prev + (str(run) if prev in "9Aa" and run > 1 else ""))
    return "".join(out) + ("~" if len(value) > limit else "")


def _is_excluded_value(value: str) -> bool:
    v = value.strip().strip("\"'")
    if _UUID_RE.match(v) or _HEX_RE.match(v):
        return True
    if v.lower() in _PLACEHOLDERS:
        return True
    # Angle-bracket / templated placeholders, e.g. <password>, ${SECRET}
    if v.startswith("<") or v.startswith("${") or v.startswith("{{"):
        return True
    # Ellipsis-redacted displays, e.g. sk-… or abc...xyz, are how docs
    # and UIs show a credential WITHOUT stating it (v0.9.1, F-18
    # negative case: the extended battery's redacted fake-key turn).
    if "…" in v or "..." in v:
        return True
    return False


def scan_text(text: str) -> ScanResult:
    """Scan one text blob. Returns detector names only."""
    if not text:
        return ScanResult(sensitive=False)
    hits: list[str] = []
    shapes: list[str] = []
    for name, pattern in _PREFIX_PATTERNS:
        if pattern.search(text):
            hits.append(name)
    cookie_starts: list[int] | None = None
    for m in _ASSIGNMENT_RE.finditer(text):
        if m.group(1).lower() == "session":
            if cookie_starts is None:
                cookie_starts = [c.start() for c in _COOKIE_CONTEXT_RE.finditer(text)]
            if _is_openclaw_session_key(text, m, cookie_starts):
                continue   # OpenClaw session key, not a credential (v0.10.4)
        if not _is_excluded_value(m.group(2)):
            hits.append("credential_assignment")
            shapes.append(f"{m.group(1).lower()}={value_shape(m.group(2))}")
            break
    for m in _PROSE_CREDENTIAL_RE.finditer(text):
        value = m.group("value")
        # Idiom guard: "the secret to/of <topic> is <thing>" is an
        # English figure of speech, not a credential statement.
        if m.group("noun").lower() == "secret" \
                and m.group("sep").lower() == "is":
            filler_words = m.group("filler").split()
            if filler_words and filler_words[0].lower() in ("to", "of"):
                continue
        if not _is_excluded_value(value) and _looks_like_credential_value(value):
            hits.append("credential_prose_assignment")
            shapes.append(f"{m.group('noun').lower()}~{value_shape(value)}")
            break
    return ScanResult(sensitive=bool(hits), detectors=sorted(set(hits)),
                      match_shapes=shapes)


def scan_messages(messages: list[dict]) -> ScanResult:
    """Scan every text part of an OpenAI-style messages list."""
    detectors: set[str] = set()
    shapes: list[str] = []
    for msg in messages or []:
        content = msg.get("content")
        texts: list[str] = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        for t in texts:
            r = scan_text(t)
            detectors.update(r.detectors)
            for sh in r.match_shapes:
                if len(shapes) < 3 and sh not in shapes:
                    shapes.append(f"{msg.get('role')}:{sh}")
    return ScanResult(sensitive=bool(detectors), detectors=sorted(detectors),
                      match_shapes=shapes)


# ----------------------------------------------------------------------
# PII detectors (v0.10.10, ROUTER-STRESS-20261128) — OWNER POLICY GATE.
# The 2026-11-28 stress matrix confirmed the gap noted in
# OPERATIONS-REVIEW-2026-11-11 item 8: a US SSN or a payment-card number
# is not a credential, so the scan above leaves it ``internal`` and it
# can be first-routed to a public-only-free lane. Two tight detectors:
#   * pii_ssn: hyphenated NNN-NN-NNNN only (bare 9-digit runs are too
#     common in IDs), with the SSA's never-issued blocks excluded
#     (area 000/666/9xx, group 00, serial 0000);
#   * pii_payment_card: 13-19 digits (optional single space/hyphen
#     separators) that pass the Luhn checksum AND carry a major-network
#     prefix (Visa 4, Mastercard 51-55/2221-2720, Amex 34/37,
#     Discover 6011/65) — order IDs and phone numbers fail one or both.
# Routing effect is decided by the gateway flag SOVEREIGN_PII_DETECTORS
# (default off = log-only signal on the route row), because promoting
# PII to ``sensitive`` moves those turns off the free public lanes and
# can raise paid spend — that trade is the owner's call.
# ----------------------------------------------------------------------
_SSN_RE = re.compile(r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])")
_CARD_RE = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _card_prefix_ok(d: str) -> bool:
    n = len(d)
    if d[0] == "4" and n in (13, 16, 19):
        return True
    if n == 16 and (51 <= int(d[:2]) <= 55 or 2221 <= int(d[:4]) <= 2720):
        return True
    if n == 15 and d[:2] in ("34", "37"):
        return True
    if n in (16, 19) and (d.startswith("6011") or d.startswith("65")):
        return True
    return False


def scan_pii_text(text: str) -> list[str]:
    """PII detector names found in one text blob (names only)."""
    if not text or not any(c.isdigit() for c in text):
        return []
    hits: list[str] = []
    if _SSN_RE.search(text):
        hits.append("pii_ssn")
    for m in _CARD_RE.finditer(text):
        digits = re.sub(r"[ -]", "", m.group(0))
        if _card_prefix_ok(digits) and _luhn_ok(digits):
            hits.append("pii_payment_card")
            break
    return hits


def scan_pii_messages(messages: list[dict]) -> list[str]:
    """PII detector names across every text part of a messages list."""
    found: set[str] = set()
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            found.update(scan_pii_text(content))
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    found.update(scan_pii_text(part["text"]))
    return sorted(found)
