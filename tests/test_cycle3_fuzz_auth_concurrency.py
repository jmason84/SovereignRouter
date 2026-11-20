"""Cycle-3 offline hardening: seeded fuzzing of the sensitive wall,
auth-header bypass variants, and ToolHealth concurrency/persistence.

Deterministic (fixed seeds) so a failure reproduces.
"""
from __future__ import annotations

import json
import random
import string
import threading

import pytest
from conftest import RAW_KEYS, auth, chat_body  # noqa: F401

from clients import ClientStore, extract_bearer_key, sha256_hex, ClientPolicy
from sensitivity import scan_messages, scan_text
from tool_health import PERSIST_EVERY, WINDOW, ToolHealth

RNG = random.Random(20261120)


def _rand(n, alphabet=string.ascii_letters + string.digits):
    return "".join(RNG.choice(alphabet) for _ in range(n))


# Known-format credentials (all fake, generated).
def _secrets():
    return [
        "sk-" + _rand(32),
        "gsk_" + _rand(28),
        "AKIA" + "".join(RNG.choice(string.ascii_uppercase + string.digits) for _ in range(16)),
        "ghp_" + _rand(30),
        "github_pat_" + _rand(30),
        "glpat-" + _rand(24),
        "xoxb-" + _rand(20),
        "AIza" + _rand(30),
        "Bearer " + _rand(30),
        "-----BEGIN RSA PRIVATE KEY-----",
        "postgres://admin:" + _rand(10) + "@db.internal:5432/app",
        "password=" + _rand(6) + "9" + _rand(6),
    ]


FILLERS = [
    "{s}", "key: {s}", "here you go\n{s}\nthanks", "```\n{s}\n```",
    '{{"token": "{s}"}}', "  \t {s}  ", "x" * 500 + " {s} " + "y" * 500,
    "line1\r\nline2 {s}\r\n", "é ü 日本語 {s} 😀",
]


def test_fuzz_known_formats_always_detected_in_any_wrapper():
    misses = []
    for _ in range(30):
        for s in _secrets():
            for f in FILLERS:
                text = f.replace("{s}", s)
                if not scan_text(text).sensitive:
                    misses.append((s[:12], f[:20]))
    assert not misses, misses[:10]


def test_fuzz_detected_in_every_message_shape():
    for s in _secrets():
        shapes = [
            [{"role": "user", "content": s}],
            [{"role": "user", "content": [{"type": "text", "text": s}]}],
            [{"role": "system", "content": "x"}, {"role": "tool", "content": s}],
            [{"role": "assistant", "content": s}],
        ]
        for m in shapes:
            assert scan_messages(m).sensitive, (s[:10], m[0]["role"])


def test_fuzz_benign_identifiers_never_flagged():
    flagged = []
    for _ in range(300):
        cands = [
            "".join(RNG.choice("0123456789abcdef") for _ in range(RNG.choice([12, 32, 40, 64]))),
            "-".join(["".join(RNG.choice("0123456789abcdef") for _ in range(n))
                      for n in (8, 4, 4, 4, 12)]),
            "commit " + "".join(RNG.choice("0123456789abcdef") for _ in range(40)),
            "container id " + "".join(RNG.choice("0123456789abcdef") for _ in range(12)),
            "the secret to good bread is patience",
            "password policy is case-sensitive",
            "see https://example.com/docs/page?id=" + _rand(8),
        ]
        for c in cands:
            if scan_text(c).sensitive:
                flagged.append(c[:50])
    assert not flagged, flagged[:10]


def test_scan_never_raises_on_garbage():
    for _ in range(300):
        n = RNG.randint(0, 3000)
        text = "".join(chr(RNG.randint(0, 0x2FFF)) for _ in range(n))
        scan_text(text)
    scan_text("a" * 200000)
    scan_text("password " + "word " * 20000 + "= x")  # regex backtracking probe
    scan_messages([{"role": "user", "content": None}, {}, {"content": 5},
                   {"content": [{"type": "image_url"}, "str", None]}])


def test_scan_text_redos_guard_is_fast():
    import time
    nasty = ["secret " + "a " * 5000, "password" + " b" * 8000 + ":",
             "api key " * 3000, "client secret " * 3000 + "is"]
    t0 = time.time()
    for n in nasty:
        scan_text(n)
    assert time.time() - t0 < 3.0


# ---------------------------------------------------------------- auth
def _store():
    st = ClientStore(admin_key_sha256=sha256_hex("admin-k"))
    st.clients["c"] = ClientPolicy(id="c", key_sha256=sha256_hex("client-k"))
    return st


def test_stored_hash_is_not_a_valid_key():
    st = _store()
    assert st.authenticate(sha256_hex("client-k")) is None
    assert not st.is_admin(sha256_hex("admin-k"))


@pytest.mark.parametrize("hdr", [
    "", "Bearer", "Bearer ", "bearer", "Basic client-k", "Token client-k",
    "Bearer  ", "client-k", "Bearerclient-k",
])
def test_bad_authorization_headers_yield_no_key_or_no_access(hdr):
    st = _store()
    k = extract_bearer_key(hdr, None)
    assert st.authenticate(k) is None


def test_near_miss_keys_rejected():
    st = _store()
    for k in ["client-k ", " client-k", "client-K", "client-k\n", "client-k\x00",
              "client-", "client-kk", "CLIENT-K", "client-k" * 100, "é"]:
        if k.strip() == "client-k" and k != "client-K":
            continue  # whitespace trimming by extract_bearer_key is intended
        assert st.authenticate(k) is None, repr(k)


