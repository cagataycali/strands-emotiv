"""FastAPI relay. Serves the dashboard and fans Cortex state out over WS.

    uvicorn strands_emotiv.server:app --port 8765

WS message types:
  {"type":"state",  "state":{...}}          ~2 Hz snapshot (includes .motion)
  {"type":"motion", "q":[Q0,Q1,Q2,Q3], "t"} fast path, every mot frame (<=32 Hz,
                                            never gated by the state cadence)
  {"type":"event",  "kind","t","meta"}      derived events (events.py)
  {"type":"marker", "label","value","t"}    every inject_marker

Static files come from dashboard/frontend/dist/ (built Vite app) when it
exists, else dashboard/static/, mounted html=True after the API routes.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .cortex import CortexClient, CortexError
from .mental import MentalTrainer

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dashboard" / "frontend" / "dist"  # repo checkout: fresh `npm run build`
PACKAGED = Path(__file__).resolve().parent / "_dashboard"  # pip install: bundled in the wheel
STATIC = ROOT / "dashboard" / "static"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await _startup()  # defined below; resolved at call time
    yield


app = FastAPI(title="strands-emotiv", lifespan=_lifespan)
app.include_router(auth.router)


@app.middleware("http")
async def _passkey_gate(request: Request, call_next):
    """The public door (brain.cagatay.my) takes a passkey: this is a live EEG
    stream. Static shell + /api/auth/* stay open so the login UI can render;
    /api/health answers redacted. Local requests bypass (see auth.is_local)."""
    p = request.url.path
    if p.startswith("/api/") and not p.startswith("/api/auth/"):
        if p == "/api/health":
            if auth.auth_enabled() and not auth.is_authed(request):
                return JSONResponse({"ok": True, "auth": "required"})
        else:
            denied = auth.guard(request)
            if denied is not None:
                return denied
    return await call_next(request)
# com = mental commands, mot = head pose fast path
if os.environ.get("EMOTIV_FAKE"):
    from .fake import FakeRelayClient

    client: CortexClient = FakeRelayClient(
        streams=["met", "pow", "fac", "com", "mot", "dev", "eq"])
else:
    client = CortexClient(streams=["met", "pow", "fac", "com", "mot", "dev", "eq"])
trainer = MentalTrainer(client)
_clients: set[WebSocket] = set()

# fast motion path: latest frame + wake flag (coalesces if a client is slow)
_motion_latest: str | None = None
_motion_wake: asyncio.Event | None = None

# events.py is optional; the server must run without it
try:
    from .events import EventEngine
    _events: EventEngine | None = EventEngine()
except Exception:
    _events = None

# agent chat rail (POST /api/agent/ask), also optional
try:
    from . import agent_api as _agent_api
    app.include_router(_agent_api.router)
    if _events is not None:
        _agent_api.set_engine(_events)
    from . import tools as _bt_setup

    _bt_setup.set_source(client)  # agent markers stamp THIS session
except Exception:  # no agent module: Chat shows "agent offline"
    pass

# dataset recorder routes, optional for the same reason
try:
    from . import dataset_api as _dataset_api
    app.include_router(_dataset_api.router)
except Exception:
    pass


async def _send_all(payload: str) -> None:
    dead = set()
    for ws in _clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


_tasks: set[asyncio.Task] = set()  # strong refs; a dangling task can be GC-cancelled


def _keep(coro) -> None:
    """ensure_future + keep a strong reference until the task finishes."""
    t = asyncio.ensure_future(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _fanout(payload: str) -> None:
    """Schedule a broadcast from sync listener context (reader coroutine)."""
    if _clients:
        _keep(_send_all(payload))


def _on_sample(sample) -> None:
    """Sync Sample listener; runs inside the Cortex reader coroutine."""
    global _motion_latest
    if sample.stream == "mot":
        d = sample.data
        try:
            q = [d["Q0"], d["Q1"], d["Q2"], d["Q3"]]
        except (KeyError, TypeError):
            return
        _motion_latest = json.dumps({"type": "motion", "q": q, "t": sample.time})
        if _motion_wake is not None:
            _motion_wake.set()
    if _events is not None:
        try:
            _events.feed(sample)
        except Exception:  # event handling must not kill the reader
            pass
    # feed tools.STATE only, not tools.ENGINE: _events above is already the one
    # EventEngine, a second would double-fire
    try:
        from . import tools as _bt

        _bt.STATE.feed(sample)
    except Exception:
        pass


def _on_event(ev) -> None:
    _fanout(json.dumps({"type": "event", "kind": ev.kind, "t": ev.time,
                        "meta": ev.detail}))


def _on_legacy(stream: str, data: dict) -> None:
    if stream == "marker":
        _fanout(json.dumps({"type": "marker", "label": data.get("label"),
                            "value": data.get("value"), "t": data.get("time")}))


client._sample_listeners.append(_on_sample)
client.listeners.append(_on_legacy)
if _events is not None:
    _events.subscribe("*", _on_event)


async def _startup():
    global _motion_wake
    _motion_wake = asyncio.Event()

    async def runner():
        while True:
            try:
                await client.run()
                return
            except Exception as e:
                client.state["error"] = str(e)
                await asyncio.sleep(5)

    async def _arm_profile():
        """Reload the training profile after every (re)connect: a relay
        restart must not leave mental commands unrecognized."""
        name = os.environ.get("EMOTIV_PROFILE", "cagatay")
        while True:
            if client.state.get("connected") and \
                    client.state.get("mental_profile") != name:
                try:
                    await trainer.ensure_profile(name)
                except Exception as e:  # retry next tick
                    client.state["mental_error"] = str(e)
            await asyncio.sleep(5)

    for c in (runner(), _arm_profile(), _state_broadcaster(), _motion_broadcaster()):
        _keep(c)


def _ambient_now() -> str | None:
    """The authoritative line from tools.ambient_line, None if not computable.
    The dashboard prefers this over its client-side derivation, so the panel
    shows literally what the agent reads."""
    try:
        from . import tools as _bt

        return _bt.ambient_line()
    except Exception:
        return None


async def _state_broadcaster():
    while True:
        await asyncio.sleep(0.5)
        if _clients:
            await _send_all(json.dumps(
                {"type": "state", "state": {**client.state, "ambient": _ambient_now()}}))


async def _motion_broadcaster():
    """Push every mot frame the moment it lands, independent of the 2 Hz
    state cadence. Coalesces to latest-frame under backpressure, so the skull
    gets >=30 Hz whenever the headset delivers it (mot is 32 Hz)."""
    while True:
        await _motion_wake.wait()
        _motion_wake.clear()
        if _motion_latest is not None and _clients:
            await _send_all(_motion_latest)


@app.get("/api/health")
async def health():
    return {"ok": True, "connected": client.state["connected"],
            "headset": client.state["headset"]}


@app.get("/api/state")
async def state():
    return JSONResponse({**client.state, "ambient": _ambient_now()})


@app.get("/api/history")
async def history(limit: int = 600):
    return JSONResponse(list(client.history)[-limit:])


# mental commands

def _mental_error(e: Exception):
    code = 503 if isinstance(e, (CortexError, TimeoutError)) else 500
    return JSONResponse({"ok": False, "error": str(e)}, status_code=code)


@app.get("/api/mental/status")
async def mental_status():
    try:
        return JSONResponse(await trainer.status())
    except Exception as e:
        return _mental_error(e)


@app.post("/api/mental/profile")
async def mental_profile(req: Request):
    body = await req.json() if int(req.headers.get("content-length") or 0) else {}
    try:
        return JSONResponse(await trainer.ensure_profile(body.get("name")))
    except Exception as e:
        return _mental_error(e)


@app.post("/api/mental/train")
async def mental_train(req: Request):
    """One 8 s training round: {\"action\": \"neutral\"|\"push\"|\"pull\"}."""
    body = await req.json()
    try:
        return JSONResponse(await trainer.train_round(
            body["action"], accept=body.get("accept", True)))
    except Exception as e:
        return _mental_error(e)


@app.post("/api/mental/erase")
async def mental_erase(req: Request):
    body = await req.json()
    try:
        return JSONResponse(await trainer.erase(body["action"]))
    except Exception as e:
        return _mental_error(e)


@app.post("/api/mental/active")
async def mental_active(req: Request):
    body = await req.json()
    try:
        return JSONResponse(await trainer.set_active(body["actions"]))
    except Exception as e:
        return _mental_error(e)


@app.post("/api/mental/approval")
async def mental_approval(req: Request):
    """PUSH the box = yes · PULL = no · clench = veto. Blocks until decided.
    {\"prompt\": str, \"timeout\"?: s, \"threshold\"?: 0-1, \"hold\"?: n}"""
    body = await req.json()
    try:
        return JSONResponse(await trainer.await_decision(
            body.get("prompt", "approve?"),
            timeout=float(body.get("timeout", 45)),
            threshold=float(body.get("threshold", 0.25)),
            hold=int(body.get("hold", 3))))
    except Exception as e:
        return _mental_error(e)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    # same door as HTTP: live EEG only flows to local or passkey'd clients
    if auth.auth_enabled():
        local = getattr(ws.client, "host", None) in ("127.0.0.1", "::1") and \
            not any(h in ws.headers for h in ("cf-ray", "x-forwarded-for"))
        if not local and not auth.session_token_ok(
                (ws.cookies or {}).get(auth.COOKIE, "")):
            await ws.close(code=4401)
            return
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _clients.discard(ws)


# static LAST so /api/* and /ws win. dist (built frontend) beats static.
_static_dir = next((d for d in (DIST, PACKAGED, STATIC) if (d / "index.html").exists()), STATIC)
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="dashboard")
