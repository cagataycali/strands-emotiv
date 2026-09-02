"""Recorder unit tests: synthetic sample clock + fake agent turn (no Cortex, no writer)."""

from __future__ import annotations

import math

import numpy as np

from strands_emotiv.bus import Bus
from strands_emotiv.events import Event
from strands_emotiv.recorder import (
    AMBIENT_TICKS,
    EVENT_SLOTS,
    FPS,
    POST_ROLL_TICKS,
    POW_KEYS,
    Recorder,
)
from strands_emotiv.types import EPOCX_CHANNELS, Sample

T0 = 1_000_000.0  # synthetic epoch


def pow_sample(t: float, val: float = 1.0) -> Sample:
    return Sample(stream="pow", time=t, data={k: val for k in POW_KEYS})


def dev_sample(t: float) -> Sample:
    return Sample(stream="dev", time=t, data={ch: 4 for ch in EPOCX_CHANNELS})


def met_sample(t: float, stress: float, eng: float) -> Sample:
    return Sample(stream="met", time=t, data={
        "attention": 0.5, "attention.isActive": True,
        "eng": eng, "eng.isActive": True,
        "exc": 0.3, "exc.isActive": True, "lex": 0.3,
        "str": stress, "str.isActive": True,
        "rel": 0.5, "rel.isActive": True,
        "int": 0.6, "int.isActive": True,
    })


def drive(rec: Recorder, t0: float, seconds: float, stress: float = 0.4, eng: float = 0.6) -> float:
    """Feed pow@8Hz + dev + met from t0 for `seconds`; returns the end time."""
    n = int(seconds * FPS)
    for i in range(n):
        t = t0 + i / FPS
        rec.on_sample(pow_sample(t))
        rec.on_sample(dev_sample(t))
        rec.on_sample(met_sample(t, stress, eng))
    return t0 + n / FPS


def test_tick_clock_and_shapes():
    rec = Recorder()
    rec.start_ambient()
    drive(rec, T0, 2.0)
    # ticks flush on the NEXT sample crossing the boundary: 2s drive ⇒ ≥15 frames in ring/ambient
    assert rec._ambient_frames, "ambient frames should accumulate"
    f = rec._ambient_frames[0]
    assert f["observation.state"].shape == (70,) and f["observation.state"].dtype == np.float32
    assert f["observation.contact_quality"].shape == (14,)
    assert (f["observation.contact_quality"] == 4.0).all()
    assert f["observation.motion"].shape == (10,)
    assert f["observation.motion_valid"][0] == 0.0  # no mot samples fed
    assert f["observation.metrics"].shape == (7,)
    assert f["observation.metrics_valid"].sum() == 7.0
    assert f["observation.events"].shape == (12,)
    assert f["action"].shape == (4,)
    assert f["task"].startswith("TASK: idle | AMBIENT:")


def test_metrics_absent_is_minus_one():
    rec = Recorder()
    rec.start_ambient()
    rec.on_sample(pow_sample(T0))
    rec.on_sample(Sample(stream="met", time=T0, data={"eng": 0.7, "eng.isActive": True}))
    rec.on_sample(pow_sample(T0 + 1 / FPS))  # flush
    f = rec._ambient_frames[0]
    m, v = f["observation.metrics"], f["observation.metrics_valid"]
    assert m[1] == np.float32(0.7) and v[1] == 1.0
    assert m[0] == -1.0 and v[0] == 0.0  # attention absent


def test_events_multi_hot_and_command_mapping():
    rec = Recorder()
    rec.start_ambient()
    rec.on_sample(pow_sample(T0))
    rec.on_event(Event(kind="blink", time=T0))
    rec.on_event(Event(kind="command:push", time=T0))
    rec.on_event(Event(kind="look_up", time=T0))  # not in vocab, ignored
    rec.on_sample(pow_sample(T0 + 1 / FPS))
    ev = rec._ambient_frames[0]["observation.events"]
    assert ev[EVENT_SLOTS.index("blink")] == 1.0
    assert ev[EVENT_SLOTS.index("command")] == 1.0
    assert ev.sum() == 2.0


