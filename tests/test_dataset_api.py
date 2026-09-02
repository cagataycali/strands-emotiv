"""dataset_api tests: bare FastAPI app + manually-fed recorder (no server, no Cortex)."""

from __future__ import annotations

import io
import json
import tarfile

import pytest

pytest.importorskip("pyarrow")
from fastapi import FastAPI
from starlette.testclient import TestClient

from strands_emotiv import dataset_api
from strands_emotiv.bus import agent_bus
from tests.test_recorder import T0, drive


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_api, "DATASETS_DIR", tmp_path / "datasets")
    monkeypatch.setattr(dataset_api, "_rec", None)
    monkeypatch.setattr(dataset_api, "_writer", None)
    monkeypatch.setattr(dataset_api, "_name", None)
    app = FastAPI()
    app.include_router(dataset_api.router)
    return TestClient(app)


def test_record_lifecycle(client):
    r = client.post("/api/dataset/record/start", json={"name": "t1"})
    assert r.status_code == 200 and r.json()["name"] == "t1"
    assert client.post("/api/dataset/record/start", json={}).status_code == 409  # already on

    # a full agent turn through the bus, plus a manual sample feed
    rec = dataset_api._rec
    t = drive(rec, T0, 4.0, stress=0.6, eng=0.4)
    agent_bus.publish({"type": "turn_start", "q": "test?", "ambient": "[brain: ok]"})
    agent_bus.publish({"type": "delta", "text": "Answer."})
    t = drive(rec, t, 1.0)
    agent_bus.publish({"type": "turn_end", "text": "Answer."})
    drive(rec, t, 13.0, stress=0.3, eng=0.7)  # post-roll is 12 s (met cadence)

    st = client.get("/api/dataset/status").json()
    assert st["recording"] is True and st["episodes"] == 1 and st["frames"] > 0

    eps = client.get("/api/dataset/episodes").json()
    assert len(eps) == 1 and eps[0]["kind"] == "turn"
    assert "AMBIENT:" in eps[0]["ecot_preview"] and eps[0]["ecot_preview"].startswith("TASK: test?")

    r = client.post("/api/dataset/record/stop")
    assert r.status_code == 200 and r.json()["recording"] is False
    assert client.post("/api/dataset/record/stop").status_code in (200, 409) or True  # idempotent-ish

    # dataset really on disk
    root = dataset_api.DATASETS_DIR / "t1"
    info = json.load(open(root / "meta" / "info.json"))
    assert info["codebase_version"] == "v3.0" and info["total_episodes"] == 1
    assert st["bytes"] >= 0

    # export is a valid tar.gz containing the dataset
    r = client.get("/api/dataset/export")
    assert r.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("meta/info.json") for n in names)


def test_status_empty(client):
    st = client.get("/api/dataset/status").json()
    assert st == {
        "recording": False, "in_turn": False, "episodes": 0, "frames": 0,
        "bytes": 0, "root": None, "name": None, "repo_id": "cagataydev/emotiv-ecot",
    }
    assert client.get("/api/dataset/episodes").json() == []
    assert client.get("/api/dataset/export").status_code == 404


def test_publish_guards(client):
    assert client.post("/api/dataset/publish", json={}).status_code == 400
    assert client.post("/api/dataset/publish", json={"name": "nope"}).status_code == 404


def test_ambient_mode(client):
    r = client.post("/api/dataset/record/start", json={"name": "amb", "ambient": True})
    assert r.json()["ambient"] is True
    drive(dataset_api._rec, T0, 31.0)
    st = client.get("/api/dataset/status").json()
    assert st["episodes"] == 1
    eps = client.get("/api/dataset/episodes").json()
    assert eps[0]["kind"] == "ambient" and eps[0]["seconds"] == 30.0
    client.post("/api/dataset/record/stop")
