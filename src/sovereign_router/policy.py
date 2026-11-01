"""Client authentication and per-client policy."""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path

from .registry import DATA_CLASSES, LANES


@dataclass(frozen=True)
class ClientPolicy:
    name: str
    key_sha256: str
    default_data_class: str = "internal"
    lanes: tuple = ("private-free", "paid")
    premium: bool = False
    daily_cost_target_usd: float | None = None  # steers + alerts, never a cap
    aliases: dict = field(default_factory=dict)  # real model name -> alias
    premium_monthly_alert_usd: float | None = None  # alert only (v0.5.3)
    premium_daily_requests_alert: int | None = None  # alert when exceeded (v0.5.3)


class Clients:
    def __init__(self, clients: list[ClientPolicy], admin_sha256: str | None = None):
        self._clients = clients
        self.admin_sha256 = admin_sha256

    @classmethod
    def load(cls, path: str | Path) -> "Clients":
        raw = json.loads(Path(path).read_text())
        out = []
        for c in raw["clients"]:
            if c.get("default_data_class", "internal") not in DATA_CLASSES:
                raise ValueError(f"{c['name']}: bad default_data_class")
            lanes = tuple(c.get("lanes", ("private-free", "paid")))
            bad = [x for x in lanes if x not in LANES]
            if bad:
                raise ValueError(f"{c['name']}: bad lanes {bad}")
            out.append(ClientPolicy(
                name=c["name"], key_sha256=c["key_sha256"],
                default_data_class=c.get("default_data_class", "internal"),
                lanes=lanes, premium=c.get("premium", False),
                daily_cost_target_usd=c.get("daily_cost_target_usd"),
                aliases=c.get("aliases", {}),
                premium_monthly_alert_usd=c.get("premium_monthly_alert_usd"),
                premium_daily_requests_alert=c.get("premium_daily_requests_alert"),
            ))
        return cls(out, raw.get("admin_key_sha256"))

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def authenticate(self, bearer: str | None) -> ClientPolicy | None:
        if not bearer:
            return None
        h = self._hash(bearer)
        for c in self._clients:
            if hmac.compare_digest(c.key_sha256, h):
                return c
        return None

    def is_admin(self, bearer: str | None) -> bool:
        return bool(bearer and self.admin_sha256 and hmac.compare_digest(self.admin_sha256, self._hash(bearer)))
