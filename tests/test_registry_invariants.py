"""v0.7.0 — registry invariants (Cycle 1 aspect B).

Two layers:

1. Resolution integrity. Registry.get() must be ORDER-INDEPENDENT:
   an exact id match anywhere beats a model-string match anywhere
   (the F-02 collision class: openrouter/gemini-3.1-flash-lite's model
   string IS google/gemini-3.1-flash-lite, the Google entry's id), and
   an ambiguous model string resolves to None instead of whichever
   entry the file happens to list first. NOTE — this corrects the
   Cycle 1 plan's invariant (b) as written ("no entry's model string
   equals another entry's id"): the live registry's F-02 pair is
   itself such a collision and is legitimate, so the battery asserts
   deterministic resolution under permutation, with the known pair
   pinned, rather than absence of collision.

2. A battery over the LIVE registry file: unique ids and
   (provider, model) pairs, price posture by lane, price provenance on
   every priced entry, premium spend alerts armed, the OpenRouter
   shared-pool shape, disabled entries carrying dated notes, complete
   quality maps. The loader stays permissive (old code must keep
   loading newer files and vice versa); this battery is where the
   invariants are enforced.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

import sys
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from registry import (  # noqa: E402
    LANES, TASK_CLASSES, ModelEntry, Registry, load_registry,
)

REG = load_registry(SRC / "registry.json")
BY_ID = {e.id: e for e in REG.entries}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _entry(entry_id: str, provider: str, model: str) -> ModelEntry:
    return ModelEntry(id=entry_id, provider=provider, model=model,
                      lane="paid", quality={"chat": 3}, context_tokens=8192)


# ----------------------------------------------------------------------
# get(): order independence
# ----------------------------------------------------------------------

def test_get_exact_id_beats_model_string_regardless_of_order():
    google = _entry("google/gemini-3.1-flash-lite", "google",
                    "gemini-3.1-flash-lite")
    via_or = _entry("openrouter/gemini-3.1-flash-lite", "openrouter",
                    "google/gemini-3.1-flash-lite")
    for entries in ([google, via_or], [via_or, google]):
        reg = Registry(entries=list(entries))
        assert reg.get("google/gemini-3.1-flash-lite") is google
        assert reg.get("gemini-3.1-flash-lite") is google  # unique model match
        assert reg.get("openrouter/gemini-3.1-flash-lite") is via_or


def test_get_ambiguous_model_string_returns_none():
    a = _entry("prov-a/shared", "prov-a", "shared/model")
    b = _entry("prov-b/shared", "prov-b", "shared/model")
    for entries in ([a, b], [b, a]):
        reg = Registry(entries=list(entries))
        assert reg.get("shared/model") is None
        assert reg.get("prov-a/shared") is a


def test_get_permutation_invariant_over_live_registry():
    rng = random.Random(20261112)
    entries = list(REG.entries)
    for _ in range(5):
        shuffled = entries[:]
        rng.shuffle(shuffled)
        reg = Registry(entries=shuffled)
        for e in entries:
            assert reg.get(e.id) is not None
            assert reg.get(e.id).id == e.id
    # The live F-02 pair, pinned: the OpenRouter entry's model string
    # is the Google entry's id, and the id must win in every order.
    assert REG.get("google/gemini-3.1-flash-lite").provider == "google"
    assert REG.get("openrouter/gemini-3.1-flash-lite").provider == "openrouter"


def test_loader_rejects_duplicate_provider_model_pair(tmp_path):
    raw = {"models": [
        {"id": "a/x", "provider": "a", "model": "m", "lane": "paid",
         "quality": {"chat": 3}, "context_tokens": 8192},
        {"id": "a/y", "provider": "a", "model": "m", "lane": "paid",
         "quality": {"chat": 3}, "context_tokens": 8192},
    ]}
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="duplicate \\(provider, model\\)"):
        load_registry(p)


# ----------------------------------------------------------------------
# live-registry battery
# ----------------------------------------------------------------------

def test_unique_ids_and_provider_model_pairs():
    ids = [e.id for e in REG.entries]
    assert len(ids) == len(set(ids))
    pairs = [(e.provider, e.model) for e in REG.entries]
    assert len(pairs) == len(set(pairs))


def test_price_posture_by_lane():
    for e in REG.entries:
        priced = e.input_usd_per_million > 0 or e.output_usd_per_million > 0
        if e.lane in ("paid", "premium"):
            assert priced, f"{e.id}: paid/premium lane must carry a real price"
        elif e.provider == "groq":
            # The standing exception: Groq's private-free entries carry
            # Developer-plan prices so spend accounting is real
            # (owner decision 2026-11-09, recorded in their notes).
            assert priced, f"{e.id}: groq private-free entries carry plan prices"
        else:
            assert not priced, f"{e.id}: free lane must be $0"


def test_price_provenance_on_every_priced_entry():
    for e in REG.entries:
        if not (e.input_usd_per_million > 0 or e.output_usd_per_million > 0):
            continue
        assert e.price_confidence in ("verified", "observed", "seeded"), \
            f"{e.id}: priced entry must grade its price provenance"
        assert e.price_source, f"{e.id}: priced entry must name its price source"
        if e.price_confidence in ("verified", "observed"):
            assert e.price_verified_at and DATE_RE.fullmatch(e.price_verified_at), \
                f"{e.id}: verified/observed prices carry the confirmation date"


def test_cached_price_bounds_and_provenance():
    # v0.8.3 (true-cost ledger): a cached-input price is a real price —
    # it needs the same provenance discipline as in/out (the v0.7.0
    # battery above covers graded source + dated verification for the
    # entry as a whole) AND it must sit inside [0, input]: a cached
    # rate above the fresh-input rate would charge MORE for cheaper
    # tokens and invert the ledger fix. (A provider publishing such a
    # rate would need a source-backed exception recorded here, not a
    # silent registry value.)
    for e in REG.entries:
        cached = e.cached_input_usd_per_million
        if cached is None:
            continue
        assert cached >= 0, f"{e.id}: cached price must be >= 0"
        if e.input_usd_per_million > 0:
            assert cached <= e.input_usd_per_million, \
                f"{e.id}: cached rate {cached} exceeds input rate " \
                f"{e.input_usd_per_million}"
        else:
            # $0 entries (free lanes): a cached price must also be $0 —
            # a priced cache on an unpriced model breaks lane posture.
            assert cached == 0, \
                f"{e.id}: unpriced entry must not carry a cached price"


def test_premium_entries_have_spend_alert_armed():
    for e in REG.entries:
        if e.lane == "premium":
            assert e.spend_alert_usd is not None and e.spend_alert_usd > 0, e.id


def test_spillway_exclusion_only_on_paid_lanes():
    for e in REG.entries:
        if e.spillway_sensitive_excluded:
            assert e.lane in ("paid", "premium"), e.id


def test_openrouter_pool_shape():
    pool_limits = []
    for e in REG.entries:
        if e.provider != "openrouter":
            continue
        in_pool = e.model.endswith(":free") or e.id == "openrouter/free-router"
        assert e.quota_pool_id == ("openrouter-free" if in_pool
                                   else "openrouter-paid"), e.id
        if in_pool:
            pool_limits.append((e.id, e.limits))
    assert pool_limits, "the :free pool must have members"
    for entry_id, limits in pool_limits:
        assert limits == {"rpd": 1000, "rpm": 20}, \
            f"{entry_id}: pool members carry the shared pool limits identically"


def test_disabled_entries_carry_dated_notes():
    for e in REG.entries:
        if e.enabled:
            continue
        assert e.notes, f"{e.id}: disabled entries must say why"
        assert DATE_RE.search(e.notes), f"{e.id}: disabled note must be dated"
        assert "disabled" in e.notes.lower(), \
            f"{e.id}: disabled note must carry the DISABLED marker"


def test_quality_maps_complete():
    for e in REG.entries:
        assert set(e.quality) == set(TASK_CLASSES), e.id
        assert all(1 <= int(v) <= 5 for v in e.quality.values()), e.id


def test_provider_posture_caveats_stay_in_notes():
    # B.3(f), data-driven: NVIDIA routes are evaluation-only capacity
    # and HF routes run on :deepinfra backends only — both postures
    # live in the entry text/model string, and must not silently drop.
    for e in REG.entries:
        if e.provider == "nvidia":
            assert "evaluation" in e.notes.lower(), e.id
        if e.provider == "huggingface":
            assert ":deepinfra" in e.model, e.id
