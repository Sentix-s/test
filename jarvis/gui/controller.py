"""Bridges the JARVIS core and voice engine to the Qt GUI.

All slow work (starting Ollama, loading models, answering) runs on
background threads; results come back to the GUI thread as Qt signals.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

from jarvis.core.app import AppState, JarvisApp
from jarvis.core.errors import JarvisError
from jarvis.security.permissions import ConfirmRequest
from jarvis.settings import AppPaths, Settings, load_settings, save_settings

log = logging.getLogger(__name__)


class ConfirmTicket:
    """A pending confirmation: the worker waits until the GUI answers."""

    def __init__(self, request: ConfirmRequest) -> None:
        self.request = request
        self.allowed = False
        self.done = threading.Event()

    def answer(self, allowed: bool) -> None:
        self.allowed = allowed
        self.done.set()


class Controller(QObject):
    power_changed = Signal(str)            # off | starting | on | error
    status = Signal(str)
    app_state = Signal(str)                # ready | busy | ...
    voice_state = Signal(str)              # off | sleep | active | processing | speaking
    voice_enabled_changed = Signal(bool)
    user_message = Signal(str, str)        # text, source ("text" | "voice")
    answer_delta = Signal(str)
    answer_done = Signal(str)
    tool_event = Signal(str, str)          # kind (start|ok|fail|denied), text
    error = Signal(str)
    confirm_needed = Signal(object)        # ConfirmTicket
    conversation_changed = Signal(int)

    def __init__(self, settings: Settings, paths: AppPaths, app: JarvisApp | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.paths = paths
        self.app = app or JarvisApp(settings, paths)
        self.app.permissions.set_confirm_callback(self._confirm)
        self.app.bus.subscribe("state.changed", lambda e: self.app_state.emit(e.data.get("state", "")))
        self.voice: Any = None
        self.power = "off"
        self.conversation_id = self.app.conversations.latest_id() or self.app.conversations.create()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ helpers
    def _bg(self, target: Any, name: str) -> threading.Thread:
        from jarvis.gui.workers import run_bg

        return run_bg(target, name, on_error=lambda exc: self.error.emit(str(exc)))

    def _confirm(self, req: ConfirmRequest) -> bool:
        ticket = ConfirmTicket(req)
        self.confirm_needed.emit(ticket)
        ticket.done.wait()
        return ticket.allowed

    def _set_power(self, value: str) -> None:
        self.power = value
        self.power_changed.emit(value)

    def save_settings(self) -> None:
        file_settings = load_settings(self.paths.settings_file)
        file_settings.voice = self.settings.voice.model_copy()
        file_settings.ai = self.settings.ai.model_copy()
        save_settings(file_settings, self.paths.settings_file)

    # ------------------------------------------------------------- power
    def power_on(self) -> threading.Thread | None:
        if self.power in ("on", "starting"):
            return None

        def work() -> None:
            self._set_power("starting")
            try:
                self.app.start(progress=self.status.emit)
                self._set_power("on")
                self.status.emit(f"Готов · {self.app.model}")
                if self.settings.voice.enabled:
                    self._start_voice()
            except JarvisError as exc:
                self._set_power("error")
                self.status.emit(str(exc))
                self.error.emit(str(exc))
        return self._bg(work, "power-on")

    def power_off(self) -> threading.Thread | None:
        if self.power == "off":
            return None

        def work() -> None:
            self.status.emit("Выключение…")
            self._stop_voice()
            self.app.stop()
            self._set_power("off")
            self.status.emit("Выключен. Микрофон закрыт, модель выгружена из памяти.")
        return self._bg(work, "power-off")

    def toggle_power(self) -> None:
        if self.power in ("on", "starting"):
            self.power_off()
        else:
            self.power_on()

    def shutdown(self) -> None:
        """Application exit: stop everything synchronously, then wait for background workers."""
        from jarvis.gui.workers import wait_all

        self.app.cancel_current_task("exit")
        self._stop_voice()
        wait_all(10)
        self.app.close()

    # ------------------------------------------------------------- voice
    def _voice_listener(self, name: str, data: dict[str, Any]) -> None:
        if name == "state":
            self.voice_state.emit(data["state"])
        elif name == "heard" and data.get("text"):
            self.user_message.emit(data["text"], "voice")
        elif name == "agent.text":
            self.answer_delta.emit(data.get("text", ""))
        elif name == "answered":
            self.answer_done.emit(data.get("text", ""))
        elif name.startswith("agent.tool_") or name == "agent.denied":
            self._tool_signal(name.split(".", 1)[1], data)
        elif name == "error":
            self.error.emit(data.get("text", ""))

    def _tool_signal(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "tool_start":
            self.tool_event.emit("start", data.get("text", ""))
        elif kind == "tool_end":
            self.tool_event.emit("ok" if data.get("ok") else "fail", str(data.get("tool", "")))
        elif kind == "denied":
            self.tool_event.emit("denied", data.get("text", ""))

    def _start_voice(self) -> None:
        from jarvis.voice.engine import VoiceEngine

        with self._lock:
            if self.voice is not None and self.voice.running:
                return
            self.voice = VoiceEngine(self.app, self.paths, self._voice_listener, conversation=lambda: self.conversation_id)
        try:
            self.voice.load(progress=self.status.emit)
            self.voice.start()
            self.voice_enabled_changed.emit(True)
            self.status.emit(f"Готов · {self.app.model} · скажите «{self.settings.voice.wake_words[0].capitalize()}»")
        except Exception as exc:  # noqa: BLE001
            log.exception("Voice start failed")
            self.voice = None
            self.voice_enabled_changed.emit(False)
            self.voice_state.emit("off")
            self.error.emit(f"Голос не запустился: {exc}")

    def _stop_voice(self) -> None:
        with self._lock:
            voice, self.voice = self.voice, None
        if voice is not None:
            voice.stop()
        self.voice_state.emit("off")
        self.voice_enabled_changed.emit(False)

    def set_voice_enabled(self, enabled: bool) -> None:
        self.settings.voice.enabled = enabled
        if self.power != "on":
            return
        self._bg(self._start_voice if enabled else self._stop_voice, "voice-toggle")

    def mic_pressed(self) -> None:
        """Mic button: listen now (starting the voice engine if needed)."""
        if self.power != "on":
            self.power_on()
            return
        if self.voice is None:
            self.settings.voice.enabled = True
            self._bg(lambda: (self._start_voice(), self.voice and self.voice.activate()), "voice-mic")
        else:
            self.voice.activate()

    def restart_voice(self) -> None:
        """Apply changed voice settings."""
        if self.power == "on" and self.settings.voice.enabled:
            self._bg(lambda: (self._stop_voice(), self._start_voice()), "voice-restart")

    def mic_level(self) -> float:
        v = self.voice
        if v is None or v.input is None or v.input.muted:
            return 0.0
        return float(v.input.level)

    # ------------------------------------------------------------- chat
    def send_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.power != "on":
            self.error.emit("JARVIS выключен — нажмите кнопку питания.")
            return
        if self.app.state is AppState.BUSY:
            self.error.emit("Подождите, я ещё выполняю предыдущую задачу (или нажмите «Стоп»).")
            return
        self.user_message.emit(text, "text")
        cid = self.conversation_id

        def work() -> None:
            final = ""
            for ev in self.app.ask(cid, text):
                if ev.kind == "text":
                    self.answer_delta.emit(ev.text)
                elif ev.kind == "done":
                    final = ev.text
                elif ev.kind in ("tool_start", "tool_end", "denied"):
                    self._tool_signal(ev.kind, {"text": ev.text, **ev.data})
                elif ev.kind == "cancelled":
                    final = "[остановлено]"
                elif ev.kind == "error":
                    self.error.emit(ev.text)
            self.answer_done.emit(final)
        self._bg(work, "chat")

    def stop_task(self) -> None:
        if self.voice is not None:
            self.voice.stop_speaking()
        self.app.cancel_current_task("stop button")

    def new_conversation(self) -> None:
        self.conversation_id = self.app.conversations.create()
        self.conversation_changed.emit(self.conversation_id)

    def open_conversation(self, cid: int) -> None:
        if self.app.conversations.exists(cid):
            self.conversation_id = cid
            self.conversation_changed.emit(cid)
