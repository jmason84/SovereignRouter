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
    for name, pattern in _PREFIX_PATTERNS:
        if pattern.search(text):
            hits.append(name)
    for m in _ASSIGNMENT_RE.finditer(text):
        if m.group(1).lower() == "session" and _OPENCLAW_SESSION_KEY_RE.match(
                m.group(2).strip().strip("\"'")):
            continue   # OpenClaw session key, not a credential (v0.10.4)
        if not _is_excluded_value(m.group(2)):
            hits.append("credential_assignment")
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
            break
    return ScanResult(sensitive=bool(hits), detectors=sorted(set(hits)))


def scan_messages(messages: list[dict]) -> ScanResult:
    """Scan every text part of an OpenAI-style messages list."""
    detectors: set[str] = set()
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
            detectors.update(scan_text(t).detectors)
    return ScanResult(sensitive=bool(detectors), detectors=sorted(detectors))
