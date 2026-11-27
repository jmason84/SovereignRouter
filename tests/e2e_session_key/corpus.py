"""Synthetic E2E corpora. All secret values are generated randomly per run
(never real); callers must never print values, only case names."""
import random, string, secrets as S
R = random.Random(1127)
def rs(n, alpha=string.ascii_letters + string.digits):
    return "".join(S.choice(alpha) for _ in range(n))
UP = string.ascii_uppercase + string.digits

def secret_cases():
    """name -> (text, kind) ; kind 'must' = flagged by v0.10.2 (regression guard),
    'gap' = known pre-existing gap (informational)."""
    tok = rs(32); pw = rs(6) + "-" + rs(8)
    c = {
     "openai_key":      (f"use this key sk-{rs(40)} for the batch job", "must"),
     "openai_proj_key": (f"OPENAI_API_KEY=sk-proj-{rs(60)}", "must"),
     "anthropic_key":   (f"anthropic: sk-ant-api03-{rs(80)}", "must"),
     "groq_key":        (f"groq key gsk_{rs(52)}", "must"),
     "aws_access_key":  (f"aws id AKIA{''.join(S.choice(UP) for _ in range(16))} region us-west-2", "must"),
     "aws_secret_ctx":  (f"aws_secret_access_key = {rs(40)}", "must"),
     "github_token":    (f"git clone https://github.com/x/y  token ghp_{rs(36)}", "must"),
     "github_pat":      (f"export GH=github_pat_{rs(82)}", "must"),
     "gitlab_pat":      (f"glpat-{rs(20)} is the CI token", "must"),
     "slack_token":     (f"slack bot xoxb-{rs(12,string.digits)}-{rs(24)}", "must"),
     "google_api_key":  (f"maps key AIza{rs(35)}", "must"),
     "pem_rsa":         ("-----BEGIN RSA PRIVATE KEY-----\n" + rs(64) + "\n-----END RSA PRIVATE KEY-----", "must"),
     "pem_openssh":     ("cat ~/.ssh/id_ed25519\n-----BEGIN OPENSSH PRIVATE KEY-----\n" + rs(70) + "\n-----END OPENSSH PRIVATE KEY-----", "must"),
     "pem_ec":          ("-----BEGIN EC PRIVATE KEY-----\n" + rs(40), "must"),
     "pem_pkcs8":       ("-----BEGIN PRIVATE KEY-----\n" + rs(40), "must"),
     "bearer":          (f"curl -H 'Authorization: Bearer {rs(40)}' https://api.example.com", "must"),
     "basic_auth_url":  (f"postgres://admin:{pw}@10.0.0.5:5432/app", "must"),
     "basic_auth_http": (f"https://deploy:{pw}@registry.example.com/v2/", "must"),
     "password_assign": (f"password={pw}", "must"),
     "password_yaml":   (f"db:\n  user: app\n  password: {pw}\n", "must"),
     "api_key_assign":  (f"api_key: {tok}", "must"),
     "api_dash_key":    (f"api-key={tok}", "must"),
     "client_secret":   (f"client_secret={tok}", "must"),
     "access_token":    (f"access_token={tok}", "must"),
     "auth_token":      (f"AUTH_TOKEN: {tok}", "must"),
     "secret_assign":   (f"secret = {tok}", "must"),
     "cookie_session":  (f"Cookie: session={tok}; theme=dark", "must"),
     "set_cookie_session": (f"Set-Cookie: session={tok}; Path=/; HttpOnly", "must"),
     "session_assign":  (f"session={tok}", "must"),
     "session_colon":   (f"session: {tok}", "must"),
     "signed_session":  (f"session=s:{rs(24)}.{rs(43)}", "must"),
     "g180_session_cookie": ("here is a session cookie value session=FAKESESSIONVALUE000 -- explain why it must be treated as a credential", "must"),
     "g179_prose_pw":   ("a user shared their account password in chat: correct-horse-fake-staple -- what should support do next", "must"),
     "prose_db_pw":     (f"the database password for the reporting user is Hunter2-{rs(6)}", "must"),
     "prose_session_cookie": (f"the session cookie is {tok}", "must"),
     "runtime_plus_secret": ("Runtime: agent=main | session=agent:main:main | host=3718e29d0d93 | model=sovereign/auto\n" + f"password={pw}", "must"),
     "runtime_line_with_random_session": (f"Runtime: agent=main | session={tok} | host=x", "must"),
     "session_agent_prefix_bypass_pw": (f"password=agent:main:{tok}", "must"),
     # pre-existing gaps (not flagged by v0.10.2 either)
     "gap_sessionid":   (f"sessionid={tok}", "gap"),
     "gap_session_token": (f"session_token={tok}", "gap"),
     "gap_raw_jwt":     (f"token eyJ{rs(20)}.eyJ{rs(40)}.{rs(43)}", "gap"),
     "gap_connect_sid": (f"connect.sid=s%3A{rs(24)}.{rs(43)}", "gap"),
     "gap_encrypted_pem": ("-----BEGIN ENCRYPTED PRIVATE KEY-----\n" + rs(40), "gap"),
     "gap_pgp":         ("-----BEGIN PGP PRIVATE KEY BLOCK-----\n" + rs(40), "gap"),
    }
    return c

