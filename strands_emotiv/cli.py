"""strands-emotiv CLI: doctor, dashboard, agent, capture.

    strands-emotiv doctor              ground truth: Cortex, headset, streams
    strands-emotiv dashboard           uvicorn strands_emotiv.server:app :8765
    strands-emotiv agent               brain-aware REPL (live if possible)
    strands-emotiv capture -s 10 -o f  record live samples to jsonl
    strands-emotiv record ambient      cut baseline episodes via the relay
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="strands-emotiv", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="print the ground-truth table (Cortex, headset, streams)")
    d.add_argument("--json", action="store_true", help="machine-readable output")
    dash = sub.add_parser("dashboard", help="serve the dashboard on :8765")
    dash.add_argument("--port", type=int, default=8765)
    dash.add_argument("--host", default="127.0.0.1")
    dash.add_argument("--fake", action="store_true",
                      help="replay the packaged fixture (no headset, no Cortex)")
    a = sub.add_parser("agent", help="brain-aware agent REPL")
    a.add_argument("--model", default=None)
    a.add_argument("--fake", action="store_true", help="force FakeCortex (no headset needed)")
    m = sub.add_parser("mcp", help="expose the brain tools over MCP (stdio, or --http PORT)")
    m.add_argument("--http", type=int, default=None, help="serve streamable HTTP on this port instead of stdio")
    m.add_argument("--fake", action="store_true", help="force FakeCortex (no headset needed)")
    m.add_argument("--no-agent", action="store_true", help="tools only, no invoke_agent")
    m.add_argument("--model", default=None)
    c = sub.add_parser("capture", help="record live samples to jsonl")
    c.add_argument("-s", "--seconds", type=float, default=10.0)
    c.add_argument("-o", "--out", default="capture.jsonl")
    r = sub.add_parser("record", help="record dataset episodes via a running relay")
    r.add_argument("kind", choices=["ambient"], help="ambient: fixed 30s baseline episodes")
    r.add_argument("--minutes", type=float, default=10.0)
    r.add_argument("--name", default=None, help="dataset directory name (default: timestamped)")
    r.add_argument("--relay", default="http://127.0.0.1:8765")
    return p


# record
def _record(kind: str, minutes: float, name: str | None, relay: str) -> int:
    """Baseline episodes through the relay's recorder: start, sit, stop.
    Needs `strands-emotiv dashboard` already running (409 = already recording)."""
    import time
    import urllib.error
    import urllib.request

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(f"{relay}{path}", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    try:
        st = post("/api/dataset/record/start",
                  {"ambient": kind == "ambient", **({"name": name} if name else {})})
    except urllib.error.HTTPError as e:
        print(f"relay refused: {e.read().decode()[:200]}", file=sys.stderr)
        return 1
    except urllib.error.URLError:
        print(f"no relay at {relay}: run `strands-emotiv dashboard` first", file=sys.stderr)
        return 1
    print(f"recording {kind} -> {st.get('root')} ({minutes:g} min, ^C stops early)")
    try:
        time.sleep(minutes * 60)
    except KeyboardInterrupt:
        print("stopping early")
    st = post("/api/dataset/record/stop", {})
    print(f"done: {st.get('episodes', '?')} episodes in {st.get('name', '?')}")
    return 0


# doctor
async def _doctor() -> dict[str, Any]:
    """Measure, don't assume: is Cortex up, who's connected, what subscribes."""
    from .cortex import CortexClient

    out: dict[str, Any] = {"cortex": False, "headset": None, "streams": {}, "eeg_license": False}
    client = CortexClient()
    try:
        await client.connect()
        out["cortex"] = True
        hs = await client.wait_ready(timeout=8)
        out["headset"] = hs
        await client.create_session()
        res = await client.subscribe()
        for s in res.get("success", []):
            out["streams"][s["streamName"]] = "ok"
        for f in res.get("failure", []):
            out["streams"][f.get("streamName", "?")] = f.get("message", "failed")
        out["eeg_license"] = out["streams"].get("eeg") == "ok"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return out


def cmd_doctor(as_json: bool = False) -> int:
    out = asyncio.run(_doctor())
    if as_json:
        print(json.dumps(out, indent=2, default=str))
        return 0 if out["cortex"] else 1
    def ok(b: bool) -> str:
        return "✅" if b else "❌"
    print(f"{ok(out['cortex'])} Cortex wss://localhost:6868")
    h = out.get("headset")
    if isinstance(h, dict):
        print(f"✅ headset {h.get('id')} via {h.get('connectedBy')} "
              f"({len(h.get('sensors') or [])} sensors)")
    else:
        print("❌ headset: power it on or plug the dongle (a USB cable only charges)")
    for name, status in out.get("streams", {}).items():
        print(f"{ok(status == 'ok')} stream {name}: {status}")
    if out.get("streams") and not out["eeg_license"]:
        print("ℹ️  raw eeg needs a paid license; pow (band power) covers the dashboard")
    if "error" in out:
        print(f"⚠️  {out['error']}")
    return 0 if out["cortex"] else 1


# dashboard
def cmd_dashboard(host: str, port: int, fake: bool = False) -> int:
    import os

    import uvicorn

    if fake:
        os.environ["EMOTIV_FAKE"] = "1"
    uvicorn.run("strands_emotiv.server:app", host=host, port=port)
    return 0


# agent
async def _agent_repl(model: str | None, fake: bool) -> int:
    from . import tools as bt
    from .agent import ask, build_agent

    source = None
    if fake:
        from .fake import FakeCortex

        source = FakeCortex(realtime=True)
    name = await bt.start_stream(source)
    print(f"🧠 stream source: {name}")
    await asyncio.sleep(1.0)  # let first samples land
    agent = build_agent(model=model)
    print(bt.ambient_line())
    print("type 'exit' to leave\n")
    while True:
        try:
            q = await asyncio.to_thread(input, "you> ")
        except (EOFError, KeyboardInterrupt):
            break
        if q.strip().lower() in ("exit", "quit", "q"):
            break
        if not q.strip():
            continue
        print(await ask(agent, q))
        print(f"\n{bt.ambient_line()}")
    await bt.stop_stream()
    return 0


# capture
async def _capture(seconds: float, out: str) -> int:
    from .cortex import CortexClient

    client = CortexClient()
    await client.connect()
    await client.wait_ready()
    await client.create_session()
    await client.subscribe()
    n = 0
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    with open(out, "w") as f:
        async for s in client.samples():
            f.write(json.dumps({"stream": s.stream, "time": s.time, "data": s.data}) + "\n")
            n += 1
            if loop.time() >= end:
                break
    await client.close()
    print(f"captured {n} samples → {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from .cortex import CortexError

    try:
        if args.cmd == "doctor":
            return cmd_doctor(as_json=args.json)
        if args.cmd == "dashboard":
            return cmd_dashboard(args.host, args.port, args.fake)
        if args.cmd == "agent":
            return asyncio.run(_agent_repl(args.model, args.fake))
        if args.cmd == "mcp":
            from .mcp import serve

            return serve(fake=args.fake, http=args.http, expose_agent=not args.no_agent,
                         model=args.model)
        if args.cmd == "capture":
            return asyncio.run(_capture(args.seconds, args.out))
        if args.cmd == "record":
            return _record(args.kind, args.minutes, args.name, args.relay)
    except CortexError as e:
        print(e.message, file=sys.stderr)
        return 1
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
