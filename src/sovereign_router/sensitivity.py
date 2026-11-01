"""Local secret scan. A match raises the request to data class 'sensitive'.

The matched text is never returned or logged; callers only get a bool.
Hex digests and UUIDs are excluded from the entropy check because infra
traffic is full of them (container IDs, sha256 prefixes) and flagging them
would push most Hermes traffic onto sensitive-only lanes.
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
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{24,}"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
]
_TOKEN = re.compile(r"[A-Za-z0-9+/_\-]{32,}")
_HEX = re.compile(r"^[0-9a-fA-F\-]+$")


def _entropy(s: str) -> float:
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def contains_secret(text: str) -> bool:
    if not text:
        return False
    if any(p.search(text) for p in _PATTERNS):
        return True
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
