"""Asyncio JSON-RPC client for the Emotiv Cortex API (wss://localhost:6868).

Implements the documented flow (requestAccess, authorize, queryHeadsets,
controlDevice, createSession, subscribe), fans decoded samples out to
listeners as `types.Sample`, and keeps a latest-state cache for the server.
`strands_emotiv.fake.FakeCortex` can replay fixtures through the same client
via `ws_factory=` for offline testing.

Hardware notes: EPOC X over "usb cable" is charge/firmware only (sensors stay
[] and createSession answers -32152 forever), so data requires the USB dongle
or BLE. `dev`+`eq` are always co-subscribed so every Sample carries a contact
quality summary. Recordings are local JSON-lines files under recordings/.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .state import quat_to_euler_deg
from .types import EPOCX_CHANNELS, Sample

CORTEX_URL = os.environ.get("CORTEX_URL", "wss://localhost:6868")

#: Cortex error code for "Headset is not ready yet" (no first sample seen).
HEADSET_NOT_READY = -32152

#: dev-stream contact quality is 0..4 (0 none, 4 good). A sensor counts as
#: usable ("good" in the CQ summary) at or above this.
CQ_OK = 2

#: quality streams co-subscribed with every data stream.
TRUST_STREAMS = ("dev", "eq")

BANDS = ("theta", "alpha", "betaL", "betaH", "gamma")

# Cortex `met` cols → friendly metric names (Basic license, EPOC X).
MET_MAP = {
    "eng": "engagement", "exc": "excitement", "lex": "longExcitement",
    "str": "stress", "rel": "relaxation", "int": "interest", "foc": "focus",
    "attention": "attention",
}

SampleListener = Callable[[Sample], Any]


class CortexError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


def _env(path: str = ".env") -> dict[str, str]:
    out: dict[str, str] = {}
    if os.path.exists(path):
        with open(path) as fh:
            for raw in fh:
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k] = v
    out.update({k: v for k, v in os.environ.items() if k.startswith("EMOTIV_")})
    return out


def _flatten(labels: list, values: list) -> dict[str, Any] | None:
    """Zip cols with values, flattening parallel nested lists (the `dev`
    stream nests its per-sensor contact-quality block: cols
    ["Battery","Signal",[...sensors...],"BatteryPercent"]). Returns None on a
    shape mismatch so the caller can fall back to raw."""
    if len(labels) != len(values):
        return None
    out: dict[str, Any] = {}
    for lab, val in zip(labels, values, strict=False):  # Cortex may pad/truncate
        if isinstance(lab, list):
            if not isinstance(val, list):
                return None
            inner = _flatten(lab, val)
            if inner is None:
                return None
            out.update(inner)
        else:
            out[str(lab)] = val
    return out


def headset_usable(h: dict) -> bool:
    """A headset can feed a session only when it's connected over a data link.
    EPOC X over "usb cable" charges but does not stream (sensors stay [])."""
    return h.get("status") == "connected" and (
        h.get("connectedBy") != "usb cable" or bool(h.get("sensors")))


class CortexClient:
    """Persistent Cortex connection: documented flow, Sample fan-out, state cache."""

    DEFAULT_STREAMS: tuple[str, ...] = ("met", "pow", "fac", "mot", "dev", "eq")

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 streams: list[str] | None = None, url: str | None = None,
                 ws_factory: Callable[[], Any] | None = None,
                 poll_interval: float = 5.0,
                 backoff_base: float = 2.0, backoff_cap: float = 30.0):
        env = _env()
        self.client_id = client_id or env.get("EMOTIV_CLIENT_ID")
        self.client_secret = client_secret or env.get("EMOTIV_CLIENT_SECRET")
        self.url = url or CORTEX_URL
        self.streams = streams or list(self.DEFAULT_STREAMS)
        self._ws_factory = ws_factory
        self.poll_interval = poll_interval
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap

        self._ws: Any = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._pending_method: dict[int, str] = {}
        self._reader_task: asyncio.Task | None = None

        self.token: str | None = None
        self.session_id: str | None = None
        self.headset: str | None = None
        self.cols: dict[str, list] = {}          # stream -> cols from subscribe

        self._sample_listeners: list[SampleListener] = []
        self.listeners: list[Callable[[str, dict], None]] = []   # legacy (stream, data)

        # local jsonl recording
        self._record_fh: Any = None
        self.record_path: Any = None

        # live cache read by the server
        self.state: dict[str, Any] = {
            "connected": False, "headset": None, "battery": None, "signal": None,
            "contact_quality": {}, "eeg_quality": {}, "metrics": {},
            "band_power": {}, "facial": {}, "mental_command": {},
            "warning": None, "updated": None,
        }
        self.history: deque[dict] = deque(maxlen=3600)  # ~30 min of met @2 Hz

    # RPC plumbing

    async def _rpc(self, method: str, params: dict | None = None, timeout: float = 30) -> Any:
        self._id += 1
        rid = self._id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        self._pending_method[rid] = method
        await self._ws.send(json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": rid}))
        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)
            self._pending_method.pop(rid, None)
        if "error" in res:
            raise CortexError(res["error"]["code"], res["error"].get("message", ""))
        return res["result"]

    async def _reader(self):
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in self._pending:
                    # harvest subscribe cols HERE: Cortex fires the first
                    # samples right behind the subscribe reply, and the
                    # awaiting coroutine may not have resumed yet
                    if self._pending_method.get(msg["id"]) == "subscribe":
                        for ok in (msg.get("result") or {}).get("success", []):
                            if "cols" in ok:
                                self.cols[ok["streamName"]] = ok["cols"]
                    self._pending[msg["id"]].set_result(msg)
                elif "sid" in msg:
                    self._on_stream(msg)
                elif "warning" in msg:
                    self.state["warning"] = msg["warning"]
                    self._emit("warning", msg["warning"])
        except Exception as e:  # any transport error = drop
            self.state["connected"] = False
            self._emit("disconnect", {"error": str(e)})
        else:
            self.state["connected"] = False
            self._emit("disconnect", {"error": None})
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("cortex connection closed"))
        self._pending.clear()

    # lifecycle

    async def connect(self):
        """Open the socket, requestAccess, authorize → cortexToken."""
        if not (self.client_id and self.client_secret):
            raise CortexError(
                -1, "EMOTIV_CLIENT_ID / EMOTIV_CLIENT_SECRET missing: put both in .env, "
                    "then check with: strands-emotiv doctor")
        if self._ws_factory is not None:
            ws = self._ws_factory()
            self._ws = await ws if inspect.isawaitable(ws) else ws
        else:
            import ssl

            import websockets
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # Cortex uses a self-signed local cert
            self._ws = await websockets.connect(self.url, ssl=ctx, max_size=2 ** 24)
        self._reader_task = asyncio.ensure_future(self._reader())

        creds = {"clientId": self.client_id, "clientSecret": self.client_secret}
        access = await self._rpc("requestAccess", creds)
        if not access.get("accessGranted"):
            raise CortexError(-1, "Access not granted: approve the app in EMOTIV Launcher")
        self.token = (await self._rpc("authorize", {**creds, "debit": 1}))["cortexToken"]
        return self.token

    async def wait_ready(self, headset_id: str | None = None,
                         timeout: float = 3600) -> dict:
        """Poll until a headset has a real data link (dongle/BLE, not the
        charge-only usb cable). Connects discovered headsets and refreshes the
        device list while waiting. Returns the headset dict; raises TimeoutError."""
        t0 = time.monotonic()
        while True:
            headsets = await self._rpc("queryHeadsets")
            for h in headsets:
                if headset_id and h.get("id") != headset_id:
                    continue
                usable = headset_usable(h)
                self.state["headset"] = {
                    "id": h.get("id"), "by": h.get("connectedBy"),
                    "status": h.get("status"), "usable": usable,
                }
                if usable:
                    self.headset = h["id"]
                    return h
                if h.get("status") == "discovered":
                    await self._rpc("controlDevice",
                                    {"command": "connect", "headset": h["id"]})
            if time.monotonic() - t0 >= timeout:
                raise TimeoutError(
                    "no headset with a data link (EPOC X usb cable is charge-only; "
                    "plug the USB receiver dongle or pair BLE)")
            await self._rpc("controlDevice", {"command": "refresh"})
            await asyncio.sleep(self.poll_interval)

    # backward-compatible alias
    wait_headset = wait_ready

    async def create_session(self, retries: int = 8, status: str = "active"):
        """createSession with exponential backoff on -32152 "Headset is not
        ready yet" (Cortex hasn't seen a first sample: normal for a few
        seconds right after the dongle link comes up; forever on usb cable)."""
        last: CortexError | None = None
        for attempt in range(retries):
            try:
                res = await self._rpc("createSession", {
                    "cortexToken": self.token, "headset": self.headset,
                    "status": status})
                self.session_id = res["id"]
                self.state["connected"] = True
                return res
            except CortexError as e:
                if e.code != HEADSET_NOT_READY:
                    raise
                last = e
                delay = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
                self._emit("warning", {"code": e.code, "message": e.message,
                                       "retry_in": delay, "attempt": attempt + 1})
                await asyncio.sleep(delay)
        raise last  # type: ignore[misc]

    open_session = create_session  # backward-compatible alias

    async def subscribe(self, streams: list[str] | None = None,
                        on_sample: SampleListener | None = None) -> dict:
        """Subscribe and remember each stream's `cols` so samples fan out as
        Sample dicts (not bare arrays). on_sample fires for every Sample.
        `dev`+`eq` are always co-subscribed, so a data stream never arrives
        without its contact quality."""
        if on_sample is not None:
            self._sample_listeners.append(on_sample)
        wanted = list(streams or self.streams)
        wanted += [t for t in TRUST_STREAMS if t not in wanted]
        res = await self._rpc("subscribe", {
            "cortexToken": self.token, "session": self.session_id,
            "streams": wanted})
        for ok in res.get("success", []):
            if "cols" in ok:
                self.cols[ok["streamName"]] = ok["cols"]
        for bad in res.get("failure", []):
            self._emit("warning", {"code": bad.get("code"),
                                   "message": f"subscribe {bad.get('streamName')}: "
                                              f"{bad.get('message')}"})
        return res

    async def unsubscribe(self, streams: list[str] | None = None) -> dict:
        return await self._rpc("unsubscribe", {
            "cortexToken": self.token, "session": self.session_id,
            "streams": streams or self.streams})

    # contact quality

    @property
    def cq(self) -> dict[str, Any] | None:
        """Contact-quality summary attached to every Sample. None until the
        first dev/eq frame has landed."""
        sensors: dict[str, Any] = self.state["contact_quality"]
        eq: dict[str, Any] = self.state["eeg_quality"]
        if not sensors and not eq:
            return None
        good = sum(1 for v in sensors.values()
                   if isinstance(v, (int, float)) and v >= CQ_OK)
        return {
            "good": good,
            "total": len(sensors) or len(EPOCX_CHANNELS),
            "overall": eq.get("overall"),
            "battery": self.state["battery"],
            "signal": self.state["signal"],
            "sensors": dict(sensors),
        }

    # markers

    async def inject_marker(self, label: str, value: int | str,
                            port: str = "strands-emotiv",
                            marker_time: float | None = None) -> dict:
        """Stamp the EEG record via injectMarker (agent did X at t). The marker
        is also appended to the local recording, if one is running."""
        t = marker_time if marker_time is not None else time.time()
        res = await self._rpc("injectMarker", {
            "cortexToken": self.token, "session": self.session_id,
            "label": label, "value": value, "port": port,
            "time": int(t * 1000)})
        marker = res.get("marker", res) if isinstance(res, dict) else res
        self._record_line({"stream": "marker", "time": t,
                           "data": {"label": label, "value": value, "port": port,
                                    "marker": marker}, "cq": self.cq})
        return marker

    # recording

    def start_record(self, path: str | None = None) -> str:
        """Append every Sample (with its cq) + every injected marker to a local
        JSON-lines file. Default: recordings/<headset>-<utc>.jsonl (gitignored).
        Returns the path. Restart-safe: a second call rotates to a new file."""
        self.stop_record()
        if path is None:
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            path = os.path.join("recordings", f"{self.headset or 'session'}-{stamp}.jsonl")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._record_fh = open(path, "a", encoding="utf-8")
        self.record_path = path
        return path

    def stop_record(self) -> str | None:
        """Close the recording; returns the path that was being written."""
        path, fh = self.record_path, self._record_fh
        self._record_fh = None
        self.record_path = None
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
        return path

    def _record_line(self, obj: dict):
        if self._record_fh is None:
            return
        try:
            self._record_fh.write(json.dumps(obj, default=str) + "\n")
            self._record_fh.flush()
        except Exception:
            pass

    async def run(self):
        """connect, wait_ready, create_session, subscribe. Returns once
        streaming; the reader keeps fanning samples out in the background."""
        await self.connect()
        await self.wait_ready()
        await self.create_session()
        await self.subscribe()

    async def run_forever(self, retry_delay: float = 5.0):
        """run(), and on connection loss tear down and start over."""
        while True:
            try:
                await self.run()
                if self._reader_task:
                    await self._reader_task  # returns when the socket dies
            except asyncio.CancelledError:
                raise
            except Exception as e:  # retry loop must survive anything
                self.state["error"] = str(e)
            self.state["connected"] = False
            await self.close()
            await asyncio.sleep(retry_delay)

    async def close(self):
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # best-effort teardown
                pass
        self._ws = None
        self.state["connected"] = False

    # stream decoding

    def _on_stream(self, msg: dict):
        now = float(msg.get("time", time.time()))
        self.state["updated"] = now
        for key, values in msg.items():
            if key in ("sid", "time"):
                continue
            labels = self.cols.get(key)
            data = _flatten(labels, values) if isinstance(labels, list) and \
                isinstance(values, list) else None
            if data is not None:
                sample = Sample(stream=key, time=now, data=data)  # type: ignore[arg-type]
            else:
                sample = Sample.from_cortex({key: values, "time": now}, self.cols)
                if sample is None:
                    continue
            self._ingest(sample)

    def _ingest(self, sample: Sample) -> None:
        """State update + fan-out for one decoded Sample. The wire path
        (_on_stream) and fixture replay (fake.FakeRelayClient) share it."""
        self._update_state(sample)
        sample.cq = self.cq   # contact quality current at decode time
        self._record_line({"stream": sample.stream, "time": sample.time,
                           "data": sample.data, "cq": sample.cq})
        for fn in self._sample_listeners:
            try:
                fn(sample)
            except Exception:  # a listener must not kill the reader
                pass
        self._emit(sample.stream, sample.data)

    def _update_state(self, s: Sample):
        d = s.data
        if s.stream == "met":
            # merge per key: Cortex sends exc/lex at 2 Hz but the other five
            # metrics only every ~10 s, replacing the whole dict would blank
            # them for 9.5 of every 10 s (that is what the radar used to show)
            metrics = {MET_MAP.get(k, k): v for k, v in d.items()
                       if not k.endswith(".isActive") and isinstance(v, (int, float))
                       and not (isinstance(v, float) and math.isnan(v))
                       and d.get(f"{k}.isActive", True)}
            if metrics:
                self.state["metrics"] = {**self.state["metrics"], **metrics}
                # history rows carry the merged view (last-known-value, same
                # resampling doctrine as the dataset): a 2 Hz exc/lex row would
                # otherwise be useless to the radar trail, which needs all axes
                self.history.append({"t": s.time, **self.state["metrics"]})
        elif s.stream == "pow":
            bp: dict[str, dict[str, float]] = {}
            for k, v in d.items():
                if "/" in k and isinstance(v, (int, float)):
                    sensor, band = k.split("/", 1)
                    bp.setdefault(sensor, {})[band] = v
            if bp:
                self.state["band_power"] = bp
        elif s.stream == "fac":
            self.state["facial"] = {
                "eye": d.get("eyeAct"), "upper": d.get("uAct"),
                "upper_pow": d.get("uPow"), "lower": d.get("lAct"),
                "lower_pow": d.get("lPow")}
        elif s.stream == "com":
            self.state["mental_command"] = {"action": d.get("act"), "power": d.get("pow")}
        elif s.stream == "mot":
            try:
                q = [float(d[k]) for k in ("Q0", "Q1", "Q2", "Q3")]
            except (KeyError, TypeError, ValueError):
                return
            yaw, pitch, roll = quat_to_euler_deg(*q)
            self.state["motion"] = {
                "q": q,
                "acc": [d.get("ACCX"), d.get("ACCY"), d.get("ACCZ")],
                "mag": [d.get("MAGX"), d.get("MAGY"), d.get("MAGZ")],
                "yaw": round(yaw, 2), "pitch": round(pitch, 2), "roll": round(roll, 2),
            }
        elif s.stream == "dev":
            self.state["battery"] = d.get("BatteryPercent", d.get("Battery"))
            self.state["signal"] = d.get("Signal")
            cq = {ch: d[ch] for ch in EPOCX_CHANNELS if ch in d}
            if cq:
                self.state["contact_quality"] = cq
        elif s.stream == "eq":
            self.state["eeg_quality"] = dict(d)

    def _emit(self, stream: str, data: dict):
        for fn in self.listeners:
            try:
                fn(stream, data)
            except Exception:  # a listener must not kill the reader
                pass


async def _demo():
    c = CortexClient()
    c.listeners.append(lambda s, d: print(s, json.dumps(d)[:140]))
    await c.run()
    await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(_demo())
