"""LeRobot v3.0 dataset writer, written with pyarrow/pandas directly (no torch).

Mirrors the on-disk layout of lerobot 0.4.x (`lerobot/datasets/utils.py` and
`lerobot_dataset.py`): meta/info.json, meta/stats.json, meta/tasks.parquet,
meta/episodes/chunk-000/file-000.parquet and data/chunk-000/file-000.parquet.
Sessions are minutes long, so everything fits one chunk-000/file-000 pair far
under the 100 MB limit and both parquet files are rewritten per episode.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .recorder import (
    BANDS,
    EVENT_SLOTS,
    EYE_VOCAB,
    FPS,
    LOWER_VOCAB,
    MOT_KEYS,
)
from .types import EPOCX_CHANNELS

CODEBASE_VERSION = "v3.0"
QUANTILES = {"q01": 0.01, "q10": 0.10, "q50": 0.50, "q90": 0.90, "q99": 0.99}

METRIC_NAMES = ["attention", "engagement", "excitement", "longExcitement", "stress", "relaxation", "interest"]

FEATURES: dict[str, dict[str, Any]] = {
    "observation.state": {
        "dtype": "float32", "shape": (70,),
        "names": [f"{ch}.{band}" for ch in EPOCX_CHANNELS for band in BANDS],
    },
    "observation.contact_quality": {"dtype": "float32", "shape": (14,), "names": list(EPOCX_CHANNELS)},
    "observation.motion": {"dtype": "float32", "shape": (10,), "names": list(MOT_KEYS)},
    "observation.motion_valid": {"dtype": "float32", "shape": (1,), "names": None},
    "observation.metrics": {"dtype": "float32", "shape": (7,), "names": METRIC_NAMES},
    "observation.metrics_valid": {"dtype": "float32", "shape": (7,), "names": METRIC_NAMES},
    "observation.facial": {
        "dtype": "float32", "shape": (6,),
        "names": ["eye_neutral", "eye_blink", "eye_winkL", "eye_winkR", "upper_pow", "lower_pow"],
    },
    "observation.facial_label": {
        "dtype": "float32", "shape": (2,),
        # vocab encoded in the names so the mapping survives in info.json
        "names": [f"eyeAct_idx({','.join(EYE_VOCAB)})", f"lowerAct_idx({','.join(LOWER_VOCAB)})"],
    },
    "observation.events": {"dtype": "float32", "shape": (12,), "names": list(EVENT_SLOTS)},
    "action": {
        "dtype": "float32", "shape": (4,),
        "names": ["spoke", "tool_called", "marker_injected", "turn_length_tokens_norm"],
    },
    # standard v3 bookkeeping (lerobot DEFAULT_FEATURES)
    "timestamp": {"dtype": "float32", "shape": (1,), "names": None},
    "frame_index": {"dtype": "int64", "shape": (1,), "names": None},
    "episode_index": {"dtype": "int64", "shape": (1,), "names": None},
    "index": {"dtype": "int64", "shape": (1,), "names": None},
    "task_index": {"dtype": "int64", "shape": (1,), "names": None},
}

VECTOR_KEYS = tuple(k for k, ft in FEATURES.items() if ft["shape"][0] > 1)
SCALAR_FLOAT = tuple(k for k, ft in FEATURES.items() if ft["shape"] == (1,) and ft["dtype"] == "float32")  # motion_valid, timestamp
SCALAR_INT = ("frame_index", "episode_index", "index", "task_index")


def _feature_stats(arr: np.ndarray) -> dict[str, Any]:
    """min/max/mean/std/count + q01..q99 per feature dim, matching get_feature_stats(axis=0, keepdims=(ndim==1))."""
    keepdims = arr.ndim == 1
    out = {
        "min": arr.min(axis=0, keepdims=keepdims),
        "max": arr.max(axis=0, keepdims=keepdims),
        "mean": arr.mean(axis=0, keepdims=keepdims),
        "std": arr.std(axis=0, keepdims=keepdims),
        "count": np.array([len(arr)]),
    }
    for name, q in QUANTILES.items():
        out[name] = np.quantile(arr, q, axis=0, keepdims=keepdims)
    return out


def _agg_stats(per_ep: list[dict[str, dict[str, np.ndarray]]]) -> dict:
    """Weighted-mean aggregation across episodes (mirror of aggregate_stats semantics)."""
    agg: dict[str, dict[str, Any]] = {}
    keys = per_ep[0].keys()
    for key in keys:
        stats = [ep[key] for ep in per_ep]
        counts = np.array([s["count"][0] for s in stats], dtype=np.float64)
        w = counts / counts.sum()
        mean = sum(wi * np.asarray(s["mean"], dtype=np.float64) for wi, s in zip(w, stats, strict=True))
        # pooled variance
        var = sum(
            wi * (np.asarray(s["std"], dtype=np.float64) ** 2 + (np.asarray(s["mean"], dtype=np.float64) - mean) ** 2)
            for wi, s in zip(w, stats, strict=True)
        )
        agg[key] = {
            "min": np.min([s["min"] for s in stats], axis=0),
            "max": np.max([s["max"] for s in stats], axis=0),
            "mean": mean,
            "std": np.sqrt(var),
            "count": np.array([int(counts.sum())]),
        }
        for qname in QUANTILES:
            agg[key][qname] = sum(wi * np.asarray(s[qname], dtype=np.float64) for wi, s in zip(w, stats, strict=True))
    return agg


class V3Writer:
    """`Recorder.writer` implementation: episode dict in, v3.0 dataset on disk.

    Call `finalize()` once at the end of the session (idempotent); files are
    fully rewritten per episode, so the dataset is valid after every episode.
    """

    def __init__(self, root: str | Path, repo_id: str = "cagataydev/emotiv-ecot", robot_type: str = "epoc-x") -> None:
        self.root = Path(root)
        self.repo_id = repo_id
        self.robot_type = robot_type
        self.tasks: dict[str, int] = {}          # task string -> task_index
        self.episode_rows: list[dict] = []
        self.frame_columns: dict[str, list] = {k: [] for k in FEATURES}
        self.total_frames = 0
        self.per_episode_stats: list[dict] = []

    # public

    def __call__(self, episode: dict) -> None:
        self.write_episode(episode)

    def write_episode(self, episode: dict) -> None:
        frames = episode["frames"]
        if not frames:
            return
        ep_index = len(self.episode_rows)
        n = len(frames)
        from_index = self.total_frames

        ep_cols: dict[str, np.ndarray | list] = {}
        for key in VECTOR_KEYS:
            ep_cols[key] = np.stack([np.asarray(f[key], dtype=np.float32) for f in frames])
        ep_cols["observation.motion_valid"] = np.array(
            [float(np.asarray(f["observation.motion_valid"]).reshape(-1)[0]) for f in frames], dtype=np.float32
        )
        t0 = frames[0]["_t"]
        ep_cols["timestamp"] = np.array([f["_t"] - t0 for f in frames], dtype=np.float32)
        ep_cols["frame_index"] = np.arange(n, dtype=np.int64)
        ep_cols["episode_index"] = np.full(n, ep_index, dtype=np.int64)
        ep_cols["index"] = np.arange(from_index, from_index + n, dtype=np.int64)

        tasks = [f["task"] for f in frames]
        for t in dict.fromkeys(tasks):  # ordered dedupe, mirror save_episode_tasks
            if t not in self.tasks:
                self.tasks[t] = len(self.tasks)
        ep_cols["task_index"] = np.array([self.tasks[t] for t in tasks], dtype=np.int64)

        # accumulate frame columns
        for key in FEATURES:
            col = ep_cols[key]
            self.frame_columns[key].extend(list(col))
        self.total_frames += n

        # per-episode stats (numeric features only, task strings skipped)
        ep_stats = {k: _feature_stats(np.asarray(ep_cols[k])) for k in FEATURES}
        self.per_episode_stats.append(ep_stats)

        row: dict[str, Any] = {
            "episode_index": ep_index,
            "tasks": list(dict.fromkeys(tasks)),
            "length": n,
            "data/chunk_index": 0,
            "data/file_index": 0,
            "dataset_from_index": from_index,
            "dataset_to_index": from_index + n,
            "meta/episodes/chunk_index": 0,
            "meta/episodes/file_index": 0,
        }
        for fkey, st in ep_stats.items():
            for sname, val in st.items():
                row[f"stats/{fkey}/{sname}"] = np.asarray(val).tolist()
        self.episode_rows.append(row)

        self._write_all()

    def finalize(self) -> None:
        if self.episode_rows:
            self._write_all()

    def status(self) -> dict:
        return {"episodes": len(self.episode_rows), "frames": self.total_frames, "root": str(self.root)}

    # disk

    def _write_all(self) -> None:
        import pandas as pd
        import pyarrow as pa
        import pyarrow.parquet as pq

        root = self.root
        (root / "meta").mkdir(parents=True, exist_ok=True)

        # data/chunk-000/file-000.parquet
        arrays, fields = [], []
        for key, _ft in FEATURES.items():
            if key in VECTOR_KEYS:
                typ = pa.list_(pa.float32())
                arrays.append(pa.array([np.asarray(v, dtype=np.float32) for v in self.frame_columns[key]], type=typ))
            elif key in SCALAR_INT:
                typ = pa.int64()
                arrays.append(pa.array([int(v) for v in self.frame_columns[key]], type=typ))
            else:  # scalar float32: timestamp, observation.motion_valid
                typ = pa.float32()
                arrays.append(pa.array([float(v) for v in self.frame_columns[key]], type=typ))
            fields.append(pa.field(key, typ))
        data_path = root / "data" / "chunk-000" / "file-000.parquet"
        data_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_arrays(arrays, schema=pa.schema(fields)), data_path)

        # meta/tasks.parquet (pandas: index = task strings, col task_index)
        tasks_df = pd.DataFrame(
            {"task_index": list(self.tasks.values())},
            index=pd.Index(list(self.tasks.keys()), name=None),
        )
        tasks_df.to_parquet(root / "meta" / "tasks.parquet")

        # meta/episodes/chunk-000/file-000.parquet
        ep_path = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
        ep_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(self.episode_rows).to_parquet(ep_path, index=False)

        # meta/info.json (mirror of create_empty_dataset_info, filled)
        features_json: dict[str, Any] = {}
        for key, ft in FEATURES.items():
            features_json[key] = {"dtype": ft["dtype"], "shape": list(ft["shape"]), "names": ft["names"]}
        info = {
            "codebase_version": CODEBASE_VERSION,
            "robot_type": self.robot_type,
            "total_episodes": len(self.episode_rows),
            "total_frames": self.total_frames,
            "total_tasks": len(self.tasks),
            "chunks_size": 1000,
            "data_files_size_in_mb": 100,
            "video_files_size_in_mb": 200,
            "fps": FPS,
            "splits": {"train": f"0:{len(self.episode_rows)}"},
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": None,
            "features": features_json,
        }
        with open(root / "meta" / "info.json", "w") as f:
            json.dump(info, f, indent=4, ensure_ascii=False)

        # meta/stats.json (aggregated, flattened key/values like serialize_dict)
        agg = _agg_stats(self.per_episode_stats)
        stats_json = {
            key: {sname: np.asarray(val).tolist() for sname, val in st.items()}
            for key, st in agg.items()
        }
        with open(root / "meta" / "stats.json", "w") as f:
            json.dump(stats_json, f, indent=4, ensure_ascii=False)
