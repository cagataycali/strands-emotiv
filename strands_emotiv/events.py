"""Deterministic, debounced event detection over raw Samples. No LLM.

Derived events:

  fac  blink · double_blink · wink_left · wink_right · clench
  mot  head_turn_left · head_turn_right · nod · look_up · look_down
  met  focus_high · focus_low · stress_high        (hysteresis, L2 boundary)
  com  command:<act>                               (pow gate, L3 consent)

Every detector is a small state machine with debounce and/or hysteresis so
an event fires exactly once per gesture.

Sign convention (matches FakeCortex; flip `yaw_sign` if the real headset
disagrees): positive yaw = head turned LEFT, positive pitch = looking UP.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .state import quat_to_euler_deg
from .types import Sample

# minimum seconds between two firings of the same kind
DEBOUNCE_S: dict[str, float] = {
    "blink": 0.4,
    "double_blink": 1.0,
    "wink_left": 0.6,
    "wink_right": 0.6,
    "clench": 0.8,
    "head_turn_left": 1.0,
    "head_turn_right": 1.0,
    "nod": 1.0,
    "look_up": 1.5,
    "look_down": 1.5,
}
DOUBLE_BLINK_WINDOW_S = 0.8

# met hysteresis: (arm_at, rearm_at). An event fires crossing arm_at and re-arms crossing rearm_at.
FOCUS_HIGH = (0.70, 0.60)
FOCUS_LOW = (0.30, 0.40)
STRESS_HIGH = (0.70, 0.60)

COMMAND_POW_MIN = 0.30
COMMAND_DEBOUNCE_S = 1.0

TURN_DEG = 20.0  # |yaw delta| within TURN_WINDOW_S that counts as a deliberate turn
TURN_WINDOW_S = 1.0
NOD_DIP_DEG = 10.0  # pitch dip depth
NOD_RETURN_DEG = 5.0  # how close to baseline counts as "came back"
NOD_MAX_S = 1.5  # dip→return must complete within this
LOOK_DEG = 15.0  # sustained pitch beyond ± this → look_up/down
LOOK_REARM_DEG = 10.0


@dataclass(slots=True)
class Event:
    kind: str
    time: float  # sample time (Cortex epoch seconds)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "time": self.time, **({"detail": self.detail} if self.detail else {})}


class EventEngine:
    """feed(Sample) yields zero or more Events to sync subscribers and async wait_for.

    Time is sample time, never wall time, so fixtures replay identically.
    """

    def __init__(self, yaw_sign: float = 1.0, history: int = 256) -> None:
        self.yaw_sign = yaw_sign
        self._subs: list[tuple[str, Callable[[Event], None]]] = []
        self._queues: list[tuple[str, asyncio.Queue[Event]]] = []
        self._recent: list[Event] = []
        self._history = history
        # debounce + detector state
        self._last_fire: dict[str, float] = {}
        self._prev_eye = "neutral"
        self._last_blink_t: float | None = None
        self._yaw_hist: list[tuple[float, float]] = []  # (t, yaw)
        self._pitch_ref: float | None = None  # slow baseline
        self._nod_state: tuple[float, float] | None = None  # (dip_t, ref_at_dip)
        self._look_armed = {"up": True, "down": True}
        self._met_armed = {"focus_high": True, "focus_low": True, "stress_high": True}
        self._last_command_t: float = -1e9

    # pub/sub
    def subscribe(self, kind: str, cb: Callable[[Event], None]) -> None:
        """kind '*' matches everything; 'command:*' matches any command."""
        self._subs.append((kind, cb))

    async def wait_for(self, kind: str, timeout_s: float = 10.0) -> Event:
        q: asyncio.Queue[Event] = asyncio.Queue()
        entry = (kind, q)
        self._queues.append(entry)
        try:
            return await asyncio.wait_for(q.get(), timeout_s)
        finally:
            self._queues.remove(entry)

    def recent(self, limit: int = 20) -> list[Event]:
        return self._recent[-limit:]

    @staticmethod
    def _match(pattern: str, kind: str) -> bool:
        if pattern == "*" or pattern == kind:
            return True
        return pattern.endswith(":*") and kind.startswith(pattern[:-1])

    def _emit(self, kind: str, t: float, **detail: Any) -> None:
        gap = DEBOUNCE_S.get(kind)
        last = self._last_fire.get(kind)
        if gap is not None and last is not None and (t - last) < gap:
            return
        self._last_fire[kind] = t
        ev = Event(kind=kind, time=t, detail=detail)
        self._recent.append(ev)
        del self._recent[: -self._history]
        for pattern, cb in self._subs:
            if self._match(pattern, kind):
                cb(ev)
        for pattern, q in self._queues:
            if self._match(pattern, kind):
                q.put_nowait(ev)

    # feed
    def feed(self, s: Sample) -> None:
        if s.stream == "fac":
            self._on_fac(s)
        elif s.stream == "mot":
            self._on_mot(s)
        elif s.stream == "met":
            self._on_met(s)
        elif s.stream == "com":
            self._on_com(s)

    # fac
    def _on_fac(self, s: Sample) -> None:
        eye = s.data.get("eyeAct", "neutral")
        t = s.time
        if eye != self._prev_eye:  # rising edges only
            if eye == "blink":
                if self._last_blink_t is not None and (t - self._last_blink_t) <= DOUBLE_BLINK_WINDOW_S:
                    self._emit("double_blink", t, gap_s=round(t - self._last_blink_t, 3))
                    self._last_blink_t = None
                else:
                    self._last_blink_t = t
                self._emit("blink", t)
            elif eye == "winkL":
                self._emit("wink_left", t)
            elif eye == "winkR":
                self._emit("wink_right", t)
        self._prev_eye = eye
        for act_key, pow_key in (("uAct", "uPow"), ("lAct", "lPow")):
            if s.data.get(act_key) == "clench" and float(s.data.get(pow_key, 0.0)) >= 0.3:
                self._emit("clench", t, pow=float(s.data.get(pow_key, 0.0)))

    # mot
    def _on_mot(self, s: Sample) -> None:
        try:
            yaw, pitch, _roll = quat_to_euler_deg(
                *(float(s.data[q]) for q in ("Q0", "Q1", "Q2", "Q3"))
            )
        except (KeyError, TypeError, ValueError):
            return
        yaw *= self.yaw_sign
        t = s.time

        # deliberate turn: yaw moved >= TURN_DEG within the last TURN_WINDOW_S
        self._yaw_hist.append((t, yaw))
        while self._yaw_hist and t - self._yaw_hist[0][0] > TURN_WINDOW_S:
            self._yaw_hist.pop(0)
        oldest_yaw = self._yaw_hist[0][1]
        d_yaw = yaw - oldest_yaw
        if d_yaw >= TURN_DEG:
            self._emit("head_turn_left", t, d_yaw=round(d_yaw, 1))
        elif d_yaw <= -TURN_DEG:
            self._emit("head_turn_right", t, d_yaw=round(d_yaw, 1))

        # slow pitch baseline (EMA with long memory; deliberate motions barely move it)
        if self._pitch_ref is None:
            self._pitch_ref = pitch
        else:
            self._pitch_ref += 0.005 * (pitch - self._pitch_ref)
        ref = self._pitch_ref

        # nod: dip >= NOD_DIP_DEG below baseline, then back within NOD_MAX_S
        if self._nod_state is None:
            if pitch <= ref - NOD_DIP_DEG:
                self._nod_state = (t, ref)
        else:
            dip_t, dip_ref = self._nod_state
            if t - dip_t > NOD_MAX_S:
                self._nod_state = None
            elif pitch >= dip_ref - NOD_RETURN_DEG:
                self._nod_state = None
                self._emit("nod", t, dur_s=round(t - dip_t, 3))

        # sustained look up/down with hysteresis
        if pitch >= LOOK_DEG:
            if self._look_armed["up"]:
                self._look_armed["up"] = False
                self._emit("look_up", t, pitch=round(pitch, 1))
        elif pitch <= LOOK_REARM_DEG:
            self._look_armed["up"] = True
        if pitch <= -LOOK_DEG:
            if self._look_armed["down"]:
                self._look_armed["down"] = False
                self._emit("look_down", t, pitch=round(pitch, 1))
        elif pitch >= -LOOK_REARM_DEG:
            self._look_armed["down"] = True

    # met
    def _on_met(self, s: Sample) -> None:
        t = s.time
        foc = s.data.get("foc")
        stress = s.data.get("str")
        if foc is not None:
            foc = float(foc)
            if self._met_armed["focus_high"] and foc >= FOCUS_HIGH[0]:
                self._met_armed["focus_high"] = False
                self._emit("focus_high", t, foc=foc)
            elif not self._met_armed["focus_high"] and foc <= FOCUS_HIGH[1]:
                self._met_armed["focus_high"] = True
            if self._met_armed["focus_low"] and foc <= FOCUS_LOW[0]:
                self._met_armed["focus_low"] = False
                self._emit("focus_low", t, foc=foc)
            elif not self._met_armed["focus_low"] and foc >= FOCUS_LOW[1]:
                self._met_armed["focus_low"] = True
        if stress is not None:
            stress = float(stress)
            if self._met_armed["stress_high"] and stress >= STRESS_HIGH[0]:
                self._met_armed["stress_high"] = False
                self._emit("stress_high", t, stress=stress)
            elif not self._met_armed["stress_high"] and stress <= STRESS_HIGH[1]:
                self._met_armed["stress_high"] = True

    # com
    def _on_com(self, s: Sample) -> None:
        act = s.data.get("act", "neutral")
        pow_ = float(s.data.get("pow", 0.0))
        t = s.time
        if act != "neutral" and pow_ > COMMAND_POW_MIN and (t - self._last_command_t) >= COMMAND_DEBOUNCE_S:
            self._last_command_t = t
            self._emit(f"command:{act}", t, pow=pow_)
