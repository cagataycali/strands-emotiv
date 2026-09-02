"""Drop-in stand-in for the CortexClient stream layer.

Synthesizes realistic EMOTIV EPOC X samples, or replays a recorded fixture
jsonl, behind the same contract: `async def samples() -> AsyncIterator[Sample]`.

Synthetic model:
eeg  256 Hz  14 ch, ~4200 µV baseline + pink-ish noise + 10 Hz alpha
             + blink artifacts on AF3/AF4 every ~3 s
mot  32 Hz   quaternion Q0..Q3 (slow drift + occasional yaw turns) + ACC/MAG
fac  8 Hz    eyeAct blink/winkL/winkR (synced to eeg blink artifacts),
             uAct/lAct with pow
met  0.1 Hz  eng/exc/lex/str/rel/int/foc as isActive+value pairs
pow  8 Hz    per channel theta/alpha/betaL/betaH/gamma
dev  2 Hz    battery, signal, per-channel contact quality 0..4
"""

from __future__ import annotations

import json
import math
import random
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .cortex import CortexClient
from .types import EPOCX_CHANNELS, Sample

MET_COLS = ("eng", "exc", "lex", "str", "rel", "int", "foc")
BANDS = ("theta", "alpha", "betaL", "betaH", "gamma")

RATES = {"eeg": 256.0, "mot": 64.0, "fac": 8.0, "pow": 8.0, "dev": 2.0, "met": 0.1}


