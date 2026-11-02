"""Difficulty classifier — Layer 1 (rules) in the gateway.

Spec ("Difficulty classifier"): three layers, cheapest first. Layer 1
rules live here in the gateway and decide clear cases in <1 ms. Layer 2
(embedding nearest-match) and Layer 3 (Arch-Router) run in the sidecar;
the gateway calls the sidecar over HTTP only when the rules are not
confident, with a hard 1.5 s timeout, and falls back to the rules result
on any sidecar failure — routing never waits on the classifier beyond
that limit, and the fallback is logged.

The v0.5 fix that matters most (spec, Summary): keyword rules must not
mark a request complex for merely *mentioning* infrastructure. An
infrastructure term raises the level ONLY together with:

  * a change verb   (deploy, migrate, delete, restore, rotate, ...), or
  * a diagnosis verb (debug, fix, why, root cause, ...).

Ties: when signals disagree between levels 3-5 the higher level wins —
under-classifying a hard task costs more in failed work than the price
difference. Levels 1-2 disagreements keep the lower (cheaper) level
unless a hard signal (size, images, verb group) fired.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from clients import ALIASES

# ----------------------------------------------------------------------
# Keyword groups. Infrastructure terms ALONE never raise the level.
# ----------------------------------------------------------------------
INFRA_TERMS = {
    "docker", "container", "containers", "proxmox", "vm", "vms", "stack",
    "stacks", "nginx", "server", "servers", "database", "db", "postgres",
    "mysql", "sqlite", "backup", "backups", "firewall", "kubernetes",
    "k8s", "systemd", "service", "services", "ssh", "vps", "dns", "ssl",
    "certificate", "certificates", "portainer", "n8n", "workflow",
    "workflows", "host", "hosts", "network", "disk", "volume", "volumes",
    "cron", "hestia", "gateway",
}

# Change verbs that make an infra mention an actual change. The first set
# is destructive / production-moving (level 4); the rest are single,
# well-scoped changes (level 3 — spec example: "restart a container").
CHANGE_VERBS_STRONG = {
    "deploy", "migrate", "delete", "remove", "restore", "rotate",
    "cutover", "rollback", "wipe", "rebuild", "upgrade", "provision",
    "decommission", "purge", "drop",
}
CHANGE_VERBS_MILD = {
    "restart", "start", "stop", "create", "add", "update", "change",
    "configure", "install", "enable", "disable", "rename", "move",
    "copy", "set",
}

# Diagnosis verbs. "root cause" / "debug" imply multi-step diagnosis
# (level 4); the rest are single-step diagnosis (level 3 — spec example:
# "explain an error").
DIAGNOSIS_VERBS_STRONG = {"debug", "troubleshoot", "investigate"}
DIAGNOSIS_PHRASES_STRONG = ("root cause",)
DIAGNOSIS_VERBS_MILD = {"fix", "why", "diagnose", "explain"}

CODE_TERMS = {
    "function", "script", "code", "refactor", "compile", "regex",
    "python", "javascript", "typescript", "bash", "sql", "api",
    "endpoint", "bug", "unit test", "tests",
}
REASONING_TERMS = {
    "plan", "architecture", "design", "strategy", "compare", "evaluate",
    "tradeoff", "trade-offs", "proposal", "roadmap", "decide",
}

GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening))\b",
    re.IGNORECASE,
)
FORMAT_RE = re.compile(
    r"\b(bullet list|reformat|rewrite this|shorten|summarize|summarise|"
    r"translate|spell ?check|proofread)\b",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-']*")

# Required model quality per difficulty level (spec routing table):
#   L1 -> 2, L2 -> 3, L3 -> 3 (prefer 4 if free), L4 -> 4, L5 -> 5
REQUIRED_QUALITY: dict[int, int] = {1: 2, 2: 3, 3: 3, 4: 4, 5: 5}


@dataclass
class Classification:
    level: int                       # 1..5
    task_class: str                  # chat|infra|code|reasoning|long-context
    confidence: float                # 0..1
    layer: str                       # rules|alias|header|sidecar|rules-fallback
    signals: list[str] = field(default_factory=list)
    explicit_premium: bool = False
    sidecar_fallback: bool = False


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def _contains_phrase(text_lower: str, phrase: str) -> bool:
    return phrase in text_lower


def classify_rules(
    last_user_text: str,
    *,
    est_input_tokens: int = 0,
    has_images: bool = False,
    json_mode: bool = False,
    alias: Optional[str] = None,
    level_override: Optional[int] = None,
) -> Classification:
    """Layer 1 rules. Deterministic, <1 ms, no I/O."""
    # --- explicit client intent wins -----------------------------------
    if level_override is not None:
        return Classification(
            level=max(1, min(5, level_override)), task_class=_task_hint(last_user_text),
            confidence=1.0, layer="header", signals=["level_override_header"],
        )
    if alias and alias in ALIASES and ALIASES[alias].level is not None:
        spec = ALIASES[alias]
        return Classification(
            level=spec.level or 2,
            task_class=spec.task_class or _task_hint(last_user_text),
            confidence=1.0, layer="alias", signals=[f"alias:{alias}"],
            explicit_premium=spec.explicit_premium,
        )

    text = last_user_text or ""
    lower = text.lower()
    words = _words(text)
    wordset = set(words)
    signals: list[str] = []
    hard_levels: list[int] = []      # signals that must not be diluted
    soft_level = 2                   # default: routine (spec L2)
    task_class = "chat"

    has_infra = bool(wordset & INFRA_TERMS)
    strong_change = bool(wordset & CHANGE_VERBS_STRONG)
    mild_change = bool(wordset & CHANGE_VERBS_MILD)
    strong_diag = bool(wordset & DIAGNOSIS_VERBS_STRONG) or any(
        _contains_phrase(lower, p) for p in DIAGNOSIS_PHRASES_STRONG
    )
    mild_diag = bool(wordset & DIAGNOSIS_VERBS_MILD)

    # --- the combined keyword+verb rule (the v0.5 headline fix) --------
    if has_infra:
        task_class = "infra"
        if strong_change:
            hard_levels.append(4)
            signals.append("infra+change_strong")
        elif mild_change:
            hard_levels.append(3)
            signals.append("infra+change_mild")
        if strong_diag:
            hard_levels.append(4)
            signals.append("infra+diagnosis_strong")
        elif mild_diag:
            hard_levels.append(3)
            signals.append("infra+diagnosis_mild")
        if not (strong_change or mild_change or strong_diag or mild_diag):
            signals.append("infra_mention_only")  # explicitly NOT a raise

    # --- code / reasoning hints (only when infra did not claim it) -----
    if task_class == "chat":
        if wordset & CODE_TERMS or "```" in text:
            task_class = "code"
            if "refactor" in wordset or strong_diag:
                hard_levels.append(4)
                signals.append("code_refactor_or_debug")
            else:
                soft_level = max(soft_level, 3)
                signals.append("code_task")
        elif wordset & REASONING_TERMS:
            task_class = "reasoning"
            soft_level = max(soft_level, 4)
            signals.append("reasoning_task")

    # --- size signals ----------------------------------------------------
    if est_input_tokens > 60_000:
        task_class = "long-context"
        hard_levels.append(4)
        signals.append("size_very_long")
    elif est_input_tokens > 20_000:
        hard_levels.append(3)
        signals.append("size_long")
    elif len(text) > 4_000:
        hard_levels.append(3)
        signals.append("ask_long")

    # --- modality / format signals --------------------------------------
    if has_images:
        hard_levels.append(3)
        signals.append("images")
    if json_mode:
        soft_level = max(soft_level, 2)
        signals.append("json_mode")

    # --- trivial cases ----------------------------------------------------
    # Note: "summarize a log" is spec L2 (routine), not L1 — only pure
    # greetings, very short asks, and pure reformatting count as trivial.
    trivial = (
        len(words) <= 12
        and not hard_levels
        and task_class == "chat"  # a short *task* ("use the function") is not trivial
        and (GREETING_RE.search(text) or len(words) <= 4
             or re.search(r"\b(bullet list|reformat)\b", lower))
    )
    if trivial:
        return Classification(level=1, task_class="chat", confidence=0.9,
                              layer="rules", signals=signals + ["trivial"])

    # --- combine: hard signals win; ties in 3..5 go higher ---------------
    if hard_levels:
        level = max(hard_levels + [soft_level]) if max(hard_levels) >= 3 else max(hard_levels)
        # soft_level can only raise a hard level when both are >=3 (tie rule)
        if soft_level >= 3:
            level = max(level, soft_level)
        confidence = 0.85
    else:
        level = soft_level
        confidence = 0.55 if level == 2 and not signals else 0.7

    if alias and alias in ALIASES:
        spec = ALIASES[alias]
        if spec.task_class:
            task_class = spec.task_class
        signals.append(f"alias:{alias}")

    return Classification(level=level, task_class=task_class,
                          confidence=confidence, layer="rules", signals=signals)


def _task_hint(text: str) -> str:
    wordset = set(_words(text or ""))
    if wordset & INFRA_TERMS:
        return "infra"
    if wordset & CODE_TERMS:
        return "code"
    if wordset & REASONING_TERMS:
        return "reasoning"
    return "chat"


class Classifier:
    """Rules first; sidecar (layers 2+3) only when rules are unsure.

    The sidecar is an HTTP service (embedding nearest-match and/or
    Arch-Router) — see spec Phase 2. Contract: POST {text, rules:{level,
    task_class}} -> {level, task_class, confidence}. Any error, timeout
    (>1.5 s) or malformed reply falls back to the rules result and is
    flagged so the gateway logs the fallback.
    """

    SIDECAR_TIMEOUT_S = 1.5
    CONFIDENT_RULES = 0.8          # at/above this, rules decide alone
    SIDECAR_TEXT_CHARS = 1500      # spec: embed last user message truncated

    def __init__(self, sidecar_url: Optional[str] = None,
                 http_client: Optional[httpx.AsyncClient] = None):
        self.sidecar_url = sidecar_url.rstrip("/") if sidecar_url else None
        self._client = http_client
        self._owns_client = http_client is None
        self.last_sidecar_ok: Optional[bool] = None   # backs /health

    async def classify(self, last_user_text: str, **kwargs: Any) -> Classification:
        result = classify_rules(last_user_text, **kwargs)
        if (
            self.sidecar_url is None
            or result.layer in ("alias", "header")
            or result.confidence >= self.CONFIDENT_RULES
        ):
            return result
        try:
            client = self._client or httpx.AsyncClient(trust_env=False)
            try:
                resp = await client.post(
                    f"{self.sidecar_url}/classify",
                    json={
                        "text": (last_user_text or "")[: self.SIDECAR_TEXT_CHARS],
                        "rules": {"level": result.level, "task_class": result.task_class},
                    },
                    timeout=self.SIDECAR_TIMEOUT_S,
                )
                resp.raise_for_status()
                data = resp.json()
            finally:
                if self._owns_client:
                    await client.aclose()
            level = int(data["level"])
            task_class = str(data.get("task_class") or result.task_class)
            confidence = float(data.get("confidence", 0.5))
            # Tie rule: uncertain between two levels in 3..5 -> higher.
            if level != result.level and min(level, result.level) >= 3:
                level = max(level, result.level)
            self.last_sidecar_ok = True
            return Classification(
                level=max(1, min(5, level)), task_class=task_class,
                confidence=confidence, layer="sidecar",
                signals=result.signals + ["sidecar"],
            )
        except Exception:
            # Sidecar down/slow/malformed: rules decide, fallback is logged
            # by the gateway via sidecar_fallback=True (spec requirement).
            self.last_sidecar_ok = False
            result.layer = "rules"
            result.sidecar_fallback = True
            result.signals = result.signals + ["sidecar_fallback"]
            return result

    async def health(self) -> str:
        """ok | down | not_configured — surfaced in GET /health."""
        if self.sidecar_url is None:
            return "not_configured"
        if self.last_sidecar_ok is None:
            # No classify call yet: do one cheap probe.
            try:
                client = self._client or httpx.AsyncClient(trust_env=False)
                try:
                    resp = await client.get(f"{self.sidecar_url}/health", timeout=1.0)
                    self.last_sidecar_ok = resp.status_code == 200
                finally:
                    if self._owns_client:
                        await client.aclose()
            except Exception:
                self.last_sidecar_ok = False
        return "ok" if self.last_sidecar_ok else "down"
