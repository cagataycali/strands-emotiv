"""Strands @tool surface over the live brain state.

A module-level BrainState + EventEngine pair is fed by a background stream
task (`start_stream`), and the tools read from it. Every reading carries its
contact quality.

    from strands import Agent
    from strands_emotiv import tools as bt

    await bt.start_stream()          # CortexClient if importable+live, else FakeCortex
    agent = Agent(tools=bt.BRAIN_TOOLS)
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import Any

from strands import tool

from .events import EventEngine
from .state import BrainState
from .types import EPOCX_CHANNELS, Sample

# singletons
STATE = BrainState()
ENGINE = EventEngine()
_stream_task: asyncio.Task | None = None
_source_name: str = "none"
_source_obj: Any | None = None


def get_source() -> Any | None:
    """The live stream source (CortexClient when real), for inject_marker etc."""
    return _source_obj


def set_source(obj: Any) -> None:
    """A host that owns its own client (server.py) registers it here so
    agent markers land on the record it is streaming."""
    global _source_obj
    _source_obj = obj


CQ_USABLE = 0.5  # overall contact quality below this → "I can't see your brain"


def feed(sample: Sample) -> None:
    """Pump one sample into the shared state + reflex engine (server calls this too)."""
    STATE.feed(sample)
    ENGINE.feed(sample)


async def start_stream(source: Any | None = None) -> str:
    """Start the background feed. source = anything with `async def samples()`.

    Default: CortexClient if it imports and connects, else FakeCortex
    replaying the live fixture. Returns the source name actually used.
    """
    global _stream_task, _source_name, _source_obj
    if _stream_task is not None and not _stream_task.done():
        return _source_name
    if source is None:
        try:  # lazy import; cortex may be unavailable
            from .cortex import CortexClient  # type: ignore

            client = CortexClient()
            await client.connect()
            source = client
            _source_name = "cortex"
        except Exception:
            from .fake import FakeCortex

            source = FakeCortex(realtime=True)
            _source_name = "fake"
    else:
        _source_name = type(source).__name__
    _source_obj = source

    async def _pump() -> None:
        async for s in source.samples():
            feed(s)

    _stream_task = asyncio.get_running_loop().create_task(_pump())
    return _source_name


async def stop_stream() -> None:
    global _stream_task
    if _stream_task is not None:
        _stream_task.cancel()
        try:
            await _stream_task
        except (asyncio.CancelledError, Exception):
            pass
        _stream_task = None


# helpers
def _cq_summary() -> dict[str, Any]:
    cq = STATE.contact_quality()
    if not cq:
        return {"usable": False, "reason": "no dev stream yet", "good": 0, "total": 14}
    ch = cq.get("channels", {})
    good = sum(1 for v in ch.values() if v >= 3)
    overall = cq.get("overall", 0.0)
    out = {"usable": overall >= CQ_USABLE, "overall": overall, "good": good, "total": len(ch) or 14}
    if "battery" in cq:
        out["battery"] = cq["battery"]
    return out


def _bands() -> dict[str, dict[str, float]]:
    """FFT bands if we have raw eeg (paid license); else the pow stream
    (band power computed by Cortex itself, what the Basic license gives us)."""
    fft = STATE.band_power()
    if fft:
        return fft
    latest = STATE.snapshot()["streams"].get("pow")
    if not latest:
        return {}
    out: dict[str, dict[str, float]] = {}
    for key, val in latest["data"].items():
        if "/" not in key:
            continue
        ch, band = key.split("/", 1)
        try:
            out.setdefault(ch, {})[band] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def ambient_line() -> str:
    """The one context line the agent sees each turn.

    `[brain: focus 0.71↑ · stress 0.22↓ · alpha dominant O1/O2 · still 38s · CQ 13/14 good]`
    """
    cq = _cq_summary()
    if not cq["usable"]:
        return "[brain: not visible: " + cq.get("reason", "contact quality too low") + "]"
    parts: list[str] = []
    met = STATE.snapshot()["streams"].get("met")
    if met:
        d = met["data"]
        foc_key = "foc" if d.get("foc") is not None else "attention"
        foc = d.get(foc_key)
        if foc is not None:
            parts.append(f"focus {float(foc):.2f}{STATE.metric_trend(foc_key)}")
        if d.get("str") is not None:
            parts.append(f"stress {float(d['str']):.2f}{STATE.metric_trend('str')}")
        if d.get("eng") is not None:
            parts.append(f"engagement {float(d['eng']):.2f}{STATE.metric_trend('eng')}")
    bands = _bands()
    if bands:
        totals: dict[str, float] = {}
        for ch_bands in bands.values():
            for b, v in ch_bands.items():
                totals[b] = totals.get(b, 0.0) + v
        dom = max(totals, key=lambda b: totals[b])
        occ = [ch for ch in ("O1", "O2") if ch in bands]
        parts.append(f"{dom} dominant" + (f" {'/'.join(occ)}" if dom == "alpha" and occ else ""))
    still = STATE.stillness_s()
    if still is not None and still >= 10.0:
        parts.append(f"still {int(still)}s")
    ev = ENGINE.recent(1)
    if ev:
        parts.append(f"last event {ev[0].kind}")
    parts.append(f"CQ {cq['good']}/{cq['total']}" + (" good" if cq["good"] >= cq["total"] - 1 else ""))
    return "[brain: " + " · ".join(parts) + "]"


# tools
@tool
def brain_snapshot() -> dict[str, Any]:
    """Full current brain state: per-stream freshness, head pose, contact
    quality, band powers and the ambient context line. The widest view."""
    snap = STATE.snapshot()
    return {
        "ambient": ambient_line(),
        "contact_quality": _cq_summary(),
        "pose": snap["pose"],
        "streams": {
            k: {"age_s": round(_time.time() - v["time"], 1) if v["time"] > 1e9 else None,
                "count": v["count"]}
            for k, v in snap["streams"].items()
        },
        "source": _source_name,
    }


@tool
def brain_bands(channel: str | None = None) -> dict[str, Any]:
    """Band power (theta/alpha/betaL/betaH/gamma) per EEG channel.

    Args:
        channel: One EPOC X channel (e.g. 'O1'); omit for all 14.
    """
    bands = _bands()
    if not bands:
        return {"error": "no band data yet; is the stream started?", "contact_quality": _cq_summary()}
    if channel is not None:
        ch = channel.upper()
        if ch not in bands:
            return {"error": f"unknown channel {channel!r}", "channels": sorted(bands)}
        return {"channel": ch, "bands": bands[ch], "contact_quality": _cq_summary()}
    return {"bands": bands, "contact_quality": _cq_summary()}


@tool
def head_pose() -> dict[str, Any]:
    """Current head orientation: yaw/pitch/roll in degrees plus the change
    over the last half second (deliberate motion shows up in the deltas)."""
    pose = STATE.head_pose()
    if pose is None:
        return {"error": "no motion data yet"}
    return {**pose, "contact_quality": _cq_summary()}


@tool
def contact_quality() -> dict[str, Any]:
    """Per-electrode contact quality (0=no contact … 4=perfect), battery and
    overall usability."""
    cq = STATE.contact_quality()
    if cq is None:
        return {"error": "no dev stream yet"}
    return {**cq, "summary": _cq_summary()}


@tool
def wait_for_brain_event(kind: str, timeout_s: float = 15.0) -> dict[str, Any]:
    """Block until a Layer-1 event fires (blink, double_blink, wink_left,
    wink_right, clench, head_turn_left, head_turn_right, nod, look_up,
    look_down, focus_high, focus_low, stress_high, command:<act>, or '*').

    Args:
        kind: Event kind, 'command:*' for any mental command, '*' for any.
        timeout_s: How long to wait before giving up.
    """
    seen = len(ENGINE.recent(10**9))
    deadline = _time.monotonic() + timeout_s
    while _time.monotonic() < deadline:
        recent = ENGINE.recent(10**9)
        for ev in recent[seen:]:
            if EventEngine._match(kind, ev.kind):
                return {"event": ev.as_dict()}
        seen = len(recent)
        _time.sleep(0.02)
    return {"timeout": True, "waited_s": timeout_s, "kind": kind}


@tool
def recent_brain_events(limit: int = 10) -> dict[str, Any]:
    """The last N Layer-1 events (blinks, turns, nods, focus shifts…) with
    sample timestamps, newest last.

    Args:
        limit: Max events to return.
    """
    return {"events": [ev.as_dict() for ev in ENGINE.recent(limit)], "ambient": ambient_line()}


@tool
def brain_line() -> str:
    """The one line the agent reads every turn. Focus, stress, dominant band
    and where, stillness, contact quality. Missing values print as missing,
    never as zero. Prepend it to a prompt and any agent can feel the person."""
    return ambient_line()


@tool
def brain_status() -> dict[str, Any]:
    """Is the headset actually on and readable right now? Source, stream
    counts, contact quality, uptime. If this says not usable, say
    'I can't see your brain right now' and move on."""
    snap = STATE.snapshot()
    cq = _cq_summary()
    return {
        "source": _source_name,
        "streaming": _stream_task is not None and not _stream_task.done(),
        "usable": cq["usable"],
        "contact_quality": cq,
        "streams_seen": {k: v["count"] for k, v in snap["streams"].items()},
        "uptime_s": snap["uptime_s"],
        "channels": list(EPOCX_CHANNELS),
    }


