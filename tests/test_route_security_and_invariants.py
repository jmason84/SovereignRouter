"""Safety-net tests that must keep passing as the admin console is built.

1. Route-security enumeration: EVERY route on the app is either on the
   explicit public allowlist or rejects unauthenticated callers; every
   /admin route additionally rejects ordinary client keys. A new route
   added without an auth check fails here. (Lesson from the 2026 LiteLLM
   advisories: a control-plane endpoint reachable by a low-privilege
   caller is the failure mode.)
2. Sensitive-wall invariants: a request carrying a secret is served only
   from the private-free lane, for every client, with and without tools,
   streaming or not, and even when the caller pins a paid model.
"""

from __future__ import annotations

import pytest
from conftest import auth, chat_body  # noqa: F401

# Explicit allowlist; keep it tiny. /v1/models is public BY DESIGN
# (public model names only; OpenAI clients probe it pre-auth) — see
# docs/VERIFICATION.md. Anything added here needs a written reason.
PUBLIC_ROUTES = {("GET", "/health"), ("GET", "/v1/models")}

TOOLS = [{"type": "function",
          "function": {"name": "run_command",
                       "parameters": {"type": "object", "properties": {}}}}]

SECRETS = [
    "my key is sk-abc123def456ghi789jkl012mno",
    "aws AKIAIOSFODNN7EXAMPLE is the access key",
    "password = hunter2hunter2 for the db",
    "postgres://admin:S3cretPass@db.internal:5432/app",
]


def _routes(app):
    out = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None) or set()
        if not path or path in ("/openapi.json", "/docs", "/redoc",
                                "/docs/oauth2-redirect"):
            continue
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            out.append((m, path))
    return sorted(set(out))


def _call(client, method, path, headers=None):
    # concrete path for any parameterised route
    path = path.replace("{", "x").replace("}", "")
    return client.request(method, path, headers=headers or {}, json={})


def test_enumeration_finds_routes(app):
    routes = _routes(app)
    assert ("POST", "/v1/chat/completions") in routes
    assert any(p.startswith("/admin/") for _, p in routes)


def test_every_non_public_route_rejects_anonymous(client, app):
    leaks = []
    for method, path in _routes(app):
        if (method, path) in PUBLIC_ROUTES:
            continue
        r = _call(client, method, path)
        if r.status_code != 401:
            leaks.append((method, path, r.status_code))
    assert not leaks, f"routes reachable without credentials: {leaks}"


def test_every_admin_route_rejects_client_keys(client, app):
    leaks = []
    for method, path in _routes(app):
        if not path.startswith("/admin"):
            continue
        for who in ("hermes", "openclaw", "n8n"):
            r = _call(client, method, path, auth(who))
            if r.status_code != 401:
                leaks.append((who, method, path, r.status_code))
    assert not leaks, f"admin routes reachable by client keys: {leaks}"


def test_public_allowlist_is_not_growing_silently(app):
    assert PUBLIC_ROUTES <= set(_routes(app))
    assert len(PUBLIC_ROUTES) == 2


def test_public_models_list_exposes_only_names(client):
    j = client.get("/v1/models").json()
    for m in j["data"]:
        assert set(m) == {"id", "object", "owned_by"}


# ---------------- sensitive-wall invariants ----------------

@pytest.mark.parametrize("who", ["hermes", "openclaw", "n8n"])
@pytest.mark.parametrize("secret", SECRETS)
@pytest.mark.parametrize("with_tools", [False, True])
def test_secret_requests_only_use_private_free(client, app, who, secret,
                                               with_tools):
    body = chat_body(secret + " please remember it")
    if with_tools:
        body["tools"] = TOOLS
    r = client.post("/v1/chat/completions", json=body, headers=auth(who))
    if r.status_code != 200:
        # failing closed is acceptable; leaking to another lane is not
        assert r.status_code in (403, 429, 502, 503), r.text
        return
    assert r.headers["X-Sovereign-Lane"] == "private-free"
    rec = app.state.telemetry.recent(0)[-1]
    assert rec.data_class == "sensitive"


@pytest.mark.parametrize("who", ["hermes", "openclaw", "n8n"])
def test_pin_cannot_pull_a_secret_onto_a_paid_model(client, app, who):
    body = chat_body(SECRETS[0], model="deepinfra/deepseek-v4-flash-0731")
    r = client.post("/v1/chat/completions", json=body, headers=auth(who))
    if r.status_code == 200:
        assert r.headers["X-Sovereign-Lane"] == "private-free"


def test_shadow_never_offers_a_non_private_first_candidate_for_secrets(client):
    for secret in SECRETS:
        r = client.post("/v1/shadow", json=chat_body(secret),
                        headers=auth("hermes"))
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["data_class"] == "sensitive"
        assert all(c["lane"] == "private-free" for c in j["candidates"]), \
            j["candidates"]


def test_secret_text_never_lands_in_telemetry(client, app):
    client.post("/v1/chat/completions",
                json=chat_body(SECRETS[0]), headers=auth("hermes"))
    dump = "".join(r.to_json() for r in app.state.telemetry.recent(0))
    assert "abc123def456" not in dump
