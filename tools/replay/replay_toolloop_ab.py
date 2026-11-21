#!/usr/bin/env python3
"""Offline A/B shape replay of tool-loop sessions through a gateway source tree.

No network, no live access: fake upstream, isolated tmp state. Run once per
source tree (module names clash, so one process per version):

    python replay_toolloop_ab.py --src <dir-with-gateway.py> --registry <registry.json> \
        --sessions 400 --seed 7 > out.json

Sessions follow the real sticky-run-length histogram. A turn's tool result is
an ERROR with probability = the real tool-error rate of the model that served
the PREVIOUS turn (profile-20261120.json). Draws are paired across versions
(same seed) so the comparison isolates routing behaviour. Message CONTENT is
synthetic: this reproduces shape and feedback dynamics, not classifier input.
"""
import argparse, hashlib, json, random, sys, tempfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True)
ap.add_argument("--registry", required=True)
ap.add_argument("--profile", default=str(Path(__file__).with_name("profile-20261120.json")))
ap.add_argument("--sessions", type=int, default=400)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--client", default="hermes")
a = ap.parse_args()
sys.path.insert(0, a.src)

import httpx
from fastapi.testclient import TestClient
from gateway import GatewayConfig, create_app

prof = json.load(open(a.profile))
RATE = prof["model_tool_error_rate"]
DEFAULT = 0.2
hist = prof["hermes_sticky_run_length_hist"]
lens, wts = [h[0] for h in hist], [h[1] for h in hist]

def sha(v): return hashlib.sha256(v.encode()).hexdigest()

def upstream(request):
    body = json.loads(request.content)
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "model": body["model"],
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 400, "completion_tokens": 10, "total_tokens": 410}})

tmp = Path(tempfile.mkdtemp(prefix="replay-"))
(tmp / "clients.json").write_text(json.dumps({"admin_key_sha256": sha("a"), "clients": [
    {"id": a.client, "key_sha256": sha("k"), "lanes": ["private-free", "paid"],
     "premium_allowed": False, "daily_target_usd": 1000.0}]}))
cfg = GatewayConfig(state_dir=tmp / "state", clients_path=tmp / "clients.json",
                    registry_path=Path(a.registry),
                    provider_keys={p: "fake" for p in
                                   ("groq", "openrouter", "deepinfra", "ollama", "openai",
                                    "deepseek", "zai")})
app = create_app(cfg, upstream_transport=httpx.MockTransport(upstream))
TOOLS = [{"type": "function", "function": {"name": "run_command", "parameters": {
    "type": "object", "properties": {"command": {"type": "string"}}}}}]
rng = random.Random(a.seed)
TASKS = ["restart the nginx container and verify it", "check disk usage on the proxmox host",
         "tail the n8n logs and summarise errors", "list docker volumes and prune unused ones",
         "write a python script to parse routes.jsonl", "why is the cron job failing"]

turns = errs = breaks = non200 = flaky = 0
exp_err = 0.0
per_model = {}
sessions_with_run2 = 0
with TestClient(app) as c:
    for s in range(a.sessions):
        n = rng.choices(lens, wts)[0]
        us = [rng.random() for _ in range(n)]          # paired draws
        msgs = [{"role": "system", "content": f"You are an infra agent. session {s}"},
                {"role": "user", "content": f"[{s}] " + rng.choice(TASKS)}]
        prev = None; run = 0; maxrun = 0
        for t in range(n):
            if t > 0:
                err = us[t] < RATE.get(prev, DEFAULT)
                msgs.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": f"c{t}", "type": "function",
                     "function": {"name": "run_command", "arguments": "{}"}}]})
                msgs.append({"role": "tool", "tool_call_id": f"c{t}",
                             "content": '{"error": "command not found"}' if err else "step ok"})
                errs += err; run = run + 1 if err else 0; maxrun = max(maxrun, run)
                exp_err += RATE.get(prev, DEFAULT)
            r = c.post("/v1/chat/completions",
                       json={"model": "auto", "messages": msgs, "tools": TOOLS},
                       headers={"Authorization": "Bearer k"})
            turns += 1
            if r.status_code != 200:
                non200 += 1; break
            m = r.headers.get("X-Sovereign-Model")
            per_model[m] = per_model.get(m, 0) + 1
            if prev and m != prev: breaks += 1
            if RATE.get(m, DEFAULT) >= 0.30: flaky += 1
            prev = m
        sessions_with_run2 += maxrun >= 2

tool_turns = max(1, turns - a.sessions)
print(json.dumps({
    "src": a.src, "sessions": a.sessions, "turns": turns, "tool_result_turns": tool_turns,
    "tool_error_turns": int(errs), "tool_error_rate": round(errs / tool_turns, 4),
    "sessions_with_2plus_consecutive_errors": sessions_with_run2,
    "model_switches": breaks, "turns_on_flaky_models_ge30pct": flaky,
    "non200": non200, "top_models": sorted(per_model.items(), key=lambda x: -x[1])[:6]}, indent=1))
