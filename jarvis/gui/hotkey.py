"""Global hotkey (Ctrl+Alt+J) via the Win32 RegisterHotKey API.

A dedicated thread owns the hotkey and its message loop; presses are
delivered as a Qt signal. No admin rights, no keyboard hooks.
"""

from __future__ import annotations

import logging
import sys
import threading

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


class GlobalHotkey(QObject):
    pressed = Signal()

    def __init__(self, key: str = "J", modifiers: int = MOD_CONTROL | MOD_ALT) -> None:
        super().__init__()
        self.vk = ord(key.upper())
        self.modifiers = modifiers | MOD_NOREPEAT
        self._thread_id = 0
        self.registered = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="hotkey", daemon=True)
        self._thread.start()
        ready.wait(2)
        return self.registered

    def _run(self, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        self.registered = bool(user32.RegisterHotKey(None, 1, self.modifiers, self.vk))
        ready.set()
        if not self.registered:
            log.warning("Ctrl+Alt+J is already used by another program")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.pressed.emit()
        user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread_id and sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
