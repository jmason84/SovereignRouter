"""Sticky routing: keep one model across an agent's tool loop.

Conversation key = sha256(client + system prompt + first user message).
Only the hash is stored; no prompt text."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

TTL = 30 * 60


@dataclass
class Sticky:
    model_id: str
    level: int
    sensitive: bool
    ts: float


def conversation_key(client: str, messages: list) -> str:
    def text(m):
        c = m.get("content")
        return c if isinstance(c, str) else str(c)
    system = next((text(m) for m in messages if m.get("role") == "system"), "")
    first_user = next((text(m) for m in messages if m.get("role") == "user"), "")
    return hashlib.sha256(f"{client}\x00{system}\x00{first_user}".encode()).hexdigest()


class StickyStore:
    def __init__(self, clock=time.time, ttl: float = TTL):
        self._d: dict[str, Sticky] = {}
        self.clock, self.ttl = clock, ttl

    def get(self, key: str) -> Sticky | None:
        s = self._d.get(key)
        if s and self.clock() - s.ts > self.ttl:
            del self._d[key]
            return None
        return s

    def put(self, key: str, model_id: str, level: int, sensitive: bool):
        prev = self.get(key)
        self._d[key] = Sticky(model_id, max(level, prev.level if prev else 0),
                              sensitive or (prev.sensitive if prev else False), self.clock())
        if len(self._d) > 5000:  # bound memory
            for k in sorted(self._d, key=lambda k: self._d[k].ts)[:1000]:
                del self._d[k]