def test_admin_and_client_keys_are_disjoint_roles():
    st = _store()
    assert st.authenticate("admin-k") is None
    assert not st.is_admin("client-k")
    assert not st.is_admin(None) and not st.is_admin("")
    st2 = ClientStore(admin_key_sha256=None)
    assert not st2.is_admin("anything")


def test_wrong_scheme_lowercase_bearer_ok_but_empty_key_not():
    assert extract_bearer_key("bearer abc", None) == "abc"
    assert extract_bearer_key("Bearer ", None) in ("", None)


def test_api_over_http_header_variants(client):
    # Unauthenticated / malformed credentials never reach a completion.
    body = chat_body("hi")
    for h in [{}, {"Authorization": "Bearer "}, {"Authorization": "Bearer nope"},
              {"x-api-key": "nope"}, {"Authorization": "Basic aGVsbG8="},
              {"Authorization": "Bearer hermes-test-key-extra"}]:
        r = client.post("/v1/chat/completions", json=body, headers=h)
        assert r.status_code in (401, 403), (h, r.status_code)
    for path in ("/admin/reload", "/admin/tool-health"):
        for h in [{}, auth("hermes"), auth("n8n"), {"Authorization": "Bearer "}]:
            m = client.post if path == "/admin/reload" else client.get
            r = m(path, headers=h)
            assert r.status_code in (401, 403, 405), (path, h, r.status_code)


def test_both_headers_do_not_escalate(client):
    # a valid client key in x-api-key must not combine with a junk bearer
    # into admin access
    r = client.get("/admin/tool-health",
                   headers={"Authorization": "Bearer junk",
                            "x-api-key": RAW_KEYS["hermes"]})
    assert r.status_code in (401, 403)


# ----------------------------------------------------- ToolHealth
def test_toolhealth_concurrent_records_exact_counts(tmp_path):
    th = ToolHealth(tmp_path / "th.json")
    N, T = 100, 16

    def work(i):
        for j in range(N):
            th.record("m", had_error=(j % 4 == 0))

    ts = [threading.Thread(target=work, args=(i,)) for i in range(T)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    s = th.stats("m")
    assert s["turns"] == min(WINDOW, N * T)
    th.snapshot()  # must not deadlock


def test_toolhealth_window_bounded_and_rate(tmp_path):
    th = ToolHealth()
    for _ in range(WINDOW):
        th.record("m", True)
    for _ in range(WINDOW):
        th.record("m", False)
    s = th.stats("m")
    assert s["turns"] == WINDOW and s["errors"] == 0


def test_toolhealth_persist_roundtrip_and_flush(tmp_path):
    p = tmp_path / "th.json"
    th = ToolHealth(p)
    for i in range(PERSIST_EVERY + 3):
        th.record("a", i % 2 == 0)
    th.flush()
    th2 = ToolHealth(p)
    assert th2.stats("a") == th.stats("a")


def test_toolhealth_corrupt_and_hostile_files_never_crash(tmp_path):
    p = tmp_path / "th.json"
    for payload in ["", "{", "[]", "null", '{"a": 5}', '{"a": "str"}',
                    '{"a": [1, 0, "x", null]}', "\x00\x01", '{"a": {"b": 1}}']:
        p.write_text(payload)
        th = ToolHealth(p)           # must not raise
        th.record("a", True)
        th.snapshot()


def test_toolhealth_is_observe_only_no_selection_coupling():
    import gateway, inspect
    assert "would_cap" not in inspect.getsource(gateway)  # J-2: never feeds selection
    th = ToolHealth()
    for _ in range(100):
        th.record("bad", True)
    assert th.would_cap("bad") == 2 and th.would_cap("unseen") is None


def test_toolhealth_concurrent_readers_writers_and_autopersist(tmp_path):
    """Writers + snapshot readers + autosave race. Must not raise, the
    file must exist WITHOUT an explicit flush (auto-persist works), and
    must always be valid JSON that reloads to the same stats."""
    p = tmp_path / "th.json"
    th = ToolHealth(p)
    errors = []
    stop = threading.Event()

    def writer(i):
        try:
            for j in range(300):
                th.record(f"m{i % 3}", j % 5 == 0)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    def reader():
        try:
            while not stop.is_set():
                th.snapshot()
                if p.exists():
                    json.loads(p.read_text())  # atomic replace: never torn
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ws = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    rs = [threading.Thread(target=reader) for _ in range(3)]
    [t.start() for t in rs + ws]
    [t.join() for t in ws]
    stop.set()
    [t.join() for t in rs]
    assert not errors, errors
    assert p.exists(), "auto-persist never wrote the file"
    th.flush()
    assert ToolHealth(p).snapshot() == th.snapshot()


def test_toolhealth_first_record_visible_and_shutdown_flushes(tmp_path):
    p = tmp_path / "th.json"
    th = ToolHealth(p)
    th.record("m", True)
    assert p.exists(), "first record must be persisted immediately"
    for _ in range(PERSIST_EVERY - 2):   # below the autosave threshold
        th.record("m", False)
    th.flush()
    assert ToolHealth(p).stats("m")["turns"] == PERSIST_EVERY - 1


def test_app_shutdown_flushes_tool_health(app):
    from fastapi.testclient import TestClient
    app.state.tool_health.record("zz", True)
    app.state.tool_health.record("zz", False)
    path = app.state.tool_health.path
    with TestClient(app):
        pass  # exit triggers shutdown handlers
    assert json.loads(path.read_text())["zz"] == [1, 0]
