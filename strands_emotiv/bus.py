"""In-process pub/sub for agent turn events.

The agent path publishes here; the dataset recorder subscribes. Subscriber
exceptions are swallowed (logged), and publish is synchronous and cheap.

Turn-event contract (dicts, all keys optional except type):
    {"type": "turn_start", "q": <user message or None>, "ambient": <agent-eye line>, "t": epoch_s}
    {"type": "delta",      "text": <text emitted so far OR the increment>, "t": epoch_s}
    {"type": "tool",       "tool": <tool name>, "t": epoch_s}
    {"type": "marker",     "label": <injectMarker label>, "t": epoch_s}
    {"type": "turn_end",   "text": <full answer>, "tokens": <int|None>, "t": epoch_s}
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

TurnEvent = dict[str, Any]


class Bus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: list[Callable[[TurnEvent], None]] = []

    def subscribe(self, cb: Callable[[TurnEvent], None]) -> Callable[[], None]:
        """Register a callback; returns an unsubscribe function."""
        with self._lock:
            self._subs.append(cb)

        def unsubscribe() -> None:
            with self._lock:
                if cb in self._subs:
                    self._subs.remove(cb)

        return unsubscribe

    def publish(self, event: TurnEvent) -> None:
        with self._lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(event)
            except Exception:  # a broken subscriber must never break the publisher
                log.exception("bus subscriber failed on %s", event.get("type"))


#: The one bus agent code publishes to and the recorder listens on.
agent_bus = Bus()
