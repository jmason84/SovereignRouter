"""Cost governor: steering above target, runaway thresholds (fixed for
the first 14 days), level-mix alert, emergency switch default OFF."""

from __future__ import annotations

import time

import pytest

from clients import ClientPolicy
from governor import EmergencyConfig, Governor
from quota import QuotaStore, UsageEvent

DAY = 86_400.0


@pytest.fixture()
def store(tmp_path):
    return QuotaStore(tmp_path / "quota.db")


def _spend(store: QuotaStore, client: str, usd: float, *, lane="paid",
           level=2, ago_s=60.0, provider="deepinfra"):
    # Clamp the seeded timestamp into TODAY (local). The governor's daily
    # windows are calendar-day based by design, so a raw `now - 7200`
    # seed lands yesterday when the suite runs shortly after midnight
    # and the daily alerts correctly do not fire (flake found 2026-11-02
    # 00:1x MST: test_runaway_client_over_2x_target, test_premium_alerts).
    ts = time.time() - ago_s
    day0 = time.mktime(time.strptime(
        time.strftime("%Y-%m-%d", time.localtime()), "%Y-%m-%d"))
    ts = max(ts, day0 + 1)
    store.record_usage(UsageEvent(
        ts=ts, client_id=client, provider=provider,
        model_id="m", lane=lane, level=level, task_class="chat",
        input_tokens=1000, output_tokens=100, cached_tokens=0,
        cost_usd=usd, estimated=False))


def _policy(client="hermes", target=1.0, **kw) -> ClientPolicy:
    return ClientPolicy(id=client, key_sha256="x", daily_target_usd=target, **kw)


# ----------------------------------------------------------------------
def test_steering_below_target_is_neutral(store):
    gov = Governor(store)
    st = gov.steering("hermes", 1.0, 2)
    assert not st.above_target
    assert st.required_quality_delta == 0
    assert not st.cheapest_quality4_only
    assert not st.premium_explicit_only


def test_steering_above_target(store):
    _spend(store, "hermes", 1.50)
    gov = Governor(store)
    st2 = gov.steering("hermes", 1.0, 2)
    assert st2.above_target and st2.required_quality_delta == -1
    st4 = gov.steering("hermes", 1.0, 4)
    assert st4.cheapest_quality4_only
    assert st4.premium_explicit_only
    # one alert per day only
    gov.steering("hermes", 1.0, 2)
    alerts = store.alerts_since(time.time() - 3600)
    assert sum(1 for a in alerts if a["kind"] == "client_above_target") == 1


def test_level_mix_alert(store):
    gov = Governor(store)
    now = time.time()
    for i in range(25):
        _spend(store, "hermes", 0.01, level=4 if i < 15 else 2, ago_s=120)
    gov.evaluate_alerts({"hermes": _policy()}, {}, now)
    kinds = [a["kind"] for a in store.alerts_since(now - 3600)]
    assert "level_mix" in kinds


def test_runaway_fixed_threshold_first_14_days(store):
    gov = Governor(store)  # fresh store: first_seen = now -> fixed window
    _spend(store, "hermes", 0.60, ago_s=600)  # $0.60 in the last hour
    gov.evaluate_alerts({"hermes": _policy()}, {}, time.time())
    kinds = [a["kind"] for a in store.alerts_since(time.time() - 3600)]
    assert "runaway_hourly" in kinds


def test_runaway_client_over_2x_target(store):
    gov = Governor(store)
    _spend(store, "n8n", 1.20, ago_s=7200)  # >2x the $0.50 target, not last hour
    gov.evaluate_alerts({"n8n": _policy("n8n", 0.5)}, {}, time.time())
    kinds = [a["kind"] for a in store.alerts_since(time.time() - 3600)]
    assert "runaway_client" in kinds


def test_no_runaway_under_thresholds(store):
    gov = Governor(store)
    _spend(store, "hermes", 0.10, ago_s=600)
    gov.evaluate_alerts({"hermes": _policy()}, {}, time.time())
    kinds = [a["kind"] for a in store.alerts_since(time.time() - 3600)]
    assert "runaway_hourly" not in kinds
    assert "runaway_client" not in kinds


