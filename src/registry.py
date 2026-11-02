"""Model Registry v2 for Sovereign Router v0.5.

Extends the v0.3/v0.4 schema per spec ("Model registry v2"):

  * ``lane``: private-free | public-only-free | paid | premium
  * ``quality``: map of task class -> score 1..5
    (chat, infra, code, reasoning, long-context)
  * ``cached_input_usd_per_million``: cached-input price (DeepInfra bills
    cached input at 20% of the normal rate).
  * ``latency_p50_ms``
  * ``spend_alert_usd``: monthly alert level for paid entries. This is an
    alert, never a cap (spec decision 2026-10-31: spending is not capped).
  * ``extra_body``: provider-specific request fields, e.g. OpenRouter's
    ``data_collection=deny``.
  * ``probe``: weekly probe description; a failed probe marks the entry
    candidate until re-verified.
  * ``verified_until``: ISO date; entries past it are skipped as unverified.
  * ``limits``: real provider windows (rpm/tpm/rpd/tpd) enforced by the
    quota store — reaching one moves traffic to the next model.
  * ``sensitive_cleared``: provider cleared by the owner to receive
    data_class=sensitive prompts. Default False for every lane; until the
    owner clears one, sensitive requests are confined to private lanes.

Provider API keys are NOT part of the registry. They live as mode-600
files in the state directory (``keys/<provider>.key``), read at startup
by the gateway, and never appear in the registry, compose files or logs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

LANES = ("private-free", "public-only-free", "paid", "premium")
FREE_LANES = ("private-free", "public-only-free")
TASK_CLASSES = ("chat", "infra", "code", "reasoning", "long-context")

# Default OpenAI-compatible base URLs per provider. An entry may override
# with its own ``base_url`` (e.g. a self-hosted endpoint).
PROVIDER_BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "ollama": "https://ollama.com/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


@dataclass
class ModelEntry:
    id: str                                  # registry id, e.g. "groq/gpt-oss-120b"
    provider: str                            # credential / quota bucket
    model: str                               # upstream model name
    lane: str
    quality: dict[str, int]                  # task class -> 1..5
    context_tokens: int
    input_usd_per_million: float = 0.0
    output_usd_per_million: float = 0.0
    cached_input_usd_per_million: Optional[float] = None
    latency_p50_ms: Optional[int] = None
    spend_alert_usd: Optional[float] = None
    extra_body: dict[str, Any] = field(default_factory=dict)
    probe: dict[str, Any] = field(default_factory=dict)
    verified_until: Optional[str] = None     # ISO date "YYYY-MM-DD"
    limits: dict[str, Any] = field(default_factory=dict)
    base_url: Optional[str] = None
    supports_vision: bool = False
    supports_tools: bool = True
    sensitive_cleared: bool = False
    enabled: bool = True
    notes: str = ""

    # -- derived helpers -------------------------------------------------
    @property
    def is_free(self) -> bool:
        return self.lane in FREE_LANES

    @property
    def effective_base_url(self) -> str:
        return self.base_url or PROVIDER_BASE_URLS.get(self.provider, "")

    def quality_for(self, task_class: str) -> int:
        """Quality for a task class; falls back to the entry's minimum
        known quality so an unrated class is never over-trusted."""
        if task_class in self.quality:
            return int(self.quality[task_class])
        if self.quality:
            return min(int(v) for v in self.quality.values())
        return 1

    def is_verified(self, now: Optional[float] = None) -> bool:
        if not self.verified_until:
            return True
        today = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
        return self.verified_until >= today

    def estimate_cost(self, input_tokens: int, output_tokens: int,
                      cached_tokens: int = 0) -> float:
        """USD cost with cached-input pricing applied when configured."""
        cached = min(cached_tokens, input_tokens)
        fresh = input_tokens - cached
        cached_rate = self.cached_input_usd_per_million
        if cached_rate is None:
            cached_rate = self.input_usd_per_million
        return (
            fresh * self.input_usd_per_million
            + cached * cached_rate
            + output_tokens * self.output_usd_per_million
        ) / 1_000_000.0


@dataclass
class Registry:
    entries: list[ModelEntry]

    def get(self, entry_id: str) -> Optional[ModelEntry]:
        for e in self.entries:
            if e.id == entry_id or e.model == entry_id:
                return e
        return None

    def by_provider(self, provider: str) -> list[ModelEntry]:
        return [e for e in self.entries if e.provider == provider]


_ENTRY_REQUIRED = ("id", "provider", "model", "lane", "quality", "context_tokens")


def validate_entry(raw: dict[str, Any]) -> ModelEntry:
    missing = [k for k in _ENTRY_REQUIRED if k not in raw]
    if missing:
        raise ValueError(f"registry entry missing fields {missing}: {raw.get('id')!r}")
    if raw["lane"] not in LANES:
        raise ValueError(f"entry {raw['id']!r}: lane {raw['lane']!r} not in {LANES}")
    quality = raw["quality"]
    if not isinstance(quality, dict) or not quality:
        raise ValueError(f"entry {raw['id']!r}: quality must be a non-empty map")
    for cls, score in quality.items():
        if cls not in TASK_CLASSES:
            raise ValueError(f"entry {raw['id']!r}: unknown task class {cls!r}")
        if not (1 <= int(score) <= 5):
            raise ValueError(f"entry {raw['id']!r}: quality[{cls}]={score} outside 1..5")
    known = set(ModelEntry.__dataclass_fields__)
    kwargs = {k: v for k, v in raw.items() if k in known}
    return ModelEntry(**kwargs)


def load_registry(path: str | Path) -> Registry:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_entries = data["models"] if isinstance(data, dict) else data
    entries = [validate_entry(r) for r in raw_entries]
    ids = [e.id for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate registry entry ids")
    return Registry(entries=entries)
