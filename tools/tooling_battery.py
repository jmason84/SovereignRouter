#!/usr/bin/env python3
"""Tooling battery — the pin battery productized for the tooling
quality dimension (v0.9.0; Cycle 1 aspect F, NEXT plan §2d).

For each target registry entry, the battery pins the entry through
the production gateway (unique conversation per pin — the F-03 rule)
and administers a FIXED set of synthetic tool tasks. It measures what
chat/code quality cannot: whether the model can actually drive
tools. All prompts are synthetic and non-sensitive.

Tasks (fixed set, defined in TASKS below):
  T1 exact_args        one tool call, every argument exactly as given
  T2 large_payload     one write_file-style call carrying a 160-line
                       payload; argument integrity scored line-wise
  T3 parallel_calls    two independent calls in a single turn
  T4 follow_through    use a provided tool result correctly
  T5 error_recovery    after a tool error, adapt vs re-issue the
                       identical call (the 2026-11-13 GLM failure mode)
  V  envelope validity scored across every call the entry produces:
                       arguments must parse as strict JSON and name a
                       declared tool

Scoring rubric (mechanical; no hand-entered scores):
  * each task scores 1.0 / 0.5 / 0.0, or is marked no_evidence when
    the pinned entry never served the turn (upstream failure,
    provider key missing, pool exhaustion — recorded, never forced);
  * if the gateway escalated away from the pinned entry on a tool
    task (X-Sovereign-Escalated with a different serving model), the
    pinned response failed the gateway's own validation — the task
    scores 0.0 as `failed_validation` and the envelope component
    counts one invalid call;
  * composite = mean of the available components (T1..T5, V);
    an entry needs >= 3 available components or it stays UNMEASURED
    (no tooling score is emitted for it);
  * tooling score = 1 + round(4 * composite), clamped to 1..5 —
    except that a 0.0 envelope component (every produced call
    malformed) floors the score at 1: such an entry cannot drive
    tools at all, whatever else it managed.

The battery is also the verified_until writer: an entry that served
at least one task itself (served model == pinned id on a non-error
response) is probe-verified; --emit-registry-patch writes
{entry_id: {tooling, tooling_source, verified_until}} for measured
entries and {entry_id: {verified_until}} for served-but-unmeasured
ones. verified_until = run date + 30 days (the investigation
protocol's staleness horizon). Entries never served get nothing.

Usage (live, from inside the gateway container or any host that can
reach the gateway):
  SR_BATTERY_KEY=<client key> python3 tooling_battery.py \
      --base-url http://127.0.0.1:6075/v1 \
      --registry registry.json --out battery-report \
      [--entries a,b,c] [--pace 2] [--provider-pace cerebras=13]

The client key is read from the environment, never from argv, and
never printed. Results are checkpointed after every entry, so an
interrupted run resumes with --resume.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

# ----------------------------------------------------------------------
# the fixed task set
# ----------------------------------------------------------------------

RECORD_OBSERVATION = {
    "type": "function",
    "function": {
        "name": "record_observation",
        "description": "Record a field observation reading.",
        "parameters": {
            "type": "object",
            "properties": {
                "station": {"type": "string"},
                "samples": {"type": "integer"},
                "mode": {"type": "string", "enum": ["auto", "manual"]},
                "calibrated": {"type": "boolean"},
                "pressure_kpa": {"type": "number"},
            },
            "required": ["station", "samples", "mode", "calibrated",
                         "pressure_kpa"],
        },
    },
}

WRITE_FILE = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write text content to a file path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
}

GET_WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}},
                       "required": ["city"]},
    },
}

GET_TIME = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Get the current time in a timezone.",
        "parameters": {"type": "object",
                       "properties": {"timezone": {"type": "string"}},
                       "required": ["timezone"]},
    },
}

LOOKUP_GAUGE = {
    "type": "function",
    "function": {
        "name": "lookup_gauge_reading",
        "description": "Look up the latest reading for a gauge.",
        "parameters": {"type": "object",
                       "properties": {"gauge": {"type": "integer"}},
                       "required": ["gauge"]},
    },
}

CLICK_ELEMENT = {
    "type": "function",
    "function": {
        "name": "click_element",
        "description": "Click a page element identified by a CSS selector.",
        "parameters": {"type": "object",
                       "properties": {"selector": {"type": "string"}},
                       "required": ["selector"]},
    },
}

LIST_ELEMENTS = {
    "type": "function",
    "function": {
        "name": "list_elements",
        "description": "List the clickable elements on the current page.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _payload_lines(n: int = 160) -> list[str]:
    return [f"line {i:03d}: the quick brown fox jumps over the lazy dog "
            f"(checkpoint {i * 7 % 997:03d})" for i in range(1, n + 1)]


# A chat function: (messages, tools, pin_model) -> result dict with keys
#   status (int), served_model (str|None), escalated (bool),
#   message (assistant message dict|None), error (str|None)
ChatFn = Callable[[list[dict], list[dict], str], dict]


def _calls(result: dict) -> list[dict]:
    msg = result.get("message") or {}
    return msg.get("tool_calls") or []


def _parse_args(call: dict) -> tuple[Optional[dict], bool]:
    raw = (call.get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw, True
    try:
        parsed = json.loads(raw)
        return (parsed if isinstance(parsed, dict) else None), True
    except (TypeError, ValueError):
        return None, False


def _call_name(call: dict) -> str:
    return (call.get("function") or {}).get("name") or ""


# ----------------------------------------------------------------------
# task runners — each returns (score|None, detail dict)
# None score = no evidence (the pinned entry never served the turn)
# ----------------------------------------------------------------------

def _served_ok(result: dict, pin: str) -> Optional[str]:
    """None when the turn is usable evidence for the pinned entry,
    else the no-evidence / defect verdict for the whole task."""
    if result.get("error"):
        return "no_evidence"
    if result.get("served_model") != pin:
        if result.get("escalated"):
            return "failed_validation"
        return "no_evidence"
    return None


def task_t1(chat: ChatFn, pin: str, nonce: str) -> tuple[Optional[float], dict]:
    msgs = [{"role": "user", "content":
             f"[{nonce}] Record this observation exactly as given, using "
             "the record_observation tool: station 'north-ridge', "
             "samples 42, mode 'manual', calibrated true, "
             "pressure_kpa 87.5. Do not change any value."}]
    r = chat(msgs, [RECORD_OBSERVATION], pin)
    verdict = _served_ok(r, pin)
    if verdict == "no_evidence":
        return None, {"outcome": "no_evidence", "served": r.get("served_model")}
    if verdict == "failed_validation":
        return 0.0, {"outcome": "failed_validation",
                     "served": r.get("served_model")}
    calls = _calls(r)
    if not calls:
        return 0.0, {"outcome": "no_tool_call"}
    args, valid = _parse_args(calls[0])
    if _call_name(calls[0]) != "record_observation" or not valid or args is None:
        return 0.0, {"outcome": "wrong_tool_or_bad_json",
                     "excerpt": str(calls[0])[:200]}
    expect = {"station": "north-ridge", "samples": 42, "mode": "manual",
              "calibrated": True, "pressure_kpa": 87.5}
    exact = all(args.get(k) == v for k, v in expect.items())
    if exact:
        return 1.0, {"outcome": "exact"}
    return 0.5, {"outcome": "args_deviated", "args": args}


def task_t2(chat: ChatFn, pin: str, nonce: str) -> tuple[Optional[float], dict]:
    lines = _payload_lines()
    content = "\n".join(lines)
    msgs = [{"role": "user", "content":
             f"[{nonce}] Write the following text to /tmp/battery-note.txt "
             "using the write_file tool, in ONE call, reproducing the "
             "content EXACTLY (every line, in order, nothing added):\n\n"
             + content}]
    r = chat(msgs, [WRITE_FILE], pin)
    verdict = _served_ok(r, pin)
    if verdict == "no_evidence":
        return None, {"outcome": "no_evidence", "served": r.get("served_model")}
    if verdict == "failed_validation":
        return 0.0, {"outcome": "failed_validation",
                     "served": r.get("served_model")}
    calls = _calls(r)
    if not calls:
        return 0.0, {"outcome": "no_tool_call"}
    args, valid = _parse_args(calls[0])
    if _call_name(calls[0]) != "write_file" or not valid or args is None:
        return 0.0, {"outcome": "wrong_tool_or_bad_json",
                     "excerpt": str(calls[0])[:200]}
    got_lines = str(args.get("content") or "").split("\n")
    intact = sum(1 for a, b in zip(got_lines, lines) if a == b)
    integrity = intact / len(lines)
    detail = {"outcome": "integrity_scored", "integrity": round(integrity, 3),
              "lines_intact": intact, "lines_total": len(lines)}
    if integrity >= 0.98:
        return 1.0, detail
    if integrity >= 0.90:
        return 0.5, detail
    return 0.0, detail


def task_t3(chat: ChatFn, pin: str, nonce: str) -> tuple[Optional[float], dict]:
    msgs = [{"role": "user", "content":
             f"[{nonce}] I need two things at once: the weather in "
             "Denver and the current time in America/Denver. Call both "
             "tools in a single response."}]
    r = chat(msgs, [GET_WEATHER, GET_TIME], pin)
    verdict = _served_ok(r, pin)
    if verdict == "no_evidence":
        return None, {"outcome": "no_evidence", "served": r.get("served_model")}
    if verdict == "failed_validation":
        return 0.0, {"outcome": "failed_validation",
                     "served": r.get("served_model")}
    # a call counts only if its arguments parse — a named call with
    # garbage arguments is not a parallel call, it is envelope noise
    # (and the V component scores it as such too)
    names = {_call_name(c) for c in _calls(r) if _parse_args(c)[1]}
    if {"get_weather", "get_time"} <= names:
        return 1.0, {"outcome": "both_calls", "calls": sorted(names)}
    if names & {"get_weather", "get_time"}:
        return 0.5, {"outcome": "one_call_only", "calls": sorted(names)}
    return 0.0, {"outcome": "no_calls", "calls": sorted(names)}


def task_t4(chat: ChatFn, pin: str, nonce: str) -> tuple[Optional[float], dict]:
    msgs = [{"role": "user", "content":
             f"[{nonce}] Look up the latest reading for gauge 7."}]
    r1 = chat(msgs, [LOOKUP_GAUGE], pin)
    verdict = _served_ok(r1, pin)
    if verdict == "no_evidence":
        return None, {"outcome": "no_evidence", "served": r1.get("served_model")}
    if verdict == "failed_validation":
        return 0.0, {"outcome": "failed_validation",
                     "served": r1.get("served_model")}
    calls = _calls(r1)
    if not calls:
        return 0.0, {"outcome": "no_tool_call"}
    call_id = calls[0].get("id") or "call_1"
    msgs2 = msgs + [
        {"role": "assistant", "content": None, "tool_calls": calls},
        {"role": "tool", "tool_call_id": call_id,
         "content": json.dumps({"gauge": 7, "reading": 4815,
                                "unit": "kPa", "location": "north vault"})},
        {"role": "user", "content":
         "What was the reading, in what unit, and where is the gauge?"},
    ]
    r2 = chat(msgs2, [LOOKUP_GAUGE], pin)
    verdict2 = _served_ok(r2, pin)
    if verdict2 is not None:
        return (0.0 if verdict2 == "failed_validation" else None), \
            {"outcome": verdict2, "served": r2.get("served_model")}
    text = str((r2.get("message") or {}).get("content") or "")
    if "4815" in text and "kPa" in text:
        return 1.0, {"outcome": "followed_through", "excerpt": text[:160]}
    if "4815" in text:
        return 0.5, {"outcome": "value_only", "excerpt": text[:160]}
    return 0.0, {"outcome": "fact_not_used", "excerpt": text[:160]}


def task_t5(chat: ChatFn, pin: str, nonce: str) -> tuple[Optional[float], dict]:
    tools = [CLICK_ELEMENT, LIST_ELEMENTS]
    msgs = [{"role": "user", "content":
             f"[{nonce}] You are viewing a documentation page in the "
             "browser. Click the in-page link with selector "
             "'a[href=\"#add-bot-to-channel\"]' using click_element."}]
    r1 = chat(msgs, tools, pin)
    verdict = _served_ok(r1, pin)
    if verdict == "no_evidence":
        return None, {"outcome": "no_evidence", "served": r1.get("served_model")}
    if verdict == "failed_validation":
        return 0.0, {"outcome": "failed_validation",
                     "served": r1.get("served_model")}
    calls = _calls(r1)
    if not calls:
        return None, {"outcome": "no_call_to_recover_from"}
    first = calls[0]
    call_id = first.get("id") or "call_1"
    msgs2 = msgs + [
        {"role": "assistant", "content": None, "tool_calls": calls},
        {"role": "tool", "tool_call_id": call_id,
         "content": "Error: evaluate failed — no element matches the "
                    "given selector; this page contains no such anchor "
                    "(element not found)."},
        {"role": "user", "content":
         "The click failed with that error. Continue the task."},
    ]
    r2 = chat(msgs2, tools, pin)
    verdict2 = _served_ok(r2, pin)
    if verdict2 is not None:
        return (0.0 if verdict2 == "failed_validation" else None), \
            {"outcome": verdict2, "served": r2.get("served_model")}
    next_calls = _calls(r2)
    if not next_calls:
        # A text reply that acknowledges the failure and changes
        # approach is adaptation; only an identical re-issue fails.
        return 1.0, {"outcome": "adapted_text_reply",
                     "excerpt": str((r2.get("message") or {})
                                    .get("content") or "")[:160]}
    nxt = next_calls[0]
    same = (_call_name(nxt) == _call_name(first)
            and json.dumps(_parse_args(nxt)[0], sort_keys=True)
            == json.dumps(_parse_args(first)[0], sort_keys=True))
    if same:
        return 0.0, {"outcome": "identical_reissue",
                     "excerpt": str(nxt)[:200]}
    return 1.0, {"outcome": "adapted", "next_call": _call_name(nxt)}


TASKS = [("t1_exact_args", task_t1), ("t2_large_payload", task_t2),
         ("t3_parallel_calls", task_t3), ("t4_follow_through", task_t4),
         ("t5_error_recovery", task_t5)]


# ----------------------------------------------------------------------
# per-entry run
# ----------------------------------------------------------------------

def run_entry(chat: ChatFn, entry_id: str, run_id: str) -> dict:
    components: dict[str, Optional[float]] = {}
    details: dict[str, Any] = {}
    served_self = False
    env_valid = env_invalid = 0
    escalations = 0
    for name, fn in TASKS:
        nonce = f"battery:{run_id}:{entry_id}:{name}"
        before = _CallLedger(chat)
        score, detail = fn(before.fn, entry_id, nonce)
        components[name] = score
        details[name] = detail
        env_valid += before.valid
        env_invalid += before.invalid
        if detail.get("outcome") == "failed_validation":
            env_invalid += 1
            escalations += 1
        if detail.get("served") == entry_id or score is not None:
            served_self = True
    if env_valid + env_invalid:
        components["v_envelope"] = env_valid / (env_valid + env_invalid)
    else:
        components["v_envelope"] = None
    available = [v for v in components.values() if v is not None]
    composite = (sum(available) / len(available)) if available else None
    measured = composite is not None and len(available) >= 3
    score = None
    if measured:
        score = derive_score(composite)
        if components.get("v_envelope") == 0.0:
            # rubric gate: an entry whose every produced call was
            # malformed cannot drive tools at all, whatever the other
            # components say — the score floors at 1
            score = 1
    return {
        "entry": entry_id,
        "measured": measured,
        "tooling_score": score,
        "composite": round(composite, 3) if composite is not None else None,
        "components": {k: (round(v, 3) if isinstance(v, float) else v)
                       for k, v in components.items()},
        "tasks": details,
        "envelope": {"valid": env_valid, "invalid": env_invalid},
        "escalations": escalations,
        "served_self": served_self,
    }


class _CallLedger:
    """Wraps the chat fn to count envelope validity of every call the
    pinned entry produced across a task's turns."""

    def __init__(self, chat: ChatFn):
        self.valid = 0
        self.invalid = 0

        def wrapped(messages, tools, pin):
            result = chat(messages, tools, pin)
            if result.get("served_model") == pin and not result.get("error"):
                declared = {(t.get("function") or {}).get("name")
                            for t in tools}
                for call in _calls(result):
                    _, ok = _parse_args(call)
                    if ok and _call_name(call) in declared:
                        self.valid += 1
                    else:
                        self.invalid += 1
            return result

        self.fn = wrapped


