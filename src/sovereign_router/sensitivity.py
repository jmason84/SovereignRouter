"""Local secret scan. A match raises the request to data class 'sensitive'.

v0.5.3: known-format detectors (gitleaks style) only by default. The raw
entropy check is opt-in (`entropy=True`) because infra traffic is full of
hashes and IDs that look random. Hex digests and UUIDs are always excluded.
The matched text is never returned or logged; callers only get a bool.

Mode (env SOVEREIGN_SECRET_SCAN): "route" (default) raises the data class;
"log" only records that a match happened, to measure false positives; "off".
"""
from __future__ import annotations

import math
import re

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{24,}"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
]
_TOKEN = re.compile(r"[A-Za-z0-9+/_\-]{32,}")
_HEX = re.compile(r"^[0-9a-fA-F\-]+$")


def _entropy(s: str) -> float:
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def contains_secret(text: str, *, entropy: bool = False) -> bool:
    if not text:
        return False
    if any(p.search(text) for p in _PATTERNS):
        return True
    if not entropy:
        return False
    for tok in _TOKEN.findall(text):
        if _HEX.match(tok):
            continue
        has_mixed = any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)
        if has_mixed and _entropy(tok) > 4.3:
            return True
    return False


def messages_text(messages: list) -> str:
    parts = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(p.get("text", "") for p in c if isinstance(p, dict))
    return "\n".join(parts)
