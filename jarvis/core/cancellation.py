"""Cooperative cancellation used by the stop button and emergency stop."""

from __future__ import annotations

import threading
from typing import Callable

from jarvis.core.errors import TaskCancelled


class CancelToken:
    """Thread-safe cancellation flag.

    Long-running code calls :meth:`raise_if_cancelled` at safe points.
    Resources that can be interrupted (e.g. an HTTP stream) register a
    callback with :meth:`on_cancel` so they are closed immediately.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self.reason: str = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self._event.is_set():
                return
            self.reason = reason
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for cb in callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE001 - a failing callback must not block cancellation
                pass

    def on_cancel(self, callback: Callable[[], None]) -> None:
        run_now = False
        with self._lock:
            if self._event.is_set():
                run_now = True
            else:
                self._callbacks.append(callback)
        if run_now:
            callback()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TaskCancelled(self.reason or "cancelled")

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)
