"""Sovereign Router E2E harness for fix/v0.10.4-session-id-detector.
Real uvicorn gateway (branch src) + real classifier sidecar (tfidf, as live)
+ mock upstreams. Live registry shape (repo registry + VM103 live overlay),
live clients.json shape with test keys. Never prints secret values."""
from __future__ import annotations
import hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile, time
import urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("SR_REPO", HERE.parents[1]))
SRC, CLS = REPO / "src", REPO / "classifier"
VPY = os.environ.get("SR_PY", "/tmp/ci-venv/bin/python")
sys.path.insert(0, str(HERE))
import corpus
UP_PORT, CLS_PORT, GW_PORT = 28099, 28098, 26075
GW = f"http://127.0.0.1:{GW_PORT}"
KEYS = {c: f"e2e-{c}-key" for c in ("hermes", "openclaw", "n8n", "crm", "admin")}
RESULTS: list[dict] = []
def check(cat, name, ok, detail=""):
    RESULTS.append({"cat": cat, "name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    if not ok: print(f"FAIL [{cat}] {name} :: {str(detail)[:300]}", flush=True)
def http(method, url, key=None, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if key: req.add_header("Authorization", f"Bearer {key}")
    if data: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
def chat(client, messages, _t=90, **kw):
    body = {"model": kw.pop("model", "auto"), "messages": messages}; body.update(kw)
    return http("POST", f"{GW}/v1/chat/completions", KEYS[client], body, timeout=_t)
def shadow(client, messages, **kw):
    body = {"model": kw.pop("model", "auto"), "messages": messages}; body.update(kw)
    st, _, raw = http("POST", f"{GW}/v1/shadow", KEYS[client], body)
    try: return st, json.loads(raw)
    except Exception: return st, {}
def wait(url, t=60):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200: return True
        except Exception: time.sleep(0.4)
    return False
TOOLS = [{"type": "function", "function": {"name": "terminal", "description": "run a shell command",
          "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]
def sse_text_and_args(raw: bytes):
    txt, args, done = [], [], False
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.strip() == "data: [DONE]": done = True
        if not line.startswith("data: {"): continue
        ch = (json.loads(line[6:]).get("choices") or [])
        d = (ch[0].get("delta") or {}) if ch else {}
        if d.get("content"): txt.append(d["content"])
        for tc in d.get("tool_calls") or []: args.append((tc.get("function") or {}).get("arguments") or "")
    return "".join(txt), "".join(args), done

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sr-e2e-")); procs = []; summary = None
    state = tmp / "state"; (state / "keys").mkdir(parents=True)
    uplog = tmp / "upstream.jsonl"; uplog.touch(); canary_file = tmp / "canaries.json"
    secrets = corpus.secret_cases()
    BENIGN_TOK = set(re.findall(r"[A-Za-z0-9_\-+/.:%]{12,}", corpus.runtime_line("agent:main:main")))
    def core(t):
        toks = [x for x in re.findall(r"[A-Za-z0-9_\-+/.:%]{12,}", t) if x not in BENIGN_TOK and not (x.startswith("agent:") and len(x) < 30)]
        toks.sort(key=len, reverse=True)
        return toks[0] if toks else "\x00NO-CANARY\x00"
    canary_file.write_text(json.dumps({k: core(v[0]) for k, v in secrets.items()}))
    reg = json.loads((SRC / "registry.json").read_text())
    overlay = json.loads((HERE / "live_overlay.json").read_text())
    lane_of = {}
    for i, m in enumerate(reg["models"]):
        o = overlay.get(m["id"])
        if o:
            m["lane"], m["enabled"], m["spillway_sensitive_excluded"], m["quality"] = o[1], o[2], o[3], o[4]
        m["base_url"] = f"http://127.0.0.1:{UP_PORT}/e/{m['lane']}/{i}/v1"
        lane_of[i] = (m["id"], m["lane"], bool(m.get("spillway_sensitive_excluded")))
    (state / "registry.json").write_text(json.dumps(reg))
    live_clients = {"hermes": (1.25, ["private-free", "paid", "public-only-free"], False),
                    "n8n": (2.5, ["private-free", "paid", "public-only-free"], False),
                    "openclaw": (5.0, ["private-free", "paid", "premium", "public-only-free"], True),
                    "crm": (1.25, ["private-free", "paid"], False)}
    clients = {"admin_key_sha256": hashlib.sha256(KEYS["admin"].encode()).hexdigest(), "clients": []}
    for cid, (tgt, lanes, prem) in live_clients.items():
        c = {"id": cid, "key_sha256": hashlib.sha256(KEYS[cid].encode()).hexdigest(), "daily_target_usd": tgt,
             "default_data_class": "internal", "lanes": lanes, "premium_allowed": prem, "sensitive_cleared_providers": []}
        if cid == "openclaw": c.update(premium_daily_count_alert=20, premium_monthly_alert_usd=20.0)
        clients["clients"].append(c)
    (state / "clients.json").write_text(json.dumps(clients))
    for prov in {m["provider"] for m in reg["models"]}:
        (state / "keys" / f"{prov}.key").write_text("dummy-not-a-real-key")
    PUB = "public-only-free"
    try:
        procs.append(subprocess.Popen([sys.executable, str(HERE / "mock_upstream.py"), str(UP_PORT), str(uplog), str(canary_file)]))
        procs.append(subprocess.Popen([sys.executable, str(CLS / "sidecar.py")], env=dict(os.environ, PORT=str(CLS_PORT), CLASSIFIER_EMBEDDER="tfidf"),
                                      stdout=open(tmp / "sidecar.log", "w"), stderr=subprocess.STDOUT))
        check("boot", "sidecar /health", wait(f"http://127.0.0.1:{CLS_PORT}/health"))
        env = dict(os.environ, SOVEREIGN_STATE_DIR=str(state), SOVEREIGN_SIDECAR_URL=f"http://127.0.0.1:{CLS_PORT}",
                   SOVEREIGN_STREAM_FIRST_CHUNK_TIMEOUT_S="3", SOVEREIGN_STREAM_STALL_TIMEOUT_S="3")
        procs.append(subprocess.Popen([VPY, "-m", "uvicorn", "gateway:create_app", "--factory", "--host", "127.0.0.1",
                                       "--port", str(GW_PORT)], cwd=SRC, env=env, stdout=open(tmp / "gateway.log", "w"), stderr=subprocess.STDOUT))
        ok = wait(f"{GW}/health"); check("boot", "gateway /health", ok)
        if not ok:
            print((tmp / "gateway.log").read_text()[-3000:]); return 2
        st, _, raw = http("GET", f"{GW}/health"); h = json.loads(raw)
        _want = re.search(r'^VERSION = "([^"]+)"', (Path(__file__).resolve().parents[2] / "src" / "gateway.py").read_text(), re.M).group(1)
        check("boot", f"version is {_want} (this tree)", h.get("version") == _want, h.get("version"))
        def routes():
            p = state / "routes.jsonl"
            return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
        def last_route():
            r = routes(); return r[-1] if r else {}

        # ---------------- A. OpenClaw runtime line ----------------
        for sk in corpus.session_keys():
            user = {"role": "user", "content": "check disk usage on the NAS"}
            base_msgs = [{"role": "system", "content": "You are OpenClaw."}, user]
            rt_msgs = [{"role": "system", "content": "You are OpenClaw.\n" + corpus.runtime_line(sk)}, user]
            s0, b = shadow("openclaw", base_msgs); s1, a = shadow("openclaw", rt_msgs)
            okA = s1 == 200 and a.get("data_class") != "sensitive" and not a.get("sensitive_detectors")
            sa = {c["model"] for c in a.get("candidates", [])}; sb = {c["model"] for c in b.get("candidates", [])}
            same = sa == sb
            lanes = {c["lane"] for c in a.get("candidates", [])}
            tag = ":".join(sk.split(":")[1:3])
            check("A openclaw-runtime", f"shadow not sensitive [{tag}]", okA, f"dc={a.get('data_class')} det={a.get('sensitive_detectors')}")
            check("A openclaw-runtime", f"full candidate set == baseline [{tag}]", same and PUB in lanes,
                  f"n={len(sa)} base={len(sb)} lvl={a.get('level')}/{b.get('level')} task={a.get('task_class')}/{b.get('task_class')} only_rt={sorted(sa-sb)[:3]} only_base={sorted(sb-sa)[:3]} lanes={sorted(lanes)}")
        for stream in (False, True):
            msgs = corpus.benign_openclaw(1)[0]
            st, hd, raw = chat("openclaw", msgs, stream=stream)
            r = last_route()
            check("A openclaw-runtime", f"live chat stream={stream} 200 + route internal", st == 200 and r.get("data_class") != "sensitive" and not r.get("sensitive_detectors"),
                  f"st={st} dc={r.get('data_class')} det={r.get('sensitive_detectors')} lane={hd.get('x-sovereign-lane')}")
        msgs = [{"role": "system", "content": "You are OpenClaw.\n" + corpus.runtime_line("agent:main:main")}, {"role": "user", "content": "USE_TOOL list files"}]
        st, hd, raw = chat("openclaw", msgs, tools=TOOLS)
        j = json.loads(raw) if st == 200 else {}
        tc = (((j.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or [])
        check("A openclaw-runtime", "tool turn with runtime line -> tool_call, not sensitive", st == 200 and tc and last_route().get("data_class") != "sensitive", f"st={st}")

        # ---------------- B. real secrets ----------------
        excl = {lane_of[i][0] for i in lane_of if lane_of[i][2]}
        for name, (text, kind) in secrets.items():
            msgs = [{"role": "system", "content": "You are Hermes."}, {"role": "user", "content": text + "\nplease help with this"}]
            s, d = shadow("hermes", msgs)
            flagged = d.get("data_class") == "sensitive"
            cand = d.get("candidates", [])
            bad = [c["model"] for c in cand if c["lane"] == PUB or c["model"] in excl]
            if kind == "must":
                check("B secrets", f"{name}: flagged", flagged, f"det={d.get('sensitive_detectors')}")
                check("B secrets", f"{name}: no public/excluded candidates", flagged and not bad, f"bad={bad[:3]}")
            else:
                check("B gaps(info)", f"{name}: flagged", flagged, "pre-existing gap (v0.10.2 also misses)")
        for name, (text, kind) in secrets.items():
            if kind != "must": continue
            for cl in ("hermes", "openclaw"):
                sysm = "You are OpenClaw.\n" + corpus.runtime_line("agent:main:main") if cl == "openclaw" else "You are Hermes."
                st, hd, raw = chat(cl, [{"role": "system", "content": sysm}, {"role": "user", "content": text}])
                r = last_route()
                check("B secrets", f"{name}/{cl}: live 200 non-public lane, route sensitive",
                      st == 200 and hd.get("x-sovereign-lane") != PUB and r.get("data_class") == "sensitive",
                      f"st={st} lane={hd.get('x-sovereign-lane')} dc={r.get('data_class')}")
        text = secrets["password_assign"][0]
        st, hd, raw = chat("hermes", [{"role": "user", "content": text + "\nMOCK_STATUS::429::private-free"}])
        check("B secrets", "spillway: private-free 429 -> served paid/premium spillway, never public", st == 200 and hd.get("x-sovereign-lane") in ("paid", "premium"), f"st={st} lane={hd.get('x-sovereign-lane')}")
        st, _, raw = http("GET", f"{GW}/admin/alerts", KEYS["admin"])
        check("B secrets", "alert path: sensitive_no_cleared_provider on paid serve", st == 200 and b"sensitive_no_cleared_provider" in raw, f"st={st}")
        st, hd, raw = chat("hermes", [{"role": "user", "content": text + "\nMOCK_STATUS::429::private-free MOCK_STATUS::429::paid MOCK_STATUS::429::premium"}])
        check("B secrets", "fail-closed: private+paid down -> no public serve", hd.get("x-sovereign-lane") != PUB, f"st={st} lane={hd.get('x-sovereign-lane')}")

        # ---------------- C. traffic shapes ----------------
        st, hd, raw = chat("hermes", [{"role": "system", "content": "You are Hermes."}, {"role": "user", "content": "USE_TOOL check docker"}], tools=TOOLS)
        j = json.loads(raw) if st == 200 else {}
        tc = ((j.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
        ok_tc = st == 200 and bool(tc) and json.loads(tc[0]["function"]["arguments"]) == {"command": "ls"}
        check("C traffic", "hermes non-stream tool call valid JSON args", ok_tc, f"st={st}")
        follow = [{"role": "system", "content": "You are Hermes."}, {"role": "user", "content": "USE_TOOL check docker"},
                  {"role": "assistant", "content": None, "tool_calls": tc or [{"id": "c1", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]},
                  {"role": "tool", "tool_call_id": (tc[0]["id"] if tc else "c1"), "content": corpus.TOOL_OUTPUTS[0]}]
        st, hd, raw = chat("hermes", follow, tools=TOOLS)
        check("C traffic", "hermes tool-result continuation -> 200 answer", st == 200 and b"answer from" in raw, f"st={st}")
        st, hd, raw = chat("hermes", [{"role": "user", "content": "USE_TOOL check docker"}], tools=TOOLS, stream=True)
        _, args, done = sse_text_and_args(raw)
        try: argsok = json.loads(args or "null") == {"command": "ls"}
        except Exception: argsok = False
        check("C traffic", "hermes streamed tool call reassembles + [DONE]", st == 200 and done and argsok, f"st={st}")
        st, hd, raw = chat("hermes", [{"role": "user", "content": "hello there, give me a status"}], stream=True)
        txt, _, done = sse_text_and_args(raw)
        check("C traffic", "plain streaming text + [DONE] + unicode intact", st == 200 and done and "ünïcødé ✓" in txt, f"st={st}")
        st, hd, raw = chat("hermes", [{"role": "user", "content": "MOCK_429_FIRST summarise this"}])
        check("C traffic", "429 on first attempt -> fallback 200", st == 200, f"st={st} model={hd.get('x-sovereign-model')}")
        for code in (402, 403, 500):
            for lane in ("private-free", PUB):
                st, hd, raw = chat("n8n", [{"role": "user", "content": f"classify this email please MOCK_STATUS::{code}::{lane}"}])
                check("C traffic", f"{code} on whole {lane} lane -> served elsewhere", st == 200 and hd.get("x-sovereign-lane") != lane, f"st={st} lane={hd.get('x-sovereign-lane')}")
        st, hd, raw = chat("hermes", [{"role": "user", "content": "hello MOCK_STALL::private-free MOCK_STALL::public-only-free"}], stream=True, _t=150)
        txt, _, done = sse_text_and_args(raw)
        check("C traffic", "stream first-chunk stall -> failover, completes", st == 200 and done and "Hello" in txt, f"st={st} lane={hd.get('x-sovereign-lane')}")
        bulk = corpus.benign_n8n(160); t_bulk = time.time()
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(lambda m: chat("n8n", m)[0], bulk))
        bulk_wall = round(time.time() - t_bulk, 2)
        check("C traffic", "n8n bulk 160 req / 8 threads all 200", all(s == 200 for s in res), f"non200={[s for s in res if s != 200][:5]}")
        n8n_r = [r for r in routes() if r.get("client") == "n8n"]
        check("C traffic", "n8n bulk: 0 sensitive flags", not any(r.get("data_class") == "sensitive" for r in n8n_r), f"n={len(n8n_r)}")
        st, _, _ = http("POST", f"{GW}/v1/chat/completions", "wrong-key", {"model": "auto", "messages": [{"role": "user", "content": "hi"}]})
        check("C traffic", "bad client key -> 401", st == 401, st)

        # ---------------- E. routing self-correction (v0.10.5, Job B) ----------------
        def _ups(): return len(uplog.read_text().splitlines())
        def _lanes_since(n0): return [json.loads(l)["lane"] for l in uplog.read_text().splitlines()[n0:]]
        CONG_FREE = " MOCK_STATUS::429::private-free MOCK_STATUS::429::public-only-free"
        CONG_ALL_BUT_PREMIUM = CONG_FREE + " MOCK_STATUS::429::paid"
        for stream in (False, True):
            for cl in ("n8n", "hermes", "openclaw"):
                n0 = _ups()
                st, hd, raw = chat(cl, [{"role": "user", "content": "summarise the paragraph about cats" + CONG_FREE}], stream=stream)
                ls = _lanes_since(n0); r = last_route()
                check("E routing", f"{cl} free congested stream={stream}: <=6 upstream calls", len(ls) <= 6, f"calls={len(ls)} lanes={ls}")
                check("E routing", f"{cl} free congested stream={stream}: served paid, premium never called",
                      st == 200 and r.get("lane") == "paid" and "premium" not in ls, f"st={st} lane={r.get('lane')} lanes={ls}")
                n0 = _ups()
                st, hd, raw = chat(cl, [{"role": "user", "content": "summarise the paragraph about cats" + CONG_ALL_BUT_PREMIUM}], stream=stream)
                ls = _lanes_since(n0)
                check("E routing", f"{cl} free+paid congested stream={stream}: premium never called, <=6 calls",
                      "premium" not in ls and len(ls) <= 6, f"st={st} calls={len(ls)} lanes={ls}")
        for content, want in (('{"error": null, "stdout": "nginx restarted"}', False),
                              ('{"ok": true, "error": ""}', False),
                              ('{"error": "container not found"}', True),
                              ("Error: permission denied", True)):
            msgs = [{"role": "system", "content": "You are Hermes."}, {"role": "user", "content": "restart nginx"},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "c9", "content": content}]
            st, hd, raw = chat("hermes", msgs, tools=TOOLS)
            r = last_route()
            check("E routing", f"tool result {content[:28]!r} -> tool_error_next_turn={want}",
                  st == 200 and bool(r.get("tool_error_next_turn")) is want, f"st={st} got={r.get('tool_error_next_turn')}")

        # ---------------- D. edge cases ----------------
        rt = corpus.runtime_line("agent:main:main")
        edge_benign = {
            "list content parts": [{"role": "system", "content": [{"type": "text", "text": rt}]}, {"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "CRLF runtime line": [{"role": "system", "content": rt.replace(" | ", "\r\n")}, {"role": "user", "content": "hi"}],
            "markdown code span": [{"role": "user", "content": "my runtime says `session=agent:main:main` - what does it mean?"}],
            "trailing period": [{"role": "user", "content": "The OpenClaw session=agent:main:main."}],
            "quoted value": [{"role": "user", "content": "set session='agent:main:main' in the config"}],
            "json embedded": [{"role": "user", "content": '{"runtime":"agent=main | session=agent:main:main | host=x"}'}],
            "unicode around": [{"role": "user", "content": "→ session=agent:main:main ← 日本語 🚀"}],
            "status line emoji": [{"role": "assistant", "content": "🧵 Session: agent:devops-agent:main\n📚 Context: 40k/131k"}, {"role": "user", "content": "ok"}],
            "very long benign 300k chars": [{"role": "system", "content": rt + "\n" + ("lorem ipsum dolor sit amet " * 11000)}, {"role": "user", "content": "summarise"}],
            "multiline runtime block": [{"role": "system", "content": "## Runtime\n" + "\n".join(rt.split(" | "))}, {"role": "user", "content": "hi"}],
        }
        for nm, msgs in edge_benign.items():
            s, d = shadow("openclaw", msgs)
            check("D edges", f"benign: {nm} -> not sensitive", s == 200 and d.get("data_class") != "sensitive", f"st={s} det={d.get('sensitive_detectors')}")
        pw = secrets["password_assign"][0]; bearer = secrets["bearer"][0]
        edge_secret = {
            "list parts secret": [{"role": "system", "content": [{"type": "text", "text": rt}]}, {"role": "user", "content": [{"type": "text", "text": pw}]}],
            "secret in tool result": [{"role": "system", "content": rt}, {"role": "user", "content": "cat .env"}, {"role": "tool", "tool_call_id": "c1", "content": "PORT=8099\n" + pw}],
            "secret at end of 300k prompt": [{"role": "system", "content": rt}, {"role": "user", "content": ("lorem ipsum " * 25000) + "\n" + bearer}],
            "secret after runtime on same line": [{"role": "system", "content": rt + " | " + pw}],
            "multiline secret block": [{"role": "user", "content": secrets["password_yaml"][0]}],
            "unicode + secret": [{"role": "user", "content": "日本語 " + secrets["groq_key"][0] + " 🚀"}],
        }
        for nm, msgs in edge_secret.items():
            s, d = shadow("openclaw", msgs)
            check("D edges", f"secret: {nm} -> sensitive", s == 200 and d.get("data_class") == "sensitive", f"st={s}")
        big = [{"role": "system", "content": "lorem ipsum session=agent:main:main | dolor " * 20000}, {"role": "user", "content": "hi"}]
        t0 = time.time(); s, d = shadow("openclaw", big); dt = time.time() - t0
        check("D edges", "perf: 900k-char line with 20k session keys scans fast + not sensitive", s == 200 and dt < 5 and d.get("data_class") != "sensitive", f"st={s} {dt:.2f}s")
        # privacy + upstream exposure
        vals = json.loads(canary_file.read_text()); leaks = []
        for f in [state / "routes.jsonl", tmp / "gateway.log"] + list(state.glob("*.db*")):
            if f.exists():
                blob = f.read_bytes(); leaks += [f"{f.name}:{n}" for n, v in vals.items() if v.encode() in blob]
        check("B secrets", "privacy: no secret canary in routes.jsonl / gateway.log / quota.db", not leaks, f"leaks={sorted(set(leaks))[:6]}")
        must = {k for k, v in secrets.items() if v[1] == "must"}; bad = []
        for l in uplog.read_text().splitlines():
            e = json.loads(l); mid, lane, ex = lane_of[e["idx"]]
            bad += [f"{n}->{mid}" for n in e["canaries"] if n in must and (lane == PUB or ex)]
        check("B secrets", "upstream: no must-secret reached public-only-free or excluded entry", not bad, f"bad={bad[:5]}")
        # ---------------- F. tiered shadow (v0.10.6, Job C) ----------------
        rr_all = routes(); id_lane = {v[0]: (v[1], v[2]) for v in lane_of.values()}
        missing = [i for i, r in enumerate(rr_all) if r.get("status") == 200 and not r.get("tier_shadow")]
        check("F tier-shadow", "every served route row carries tier_shadow", not missing, f"missing={len(missing)}/{len(rr_all)}")
        bad_wall = [r["tier_shadow"]["pick"] for r in rr_all if r.get("data_class") == "sensitive" and r.get("tier_shadow")
                    and r["tier_shadow"].get("pick") and (id_lane.get(r["tier_shadow"]["pick"], ("?", False))[0] == PUB
                                                          or (id_lane.get(r["tier_shadow"]["pick"], ("?", False))[1] and id_lane[r["tier_shadow"]["pick"]][0] not in ("paid", "premium")))]
        check("F tier-shadow", "sensitive rows: shadow pick never public-only-free", not bad_wall, f"bad={bad_wall[:5]}")
        prem = [r for r in rr_all if r.get("tier_shadow") and r["tier_shadow"].get("pick_tier") == "premium"
                and any(t.get("tier") in ("free", "cheap") for t in r["tier_shadow"].get("top", []))]
        check("F tier-shadow", "shadow never picks premium while a free/cheap candidate exists", not prem, f"n={len(prem)}")
        st, sh = shadow("hermes", [{"role": "user", "content": "hello there"}])
        check("F tier-shadow", "/v1/shadow carries tiered_shadow with live_pick == would_route_to",
              st == 200 and (sh.get("tiered_shadow") or {}).get("live_pick") == (sh.get("would_route_to") or {}).get("model"), f"st={st}")
        rr = routes()
        lat = sorted(float(r.get("latency_upstream_ms") or 0) + float(r.get("latency_classify_ms") or 0) for r in rr)
        check("C traffic", "no gateway 5xx in route log", not any(int(r.get("status") or 200) >= 500 for r in rr), f"n={len(rr)}")
        summary = {"routes": len(rr), "p50_latency_ms": lat[len(lat)//2] if lat else None, "p95_latency_ms": lat[int(len(lat)*.95)] if lat else None,
                   "bulk160_wall_s": bulk_wall}
    finally:
        for p in procs:
            try: p.send_signal(signal.SIGTERM); p.wait(5)
            except Exception: p.kill()
    (Path(os.environ.get("SR_OUT", "/tmp")) / "e2e_results.json").write_text(json.dumps({"results": RESULTS, "summary": summary}, indent=1))
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0])
    for r in RESULTS: agg[r["cat"]][0 if r["ok"] else 1] += 1
    for c, (p, f) in sorted(agg.items()): print(f"{c:22s} pass={p} fail={f}")
    print("summary", summary); shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all(r["ok"] for r in RESULTS if not r["cat"].startswith("B gaps")) else 1
if __name__ == "__main__":
    sys.exit(main())
