"""Layer 1 reflexes: every gesture fires exactly once, debounced, hysteretic."""

from __future__ import annotations

import asyncio
import math

from strands_emotiv.events import Event, EventEngine
from strands_emotiv.fake import FakeCortex
from strands_emotiv.types import Sample


def collector(engine: EventEngine, pattern: str = "*") -> list[Event]:
    got: list[Event] = []
    engine.subscribe(pattern, got.append)
    return got


def fac(t: float, eye: str = "neutral", **kw) -> Sample:
    data = {"eyeAct": eye, "uAct": "neutral", "uPow": 0.0, "lAct": "neutral", "lPow": 0.0}
    data.update(kw)
    return Sample(stream="fac", time=t, data=data)


def mot(t: float, yaw: float = 0.0, pitch: float = 0.0) -> Sample:
    cy, sy = math.cos(math.radians(yaw) / 2), math.sin(math.radians(yaw) / 2)
    cp, sp = math.cos(math.radians(pitch) / 2), math.sin(math.radians(pitch) / 2)
    return Sample(stream="mot", time=t, data={"Q0": cp * cy, "Q1": -sp * sy, "Q2": sp * cy, "Q3": cp * sy})


def met(t: float, **kw) -> Sample:
    return Sample(stream="met", time=t, data=kw)


def com(t: float, act: str = "neutral", pow: float = 0.0) -> Sample:
    return Sample(stream="com", time=t, data={"act": act, "pow": pow})


# ------------------------------------------------------------------- blink
def test_blink_fires_once_per_gesture():
    e = EventEngine()
    got = collector(e, "blink")
    # one blink = several consecutive 8Hz fac samples reporting "blink"
    for i, eye in enumerate(["neutral", "blink", "blink", "blink", "neutral", "neutral"]):
        e.feed(fac(i * 0.125, eye))
    assert len(got) == 1
    assert got[0].kind == "blink" and got[0].time == 0.125


def test_two_separated_blinks_fire_twice_and_double_blink_once():
    e = EventEngine()
    blinks = collector(e, "blink")
    doubles = collector(e, "double_blink")
    seq = [(0.0, "neutral"), (0.125, "blink"), (0.25, "neutral"),
           (0.625, "blink"), (0.75, "neutral")]  # gap 0.5 s → double
    for t, eye in seq:
        e.feed(fac(t, eye))
    assert len(blinks) == 2
    assert len(doubles) == 1
    assert doubles[0].detail["gap_s"] == 0.5


def test_slow_blinks_are_not_double_blink():
    e = EventEngine()
    doubles = collector(e, "double_blink")
    for t, eye in [(0.0, "blink"), (0.5, "neutral"), (1.5, "blink"), (2.0, "neutral"),
                   (3.0, "blink"), (3.5, "neutral")]:
        e.feed(fac(t, eye))
    assert doubles == []


def test_winks():
    e = EventEngine()
    got = collector(e)
    for t, eye in [(0.0, "neutral"), (0.125, "winkL"), (0.25, "winkL"), (0.375, "neutral"),
                   (1.0, "winkR"), (1.125, "neutral")]:
        e.feed(fac(t, eye))
    kinds = [ev.kind for ev in got]
    assert kinds == ["wink_left", "wink_right"]


def test_clench_needs_power():
    e = EventEngine()
    got = collector(e, "clench")
    e.feed(fac(0.0, "neutral", lAct="clench", lPow=0.1))  # too weak
    e.feed(fac(0.125, "neutral", lAct="clench", lPow=0.6))
    e.feed(fac(0.25, "neutral", lAct="clench", lPow=0.7))  # debounced
    e.feed(fac(2.0, "neutral", lAct="clench", lPow=0.8))  # new gesture
    assert len(got) == 2
    assert got[0].time == 0.125 and got[0].detail["pow"] == 0.6


# ------------------------------------------------------------------- head
def feed_turn(e: EventEngine, t0: float, deg: float, dur: float = 0.5, rate: int = 64):
    n = int(dur * rate)
    for i in range(n + 1):
        e.feed(mot(t0 + i / rate, yaw=deg * i / n))


def test_head_turn_left_once():
    e = EventEngine()
    got = collector(e, "*")
    for i in range(64):  # 1 s still
        e.feed(mot(i / 64))
    feed_turn(e, 1.0, +35.0)
    for i in range(64):  # hold the pose
        e.feed(mot(1.5 + i / 64, yaw=35.0))
    turns = [ev for ev in got if ev.kind.startswith("head_turn")]
    assert len(turns) == 1
    assert turns[0].kind == "head_turn_left"
    assert turns[0].detail["d_yaw"] >= 20


def test_head_turn_right():
    e = EventEngine()
    got = collector(e, "head_turn_right")
    feed_turn(e, 0.0, -30.0)
    assert len(got) == 1


def test_slow_drift_is_not_a_turn():
    e = EventEngine()
    got = collector(e, "*")
    # 35° but over 10 s: a posture change, not a gesture
    for i in range(640):
        e.feed(mot(i / 64, yaw=35.0 * i / 640))
    assert [ev for ev in got if ev.kind.startswith("head_turn")] == []


