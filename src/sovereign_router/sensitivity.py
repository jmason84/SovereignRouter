"""Local secret scan, in the style of gitleaks rules: known credential formats only.

No raw-entropy check (v0.5.3): infra traffic is full of hashes, UUIDs, container IDs and
commit SHAs and entropy rules flag them. A match raises the request to data class
'sensitive'; the matched text is never returned or logged, callers only get a bool.
Run with SOVEREIGN_SECRET_SCAN=log-only to count matches without changing routing."""
from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bsk-(?:ant-|proj-|or-v1-)?[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
    re.compile(r"(?i)\bbearer\s+(?=[A-Za-z0-9._\-]{24,})(?![0-9a-f]{24,}\b)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
]


def contains_secret(text: str) -> bool:
    return bool(text) and any(p.search(text) for p in _PATTERNS)


def messages_text(messages: list) -> str:
    parts = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(p.get("text", "") for p in c if isinstance(p, dict))
    return "\n".join(parts)
