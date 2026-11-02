"""Sticky routing for agent tool loops.

Spec ("Sticky routing in agent tool loops"): an agent turn spans many
requests (model -> tool -> result -> model ...). Switching models inside
the loop wastes the provider's prompt cache (DeepInfra cached input is
20% of the normal rate) and can break tool-call formats.

Conversation key = client + sha256(first user message + system prompt).
The key is a hash — no prompt text is ever stored. The chosen model is
kept for 30 minutes of *activity* (the TTL slides on each request) and
is only abandoned when:

  * the model fails or hits a provider rate limit (caller decides and
    calls :meth:`StickyStore.invalidate`), or
  * a new *user* message arrives whose classified level is higher than
    the level the sticky model was chosen for.

Tool-result messages do not re-trigger classification-driven switching:
within a turn, the level of the opening user message stands.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Optional


def conversation_key(client_id: str, first_user_text: str, system_text: str) -> str:
    h = hashlib.sha256()
    h.update(client_id.encode("utf-8"))
    h.update(b"\x00")
    h.update((first_user_text or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((system_text or "").encode("utf-8"))
    return h.hexdigest()


@dataclass
class StickyEntry:
    model_id: str
    level: int                 # level the model was chosen for
    last_seen: float
    created: float
    tool_error_pending: bool = False   # outcome signal for telemetry


class StickyStore:
    """In-memory conversation -> model map with a sliding 30-min TTL.

    In-memory is deliberate for v0.5: sticky state is a cache-affinity
    optimisation, not correctness state; losing it on restart costs one
    cache-cold request per active conversation.
    """

    TTL_S = 30 * 60

    def __init__(self, ttl_s: int = TTL_S):
        self.ttl_s = ttl_s
        self._entries: dict[str, StickyEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str, now: Optional[float] = None) -> Optional[StickyEntry]:
        now = now or time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if now - entry.last_seen > self.ttl_s:
                del self._entries[key]
                return None
            return entry

    def touch(self, key: str, now: Optional[float] = None) -> None:
        now = now or time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.last_seen = now

    def set(self, key: str, model_id: str, level: int,
            now: Optional[float] = None) -> None:
        now = now or time.time()
        with self._lock:
            existing = self._entries.get(key)
            self._entries[key] = StickyEntry(
                model_id=model_id,
                level=level,
                last_seen=now,
                created=existing.created if existing else now,
                tool_error_pending=False,
            )

    def invalidate(self, key: str) -> None:
        """Drop the sticky binding (model failed / rate-limited)."""
        with self._lock:
            self._entries.pop(key, None)

    def note_tool_error(self, key: str) -> None:
        """Record that the latest turn carried a tool error — an outcome
        signal consumed by telemetry for the *next* request in this
        conversation (spec: Telemetry and learning loop)."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.tool_error_pending = True

    def consume_tool_error(self, key: str) -> bool:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.tool_error_pending:
                entry.tool_error_pending = False
                return True
            return False

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
