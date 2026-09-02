"""ECoT episode recorder.

Assembles fps=8 frames from the same fan-out server.py already feeds (Sample,
Event, agent turn events via strands_emotiv.bus) and cuts them into episodes;
it never opens its own Cortex connection. Tick boundaries are derived from
sample time, never wall time, so fixture replays are deterministic. A turn
episode starts 3 s before turn_start (ring buffer) and ends 5 s after turn_end
so the reward (delta stress/engagement over the next 2 s) can be back-filled
into every frame's task string; ambient episodes are fixed 30 s. Writing the
finished episode dicts as LeRobot v3.0 parquet is dataset_v3.py's job (the
`writer` is pluggable, so tests can capture episodes raw).
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from .events import Event
from .types import EPOCX_CHANNELS, Sample

log = logging.getLogger(__name__)

FPS = 8
TICK_S = 1.0 / FPS
BANDS = ("theta", "alpha", "betaL", "betaH", "gamma")
POW_KEYS = tuple(f"{ch}/{band}" for ch in EPOCX_CHANNELS for band in BANDS)  # [70], ch-major
MOT_KEYS = ("Q0", "Q1", "Q2", "Q3", "ACCX", "ACCY", "ACCZ", "MAGX", "MAGY", "MAGZ")
# fixed feature order; values are the Cortex met keys
MET_KEYS = ("attention", "eng", "exc", "lex", "str", "rel", "int")
EYE_VOCAB = ("neutral", "blink", "winkL", "winkR")
LOWER_VOCAB = ("neutral", "smile", "clench", "frown", "laugh", "smirkLeft", "smirkRight")
EVENT_SLOTS = (
    "blink", "wink_left", "wink_right", "head_turn_left", "head_turn_right",
    "nod", "clench", "smile", "focus_high", "focus_low", "stress_high", "command",
)
MOTION_STALE_S = 0.5
PRE_ROLL_TICKS = 3 * FPS       # 3 s before the user message
POST_ROLL_TICKS = 12 * FPS     # after the agent finishes: > the 10 s met cadence,
                               # so the post-roll holds at least one NEW met sample
MET_STALE_S = 15.0             # met arrives ~every 10 s (measured); older = invalid
AMBIENT_TICKS = 30 * FPS       # 30 s idle episodes
ACT_CHARS = 200
TOKENS_NORM = 2000.0           # crude char-based norm for turn_length_tokens_norm


def _event_slot(kind: str) -> int | None:
    if kind == "double_blink":
        kind = "blink"
    elif kind.startswith("command:"):
        kind = "command"
    try:
        return EVENT_SLOTS.index(kind)
    except ValueError:
        return None  # look_up/look_down etc. are not in the vocab, by design


class _Tick:
    """Accumulator for one 125 ms slot."""

    __slots__ = ("events", "index", "marker", "spoke", "tool")

    def __init__(self, index: int) -> None:
        self.index = index
        self.events = np.zeros(len(EVENT_SLOTS), dtype=np.float32)
        self.spoke = 0.0
        self.tool = 0.0
        self.marker = 0.0


class Recorder:
    """Assembles fps=8 frames from the live fan-out and cuts episodes.

    writer: callable(episode: dict) -> None, invoked with a finished episode:
        {"frames": [frame_dict, ...], "kind": "turn"|"ambient", "task": str,
         "t_start": float, "t_end": float}
    Each frame_dict carries every feature key + "task" (ECoT string), exactly
    what LeRobotDataset.add_frame wants, minus bookkeeping the writer adds.
    """

    def __init__(
        self,
        dataset_root: str | None = None,
        repo_id: str = "cagataydev/emotiv-ecot",
        writer: Callable[[dict], None] | None = None,
    ) -> None:
        self.dataset_root = dataset_root
        self.repo_id = repo_id
        self.writer = writer
        self.episodes: list[dict] = []  # kept when writer is None (tests)
        self._lock = threading.RLock()

        self.recording = False          # master switch (REC button)
        self._latest: dict[str, tuple[float, dict]] = {}  # stream -> (t, data)
        self._tick: _Tick | None = None
        self._tick_t0: float | None = None
        self._met: dict[int, tuple[float, float]] = {}  # metric idx -> (t, value)
        self._ring: list[dict] = []     # last PRE_ROLL_TICKS finished frames

        # turn state
        self._turn: dict[str, Any] | None = None  # {"q","ambient","text","tool","frames",...}
        self._post_left = 0             # ticks still to record after turn_end
        self._ambient_left = 0          # ticks left in the current ambient episode
        self._ambient_frames: list[dict] = []

        self.frames_total = 0
        self.episodes_total = 0

    # inputs (mirror server.py's fan-out)

    def on_sample(self, s: Sample) -> None:
        with self._lock:
            tick_idx = math.floor(s.time * FPS)
            if self._tick is None:
                self._tick = _Tick(tick_idx)
            elif tick_idx > self._tick.index:
                if tick_idx - self._tick.index > 2 * FPS:
                    # >2 s of silence = a clock reset (stream restart), not missing ticks:
                    # flush the tick we have, do NOT fabricate repeated frames for the gap
                    self._flush_tick(self._tick.index)
                else:
                    # small gaps = repeated frames, keeps the fps clock continuous
                    for idx in range(self._tick.index, tick_idx):
                        self._flush_tick(idx)
                self._tick = _Tick(tick_idx)
            self._latest[s.stream] = (s.time, s.data)
            if s.stream == "met":
                for i, k in enumerate(MET_KEYS):
                    v = s.data.get(k)
                    active = s.data.get(f"{k}.isActive", True)
                    if v is not None and active and not (isinstance(v, float) and math.isnan(v)):
                        self._met[i] = (s.time, float(v))

    def on_event(self, e: Event) -> None:
        with self._lock:
            slot = _event_slot(e.kind)
            if slot is not None and self._tick is not None:
                self._tick.events[slot] = 1.0

    def on_agent(self, ev: dict) -> None:
        with self._lock:
            t = ev.get("type")
            if t == "turn_start":
                if self._turn is not None:
                    self._end_turn(aborted=True)
                self._turn = {
                    "q": ev.get("q") or "ambient",
                    "ambient": ev.get("ambient") or "",
                    "text": "",
                    "tool": "none",
                    "frames": list(self._ring),  # 3 s pre-roll, copied
                    "ended": False,
                }
            elif self._turn is None:
                return
            elif t == "delta":
                txt = ev.get("text") or ""
                # accept either accumulated text or increments
                self._turn["text"] = txt if txt.startswith(self._turn["text"]) or len(txt) > len(self._turn["text"]) else self._turn["text"] + txt
                if self._tick is not None:
                    self._tick.spoke = 1.0
            elif t == "tool":
                self._turn["tool"] = ev.get("tool") or "tool"
                if self._tick is not None:
                    self._tick.tool = 1.0
            elif t == "marker":
                if self._tick is not None:
                    self._tick.marker = 1.0
            elif t == "turn_end":
                if ev.get("text"):
                    self._turn["text"] = ev["text"]
                self._turn["ended"] = True
                self._post_left = POST_ROLL_TICKS

    # controls

    def start(self) -> None:
        with self._lock:
            self.recording = True
            self._tick = None  # fresh clock: never inherit gap-fill across sessions

    def stop(self) -> None:
        with self._lock:
            if self._turn is not None:
                self._end_turn(aborted=not self._turn.get("ended", False))
            # partial ambient tail: keep if ≥2 s (useful baseline), else discard
            if self._ambient_left and len(self._ambient_frames) >= 2 * FPS:
                self._cut_ambient()
            self._ambient_frames = []
            self._ambient_left = 0
            self.recording = False

    def start_ambient(self) -> None:
        """Begin cutting fixed 30 s idle episodes (until stop())."""
        with self._lock:
            self.recording = True
            self._tick = None  # fresh clock: never inherit gap-fill across sessions
            self._ambient_left = AMBIENT_TICKS
            self._ambient_frames = []

    # tick machinery

    def _flush_tick(self, idx: int) -> None:
        tick = self._tick if (self._tick and self._tick.index == idx) else _Tick(idx)
        frame = self._assemble(tick, idx / FPS)
        # ring buffer always runs, even when not recording (the 3 s pre-roll)
        self._ring.append(frame)
        if len(self._ring) > PRE_ROLL_TICKS:
            self._ring.pop(0)
        if not self.recording:
            return
        if self._turn is not None:
            self._turn["frames"].append(frame)
            if self._turn.get("ended"):
                self._post_left -= 1
                if self._post_left <= 0:
                    self._end_turn()
        elif self._ambient_left > 0:
            self._ambient_frames.append(frame)
            self._ambient_left -= 1
            if self._ambient_left == 0:
                self._cut_ambient()
                self._ambient_left = AMBIENT_TICKS  # keep cutting until stop()

    def _assemble(self, tick: _Tick, t: float) -> dict:
        latest = self._latest
        _, pow_d = latest.get("pow", (0.0, {}))
        state = np.array([pow_d.get(k, 0.0) for k in POW_KEYS], dtype=np.float32)

        _, dev_d = latest.get("dev", (0.0, {}))
        cq = np.array([dev_d.get(ch, 0.0) for ch in EPOCX_CHANNELS], dtype=np.float32)

        mot_t, mot_d = latest.get("mot", (0.0, {}))
        mot_ok = bool(mot_d) and (t - mot_t) <= MOTION_STALE_S
        motion = (
            np.array([mot_d.get(k, 0.0) for k in MOT_KEYS], dtype=np.float32)
            if mot_ok else np.zeros(10, dtype=np.float32)
        )

        metrics = np.full(7, -1.0, dtype=np.float32)
        metrics_valid = np.zeros(7, dtype=np.float32)
        for i, (mt, mv) in self._met.items():
            if t - mt <= MET_STALE_S:
                metrics[i] = mv
                metrics_valid[i] = 1.0

        _, fac_d = latest.get("fac", (0.0, {}))
        eye = str(fac_d.get("eyeAct", "neutral"))
        facial = np.zeros(6, dtype=np.float32)
        if eye in EYE_VOCAB:
            facial[EYE_VOCAB.index(eye)] = 1.0
        facial[4] = float(fac_d.get("uPow", 0.0))
        facial[5] = float(fac_d.get("lPow", 0.0))
        lower = str(fac_d.get("lAct", "neutral"))
        facial_label = np.array(
            [EYE_VOCAB.index(eye) if eye in EYE_VOCAB else -1.0,
             LOWER_VOCAB.index(lower) if lower in LOWER_VOCAB else -1.0],
            dtype=np.float32,
        )
        if lower == "smile":
            tick.events[EVENT_SLOTS.index("smile")] = 1.0

        turn = self._turn
        text = turn["text"] if turn else ""
        action = np.array(
            [tick.spoke, tick.tool, tick.marker, min(len(text) / TOKENS_NORM, 1.0)],
            dtype=np.float32,
        )

        return {
            "observation.state": state,
            "observation.contact_quality": cq,
            "observation.motion": motion,
            "observation.motion_valid": np.array([1.0 if mot_ok else 0.0], dtype=np.float32),
            "observation.metrics": metrics,
            "observation.metrics_valid": metrics_valid,
            "observation.facial": facial,
            "observation.facial_label": facial_label,
            "observation.events": tick.events.copy(),
            "action": action,
            "task": self._ecot(turn, text, tick),
            "_t": t,  # sample-clock time; writer converts to per-episode timestamp
            # (stress, its sample time, engagement, its sample time) for REWARD
            "_met": (*self._met.get(4, (0.0, -1.0))[::-1], *self._met.get(1, (0.0, -1.0))[::-1]),
        }

    def _ecot(self, turn: dict | None, text: str, tick: _Tick) -> str:
        from . import tools  # late import; ambient_line needs live STATE

        try:
            ambient = tools.ambient_line()
        except Exception:
            ambient = (turn or {}).get("ambient", "") or "[brain: unavailable]"
        if turn is None:
            return f"TASK: idle | AMBIENT: {ambient}"
        plan = (text.split(". ")[0][:120] + ".") if text else "…"
        return (
            f"TASK: {turn['q']} | AMBIENT: {turn.get('ambient') or ambient} | "
            f"PLAN: {plan} | TOOL: {turn['tool']} | ACT: {text[:ACT_CHARS]} | REWARD: pending"
        )

    # episode cutting

    def _end_turn(self, aborted: bool = False) -> None:
        turn, self._turn = self._turn, None
        self._post_left = 0
        if not turn or not turn["frames"] or (not self.recording and not turn.get("ended")):
            return
        frames = turn["frames"]
        n_post = min(POST_ROLL_TICKS, len(frames)) if turn.get("ended") else 0
        reward = self._reward(frames, n_post)
        for f in frames:
            f["task"] = f["task"].replace("REWARD: pending", f"REWARD: {reward}")
            if "REWARD:" not in f["task"]:  # pre-roll idle frames
                f["task"] = f"{f['task']} | REWARD: {reward}"
        self._emit({"frames": frames, "kind": "turn", "task": turn["q"],
                    "aborted": aborted, "t_start": frames[0]["_t"], "t_end": frames[-1]["_t"]})

    def _cut_ambient(self) -> None:
        frames, self._ambient_frames = self._ambient_frames, []
        if not frames:
            return
        self._emit({"frames": frames, "kind": "ambient", "task": "idle",
                    "t_start": frames[0]["_t"], "t_end": frames[-1]["_t"]})

    def _reward(self, frames: list[dict], n_post: int) -> str:
        """Δstress, Δengagement across the turn boundary: the last met sample
        before the turn ends vs the first genuinely NEW sample after it.
        met ticks ~every 10 s (measured), so windowed means see zero samples;
        nearest-new-sample is the honest form. nan = the post-roll ended
        before the next met sample arrived, i.e. unmeasured, not zero."""
        def delta(vi: int, ti: int) -> float:
            pre = next((f["_met"][vi]
                        for f in reversed(frames[:end]) if f["_met"][vi] >= 0), None)
            if pre is None:
                return float("nan")
            post = next((f["_met"][vi] for f in frames[end:]
                         if f["_met"][vi] >= 0 and f["_met"][ti] > t_bound), None)
            return float("nan") if post is None else post - pre

        if n_post <= 0 or len(frames) <= n_post:
            return "Δstress=nan,Δengagement=nan"
        end = len(frames) - n_post
        t_bound = frames[end]["_t"]  # samples older than the boundary tick are turn material
        ds, de = delta(0, 1), delta(2, 3)
        fmt = lambda v: f"{v:+.3f}" if not math.isnan(v) else "nan"  # noqa: E731
        return f"Δstress={fmt(ds)},Δengagement={fmt(de)}"

    def _emit(self, episode: dict) -> None:
        self.episodes_total += 1
        self.frames_total += len(episode["frames"])
        if self.writer is not None:
            try:
                self.writer(episode)
            except Exception:
                log.exception("episode writer failed; keeping episode in memory")
                self.episodes.append(episode)
        else:
            self.episodes.append(episode)

    # introspection (dataset_api)

    def status(self) -> dict:
        with self._lock:
            return {
                "recording": self.recording,
                "in_turn": self._turn is not None,
                "episodes": self.episodes_total,
                "frames": self.frames_total,
                "repo_id": self.repo_id,
                "root": self.dataset_root,
            }
