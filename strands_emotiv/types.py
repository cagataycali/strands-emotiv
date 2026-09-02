"""Shared data types between the Cortex stream layer and state/events/tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Stream = Literal["eeg", "mot", "fac", "com", "met", "pow", "dev", "eq", "sys"]
ALL_STREAMS: tuple[Stream, ...] = ("eeg", "mot", "fac", "com", "met", "pow", "dev", "eq", "sys")

# EPOC X channel order as Cortex reports it in `cols` for the "eeg" stream.
EPOCX_CHANNELS = ("AF3", "F7", "F3", "FC5", "T7", "P7", "O1", "O2", "P8", "T8", "FC6", "F4", "F8", "AF4")


@dataclass(slots=True)
class Sample:
    """One Cortex data sample, already zipped with its `cols` into a dict.

    `cq` carries the contact-quality summary current at the moment the sample
    was decoded: {"good": int, "total": int, "overall": float|None,
    "battery": ..., "signal": ..., "sensors": {ch: 0..4}}. None only before
    the first dev/eq frame lands.
    """

    stream: Stream
    time: float  # seconds since epoch, from Cortex
    data: dict[str, Any] = field(default_factory=dict)
    cq: dict[str, Any] | None = None

    @classmethod
    def from_cortex(cls, msg: dict[str, Any], cols: dict[str, list[str]]) -> Sample | None:
        """Build from a raw Cortex sample object {"<stream>": [...], "sid": ..., "time": ...}."""
        for key, values in msg.items():
            if key in ("sid", "time"):
                continue
            labels = cols.get(key)
            if labels is None:
                return cls(stream=key, time=float(msg.get("time", 0.0)), data={"raw": values})  # type: ignore[arg-type]
            return cls(stream=key, time=float(msg.get("time", 0.0)), data=dict(zip(labels, values, strict=False)))  # type: ignore[arg-type]
        return None
