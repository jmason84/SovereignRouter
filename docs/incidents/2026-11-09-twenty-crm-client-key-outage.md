# Sovereign Router v0.5.6 — Twenty CRM key incident / commands

**Security notice:** This is the PUBLIC-safe copy. Private LAN IPs and domains were replaced with placeholders. Full address-level command transcript is stored in the private Google Doc: https://docs.google.com/document/d/1rUfJscQpaMKnXM70KNP20V3YeGmr2z13zDVu7RY5tLg/edit.

SOVEREIGN ROUTER v0.5.6 — TWENTY CRM API KEY INCIDENT
Complete command and evidence log | November 9, 2026
Recorded from ChatGPT conversation, user-pasted terminal transcripts and read-only live Portainer observations. No credentials reproduced.

STATUS AS OF THE LATEST USER EVIDENCE
RECOVERED — sovereign-router-v05-gateway was “Up 6 seconds (healthy)” after moving the raw CRM key out of the mounted provider-key directory. Later startup logs showed Uvicorn started successfully and an INTERNAL GET /health returned 200 OK. This proves gateway health at that moment, not CRM connectivity or successful CRM-key authentication.
Twenty-specific client registration: NOT ACTIVE in the restored three-client registry.
CRM allowlist entry: USER OPENED NGINX CONFIG IN NANO; actual edit is unverified. nginx -t and nginx -s reload both failed before recovery; no successful reload evidence.
The admin key previously pasted in chat was exposed and requires controlled rotation. Do not publish any secret.
User's explicit correction: Windows laptop is only a test client; the actual Twenty CRM server to authorize is CT107 [CRM_CT107_IP].

1 — PURPOSE AND SYSTEM INVENTORY
Objective: add a dedicated Twenty CRM inference client using Sovereign Router OpenAI-compatible URL http://[ROUTER_VM103_IP]:6075/v1, with model alias auto.
Production router: VM103 / root@[ROUTER_HOST], IP [ROUTER_VM103_IP], image [INTERNAL_REGISTRY_IP]:5000/sovereign-router:v0.5.6, container sovereign-router-v05-gateway.
Nginx: sovereign-router-v05-nginx, publishes LAN [ROUTER_VM103_IP]:6075 to container port 8080. Local source-IP allowlist denies unlisted origins.
Classifier: sovereign-router-v05-classifier.
Older router at :6070, v0.3/v0.4 canaries, and other services were not intentionally modified.
Important live mount evidence: host /root/sovereign-router-v05/state -> /app/state (rw); host /root/sovereign-router-v05/secrets -> /app/state/keys (ro).
The v0.5.6 gateway loads clients.json from state and automatically reads every file in /app/state/keys as a provider key. Client raw keys therefore MUST NOT be stored in /root/sovereign-router-v05/secrets/.
Existing registry before change: top-level keys admin_key_sha256, clients, comment; 3 clients as a list. Per-client field names: id, key_sha256, default_data_class, lanes, premium_allowed, daily_target_usd, sensitive_cleared_providers; third client also has premium_daily_count_alert and premium_monthly_alert_usd. A public v0.5.3 source sample uses different field names and cannot safely be used for this deployed schema.

2 — ROOT CAUSES AND IMPACT
R1. First key script assumed client field name "name" rather than v0.5.6 "id"; failed with KeyError before modification.
R2. Revised script wrote clients.json via mkstemp with mode 0600. The non-root running gateway could no longer read /app/state/clients.json: PermissionError [Errno 13], causing a restart loop.
R3. Revised script put twenty-crm.key at /root/sovereign-router-v05/secrets/twenty-crm.key mode 0600. Gateway auto-scanned the mounted provider-key directory, failed to read that client-key file at /app/state/keys/twenty-crm.key, and restarted repeatedly.
R4. Nginx config validation failed “host not found in upstream gateway:6075” because the gateway was down. An attempted reload also failed; no successful Nginx config load documented.
R5. Earlier curl attempts from Windows hit Nginx 403 and therefore did not validate admin or client key. PowerShell initially rejected Linux Bash syntax.
Restoration: restore the original file from timestamped backup (mode 0644 root:root); move raw client key to a root-only directory OUTSIDE the provider-key mount; restart gateway. Latest user evidence confirmed gateway recovered.
Outstanding: add the existing raw CRM key's hash as fourth client with correct permissions; test auth; inspect and validate CRM IP allowlist; use an authorized test from CT107; configure Twenty custom model auto; rotate exposed admin key after planning impacted integrations.
Warning: repeated cp -a original backup to clients.json and cp -a clients.json to clients.json.failed-crm may have overwritten the failed-CRM snapshot. Do not assume the failed-state file contains original failed contents without inspecting it.

