"""Upstream calls with failover signalling and streamed-answer validation."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx

from .validate import validate_message, validate_response

FAILOVER_STATUS = {401, 403, 408, 413, 429, 500, 502, 503, 504}


class Failover(Exception):
    def __init__(self, reason: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(reason)
        self.reason, self.status, self.retry_after = reason, status, retry_after


class Invalid(Exception):
    def __init__(self, reason: str, usage: dict | None = None):
        super().__init__(reason)
        self.reason, self.usage = reason, usage


def deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def build_body(model, body: dict, stream: bool) -> dict:
    out = dict(body)
    out["model"] = model.model
    if stream:
        out["stream_options"] = {"include_usage": True}
    return deep_merge(out, model.extra_body) if model.extra_body else out


def headers_for(model) -> dict:
    h = {"Content-Type": "application/json"}
    if model.api_key_file:
        key = Path(model.api_key_file).read_text().strip()  # read per call: rotation = overwrite file
        h["Authorization"] = f"Bearer {key}"
    return h


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def _check_status(resp: httpx.Response):
    if resp.status_code in FAILOVER_STATUS:
        raise Failover(f"upstream {resp.status_code}", resp.status_code, _retry_after(resp))
    if resp.status_code >= 400:
        raise Failover(f"upstream {resp.status_code}", resp.status_code)


def usage_of(usage: dict | None) -> dict:
    u = usage or {}
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    return {"in": u.get("prompt_tokens", 0) or 0, "out": u.get("completion_tokens", 0) or 0, "cached": cached}


async def call_json(http: httpx.AsyncClient, model, body: dict, *, tool_names: set, json_mode: bool):
    url = model.base_url.rstrip("/") + "/chat/completions"
    try:
        resp = await http.post(url, json=build_body(model, body, False), headers=headers_for(model), timeout=120)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise Failover(f"{type(e).__name__}") from e
    _check_status(resp)
    try:
        data = resp.json()
    except ValueError:
        raise Failover("non-JSON upstream body", resp.status_code)
    bad = validate_response(data, tool_names=tool_names, json_mode=json_mode)
    if bad:
        raise Invalid(bad, usage_of(data.get("usage")))
    return data, usage_of(data.get("usage"))


class StreamResult:
    def __init__(self, buffered: list, rest, usage_box: dict):
        self.buffered, self.rest, self.usage_box = buffered, rest, usage_box


async def _sse(resp: httpx.Response):
    async for line in resp.aiter_lines():
        if line.startswith("data:"):
            yield line[5:].strip()


async def open_stream(http: httpx.AsyncClient, model, body: dict, *, tool_names: set, json_mode: bool):
    """Open a stream and buffer until the answer is known to be usable.

    Content streams commit at the first content delta. Tool-call (and JSON
    mode) streams are buffered to the end, because argument validity is only
    known once the arguments finish streaming; escalation stays possible
    until then."""
    url = model.base_url.rstrip("/") + "/chat/completions"
    try:
        req = http.build_request("POST", url, json=build_body(model, body, True), headers=headers_for(model),
                                 timeout=120)
        resp = await http.send(req, stream=True)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise Failover(f"{type(e).__name__}") from e
    try:
        _check_status(resp)
    except Failover:
        await resp.aclose()
        raise
    agen = _sse(resp)
    buf: list[str] = []
    usage_box: dict = {}
    content, calls, finish, done = "", {}, None, False
    try:
        async for payload in agen:
            buf.append(payload)
            if payload == "[DONE]":
                done = True
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if chunk.get("usage"):
                usage_box.update(usage_of(chunk["usage"]))
            for ch in chunk.get("choices") or []:
                d = ch.get("delta") or {}
                content += d.get("content") or ""
                for tc in d.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"function": {"name": "", "arguments": ""}})
                    fn = tc.get("function") or {}
                    slot["function"]["name"] += fn.get("name") or ""
                    slot["function"]["arguments"] += fn.get("arguments") or ""
                finish = ch.get("finish_reason") or finish
            if content.strip() and not calls and not json_mode:
                break  # committed: plain content has started
    except (httpx.TimeoutException, httpx.TransportError) as e:
        await resp.aclose()
        raise Failover(f"{type(e).__name__} mid-stream") from e
    committed_early = bool(content.strip()) and not calls and not json_mode
    if not committed_early:
        bad = validate_message({"content": content, "tool_calls": [calls[i] for i in sorted(calls)]}, finish,
                               tool_names=tool_names, json_mode=json_mode)
        if bad:
            await resp.aclose()
            raise Invalid(bad, usage_box or None)

    async def rest():
        try:
            if not done and committed_early:
                async for payload in agen:
                    if payload != "[DONE]":
                        try:
                            chunk = json.loads(payload)
                            if chunk.get("usage"):
                                usage_box.update(usage_of(chunk["usage"]))
                        except ValueError:
                            pass
                    yield payload
        except (httpx.TimeoutException, httpx.TransportError):
            pass  # client already has the start of the answer; end the stream
        finally:
            await resp.aclose()

    return StreamResult(buf, rest(), usage_box)
