"""Model registry v2: lanes, per-task quality, prices, provider limits."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

LANES = ("private-free", "public-only-free", "paid", "premium")
DATA_CLASSES = ("public", "internal", "sensitive")
TASKS = ("chat", "infra", "code", "reasoning", "long-context")


def data_rank(cls: str) -> int:
    return DATA_CLASSES.index(cls)


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    model: str
    base_url: str
    lane: str
    api_key_file: str | None = None
    price_in: float = 0.0  # USD per 1M input tokens
    price_out: float = 0.0
    cached_in: float | None = None  # USD per 1M cached input tokens
    context: int = 131072
    quality: dict = field(default_factory=lambda: {"default": 3})
    limits: dict = field(default_factory=dict)  # rpm, tpm, rpd, tpd
    extra_body: dict = field(default_factory=dict)
    spend_alert_usd: float | None = None  # monthly alert, never blocks
    status: str = "verified"  # verified | candidate | disabled
    max_data_class: str = "internal"
    supports_tools: bool = True
    supports_json: bool = True
    supports_vision: bool = False
    bytes_per_token: float = 3.5
    no_retention: bool = False  # verified no-retention/no-training terms (sensitive fallback lane)
    latency_p50_ms: int | None = None
    probe: dict | None = None  # weekly probe request; failure marks the entry candidate

    @property
    def is_free(self) -> bool:
        return self.price_in == 0 and self.price_out == 0

    def q(self, task: str) -> int:
        return int(self.quality.get(task, self.quality.get("default", 3)))

    def est_cost(self, in_tokens: int, out_tokens: int, cached_tokens: int = 0) -> float:
        cached_rate = self.cached_in if self.cached_in is not None else self.price_in
        fresh = max(in_tokens - cached_tokens, 0)
        return (fresh * self.price_in + cached_tokens * cached_rate + out_tokens * self.price_out) / 1e6

    def never_fits(self, est_in: int, max_out: int) -> bool:
        """True when no amount of waiting makes this request fit."""
        ceiling = self.context
        if self.limits.get("tpm"):
            ceiling = min(ceiling, self.limits["tpm"])
        return est_in + max_out > ceiling


class RegistryError(ValueError):
    pass


def parse_model(raw: dict, allow_http: bool = False) -> Model:
    for key in ("id", "provider", "model", "base_url", "lane"):
        if key not in raw:
            raise RegistryError(f"registry entry missing '{key}': {raw.get('id', raw)}")
    if raw["lane"] not in LANES:
        raise RegistryError(f"{raw['id']}: bad lane {raw['lane']!r}")
    if not (raw["base_url"].startswith("https://") or (allow_http and raw["base_url"].startswith("http://"))):
        raise RegistryError(f"{raw['id']}: base_url must be https")
    if raw.get("max_data_class", "internal") not in DATA_CLASSES:
        raise RegistryError(f"{raw['id']}: bad max_data_class")
    for k in ("price_in", "price_out"):
        if raw.get(k, 0) < 0 or not math.isfinite(raw.get(k, 0)):
            raise RegistryError(f"{raw['id']}: bad {k}")
    if raw["lane"] in ("private-free", "public-only-free") and (raw.get("price_in", 0) or raw.get("price_out", 0)):
        raise RegistryError(f"{raw['id']}: free lane entry has a price")
    if raw["lane"] in ("paid", "premium") and not (raw.get("price_in", 0) or raw.get("price_out", 0)):
        raise RegistryError(f"{raw['id']}: paid lane entry needs a verified price")
    fields = {k: v for k, v in raw.items() if k in Model.__dataclass_fields__}
    return Model(**fields)


class Registry:
    def __init__(self, models: list[Model]):
        ids = [m.id for m in models]
        if len(ids) != len(set(ids)):
            raise RegistryError("duplicate model ids")
        self.models = models

    @classmethod
    def load(cls, path: str | Path, allow_http: bool = False) -> "Registry":
        raw = json.loads(Path(path).read_text())
        return cls([parse_model(r, allow_http) for r in raw["models"]])

    def usable(self) -> list[Model]:
        return [m for m in self.models if m.status == "verified"]

    def get(self, model_id: str) -> Model | None:
        return next((m for m in self.models if m.id == model_id), None)