3 — CHRONOLOGICAL COMMAND TRANSCRIPT: USER-EXECUTED COMMANDS
The following blocks transcribe the distinct submitted commands from the user-provided conversation; repetitions are explicitly called out. Output/error summaries are included. Shell prompts and line-continuation leading ">" have been removed. No raw secret was pasted in any command shown; the admin secret pasted separately in chat is intentionally REDACTED.

3.1 FIRST CURL ATTEMPT — WINDOWS POWERSHELL — BAD BASH SYNTAX (sequence repeated; not successful)
~~~shell
curl -sS -i --connect-timeout 5 --max-time 20 \
-H "Authorization: Bearer $ROUTER_KEY" \
http://[ROUTER_VM103_IP]:6075/admin/costs
unset ROUTER_KEY
read -rsp "Enter Sovereign Router API key: " ROUTER_KEY
echo
curl -sS -i --connect-timeout 5 --max-time 20 \
-H "Authorization: Bearer $ROUTER_KEY" \
http://[ROUTER_VM103_IP]:6075/v1/models
unset ROUTER_KEY
~~~
Observed: “Invoke-WebRequest: A parameter cannot be found that matches parameter name 'sS'”, -H not recognized, URL not recognized, unset and read not recognized; echo prompted for InputObject. This was a shell mismatch, not evidence of key failure.

3.2 REVISED POWERSHELL CREDENTIAL CHECK — EXECUTED TWICE
~~~powershell
$secret = Read-Host "Enter Sovereign Router API key" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    Write-Host "`n=== ADMIN API TEST ==="
    curl.exe -sS -i --connect-timeout 5 --max-time 20 `
        -H "Authorization: Bearer $key" `
        "http://[ROUTER_VM103_IP]:6075/admin/costs"
    Write-Host "`n=== MODEL API TEST ==="
    curl.exe -sS -i --connect-timeout 5 --max-time 20 `
        -H "Authorization: Bearer $key" `
        "http://[ROUTER_VM103_IP]:6075/v1/models"
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    Remove-Variable key, secret -ErrorAction SilentlyContinue
}
~~~
Results: 10:16:01 UTC and 10:16:26 UTC; HTTP/1.1 403 Forbidden, Server: nginx on BOTH admin/costs and v1/models each time. The Windows source was unapproved; key validity not measured. Original admin credential deliberately omitted.

3.3 INCORRECT KEY-CREATION PYTHON — EXECUTED TWICE; BOTH FAILED BEFORE WRITES
~~~bash
sudo python3 - <<'PY'
import os, json, secrets, hashlib, shutil, tempfile
from pathlib import Path
from datetime import datetime, timezone

root = Path("/root/sovereign-router-v05")
config = root / "state/clients.json"
keyfile = root / "secrets/twenty-crm.key"

data = json.loads(config.read_text())

if any(c["name"] == "twenty-crm" for c in data["clients"]):
    raise SystemExit("twenty-crm already registered. No changes made.")
if keyfile.exists():
    raise SystemExit("Key file already exists. No changes made.")

key = "svr_" + secrets.token_hex(24)
data["clients"].append({
    "name": "twenty-crm",
    "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
    "default_data_class": "internal",
    "lanes": ["private-free", "paid"],
    "premium": False,
    "daily_cost_target_usd": 0.50
})

stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
backup = config.with_name("clients.json.bak-" + stamp)
shutil.copy2(config, backup)

fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(key + "\n")

fd, temp = tempfile.mkstemp(dir=config.parent, prefix=".clients-")
with os.fdopen(fd, "w") as f:
    os.fchmod(f.fileno(), 0o600)
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(temp, config)

print("New twenty-crm client registered.")
print("Backup:", backup)
print("Key saved securely:", keyfile)
PY
~~~
Output BOTH attempts: Traceback line 11 in generator, KeyError: 'name'. No file modification was reached.

