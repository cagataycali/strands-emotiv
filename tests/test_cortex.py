"""CortexClient tests against the wire-level fake (see conftest.py).

Covers the documented flow, the measured -32152 behaviour (usb cable =
charge-only), wait_ready, exponential backoff, cols→dict Sample fan-out and
warning surfacing. No CortexService, no hardware.
"""

from __future__ import annotations

import asyncio

import pytest

from strands_emotiv.cortex import (
    HEADSET_NOT_READY,
    CortexClient,
    CortexError,
    headset_usable,
)
from strands_emotiv.types import EPOCX_CHANNELS, Sample
from tests.conftest import COLS, FIXTURES, HEADSET_ID, CortexWire


def end_of_tape(client: CortexClient) -> asyncio.Event:
    """Register BEFORE run(): resolves when the end-of-tape sys marker lands
    (replay starts during subscribe, so a late listener would miss it)."""
    done = asyncio.Event()

    def spot(s: Sample):
        if s.stream == "sys":
            done.set()

    client._sample_listeners.append(spot)
    return done


# ---------- readiness: the measured ground truth ----------

def test_usb_cable_is_not_usable():
    h = {"status": "connected", "connectedBy": "usb cable", "sensors": []}
    assert not headset_usable(h)


def test_dongle_is_usable():
    h = {"status": "connected", "connectedBy": "dongle", "sensors": ["AF3"]}
    assert headset_usable(h)


async def test_wait_ready_blocks_on_cable_then_times_out(wire, client_factory):
    client = client_factory(wire)
    await client.connect()
    with pytest.raises(TimeoutError):
        await client.wait_ready(timeout=0.05)
    # while waiting it kept refreshing the device list
    assert any(r["method"] == "controlDevice"
               and r["params"].get("command") == "refresh"
               for r in wire.requests)
    await client.close()


async def test_wait_ready_returns_when_dongle_appears(wire, client_factory):
    client = client_factory(wire)
    await client.connect()
    asyncio.get_running_loop().call_later(0.03, wire.plug_dongle)
    h = await client.wait_ready(timeout=2)
    assert h["id"] == HEADSET_ID and client.headset == HEADSET_ID
    assert client.state["headset"]["usable"] is True
    await client.close()


# ---------- createSession: -32152 backoff ----------

async def test_create_session_backs_off_then_succeeds(wire, client_factory):
    wire.plug_dongle()
    wire.not_ready_answers = 3           # first sample not seen yet, 3 times
    client = client_factory(wire)
    await client.connect()
    await client.wait_ready(timeout=1)
    res = await client.create_session(retries=8)
    assert res["id"] == "fake-session-1"
    tries = [r for r in wire.requests if r["method"] == "createSession"]
    assert len(tries) == 4               # 3 × -32152 + 1 success
    await client.close()


async def test_create_session_raises_after_retries_exhausted(wire, client_factory):
    # cable-only headset answers -32152 forever (the measured 4/4 failure)
    client = client_factory(wire)
    await client.connect()
    client.headset = HEADSET_ID          # force past readiness, as the live run did
    with pytest.raises(CortexError) as e:
        await client.create_session(retries=3)
    assert e.value.code == HEADSET_NOT_READY
    assert len([r for r in wire.requests if r["method"] == "createSession"]) == 3
    await client.close()


async def test_non_32152_errors_raise_immediately(wire, client_factory):
    client = client_factory(wire)
    await client.connect()
    client.token = None                  # keep flow, break the params
    wire._h_createSession = lambda p: {"error": {"code": -32106, "message": "bad token"}}
    client.headset = HEADSET_ID
    with pytest.raises(CortexError) as e:
        await client.create_session(retries=5)
    assert e.value.code == -32106
    assert len([r for r in wire.requests if r["method"] == "createSession"]) == 1
    await client.close()


# ---------- full flow + Sample fan-out ----------