def test_turn_episode_boundaries_and_reward():
    rec = Recorder()
    rec.start()
    bus = Bus()
    bus.subscribe(rec.on_agent)

    t = drive(rec, T0, 4.0, stress=0.5, eng=0.5)          # pre-roll material
    bus.publish({"type": "turn_start", "q": "how do I seem?", "ambient": "[brain: focus 0.5]", "t": t})
    bus.publish({"type": "delta", "text": "You look focused.", "t": t})
    bus.publish({"type": "tool", "tool": "brain_snapshot", "t": t})
    t = drive(rec, t, 2.0, stress=0.5, eng=0.5)           # the turn itself
    bus.publish({"type": "turn_end", "text": "You look focused. Engagement is rising.", "t": t})
    t = drive(rec, t, 13.0, stress=0.3, eng=0.8)          # post-roll: calmer + more engaged

    assert rec.episodes_total == 1
    ep = rec.episodes[0]
    assert ep["kind"] == "turn"
    frames = ep["frames"]
    # pre-roll present (3 s = 24 ticks) + turn + 5 s post-roll
    assert len(frames) >= 24 + POST_ROLL_TICKS
    tasks = {f["task"] for f in frames}
    turn_tasks = [x for x in tasks if x.startswith("TASK: how do I seem?")]
    assert turn_tasks, tasks
    sample = turn_tasks[0]
    assert "AMBIENT:" in sample and "TOOL: brain_snapshot" in sample and "ACT: You look focused." in sample
    # REWARD back-filled everywhere, no 'pending' left
    assert all("REWARD:" in f["task"] and "pending" not in f["task"] for f in frames)
    m = next(f["task"] for f in frames if "Δstress=" in f["task"])
    ds = float(m.split("Δstress=")[1].split(",")[0])
    de = float(m.split("Δengagement=")[1])
    assert ds < 0 and de > 0  # calmer + more engaged after the answer
    # action bits fired during the turn
    assert any(f["action"][0] == 1.0 for f in frames)  # spoke
    assert any(f["action"][1] == 1.0 for f in frames)  # tool_called


def test_ambient_episode_is_30s():
    rec = Recorder()
    rec.start_ambient()
    drive(rec, T0, 31.0)
    assert rec.episodes_total == 1
    ep = rec.episodes[0]
    assert ep["kind"] == "ambient" and len(ep["frames"]) == AMBIENT_TICKS
    dur = ep["t_end"] - ep["t_start"]
    assert math.isclose(dur, (AMBIENT_TICKS - 1) / FPS, abs_tol=1e-6)


def test_stop_mid_turn_cuts_episode():
    rec = Recorder()
    rec.start()
    drive(rec, T0, 1.0)
    rec.on_agent({"type": "turn_start", "q": "hi", "ambient": ""})
    drive(rec, T0 + 1.0, 1.0)
    rec.stop()
    assert rec.episodes_total == 1
    assert rec.episodes[0].get("aborted") is True
    assert rec.status()["recording"] is False


def test_bus_isolation():
    bus = Bus()
    hits = []
    bus.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe(hits.append)
    bus.publish({"type": "turn_start"})
    assert hits and hits[0]["type"] == "turn_start"  # broken sub didn't break the bus


def test_reward_at_real_met_cadence():
    """met arrives every ~10 s on the Basic license (measured). The reward
    must still compute: last sample before turn end vs first NEW one after."""
    rec = Recorder()
    rec.start()
    bus = Bus()
    bus.subscribe(rec.on_agent)

    def drive_sparse(t0: float, seconds: float, stress: float, eng: float) -> float:
        n = int(seconds * FPS)
        for i in range(n):
            t = t0 + i / FPS
            rec.on_sample(pow_sample(t))
            if math.isclose((t - T0) % 10.0, 0.0, abs_tol=1e-6):  # met every 10 s
                rec.on_sample(met_sample(t, stress, eng))
        return t0 + n / FPS

    t = drive_sparse(T0, 4.0, stress=0.6, eng=0.4)        # met lands at T0 only
    bus.publish({"type": "turn_start", "q": "ok?", "ambient": "[brain: ...]", "t": t})
    t = drive_sparse(t, 2.0, stress=0.6, eng=0.4)
    bus.publish({"type": "turn_end", "text": "done", "t": t})
    t = drive_sparse(t, 13.0, stress=0.2, eng=0.9)        # next met lands at T0+10 (post-roll)

    assert rec.episodes_total == 1
    task = next(f["task"] for f in rec.episodes[0]["frames"] if "Δstress=" in f["task"])
    ds = float(task.split("Δstress=")[1].split(",")[0])
    de = float(task.split("Δengagement=")[1])
    assert ds < 0 and de > 0  # measured across the boundary, not nan


def test_reward_nan_when_no_new_met_arrives():
    """If the post-roll ends before the next met sample, reward is honestly nan."""
    rec = Recorder()
    rec.start()
    bus = Bus()
    bus.subscribe(rec.on_agent)

    def drive_pow_only(t0: float, seconds: float) -> float:
        n = int(seconds * FPS)
        for i in range(n):
            rec.on_sample(pow_sample(t0 + i / FPS))
        return t0 + n / FPS

    rec.on_sample(met_sample(T0, 0.5, 0.5))               # one met sample, then silence
    t = drive_pow_only(T0, 4.0)
    bus.publish({"type": "turn_start", "q": "ok?", "ambient": "[brain: ...]", "t": t})
    t = drive_pow_only(t, 2.0)
    bus.publish({"type": "turn_end", "text": "done", "t": t})
    drive_pow_only(t, 13.0)

    assert rec.episodes_total == 1
    task = next(f["task"] for f in rec.episodes[0]["frames"] if "REWARD:" in f["task"])
    assert "Δstress=nan" in task and "Δengagement=nan" in task
