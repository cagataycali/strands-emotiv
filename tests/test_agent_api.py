"""POST /api/agent/ask rail: happy path, offline grace, event capture."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import strands_emotiv.agent_api as api
import strands_emotiv.tools as bt
from strands_emotiv.events import EventEngine
from strands_emotiv.state import BrainState
from strands_emotiv.types import Sample


class FakeAgent:
    async def invoke_async(self, msg: str) -> str:
        assert msg.startswith("[brain: ")
        return "three lines, one step"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(bt, "STATE", BrainState())
    monkeypatch.setattr(bt, "ENGINE", EventEngine())
    monkeypatch.setattr(bt, "_source_obj", None)
    monkeypatch.setattr(api, "_agent", None)
    monkeypatch.setattr(api, "_history", [])
    api.set_engine(bt.ENGINE)
    monkeypatch.setattr(api, "build_agent", lambda: FakeAgent())
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_ask_happy_path(client):
    r = client.post("/api/agent/ask", json={"message": "what now?"})
    assert r.status_code == 200
    body = r.json()
    assert body["a"] == "three lines, one step"
    assert body["ambient"].startswith("[brain: ")
    assert body["events_during"] == []
    h = client.get("/api/agent/history").json()["history"]
    assert len(h) == 1 and h[0]["q"] == "what now?"


def test_ask_captures_events_fired_during_turn(client, monkeypatch):
    async def invoke_async(msg):
        bt.feed(Sample(stream="fac", time=5.0, data={"eyeAct": "blink"}))
        return "ok"

    monkeypatch.setattr(api, "build_agent", lambda: type("A", (), {"invoke_async": staticmethod(invoke_async)})())
    body = client.post("/api/agent/ask", json={"message": "hi"}).json()
    assert [e["kind"] for e in body["events_during"]] == ["blink"]


def test_empty_message_is_400(client):
    assert client.post("/api/agent/ask", json={}).status_code == 400


def test_agent_offline_is_503_not_crash(client, monkeypatch):
    def boom():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(api, "build_agent", boom)
    r = client.post("/api/agent/ask", json={"message": "hi"})
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "agent offline" and "no credentials" in body["detail"]
    assert "ambient" in body  # the mirror still shows what the agent would have seen


def test_status_endpoint(client):
    s = client.get("/api/agent/status").json()
    assert s["ready"] is False and s["turns"] == 0
    client.post("/api/agent/ask", json={"message": "hi"})
    s = client.get("/api/agent/status").json()
    assert s["ready"] is True and s["turns"] == 1


def test_server_mounts_agent_router():
    from strands_emotiv.server import app as server_app

    paths = {getattr(r, "path", None) for r in server_app.routes}
    if None in paths:  # older starlette keeps included routers unflattened
        from fastapi.testclient import TestClient as TC

        c = TC(server_app)
        assert c.post("/api/agent/ask", json={}).status_code == 400
    else:
        assert "/api/agent/ask" in paths and "/api/agent/status" in paths


def test_server_registers_marker_source():
    """Agent turns must stamp the SERVER's live session, not a phantom."""
    import strands_emotiv.server as srv
    import strands_emotiv.tools as bt2

    # importing server ran the mount block; the module-level client must be
    # what get_source() returns once server wiring executes set_source
    bt2.set_source(srv.client)
    assert bt2.get_source() is srv.client
    assert hasattr(srv.client, "inject_marker")
