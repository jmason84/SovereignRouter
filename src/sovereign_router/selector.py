"""Candidate selection: policy eligibility -> capability -> fit -> cost order."""
from __future__ import annotations

from dataclasses import dataclass, field

from .registry import Model, data_rank

REQUIRED_QUALITY = {1: 2, 2: 3, 3: 3, 4: 4, 5: 5}


@dataclass
class Profile:
    est_tokens: int
    max_tokens: int
    has_tools: bool = False
    has_images: bool = False
    json_mode: bool = False
    tool_names: set = field(default_factory=set)
    tool_loop: bool = False
    data_class: str = "internal"
    explicit_premium: bool = False


@dataclass
class Selection:
    ordered: list  # candidates, best first
    skips: dict  # model id -> reason
    policy_blocked: int = 0  # excluded by lane/data-class/capability
    never_fit: int = 0  # eligible but can never fit
    rate_limited: int = 0


def select(models: list[Model], policy, profile: Profile, level: int, task: str, required: int, *,
           store, backstop_blocked: bool = False, preferred: str | None = None,
           exclude: set | frozenset = frozenset()) -> Selection:
    sel = Selection([], {})
    ok: list[Model] = []
    for m in models:
        why = None
        if m.id in exclude:
            why = "already tried"
        elif m.lane not in policy.lanes:
            why = f"lane {m.lane} not allowed"; sel.policy_blocked += 1
        elif m.lane == "premium" and not (policy.premium and (level >= 5 or profile.explicit_premium)):
            why = "premium not requested/permitted"; sel.policy_blocked += 1
        elif backstop_blocked and not m.is_free:
            why = "global backstop: paid lanes off today"
        elif data_rank(m.max_data_class) < data_rank(profile.data_class):
            why = f"not cleared for {profile.data_class} data"; sel.policy_blocked += 1
        elif profile.has_tools and not m.supports_tools:
            why = "no tool support"; sel.policy_blocked += 1
        elif profile.has_images and not m.supports_vision:
            why = "no vision support"; sel.policy_blocked += 1
        elif profile.json_mode and not m.supports_json:
            why = "no JSON mode"; sel.policy_blocked += 1
        elif m.never_fits(profile.est_tokens, profile.max_tokens):
            why = "can never fit (context or per-minute cap)"; sel.never_fit += 1
        else:
            why = store.check_limits(m, profile.est_tokens + profile.max_tokens)
            if why:
                sel.rate_limited += 1
        if why:
            sel.skips[m.id] = why
        else:
            ok.append(m)

    def cost(m):
        return m.est_cost(profile.est_tokens, profile.max_tokens)

    want_premium = policy.premium and (level >= 5 or profile.explicit_premium)
    # Level 5 / explicit premium: the premium lane leads when the client may use it (v0.5.2 level table).
    good = sorted((m for m in ok if m.q(task) >= required),
                  key=lambda m: (0 if (want_premium and m.lane == "premium") else 1, cost(m), -m.q(task)))
    # Last resort: lower quality beats refusing the request. Best quality first.
    tail = sorted((m for m in ok if m.q(task) < required), key=lambda m: (-m.q(task), cost(m)))
    ordered = good + tail
    if preferred:
        for i, m in enumerate(ordered):
            if m.id == preferred and m.q(task) >= required:
                ordered.insert(0, ordered.pop(i))
                break
    sel.ordered = ordered
    return sel
