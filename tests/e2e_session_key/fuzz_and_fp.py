"""Differential fuzz (old v0.10.2 detector vs branch) + FP/FN rates on corpora.
Prints only counts and case names, never values."""
import sys, random, string, json, re, importlib.util
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m
old = load("old_sens", HERE / "baseline_v0102_sensitivity.py")
new = load("new_sens", HERE.parents[1] / "src" / "sensitivity.py")
R = random.Random(20261127)
AGENT_RE = re.compile(r"(?<![A-Za-z0-9])session\s*[:=]\s*[\"'`*]*agent:[A-Za-z0-9_.\-]+(?::[A-Za-z0-9_.@+\-]+)+[`*'\")\]}>|!?.]*", re.I)
NOUNS = ["session", "Session", "SESSION", "password", "secret", "api_key", "access_token", "auth-token", "client_secret", "pwd", "cookie", "session cookie"]
SEPS = ["=", ": ", " = ", ":", " is ", "= '", '="']
def val():
    k = R.random()
    if k < .3: return "agent:" + R.choice(["main", "devops-agent", "x"]) + "".join(":" + "".join(R.choice(string.ascii_letters + string.digits + "-_.@+") for _ in range(R.randint(1, 12))) for _ in range(R.randint(1, 4)))
    if k < .45: return "agent" + R.choice([":", "", "::", ": "]) + "".join(R.choice(string.ascii_letters + string.digits) for _ in range(R.randint(0, 20)))
    if k < .6: return "".join(R.choice(string.ascii_letters + string.digits) for _ in range(R.randint(4, 48)))
    if k < .7: return "s:" + "".join(R.choice(string.ascii_letters + string.digits) for _ in range(24)) + "." + "".join(R.choice(string.ascii_letters) for _ in range(20))
    if k < .8: return R.choice(["started", "expired", "main", "none", "null", "<token>", "${SESSION}", "abc…xyz", "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"])
    return "".join(R.choice(string.printable[:94]) for _ in range(R.randint(1, 30)))
def text():
    parts = []
    for _ in range(R.randint(1, 4)):
        parts.append(R.choice(["", "Runtime: agent=main | ", "`", "**", "→ ", "日本 ", "\n", "Cookie: "]) + R.choice(NOUNS) + R.choice(SEPS) + val() + R.choice(["", " | host=x", "`", "**", ".", ")", "\r\n", " 🚀"]))
    return " ".join(parts)
N = 60000; viol_super = []; viol_explained = []; changed = 0
for i in range(N):
    t = text(); o = set(old.scan_text(t).detectors); n = set(new.scan_text(t).detectors)
    if not n <= o: viol_super.append(i)            # new must never flag something old didn't
    if n != o:
        changed += 1
        def _rm(mm):
            ls = t.rfind("\n", 0, mm.start()) + 1
            return mm.group(0) if "cookie" in t[ls:mm.start()].lower() else ""
        stripped = AGENT_RE.sub(_rm, t)
        o2 = set(old.scan_text(stripped).detectors)
        if o2 != n: viol_explained.append(i)       # every difference must be explained by an agent-shaped session value
# property: random non-agent session values flag identically
diff_nonagent = 0
for i in range(20000):
    v = "".join(R.choice(string.ascii_letters + string.digits + "-_.:/+") for _ in range(R.randint(4, 60)))
    if v.lower().startswith("agent:"): continue
    for sep in ("=", ": "):
        if old.scan_text("session" + sep + v).detectors != new.scan_text("session" + sep + v).detectors: diff_nonagent += 1
# property: agent-shaped session values never flag credential_assignment
agent_flag = 0
for sk in corpus.session_keys() * 20:
    for fmt in ("session={}", "Session: {}", "| session={} |", "session=`{}`", "**session={}**", "(session={})", "session={}."):
        if "credential_assignment" in new.scan_text(fmt.format(sk)).detectors: agent_flag += 1
res = {"fuzz_cases": N, "fuzz_changed": changed, "superset_violations": len(viol_super),
       "unexplained_differences": len(viol_explained), "nonagent_session_value_diffs": diff_nonagent,
       "agent_shaped_still_flagged": agent_flag}
# corpora
def rate(mod, convs):
    return sum(1 for m in convs if mod.scan_messages(m).sensitive) / len(convs)
oc, he, n8 = corpus.benign_openclaw(600), corpus.benign_hermes(400), corpus.benign_n8n(400)
res["fp_openclaw"] = {"old": rate(old, oc), "new": rate(new, oc), "n": len(oc)}
res["fp_hermes"] = {"old": rate(old, he), "new": rate(new, he), "n": len(he)}
res["fp_n8n"] = {"old": rate(old, n8), "new": rate(new, n8), "n": len(n8)}
HARD = ["Session: 2026-11-27 standup notes", "tmux new -s work; session: work attached", "the session expired, please log in again",
        "password reset link sent to the user", "api_key: <redacted>", "secret: ${VAULT_SECRET}", "Use session=agent:main:main for routing",
        'password = os.environ["DB_PASSWORD"]', 'api_key=os.getenv("OPENAI_API_KEY")', "access_token: required for this endpoint",
        "Session: agent:devops-agent:main", "session: main", "pwd: /home/jordan", "secret=…redacted…", "session=0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0",
        "Session 42 of user jordan started", "session_id: 0bb93de5-8c97-4d13-bd89-373452ce93fe", "the secret to good soup is patience",
        "PWD=/srv/app", "Your session cookie is stored securely"]
res["hard_benign"] = {"n": len(HARD), "old_flagged": sum(1 for h in HARD if old.scan_text(h).sensitive),
                      "new_flagged": sum(1 for h in HARD if new.scan_text(h).sensitive),
                      "new_flagged_cases": [h[:40] for h in HARD if new.scan_text(h).sensitive]}
sc = corpus.secret_cases()
must = [k for k, v in sc.items() if v[1] == "must"]; gap = [k for k, v in sc.items() if v[1] == "gap"]
res["fn_must"] = {"missed_new": [k for k in must if not new.scan_text(sc[k][0]).sensitive],
                  "missed_old": [k for k in must if not old.scan_text(sc[k][0]).sensitive], "n": len(must)}
res["fn_gap"] = {"missed_new": [k for k in gap if not new.scan_text(sc[k][0]).sensitive], "n": len(gap)}
print(json.dumps(res, indent=1)); (Path("/tmp") / "fuzz_fp_results.json").write_text(json.dumps(res, indent=1))
bad = res["superset_violations"] or res["unexplained_differences"] or res["nonagent_session_value_diffs"] or res["agent_shaped_still_flagged"] or res["fn_must"]["missed_new"]
sys.exit(1 if bad else 0)