async def test_full_flow_produces_dict_samples(wire, client_factory):
    wire.plug_dongle()
    got: list[Sample] = []
    client = client_factory(wire, streams=["met", "pow", "fac", "dev", "eq", "com"])
    client._sample_listeners.append(got.append)
    done = end_of_tape(client)
    await client.run()
    assert client.token == "fake-token" and client.session_id == "fake-session-1"
    assert set(client.cols) >= {"met", "pow", "fac", "dev", "eq", "com"}
    await asyncio.wait_for(done.wait(), 2)

    by_stream = {s.stream: s for s in got}
    # met: cols zipped, friendly names in state
    assert by_stream["met"].data["foc"] == 0.72
    assert client.state["metrics"]["focus"] == 0.72
    # fac: labelled, and state decoded
    assert by_stream["fac"].data["eyeAct"] == "blink"
    assert client.state["facial"]["eye"] == "blink"
    # dev: nested contact-quality block flattened per channel
    assert by_stream["dev"].data["BatteryPercent"] == 82
    assert by_stream["dev"].data["FC6"] == 2
    assert client.state["contact_quality"]["FC6"] == 2
    assert set(client.state["contact_quality"]) == set(EPOCX_CHANNELS)
    # pow: sensor/band split
    assert client.state["band_power"]["AF3"]["theta"] == 1.1
    assert client.state["band_power"]["AF3"]["alpha"] == 2.2
    # com: action + power
    assert client.state["mental_command"] == {"action": "push", "power": 0.83}
    # every sample is a dict, never a bare array
    assert all(isinstance(s.data, dict) for s in got)
    # met history feeds the dashboard trend
    assert client.history and client.history[-1]["focus"] == 0.72
    await client.close()


async def test_sample_from_cortex_contract(wire, client_factory):
    """The shared-contract classmethod itself, straight from types.py."""
    s = Sample.from_cortex({"fac": ["blink", "neutral", 0.0, "neutral", 0.0],
                            "sid": "x", "time": 5.0}, {"fac": COLS["fac"]})
    assert s is not None and s.stream == "fac" and s.time == 5.0
    assert s.data["eyeAct"] == "blink"
    # unknown stream → raw passthrough, not a crash
    s2 = Sample.from_cortex({"mystery": [1, 2], "time": 1.0}, {})
    assert s2 is not None and s2.data == {"raw": [1, 2]}


# ---------- warnings + access ----------

async def test_warning_events_surface(wire, client_factory):
    seen: list[tuple[str, dict]] = []
    client = client_factory(wire)
    client.listeners.append(lambda s, d: seen.append((s, d)))
    await client.connect()
    wire.push_warning(104, "headset disconnected")
    await asyncio.sleep(0.05)
    assert client.state["warning"] == {"code": 104, "message": "headset disconnected"}
    assert ("warning", {"code": 104, "message": "headset disconnected"}) in seen
    await client.close()


async def test_eeg_denied_on_basic_license_degrades_gracefully(wire, client_factory):
    """Raw eeg answers -32016 on Basic: keep granted streams flowing, surface the denial as a warning."""
    wire.plug_dongle()
    seen: list[tuple[str, dict]] = []
    client = client_factory(wire, streams=["met", "dev", "eeg"])
    client.listeners.append(lambda s, d: seen.append((s, d)))
    done = end_of_tape(client)
    await client.run()                       # must NOT raise
    assert set(client.cols) >= {"met", "dev"} and "eeg" not in client.cols
    warns = [d for s, d in seen if s == "warning" and d.get("code") == -32016]
    assert warns and "eeg" in warns[0]["message"]
    await asyncio.wait_for(done.wait(), 2)   # granted streams still replay
    assert client.state["metrics"]["focus"] == 0.72
    await client.close()


async def test_access_denied_is_a_clear_error(client_factory):
    wire = CortexWire(access_granted=False)
    client = client_factory(wire)
    with pytest.raises(CortexError, match="EMOTIV Launcher"):
        await client.connect()
    await client.close()


async def test_fixture_replayer_reads_jsonl(client_factory):
    wire = CortexWire(fixtures=FIXTURES / "replay_basic.jsonl")
    assert len(wire.fixtures) == 6
    wire.plug_dongle()
    client = client_factory(wire, streams=["met", "com"])
    done = end_of_tape(client)
    await client.run()
    await asyncio.wait_for(done.wait(), 2)
    assert client.state["mental_command"]["action"] == "push"
    await client.close()


# ---------- trust rail, markers, recording ----------

async def test_subscribe_always_co_subscribes_dev_eq(wire, client_factory):
    """A data stream cannot be subscribed without its contact quality."""
    wire.plug_dongle()
    client = client_factory(wire, streams=["met", "pow"])
    done = end_of_tape(client)
    await client.run()
    sub = next(r for r in wire.requests if r["method"] == "subscribe")
    assert set(sub["params"]["streams"]) >= {"met", "pow", "dev", "eq"}
    await asyncio.wait_for(done.wait(), 2)
    await client.close()


