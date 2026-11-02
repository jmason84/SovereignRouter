"""Difficulty classifier. Layer 1 (rules) is implemented here; the embedding
and local-router layers plug in through the `sidecar` callable and are only
consulted when rules are not confident. Routing never waits on the sidecar
longer than `timeout` seconds."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

ALIAS_LEVEL = {"cheap": (1, "chat"), "strong": (4, "reasoning"), "premium": (5, "reasoning"),
               "code": (4, "code"), "long": (3, "long-context")}

INFRA = re.compile(r"\b(docker|proxmox|portainer|container|stack|vm\d*|lxc|nginx|cloudflare|backup|snapshot|"
                   r"firewall|dns|kubernetes|k8s|server|n8n|compose|volume|tunnel|hestia\w*)\b", re.I)
CHANGE = re.compile(r"\b(deploy|migrate|delete|remove|restore|rotate|upgrade|reconfigure|destroy|wipe|"
                    r"cutover|rollback|redeploy)\b", re.I)
DIAG = re.compile(r"\b(debug|fix|why|root cause|troubleshoot|diagnose|investigate|failing|crash\w*)\b", re.I)
CRITICAL = re.compile(r"\b(production|prod|firewall|credentials?|security|cutover|iam)\b", re.I)
CODE = re.compile(r"\b(refactor|implement|function|script|class|unit test|regex|sql|stack trace|bug|code)\b", re.I)
CODE_HEAVY = re.compile(r"\b(refactor|implement|rewrite)\b", re.I)
REASON = re.compile(r"\b(architecture|design|trade-?offs?|compare|analy[sz]e|plan)\b", re.I)
TRIVIAL = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|say hello|reformat|format|"
                     r"turn this into|convert this to|bullet)\b", re.I)


@dataclass(frozen=True)
class Classification:
    level: int
    task: str
    confidence: float
    layer: str  # alias | header | rules | sidecar | sticky | fallback
    reason: str = ""


def _task_for(text: str) -> str:
    if CODE.search(text):
        return "code"
    if INFRA.search(text):
        return "infra"
    if REASON.search(text):
        return "reasoning"
    return "chat"


def classify_rules(text: str, *, alias: str | None = None, header_level: int | None = None,
                   has_images: bool = False, json_mode: bool = False) -> Classification:
    if header_level is not None and 1 <= header_level <= 5:
        return Classification(header_level, _task_for(text), 1.0, "header", "X-Sovereign-Level")
    if alias in ALIAS_LEVEL:
        level, task = ALIAS_LEVEL[alias]
        return Classification(level, task, 1.0, "alias", alias)
    task = _task_for(text)
    n = len(text)
    infra, change, diag = bool(INFRA.search(text)), bool(CHANGE.search(text)), bool(DIAG.search(text))
    crit = bool(CRITICAL.search(text))
    if n < 200 and TRIVIAL.search(text) and not (infra and (change or diag)):
        level, conf, why = 1, 0.9, "trivial"
    elif infra and change and crit:
        level, conf, why = 5, 0.8, "infra+change+critical"
    elif infra and change:
        level, conf, why = 4, 0.7, "infra+change"
    elif infra and diag:
        level, conf, why = 3, 0.6, "infra+diagnosis"
    elif CODE_HEAVY.search(text) and CODE.search(text):
        level, conf, why = 4, 0.7, "code-change"
    elif n > 4000:
        level, conf, why = 4, 0.8, "long-ask"
    elif REASON.search(text) and n > 300:
        level, conf, why = 4, 0.6, "reasoning"
    elif CODE.search(text):
        level, conf, why = 3, 0.5, "code"
    elif infra:
        level, conf, why = 2, 0.6, "infra-lookup"  # infra term alone does NOT raise the level
    else:
        level, conf, why = 2, 0.5, "default"
    if has_images:
        level = max(level, 3)
    if json_mode:
        level = max(level, 2)
    return Classification(level, task, conf, "rules", why)


def resolve_tie(a: Classification, b: Classification) -> Classification:
    """When two layers disagree by one level and either is 3-5, take the higher: under-classifying a
    hard task costs more in failed work than the price difference (v0.5.3)."""
    if a.level != b.level and abs(a.level - b.level) == 1 and max(a.level, b.level) >= 3:
        return a if a.level > b.level else b
    return b


async def classify(text: str, *, sidecar=None, timeout: float = 1.2, threshold: float = 0.6, **kw) -> Classification:
    base = classify_rules(text, **kw)
    if base.confidence >= threshold or sidecar is None:
        return base
    try:
        out = await asyncio.wait_for(sidecar(text), timeout)
        if out is not None:
            return resolve_tie(base, out)
    except Exception:  # sidecar down/slow: rules decision stands
        return Classification(base.level, base.task, base.confidence, "fallback", "sidecar unavailable")
    return base
