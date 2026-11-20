"""Per-model tool-loop health (v0.9.3) — OBSERVE-ONLY.

Records, for each model that made a tool call inside a sticky tool loop,
whether the tool result that came back reported an error. Attribution is
exact (the sticky model made the call), unlike the route row's
``tool_error_next_turn`` which lands on whichever model serves the next
turn and double-counts one error across a 3-message window.

Decision J-2 (OPEN): automatic demotion stays observe-only. This module
therefore NEVER feeds selection. ``would_cap`` reports the tooling-quality
cap an enforcing version would apply so the owner can judge it from real
numbers before deciding.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import deque
from pathlib import Path
from typing import Optional

WINDOW = 200          # most recent tool turns kept per model
MIN_TURNS = 50        # below this a rate is not trusted
WARN_RATE = 0.25      # would cap tooling quality at 3
SEVERE_RATE = 0.40    # would cap tooling quality at 2
PERSIST_EVERY = 20


class ToolHealth:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._data: dict[str, deque] = {}
        self._since_save = 0
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            for model, outcomes in raw.items():
                self._data[model] = deque((int(bool(x)) for x in outcomes),
                                          maxlen=WINDOW)
        except Exception:
            self._data = {}  # corrupt file: start clean, never crash boot

    def _save_locked(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                       prefix=".tool_health.")
            with os.fdopen(fd, "w") as fh:
                json.dump({m: list(d) for m, d in self._data.items()}, fh)
            os.replace(tmp, self.path)
        except Exception:
            pass  # observability must never break routing

    def flush(self) -> None:
        with self._lock:
            self._save_locked()
            self._since_save = 0

    # -- recording --------------------------------------------------------
    def record(self, model_id: str, had_error: bool) -> None:
        with self._lock:
            self._data.setdefault(model_id, deque(maxlen=WINDOW)).append(
                int(bool(had_error)))
            self._since_save += 1
            if self._since_save >= PERSIST_EVERY:
                self._save_locked()
                self._since_save = 0

    # -- reading ----------------------------------------------------------
    def stats(self, model_id: str) -> dict:
        with self._lock:
            d = self._data.get(model_id, ())
            n = len(d)
            errs = sum(d)
        rate = (errs / n) if n else 0.0
        return {"turns": n, "errors": errs, "rate": round(rate, 4),
                "trusted": n >= MIN_TURNS}

    def would_cap(self, model_id: str) -> Optional[int]:
        """Tooling-quality cap an enforcing version would apply, or None.
        Not read by selection (J-2)."""
        s = self.stats(model_id)
        if not s["trusted"]:
            return None
        if s["rate"] >= SEVERE_RATE:
            return 2
        if s["rate"] >= WARN_RATE:
            return 3
        return None

    def snapshot(self) -> dict:
        with self._lock:
            models = list(self._data)
        out = {}
        for m in models:
            s = self.stats(m)
            s["would_cap"] = self.would_cap(m)
            out[m] = s
        return out
