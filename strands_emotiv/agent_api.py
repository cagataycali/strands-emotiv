"""POST /api/agent/ask backs the dashboard Chat panel. The agent is built
on first ask and reused. If the strands runtime or model is unavailable the
endpoint degrades to {"error": "agent offline", ...} and the Chat panel
shows that gracefully."""

from __future__ import annotations

import asyncio
import json
import time as _time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import tools as bt
from .agent import DEFAULT_MODEL, ask, build_agent, compose_turn, mark
from .bus import agent_bus

router = APIRouter()

_agent: Any | None = None
_agent_lock = asyncio.Lock()
_last_error: str | None = None  # last build/turn failure; status reports it
_history: list[dict[str, Any]] = []  # {"q","a","ambient","t"} session memory for the panel
_engine = bt.ENGINE  # the reflex arc to read events from; server rebinds to its own


def set_engine(engine: Any) -> None:
    """server.py calls this so events_during reads the engine actually fed."""
    global _engine
    _engine = engine


async def _get_agent() -> Any:
    global _agent
    async with _agent_lock:
        if _agent is None:
            _agent = build_agent()
        return _agent


@router.get("/api/agent/status")
async def agent_status() -> JSONResponse:
    return JSONResponse({
        "ready": _agent is not None and _last_error is None,
        "built": _agent is not None,
        "error": _last_error,
        "model": DEFAULT_MODEL,
        "tools": len(bt.BRAIN_TOOLS),
        "ambient": bt.ambient_line(),
        "turns": len(_history),
    })


@router.get("/api/agent/history")
async def agent_history(limit: int = 20) -> JSONResponse:
    return JSONResponse({"history": _history[-limit:]})


@router.post("/api/agent/ask")
async def agent_ask(req: Request) -> JSONResponse:
    body = await req.json()
    q = (body.get("message") or body.get("question") or "").strip()
    if not q:
        return JSONResponse({"error": "empty message"}, status_code=400)
    ambient = bt.ambient_line()  # capture what the agent saw this turn
    n_events = len(_engine.recent(10**9))
    agent_bus.publish({"type": "turn_start", "q": q, "ambient": ambient, "t": _time.time()})
    global _last_error
    try:
        agent = await _get_agent()
        answer = await ask(agent, q)
        _last_error = None
    except Exception as e:  # agent offline is a state, not a crash
        _last_error = f"{type(e).__name__}: {e}"
        agent_bus.publish({"type": "turn_end", "text": "", "t": _time.time()})
        return JSONResponse(
            {"error": "agent offline", "detail": f"{type(e).__name__}: {e}", "ambient": ambient},
            status_code=503,
        )
    agent_bus.publish({"type": "delta", "text": answer, "t": _time.time()})
    agent_bus.publish({"type": "turn_end", "text": answer, "t": _time.time()})
    events_during = [ev.as_dict() for ev in _engine.recent(10**9)[n_events:]]
    turn = {"q": q, "a": answer, "ambient": ambient, "events_during": events_during}
    _history.append(turn)
    del _history[:-200]
    return JSONResponse(turn)

def _sse(obj: dict[str, Any]) -> str:
    """One SSE frame. json.dumps never emits bare newlines, so one data: line."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/api/agent/stream")
async def agent_stream(req: Request) -> StreamingResponse:
    """SSE turn: ambient first, then deltas / tool starts / brain events live,
    then done with the full text. Event shapes:
      {"type":"ambient","line":…}   the bracketed line the agent sees
      {"type":"delta","text":…}     one token chunk
      {"type":"tool","name":…}      a tool just started
      {"type":"event","kind":…,"time":…}  brain event caught mid-turn
      {"type":"done","full":…,"events_during":[…]}
      {"type":"error","error":…}
    """
    body = await req.json()
    q = (body.get("message") or body.get("question") or "").strip()

    async def gen() -> AsyncIterator[str]:
        global _last_error
        if not q:
            yield _sse({"type": "error", "error": "empty message"})
            return
        ambient = bt.ambient_line()
        yield _sse({"type": "ambient", "line": ambient})
        agent_bus.publish({"type": "turn_start", "q": q, "ambient": ambient, "t": _time.time()})
        n0 = len(_engine.recent(10**9))
        seen_events = 0
        seen_tools: set[str] = set()
        full: list[str] = []
        try:
            agent = await _get_agent()
        except Exception as e:  # agent offline is a state
            _last_error = f"{type(e).__name__}: {e}"
            agent_bus.publish({"type": "turn_end", "text": "", "t": _time.time()})
            yield _sse({"type": "error", "error": "agent offline", "detail": _last_error})
            return
        await mark("agent_turn_start")
        agent_bus.publish({"type": "marker", "label": "agent_turn_start", "t": _time.time()})
        try:
            async for ev in agent.stream_async(compose_turn(q)):
                if not isinstance(ev, dict):
                    continue
                data = ev.get("data")
                if isinstance(data, str) and data:
                    full.append(data)
                    agent_bus.publish({"type": "delta", "text": "".join(full), "t": _time.time()})
                    yield _sse({"type": "delta", "text": data})
                tu = ev.get("current_tool_use")
                if isinstance(tu, dict):
                    name = tu.get("name")
                    if name and name not in seen_tools:
                        seen_tools.add(name)
                        agent_bus.publish({"type": "tool", "tool": name, "t": _time.time()})
                        yield _sse({"type": "tool", "name": name})
                # brain events that landed while the agent was thinking
                now_events = _engine.recent(10**9)[n0:]
                for bev in now_events[seen_events:]:
                    d = bev.as_dict()
                    yield _sse({"type": "event", **d})
                seen_events = len(now_events)
        except Exception as e:
            _last_error = f"{type(e).__name__}: {e}"
            agent_bus.publish({"type": "turn_end", "text": "".join(full), "t": _time.time()})
            yield _sse({"type": "error", "error": "agent turn failed", "detail": _last_error})
            return
        _last_error = None
        text = "".join(full)
        await mark("agent_turn_end", min(len(text), 999))
        agent_bus.publish({"type": "turn_end", "text": text, "tokens": len(text.split()), "t": _time.time()})
        events_during = [ev.as_dict() for ev in _engine.recent(10**9)[n0:]]
        turn = {"q": q, "a": text, "ambient": ambient, "events_during": events_during}
        _history.append(turn)
        del _history[:-200]
        yield _sse({"type": "done", "full": text, "events_during": events_during})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