def derive_score(composite: float) -> int:
    return max(1, min(5, 1 + round(4 * composite)))


# ----------------------------------------------------------------------
# live transport (stdlib urllib; runs inside the gateway container)
# ----------------------------------------------------------------------

def make_http_chat(base_url: str, api_key: str, timeout: float = 150.0,
                   pace: float = 0.0,
                   provider_pace: Optional[dict[str, float]] = None,
                   entry_provider: Optional[dict[str, str]] = None):
    last_call: dict[str, float] = {}

    def chat(messages: list[dict], tools: list[dict], pin: str) -> dict:
        provider = (entry_provider or {}).get(pin, "")
        now = time.time()
        # global pace between any two calls; stricter per-provider
        # pace (e.g. a trial provider's RPM cap) on top of it
        for key, wait in (("_any", pace),
                          (provider, (provider_pace or {}).get(
                              provider, 0.0))):
            if not key:
                continue
            since = now - last_call.get(key, 0.0)
            if last_call.get(key) and since < wait:
                time.sleep(wait - since)
                now = time.time()
        # NOTE: no temperature is sent. The first fleet run
        # (2026-11-13) proved temperature=0.0 is itself a request
        # fault on real upstreams — OpenAI's gpt-5.5 rejects any
        # non-default value outright, and Mistral rejects greedy
        # sampling unless top_p == 1 (error 3054). A battery must
        # never fail an entry for the battery's own request shape.
        body = {"model": pin, "messages": messages, "tools": tools}
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                served = resp.headers.get("X-Sovereign-Model")
                escalated = (resp.headers.get("X-Sovereign-Escalated")
                             == "true")
        except urllib.error.HTTPError as e:
            last_call["_any"] = last_call[provider] = time.time()
            return {"error": f"http_{e.code}", "served_model": None,
                    "escalated": False, "message": None}
        except Exception as e:  # transport / timeout
            last_call["_any"] = last_call[provider] = time.time()
            return {"error": f"transport:{type(e).__name__}",
                    "served_model": None, "escalated": False,
                    "message": None}
        last_call["_any"] = last_call[provider] = time.time()
        msg = ((data.get("choices") or [{}])[0].get("message")
               if isinstance(data, dict) else None)
        return {"error": None, "served_model": served,
                "escalated": escalated, "message": msg}

    return chat


