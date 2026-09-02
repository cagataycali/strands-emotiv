"""Live snapshot of the headset state.

Feed it Samples (from CortexClient or FakeCortex); read back the latest
snapshot per stream, band power per channel via FFT over the eeg ring, head
pose from the mot quaternion, and the contact quality map from dev.
"""

from __future__ import annotations

import math
import threading
import time as _time
from collections import deque
from typing import Any

import numpy as np

from .types import EPOCX_CHANNELS, Sample

EEG_RATE = 256
MOT_RATE = 64
BAND_DEFS = {"theta": (4.0, 8.0), "alpha": (8.0, 13.0), "beta": (13.0, 30.0), "gamma": (30.0, 45.0)}


def quat_to_euler_deg(q0: float, q1: float, q2: float, q3: float) -> tuple[float, float, float]:
    """Convert quaternion (w,x,y,z) to (yaw, pitch, roll) in degrees, ZYX convention."""
    yaw = math.degrees(math.atan2(2 * (q0 * q3 + q1 * q2), 1 - 2 * (q2**2 + q3**2)))
    s = max(-1.0, min(1.0, 2 * (q0 * q2 - q3 * q1)))
    pitch = math.degrees(math.asin(s))
    roll = math.degrees(math.atan2(2 * (q0 * q1 + q2 * q3), 1 - 2 * (q1**2 + q2**2)))
    return yaw, pitch, roll