3.4 DOCKER INVENTORY BEFORE CHANGE
~~~bash
docker ps
~~~
Observed: v0.5.6 gateway healthy, v0.5.0 classifier healthy, Nginx healthy, legacy router and canaries alive, routellm and portainer_edge_agent alive.

3.5 READ-ONLY CONFIGURATION FIELDS DIAGNOSTIC — EXECUTED
~~~bash
sudo python3 - <<'PY'
import json
from pathlib import Path

base = Path("/root/sovereign-router-v05")
config = base / "state/clients.json"
keyfile = base / "secrets/twenty-crm.key"

data = json.loads(config.read_text())
clients = data.get("clients")

print("=== ROUTER CLIENT CONFIGURATION ===")
print("Top-level fields:", sorted(data.keys()))
print("Clients type:", type(clients).__name__)

if isinstance(clients, list):
    print("Client count:", len(clients))
    for i, entry in enumerate(clients):
        if isinstance(entry, dict):
            print(f"Client {i+1} fields:", sorted(entry.keys()))
        else:
            print(f"Client {i+1} type:", type(entry).__name__)
elif isinstance(clients, dict):
    print("Client count:", len(clients))
    for i, entry in enumerate(clients.values()):
        if isinstance(entry, dict):
            print(f"Client {i+1} fields:", sorted(entry.keys()))
        else:
            print(f"Client {i+1} type:", type(entry).__name__)

print("Twenty key already exists:", keyfile.exists())
print("Configuration JSON: VALID")
PY
~~~
Output: Top-level fields ['admin_key_sha256','clients','comment']; clients list of length 3; first two entries fields daily_target_usd, default_data_class, id, key_sha256, lanes, premium_allowed, sensitive_cleared_providers; third same plus premium_daily_count_alert, premium_monthly_alert_usd. Twenty key exists False. Valid JSON.

3.6 CORRECTED CLIENT-SCHEMA SCRIPT — EXECUTED ONCE, SUCCESS BUT CAUSED PERMISSION/DIRECTORY BUGS
~~~bash
python3 - <<'PY'
import json, hashlib, secrets, shutil, os, tempfile
from pathlib import Path
from datetime import datetime, timezone

root = Path("/root/sovereign-router-v05")
config = root / "state/clients.json"
keyfile = root / "secrets/twenty-crm.key"

data = json.loads(config.read_text())
clients = data["clients"]

if not isinstance(clients, list):
    raise SystemExit("Unexpected clients format. No changes made.")

if any(c.get("id") == "twenty-crm" for c in clients):
    raise SystemExit("twenty-crm already exists. No changes made.")

if keyfile.exists():
    raise SystemExit("Key file already exists. No changes made.")

key = "svr_" + secrets.token_hex(24)
clients.append({
    "id": "twenty-crm",
    "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
    "default_data_class": "internal",
    "lanes": ["private-free", "paid"],
    "premium_allowed": False,
    "daily_target_usd": 0.50,
    "sensitive_cleared_providers": []
})

stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
backup = config.with_name("clients.json.bak-" + stamp)
shutil.copy2(config, backup)

fd, tmp = tempfile.mkstemp(dir=config.parent, prefix=".clients-")
with os.fdopen(fd, "w") as f:
    os.fchmod(f.fileno(), 0o600)
    json.dump(data, f, indent=2)
    f.write("\n")

json.loads(Path(tmp).read_text())

fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(key + "\n")

os.replace(tmp, config)

print("SUCCESS: twenty-crm client registered")
print("Backup:", backup)
print("API key saved at:", keyfile)
print("Existing clients preserved:", len(clients) - 1)
PY
~~~
Output: SUCCESS: twenty-crm client registered; backup /root/sovereign-router-v05/state/clients.json.bak-20261109-102657; key file /root/sovereign-router-v05/secrets/twenty-crm.key; existing clients preserved: 3.
CAUTION: success meant files written, NOT that running gateway would accept new permissions/configuration.

3.7 VALIDATION, RESTART AND INITIAL CONTAINER CHECK — EXECUTED
~~~bash
python3 -m json.tool /root/sovereign-router-v05/state/clients.json >/dev/null
docker restart sovereign-router-v05-gateway
docker ps --filter name=sovereign-router-v05-gateway
nano /root/sovereign-router-v05/secrets/client-keys.txt
~~~
Output: JSON valid; docker restart returned gateway name; container initially “Up Less than a second (health: starting)” then began restarting. nano opened original client-keys file; edited content is UNKNOWN, no user-pasted evidence. SSH session subsequently disconnected with “client_loop: send disconnect: Unknown error”.

