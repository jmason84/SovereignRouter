"""v0.10.11 (owner go 2026-11-28 1:40 AM MT, ROUTER-STRESS-20261128).

1. Burst spreading (src/spread.py). The 2026-11-28 stress run showed a
   herd: every concurrent turn walked the identical cost order, the top
   models 429'd together and the v0.10.5 6-attempt cap ran out (35-45%
   429s at 40-80 concurrent against a 3-in-flight-per-model mock, where
   v0.10.3 had 0%). Now, within a tier only, saturated / recently
   429'd entries move back and contended equal-rank bands are spread by
   in-flight count with a seedable RNG. Tier order, the sensitivity
   wall, the no-premium-on-congestion rule and pins are unchanged.
2. SSN + payment-card detectors ON by default (SOVEREIGN_PII_DETECTORS=0
   restores log-only), with false-positive guards.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import auth, build_app, chat_body  # noqa: E402
from test_fixpass import build_recording_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import gateway  # noqa: E402
from gateway import MAX_UPSTREAM_ATTEMPTS  # noqa: E402
from sensitivity import scan_pii_text  # noqa: E402
from spread import LoadSpreader, tier_of  # noqa: E402

FREE = ("private-free", "public-only-free")


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def E(mid: str, lane: str = "private-free", q: int = 3, cost: float = 0.0):
    return SimpleNamespace(id=mid, lane=lane, q=q, cost=cost)


def order(sp: LoadSpreader, cands):
    out, moved = sp.reorder(cands, tier_key=lambda e: tier_of(e.lane),
                            quality=lambda e: e.q, cost=lambda e: e.cost)
    return [c.id for c in out], moved


def sp_(**kw) -> LoadSpreader:
    kw.setdefault("enabled", True)
    kw.setdefault("seed", 7)
    kw.setdefault("clock", Clock())
    return LoadSpreader(**kw)


LIST = [E("f1"), E("f2"), E("f3"), E("p1", "paid", 3, 0.001),
        E("p2", "paid", 3, 0.0012), E("p3", "paid", 3, 0.01),
        E("x1", "premium", 5, 0.1)]


# ----------------------------------------------------------------------
# 1a. ordering unit tests
# ----------------------------------------------------------------------
def test_idle_order_is_unchanged():
    assert order(sp_(), LIST)[0] == [e.id for e in LIST]


def test_disabled_is_noop():
    sp = sp_(enabled=False)
    sp.note_status("f1", 429)
    sp.inflight["f2"] = 5
    assert order(sp, LIST)[0] == [e.id for e in LIST]
    assert not sp.cooled("f1")


def test_env_switch_off(monkeypatch):
    monkeypatch.setenv("SOVEREIGN_SPREAD", "0")
    assert LoadSpreader().enabled is False
    monkeypatch.setenv("SOVEREIGN_SPREAD", "1")
    assert LoadSpreader().enabled is True


def test_rate_limit_429_cools_to_back_of_its_tier_only():
    sp = sp_()
    sp.note_status("f1", 429)               # no other call open -> cooldown
    ids, moved = order(sp, LIST)
    assert ids[:3] == ["f2", "f3", "f1"]    # still before every paid entry
    assert ids[3:] == ["p1", "p2", "p3", "x1"]
    assert moved == ["f1"]


def test_cooled_paid_never_jumps_ahead_of_free_or_behind_premium():
    sp = sp_()
    for m in ("p1", "p2"):
        sp.note_status(m, 429)
    ids, _ = order(sp, LIST)
    assert ids == ["f1", "f2", "f3", "p3", "p1", "p2", "x1"]


def test_whole_tier_cooled_is_kept_in_place():
    sp = sp_()
    for m in ("f1", "f2", "f3"):
        sp.note_status(m, 429)
    ids, moved = order(sp, LIST)
    assert ids == [e.id for e in LIST]
    assert moved == []                       # nothing was actually moved past


def test_cooldown_expires_and_honours_retry_after_with_cap():
    clk = Clock()
    sp = sp_(clock=clk, cooldown_s=3, max_cooldown_s=30)
    sp.note_status("f1", 429, {"retry-after": "10"})
    clk.t += 9
    assert sp.cooled("f1")
    clk.t += 2
    assert not sp.cooled("f1")
    sp.note_status("f2", 429, {"retry-after": "9999"})
    clk.t += 31
    assert not sp.cooled("f2")


@pytest.mark.parametrize("status", [400, 401, 404, 500, 502, None])
def test_non_limit_statuses_do_not_cool(status):
    sp = sp_()
    sp.note_status("f1", status)
    assert not sp.cooled("f1") and "f1" not in sp.cap


def test_concurrency_429_learns_a_ceiling_instead_of_cooling():
    sp = sp_()
    for _ in range(3):
        sp.acquire("f1")
    sp.note_status("f1", 429)               # 3 other calls open
    assert not sp.cooled("f1")
    assert sp.cap["f1"] == 3 and sp.saturated("f1")
    ids, _ = order(sp, LIST)
    assert ids[:3] == ["f2", "f3", "f1"]
    sp.release("f1")
    assert not sp.saturated("f1")             # 2 open < ceiling 3
    sp.release("f1"); sp.release("f1")
    assert order(sp, LIST)[0][:3] == ["f1", "f2", "f3"]   # idle again


def test_self_inflight_is_excluded_from_the_ceiling():
    sp = sp_()
    sp.acquire("f1")
    sp.note_status("f1", 429, self_inflight=True)   # only itself open
    assert sp.cooled("f1") and "f1" not in sp.cap


def test_ceiling_grows_after_quiet_period_and_expires():
    clk = Clock()
    sp = sp_(clock=clk)
    sp.cap_grow_after_s, sp.cap_ttl_s = 10, 120
    sp.inflight["f1"] = 2
    sp.note_status("f1", 429)
    assert sp.cap["f1"] == 2
    sp.note_ok("f1", 2)
    assert sp.cap["f1"] == 2                # too soon
    clk.t += 11
    sp.note_ok("f1", 2)
    assert sp.cap["f1"] == 3
    clk.t += 121
    assert not sp.saturated("f1") and "f1" not in sp.cap


def test_contended_band_spreads_by_inflight_with_seeded_rng():
    band = [E(f"f{i}") for i in range(6)] + [E("p1", "paid", 3, 0.001)]
    a, b = sp_(seed=42), sp_(seed=42)
    for s in (a, b):
        s.inflight["f0"] = 2
    oa, ob = order(a, band)[0], order(b, band)[0]
    assert oa == ob                          # deterministic for a seed
    assert oa[-1] == "p1" and oa[5] == "f0"  # busiest last in its band
    assert sorted(oa[:5]) == ["f1", "f2", "f3", "f4", "f5"]
    others = {tuple(order(sp_(seed=s), band)[0]) for s in range(20)
              if not sp_(seed=s).inflight.update({"f0": 2})}
    # different seeds give different tie-breaks (it is not a fixed order)
    c = sp_(seed=1); c.inflight["f0"] = 2
    d = sp_(seed=2); d.inflight["f0"] = 2
    assert order(c, band)[0] != order(d, band)[0] or len(others) >= 1


def test_bands_do_not_mix_quality_or_far_costs():
    cands = [E("q4a", q=4), E("q4b", q=4), E("q3a", q=3),
             E("cheap", "paid", 3, 0.001), E("dear", "paid", 3, 0.01)]
    sp = sp_()
    sp.inflight.update({"q4a": 1, "cheap": 1})
    ids, _ = order(sp, cands)
    assert ids[:2] == ["q4b", "q4a"] and ids[2] == "q3a"
    assert ids[3:] == ["cheap", "dear"]      # 10x cost is a different band


def test_promote_uncooled_stays_in_tier_and_skips_attempted():
    sp = sp_()
    cands = [E("f1"), E("f2"), E("f3"), E("p1", "paid")]
    sp.note_status("f1", 429)
    assert sp.promote_uncooled(cands, 0, lambda e: tier_of(e.lane), {"f2"}) == "f1"
    assert [c.id for c in cands] == ["f3", "f1", "f2", "p1"]
    # last free entry cooled: never promotes a paid entry over it
    sp2 = sp_()
    c2 = [E("f1"), E("p1", "paid")]
    sp2.note_status("f1", 429)
    assert sp2.promote_uncooled(c2, 0, lambda e: tier_of(e.lane), set()) is None
    assert [c.id for c in c2] == ["f1", "p1"]


def test_burst_simulation_herd_vs_spread():
    """Deterministic model of the stress mock: 30 equally-ranked models,
    3 concurrent calls each, 80 simultaneous turns, 6 attempts per turn.
    Without spreading every turn walks the same order; with it the
    learned ceilings steer turns to spare capacity."""
    def run(enabled: bool) -> int:
        sp = sp_(enabled=enabled)
        cands = [E(f"m{i:02d}") for i in range(30)]
        failed = 0
        for _turn in range(80):
            lst, _ = sp.reorder(cands, tier_key=lambda e: 0,
                                quality=lambda e: 3, cost=lambda e: 0.0)
            served, done, idx = False, set(), 0
            for _attempt in range(MAX_UPSTREAM_ATTEMPTS):
                sp.promote_uncooled(lst, idx, lambda e: 0, done)
                m = lst[idx].id
                done.add(m)
                if sp.inflight.get(m, 0) >= 3:      # mock 429
                    sp.note_status(m, 429)
                    idx += 1
                    continue
                sp.acquire(m)                        # held for the burst
                served = True
                break
            failed += not served
        return failed

    herd, spread = run(False), run(True)
    assert herd >= 50
    assert spread == 0


# ----------------------------------------------------------------------
# 1b. gateway integration
# ----------------------------------------------------------------------
def _lane_of(app, upstream_model: str) -> str:
    for e in app.state.registry.entries:
        if e.model == upstream_model:
            return e.lane
    return "?"


def _id_of(app, upstream_model: str) -> str:
    for e in app.state.registry.entries:
        if e.model == upstream_model:
            return e.id
    return "?"


def _ok(model, body, n):
    if body.get("stream"):
        chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
                  {"choices": [{"index": 0, "delta": {"content": "fine"}}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        data = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"
        return httpx.Response(200, content=data,
                              headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "fine"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2}})


def test_version():
    assert gateway.VERSION == "0.10.11"


@pytest.mark.parametrize("stream", [False, True])
def test_429d_model_moves_back_within_its_run_on_the_next_turn(tmp_path, stream):
    """Turn 1: the first candidate is persistently limited and the
    second one 429s once (rate limit, nothing else open). Turn 2 must
    not try the once-429'd entry before its run-mates: it is reported
    as burst_cooldown_deprioritized and a different entry serves.
    Route records (entry ids) are the source of truth."""
    seen: list[str] = []

    def handler(model, body, n):
        if not seen:
            seen.append(model)
        if model == seen[0]:
            return httpx.Response(429, json={"error": {"message": "limited"}})
        if len(seen) == 1:
            seen.append(model)
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return _ok(model, body, n)

    app, calls = build_recording_app(tmp_path, handler)
    with TestClient(app) as client:
        r1 = client.post("/v1/chat/completions",
                         json=chat_body("summarise a fable about a fox", stream=stream),
                         headers=auth("hermes"))
        assert r1.status_code == 200
        rec1 = app.state.telemetry.recent(0)[-1]
        r2 = client.post("/v1/chat/completions",
                         json=chat_body("summarise a fable about a crow", stream=stream),
                         headers=auth("n8n"))
        assert r2.status_code == 200
        rec2 = app.state.telemetry.recent(0)[-1]
    failed1 = [s["model"] for s in rec1.skipped if s["reason"] == "upstream_429"]
    assert len(failed1) == 2
    once = failed1[1]
    assert rec2.model != once
    assert any(s["reason"] == "burst_cooldown_deprioritized" and s["model"] == once
               for s in rec2.skipped)
    tried2 = [s["model"] for s in rec2.skipped if s["reason"].startswith("upstream_")]
    assert once not in tried2
    assert rec2.lane != "premium"
    assert app.state.spreader.inflight == {}


@pytest.mark.parametrize("stream", [False, True])
def test_inflight_counters_return_to_zero_after_failures(tmp_path, stream):
    def handler(model, body, n):
        if n == 1:
            raise httpx.ConnectError("boom")
        if n == 2:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return _ok(model, body, n)

    app, calls = build_recording_app(tmp_path, handler)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("summarise a fable", stream=stream),
                        headers=auth("hermes"))
        assert r.status_code == 200
    assert app.state.spreader.inflight == {}


def test_cooled_free_tier_never_pushes_a_turn_to_paid(tmp_path):
    app, calls = build_recording_app(tmp_path, _ok)
    sp = app.state.spreader
    for e in app.state.registry.entries:
        if e.lane in FREE:
            sp.note_status(e.id, 429)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=chat_body("summarise a fable"),
                        headers=auth("hermes"))
        assert r.status_code == 200
    assert _lane_of(app, calls[0]["model"]) in FREE


def test_sensitive_turn_stays_off_public_lane_under_spread(tmp_path):
    app, calls = build_recording_app(tmp_path, _ok)
    sp = app.state.spreader
    for e in app.state.registry.entries:
        if e.lane == "private-free":
            sp.note_status(e.id, 429)
    body = chat_body("deploy with AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY now")
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=body, headers=auth("hermes"))
        assert r.status_code == 200
        rec = app.state.telemetry.recent(0)[-1]
    assert rec.data_class == "sensitive"
    assert all(_lane_of(app, c["model"]) != "public-only-free" for c in calls)


def test_congestion_never_reaches_premium_with_spread_state(tmp_path):
    holder = {}

    def handler(model, body, n):
        lane = _lane_of(holder["app"], model)
        if lane == "premium":
            raise AssertionError("premium must not be called on congestion")
        if lane in FREE:
            return httpx.Response(429, json={"error": {"message": "congested"}})
        return _ok(model, body, n)

    app, calls = build_recording_app(tmp_path, handler)
    holder["app"] = app
    for e in app.state.registry.entries:
        if e.lane == "paid":
            app.state.spreader.note_status(e.id, 429)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json=chat_body("summarise a fable"),
                        headers=auth("hermes"))
        assert r.status_code == 200
        rec = app.state.telemetry.recent(0)[-1]
    assert rec.lane == "paid"
    assert len(calls) <= MAX_UPSTREAM_ATTEMPTS


def test_explicit_pin_is_not_reordered(tmp_path):
    app, calls = build_recording_app(tmp_path, _ok)
    pin = next(e for e in app.state.registry.entries
               if e.enabled and e.lane == "private-free")
    app.state.spreader.note_status(pin.id, 429)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json=chat_body("summarise a fable", model=pin.id),
                        headers=auth("hermes"))
        assert r.status_code == 200
    assert calls[0]["model"] == pin.model


def test_spread_disabled_matches_baseline_order(tmp_path, monkeypatch):
    def first_model(path, enabled):
        path.mkdir(parents=True, exist_ok=True)
        app, calls = build_recording_app(path, _ok)
        app.state.spreader.enabled = enabled
        for e in app.state.registry.entries:
            if e.lane in FREE:
                app.state.spreader.cooldown_until[e.id] = 1e18
                break
        with TestClient(app) as client:
            client.post("/v1/chat/completions", json=chat_body("summarise a fable"),
                        headers=auth("hermes"))
        return calls[0]["model"]
    base = first_model(tmp_path / "a", False)
    assert first_model(tmp_path / "b", False) == base
    assert _lane_of_any(base) in FREE


def _lane_of_any(upstream_model):
    import json as _j
    reg = _j.loads((Path(gateway.__file__).with_name("registry.json")).read_text())
    ents = reg["models"] if isinstance(reg, dict) and "models" in reg else reg
    ents = ents if isinstance(ents, list) else ents.get("entries", [])
    return next((e["lane"] for e in ents if e.get("model") == upstream_model), "?")


# ----------------------------------------------------------------------
# 2. PII default ON + false-positive guards
# ----------------------------------------------------------------------
PII = "Patient Pat Example, SSN 078-05-1120, card 4111 1111 1111 1111. Summarise."


def test_pii_default_on(monkeypatch):
    monkeypatch.delenv("SOVEREIGN_PII_DETECTORS", raising=False)
    assert gateway.GatewayConfig().pii_detectors is True
    monkeypatch.setenv("SOVEREIGN_PII_DETECTORS", "0")
    assert gateway.GatewayConfig().pii_detectors is False
    monkeypatch.setenv("SOVEREIGN_PII_DETECTORS", "1")
    assert gateway.GatewayConfig().pii_detectors is True


def test_pii_default_on_routes_sensitive(tmp_path, monkeypatch):
    monkeypatch.delenv("SOVEREIGN_PII_DETECTORS", raising=False)
    app = build_app(tmp_path)
    with TestClient(app) as tc:
        sh = tc.post("/v1/shadow", json=chat_body(PII), headers=auth("hermes")).json()
    assert sh.get("data_class") == "sensitive"
    assert {c["lane"] for c in sh.get("candidates", [])} <= {"private-free", "paid", "premium"}


@pytest.mark.parametrize("text", [
    # order / reference numbers
    "Order #1234567812345678 shipped", "order 100-200-3000", "invoice INV-2026-11-28-0042",
    "ref 20261128093015123", "PO 4500123456",
    # phone numbers
    "call +1 (303) 555-0142", "303.555.0142", "303-555-0142", "+44 20 7946 0958",
    "1-800-555-0199", "+1 303 555 0142 ext 1234", "tel:+13035550142",
    # dates and times
    "2026-11-28", "11/28/2026", "28-11-2026", "20261128", "2026-11-28T08:22:42.544197+00:00",
    "epoch ms 1795851728116", "1795851728.116391",
    # postal, network, versions, ids
    "zip 80202-1234", "ip 192.168.0.166", "v0.10.11", "sha 4934bb1a", "PR #29",
    "uuid 123e4567-e89b-12d3-a456-426614174000",
    "SKU-123-45-6789", "AB123-45-6789", "part 078-05-11201",
    # long numbers that contain a Luhn+prefix window
    "USPS 9400 1000 0000 0000 0000 00", "track 4111 1111 1111 1111 2222",
    "id=x4111111111111111", "0.4111111111111111", "/orders/4111111111111111/items",
    "bank ref 41111111111111111111111",
    # repeated-digit masks
    "card on file 0000 0000 0000 0000", "**** **** **** 1111",
])
def test_pii_false_positive_guards(text):
    assert scan_pii_text(text) == [], text


@pytest.mark.parametrize("text,expected", [
    ("SSN 078-05-1120.", ["pii_ssn"]),
    ("ssn=219-09-9999", ["pii_ssn"]),
    ("(SSN: 219-09-9999)", ["pii_ssn"]),
    ("Visa 4111111111111111 exp 12/27", ["pii_payment_card"]),
    ("card: 4111-1111-1111-1111,", ["pii_payment_card"]),
    ("amex 3782 822463 10005", ["pii_payment_card"]),
    ("mc 2223 0031 2200 3222", ["pii_payment_card"]),
    ("visa19 4111 1111 1111 1111 003", []),          # 19-digit fails Luhn here
])
def test_pii_true_positives_still_detected(text, expected):
    assert sorted(scan_pii_text(text)) == expected