class FakeCortex:
    """Replays a fixture jsonl if given/found, else synthesizes EPOC X data.

    `async for sample in fake.samples(): ...` yields `Sample` objects, same as
    CortexClient. Not realtime by default, so tests don't sleep; pass
    realtime=True for dashboard demos.
    """

    DEFAULT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "epocx_live_10s.jsonl"

    def __init__(
        self,
        fixture: str | Path | None = None,
        *,
        duration_s: float = 10.0,
        seed: int = 42,
        realtime: bool = False,
        streams: tuple[str, ...] = ("eeg", "mot", "fac", "met", "pow", "dev"),
        blink_every_s: float = 3.0,
        yaw_turn_at_s: tuple[float, ...] = (4.0, 7.5),
    ) -> None:
        self.fixture = Path(fixture) if fixture else (self.DEFAULT_FIXTURE if self.DEFAULT_FIXTURE.exists() else None)
        self.duration_s = duration_s
        self.seed = seed
        self.realtime = realtime
        self.streams = streams
        self.blink_every_s = blink_every_s
        self.yaw_turn_at_s = yaw_turn_at_s

    # public
    async def samples(self) -> AsyncIterator[Sample]:
        if self.fixture is not None:
            async for s in self._replay():
                yield s
            return
        async for s in self._synthesize():
            yield s

    # replay
    async def _replay(self) -> AsyncIterator[Sample]:
        import asyncio

        prev_t: float | None = None
        with open(self.fixture) as f:  # type: ignore[arg-type]
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                s = Sample(stream=obj["stream"], time=float(obj["time"]), data=obj["data"])
                if self.realtime and prev_t is not None:
                    dt = s.time - prev_t
                    if 0 < dt < 1:
                        await asyncio.sleep(dt)
                prev_t = s.time
                yield s

    # synthesize
    async def _synthesize(self) -> AsyncIterator[Sample]:
        import asyncio

        rng = random.Random(self.seed)
        t0 = 1_700_000_000.0
        # per-stream next-due simulated clock
        due = {s: 0.0 for s in self.streams if s in RATES}
        yaw = 0.0
        pitch = 0.0
        wall_elapsed = 0.0

        while due and min(due.values()) < self.duration_s:
            stream = min(due, key=lambda s: due[s])
            t = due[stream]
            due[stream] += 1.0 / RATES[stream]

            if self.realtime and t > wall_elapsed:
                await asyncio.sleep(t - wall_elapsed)
                wall_elapsed = t

            if stream == "eeg":
                data = self._eeg(t, rng)
            elif stream == "mot":
                yaw, pitch, data = self._mot(t, yaw, pitch, rng)
            elif stream == "fac":
                data = self._fac(t, rng)
            elif stream == "met":
                data = self._met(t, rng)
            elif stream == "pow":
                data = self._pow(t, rng)
            else:  # dev
                data = self._dev(t, rng)

            yield Sample(stream=stream, time=t0 + t, data=data)  # type: ignore[arg-type]

    # blink window: 300 ms starting at each multiple of blink_every_s
    def _in_blink(self, t: float) -> bool:
        return t >= self.blink_every_s and (t % self.blink_every_s) < 0.3

    def _eeg(self, t: float, rng: random.Random) -> dict:
        alpha = 20.0 * math.sin(2 * math.pi * 10.0 * t)  # 10 Hz alpha, ~20 µV
        data: dict = {}
        blink = self._in_blink(t)
        for ch in EPOCX_CHANNELS:
            v = 4200.0 + alpha + rng.gauss(0, 6.0)
            if blink and ch in ("AF3", "AF4"):
                # classic frontal blink artifact: big positive deflection
                phase = (t % self.blink_every_s) / 0.3
                v += 180.0 * math.sin(math.pi * phase)
            data[ch] = round(v, 2)
        return data

    def _mot(self, t: float, yaw: float, pitch: float, rng: random.Random) -> tuple[float, float, dict]:
        # occasional yaw turns: ±35° over 0.8 s at configured times
        for i, at in enumerate(self.yaw_turn_at_s):
            if at <= t < at + 0.8:
                direction = 1.0 if i % 2 == 0 else -1.0
                yaw = direction * 35.0 * (t - at) / 0.8
        yaw += rng.gauss(0, 0.05)
        pitch = 2.0 * math.sin(2 * math.pi * 0.1 * t) + rng.gauss(0, 0.05)
        cy, sy = math.cos(math.radians(yaw) / 2), math.sin(math.radians(yaw) / 2)
        cp, sp = math.cos(math.radians(pitch) / 2), math.sin(math.radians(pitch) / 2)
        # ZYX euler (roll=0) → quaternion; Cortex order Q0=w Q1=x Q2=y Q3=z
        q0 = cp * cy
        q1 = -sp * sy
        q2 = sp * cy
        q3 = cp * sy
        data = {
            "Q0": round(q0, 6), "Q1": round(q1, 6), "Q2": round(q2, 6), "Q3": round(q3, 6),
            "ACCX": round(rng.gauss(0, 0.02), 4), "ACCY": round(rng.gauss(0, 0.02), 4),
            "ACCZ": round(1.0 + rng.gauss(0, 0.02), 4),
            "MAGX": round(30 + rng.gauss(0, 0.5), 3), "MAGY": round(-12 + rng.gauss(0, 0.5), 3),
            "MAGZ": round(44 + rng.gauss(0, 0.5), 3),
        }
        return yaw, pitch, data

    def _fac(self, t: float, rng: random.Random) -> dict:
        eye = "neutral"
        if self._in_blink(t):
            eye = "blink"
        elif abs((t % 9.0) - 5.0) < 0.2:
            eye = "winkL"
        elif abs((t % 9.0) - 8.0) < 0.2:
            eye = "winkR"
        return {
            "eyeAct": eye,
            "uAct": "neutral" if rng.random() < 0.9 else "surprise",
            "uPow": round(rng.random() * 0.3, 3),
            "lAct": "neutral" if rng.random() < 0.9 else "smile",
            "lPow": round(rng.random() * 0.3, 3),
        }

    def _met(self, t: float, rng: random.Random) -> dict:
        data: dict = {}
        for m in MET_COLS:
            base = {"eng": 0.62, "exc": 0.35, "lex": 0.3, "str": 0.28, "rel": 0.55, "int": 0.6, "foc": 0.58}[m]
            data[f"{m}.isActive"] = True
            data[m] = round(min(1.0, max(0.0, base + 0.1 * math.sin(t / 30 + hash(m) % 7) + rng.gauss(0, 0.02))), 4)
        return data

    def _pow(self, t: float, rng: random.Random) -> dict:
        data: dict = {}
        for ch in EPOCX_CHANNELS:
            for band in BANDS:
                base = {"theta": 1.2, "alpha": 2.5, "betaL": 0.9, "betaH": 0.6, "gamma": 0.35}[band]
                data[f"{ch}/{band}"] = round(max(0.01, base + rng.gauss(0, base * 0.15)), 4)
        return data

    def _dev(self, t: float, rng: random.Random) -> dict:
        data: dict = {"battery": 82, "signal": 1.0, "batteryPercent": 82}
        for ch in EPOCX_CHANNELS:
            data[ch] = 4 if rng.random() > 0.08 else 2
        return data


class FakeRelayClient(CortexClient):
    """A CortexClient whose wire is the fixture. run() loops FakeCortex
    (realtime pacing) through the same _ingest path the real reader uses,
    stamping arrival time, so dashboard, tools and recorder get identical
    plumbing. Built by `strands-emotiv dashboard --fake` / EMOTIV_FAKE=1."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.state["connected"] = True
        self.state["headset"] = {"id": "FAKE-EPOCX", "status": "connected",
                                 "connectedBy": "fixture",
                                 "sensors": list(EPOCX_CHANNELS)}

    async def run(self) -> None:
        while True:
            fake = FakeCortex(realtime=True)
            async for s in fake.samples():
                s.time = time.time()
                self.state["updated"] = s.time
                self._ingest(s)
