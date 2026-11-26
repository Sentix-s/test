"""Application orchestrator: builds every component, owns the lifecycle
(power on / power off) and the currently running task.

Phase 1 lifecycle:  OFF -> STARTING -> READY -> (BUSY) -> STOPPING -> OFF
Phase 2 adds SLEEP/ACTIVE voice states on top of READY.
"""

from __future__ import annotations

import enum
import logging
import threading
from typing import Any, Callable, Iterator

from jarvis.ai.agent import Agent, AgentEvent
from jarvis.ai.ollama_client import OllamaClient
from jarvis.core.cancellation import CancelToken
from jarvis.core.errors import JarvisError, LLMUnavailableError, ModelNotFoundError
from jarvis.core.events import EventBus
from jarvis.memory import AuditLog, ConversationStore, Database, FactStore
from jarvis.security.permissions import ConfirmCallback, PermissionManager
from jarvis.settings import AppPaths, Settings
from jarvis.tools.base import ToolRegistry
from jarvis.tools.builtin import register_builtin_tools

log = logging.getLogger(__name__)


class AppState(str, enum.Enum):
    OFF = "off"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    STOPPING = "stopping"
    ERROR = "error"


class JarvisApp:
    def __init__(
        self,
        settings: Settings,
        paths: AppPaths,
        confirm: ConfirmCallback | None = None,
        llm: OllamaClient | None = None,
        database: Database | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths
        self.bus = EventBus()
        self.db = database or Database(paths.database_file)
        self.conversations = ConversationStore(self.db)
        self.facts = FactStore(self.db)
        self.audit = AuditLog(self.db)
        self.llm = llm or OllamaClient(settings.ai.host, settings.ai.request_timeout, settings.ai.connect_timeout)
        self.registry = register_builtin_tools(ToolRegistry())
        self.permissions = PermissionManager(settings.security, confirm)
        self.model: str | None = None
        self.agent: Agent | None = None
        self._state = AppState.OFF
        self._state_lock = threading.RLock()
        self._task_cancel: CancelToken | None = None
        self._task_lock = threading.Lock()

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> AppState:
        return self._state

    def _set_state(self, state: AppState, **info: Any) -> None:
        with self._state_lock:
            self._state = state
        log.info("State -> %s %s", state.value, info or "")
        self.bus.publish("state.changed", state=state.value, **info)

    # -------------------------------------------------------------- lifecycle
    def resolve_model(self) -> str:
        """Pick the configured model, or the first installed fallback."""
        for candidate in [self.settings.ai.model, *self.settings.ai.fallback_models]:
            if self.llm.has_model(candidate):
                if candidate != self.settings.ai.model:
                    log.warning("Model %s not installed, using fallback %s", self.settings.ai.model, candidate)
                return candidate
        raise ModelNotFoundError(
            f"Модель {self.settings.ai.model} не установлена. Выполните: ollama pull {self.settings.ai.model}"
        )

    def start(self, warmup: bool = True, progress: Callable[[str], None] | None = None) -> None:
        """Power on: check the backend, select and load the model."""
        say = progress or (lambda _m: None)
        self._set_state(AppState.STARTING)
        try:
            say("Проверка Ollama…")
            try:
                version = self.llm.version()
            except LLMUnavailableError:
                from jarvis.core.ollama_setup import start_ollama

                say("Запуск Ollama…")
                if not start_ollama(self.settings.ai.host):
                    raise
                version = self.llm.version()
            say(f"Ollama {version}. Выбор модели…")
            self.model = self.resolve_model()
            if warmup:
                say(f"Загрузка {self.model} в видеопамять…")
                self.llm.load(self.model, keep_alive=self.settings.ai.keep_alive)
            self.agent = Agent(
                backend=self.llm, registry=self.registry, permissions=self.permissions,
                conversations=self.conversations, facts=self.facts, audit=self.audit,
                ai_settings=self.settings.ai, memory_settings=self.settings.memory, model=self.model,
            )
        except JarvisError as exc:
            self._set_state(AppState.ERROR, error=str(exc))
            raise
        self._set_state(AppState.READY, model=self.model, ollama=version)

    def stop(self, unload_model: bool = True) -> None:
        """Power off: cancel the current task and free VRAM."""
        if self._state is AppState.OFF:
            return
        self._set_state(AppState.STOPPING)
        self.cancel_current_task("power off")
        if unload_model and self.model:
            try:
                self.llm.unload(self.model)
                log.info("Model %s unloaded", self.model)
            except LLMUnavailableError:
                pass  # Ollama already gone: nothing to unload
            except JarvisError as exc:
                log.warning("Could not unload model: %s", exc)
        self._set_state(AppState.OFF)

    def close(self) -> None:
        self.stop()
        self.llm.close()
        self.db.close()

    # ------------------------------------------------------------------ tasks
    def cancel_current_task(self, reason: str = "stopped by user") -> bool:
        with self._task_lock:
            token = self._task_cancel
        if token is None:
            return False
        token.cancel(reason)
        self.bus.publish("task.cancel_requested", reason=reason)
        return True

    def ask(self, conversation_id: int, text: str) -> Iterator[AgentEvent]:
        """Run one user request through the agent, streaming events.
        Only one task runs at a time; a new request is refused while busy."""
        if self.agent is None or self._state not in (AppState.READY,):
            raise JarvisError(f"JARVIS is not ready (state: {self._state.value})")
        token = CancelToken()
        with self._task_lock:
            self._task_cancel = token
        self._set_state(AppState.BUSY)
        self.bus.publish("task.started", conversation_id=conversation_id, text=text)
        try:
            for event in self.agent.run(conversation_id, text, token):
                self.bus.publish(f"agent.{event.kind}", text=event.text, **event.data)
                yield event
        finally:
            with self._task_lock:
                self._task_cancel = None
            self.bus.publish("task.finished", conversation_id=conversation_id)
            if self._state is AppState.BUSY:
                self._set_state(AppState.READY, model=self.model)