3.8 REPEATED FAILED IN-CONTAINER API TEST — USER RAN IT THREE TIMES; CONTAINER RESTART LOOP
~~~bash
docker exec -i sovereign-router-v05-gateway python - <<'PY'
import urllib.request

key = open("/app/state/keys/twenty-crm.key").read().strip()

req = urllib.request.Request(
    "http://127.0.0.1:6075/v1/models",
    headers={"Authorization": "Bearer " + key}
)

try:
    with urllib.request.urlopen(req, timeout=15) as response:
        print("HTTP STATUS:", response.status)
        print("AUTHENTICATION: SUCCESS")
except Exception as e:
    print("TEST FAILED:", e)
PY
~~~
Outcome for each: Error response from daemon: Container 3f82789675cc... is restarting; wait until running. The test was NOT executed inside gateway.
Between retries user also ran:
~~~bash
sudo -i
~~~
An interactive sudo password prompt followed; password is not recorded.

3.9 NGINX EDIT AND VALIDATION — EXECUTED, BUT CHANGES WERE NOT ACTIVATED
~~~bash
nano /root/sovereign-router-v05/deploy/nginx.conf
docker exec sovereign-router-v05-nginx nginx -t
docker exec sovereign-router-v05-nginx nginx -s reload
~~~
Output for nginx -t: [emerg] host not found in upstream "gateway:6075" in /etc/nginx/nginx.conf:45; configuration test failed.
Output for nginx -s reload: same [emerg] host not found. This was attempted despite failure of nginx -t. No successful reload evidenced. nano edit is unknown; need inspect allowlist.

3.10 RESTORE BACKUP AND CHECK GATEWAY — EXECUTED WITH REPETITIONS
~~~bash
cp -a /root/sovereign-router-v05/state/clients.json \
  /root/sovereign-router-v05/state/clients.json.failed-crm
cp -a /root/sovereign-router-v05/state/clients.json.bak-20261109-102657 \
  /root/sovereign-router-v05/state/clients.json
python3 -m json.tool /root/sovereign-router-v05/state/clients.json >/dev/null
stat -c '%a %U:%G %n' /root/sovereign-router-v05/state/clients.json
docker restart sovereign-router-v05-gateway
~~~
User then repeated “cp -a clients.json clients.json.failed-crm” once, repeated “cp -a ...clients.json.bak-20261109-102657 ...clients.json” TWICE more, repeated python3 -m json.tool and stat once, and repeated docker restart once. Terminal displayed permission “644 root:root” both times.
~~~bash
docker ps --filter name=sovereign-router-v05-gateway
docker logs --tail 15 sovereign-router-v05-gateway
~~~
Gateway still “Restarting (1)”; logs now showed gateway.py create_app line 255 -> cfg.load_provider_keys -> read_text; PermissionError: [Errno 13] Permission denied: '/app/state/keys/twenty-crm.key'. That means original registry access had been repaired, but the newly introduced client raw key remained in a provider-key scan directory.

3.11 ADDITIONAL FILE INSPECTION AND LAN HEALTH REQUEST — EXECUTED
~~~bash
nano /root/sovereign-router-v05/secrets/twenty-crm.key
curl -i http://[ROUTER_VM103_IP]:6075/health
~~~
nano user edit unknown. curl returned 403 Forbidden, Server: nginx, Date Mon 09 Nov 2026 10:37:34 GMT. This demonstrates source-IP allowlist behavior, not whether the gateway itself is healthy. The gateway was still unavailable according to Docker.

3.12 SUCCESSFUL RECOVERY ACTIONS — EXECUTED
~~~bash
install -d -m 700 /root/sovereign-router-v05/client-credentials
mv /root/sovereign-router-v05/secrets/twenty-crm.key \
   /root/sovereign-router-v05/client-credentials/twenty-crm.key