AGENTS = ["main", "devops-agent", "creative-agent", "openclaw"]
def session_keys():
    ks = []
    for a in AGENTS:
        ks += [f"agent:{a}:main", f"agent:{a}:telegram:direct:{R.randint(10**8,10**10)}",
               f"agent:{a}:cron:{'-'.join(rs(n,'0123456789abcdef') for n in (8,4,4,4,12))}",
               f"agent:{a}:telegram:group:-100{R.randint(10**9,10**10)}",
               f"agent:{a}:subagent:{rs(8,'0123456789abcdef')}",
               f"agent:{a}:discord:channel:{R.randint(10**15,10**17)}",
               f"agent:{a}:explicit:{'-'.join(rs(n,'0123456789abcdef') for n in (8,4,4,4,12))}"]
    return ks

def runtime_line(sk, model="sovereign/auto"):
    return (f"Runtime: agent={sk.split(':')[1]} | session={sk} | host={rs(12,'0123456789abcdef')} | "
            f"repo=/home/node/.openclaw/workspace/main | os=Linux 6.8.0-142-generic (x64) | node=v24.22.1 | "
            f"active_node=unknown | active_node_identity=unknown | model={model} | default_model=sovereign/auto | channel=telegram")

USER_PROMPTS = [
    "check disk usage on the NAS and tell me what is filling it",
    "summarise today's calendar and draft replies to anything urgent",
    "why is the plex container restarting? look at the logs",
    "write a python script that renames photos by EXIF date",
    "what's the weather in Denver tomorrow",
    "refactor the n8n workflow export into smaller sub-workflows",
    "explain the difference between ZFS mirror and raidz1 for 4 disks",
    "deploy the staging stack and run the smoke tests",
    "Ünïcødé test: 日本語のメッセージを要約してください 🚀",
    "list the open PRs and tell me which ones are stale",
]

TOOL_OUTPUTS = [
    "CONTAINER ID   IMAGE          STATUS\n3718e29d0d93   openclaw:2026.11.10   Up 12 hours (healthy)",
    "commit 11b53695ed953dff35813d1cdc3fa96a2d5d4758\nAuthor: jmason84\nDate: Fri Nov 27 00:45:31 2026 -0700\n\n    Merge PR #21",
    "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 916G 702G 168G 81% /mnt/nas",
    "Nov 27 13:01:02 host systemd[1]: Started Session 42 of User jordan.",
    "uuid=0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0 sha=9f86d081884c7d659a2feaa0c55ad015",
    "HTTP/1.1 200 OK\nContent-Type: application/json\n{\"ok\":true,\"session_id\":\"0bb93de5-8c97-4d13-bd89-373452ce93fe\"}",
]

def benign_openclaw(n):
    out = []; sks = session_keys()
    for i in range(n):
        sk = R.choice(sks)
        sys_prompt = ("You are OpenClaw, Jordan's assistant.\n## Runtime\n" + runtime_line(sk)
                      + "\n\nThinking level: low. Reply in Markdown.")
        msgs = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": R.choice(USER_PROMPTS)}]
        if i % 3 == 0:
            msgs.append({"role": "assistant", "content": f"🧵 Session: {sk}\nContext: 40k/131k (30%)"})
            msgs.append({"role": "user", "content": R.choice(USER_PROMPTS)})
        if i % 4 == 0:
            msgs.append({"role": "tool", "tool_call_id": "c1", "content": R.choice(TOOL_OUTPUTS)})
        out.append(msgs)
    return out

def benign_hermes(n):
    out = []
    for i in range(n):
        msgs = [{"role": "system", "content": "You are Hermes, an ops agent. Use tools when needed. Session started 2026-11-27."},
                {"role": "user", "content": R.choice(USER_PROMPTS)}]
        if i % 2 == 0:
            msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "terminal", "arguments": "{\"command\":\"docker ps\"}"}}]})
            msgs.append({"role": "tool", "tool_call_id": "c1", "content": R.choice(TOOL_OUTPUTS)})
        out.append(msgs)
    return out

def benign_n8n(n):
    subjects = ["Invoice #4411 overdue", "Weekly SODAK news digest", "New lead from website form", "Calendar: board meeting moved"]
    return [[{"role": "system", "content": "Classify the email into one of: billing, news, lead, calendar. Reply with JSON."},
             {"role": "user", "content": f"Subject: {R.choice(subjects)}\nFrom: someone@example.com\n\nBody text {i}: please see attached."}]
            for i in range(n)]
