"""FakeCortex must look like a real EPOC X: rates, shapes, artifacts, gestures."""

from __future__ import annotations

import math

from strands_emotiv.fake import BANDS, MET_COLS, FakeCortex
from strands_emotiv.types import EPOCX_CHANNELS, Sample


async def collect(duration_s: float = 2.0, **kw) -> list[Sample]:
    fake = FakeCortex(fixture=None, duration_s=duration_s, **kw)
    fake.fixture = None  # never replay in unit tests, even if a fixture lands later
    return [s async for s in fake.samples()]


def by_stream(samples: list[Sample]) -> dict[str, list[Sample]]:
    out: dict[str, list[Sample]] = {}
    for s in samples:
        out.setdefault(s.stream, []).append(s)
    return out


async def test_rates_are_realistic():
    per = by_stream(await collect(2.0))
    assert 500 <= len(per["eeg"]) <= 524  # 256 Hz * 2 s
    assert 120 <= len(per["mot"]) <= 132  # 64 Hz
    assert 14 <= len(per["fac"]) <= 18  # 8 Hz
    assert 14 <= len(per["pow"]) <= 18
    assert 3 <= len(per["dev"]) <= 5  # 2 Hz
    assert len(per["met"]) >= 1  # 0.1 Hz → first sample at t=0


async def test_timestamps_monotonic_per_stream():
    per = by_stream(await collect(1.0))
    for stream, samples in per.items():
        times = [s.time for s in samples]
        assert times == sorted(times), f"{stream} not monotonic"


async def test_eeg_shape_and_baseline():
    per = by_stream(await collect(1.0))
    for s in per["eeg"]:
        assert set(s.data) == set(EPOCX_CHANNELS)
    o1 = [s.data["O1"] for s in per["eeg"]]
    mean = sum(o1) / len(o1)
    assert 4150 < mean < 4250  # ~4200 µV baseline
    assert max(o1) - min(o1) > 20  # alpha + noise actually move


async def test_blink_artifact_on_frontal_channels_only():
    samples = await collect(4.0, blink_every_s=3.0)
    per = by_stream(samples)
    t0 = per["eeg"][0].time
    in_blink = [s for s in per["eeg"] if 3.0 <= (s.time - t0) < 0.3 + 3.0]
    outside = [s for s in per["eeg"] if 1.0 <= (s.time - t0) < 2.0]
    assert in_blink and outside
    peak_af3 = max(s.data["AF3"] for s in in_blink)
    base_af3 = sum(s.data["AF3"] for s in outside) / len(outside)
    assert peak_af3 - base_af3 > 100  # big frontal deflection
    peak_o1 = max(s.data["O1"] for s in in_blink)
    base_o1 = sum(s.data["O1"] for s in outside) / len(outside)
    assert peak_o1 - base_o1 < 60  # occipital untouched


async def test_fac_reports_blink_during_artifact():
    samples = await collect(4.0, blink_every_s=3.0)
    per = by_stream(samples)
    t0 = per["fac"][0].time
    acts = {round(s.time - t0, 2): s.data["eyeAct"] for s in per["fac"]}
    blinks = [t for t, a in acts.items() if a == "blink"]
    assert blinks, f"no blink in fac: {acts}"
    assert all(3.0 <= t < 3.31 for t in blinks)


async def test_mot_quaternion_unit_norm_and_yaw_turn():
    samples = await collect(5.0, yaw_turn_at_s=(4.0,))
    per = by_stream(samples)
    t0 = per["mot"][0].time
    for s in per["mot"]:
        n = math.sqrt(sum(s.data[q] ** 2 for q in ("Q0", "Q1", "Q2", "Q3")))
        assert 0.98 < n < 1.02
    def yaw_deg(s: Sample) -> float:
        q0, q1, q2, q3 = (s.data[q] for q in ("Q0", "Q1", "Q2", "Q3"))
        return math.degrees(math.atan2(2 * (q0 * q3 + q1 * q2), 1 - 2 * (q2 ** 2 + q3 ** 2)))
    early = [yaw_deg(s) for s in per["mot"] if (s.time - t0) < 3.0]
    late = [yaw_deg(s) for s in per["mot"] if 4.7 <= (s.time - t0) < 5.0]
    assert max(abs(y) for y in early) < 5
    assert max(abs(y) for y in late) > 25  # the turn happened


async def test_met_pow_dev_shapes():
    per = by_stream(await collect(1.0))
    met = per["met"][0].data
    for m in MET_COLS:
        assert met[f"{m}.isActive"] is True
        assert 0.0 <= met[m] <= 1.0
    powd = per["pow"][0].data
    assert len(powd) == 14 * 5
    assert all(f"{ch}/{b}" in powd for ch in EPOCX_CHANNELS for b in BANDS)
    dev = per["dev"][0].data
    assert dev["battery"] == 82
    assert all(0 <= dev[ch] <= 4 for ch in EPOCX_CHANNELS)


async def test_deterministic_with_seed():
    a = await collect(0.5, seed=7)
    b = await collect(0.5, seed=7)
    assert [(s.stream, s.time, s.data) for s in a] == [(s.stream, s.time, s.data) for s in b]


async def test_replay_fixture(tmp_path):
    p = tmp_path / "fix.jsonl"
    p.write_text(
        '{"stream":"eeg","time":1.0,"data":{"AF3":4200.0}}\n'
        '{"stream":"fac","time":1.1,"data":{"eyeAct":"blink"}}\n'
    )
    fake = FakeCortex(fixture=p)
    got = [s async for s in fake.samples()]
    assert len(got) == 2
    assert got[0].stream == "eeg" and got[0].data["AF3"] == 4200.0
    assert got[1].data["eyeAct"] == "blink"


def test_fake_relay_client_ingests_like_the_wire():
    """dashboard --fake: FakeRelayClient shares CortexClient's _ingest path."""
    import asyncio

    from strands_emotiv.fake import FakeCortex, FakeRelayClient

    c = FakeRelayClient()
    assert c.state["connected"] is True
    assert c.state["headset"]["connectedBy"] == "fixture"

    async def pump(n_max: int = 300) -> int:
        n = 0
        async for s in FakeCortex().samples():
            c._ingest(s)
            n += 1
            if n >= n_max:
                break
        return n

    assert asyncio.run(pump()) > 0
    assert c.state["band_power"]          # pow flowed through _update_state
    assert c.state["contact_quality"]     # dev flowed too


def test_dashboard_fake_flag_parses():
    from strands_emotiv.cli import _parser

    args = _parser().parse_args(["dashboard", "--fake"])
    assert args.fake is True and args.cmd == "dashboard"
