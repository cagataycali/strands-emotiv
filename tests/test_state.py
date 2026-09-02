"""BrainState: ring buffers, FFT band power, head pose, contact quality."""

from __future__ import annotations

import math

import numpy as np

from strands_emotiv.fake import FakeCortex
from strands_emotiv.state import BAND_DEFS, EEG_RATE, BrainState, quat_to_euler_deg
from strands_emotiv.types import EPOCX_CHANNELS, Sample


async def fed_state(duration_s: float = 3.0, **kw) -> BrainState:
    st = BrainState()
    fake = FakeCortex(fixture=None, duration_s=duration_s, **kw)
    fake.fixture = None
    async for s in fake.samples():
        st.feed(s)
    return st


# ------------------------------------------------------------------ euler
def test_quat_roundtrip_yaw():
    for yaw_deg in (-90, -35, 0, 10, 45, 120):
        h = math.radians(yaw_deg) / 2
        y, p, r = quat_to_euler_deg(math.cos(h), 0.0, 0.0, math.sin(h))
        assert abs(y - yaw_deg) < 1e-6 and abs(p) < 1e-6 and abs(r) < 1e-6


def test_quat_pitch_clamped():
    # numerically slightly out-of-range asin input must not raise
    _, p, _ = quat_to_euler_deg(0.7071068, 0.0, 0.7071068, 0.0)
    assert abs(p - 90.0) < 0.01


# ------------------------------------------------------------------ feed/snapshot
async def test_snapshot_has_all_streams():
    st = await fed_state(2.0)
    snap = st.snapshot()
    assert set(snap["streams"]) == {"eeg", "mot", "fac", "met", "pow", "dev"}
    assert snap["eeg_buffer"] > 400
    assert snap["streams"]["eeg"]["count"] > 400
    assert snap["pose"] is not None
    assert snap["contact_quality"] is not None


def test_empty_state_is_graceful():
    st = BrainState()
    snap = st.snapshot()
    assert snap["streams"] == {} and snap["pose"] is None and snap["contact_quality"] is None
    assert st.band_power() == {}
    assert st.eeg_tail()["t"] == []


def test_feed_ignores_malformed_samples():
    st = BrainState()
    st.feed(Sample(stream="eeg", time=1.0, data={"AF3": "not-a-number"}))
    st.feed(Sample(stream="mot", time=1.0, data={"Q0": 1.0}))  # missing Q1..Q3
    assert st.snapshot()["eeg_buffer"] == 0
    assert st.head_pose() is None


# ------------------------------------------------------------------ band power
async def test_alpha_dominates_band_power():
    st = await fed_state(3.0, blink_every_s=1e9)  # no blink artifacts
    bands = st.band_power(window_s=2.0)
    assert set(bands) == set(EPOCX_CHANNELS)
    for ch in ("O1", "T7"):
        b = bands[ch]
        assert set(b) == set(BAND_DEFS)
        assert b["alpha"] > b["theta"] and b["alpha"] > b["beta"] and b["alpha"] > b["gamma"], (
            f"{ch}: alpha (10Hz injected) must dominate, got {b}"
        )


def test_band_power_pure_sine():
    st = BrainState()
    for i in range(EEG_RATE * 2):
        t = i / EEG_RATE
        v = 4200 + 50 * math.sin(2 * math.pi * 6.0 * t)  # 6 Hz = theta
        st.feed(Sample(stream="eeg", time=t, data={ch: v for ch in EPOCX_CHANNELS}))
    b = st.band_power(window_s=2.0)["AF3"]
    assert b["theta"] > 10 * max(b["alpha"], b["beta"], b["gamma"])


# ------------------------------------------------------------------ pose
async def test_head_pose_tracks_yaw_turn():
    st = await fed_state(5.0, yaw_turn_at_s=(4.0,))
    pose = st.head_pose()
    assert pose is not None
    assert abs(pose["yaw"]) > 25  # ended mid/after a 35° turn
    assert abs(pose["d_yaw"]) > 5  # and it moved within the last 0.5 s


# ------------------------------------------------------------------ contact quality
async def test_contact_quality_map():
    st = await fed_state(1.0)
    cq = st.contact_quality()
    assert cq is not None
    assert set(cq["channels"]) == set(EPOCX_CHANNELS)
    assert all(0 <= v <= 4 for v in cq["channels"].values())
    assert cq["battery"] == 82
    assert 0.0 <= cq["overall"] <= 1.0