def test_provider_spend_alert(store):
    gov = Governor(store)
    now = time.time()
    _spend(store, "hermes", 11.0, ago_s=3600)
    gov.evaluate_alerts({"hermes": _policy()}, {"deepinfra": 10.0}, now)
    kinds = [a["kind"] for a in store.alerts_since(now - 3600)]
    assert "provider_spend" in kinds


def test_premium_alerts(store):
    gov = Governor(store)
    now = time.time()
    for _ in range(21):
        _spend(store, "openclaw", 0.05, lane="premium", ago_s=300,
               provider="openai")
    pol = _policy("openclaw", 3.0, premium_monthly_alert_usd=0.50,
                  premium_daily_count_alert=20)
    gov.evaluate_alerts({"openclaw": pol}, {}, now)
    kinds = [a["kind"] for a in store.alerts_since(now - 3600)]
    assert "premium_daily_count" in kinds
    assert "premium_monthly_spend" in kinds  # 21 x $0.05 = $1.05 > $0.50


def test_emergency_switch_default_off(store):
    gov = Governor(store)  # default: disabled pending owner decision
    _spend(store, "hermes", 100.0, ago_s=60)
    off, _ = gov.paid_lanes_off()
    assert off is False


def test_emergency_switch_trips_and_clears(store):
    gov = Governor(store, emergency=EmergencyConfig(enabled=True, daily_limit_usd=25.0))
    _spend(store, "hermes", 30.0, ago_s=60)
    off, reason = gov.paid_lanes_off()
    assert off is True and reason == "emergency_paid_lane_off"
    gov.clear_emergency()
    off, _ = gov.paid_lanes_off()
    assert off is False


def test_emergency_warn_only_alerts_but_never_vetoes(store):
    # v0.5.7 (owner ruling 2026-11-09): at the limit the governor
    # warns and the paid lanes stay ON — no clear needed, no veto.
    gov = Governor(store, emergency=EmergencyConfig(
        enabled=True, daily_limit_usd=25.0, warn_only=True))
    _spend(store, "hermes", 30.0, ago_s=60)
    now = time.time()
    off, reason = gov.paid_lanes_off(now)
    assert off is False and reason == ""
    alerts = [a for a in store.alerts_since(now - 3600)
              if a["kind"] == "emergency_paid_lane_switch"]
    assert len(alerts) == 1 and "REMAIN ON" in alerts[0]["message"]
    # Repeat calls stay off and do not duplicate the alert.
    off, _ = gov.paid_lanes_off(now)
    assert off is False
    alerts = [a for a in store.alerts_since(now - 3600)
              if a["kind"] == "emergency_paid_lane_switch"]
    assert len(alerts) == 1
    # Below the level: no alert at all.
    gov2 = Governor(store, emergency=EmergencyConfig(
        enabled=True, daily_limit_usd=250.0, warn_only=True))
    off, _ = gov2.paid_lanes_off(now)
    assert off is False
    alerts = [a for a in store.alerts_since(now - 3600)
              if a["kind"] == "emergency_paid_lane_switch"]
    assert len(alerts) == 1


def test_spend_trend_alert(store):
    gov = Governor(store)
    now = time.time()
    day0 = now - (now % 1)  # not used; ages below place spend on prior days
    # 3 rising days ending above target: place at day boundaries via ago
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    for days_ago, usd in ((3, 0.60), (2, 0.80), (1, 1.20)):
        ts = midnight - (days_ago - 1) * DAY - 3600
        store.record_usage(UsageEvent(
            ts=ts, client_id="hermes", provider="deepinfra", model_id="m",
            lane="paid", level=2, task_class="chat", input_tokens=100,
            output_tokens=10, cached_tokens=0, cost_usd=usd, estimated=False))
    gov.evaluate_alerts({"hermes": _policy()}, {}, now)
    kinds = [a["kind"] for a in store.alerts_since(now - 3600)]
    assert "spend_trend" in kinds
