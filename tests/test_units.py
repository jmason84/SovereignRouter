import asyncio

import pytest

from sovereign_router.classifier import Classification, classify, classify_rules
from sovereign_router.registry import RegistryError, parse_model
from sovereign_router.sensitivity import contains_secret
from sovereign_router.validate import validate_message


def test_infra_term_alone_is_not_complex():
    assert classify_rules("what is the status of the docker container on vm103?").level <= 2


def test_infra_plus_change_is_complex_and_critical_is_5():
    assert classify_rules("migrate the docker stack to the new vm").level == 4
    assert classify_rules("rotate the production firewall credentials on proxmox").level == 5


def test_alias_and_header_override():
    assert classify_rules("hello", alias="strong").level == 4
    assert classify_rules("migrate docker", header_level=1).level == 1


def test_trivial():
    assert classify_rules("say hello").level == 1


async def test_sidecar_timeout_falls_back_to_rules():
    async def slow(text):
        await asyncio.sleep(5)
    c = await classify("something vague here", sidecar=slow, timeout=0.05)
    assert c.layer == "fallback"


async def test_sidecar_used_when_low_confidence():
    async def sc(text):
        return Classification(3, "chat", 0.9, "sidecar")
    assert (await classify("something vague here", sidecar=sc)).layer == "sidecar"


def test_secrets_detected_but_hex_ids_are_not():
    assert contains_secret("key is sk-abcdefghijklmnopqrstuvwxyz123456")
    assert contains_secret("password = hunter2hunter2")
    assert contains_secret("-----BEGIN RSA PRIVATE KEY-----")
    assert not contains_secret("container 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08 is up")
    assert not contains_secret("restart the nginx container please")


def test_validate_message():
    ok = {"content": "hi"}
    assert validate_message(ok, "stop", tool_names=set(), json_mode=False) is None
    assert validate_message({"content": ""}, "stop", tool_names=set(), json_mode=False) == "empty"
    assert validate_message({"content": ""}, "length", tool_names=set(), json_mode=False) == "length_no_content"
    call = {"function": {"name": "nope", "arguments": "{}"}}
    assert validate_message({"tool_calls": [call]}, "tool_calls", tool_names={"x"}, json_mode=False) == "unknown_tool"
    bad = {"function": {"name": "x", "arguments": "{oops"}}
    assert validate_message({"tool_calls": [bad]}, "tool_calls", tool_names={"x"}, json_mode=False) == "bad_tool_args"
    assert validate_message({"content": "not json"}, "stop", tool_names=set(), json_mode=True) == "bad_json"


def test_registry_validation():
    base = {"id": "a", "provider": "p", "model": "m", "base_url": "https://x/v1", "lane": "private-free"}
    parse_model(base)
    with pytest.raises(RegistryError):
        parse_model({**base, "base_url": "http://x/v1"})
    with pytest.raises(RegistryError):
        parse_model({**base, "price_in": 1})  # free lane with a price
    with pytest.raises(RegistryError):
        parse_model({**base, "lane": "paid"})  # paid lane without a price
