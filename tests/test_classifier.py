"""Classifier rules: the headline v0.5 fix is that infrastructure terms
ALONE never raise the level — only infra + change/diagnosis verb does."""

from __future__ import annotations

import asyncio

import httpx

from classifier import Classifier, classify_rules


def test_infra_mention_alone_does_not_raise():
    r = classify_rules("what is the status of the docker containers on the server?")
    assert r.level <= 2
    assert r.task_class == "infra"
    assert "infra_mention_only" in r.signals


def test_infra_plus_delete_raises():
    mention = classify_rules("show me the docker container list")
    action = classify_rules("delete the docker container for the old stack")
    assert action.level >= 4
    assert action.level > mention.level
    assert "infra+change_strong" in action.signals


def test_infra_plus_restart_is_level3():
    r = classify_rules("restart the nginx container")
    assert r.level == 3
    assert r.task_class == "infra"


def test_infra_plus_root_cause_is_level4():
    r = classify_rules("find the root cause of the docker container crash")
    assert r.level == 4
    assert "infra+diagnosis_strong" in r.signals


def test_infra_plus_why_is_level3():
    r = classify_rules("why did the backup job fail last night?")
    assert r.level == 3


def test_greeting_is_trivial():
    assert classify_rules("hello").level == 1
    assert classify_rules("turn this into a bullet list please").level == 1


def test_default_is_routine_level2():
    r = classify_rules("summarize the meeting notes from yesterday for the team")
    assert r.level == 2


def test_long_ask_raises_to_3():
    r = classify_rules("please review this", est_input_tokens=25_000)
    assert r.level >= 3


def test_very_long_is_long_context_level4():
    r = classify_rules("review", est_input_tokens=80_000)
    assert r.level == 4
    assert r.task_class == "long-context"


def test_images_raise_to_3():
    r = classify_rules("what is in this picture?", has_images=True)
    assert r.level >= 3


def test_alias_and_header_overrides():
    assert classify_rules("anything", alias="premium").level == 5
    assert classify_rules("anything", alias="premium").explicit_premium
    assert classify_rules("anything", alias="cheap").level == 2
    r = classify_rules("anything", level_override=4)
    assert r.level == 4 and r.layer == "header"
    r = classify_rules("anything", alias="code")
    assert r.task_class == "code"


def test_code_task_levels():
    assert classify_rules("write a function to parse a csv file").level == 3
    assert classify_rules("refactor the parser function").level == 4


# ----------------------------------------------------------------------
# sidecar behaviour: timeout/failure falls back to rules and is flagged;
# tie between two levels in 3..5 resolves to the higher level.
# ----------------------------------------------------------------------
def _classifier_with_transport(handler) -> Classifier:
    transport = httpx.MockTransport(handler)
    return Classifier(sidecar_url="http://sidecar.test",
                      http_client=httpx.AsyncClient(transport=transport))


def test_sidecar_failure_falls_back_to_rules():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    clf = _classifier_with_transport(boom)
    result = asyncio.run(clf.classify("summarize the meeting notes from yesterday"))
    assert result.layer == "rules"
    assert result.sidecar_fallback is True
    assert result.level == 2


def test_sidecar_success_and_tie_goes_higher():
    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"level": 3, "task_class": "reasoning",
                                         "confidence": 0.9})

    clf = _classifier_with_transport(ok)
    # rules say 2 (not confident) -> sidecar consulted; sidecar 3 wins.
    result = asyncio.run(clf.classify("summarize the meeting notes from yesterday"))
    assert result.layer == "sidecar"
    assert result.level == 3


def test_sidecar_not_called_when_rules_confident():
    calls = []

    def ok(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"level": 1, "task_class": "chat",
                                         "confidence": 0.9})

    clf = _classifier_with_transport(ok)
    result = asyncio.run(clf.classify("delete the docker container for the old stack"))
    assert result.level == 4
    assert calls == []  # confident rules decision: sidecar untouched