# ------------------------------------------------------------------ eeg tail
async def test_eeg_tail_shape():
    st = await fed_state(2.0)
    tail = st.eeg_tail(seconds=1.0)
    n = len(tail["t"])
    assert 200 <= n <= 260
    assert set(tail["channels"]) == set(EPOCX_CHANNELS)
    assert all(len(v) == n for v in tail["channels"].values())
    assert tail["t"] == sorted(tail["t"])


# ------------------------------------------------------------------ ring bound
def test_ring_buffer_bounded():
    st = BrainState(eeg_window_s=1.0)
    data = {ch: 4200.0 for ch in EPOCX_CHANNELS}
    for i in range(EEG_RATE * 5):
        st.feed(Sample(stream="eeg", time=i / EEG_RATE, data=data))
    assert st.snapshot()["eeg_buffer"] == EEG_RATE  # capped at window
    assert isinstance(st.eeg_tail(0.5)["channels"]["AF3"][0], float)


def test_numpy_types_not_leaked():
    st = BrainState()
    for i in range(EEG_RATE):
        t = i / EEG_RATE
        st.feed(Sample(stream="eeg", time=t, data={ch: 4200.0 for ch in EPOCX_CHANNELS}))
    b = st.band_power(window_s=1.0)
    v = b["AF3"]["alpha"]
    assert isinstance(v, float) and not isinstance(v, np.floating)


def test_public_api_lazy_exports():
    """Every name in strands_emotiv.__all__ resolves; unknown names raise."""
    import pytest

    import strands_emotiv as se

    for name in se.__all__:
        assert getattr(se, name) is not None
    with pytest.raises(AttributeError):
        _ = se.nope


def test_version_matches_pyproject():
    """A tag release ships one version, not two."""
    import tomllib
    from pathlib import Path

    import strands_emotiv as se

    pyproject = Path(se.__file__).parent.parent / "pyproject.toml"
    with open(pyproject, "rb") as fh:
        assert tomllib.load(fh)["project"]["version"] == se.__version__


def test_met_merges_per_key_for_ambient_line():
    """exc/lex tick at 2 Hz, the rest every ~10 s: a partial met sample must not
    blank focus/stress/engagement from the store the ambient line reads."""
    st = BrainState()
    st.feed(Sample(stream="met", time=100.0, data={
        "foc": 0.7, "foc.isActive": True, "eng": 0.6, "eng.isActive": True,
        "str": 0.4, "str.isActive": True, "exc": 0.3, "exc.isActive": True,
    }))
    st.feed(Sample(stream="met", time=100.5, data={
        "exc": 0.9, "exc.isActive": True, "lex": 0.2,
        "str": float("nan"), "str.isActive": True,  # nan must not overwrite
        "eng": 0.99, "eng.isActive": False,         # inactive must not overwrite
    }))
    d = st.snapshot()["streams"]["met"]["data"]
    assert d["exc"] == 0.9 and d["lex"] == 0.2          # fresh
    assert d["foc"] == 0.7 and d["str"] == 0.4 and d["eng"] == 0.6  # kept


def test_metric_trend_and_stillness():
    st = BrainState()
    assert st.metric_trend("str") == ""          # no history, no arrow
    st.feed(Sample(stream="met", time=100.0, data={"str": 0.2, "str.isActive": True}))
    assert st.metric_trend("str") == ""          # one sample is not a trend
    st.feed(Sample(stream="met", time=110.0, data={"str": 0.4, "str.isActive": True}))
    assert st.metric_trend("str") == "\u2191"
    st.feed(Sample(stream="met", time=120.0, data={"str": 0.39, "str.isActive": True}))
    assert st.metric_trend("str") == ""          # inside the deadband
    assert st.stillness_s() is None              # no motion yet: absent, not zero
    q = {"Q0": 1.0, "Q1": 0.0, "Q2": 0.0, "Q3": 0.0}
    for i in range(20):
        st.feed(Sample(stream="mot", time=200.0 + i, data=q))
    assert st.stillness_s() == 19.0              # anchored at the first sample
    st.feed(Sample(stream="mot", time=220.0, data={"Q0": 0.7, "Q1": 0.0, "Q2": 0.7, "Q3": 0.0}))
    assert st.stillness_s() == 0.0               # a real turn resets the clock