@tool
def mental_approval(question: str, timeout_s: int = 45) -> dict[str, Any]:
    """Ask cagatay for HANDS-FREE CONSENT over the EEG headset. He answers
    with a trained mental command: PUSHING the imaginary box means YES,
    PULLING it means NO. A jaw clench VETOES the question; silence times out.
    Use before any irreversible action when his hands are busy.

    Args:
        question: What you are asking approval for, shown on the dashboard.
        timeout_s: How long to wait for a decision before giving up.

    Returns dict with decision: 'yes' | 'no' | 'vetoed' | 'timeout' |
    'refused' (contact quality too poor to trust).
    """
    res = _relay("/api/mental/approval",
                 {"prompt": question, "timeout": timeout_s},
                 timeout=timeout_s + 15)
    res.setdefault("decision", "unavailable")  # relay down is a state, not a crash
    return res


def _relay(path: str, payload: dict | None = None,
           timeout: float = 30.0) -> dict[str, Any]:
    """POST (or GET when payload is None) against the local relay, the same
    rail mental_approval uses. Local requests bypass the passkey gate."""
    import json as _json
    import os as _os
    import urllib.request as _rq

    base = _os.environ.get("EMOTIV_RELAY", "http://127.0.0.1:8765")
    if payload is None:
        req = _rq.Request(f"{base}{path}")
    else:
        req = _rq.Request(f"{base}{path}", data=_json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"},
                          method="POST")
    try:
        with _rq.urlopen(req, timeout=timeout) as r:
            return _json.loads(r.read())
    except Exception as e:  # relay down is a state, not a crash
        return {"ok": False, "error": f"relay not reachable: {e}",
                "hint": "start it: uv run uvicorn strands_emotiv.server:app "
                        "--port 8765"}


