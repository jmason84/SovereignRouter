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


def _seed_day(store: QuotaStore, client: str, days_ago: int, n: int,
              hi_frac: float, *, lane="paid", usd=0.001, provider="deepinfra"):
    """Seed `n` usage rows at midday of the day `days_ago` before today
    (local), `hi_frac` of them at level 4."""
    lt = time.localtime()
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    if days_ago == 0:
        ts = time.time() - 900  # inside today, safely before `now`
    else:
        ts = midnight - days_ago * DAY + 3600
    hi_n = round(n * hi_frac)
    for i in range(n):
        store.record_usage(UsageEvent(
            ts=ts + i, client_id=client, provider=provider, model_id="m",
            lane=lane, level=4 if i < hi_n else 2, task_class="chat",
            input_tokens=100, output_tokens=10, cached_tokens=0,
            cost_usd=usd, estimated=False))


def test_level_mix_alert(store):
    # F-14 (v0.9.1): baseline-relative — 7 baseline days at 50% L4-5,
    # today 250 requests at 90%: share exceeds baseline + 10 points.
    gov = Governor(store)
    now = time.time()
    for back in range(1, 8):
        _seed_day(store, "hermes", back, 100, 0.50)
    _seed_day(store, "hermes", 0, 250, 0.90)
    gov.evaluate_alerts({"hermes": _policy()}, {}, now)
    kinds = [a["kind"] for a in store.alerts_since(now - 3600)]
    assert "level_mix" in kinds


def test_level_mix_silent_at_baseline(store):
    # Today matches the trailing baseline (both ~78%, Hermes' real
    # operating point) — the pre-v0.9.1 static 40% threshold paged on
    # exactly this; F-14 must not.
    gov = Governor(store)
    now = time.time()
    for back in range(1, 8):
        _seed_day(store, "hermes", back, 300, 0.78)
    _seed_day(store, "hermes", 0, 300, 0.80)
    gov.evaluate_alerts({"hermes": _policy()}, {}, now)
    kinds = [a["kind"] for a in store.alerts_since(now - 3600)]
    assert "level_mix" not in kinds


def test_level_mix_silent_below_sample_floor(store):
    # 100 requests at 100% L4-5 against a 0% baseline: a real signal
    # in miniature, but below the n >= 200 floor it cannot fire.
    gov = Governor(store)
    now = time.time()
    for back in range(1, 8):
        _seed_day(store, "hermes", back, 100, 0.0)
    _seed_day(store, "hermes", 0, 100, 1.0)
    gov.evaluate_alerts({"hermes": _policy()}, {}, now)
    kinds = [a["kind"] for a in store.alerts_since(now - 3600)]
    assert "level_mix" not in kinds


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


# ----------------------------------------------------------------------
# F-13 (v0.9.1): spend alerts compute on the ACTUALS window — November
# 2026's pre-cutover ledger slice is a ceiling, not spend. Cutover:
# 2026-11-13 21:33:54 MST (epoch 1794630834). Seeds use local midday
# timestamps so they sit on the same side of the cutover in any TZ the
# suite runs in: Nov 10 is pre-cutover everywhere, Nov 14 post.
# ----------------------------------------------------------------------
_NOV10 = time.mktime((2026, 11, 10, 12, 0, 0, 0, 0, -1))
_NOV14 = time.mktime((2026, 11, 14, 12, 0, 0, 0, 0, -1))
_NOV15 = time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1))


def _seed_at(store: QuotaStore, ts: float, client: str, usd: float, *,
             lane="paid", provider="deepinfra"):
    store.record_usage(UsageEvent(
        ts=ts, client_id=client, provider=provider, model_id="m",
        lane=lane, level=2, task_class="chat", input_tokens=1000,
        output_tokens=100, cached_tokens=0, cost_usd=usd, estimated=False))


