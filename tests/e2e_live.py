"""End-to-end LIVE verification for Sovereign Router v0.5.

Boots three real HTTP servers and exercises the full pipeline over the wire:
  1. a fake OpenAI-compatible upstream (marker-driven, like conftest),
  2. the classifier sidecar (real process, TF-IDF embedder),
  3. the v0.5 gateway (real uvicorn process) pointed at both.

State is built in a temp dir: registry with every model's base_url
overridden to the fake upstream, clients.json with real sha256 hashes of
test keys, provider keys as files. No real providers, no real keys.

Run:  ../src/.venv/bin/python e2e_live.py
Exit code 0 only if every check passes.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
CLS = ROOT / "classifier"
PY = SRC / ".venv" / "bin" / "python"

UP_PORT, CLS_PORT, GW_PORT = 18099, 18098, 16075
UP = f"http://127.0.0.1:{UP_PORT}/v1"
GW = f"http://127.0.0.1:{GW_PORT}"

KEYS = {"hermes": "e2e-hermes-key", "openclaw": "e2e-openclaw-key",
        "n8n": "e2e-n8n-key", "admin": "e2e-admin-key"}

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def http(method: str, url: str, key: str | None = None, body: dict | None = None,
         headers: dict | None = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def chat(client: str, messages: list[dict], **kw):
    body: dict = {"model": kw.pop("model", "auto"), "messages": messages}
    body.update(kw)
    return http("POST", f"{GW}/v1/chat/completions", KEYS[client], body)


# ---------------------------------------------------------------- fake upstream
FAKE = r'''
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

FREE = {"openai/gpt-oss-20b","openai/gpt-oss-120b","qwen/qwen3.8-27b",
        "cohere/north-mini-code:free","dots-studio/dots-3-note-preview:free"}

def text_of(body):
    out=[]
    for m in body.get("messages") or []:
        c=m.get("content")
        if isinstance(c,str): out.append(c)
    return "\n".join(out)

def completion(model, content, tool_calls=None, finish="stop"):
    msg={"role":"assistant","content":content}
    if tool_calls: msg["tool_calls"]=tool_calls
    return {"id":"x","object":"chat.completion","model":model,
            "choices":[{"index":0,"message":msg,"finish_reason":finish}],
            "usage":{"prompt_tokens":100,"completion_tokens":10,"total_tokens":110,
                     "prompt_tokens_details":{"cached_tokens":40}}}

def chunk(model, delta, finish=None):
    return {"id":"x","object":"chat.completion.chunk","model":model,
            "choices":[{"index":0,"delta":delta,"finish_reason":finish}]}

def sse(chunks):
    lines=[f"data: {json.dumps(c)}\n\n" for c in chunks]
    lines.append(f"data: {json.dumps({'id':'x','choices':[],'usage':{'prompt_tokens':100,'completion_tokens':6,'total_tokens':106}})}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model=body["model"]; text=text_of(body)
        if "FAIL_ALL" in text:
            return self._send(500, {"error":{"message":"boom"}})
        if f"FAIL::{model}" in text:
            return self._send(429, {"error":{"message":"slow down"}})
        if body.get("stream"):
            tools=body.get("tools") or []
            if tools and "USE_TOOL" in text:
                name=tools[0]["function"]["name"]
                bad="BADTOOL" in text and model in FREE
                parts=["{not-","json"] if bad else ['{"command": ','"ls"}']
                chunks=[chunk(model,{"role":"assistant","tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":name,"arguments":""}}]}),
                        chunk(model,{"tool_calls":[{"index":0,"function":{"arguments":parts[0]}}]}),
                        chunk(model,{"tool_calls":[{"index":0,"function":{"arguments":parts[1]}}]}),
                        chunk(model,{},finish="tool_calls")]
                return self._raw(200, sse(chunks), "text/event-stream")
            chunks=[chunk(model,{"role":"assistant","content":"Hello "}),
                    chunk(model,{"content":"world"}), chunk(model,{},finish="stop")]
            return self._raw(200, sse(chunks), "text/event-stream")
        if "PLEASE_EMPTY" in text and model in FREE:
            return self._send(200, completion(model, ""))
        if "BADJSONMODE" in text and model in FREE:
            return self._send(200, completion(model, "not json at all"))
        tools=body.get("tools") or []
        if tools and "USE_TOOL" in text:
            name=tools[0]["function"]["name"]
            return self._send(200, completion(model, None, tool_calls=[
                {"id":"c1","type":"function","function":{"name":name,"arguments":'{"command":"ls"}'}}], finish="tool_calls"))
        return self._send(200, completion(model, f"answer from {model}"))
    def _send(self, code, obj):
        self._raw(code, json.dumps(obj).encode(), "application/json")
    def _raw(self, code, data, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def wait_health(url: str, timeout: float = 60) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sr-e2e-"))
    procs: list[subprocess.Popen] = []
    try:
        # ---- fake upstream -------------------------------------------------
        fake_py = tmp / "fake_upstream.py"
        fake_py.write_text(FAKE)
        procs.append(subprocess.Popen([sys.executable, str(fake_py), str(UP_PORT)]))

        # ---- classifier sidecar -------------------------------------------
        env_cls = dict(os.environ, PORT=str(CLS_PORT), CLASSIFIER_EMBEDDER="tfidf")
        procs.append(subprocess.Popen(
            [sys.executable, str(CLS / "sidecar.py")], env=env_cls,
            stdout=open(tmp / "sidecar.log", "w"), stderr=subprocess.STDOUT))
        check("sidecar boots + /health", wait_health(f"http://127.0.0.1:{CLS_PORT}/health"))
        try:
            st, _, raw = http("POST", f"http://127.0.0.1:{CLS_PORT}/classify",
                              body={"text": "delete the docker stack and migrate it to the new host", "client": "hermes"})
            cls_out = json.loads(raw)
            check("sidecar classifies hard infra task at L4/L5",
                  st == 200 and cls_out.get("level", 0) >= 4, str(cls_out))
        except Exception as exc:  # record, don't crash the whole run
            check("sidecar classifies hard infra task at L4/L5", False, repr(exc))
            print((tmp / "sidecar.log").read_text()[-2000:])

        # ---- gateway state --------------------------------------------------
        state = tmp / "state"
        (state / "keys").mkdir(parents=True)
        reg = json.loads((SRC / "registry.json").read_text())
        for m in reg["models"]:
            m["base_url"] = UP
        (state / "registry.json").write_text(json.dumps(reg))
        clients = {"admin_key_sha256": hashlib.sha256(KEYS["admin"].encode()).hexdigest(),
                   "clients": [
                       {"id": cid, "key_sha256": hashlib.sha256(KEYS[cid].encode()).hexdigest(),
                        "default_data_class": "internal",
                        "lanes": ["private-free", "paid"] + (["premium"] if cid == "openclaw" else []),
                        "premium_allowed": cid == "openclaw",
                        "daily_target_usd": {"hermes": 1.0, "openclaw": 3.0, "n8n": 0.5}[cid],
                        "sensitive_cleared_providers": [],
                        **({"premium_monthly_alert_usd": 20.0, "premium_daily_count_alert": 20} if cid == "openclaw" else {})}
                       for cid in ("hermes", "openclaw", "n8n")]}
        (state / "clients.json").write_text(json.dumps(clients))
        for prov in ("groq", "openrouter", "deepinfra", "ollama", "openai", "anthropic"):
            (state / "keys" / f"{prov}.key").write_text("fake")

        env_gw = dict(os.environ,
                      SOVEREIGN_STATE_DIR=str(state),
                      SOVEREIGN_SIDECAR_URL=f"http://127.0.0.1:{CLS_PORT}",
                      SOVEREIGN_EMERGENCY_SWITCH="1",
                      SOVEREIGN_EMERGENCY_DAILY_USD="25")
        procs.append(subprocess.Popen(
            [str(PY), "-m", "uvicorn", "gateway:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(GW_PORT)],
            cwd=SRC, env=env_gw,
            stdout=open(tmp / "gateway.log", "w"), stderr=subprocess.STDOUT))
        ok_gw = wait_health(f"{GW}/health")
        if not ok_gw:
            print((tmp / "gateway.log").read_text()[-3000:])
        check("gateway boots + /health", ok_gw)

        st, hd, raw = http("GET", f"{GW}/health")
        health = json.loads(raw)
        check("health: emergency switch ON at $25 (decision #1)",
              health["emergency_paid_lane_switch"]["enabled"] is True
              and health["emergency_paid_lane_switch"]["daily_limit_usd"] == 25.0,
              str(health["emergency_paid_lane_switch"]))
        check("health: alert chain degraded before any poll (by design)",
              health["alert_chain"]["status"] == "degraded", str(health["alert_chain"]))
        check("health: sidecar visible", "classifier_sidecar" in health, str(health.get("classifier_sidecar"))[:120])

        # ---- auth ------------------------------------------------------------
        # NOTE (verified 2026-11-02): /v1/models is intentionally public —
        # it lists only public model names/aliases, matches the unit-tested
        # contract, and OpenAI clients probe it pre-auth. All data/admin
        # endpoints enforce keys (checked below).
        st, _, _ = http("GET", f"{GW}/v1/models")
        check("models list is public by design (no secrets in it)", st == 200, str(st))
        st, _, _ = http("GET", f"{GW}/v1/usage")
        check("usage without key -> 401", st == 401, str(st))
        st, _, raw = http("GET", f"{GW}/v1/models", KEYS["hermes"])
        check("models with hermes key -> 200 + aliases",
              st == 200 and any(m["id"] == "auto" for m in json.loads(raw)["data"]), str(st))

        # ---- routing basics ---------------------------------------------------
        st, hd, raw = chat("hermes", [{"role": "user", "content": "hello, say hi back"}])
        check("L1 trivial chat served free", st == 200 and hd.get("x-sovereign-lane") == "private-free",
              f"st={st} lane={hd.get('x-sovereign-lane')} model={hd.get('x-sovereign-model')} level={hd.get('x-sovereign-level')}")
        st, hd, raw = chat("hermes", [{"role": "user", "content": "is docker running on the host?"}])
        check("infra-easy question NOT premium/complex", st == 200 and hd.get("x-sovereign-lane") != "premium"
              and int(hd.get("x-sovereign-level", "9")) <= 3,
              f"level={hd.get('x-sovereign-level')} lane={hd.get('x-sovereign-lane')}")
        st, hd, raw = chat("hermes", [{"role": "user", "content": "delete the old docker stack and migrate it to the new proxmox host, then debug why the restore failed"}])
        check("infra change+diagnosis -> L4/5, paid-or-better lane", st == 200
              and int(hd.get("x-sovereign-level", "0")) >= 4,
              f"level={hd.get('x-sovereign-level')} model={hd.get('x-sovereign-model')}")

        # ---- premium discipline ------------------------------------------------
        st, _, _ = chat("hermes", [{"role": "user", "content": "hi"}], model="premium")
        check("hermes premium alias -> 403", st == 403, str(st))
        st, hd, raw = chat("openclaw", [{"role": "user", "content": "design the cutover plan"}], model="premium")
        check("openclaw premium alias -> 200 premium lane", st == 200 and hd.get("x-sovereign-lane") == "premium",
              f"st={st} lane={hd.get('x-sovereign-lane')}")

        # ---- sensitive ----------------------------------------------------------
        fake_key = "gsk_" + "A" * 48
        st, hd, raw = chat("hermes", [{"role": "user", "content": f"my key is {fake_key} please store it"}])
        check("sensitive request served only on private lane", st == 200 and hd.get("x-sovereign-lane") == "private-free",
              f"st={st} lane={hd.get('x-sovereign-lane')}")

        # ---- streaming ------------------------------------------------------------
        st, hd, raw = chat("hermes", [{"role": "user", "content": "hello there"}], stream=True)
        txt = raw.decode()
        check("streaming text returns SSE + [DONE]", st == 200 and "Hello " in txt and "[DONE]" in txt, txt[:80].replace("\n", " "))

        tools = [{"type": "function", "function": {"name": "run_shell",
                 "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}}]
        def streamed_args(raw_bytes: bytes) -> str:
            """Concatenate tool-call argument fragments across SSE chunks,
            the way a real OpenAI client reassembles them."""
            frags = []
            for line in raw_bytes.decode().splitlines():
                if not line.startswith("data: {"):
                    continue
                choices = json.loads(line[6:]).get("choices") or []
                delta = (choices[0].get("delta") or {}) if choices else {}
                for tc in delta.get("tool_calls") or []:
                    frags.append((tc.get("function") or {}).get("arguments") or "")
            return "".join(frags)

        def args_are_ls(raw_bytes: bytes) -> bool:
            try:
                return json.loads(streamed_args(raw_bytes)) == {"command": "ls"}
            except Exception:
                return False

        st, hd, raw = chat("hermes", [{"role": "user", "content": "USE_TOOL please list files"}],
                           tools=tools, stream=True)
        check("streaming tool call reassembled to valid JSON args",
              st == 200 and args_are_ls(raw), f"args={streamed_args(raw)!r}")
        st, hd, raw = chat("hermes", [{"role": "user", "content": "USE_TOOL BADTOOL please list files"}],
                           tools=tools, stream=True)
        check("streaming BAD tool call escalated, bad args never reach client",
              st == 200 and "{not-" not in streamed_args(raw) and args_are_ls(raw),
              f"escalated={hd.get('x-sovereign-escalated')} model={hd.get('x-sovereign-model')} args={streamed_args(raw)!r}")

        # ---- validation escalation (non-stream) -----------------------------------
        st, hd, raw = chat("hermes", [{"role": "user", "content": "PLEASE_EMPTY give me the status"}])
        check("empty answer escalates to a stronger model", st == 200 and hd.get("x-sovereign-escalated") == "true",
              f"escalated={hd.get('x-sovereign-escalated')} model={hd.get('x-sovereign-model')}")

        # ---- sticky tool loop --------------------------------------------------------
        convo = [{"role": "system", "content": "You are a devops agent."},
                 {"role": "user", "content": "check the server and fix the config"}]
        models_seen = []
        msgs = list(convo)
        for i in range(3):
            st, hd, raw = chat("hermes", msgs, tools=tools)
            models_seen.append(hd.get("x-sovereign-model"))
            msgs = msgs + [{"role": "assistant", "content": None,
                            "tool_calls": [{"id": "c1", "type": "function",
                                            "function": {"name": "run_shell", "arguments": "{}"}}]},
                           {"role": "tool", "tool_call_id": "c1", "content": "ok"}]
        check("sticky: same model across 3-step tool loop", len(set(models_seen)) == 1 and models_seen[0],
              str(models_seen))

        # ---- admin + alert chain ------------------------------------------------------
        st, _, _ = http("GET", f"{GW}/admin/costs", KEYS["hermes"])
        check("admin/costs with client key -> 401", st == 401, str(st))
        st, _, raw = http("GET", f"{GW}/admin/costs", KEYS["admin"])
        costs = json.loads(raw) if st == 200 else {}
        check("admin/costs with admin key -> 200, spend recorded", st == 200 and bool(costs), str(costs)[:160])
        st, _, raw = http("GET", f"{GW}/health")
        check("health: alert chain ok after admin poll",
              json.loads(raw)["alert_chain"]["status"] == "ok")

        # ---- usage scoping ---------------------------------------------------------------
        st, _, raw = http("GET", f"{GW}/v1/usage", KEYS["hermes"])
        usage = json.loads(raw) if st == 200 else {}
        blob = json.dumps(usage)
        check("usage scoped to caller (hermes sees no openclaw)", st == 200 and "openclaw" not in blob, blob[:120])

        # ---- killswitch ---------------------------------------------------------------------
        st, _, _ = http("POST", f"{GW}/admin/killswitch", KEYS["admin"],
                        {"client_id": "n8n", "active": True})
        st2, _, _ = chat("n8n", [{"role": "user", "content": "hello"}])
        st3, _, _ = http("POST", f"{GW}/admin/killswitch", KEYS["admin"],
                         {"client_id": "n8n", "active": False})
        st4, _, _ = chat("n8n", [{"role": "user", "content": "hello"}])
        check("killswitch stops then restores a client", st == 200 and st2 == 403 and st4 == 200,
              f"set={st} blocked={st2} restored={st4}")

        # ---- shadow mode + alerts outbox (ported in convergence) ------------------------------
        st, _, raw = http("POST", f"{GW}/v1/shadow", KEYS["hermes"],
                          {"model": "auto", "messages": [{"role": "user", "content": "restart the nginx container"}]})
        shadow = json.loads(raw) if st == 200 else {}
        check("shadow returns a decision, no upstream call",
              st == 200 and shadow.get("shadow") is True and shadow.get("would_route_to", {}).get("model"),
              str(shadow.get("would_route_to")))
        st, _, _ = http("POST", f"{GW}/v1/shadow", KEYS["hermes"],
                        {"model": "premium", "messages": [{"role": "user", "content": "hi"}]})
        check("shadow enforces premium permission", st == 403, str(st))
        st, _, raw = http("GET", f"{GW}/admin/alerts", KEYS["admin"])
        alerts_body = json.loads(raw) if st == 200 else {}
        check("alerts outbox polls with admin key",
              st == 200 and "alerts" in alerts_body and "last_id" in alerts_body,
              f"n_alerts={len(alerts_body.get('alerts', []))} last_id={alerts_body.get('last_id')}")
        st, _, _ = http("GET", f"{GW}/admin/alerts", KEYS["hermes"])
        check("alerts outbox rejects client key", st == 401, str(st))

        # ---- telemetry hygiene -----------------------------------------------------------------
        routes = (state / "routes.jsonl").read_text() if (state / "routes.jsonl").exists() else ""
        check("route log written, one JSON line per request", routes.count("\n") >= 10, f"lines={routes.count(chr(10))}")
        check("route log contains NO prompt text and NO secrets",
              "is docker running" not in routes and fake_key not in routes and "PLEASE_EMPTY" not in routes)

        fails = [r for r in RESULTS if not r[1]]
        print(f"\n== E2E: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ==")
        return 1 if fails else 0
    finally:
        for p in procs:
            p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        print(f"artifacts kept in {tmp}")


if __name__ == "__main__":
    sys.exit(main())
