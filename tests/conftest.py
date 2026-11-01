import hashlib
import json

import httpx
import pytest

from sovereign_router.gateway import create_app

KEYS = {"hermes": "hk", "openclaw": "ok", "n8n": "nk", "admin": "ak"}


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def model(id, host, lane="private-free", q=3, pi=0.0, po=0.0, limits=None, ctx=131072, mdc="internal", **kw):
    return {"id": id, "provider": host, "model": id, "base_url": f"http://{host}.test/v1", "lane": lane,
            "price_in": pi, "price_out": po, "context": ctx, "limits": limits or {}, "quality": {"default": q},
            "max_data_class": mdc, **kw}


REGISTRY = {"models": [
    model("groq20", "groq", q=2, limits={"tpm": 8000}, mdc="sensitive"),
    model("groq120", "groq", q=4, limits={"tpm": 8000}, mdc="sensitive"),
    model("orfree", "or", q=3, limits={"rpm": 20, "rpd": 1000}, ctx=262144),
    model("deepseek", "di", lane="paid", q=5, pi=0.09, po=0.18, cached_in=0.018, ctx=262144, spend_alert_usd=10),
    model("ollama", "ol", lane="paid", q=4, pi=0.15, po=0.6, spend_alert_usd=5),
    model("opus", "pr", lane="premium", q=5, pi=15, po=75),
]}


def clients(**over):
    base = [
        {"name": "hermes", "key_sha256": sha("hk"), "lanes": ["private-free", "paid"], "daily_cost_target_usd": 1.0},
        {"name": "openclaw", "key_sha256": sha("ok"), "lanes": ["private-free", "paid", "premium"],
         "premium": True, "daily_cost_target_usd": 3.0},
        {"name": "n8n", "key_sha256": sha("nk"), "lanes": ["private-free"]},
    ]
    return {"admin_key_sha256": sha("ak"), "clients": base}


class Upstream:
    """Mock providers keyed by host. Each handler: (request, body) -> httpx.Response."""

    def __init__(self):
        self.handlers, self.calls = {}, []

    def __call__(self, request: httpx.Request):
        body = json.loads(request.content)
        host = request.url.host.removesuffix(".test")
        self.calls.append((host, body))
        return self.handlers.get(host, ok_handler)(request, body)


def completion(content="hello", tool_calls=None, usage=(100, 20, 0), finish="stop"):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
        msg["content"] = None
    return {"id": "x", "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                      "prompt_tokens_details": {"cached_tokens": usage[2]}}}


def ok_handler(request, body):
    return httpx.Response(200, json=completion())


def sse(chunks):
    return "".join(f"data: {c if isinstance(c, str) else json.dumps(c)}\n\n" for c in chunks)


def stream_response(chunks):
    return httpx.Response(200, content=sse(chunks).encode(), headers={"content-type": "text/event-stream"})


@pytest.fixture
def upstream():
    return Upstream()


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_app(tmp_path, upstream, clock):
    def _make(registry=None, client_cfg=None, webhook=None, monkeypatch=None, **kw):
        (tmp_path / "registry.json").write_text(json.dumps(registry or REGISTRY))
        (tmp_path / "clients.json").write_text(json.dumps(client_cfg or clients()))
        http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        return create_app(str(tmp_path), http, allow_http=True, clock=clock, **kw)
    return _make


@pytest.fixture
async def client(make_app):
    app = make_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://router") as c:
        c.app = app
        yield c


def auth(name="hermes"):
    return {"Authorization": f"Bearer {KEYS[name]}"}


def chat(text, **extra):
    return {"model": "auto", "messages": [{"role": "user", "content": text}], **extra}
