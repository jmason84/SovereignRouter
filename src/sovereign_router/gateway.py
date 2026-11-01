"""FastAPI gateway."""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .alerts import Alerter
from .engine import Engine, EngineError
from .governor import Governor, utc_day_start
from .policy import Clients
from .quota import Store
from .registry import Registry
from .sticky import StickyStore

VERSION = "0.5.3"


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def create_app(state_dir: str | None = None, http: httpx.AsyncClient | None = None, *, allow_http: bool = False,
               sidecar=None, clock=time.time) -> FastAPI:
    state = Path(state_dir or os.environ.get("SOVEREIGN_STATE_DIR", "/state"))
    allow_http = allow_http or os.environ.get("SOVEREIGN_ALLOW_HTTP") == "1"
    http = http or httpx.AsyncClient()
    store = Store(str(state / "quota.sqlite3"), clock=clock)
    alerter = Alerter(store, os.environ.get("SOVEREIGN_ALERT_WEBHOOK"), http,
                      mode=os.environ.get("SOVEREIGN_ALERT_MODE", "pull"), started=clock())
    backstop = os.environ.get("SOVEREIGN_GLOBAL_DAILY_BACKSTOP_USD")
    holder = {"registry": Registry.load(state / "registry.json", allow_http),
              "clients": Clients.load(state / "clients.json")}
    governor = Governor(store, alerter, registry=holder["registry"],
                        global_daily_backstop_usd=float(backstop) if backstop else None)
    sticky = StickyStore(clock)
    engine = Engine(holder["registry"], store, http, governor, sticky, sidecar=sidecar)
    app = FastAPI(title="Sovereign Router", version=VERSION)
    app.state.engine, app.state.store, app.state.alerter, app.state.governor = engine, store, alerter, governor

    def err(e: EngineError):
        body = {"error": {"message": e.message, "skipped": e.skips}}
        return JSONResponse(body, status_code=e.status, headers=e.headers)

    def auth(request: Request):
        return holder["clients"].authenticate(_bearer(request))

    @app.get("/health")
    async def health(strict: int = 0):
        chain = alerter.chain()
        body = {"status": "degraded" if chain["degraded"] else "ok", "version": VERSION,
                "models": len(holder["registry"].usable()), "alerts": alerter.health(),
                "backstop_usd": governor.backstop, "backstop_tripped": governor.paid_blocked_by_backstop()}
        # ?strict=1 returns 503 while the alert chain is degraded (for a container health check)
        return JSONResponse(body, status_code=503 if (strict and chain["degraded"]) else 200)

    @app.get("/v1/models")
    async def models(request: Request):
        if not auth(request):
            return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
        from .engine import ALIASES
        return {"object": "list", "data": [{"id": a, "object": "model"} for a in sorted(ALIASES)]}

    @app.get("/v1/quota")
    async def quota(request: Request):
        if not auth(request):
            return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
        return {m.id: {"lane": m.lane, "limits": m.limits, **store.window_usage(m.id)}
                for m in holder["registry"].usable()}

    @app.get("/v1/usage")
    async def usage(request: Request):
        p = auth(request)
        if not p:
            return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
        return {"client": p.name, "spend_today_usd": store.spend(utc_day_start(clock()), client=p.name),
                "daily_cost_target_usd": p.daily_cost_target_usd}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        policy = auth(request)
        if not policy:
            return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
        try:
            raw = await request.json()
            if not isinstance(raw, dict):
                raise ValueError
        except ValueError:
            return JSONResponse({"error": {"message": "Body must be a JSON object."}}, status_code=400)
        try:
            ctx = await engine.prepare(policy, raw, {k.lower(): v for k, v in request.headers.items()})
            out = await engine.execute(ctx)
        except EngineError as e:
            return err(e)
        if out.stream is not None:
            async def sse():
                async for payload in out.stream:
                    yield f"data: {payload}\n\n"
            return StreamingResponse(sse(), media_type="text/event-stream", headers=out.headers)
        return JSONResponse(out.json, headers=out.headers)

    def admin(request: Request):
        return holder["clients"].is_admin(_bearer(request))

    @app.post("/admin/reload")
    async def reload(request: Request):
        if not admin(request):
            return JSONResponse({"error": {"message": "Forbidden"}}, status_code=403)
        try:  # validate fully before swapping anything in
            reg, cl = Registry.load(state / "registry.json", allow_http), Clients.load(state / "clients.json")
        except Exception as e:
            return JSONResponse({"error": {"message": f"reload rejected: {e}"}}, status_code=400)
        holder["registry"], holder["clients"], engine.registry, governor.registry = reg, cl, reg, reg
        return {"reloaded": True, "models": len(reg.usable())}

    @app.get("/admin/routes")
    async def routes(request: Request):
        if not admin(request):
            return JSONResponse({"error": {"message": "Forbidden"}}, status_code=403)
        rows = store.breakdown(clock() - 86400)
        return [{"client": r[0], "provider": r[1], "model": r[2], "level": r[3], "requests": r[4],
                 "tokens": r[5], "cost": r[6]} for r in rows]

    @app.get("/admin/costs")
    async def costs(request: Request):
        if not admin(request):
            return JSONResponse({"error": {"message": "Forbidden"}}, status_code=403)
        day = utc_day_start(clock())
        by_client: dict = {}
        for r in store.breakdown(day):
            by_client[r[0]] = by_client.get(r[0], 0) + r[6]
        alerter.poll(10**18)  # a costs read is also a poll (heartbeat)
        return {"today_total_usd": store.spend(day), "by_client": by_client,
                "runaway_threshold_usd_per_hour": governor.runaway_threshold(),
                "warmup": governor.in_warmup(), "backstop_usd": governor.backstop,
                "backstop_tripped": governor.paid_blocked_by_backstop()}

    @app.post("/admin/killswitch")
    async def killswitch(request: Request):
        if not admin(request):
            return JSONResponse({"error": {"message": "Forbidden"}}, status_code=403)
        b = await request.json()
        store.set_kill(b["client"], bool(b.get("on", True)))
        return {"client": b["client"], "killed": store.is_killed(b["client"])}

    @app.post("/admin/daily-summary")
    async def summary(request: Request):
        if not admin(request):
            return JSONResponse({"error": {"message": "Forbidden"}}, status_code=403)
        return {"delivered": await governor.daily_summary()}

    # --- v0.5.3: pull alerts, summary, emergency switch, shadow mode ---------
    @app.get("/admin/alerts")
    async def alerts(request: Request, after: int = 0):
        """n8n poller: returns queued alerts with id > after and records the poll (heartbeat)."""
        if not admin(request):
            return JSONResponse({"error": {"message": "Forbidden"}}, status_code=403)
        items = alerter.poll(after)
        return {"alerts": items, "next_after": items[-1]["id"] if items else after,
                "alert_chain": alerter.chain()}

    @app.get("/admin/summary")
    async def summary_get(request: Request):
        """Daily all-clear payload for the n8n workflow (also counts as a poll)."""
        if not admin(request):
            return JSONResponse({"error": {"message": "Forbidden"}}, status_code=403)
        alerter.poll(10**18)
        return governor.summary()

    @app.post("/admin/backstop")
    async def backstop_set(request: Request):
        """{"usd": 25} sets the emergency switch level, {"usd": null} disables it,
        {"clear_today": true} re-enables paid lanes for the rest of the UTC day."""
        if not admin(request):
            return JSONResponse({"error": {"message": "Forbidden"}}, status_code=403)
        b = await request.json()
        if "usd" in b:
            if b["usd"] is not None and (not isinstance(b["usd"], (int, float)) or b["usd"] <= 0):
                return JSONResponse({"error": {"message": "usd must be a positive number or null"}}, status_code=400)
            governor.backstop = b["usd"]
        if b.get("clear_today"):
            governor.clear_backstop_today()
        return {"backstop_usd": governor.backstop, "tripped": governor.paid_blocked_by_backstop()}

    @app.post("/v1/shadow")
    async def shadow(request: Request):
        """Decision only: what v0.5 would choose and cost. No upstream call, no quota use."""
        policy = auth(request)
        if not policy:
            return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
        try:
            raw = await request.json()
            if not isinstance(raw, dict):
                raise ValueError
            ctx = await engine.prepare(policy, raw, {k.lower(): v for k, v in request.headers.items()})
        except ValueError:
            return JSONResponse({"error": {"message": "Body must be a JSON object."}}, status_code=400)
        except EngineError as e:
            return err(e)
        return engine.shadow(ctx)

    @app.get("/admin/shadow")
    async def shadow_report(request: Request, hours: float = 24):
        if not admin(request):
            return JSONResponse({"error": {"message": "Forbidden"}}, status_code=403)
        rows = store.shadow_breakdown(clock() - hours * 3600)
        return [{"client": r[0], "lane": r[1], "model": r[2], "level": r[3], "requests": r[4],
                 "est_cost": r[5]} for r in rows]

    return app