# ----------------------------------------------------------------------
# report + registry patch
# ----------------------------------------------------------------------

RUBRIC_MD = """\
## Rubric (mechanical — no hand-entered scores)

Components per entry: T1 exact-args, T2 large-payload integrity,
T3 parallel calls, T4 tool-result follow-through, T5 error recovery
(identical re-issue after a tool error = 0), V envelope validity
(fraction of the entry's calls with strict-JSON arguments naming a
declared tool; a gateway escalation away from the pinned entry on a
tool task counts one invalid call and scores that task 0 — the
pinned response failed the gateway's own validation).
Composite = mean of available components; an entry needs >= 3
available components to be measured. Tooling score =
1 + round(4 * composite), clamped 1..5 — with one gate: if the
envelope component is 0 (every call the entry produced was
malformed), the score floors at 1 regardless of composite.
Tasks the pinned entry never served are no_evidence and
contribute nothing.
"""


def render_markdown(report: dict) -> str:
    lines = [f"# Tooling battery report — {report['run_id']}",
             "",
             f"Gateway: {report.get('gateway_version')} · "
             f"entries targeted: {report['entries_targeted']} · "
             f"measured: {report['entries_measured']} · "
             f"served-but-unmeasured: {report['entries_served_unmeasured']} · "
             f"never served: {report['entries_never_served']}",
             "", RUBRIC_MD, "",
             "| entry | tooling | composite | T1 | T2 | T3 | T4 | T5 | env | esc |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["results"]:
        c = r["components"]
        fmt = lambda v: "—" if v is None else f"{v:.2f}"  # noqa: E731
        lines.append(
            f"| {r['entry']} | "
            f"{r['tooling_score'] if r['tooling_score'] is not None else '—'} "
            f"| {fmt(r['composite'])} | {fmt(c.get('t1_exact_args'))} "
            f"| {fmt(c.get('t2_large_payload'))} "
            f"| {fmt(c.get('t3_parallel_calls'))} "
            f"| {fmt(c.get('t4_follow_through'))} "
            f"| {fmt(c.get('t5_error_recovery'))} "
            f"| {fmt(c.get('v_envelope'))} | {r['escalations']} |")
    lines += ["", "## Per-entry detail", ""]
    for r in report["results"]:
        lines.append(f"### {r['entry']}")
        lines.append(f"- measured: {r['measured']} · served_self: "
                     f"{r['served_self']} · envelope: "
                     f"{r['envelope']['valid']} valid / "
                     f"{r['envelope']['invalid']} invalid")
        for name, d in r["tasks"].items():
            lines.append(f"- {name}: {json.dumps(d)[:300]}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--registry", required=True,
                    help="registry.json to take the entry list from")
    ap.add_argument("--out", required=True, help="report path prefix")
    ap.add_argument("--entries", default=None,
                    help="comma-separated subset of entry ids")
    ap.add_argument("--pace", type=float, default=2.0)
    ap.add_argument("--provider-pace", default="",
                    help="provider=seconds pairs, comma-separated")
    ap.add_argument("--key-env", default="SR_BATTERY_KEY")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--emit-registry-patch", action="store_true")
    args = ap.parse_args(argv)

    api_key = os.environ.get(args.key_env)
    if not api_key:
        print(f"missing client key in env {args.key_env}", file=sys.stderr)
        return 2

    reg = json.loads(Path(args.registry).read_text())
    raw = reg["models"] if isinstance(reg, dict) else reg
    entries = [m for m in raw if m.get("enabled", True)]
    if args.entries:
        wanted = {s.strip() for s in args.entries.split(",")}
        entries = [m for m in entries if m["id"] in wanted]
    entry_provider = {m["id"]: m["provider"] for m in entries}

    run_id = date.today().isoformat()
    out_json = Path(f"{args.out}.json")
    results: list[dict] = []
    done: set[str] = set()
    if args.resume and out_json.exists():
        prior = json.loads(out_json.read_text())
        results = prior.get("results", [])
        done = {r["entry"] for r in results}
        run_id = prior.get("run_id", run_id)

    provider_pace = {}
    for pair in args.provider_pace.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            provider_pace[k.strip()] = float(v)

    chat = make_http_chat(args.base_url, api_key, pace=args.pace,
                          provider_pace=provider_pace,
                          entry_provider=entry_provider)

    version = None
    try:
        req = urllib.request.Request(
            args.base_url.rstrip("/").rsplit("/v1", 1)[0] + "/health")
        with urllib.request.urlopen(req, timeout=10) as resp:
            version = json.loads(resp.read()).get("version")
    except Exception:
        version = None

    for m in entries:
        if m["id"] in done:
            continue
        t0 = time.time()
        res = run_entry(chat, m["id"], run_id)
        res["provider"] = m["provider"]
        res["lane"] = m["lane"]
        res["seconds"] = round(time.time() - t0, 1)
        results.append(res)
        report = _assemble(run_id, version, len(entries), results)
        out_json.write_text(json.dumps(report, indent=1))
        print(f"{m['id']}: measured={res['measured']} "
              f"score={res['tooling_score']} ({res['seconds']}s)",
              flush=True)

    report = _assemble(run_id, version, len(entries), results)
    out_json.write_text(json.dumps(report, indent=1))
    Path(f"{args.out}.md").write_text(render_markdown(report))

    if args.emit_registry_patch:
        patch = build_registry_patch(results, run_id)
        Path(f"{args.out}-registry-patch.json").write_text(
            json.dumps(patch, indent=1, sort_keys=True))
    print(f"done: {report['entries_measured']} measured of "
          f"{report['entries_targeted']}")
    return 0


def build_registry_patch(results: list[dict], run_id: str,
                         today: Optional[date] = None) -> dict:
    """The battery's registry consequences, and ONLY those: measured
    entries get their derived tooling score + provenance; every entry
    the battery actually served (measured or not) gets a
    verified_until stamp 30 days out (the protocol's staleness
    horizon). Never-served entries get nothing — the battery does not
    vouch for routes it could not reach."""
    until = ((today or date.today()) + timedelta(days=30)).isoformat()
    patch: dict[str, dict] = {}
    for r in results:
        if r["measured"]:
            patch[r["entry"]] = {
                "tooling": r["tooling_score"],
                "tooling_source": f"tooling-battery:{run_id}",
                "verified_until": until}
        elif r["served_self"]:
            patch[r["entry"]] = {"verified_until": until}
    return patch


def _assemble(run_id: str, version: Optional[str], targeted: int,
              results: list[dict]) -> dict:
    return {
        "run_id": run_id,
        "gateway_version": version,
        "entries_targeted": targeted,
        "entries_measured": sum(1 for r in results if r["measured"]),
        "entries_served_unmeasured":
            sum(1 for r in results if r["served_self"] and not r["measured"]),
        "entries_never_served":
            sum(1 for r in results if not r["served_self"]),
        "results": results,
    }


if __name__ == "__main__":
    raise SystemExit(main())