chmod 600 /root/sovereign-router-v05/client-credentials/twenty-crm.key
docker restart sovereign-router-v05-gateway
docker ps --filter name=sovereign-router-v05-gateway
docker logs --tail 15 sovereign-router-v05-gateway
~~~
Output: docker restart sovereign-router-v05-gateway; docker ps “Up 6 seconds (healthy)”; logs include historical PermissionError lines from before restart followed by “INFO: Started server process [1]”, “Application startup complete”, “Uvicorn running on http://0.0.0.0:6075”, and “127.0.0.1:47378 - GET /health HTTP/1.1 200 OK”.
VERIFIED: gateway service restored. Raw key preserved at /root/sovereign-router-v05/client-credentials/twenty-crm.key mode 0600. The client key is not registered in active clients.json after backup restore. No credential content is reproduced here.

4 — ASSISTANT-SUBMITTED COMMANDS/RECOMMENDATIONS, NOT USER-EXECUTION EVIDENCE
Earlier assistant recommended: Bash “read -rsp” curl diagnostic; corrected PowerShell curl diagnostic; original Python key-generation script; read-only schema diagnostic; corrected Python script; a docker exec Python /v1/models probe; editing Nginx to allow [CRM_CT107_IP] and testing/reloading Nginx; saving key to Twenty; backing up/restoring clients.json; moving the raw client key outside secrets/; and checking docker logs/ps. Many of these later appeared in the user's transcript and are recorded as executed in Section 3; the following remain only PROPOSED/UNVERIFIED:
- CRM Nginx rule “allow [CRM_CT107_IP];” (file edited in nano but no verified content or successful nginx -s reload).
- Successful authenticated /v1/models call using twenty-crm key (never proven; old in-container probe failed because container was restarting).
- Direct CT107 network request to the gateway (not shown).
- Successful /v1/chat/completions using model auto as Twenty client (not shown).
- Twenty custom model registration/selection, observed end-to-end Twenty inference (not shown).
- Dedicated rotation of exposed admin key plus updating n8n admin monitoring credentials (not shown).

5 — RECOVERY/CONTINUATION CHECKLIST (DO NOT EXECUTE BLINDLY)
1. Confirm production health remains healthy: docker ps --filter name=sovereign-router-v05-gateway, docker logs --tail 20 sovereign-router-v05-gateway, and INTERNAL health check via approved gateway path.
2. Verify current clients.json has the original three working clients, permissions 0644 root:root, and API keys outside provider secrets scan. Confirm no unwanted file changes in client-keys.txt or Nginx from nano sessions.
3. Use a staging validation or safe preflight to add fourth id “twenty-crm” client using SHA-256 of the EXISTING raw key at /root/sovereign-router-v05/client-credentials/twenty-crm.key, preserve clients.json ownership, file mode 0644 and existing entries, and validate against installed v0.5.6 schema BEFORE restart/reload. Prefer admin /admin/reload if supported and prevalidated; avoid untested file replacement.
4. On an authorized internal host, test client /v1/models with the new key (200), then a small POST /v1/chat/completions with model auto (200) and inspect served model/cost. Never output the raw key.
5. Inspect Nginx allowlist; allow CT107 [CRM_CT107_IP] only if absent; run nginx -t and only reload after test succeeds; verify CT107 source path. Do NOT whitelist Windows desktop simply because a manual diagnostic failed.
6. Configure Twenty AI provider base URL http://[ROUTER_VM103_IP]:6075/v1, key = new dedicated twenty-crm client secret (NOT admin key), register model ID auto, and perform end-to-end smoke test.
7. Rotate exposed admin credential and update dependent n8n admin header auth integrations without disrupting alerts.
8. Record test status, incident closure, exact successful change, and rollback checkpoints in Google Drive and GitHub. Preserve this log.
9. Improve build/runbooks: schema-specific client provisioning with valid writable/readable file modes, path separation of client vs provider keys, automatic startup/health preflight, Nginx validation while upstream is down, and regression test for permission errors.

6 — SOURCE/PROVENANCE
- User-submitted Windows PowerShell and VM103 shell transcripts provided during 2026-11-09 conversation.
- Live read-only Portainer inspection/logs during the same session, endpoint 8 (router VM103): gateway PermissionError on clients.json, gateway restart status; Nginx container healthy. No mutation through connector was made.
- Sovereign Router v0.5 Drive progress report: https://drive.google.com/file/d/1zqD3vSArmgoAJoioXAzhehUaZIjr0TMZ/view
- Sovereign Router v0.5.3 architecture: https://docs.google.com/document/d/1lm_siB4tWOnBen3aAlG6itBqoXANnSBH7k85W0l0CJ4/edit
- Project GitHub (public; redact secrets before posting): https://github.com/jmason84/SovereignRouter
- Explicit caveat: Times are those in user output. Repeated shell commands are summarized by repetition count, not claimed as separately archived system logs. Re-run requirements remain unverified unless a user/connector supplies actual outputs.



