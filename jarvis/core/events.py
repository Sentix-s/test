"""A small thread-safe publish/subscribe event bus.

Components never call each other's UI directly; they publish events
(``state.changed``, ``task.started``, ...) and the GUI/CLI subscribes.
This keeps the voice engine, agent and GUI decoupled (the GUI arrives in
Phase 3 and will bridge these events into Qt signals).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[["Event"], None]


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    WILDCARD = "*"

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, name: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to an event name (or ``"*"``). Returns an unsubscribe function."""
        with self._lock:
            self._handlers[name].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._handlers[name].remove(handler)
                except ValueError:
                    pass

        return unsubscribe

    def publish(self, name: str, **data: Any) -> None:
        event = Event(name, data)
        with self._lock:
            handlers = list(self._handlers.get(name, ())) + list(self._handlers.get(self.WILDCARD, ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break others
                log.exception("Event handler failed for %s", name)
