"""Sensitivity scan — known-format credential detectors (gitleaks style).

Spec ("Request pipeline", step 3): detectors for *known formats* —
provider key prefixes, cloud access keys, PEM private-key blocks, bearer
tokens, password/secret assignments — NOT raw entropy. Plain hex hashes,
UUIDs, container IDs and commit SHAs are explicitly EXCLUDED: they are
normal infrastructure text for Hermes/OpenClaw and must not reroute.

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
    # Added 2026-11-11: other distinctive, low-false-positive formats.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("sendgrid_key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}")),
    ("twilio_key", re.compile(r"\bSK[0-9a-f]{32}\b")),
    ("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("digitalocean_token", re.compile(r"\bdop_v1_[a-f0-9]{64}\b")),
    ("azure_storage_key", re.compile(r"\bAccountKey=[A-Za-z0-9+/=]{40,}")),
    ("basic_auth_url", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s/@]{4,}@")),
]

# password=/secret=/api_key= style assignments. The value is checked
# against the exclusion shapes before it counts.
_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(password|passwd|pwd|secret|client_secret|api[_-]?key|"
    r"access[_-]?token|auth[_-]?token)"
    # Optional closing quote: JSON-style `"password": "..."` (agents pass
    # JSON constantly; the unquoted-only form missed it, 2026-11-11).
    r"[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{4,})",
    re.IGNORECASE,
)

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
    # Trailing JSON/call punctuation is not part of the value (`true}`).
    v = value.strip().strip("\"'").rstrip("}])").strip("\"'")
    if _UUID_RE.match(v) or _HEX_RE.match(v):
        return True
    if v.lower() in _PLACEHOLDERS:
        return True
    # Angle-bracket / templated placeholders, e.g. <password>, ${SECRET}
    if v.startswith("<") or v.startswith("${") or v.startswith("{{"):
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
        if not _is_excluded_value(m.group(2)):
            hits.append("credential_assignment")
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
        # Assistant tool-call arguments carry whatever the model was
        # given or generated — scan them like any other text.
        for call in (msg.get("tool_calls") or []):
            args = ((call or {}).get("function") or {}).get("arguments")
            if isinstance(args, str):
                texts.append(args)
            elif isinstance(args, dict):
                texts.append(str(args))
        for t in texts:
            detectors.update(scan_text(t).detectors)
    return ScanResult(sensitive=bool(detectors), detectors=sorted(detectors))
