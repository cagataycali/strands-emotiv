"""Mental commands: profile management, training, and the approval rail.

The flagship surface is `await_decision`: ask the human to PUSH the imaginary
box for YES, PULL it for NO. Everything else in this file exists to make that
one call trustworthy.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .cortex import CortexClient, CortexError
from .types import Sample

DETECTION = "mentalCommand"
DEFAULT_PROFILE = "strands"

# sys-stream event names (Cortex docs, BCI training flow)
EV_STARTED = "MC_Started"
EV_SUCCEEDED = "MC_Succeeded"
EV_FAILED = "MC_Failed"
EV_COMPLETED = "MC_Completed"
EV_REJECTED = "MC_Rejected"
EV_RESET = "MC_Reset"
EV_ERASED = "MC_DataErased"


def _sys_event(s: Sample) -> str | None:
    """Extract the event name from a sys Sample regardless of cols shape."""
    vals = s.data.get("raw") if "raw" in s.data else list(s.data.values())
    if isinstance(vals, list):
        for v in vals:
            if isinstance(v, str) and v.startswith("MC_"):
                return v
    return None


class MentalTrainer:
    """Training + approval on top of a live CortexClient (shares its socket)."""

    def __init__(self, client: CortexClient, profile: str = DEFAULT_PROFILE):
        self.client = client
        self.profile = profile
        self._sys_events: asyncio.Queue[str] = asyncio.Queue()
        self._hooked = False
        self._sys_subscribed = False
        self._lock = asyncio.Lock()  # one training round / approval at a time

    # ---------- plumbing ----------

    def _hook(self):
        if not self._hooked:
            self.client._sample_listeners.append(self._on_sample)
            self._hooked = True

    def _on_sample(self, s: Sample):
        if s.stream == "sys":
            ev = _sys_event(s)
            if ev:
                self._sys_events.put_nowait(ev)
                self.client.state["training_event"] = {"event": ev, "t": s.time}

    async def _ensure_sys(self):
        self._hook()
        if not self._sys_subscribed:
            await self.client.subscribe(["sys"])
            self._sys_subscribed = True

    async def _wait_event(self, wanted: set[str], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise TimeoutError(f"no {wanted} within {timeout}s")
            ev = await asyncio.wait_for(self._sys_events.get(), timeout=remain)
            if ev in wanted:
                return ev

    def _drain(self):
        while not self._sys_events.empty():
            self._sys_events.get_nowait()

    # ---------- profile ----------

    async def profiles(self) -> list[str]:
        res = await self.client._rpc("queryProfile", {"cortexToken": self.client.token})
        return [p["name"] if isinstance(p, dict) else str(p) for p in res]

    async def current_profile(self) -> str | None:
        res = await self.client._rpc("getCurrentProfile", {
            "cortexToken": self.client.token, "headset": self.client.headset})
        return (res or {}).get("name") if isinstance(res, dict) else None

    async def ensure_profile(self, name: str | None = None) -> dict:
        """Create the profile if missing, load it onto the headset."""
        name = name or self.profile
        self.profile = name
        names = await self.profiles()
        created = False
        if name not in names:
            await self.client._rpc("setupProfile", {
                "cortexToken": self.client.token, "headset": self.client.headset,
                "profile": name, "status": "create"})
            created = True
        if await self.current_profile() != name:
            await self.client._rpc("setupProfile", {
                "cortexToken": self.client.token, "headset": self.client.headset,
                "profile": name, "status": "load"})
        self.client.state["mental_profile"] = name
        return {"profile": name, "created": created, "loaded": True}

    async def save(self):
        await self.client._rpc("setupProfile", {
            "cortexToken": self.client.token, "headset": self.client.headset,
            "profile": self.profile, "status": "save"})

    async def trained_actions(self) -> dict:
        return await self.client._rpc("getTrainedSignatureActions", {
            "cortexToken": self.client.token, "detection": DETECTION,
            "profile": self.profile})

    async def set_active(self, actions: list[str]) -> Any:
        return await self.client._rpc("mentalCommandActiveAction", {
            "cortexToken": self.client.token, "profile": self.profile,
            "status": "set", "actions": actions})

    async def status(self) -> dict:
        out: dict[str, Any] = {"profile": self.profile}
        try:
            out["profiles"] = await self.profiles()
            out["loaded"] = await self.current_profile()
            out["trained"] = await self.trained_actions()
            out["active"] = await self.client._rpc("mentalCommandActiveAction", {
                "cortexToken": self.client.token, "profile": self.profile,
                "status": "get"})
        except CortexError as e:
            out["error"] = f"{e}"
        return out

    # ---------- training ----------

    async def train_round(self, action: str, accept: bool = True,
                          timeout: float = 30.0) -> dict:
        """One 8-second training round for `action` (neutral/push/pull/...).

        Flow per Cortex docs: training:start → MC_Started → ~8 s of the human
        holding the thought → MC_Succeeded|MC_Failed → training:accept →
        MC_Completed → profile save.
        """
        async with self._lock:
            await self._ensure_sys()
            await self.ensure_profile()
            self._drain()
            t0 = time.time()
            self.client.state["training"] = {
                "action": action, "phase": "starting", "t0": t0}

            async def _phase(p):
                self.client.state["training"] = {
                    "action": action, "phase": p, "t0": t0}

            await self.client._rpc("training", {
                "cortexToken": self.client.token, "session": self.client.session_id,
                "detection": DETECTION, "action": action, "status": "start"})
            await self._wait_event({EV_STARTED}, timeout=10)
            await _phase("live")  # the 8 seconds the human is performing
            ev = await self._wait_event({EV_SUCCEEDED, EV_FAILED}, timeout=timeout)
            if ev == EV_FAILED:
                await _phase("failed")
                return {"action": action, "ok": False, "event": ev,
                        "hint": "signal too inconsistent; re-seat sensors or "
                                "hold one vivid, repeatable thought"}
            if accept:
                await self.client._rpc("training", {
                    "cortexToken": self.client.token, "session": self.client.session_id,
                    "detection": DETECTION, "action": action, "status": "accept"})
                await self._wait_event({EV_COMPLETED}, timeout=10)
                await self.save()
            await _phase("done")
            return {"action": action, "ok": True, "event": EV_COMPLETED,
                    "elapsed": round(time.time() - t0, 1)}

    async def erase(self, action: str) -> dict:
        await self._ensure_sys()
        self._drain()
        await self.client._rpc("training", {
            "cortexToken": self.client.token, "session": self.client.session_id,
            "detection": DETECTION, "action": action, "status": "erase"})
        await self._wait_event({EV_ERASED}, timeout=10)
        await self.save()
        return {"action": action, "erased": True}

    # ---------- approval ----------

    async def await_decision(self, prompt: str, timeout: float = 45.0,
                             threshold: float = 0.25, hold: int = 3,
                             window_s: float = 2.5) -> dict:
        """PUSH = yes · PULL = no · jaw clench = veto · silence = timeout.

        Decision fires when `hold` com samples for one action, each with
        power ≥ threshold, land inside a rolling `window_s` window.
        Refuses to even ask when contact quality says the signal is bad.
        """
        cq = self.client.cq
        if cq and cq.get("total") and (cq.get("good") or 0) < cq["total"] * 0.5:
            return {"decision": "refused", "reason":
                    f"contact quality too poor ({cq.get('good')}/{cq.get('total')} "
                    "sensors good); a dry electrode is not consent"}
        async with self._lock:
            hits: dict[str, list[float]] = {"push": [], "pull": []}
            q: asyncio.Queue[Sample] = asyncio.Queue()

            def listen(s: Sample):
                if s.stream in ("com", "fac"):
                    q.put_nowait(s)

            self.client._sample_listeners.append(listen)
            t0 = time.time()
            self.client.state["approval"] = {
                "prompt": prompt, "t0": t0, "deadline": t0 + timeout,
                "status": "waiting"}
            decision, detail = "timeout", {}
            try:
                while time.time() - t0 < timeout:
                    try:
                        s = await asyncio.wait_for(q.get(), timeout=0.5)
                    except TimeoutError:
                        continue
                    now = s.time
                    if s.stream == "fac":  # the body has a veto
                        if "clench" in (str(s.data.get("uAct", "")).lower() +
                                        str(s.data.get("lAct", "")).lower()) and \
                                float(s.data.get("lPow") or s.data.get("uPow") or 0) >= 0.4:
                            decision, detail = "vetoed", {"by": "jaw clench"}
                            break
                        continue
                    act = s.data.get("act")
                    pow_ = float(s.data.get("pow") or 0)
                    self.client.state["approval"]["live"] = {
                        "act": act, "pow": round(pow_, 3)}
                    if act in hits and pow_ >= threshold:
                        hits[act].append(now)
                        hits[act] = [t for t in hits[act] if now - t <= window_s]
                        if len(hits[act]) >= hold:
                            decision = "yes" if act == "push" else "no"
                            detail = {"action": act, "power": round(pow_, 3),
                                      "hits": len(hits[act])}
                            break
            finally:
                self.client._sample_listeners.remove(listen)
                self.client.state["approval"] = {
                    "prompt": prompt, "status": "decided", "decision": decision,
                    **detail, "elapsed": round(time.time() - t0, 1)}
            # stamp the record
            try:
                await self.client.inject_marker("mental_approval", decision)
            except Exception:
                pass
            return {"decision": decision, **detail,
                    "elapsed": round(time.time() - t0, 1)}
