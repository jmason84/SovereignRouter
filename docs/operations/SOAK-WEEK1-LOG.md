# Sovereign Router — Week 1 Soak Log (2026-11-02 → 2026-11-09)

The daily soak record, committed 2026-11-11. Detailed entries survive for Day 1, Day 2 and Day 8; the intervening days ran within the same reporting and are summarized in `docs/OPERATIONS-REVIEW-2026-11-11.md`. Raw runtime logs (routes.jsonl, quota.db) are never committed — they live on VM103 only.

# Soak Day 1 — report run 2026-11-03 ~08:49 MST, covering 2026-11-02 (partial/deploy day)

Source commands (VM103 via ssh -F /home/hatch/.ssh/config vm103):
- python3 /root/sovereign-router-v05/tools/soak_report.py 2026-11-02
- docker ps --format "{{.Names}} {{.Status}}" | grep -E "v05|sovereign-router-v1 |v03-canary"
- curl http://127.0.0.1:6070/health (prod v0.2.1)

Results:
- ALL: 16 reqs, $0.0000, free share 81%, escalations 3 (18.8%, target <8% — BREACH), failed 0 (0.0%, target <1%), p50 46ms / p95 76ms (targets <300 / <1500), premium 0, level mix all L1.
- hermes: 6 reqs, $0 vs $1.00 target, free 83%, esc 1 (16.7%), p50 47ms / p95 65ms.
- n8n: 4 reqs, $0 vs $0.50 target, free 75%, esc 1 (25.0%), p50 43ms / p95 51ms.
- openclaw: 6 reqs, $0 vs $3.00 target, free 83%, esc 1 (16.7%), p50 46ms / p95 76ms.
- Note: openclaw/n8n rows on v0.5 despite neither having cut over — consistent with deploy-day test traffic; escalation % is on n=16, so treat the breach as a watch item, not a production verdict.
- Stack: v05 gateway/classifier/nginx Up (healthy), v03-canary Up (healthy), sovereign-router-v1 Up (healthy).
- Prod :6070 /health: ok, version 0.2.1.
- v0.5 :6075 from VM103: nginx 403 (allowlist — VM103 not allowlisted) / localhost connection refused (only 192.168.0.166:6075 published). Expected, not an outage; containers healthy.
- No configuration changed.

---


## 2026-11-04 run — Day 2 (covering 2026-11-03)
- ALL: 729 reqs, $0.4586, free 72%, esc 13.3% BREACH, fail 1.2% BREACH, p50 42ms/p95 51ms, premium 25.
- Hermes: 697 reqs, $0.0975 vs $1.25 target, free 75%, esc 13.9% BREACH, fail 0.9%, p50 42/p95 51, premium 0.
- OpenClaw: 32 reqs, $0.3611 vs $5.00 target, free 22%, esc 0%, fail 9.4% BREACH (3/32), p50 0/p95 1, premium 25.
- Health: v05 stack + v03-canary + v1 up; prod :6070 healthy. Killswitch empty; no budget-refusal alerts.

## 2026-11-10 run — Day 8 (covering 2026-11-09, FINAL soak day)
- ALL: 1,968 reqs, $28.2301, free 59%, esc 17.8% BREACH, fail 3.8% BREACH (75x 429, 11:00–12:55 MST, emergency_paid_lane_off 438x = budget refusals; switch cleared Nov 9, zero fails after 12:55), p50 1ms/p95 65ms, premium 308 ($24.58), spillway 342 ($24.19).
- Hermes: 755 reqs, $12.3219 vs $1.25 target OVER ~10x, free 55%, esc 18.0% BREACH, fail 1.2% BREACH (9), p50 1/p95 50, premium 102 ($10.13).
- OpenClaw: 914 reqs, $15.7516 vs $5.00 OVER ~3x, free 64%, esc 23.2% BREACH, fail 7.1% BREACH (65), p50 1/p95 45, premium 205 ($14.44).
- n8n: 292 reqs, $0.1511 vs $2.50 within, free 56%, esc 1.0%, fail 0.3% (1), p50 2/p95 95, premium 1. All targets met.
- crm: 7 reqs, $0.0056, free 71%, esc 0%, fail 0%, p50 1/p95 68, premium 0. All targets met.
- Cost drivers: ollama/gpt-oss-120b + deepinfra/deepseek-v4-flash lanes dead Nov 9 (upstream 402s); spillway fell through to deepseek-v4-pro ($19.54). L4+L5 64% overall. Slow-leak alert: openclaw paid $0.11 → $5.45 → $15.53 over 3 days. DeepInfra MTD $10.62 > $10 alert.
- Health: all 5 containers Up (healthy); prod :6070 ok v0.2.1; :6075 serving (nginx binds LAN IP only; 403 for non-allowlisted source is expected allowlist behavior); traffic flowing Nov 10 AM. Verdict: FAIL — cost, escalation, failure, budget-refusal targets all breached on final day. Soak window Nov 2–9 closed; Week 2 gate ready for Jordan's review. Open: openclaw traffic (914 reqs) already flowing through v0.5 — confirm at gate whether move happened early. Cron reports window-closed from Nov 11 run unless removed at gate.

---

## Post-window reference — 2026-11-10 (first full day on the fixed v0.5.7 stack)

- ALL: 1,638 reqs, $3.16, free 61%, escalations 1.3%, failures 0%, p50 1ms / p95 73ms, premium 0, budget refusals 0.
- Hermes $2.35 vs $1.25 target OVER; OpenClaw $0.79 within $5.00; n8n $0.01 within $2.50.
- Stack healthy; prod :6070 answering. (From the 2026-11-11 closure run of the soak check.)
