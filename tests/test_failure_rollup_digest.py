"""Daily failure-rollup digest (2026-11-25 census, alerting gap).

The census week's two dominant attempt-level failure classes —
congested 4,758 and entry_missing 441 — generated NO alerts at all:
no alert kind covers pool exhaustion or dead/intermittently-dead
registry entries, so entry_missing ran for six days (Nov 19–25)
before a manual census noticed it. The governor's alert kinds all
watch money or sensitivity; attempt-level failure classes watched
nobody.

v0.10.1 adds failure_rollup_digest to the governor's daily digest
family (same once-per-day dedup as sensitive_posture_digest): when
the trailing 24h contains entry_missing attempts, pool-scope
congested attempts, or request_fault attempts, one digest alert
summarizes the counts with the top offenders. Silence when the
trailing day is clean — this is a rollup, not a pager.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clients import ClientPolicy  # noqa: E402
from governor import Governor  # noqa: E402
from quota import AttemptEvent, QuotaStore  # noqa: E402

NOW = time.time()


def _policy(client="hermes", target=1.0) -> ClientPolicy:
    return ClientPolicy(id=client, key_sha256="x",
                        daily_target_usd=target)


def _attempt(store, *, ago_s, model_id, provider, pool_id, outcome="failed",
             status=None, failure_class=None, scope=None,
             scope_target=None):
    store.record_attempt(AttemptEvent(
        ts=NOW - ago_s, client_id="n8n", provider=provider,
        model_id=model_id, pool_id=pool_id, outcome=outcome,
        status=status, failure_class=failure_class, scope=scope,
        scope_target=scope_target))


def _seed_week_shape(store):
    # Mirrors the census week's shape at small scale.
    for i in range(2):
        _attempt(store, ago_s=3600 + i, status=404,
                 model_id="openrouter/north-mini-code-free",
                 provider="openrouter", pool_id="openrouter-free",
                 failure_class="entry_missing", scope="model",
                 scope_target="openrouter/north-mini-code-free")
    _attempt(store, ago_s=3500, status=404,
             model_id="openrouter/dots-3-note-preview-free",
             provider="openrouter", pool_id="openrouter-free",
             failure_class="entry_missing", scope="model",
             scope_target="openrouter/dots-3-note-preview-free")
    for i in range(5):
        _attempt(store, ago_s=3000 + i, status=429,
                 model_id="cloudflare/gpt-oss-120b",
                 provider="cloudflare", pool_id="cloudflare:x",
                 failure_class="congested", scope="pool",
                 scope_target="cloudflare-workers-ai")
    for i in range(2):
        _attempt(store, ago_s=2000 + i, status=400,
                 model_id="google/gemini-3.1-flash-lite",
                 provider="google", pool_id="google:x",
                 failure_class="request_fault", scope="request",
                 scope_target="-")
    # A model-scope 429 is ordinary free-tier congestion noise; it
    # must NOT by itself trigger or inflate the digest's pool line.
    _attempt(store, ago_s=1500, status=429,
             model_id="groq/gpt-oss-120b", provider="groq",
             pool_id="groq:x", failure_class="congested", scope="model",
             scope_target="groq/gpt-oss-120b")


def _digests(store):
    return [a for a in store.alerts_since(0)
            if a["kind"] == "failure_rollup_digest"]


def test_rollup_digest_fires_with_counts(tmp_path):
    store = QuotaStore(tmp_path / "quota.db")
    _seed_week_shape(store)
    gov = Governor(store)
    gov.evaluate_alerts({"hermes": _policy()}, {}, NOW)
    digests = _digests(store)
    assert len(digests) == 1
    msg = digests[0]["message"]
    assert "3 entry_missing" in msg
    assert "openrouter/north-mini-code-free" in msg
    assert "5 pool-scope congested" in msg
    assert "cloudflare-workers-ai" in msg
    assert "2 request_fault" in msg


def test_rollup_digest_dedups_per_day(tmp_path):
    store = QuotaStore(tmp_path / "quota.db")
    _seed_week_shape(store)
    gov = Governor(store)
    gov.evaluate_alerts({"hermes": _policy()}, {}, NOW)
    gov.evaluate_alerts({"hermes": _policy()}, {}, NOW + 600)
    assert len(_digests(store)) == 1


def test_rollup_digest_silent_on_clean_day(tmp_path):
    store = QuotaStore(tmp_path / "quota.db")
    _attempt(store, ago_s=600, model_id="groq/gpt-oss-120b",
             provider="groq", pool_id="groq:x", outcome="served",
             status=200)
    _attempt(store, ago_s=500, status=429,
             model_id="groq/gpt-oss-120b", provider="groq",
             pool_id="groq:x", failure_class="congested", scope="model",
             scope_target="groq/gpt-oss-120b")
    gov = Governor(store)
    gov.evaluate_alerts({"hermes": _policy()}, {}, NOW)
    assert _digests(store) == []


def test_rollup_digest_ignores_stale_attempts(tmp_path):
    store = QuotaStore(tmp_path / "quota.db")
    _attempt(store, ago_s=30 * 3600, status=404,
             model_id="openrouter/north-mini-code-free",
             provider="openrouter", pool_id="openrouter-free",
             failure_class="entry_missing", scope="model",
             scope_target="openrouter/north-mini-code-free")
    gov = Governor(store)
    gov.evaluate_alerts({"hermes": _policy()}, {}, NOW)
    assert _digests(store) == []
