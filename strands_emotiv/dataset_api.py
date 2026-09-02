"""HTTP control surface for the ECoT dataset recorder: record start/stop,
status, episode listing, tar.gz export, and publish to the HF Hub. The router
taps the server's existing Cortex fan-out at record-start time, so there is
never a second headset connection; agent turns arrive via strands_emotiv.bus.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from .bus import agent_bus
from .dataset_v3 import V3Writer
from .recorder import Recorder

log = logging.getLogger(__name__)
router = APIRouter()

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"
REPO_ID = "cagataydev/emotiv-ecot"

_rec: Recorder | None = None
_writer: V3Writer | None = None
_name: str | None = None
_wired: list = []  # cleanup callbacks


def _default_name() -> str:
    return _dt.datetime.now().strftime("session-%Y%m%d-%H%M")


def _on_agent(ev: dict) -> None:
    """Module-level bus subscriber; forwards to whatever recorder is live."""
    if _rec is not None:
        _rec.on_agent(ev)


agent_bus.subscribe(_on_agent)


def _wire(rec: Recorder) -> None:
    """Tap the live server fan-out. The import is deferred because server.py
    imports this module; at call time it is a sys.modules hit, not a circular
    import."""
    try:
        from . import server as _srv

        _srv.client._sample_listeners.append(rec.on_sample)
        _wired.append(lambda: _srv.client._sample_listeners.remove(rec.on_sample))
        if _srv._events is not None:
            _srv._events.subscribe("*", rec.on_event)
            # EventEngine has no unsubscribe; recorder checks its own tick, harmless after stop
    except Exception:  # tests / server not running: feed the recorder directly
        log.info("dataset_api: no live server fan-out; recorder must be fed manually")


def _unwire() -> None:
    for undo in _wired:
        try:
            undo()
        except Exception:
            pass
    _wired.clear()


@router.post("/api/dataset/record/start")
async def record_start(req: Request) -> JSONResponse:
    global _rec, _writer, _name
    if _rec is not None and _rec.recording:
        return JSONResponse({"error": "already recording", "name": _name}, status_code=409)
    try:
        body = await req.json()
    except Exception:
        body = {}
    _name = str(body.get("name") or _default_name())
    root = DATASETS_DIR / _name
    _writer = V3Writer(root, repo_id=REPO_ID)
    _rec = Recorder(dataset_root=str(root), repo_id=REPO_ID, writer=_writer)
    _wire(_rec)
    if body.get("ambient"):
        _rec.start_ambient()
    else:
        _rec.start()
    return JSONResponse({"ok": True, "name": _name, "root": str(root), "ambient": bool(body.get("ambient"))})


@router.post("/api/dataset/record/stop")
async def record_stop() -> JSONResponse:
    if _rec is None:
        return JSONResponse({"error": "not recording"}, status_code=409)
    _rec.stop()
    _unwire()
    if _writer is not None:
        _writer.finalize()
    st = _status_dict()
    return JSONResponse({"ok": True, **st})


def _dir_bytes(root: Path) -> int:
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0


def _status_dict() -> dict[str, Any]:
    root = DATASETS_DIR / _name if _name else None
    return {
        "recording": bool(_rec and _rec.recording),
        "in_turn": bool(_rec and _rec.status().get("in_turn")),
        "episodes": _rec.episodes_total if _rec else 0,
        "frames": _rec.frames_total if _rec else 0,
        "bytes": _dir_bytes(root) if root else 0,
        "root": str(root) if root else None,
        "name": _name,
        "repo_id": REPO_ID,
    }


@router.get("/api/dataset/status")
async def dataset_status() -> JSONResponse:
    return JSONResponse(_status_dict())


@router.get("/api/dataset/episodes")
async def dataset_episodes() -> JSONResponse:
    if _writer is None:
        return JSONResponse([])
    out = []
    for row in _writer.episode_rows:
        tasks = row.get("tasks") or [""]
        # prefer a turn task over idle for the preview
        preview = next((t for t in tasks if not t.startswith("TASK: idle")), tasks[0])
        out.append({
            "episode_index": row["episode_index"],
            "length": row["length"],
            "seconds": round(row["length"] / 8.0, 1),
            "kind": "ambient" if all(t.startswith("TASK: idle") for t in tasks) else "turn",
            "ecot_preview": preview[:300],
        })
    return JSONResponse(out)


@router.get("/api/dataset/export")
async def dataset_export() -> Any:
    if _name is None:
        return JSONResponse({"error": "no dataset recorded yet"}, status_code=404)
    root = DATASETS_DIR / _name
    if not root.exists():
        return JSONResponse({"error": f"{root} missing"}, status_code=404)
    tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    with tarfile.open(tmp.name, "w:gz") as tar:
        tar.add(root, arcname=_name)
    return FileResponse(tmp.name, filename=f"{_name}.tar.gz", media_type="application/gzip")


@router.post("/api/dataset/publish")
async def dataset_publish(req: Request) -> JSONResponse:
    """Push a recorded dataset to the HF Hub as a private dataset, using the
    `hf` CLI with this machine's cached token."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    name = str(body.get("name") or _name or "")
    if not name:
        return JSONResponse({"error": "no dataset name"}, status_code=400)
    root = DATASETS_DIR / name
    if not (root / "meta" / "info.json").exists():
        return JSONResponse({"error": f"{root} is not a dataset"}, status_code=404)

    def _push() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["hf", "upload", REPO_ID, str(root), ".", "--repo-type", "dataset",
             "--commit-message", f"ECoT session {name} (strands-emotiv)"],
            capture_output=True, text=True, timeout=600, check=False,
        )

    proc = await asyncio.to_thread(_push)
    if proc.returncode != 0:
        return JSONResponse({"error": "hf upload failed", "detail": proc.stderr[-800:]}, status_code=502)

    def _tag() -> None:
        # lerobot refuses Hub datasets without a codebase-version tag; move it to the new head
        for args in (["hf", "repo", "tag", "delete", REPO_ID, "v3.0", "--repo-type", "dataset", "-y"],
                     ["hf", "repo", "tag", "create", REPO_ID, "v3.0", "--repo-type", "dataset"]):
            try:
                subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
            except Exception:
                pass

    await asyncio.to_thread(_tag)
    return JSONResponse({
        "ok": True, "repo_id": REPO_ID, "private": True,
        "url": f"https://huggingface.co/datasets/{REPO_ID}",
        "name": name,
    })
