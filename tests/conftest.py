"""Wire-level Cortex fake for CortexClient tests.

Distinct from `strands_emotiv.fake.FakeCortex` (the Sample-layer
synthesizer): this one is a duck-typed WEBSOCKET. It answers JSON-RPC
(requestAccess → authorize → queryHeadsets → controlDevice → createSession →
subscribe) and then replays JSON-lines fixtures as raw `{"sid": ...}` frames,
so `CortexClient` is exercised end to end without CortexService or hardware.

Faithful to measured ground truth: the default headset is
EPOCX-E5020C65 on "usb cable" with sensors [] (charge-only ⇒ createSession
answers -32152 forever). `plug_dongle()` flips it to a real data link.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
HEADSET_ID = "EPOCX-E5020C65"

SENSORS = ["AF3", "F7", "F3", "FC5", "T7", "P7", "O1",
           "O2", "P8", "T8", "FC6", "F4", "F8", "AF4"]

# cols as Cortex documents them for the Basic-license streams
COLS: dict[str, list] = {
    "met": ["eng.isActive", "eng", "exc.isActive", "exc", "lex",
            "str.isActive", "str", "rel.isActive", "rel",
            "int.isActive", "int", "foc.isActive", "foc"],
    "pow": [f"{ch}/{band}" for ch in SENSORS
            for band in ("theta", "alpha", "betaL", "betaH", "gamma")],
    "fac": ["eyeAct", "uAct", "uPow", "lAct", "lPow"],
    "com": ["act", "pow"],
    "dev": ["Battery", "Signal", list(SENSORS), "BatteryPercent"],
    "eq": ["batteryPercent", "overall", "sampleRateQuality", *SENSORS],
}


class CortexWire:
    """Duck-typed websocket: answers JSON-RPC, then streams fixture lines."""

    def __init__(self, fixtures: str | Path | Iterable[dict] | None = None,
                 not_ready_answers: int = 0, access_granted: bool = True):
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._closed = False
        self.access_granted = access_granted
        self.not_ready_answers = not_ready_answers   # extra -32152s post-dongle
        self.requests: list[dict] = []               # every RPC seen, for asserts
        self.session_created = False

        # measured default: cable-charging headset, no data link
        self.headset: dict[str, Any] = {
            "id": HEADSET_ID, "status": "connected", "connectedBy": "usb cable",
            "dongle": "0", "sensors": [], "motionSensors": [],
            "settings": {"eegRate": 256, "memsRate": 64, "eegRes": 16},
        }

        if fixtures is None:
            self.fixtures: list[dict] = []
        elif isinstance(fixtures, (str, Path)):
            self.fixtures = [json.loads(line)
                             for line in Path(fixtures).read_text().splitlines()
                             if line.strip()]
        else:
            self.fixtures = list(fixtures)

    # -- world control ------------------------------------------------------

    def plug_dongle(self):
        """The moment cagatay inserts the USB receiver."""
        self.headset.update({
            "connectedBy": "dongle", "dongle": "1",
            "sensors": list(SENSORS), "motionSensors": ["Q0", "Q1", "Q2", "Q3"],
        })

    def push_warning(self, code: int, message: str):
        self._queue.put_nowait(json.dumps(
            {"warning": {"code": code, "message": message}}))

    # -- websocket surface ----------------------------------------------------

    async def send(self, raw: str):
        if self._closed:
            raise ConnectionError("fake socket closed")
        msg = json.loads(raw)
        self.requests.append(msg)
        rid, method, params = msg.get("id"), msg.get("method"), msg.get("params", {})
        handler = getattr(self, f"_h_{method}", None)
        if handler is None:
            self._reply(rid, error={"code": -32601, "message": f"unknown {method}"})
            return
        out = handler(params)
        if isinstance(out, dict) and set(out) == {"error"}:
            self._reply(rid, error=out["error"])
        else:
            self._reply(rid, result=out)

    async def close(self):
        self._closed = True
        await self._queue.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    # -- rpc handlers -----------------------------------------------------------

    def _reply(self, rid, result=None, error=None):
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid}
        body["error" if error else "result"] = error if error else result
        self._queue.put_nowait(json.dumps(body))

    def _h_requestAccess(self, p):
        return {"accessGranted": self.access_granted,
                "message": "granted" if self.access_granted else "denied"}

    def _h_authorize(self, p):
        return {"cortexToken": "fake-token"}

    def _h_queryHeadsets(self, p):
        return [dict(self.headset)]

    def _h_controlDevice(self, p):
        return {"command": p.get("command"), "message": "ok"}

    def _h_createSession(self, p):
        cable_only = (self.headset["connectedBy"] == "usb cable"
                      and not self.headset["sensors"])
        if cable_only or self.not_ready_answers > 0:
            self.not_ready_answers = max(0, self.not_ready_answers - 1)
            return {"error": {"code": -32152, "message": "Headset is not ready yet"}}
        self.session_created = True
        return {"id": "fake-session-1", "status": p.get("status", "active"),
                "headset": {"id": self.headset["id"]}}

    def _h_subscribe(self, p):
        if not self.session_created:
            return {"error": {"code": -32104, "message": "no session"}}
        success, failure = [], []
        for s in p.get("streams", []):
            if s == "eeg":
                # measured on the real headset: raw EEG is paid-license only
                failure.append({"streamName": s, "code": -32016,
                                "message": "The current license does not "
                                           "include EEG data"})
            elif s in COLS:
                success.append({"streamName": s, "cols": COLS[s], "sid": "fake-sid"})
            else:
                failure.append({"streamName": s, "code": -32105,
                                "message": "unknown stream"})
        asyncio.get_running_loop().create_task(self._replay())
        return {"success": success, "failure": failure}

    def _h_unsubscribe(self, p):
        return {"success": [{"streamName": s} for s in p.get("streams", [])],
                "failure": []}

    def _h_injectMarker(self, p):
        if not self.session_created:
            return {"error": {"code": -32104, "message": "no session"}}
        marker = {"uuid": f"fake-marker-{len(self.requests)}",
                  "label": p.get("label"), "value": p.get("value"),
                  "port": p.get("port"), "startDatetime": p.get("time")}
        return {"marker": marker}

    async def _replay(self):
        for sample in self.fixtures:
            if self._closed:
                return
            self._queue.put_nowait(json.dumps({"sid": "fake-sid", **sample}))
        # end-of-tape marker so tests can await quiescence
        self._queue.put_nowait(json.dumps(
            {"sid": "fake-sid", "sys": ["replay", "end"], "time": 0.0}))


@pytest.fixture
def wire() -> CortexWire:
    return CortexWire(fixtures=FIXTURES / "replay_basic.jsonl")


@pytest.fixture
def client_factory():
    from strands_emotiv.cortex import CortexClient

    def make(wire: CortexWire, **kw) -> CortexClient:
        kw.setdefault("client_id", "test-id")
        kw.setdefault("client_secret", "test-secret")
        kw.setdefault("backoff_base", 0.01)
        kw.setdefault("poll_interval", 0.01)
        return CortexClient(ws_factory=lambda: wire, **kw)

    return make


# ---------------------------------------------------------------- isolation
import pytest


@pytest.fixture(autouse=True)
def _isolated_auth_store(tmp_path, monkeypatch):
    """The developer's REAL passkey store (~/.emotiv/auth.json) must never
    decide test outcomes: one enrolled passkey flips auth_enabled() globally
    and every unauthed TestClient request becomes 401."""
    monkeypatch.setenv("EMOTIV_AUTH_STORE", str(tmp_path / "auth-isolated.json"))
    import strands_emotiv.auth as _auth
    _auth._cache.clear()