async def test_samples_carry_contact_quality(wire, client_factory):
    """Every fanned-out Sample carries contact quality in `sample.cq`."""
    wire.plug_dongle()
    got: list[Sample] = []
    client = client_factory(wire, streams=["met", "pow", "fac"])
    client._sample_listeners.append(got.append)
    done = end_of_tape(client)
    await client.run()
    await asyncio.wait_for(done.wait(), 2)
    # pow replays after dev+eq → full trust rail attached
    pow_s = next(s for s in got if s.stream == "pow")
    assert pow_s.cq is not None
    assert pow_s.cq["total"] == 14 and pow_s.cq["good"] == 14
    assert pow_s.cq["sensors"]["FC6"] == 2          # weakest felt, still ≥ CQ_OK
    assert pow_s.cq["overall"] == 3                 # from eq
    assert pow_s.cq["battery"] == 82 and pow_s.cq["signal"] == 2
    # met replays BEFORE any dev/eq frame → cq is honestly None, never faked
    met_s = next(s for s in got if s.stream == "met")
    assert met_s.cq is None
    await client.close()


async def test_inject_marker_and_jsonl_recording(wire, client_factory, tmp_path):
    """Agent actions stamp the record; recordings are local jsonl."""
    import json as _json
    wire.plug_dongle()
    client = client_factory(wire)
    done = end_of_tape(client)
    path = client.start_record(str(tmp_path / "rec.jsonl"))
    await client.run()
    await asyncio.wait_for(done.wait(), 2)

    marker = await client.inject_marker("agent_said", 1)
    assert marker["label"] == "agent_said" and marker["uuid"]
    req = next(r for r in wire.requests if r["method"] == "injectMarker")
    p = req["params"]
    assert p["label"] == "agent_said" and p["value"] == 1
    assert p["port"] == "strands-emotiv"
    assert p["session"] == "fake-session-1" and p["cortexToken"] == "fake-token"
    assert isinstance(p["time"], int) and p["time"] > 10 ** 12   # ms epoch

    assert client.stop_record() == path
    lines = [_json.loads(ln) for ln in open(path)]
    streams = [ln["stream"] for ln in lines]
    assert {"met", "dev", "eq", "pow", "marker"} <= set(streams)
    assert all("cq" in ln for ln in lines)          # cq present on disk too
    m = next(ln for ln in lines if ln["stream"] == "marker")
    assert m["data"]["label"] == "agent_said" and m["cq"]["total"] == 14
    # stop is idempotent and nothing writes after it
    n = len(lines)
    assert client.stop_record() is None
    client._record_line({"stream": "ghost", "time": 0, "data": {}, "cq": None})
    assert len(open(path).readlines()) == n
    await client.close()


def test_missing_creds_construct_ok_connect_fails(monkeypatch, tmp_path):
    """No credentials: importing and constructing work, connect raises the hint."""
    monkeypatch.delenv("EMOTIV_CLIENT_ID", raising=False)
    monkeypatch.delenv("EMOTIV_CLIENT_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    c = CortexClient()
    with pytest.raises(CortexError, match="strands-emotiv doctor"):
        asyncio.run(c.connect())


def test_met_partial_sample_merges_not_replaces():
    """Cortex sends exc/lex at 2 Hz, the other metrics every ~10 s. A partial
    sample must not blank the slow ones (and nan/inactive values stay out)."""
    client = CortexClient(client_id="x", client_secret="y")
    full = Sample(stream="met", time=1000.0, data={
        "eng": 0.7, "eng.isActive": True, "exc": 0.3, "exc.isActive": True,
        "lex": 0.2, "str": 0.4, "str.isActive": True,
        "rel": 0.5, "rel.isActive": True, "int": 0.6, "int.isActive": True,
    })
    partial = Sample(stream="met", time=1000.5, data={
        "exc": 0.9, "exc.isActive": True, "lex": 0.25,
        "str": float("nan"), "str.isActive": True,   # nan must not overwrite
        "rel": 0.8, "rel.isActive": False,           # inactive must not overwrite
    })
    client._update_state(full)
    client._update_state(partial)
    m = client.state["metrics"]
    assert m["excitement"] == 0.9 and m["longExcitement"] == 0.25  # fresh
    assert m["engagement"] == 0.7 and m["stress"] == 0.4 and m["relaxation"] == 0.5  # kept
    # history rows carry the merged view: the radar trail needs every axis
    last = client.history[-1]
    assert last["t"] == 1000.5 and last["excitement"] == 0.9
    assert last["stress"] == 0.4 and last["relaxation"] == 0.5