7 — ADDENDUM: NOVEMBER 9, 2026, SUBSEQUENT RECOVERY RE-RUN AND INDEPENDENT HEALTH CONFIRMATION

SOURCE: New terminal transcript supplied by the owner after the initial incident report was committed. This addendum is additive and does not change the earlier command/evidence record.

7.1 EXACT USER-SUBMITTED COMMANDS (this attempt)

root@[ROUTER_HOST]:~# install -d -m 700 /root/sovereign-router-v05/client-credentials
root@[ROUTER_HOST]:~# mv /root/sovereign-router-v05/secrets/twenty-crm.key \
   /root/sovereign-router-v05/client-credentials/twenty-crm.key
mv: cannot stat '/root/sovereign-router-v05/secrets/twenty-crm.key': No such file or directory
root@[ROUTER_HOST]:~# chmod 600 /root/sovereign-router-v05/client-credentials/twenty-crm.key
root@[ROUTER_HOST]:~# docker restart sovereign-router-v05-gateway
sovereign-router-v05-gateway
root@[ROUTER_HOST]:~# docker ps --filter name=sovereign-router-v05-gateway
CONTAINER ID   IMAGE                                     STATUS                  PORTS      NAMES
3f82789675cc   [INTERNAL_REGISTRY_IP]:5000/sovereign-router:v0.5.6   Up 5 seconds (healthy)   6075/tcp   sovereign-router-v05-gateway
root@[ROUTER_HOST]:~# docker logs --tail 15 sovereign-router-v05-gateway
INFO:     [CONTAINER_LOOPBACK_IP]:34748 - "GET /health HTTP/1.1" 200 OK
INFO:     [CONTAINER_LOOPBACK_IP]:47988 - "GET /health HTTP/1.1" 200 OK
INFO:     [CONTAINER_LOOPBACK_IP]:38618 - "GET /health HTTP/1.1" 200 OK
INFO:     [DOCKER_PROXY_IP]:50004 - "POST /v1/chat/completions HTTP/1.1" 200 OK
INFO:     [DOCKER_PROXY_IP]:50004 - "POST /v1/chat/completions HTTP/1.1" 200 OK
INFO:     [CONTAINER_LOOPBACK_IP]:57132 - "GET /health HTTP/1.1" 200 OK
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [1]
INFO:     Started server process [1]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:6075 (Press CTRL+C to quit)
INFO:     [CONTAINER_LOOPBACK_IP]:35046 - "GET /health HTTP/1.1" 200 OK

