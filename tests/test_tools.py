"""@tool surface: reads the singleton, degrades gracefully, carries CQ everywhere."""

from __future__ import annotations

import asyncio

import pytest

import strands_emotiv.tools as bt
from strands_emotiv.events import EventEngine
from strands_emotiv.fake import FakeCortex
from strands_emotiv.state import BrainState
from strands_emotiv.types import EPOCX_CHANNELS, Sample


@pytest.fixture(autouse=True)
def fresh_singleton(monkeypatch):
    """Each test gets clean module state."""
    monkeypatch.setattr(bt, "STATE", BrainState())
    monkeypatch.setattr(bt, "ENGINE", EventEngine())
    monkeypatch.setattr(bt, "_stream_task", None)
    monkeypatch.setattr(bt, "_source_name", "none")
    yield


def call(t, **kw):
    """Invoke a @tool's underlying function regardless of wrapper style."""
    fn = getattr(t, "original_function", None) or getattr(t, "_tool_func", None) or t
    return fn(**kw)


async def prime(duration_s: float = 2.0, **kw):
    fake = FakeCortex(fixture=None, duration_s=duration_s, **kw)
    fake.fixture = None
    async for s in fake.samples():
        bt.feed(s)


# ------------------------------------------------------------------ empty
def test_tools_degrade_gracefully_when_empty():
    assert call(bt.brain_status)["usable"] is False
    assert "error" in call(bt.head_pose)
    assert "error" in call(bt.contact_quality)
    assert "error" in call(bt.brain_bands)
    assert call(bt.recent_brain_events)["events"] == []
    assert bt.ambient_line().startswith("[brain: not visible")


# ------------------------------------------------------------------ fed
async def test_snapshot_and_status_after_feed():
    await prime()
    st = call(bt.brain_status)
    assert st["usable"] is True
    assert set(st["streams_seen"]) == {"eeg", "mot", "fac", "met", "pow", "dev"}
    snap = call(bt.brain_snapshot)
    assert snap["ambient"].startswith("[brain: ")
    assert snap["contact_quality"]["good"] >= 12
    assert snap["pose"] is not None


async def test_brain_bands_fft_path_and_channel_arg():
    await prime()
    all_bands = call(bt.brain_bands)
    assert set(all_bands["bands"]) == set(EPOCX_CHANNELS)
    one = call(bt.brain_bands, channel="o1")
    assert one["channel"] == "O1" and "alpha" in one["bands"]
    bad = call(bt.brain_bands, channel="XX")
    assert "error" in bad and "O1" in bad["channels"]


def test_brain_bands_pow_fallback_without_eeg():
    """Basic-license reality: no raw eeg, bands come from the pow stream."""
    bt.feed(Sample(stream="pow", time=1.0,
                   data={f"{ch}/{b}": 1.5 for ch in EPOCX_CHANNELS
                         for b in ("theta", "alpha", "betaL", "betaH", "gamma")}))
    bands = call(bt.brain_bands)["bands"]
    assert bands["AF3"]["alpha"] == 1.5
    assert len(bands) == 14


async def test_ambient_line_contents():
    await prime()
    line = bt.ambient_line()
    assert line.startswith("[brain: ") and line.endswith("]")
    assert "focus" in line and "CQ" in line
    assert "dominant" in line  # synthetic alpha should dominate FFT bands


def test_ambient_line_refuses_without_cq():
    """Reading without contact quality is refused."""
    bt.feed(Sample(stream="met", time=1.0, data={"foc": 0.9}))
    assert bt.ambient_line().startswith("[brain: not visible")


async def test_recent_events_and_wait_for():
    # feed a blink through the engine
    bt.feed(Sample(stream="fac", time=1.0, data={"eyeAct": "neutral"}))
    bt.feed(Sample(stream="fac", time=1.1, data={"eyeAct": "blink"}))
    evs = call(bt.recent_brain_events, limit=5)["events"]
    assert [e["kind"] for e in evs] == ["blink"]
    got = call(bt.wait_for_brain_event, kind="blink", timeout_s=0.1)
    assert got.get("timeout") is True  # already consumed, only NEW events count

    async def wink_soon():
        await asyncio.sleep(0.05)
        bt.feed(Sample(stream="fac", time=2.0, data={"eyeAct": "winkL"}))

    task = asyncio.create_task(wink_soon())
    got = await asyncio.to_thread(lambda: call(bt.wait_for_brain_event, kind="wink_left", timeout_s=2.0))
    assert got["event"]["kind"] == "wink_left"
    await task


async def test_start_stream_with_explicit_source_and_stop():
    fake = FakeCortex(fixture=None, duration_s=0.5)
    fake.fixture = None
    name = await bt.start_stream(fake)
    assert name == "FakeCortex"
    await asyncio.sleep(0.1)  # non-realtime source finishes instantly
    assert call(bt.brain_status)["streams_seen"]
    await bt.stop_stream()
    assert bt._stream_task is None


async def test_start_stream_is_idempotent():
    fake = FakeCortex(fixture=None, duration_s=5.0, realtime=True)
    fake.fixture = None
    n1 = await bt.start_stream(fake)
    n2 = await bt.start_stream()  # second call: no new task
    assert n1 == n2 == "FakeCortex"
    await bt.stop_stream()


def test_all_tools_exported():
    names = {getattr(t, "tool_name", getattr(t, "__name__", "")) for t in bt.BRAIN_TOOLS}
    assert names == {"brain_line", "brain_snapshot", "brain_bands", "head_pose", "contact_quality",
                     "wait_for_brain_event", "recent_brain_events", "brain_status",
                     "mental_approval",
                     "record_start", "record_stop", "record_status", "record_publish"}


def test_mental_approval_degrades_without_relay(monkeypatch):
    monkeypatch.setenv("EMOTIV_RELAY", "http://127.0.0.1:1")  # nothing listens
    got = call(bt.mental_approval, question="deploy?", timeout_s=1)
    assert got["decision"] == "unavailable" and "relay" in got["error"]


def test_recorder_tools_degrade_without_relay(monkeypatch):
    monkeypatch.setenv("EMOTIV_RELAY", "http://127.0.0.1:1")  # nothing listens
    for fn, kw in ((bt.record_start, {"name": "x"}), (bt.record_stop, {}),
                   (bt.record_status, {}), (bt.record_publish, {})):
        got = call(fn, **kw)
        assert got["ok"] is False and "relay" in got["error"]


def test_recorder_tools_hit_the_right_rail(monkeypatch):
    """record_* must call the exact /api/dataset/* paths with the right verb."""
    seen: list[tuple[str, bytes | None]] = []

    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b'{"recording": true}'

    def fake_urlopen(req, timeout=0):
        seen.append((req.full_url, req.data))
        return _Resp()

    import urllib.request as rq
    monkeypatch.setattr(rq, "urlopen", fake_urlopen)
    call(bt.record_start, name="demo")
    call(bt.record_stop)
    call(bt.record_status)
    call(bt.record_publish)
    urls = [u for u, _ in seen]
    assert urls == ["http://127.0.0.1:8765/api/dataset/record/start",
                    "http://127.0.0.1:8765/api/dataset/record/stop",
                    "http://127.0.0.1:8765/api/dataset/status",
                    "http://127.0.0.1:8765/api/dataset/publish"]
    assert b'"demo"' in seen[0][1]      # name forwarded
    assert seen[2][1] is None           # status is a GET