class BrainState:
    """Ring buffers + derived views. Thread-safe: dashboard reads while the
    stream task writes."""

    def __init__(self, eeg_window_s: float = 4.0, mot_window_s: float = 4.0) -> None:
        self._lock = threading.RLock()
        self._eeg = deque(maxlen=int(EEG_RATE * eeg_window_s))  # (t, np.array[14])
        self._mot = deque(maxlen=int(MOT_RATE * mot_window_s))  # (t, yaw, pitch, roll)
        self._latest: dict[str, dict[str, Any]] = {}  # stream -> last data dict
        self._latest_t: dict[str, float] = {}
        self._met_prev: dict[str, float] = {}  # metric -> previous distinct value
        self._pose_anchor: tuple[float, float, float] | None = None
        self._last_move_t: float | None = None
        self._counts: dict[str, int] = {}
        self._started = _time.time()

    # feed
    def feed(self, s: Sample) -> None:
        with self._lock:
            if s.stream == "met":
                # merge per key: exc/lex arrive at 2 Hz, the other metrics only
                # every ~10 s. Replacing the dict blanks focus/stress/engagement
                # from the ambient line for 9.5 of every 10 s.
                merged = dict(self._latest.get("met", {}))
                for k, v in s.data.items():
                    if k.endswith(".isActive"):
                        continue
                    if (isinstance(v, (int, float))
                            and not (isinstance(v, float) and math.isnan(v))
                            and s.data.get(f"{k}.isActive", True)):
                        prev = merged.get(k)
                        if isinstance(prev, (int, float)) and prev != v:
                            self._met_prev[k] = float(prev)
                        merged[k] = v
                        merged[f"{k}.isActive"] = True
                self._latest["met"] = merged
            else:
                self._latest[s.stream] = s.data
            self._latest_t[s.stream] = s.time
            self._counts[s.stream] = self._counts.get(s.stream, 0) + 1
            if s.stream == "eeg":
                try:
                    row = np.array([float(s.data[ch]) for ch in EPOCX_CHANNELS])
                except (KeyError, TypeError, ValueError):
                    return
                self._eeg.append((s.time, row))
            elif s.stream == "mot":
                try:
                    ypr = quat_to_euler_deg(*(float(s.data[q]) for q in ("Q0", "Q1", "Q2", "Q3")))
                except (KeyError, TypeError, ValueError):
                    return
                self._mot.append((s.time, *ypr))
                if self._pose_anchor is None or sum(
                        abs(a - b) for a, b in zip(ypr, self._pose_anchor, strict=True)) > 5.0:
                    self._pose_anchor = ypr
                    self._last_move_t = s.time

    # views
    def snapshot(self) -> dict[str, Any]:
        """Latest data for every stream + freshness + pose + bands summary."""
        with self._lock:
            now = _time.time()
            streams = {
                stream: {"data": data, "time": self._latest_t[stream], "count": self._counts[stream]}
                for stream, data in self._latest.items()
            }
            return {
                "streams": streams,
                "pose": self.head_pose(),
                "contact_quality": self.contact_quality(),
                "uptime_s": round(now - self._started, 1),
                "eeg_buffer": len(self._eeg),
            }

    def band_power(self, window_s: float = 2.0) -> dict[str, dict[str, float]]:
        """{channel: {theta, alpha, beta, gamma}}: mean squared magnitude per
        band from an rFFT over the last `window_s` of eeg (detrended)."""
        with self._lock:
            n = int(EEG_RATE * window_s)
            rows = list(self._eeg)[-n:]
        if len(rows) < EEG_RATE // 2:  # need at least 0.5 s
            return {}
        arr = np.stack([r for _, r in rows])  # (t, 14)
        arr = arr - arr.mean(axis=0, keepdims=True)
        freqs = np.fft.rfftfreq(len(arr), d=1.0 / EEG_RATE)
        mag2 = np.abs(np.fft.rfft(arr, axis=0)) ** 2 / len(arr)
        out: dict[str, dict[str, float]] = {}
        for i, ch in enumerate(EPOCX_CHANNELS):
            out[ch] = {}
            for band, (lo, hi) in BAND_DEFS.items():
                sel = (freqs >= lo) & (freqs < hi)
                out[ch][band] = float(round(mag2[sel, i].mean() if sel.any() else 0.0, 4))
        return out

    def metric_trend(self, key: str, deadband: float = 0.03) -> str:
        """'↑' or '↓' vs the metric's previous distinct value, '' inside the
        deadband or without two samples. Honest: no history, no arrow."""
        with self._lock:
            cur = self._latest.get("met", {}).get(key)
            prev = self._met_prev.get(key)
        if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)):
            return ""
        if cur - prev > deadband:
            return "\u2191"
        if prev - cur > deadband:
            return "\u2193"
        return ""

    def stillness_s(self) -> float | None:
        """Seconds since the head last moved more than 5 deg from its anchor.
        None before any motion sample: absent is not zero."""
        with self._lock:
            if self._last_move_t is None or not self._mot:
                return None
            return max(0.0, self._mot[-1][0] - self._last_move_t)

    def head_pose(self) -> dict[str, float] | None:
        """Latest yaw/pitch/roll (deg) + delta over the last ~0.5 s."""
        with self._lock:
            if not self._mot:
                return None
            t, yaw, pitch, roll = self._mot[-1]
            past = None
            for pt, py, pp, pr in reversed(self._mot):
                if t - pt >= 0.5:
                    past = (py, pp, pr)
                    break
            if past is None:
                past = (self._mot[0][1], self._mot[0][2], self._mot[0][3])
        return {
            "yaw": round(yaw, 2), "pitch": round(pitch, 2), "roll": round(roll, 2),
            "d_yaw": round(yaw - past[0], 2), "d_pitch": round(pitch - past[1], 2),
            "d_roll": round(roll - past[2], 2), "time": t,
        }

    def contact_quality(self) -> dict[str, Any] | None:
        """{channel: 0-4} + battery/signal from the latest dev sample."""
        with self._lock:
            dev = self._latest.get("dev")
        if not dev:
            return None
        cq = {ch: dev[ch] for ch in EPOCX_CHANNELS if ch in dev}
        out: dict[str, Any] = {"channels": cq}
        for k in ("battery", "batteryPercent", "signal"):
            if k in dev:
                out[k] = dev[k]
        if cq:
            out["overall"] = round(sum(cq.values()) / (4 * len(cq)), 3)  # 0..1
        return out

    def eeg_tail(self, seconds: float = 2.0) -> dict[str, Any]:
        """Raw eeg tail for the dashboard scope: {t: [...], channels: {ch: [...]}}."""
        with self._lock:
            n = int(EEG_RATE * seconds)
            rows = list(self._eeg)[-n:]
        if not rows:
            return {"t": [], "channels": {ch: [] for ch in EPOCX_CHANNELS}}
        ts = [r[0] for r in rows]
        arr = np.stack([r[1] for r in rows])
        return {
            "t": ts,
            "channels": {ch: arr[:, i].round(2).tolist() for i, ch in enumerate(EPOCX_CHANNELS)},
        }