7.2 INTERPRETATION
- The mv command FAILED because the original source pathname did not exist. This is consistent with the key having been moved already during the preceding recovery. Do not interpret this error as proof that the key was lost; the subsequent chmod on the destination path produced no error, which supports destination-file existence. Confirm contents only by local hash comparison; never print raw key in shared logs.
- The repeated install -d is safe and preserves an existing directory. chmod succeeded without output. docker restart succeeded.
- The new docker ps shows gateway Up (healthy). The new logs show both historical completed chat requests (HTTP 200) and post-restart internal health (HTTP 200), with clean Uvicorn startup. The two POST completions are not proven to originate from Twenty; they only establish prior gateway inference serving.
- User output contains an interleaved terminal fragment near docker ps (\"--tail 15 sovereign-router-v05-gateway\") attributable to terminal rendering/typing; the intact following docker logs command is included above.
- LIVE read-only Portainer cross-check made during documentation: gateway \"Up 3 minutes (healthy)\", Nginx \"Up 7 days (healthy)\", classifier \"Up 7 days (healthy)\". No changes to the running environment were performed by the assistant.
- CRM key still needs careful registration: the active registry was restored from backup with the original three clients. No successful /v1/models authentication using twenty-crm, no test from CT107, and no end-to-end Twenty model auto response have been evidenced.
- Nginx's allowlist must be inspected and verified; the user opened its config in nano earlier but no successful nginx -t or nginx -s reload after gateway recovery was supplied.
- This addendum supersedes any implication that the most recent mv command succeeded. Earlier successful move is supported by the previous transcript; subsequent failed duplicate move is preserved here as a separate event.

7.3 STATUS AT END OF ADDENDUM
RECOVERED and live-verified healthy: gateway, Nginx, classifier.
UNVERIFIED / PENDING: dedicated CRM authentication, network permission from CT107, Twenty model auto, secure admin-key rotation, complete integration.
This record does not claim that the CRM connection is working.

---

8 — REPAIR BY Muse (2026-11-09, after this log reached GitHub)

Source: Muse (Sovereign Router chat), working from live state before this log arrived, then cross-checked against it line by line. The owner ordered the repair after the retrace.

8.1 CROSS-CHECK VERDICT
This log is accurate. Its status section said exactly the right thing: gateway health was recovered; CRM connectivity was NOT verified. Every independent live observation agrees with the log, and the log supplies the two facts live state could not: (a) the gateway spent part of the night in a restart loop from the mode-0600 clients.json write (R2) and from the raw client key placed inside the provider-key scan directory (R3); (b) the router admin key was pasted into the ChatGPT conversation (see 8.4).

Key identity — resolved: the raw key preserved at /root/sovereign-router-v05/client-credentials/twenty-crm.key (mode 0600) hashes to exactly the key configured in Twenty's AI_PROVIDERS, and to the hash registered by the repair below (hash value withheld — this is the public-safe copy). The key was never wrong. Its registration (client id "twenty-crm", §3.6) was destroyed by the backup restore (§3.10), and the intermediate clients.json state containing it was overwritten by the repeated cp -a commands the log itself warns about (§2). The "failed-crm" file therefore holds the restored three-client state, not the failed one — as the log suspected.

8.2 REPAIR ACTIONS (2026-11-09, ~03:52–03:57 MST)
1. VM103 snapshot pre-twenty-fix-20261109 taken before any change.
2. clients.json backed up (clients.json.bak-pretwentyfix-20261109), then client "crm" appended — checklist §5.3, with client id "crm" (the log's plan said "twenty-crm"; neither the surviving key nor Twenty's config depends on the id). key_sha256 = the existing key's hash; lanes private-free + paid; premium_allowed false; default_data_class internal; daily_target_usd 1.25 (alert/steering only, standing no-caps rule). File mode preserved gateway-readable (0644 root:root) — the R2 trap avoided.
3. Gateway container restarted once: startup complete, internal /health 200.
4. Nginx: the allow rule for the CRM container's address was already present in deploy/nginx.conf — the §3.9 nano edit HAD landed; it had simply never been loaded, and the reload attempts failed only because the gateway upstream was down at that moment. After gateway recovery: nginx -t clean, then nginx -s reload — the first successful reload of the night. The deploy/nginx.conf in this repo now mirrors production.

8.3 VERIFICATION (end-to-end, from the CRM container host)
- Authenticated POST /v1/chat/completions from CT107 using the CRM client's own key, model "auto" → HTTP 200, content "TWENTY-ROUTER-OK", served by groq/gpt-oss-120b (private-free lane).
- routes.jsonl: first client=crm row, cost $0.00. The soak report now carries a crm section (100% free, 0 failures).
- Production clients (hermes / n8n / openclaw) verified flowing after the restart.
- This completes checklist §5 items 2–6 and 8 (Drive progress doc §15, and this section). Remaining from §5: item 7 (see 8.4). Item 9 (runbook hardening) is future work; the R2/R3 lessons are recorded in §2 and in 8.2 above.

8.4 OUTSTANDING — ADMIN KEY ROTATION (owner decision)
The router admin key was pasted into a ChatGPT conversation and must be treated as exposed (log STATUS section, §5.7). Rotation plan: generate a new admin key on VM103, replace admin_key_sha256 in clients.json, update the n8n credential the alert chain uses for /admin polling, restart/reload the gateway, verify the alert chain still polls and the old key no longer authenticates. NOT executed with this repair — it touches the alert chain and needs the owner's explicit go.

8.5 NOT RESTORED, ON PURPOSE
The hermes-voice router client remains absent from clients.json. It was removed earlier on Nov 8 by the owner's deliberate rollback of the voice-router change (Hermes chat), unrelated to this incident.