@tool
def record_start(name: str | None = None) -> dict[str, Any]:
    """Start recording the ECoT dataset: every brain frame (fps=8) plus the
    conversation as episode boundaries and task strings (lerobot v3.0).
    SAY that recording started; the person being recorded must know. The
    dashboard REC panel lights up as the second witness.

    Args:
        name: Dataset session name; omit for a timestamped default.
    """
    return _relay("/api/dataset/record/start",
                  {"name": name} if name else {})


@tool
def record_stop() -> dict[str, Any]:
    """Stop the running ECoT recording and close the episode cleanly.
    Returns final counts (episodes, frames, bytes, root)."""
    return _relay("/api/dataset/record/stop", {})


@tool
def record_status() -> dict[str, Any]:
    """Whether a dataset recording is running right now, and its live
    counters (episodes, frames, bytes, name, root)."""
    return _relay("/api/dataset/status")


@tool
def record_publish(name: str | None = None) -> dict[str, Any]:
    """Upload the recorded ECoT dataset to the Hugging Face Hub
    (cagataydev/emotiv-ecot, private). This ships brain data OFF this
    machine; ask first (words or mental_approval) unless cagatay already
    told you to publish.

    Args:
        name: Which recorded session to publish; omit for the current one.
    """
    return _relay("/api/dataset/publish",
                  {"name": name} if name else {}, timeout=300.0)


BRAIN_TOOLS = [
    brain_line,
    brain_snapshot,
    brain_bands,
    head_pose,
    contact_quality,
    wait_for_brain_event,
    recent_brain_events,
    brain_status,
    mental_approval,
    record_start,
    record_stop,
    record_status,
    record_publish,
]
