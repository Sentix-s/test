"""Background threads of the GUI, tracked so the app can wait for them before
Qt is torn down (a thread touching Qt objects during interpreter shutdown can
crash the process on exit)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

_threads: set[threading.Thread] = set()
_lock = threading.Lock()


def run_bg(fn: Callable[[], None], name: str, on_error: Callable[[Exception], None] | None = None) -> threading.Thread:
    def run() -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - never let a worker die silently
            log.exception("%s failed", name)
            if on_error is not None:
                try:
                    on_error(exc)
                except RuntimeError:  # receiver already deleted
                    pass
        finally:
            with _lock:
                _threads.discard(threading.current_thread())

    t = threading.Thread(target=run, name=name, daemon=True)
    with _lock:
        _threads.add(t)
    t.start()
    return t


def wait_all(timeout: float = 10.0) -> bool:
    """Join all running workers; True if all finished in time."""
    end = time.monotonic() + timeout
    while True:
        with _lock:
            alive = [t for t in _threads if t.is_alive()]
        if not alive:
            return True
        left = end - time.monotonic()
        if left <= 0:
            log.warning("Workers still running at exit: %s", [t.name for t in alive])
            return False
        alive[0].join(min(left, 0.2))
