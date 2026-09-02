"""V3Writer tests: recorder episodes → v3.0 layout on disk → load back with pyarrow."""

from __future__ import annotations

import json

import numpy as np
import pytest

pa = pytest.importorskip("pyarrow")
import pandas as pd
import pyarrow.parquet as pq

from strands_emotiv.dataset_v3 import FEATURES, V3Writer
from strands_emotiv.recorder import FPS, Recorder
from tests.test_recorder import T0, drive


def record_two_episodes(tmp_path):
    w = V3Writer(tmp_path / "ds", repo_id="cagataydev/emotiv-ecot")
    rec = Recorder(dataset_root=str(tmp_path / "ds"), writer=w)
    rec.start()
    # episode 1: a turn
    t = drive(rec, T0, 4.0, stress=0.6, eng=0.4)
    rec.on_agent({"type": "turn_start", "q": "am I stressed?", "ambient": "[brain: stress 0.6]"})
    rec.on_agent({"type": "delta", "text": "A little. Breathe."})
    t = drive(rec, t, 2.0, stress=0.6, eng=0.4)
    rec.on_agent({"type": "turn_end", "text": "A little. Breathe."})
    t = drive(rec, t, 6.0, stress=0.3, eng=0.7)
    rec.stop()
    # episode 2: ambient 30 s
    rec.start_ambient()
    drive(rec, t + 1.0, 31.0)
    rec.stop()
    w.finalize()
    return w, rec


def test_v3_layout_and_loadback(tmp_path):
    w, rec = record_two_episodes(tmp_path)
    root = tmp_path / "ds"
    assert rec.episodes_total == 2 and w.status()["episodes"] == 2

    # layout
    for p in ("meta/info.json", "meta/stats.json", "meta/tasks.parquet",
              "meta/episodes/chunk-000/file-000.parquet", "data/chunk-000/file-000.parquet"):
        assert (root / p).exists(), p

    # info.json
    info = json.load(open(root / "meta" / "info.json"))
    assert info["codebase_version"] == "v3.0"
    assert info["fps"] == FPS
    assert info["total_episodes"] == 2
    assert info["splits"] == {"train": "0:2"}
    assert info["features"]["observation.state"]["shape"] == [70]
    assert info["features"]["observation.state"]["names"][0] == "AF3.theta"
    assert info["video_path"] is None

    # data parquet: shapes + fps clock
    tbl = pq.read_table(root / "data" / "chunk-000" / "file-000.parquet")
    assert tbl.num_rows == info["total_frames"] == rec.frames_total
    df = tbl.to_pandas()
    assert len(df["observation.state"].iloc[0]) == 70
    assert len(df["action"].iloc[0]) == 4
    ep0 = df[df["episode_index"] == 0]
    ts = ep0["timestamp"].to_numpy()
    assert np.allclose(np.diff(ts), 1.0 / FPS, atol=1e-4)
    assert ts[0] == 0.0
    assert (df["index"].to_numpy() == np.arange(len(df))).all()

    # tasks parquet: dedupe + round-trip through task_index
    tasks = pd.read_parquet(root / "meta" / "tasks.parquet")
    assert (tasks["task_index"].to_numpy() == np.arange(len(tasks))).all()
    inv = {v: k for k, v in zip(tasks.index, tasks["task_index"], strict=True)}
    turn_frame_tasks = [inv[i] for i in df[df["episode_index"] == 0]["task_index"]]
    assert any("AMBIENT:" in t for t in turn_frame_tasks)
    assert any("REWARD:" in t and "pending" not in t for t in turn_frame_tasks)
    assert any(t.startswith("TASK: am I stressed?") for t in turn_frame_tasks)

    # episodes metadata: boundaries
    eps = pd.read_parquet(root / "meta" / "episodes" / "chunk-000" / "file-000.parquet")
    assert list(eps["episode_index"]) == [0, 1]
    assert eps["dataset_from_index"].iloc[0] == 0
    assert eps["dataset_to_index"].iloc[0] == eps["length"].iloc[0]
    assert eps["dataset_from_index"].iloc[1] == eps["dataset_to_index"].iloc[0]
    assert eps["length"].iloc[1] == 30 * FPS  # ambient episode is exactly 30 s
    assert "stats/observation.state/mean" in eps.columns

    # stats.json: every feature, every stat, right dims
    stats = json.load(open(root / "meta" / "stats.json"))
    for key, ft in FEATURES.items():
        assert key in stats, key
        for s in ("min", "max", "mean", "std", "count", "q01", "q50", "q99"):
            assert s in stats[key], (key, s)
        if ft["shape"] != (1,):
            assert len(stats[key]["mean"]) == ft["shape"][0]
    assert stats["observation.state"]["count"][0] == info["total_frames"]


def test_dataset_valid_after_every_episode(tmp_path):
    """Files are rewritten per episode; no finalize needed for validity."""
    w = V3Writer(tmp_path / "ds")
    rec = Recorder(writer=w)
    rec.start_ambient()
    drive(rec, T0, 31.0)
    info = json.load(open(tmp_path / "ds" / "meta" / "info.json"))
    assert info["total_episodes"] == 1
    tbl = pq.read_table(tmp_path / "ds" / "data" / "chunk-000" / "file-000.parquet")
    assert tbl.num_rows == info["total_frames"] > 0
