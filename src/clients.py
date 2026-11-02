"""Client policies and authentication for Sovereign Router v0.5.

Auth keeps the v0.3 pattern: clients present a bearer key; the gateway
stores only its sha256 hash in the clients file and compares with
``hmac.compare_digest``. Raw keys never appear in code, config or logs.

Client policies come straight from the spec's "Client policies" table:

  =========  ==================  ======================  =======  ======
  Client     Default data class  Lanes                   Premium  Target
  =========  ==================  ======================  =======  ======
  Hermes     internal            private-free, paid      No       $1.00
  OpenClaw   internal            private-free, paid,     Yes*     $3.00
                                 premium
  n8n        internal            private-free, paid      No       $0.50
  =========  ==================  ======================  =======  ======

  * OpenClaw premium: level 5 or explicit request only, with its own
    alerts — $20/month premium spend and >20 premium requests/day
    (both proposed in the spec, set by the owner before Phase 3).

Daily targets are *steering targets, not caps*: a request is never
refused for budget reasons (spec decision, Jordan 2026-10-31).

Aliases (body ``model`` field): auto, cheap, strong, premium, code, long.
The header ``X-Sovereign-Level: 1..5`` is an explicit level override.
Overrides are logged in the route line.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class ClientPolicy:
    id: str
    key_sha256: str
    default_data_class: str = "internal"      # internal | public | sensitive
    lanes: tuple[str, ...] = ("private-free", "paid")
    premium_allowed: bool = False
    daily_target_usd: float = 1.0
    premium_monthly_alert_usd: Optional[float] = None
    premium_daily_count_alert: Optional[int] = None
    # Providers the owner has cleared to receive this client's sensitive
    # prompts. Empty until the owner decides (spec: open decision).
    sensitive_cleared_providers: tuple[str, ...] = ()

    def lane_allowed(self, lane: str) -> bool:
        return lane in self.lanes


@dataclass(frozen=True)
class AliasSpec:
    """What an explicit model alias asks for."""

    level: Optional[int] = None               # forced difficulty level
    task_class: Optional[str] = None          # forced task class
    explicit_premium: bool = False            # counts as an explicit premium request


# Spec aliases: auto/cheap/strong/premium/code/long.
ALIASES: dict[str, AliasSpec] = {
    "auto": AliasSpec(),
    "cheap": AliasSpec(level=2),
    "strong": AliasSpec(level=4),
    "premium": AliasSpec(level=5, explicit_premium=True),
    "code": AliasSpec(level=3, task_class="code"),
    "long": AliasSpec(level=3, task_class="long-context"),
}


@dataclass
class ClientStore:
    clients: dict[str, ClientPolicy] = field(default_factory=dict)
    admin_key_sha256: Optional[str] = None

    # ------------------------------------------------------------------
    def authenticate(self, presented_key: Optional[str]) -> Optional[ClientPolicy]:
        if not presented_key:
            return None
        digest = sha256_hex(presented_key)
        for policy in self.clients.values():
            if hmac.compare_digest(digest, policy.key_sha256):
                return policy
        return None

    def is_admin(self, presented_key: Optional[str]) -> bool:
        if not presented_key or not self.admin_key_sha256:
            return False
        return hmac.compare_digest(sha256_hex(presented_key), self.admin_key_sha256)

    def get(self, client_id: str) -> Optional[ClientPolicy]:
        return self.clients.get(client_id)


def load_clients(path: str | Path) -> ClientStore:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    store = ClientStore(admin_key_sha256=data.get("admin_key_sha256"))
    for raw in data.get("clients", []):
        policy = ClientPolicy(
            id=raw["id"],
            key_sha256=raw["key_sha256"],
            default_data_class=raw.get("default_data_class", "internal"),
            lanes=tuple(raw.get("lanes", ["private-free", "paid"])),
            premium_allowed=bool(raw.get("premium_allowed", False)),
            daily_target_usd=float(raw.get("daily_target_usd", 1.0)),
            premium_monthly_alert_usd=raw.get("premium_monthly_alert_usd"),
            premium_daily_count_alert=raw.get("premium_daily_count_alert"),
            sensitive_cleared_providers=tuple(raw.get("sensitive_cleared_providers", [])),
        )
        store.clients[policy.id] = policy
    return store


def extract_bearer_key(authorization: Optional[str],
                       api_key_header: Optional[str] = None) -> Optional[str]:
    """Accept ``Authorization: Bearer <key>`` or ``x-api-key: <key>``
    (OpenAI-compatible clients use both in the wild)."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if api_key_header:
        return api_key_header.strip()
    return None