def test_premium_alert_ignores_pre_cutover_ceiling(store):
    # The A1 shape: $25 of pre-cutover ceiling + $0.05 of actuals must
    # NOT trip the $20 premium alert (actuals are $0.05).
    gov = Governor(store)
    _seed_at(store, _NOV10, "openclaw", 25.0, lane="premium", provider="openai")
    _seed_at(store, _NOV14, "openclaw", 0.05, lane="premium", provider="openai")
    pol = _policy("openclaw", 3.0, premium_monthly_alert_usd=20.0)
    gov.evaluate_alerts({"openclaw": pol}, {}, _NOV15)
    kinds = [a["kind"] for a in store.alerts_since(_NOV10 - 60)]
    assert "premium_monthly_spend" not in kinds


def test_premium_alert_fires_on_actuals_and_annotates_ceiling(store):
    gov = Governor(store)
    _seed_at(store, _NOV10, "openclaw", 25.0, lane="premium", provider="openai")
    _seed_at(store, _NOV14, "openclaw", 21.0, lane="premium", provider="openai")
    pol = _policy("openclaw", 3.0, premium_monthly_alert_usd=20.0)
    gov.evaluate_alerts({"openclaw": pol}, {}, _NOV15)
    alerts = [a for a in store.alerts_since(_NOV10 - 60)
              if a["kind"] == "premium_monthly_spend"]
    assert len(alerts) == 1
    assert "$21.00" in alerts[0]["message"]
    assert "Actuals basis (F-13)" in alerts[0]["message"]
    assert "$25.00 pre-cutover" in alerts[0]["message"]


def test_provider_spend_alert_ignores_pre_cutover_ceiling(store):
    # The A3 shape: $150 ceiling + $0.06 actuals vs the $10 level.
    gov = Governor(store)
    _seed_at(store, _NOV10, "hermes", 150.0, provider="deepseek")
    _seed_at(store, _NOV14, "hermes", 0.06, provider="deepseek")
    gov.evaluate_alerts({"hermes": _policy()}, {"deepseek": 10.0}, _NOV15)
    kinds = [a["kind"] for a in store.alerts_since(_NOV10 - 60)]
    assert "provider_spend" not in kinds


def test_provider_spend_alert_fires_on_actuals_and_annotates(store):
    gov = Governor(store)
    _seed_at(store, _NOV10, "hermes", 150.0, provider="deepseek")
    _seed_at(store, _NOV14, "hermes", 11.0, provider="deepseek")
    gov.evaluate_alerts({"hermes": _policy()}, {"deepseek": 10.0}, _NOV15)
    alerts = [a for a in store.alerts_since(_NOV10 - 60)
              if a["kind"] == "provider_spend"]
    assert len(alerts) == 1
    assert "$11.00" in alerts[0]["message"]
    assert "Actuals basis (F-13)" in alerts[0]["message"]
    assert "$150.00 pre-cutover" in alerts[0]["message"]


# ----------------------------------------------------------------------
# F-15 (v0.9.1): the sensitive posture digest replaces the daily
# guaranteed-fire alert — one digest record per day, carrying the
# trailing-24h sensitive serves and the cleared-set status.
# ----------------------------------------------------------------------
def test_sensitive_posture_digest_emitted_once_with_serves(store):
    gov = Governor(store)
    now = time.time()
    store.record_usage(UsageEvent(
        ts=now - 3600, client_id="hermes", provider="openrouter",
        model_id="openrouter/dots-3-note-preview-free", lane="private-free",
        level=4, task_class="infra", input_tokens=5000, output_tokens=10,
        cached_tokens=0, cost_usd=0.0, estimated=False,
        data_class="sensitive"))
    gov.evaluate_alerts({"hermes": _policy()}, {}, now)
    gov.evaluate_alerts({"hermes": _policy()}, {}, now)  # dedup: still one
    digests = [a for a in store.alerts_since(now - 7200)
               if a["kind"] == "sensitive_posture_digest"]
    assert len(digests) == 1
    assert "openrouter/dots-3-note-preview-free" in digests[0]["message"]
    assert "NO provider is owner-cleared" in digests[0]["message"]


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