def test_nod_fires_once():
    e = EventEngine()
    got = collector(e, "nod")
    t = 0.0
    for i in range(64):
        e.feed(mot(t := i / 64, pitch=0.0))
    # dip down 15° over 0.3 s, back up over 0.3 s
    for i in range(20):
        e.feed(mot(t + (i + 1) / 64, pitch=-15.0 * (i + 1) / 20))
    t += 20 / 64
    for i in range(20):
        e.feed(mot(t + (i + 1) / 64, pitch=-15.0 * (19 - i) / 20))
    assert len(got) == 1
    assert got[0].detail["dur_s"] < 1.0


def test_look_down_held_fires_once_and_rearms():
    e = EventEngine()
    got = collector(e, "look_down")
    for i in range(32):
        e.feed(mot(i / 64, pitch=0.0))
    for i in range(128):  # look down and HOLD 2 s
        e.feed(mot(0.5 + i / 64, pitch=-25.0))
    for i in range(64):  # back to level
        e.feed(mot(2.5 + i / 64, pitch=0.0))
    for i in range(64):  # down again
        e.feed(mot(3.5 + i / 64, pitch=-25.0))
    assert len(got) == 2  # once per look, not per sample


# ------------------------------------------------------------------- met
def test_focus_hysteresis():
    e = EventEngine()
    got = collector(e, "*")
    vals = [0.5, 0.72, 0.75, 0.68, 0.65, 0.55, 0.73, 0.2, 0.35, 0.28, 0.45, 0.25]
    for i, v in enumerate(vals):
        e.feed(met(float(i * 10), foc=v))
    kinds = [ev.kind for ev in got]
    # 0.72 arms high; 0.73 must NOT re-fire (never dropped below 0.60);
    # wait: 0.55 < 0.60 re-arms → 0.73 fires again. Then lows: 0.2 fires,
    # 0.35 no re-arm (<0.40), 0.28 no, 0.45 re-arms, 0.25 fires.
    assert kinds == ["focus_high", "focus_high", "focus_low", "focus_low"]


def test_stress_high_once_until_rearmed():
    e = EventEngine()
    got = collector(e, "stress_high")
    for i, v in enumerate([0.3, 0.75, 0.8, 0.72, 0.5, 0.9]):
        e.feed(met(float(i * 10), str=v))
    assert len(got) == 2  # 0.75 fires; 0.5 re-arms; 0.9 fires


# ------------------------------------------------------------------- com
def test_command_gated_and_debounced():
    e = EventEngine()
    got = collector(e, "command:*")
    e.feed(com(0.0, "push", 0.2))  # below pow gate
    e.feed(com(0.1, "push", 0.6))  # fires
    e.feed(com(0.3, "push", 0.9))  # inside debounce
    e.feed(com(1.5, "push", 0.8))  # fires
    e.feed(com(2.8, "pull", 0.5))  # different act still debounced by com gate
    assert [ev.kind for ev in got] == ["command:push", "command:push", "command:pull"]
    assert got[0].detail["pow"] == 0.6


def test_neutral_never_fires():
    e = EventEngine()
    got = collector(e, "*")
    for i in range(20):
        e.feed(com(i * 0.125, "neutral", 1.0))
    assert got == []


# ------------------------------------------------------------------- async
async def test_wait_for_receives_event():
    e = EventEngine()

    async def blink_soon():
        await asyncio.sleep(0.01)
        e.feed(fac(1.0, "blink"))

    task = asyncio.create_task(blink_soon())
    ev = await e.wait_for("blink", timeout_s=1.0)
    assert ev.kind == "blink" and ev.time == 1.0
    await task


async def test_wait_for_times_out():
    e = EventEngine()
    with __import__("pytest").raises(asyncio.TimeoutError):
        await e.wait_for("nod", timeout_s=0.05)


# ------------------------------------------------------------ integration
async def test_fake_cortex_synthetic_end_to_end():
    e = EventEngine()
    got = collector(e, "*")
    fake = FakeCortex(fixture=None, duration_s=4.0, blink_every_s=3.0, yaw_turn_at_s=())
    fake.fixture = None
    async for s in fake.samples():
        e.feed(s)
    blinks = [ev for ev in got if ev.kind == "blink"]
    assert len(blinks) == 1  # one 300 ms artifact at t=3 → exactly one event
    assert [ev for ev in got if ev.kind.startswith("head_turn")] == []


async def test_real_fixture_replay_produces_no_false_gestures():
    """The live 10s capture (sitting still at the desk) must not hallucinate
    Layer-1 gestures; smooth idle head motion may trigger at most a couple."""
    fix = FakeCortex.DEFAULT_FIXTURE
    if not fix.exists():
        __import__("pytest").skip("live fixture not captured yet")
    e = EventEngine()
    got = collector(e, "*")
    fake = FakeCortex(fixture=fix)
    async for s in fake.samples():
        e.feed(s)
    gestures = [ev for ev in got if ev.kind in ("head_turn_left", "head_turn_right", "nod", "clench")]
    assert len(gestures) <= 2, f"idle recording fired {[g.as_dict() for g in gestures]}"
    assert e.recent(5) == got[-5:] if got else e.recent(5) == []
