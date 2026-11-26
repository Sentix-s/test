"""First-run setup: Ollama -> language model -> voice models, with progress bars.

Nothing is installed silently: the official Ollama installer opens with its
own window so the user sees and approves it (Windows may ask for permission).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from jarvis.ai.ollama_client import OllamaClient
from jarvis.core import ollama_setup
from jarvis.settings import AppPaths, Settings

log = logging.getLogger(__name__)


class _Sig(QObject):
    step = Signal(int, str, str)        # index, state (todo|work|ok|fail), text
    progress = Signal(int, int, int)    # index, done, total


def check_environment(settings: Settings) -> dict[str, Any]:
    """Fast check used at startup to decide whether the wizard is needed."""
    out: dict[str, Any] = {"ollama_installed": ollama_setup.find_ollama() is not None, "ollama_running": False,
                           "model": False}
    if not ollama_setup.is_local(settings.ai.host):
        out["ollama_installed"] = True
    with OllamaClient(settings.ai.host, connect_timeout=2.0) as c:
        out["ollama_running"] = c.is_available()
        if not out["ollama_running"] and out["ollama_installed"]:
            out["ollama_running"] = ollama_setup.start_ollama(settings.ai.host)
        if out["ollama_running"]:
            out["model"] = any(c.has_model(m) for m in [settings.ai.model, *settings.ai.fallback_models])
    return out


def needs_setup(env: dict[str, Any]) -> bool:
    return not (env["ollama_running"] and env["model"])


class SetupWizard(QDialog):
    STEPS = ["Ollama — движок нейросети", "Языковая модель", "Голос и распознавание речи"]

    def __init__(self, settings: Settings, paths: AppPaths, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.paths = paths
        self.setWindowTitle("JARVIS — первоначальная настройка")
        self.setMinimumSize(720, 480)
        self._sig = _Sig()
        self._sig.step.connect(self._on_step)
        self._sig.progress.connect(self._on_progress)
        self.done_steps: set[int] = set()   # GUI thread view
        self._ok: set[int] = set()           # worker view, updated before the signal is sent
        self._ok_lock = threading.Lock()

        root = QVBoxLayout(self)
        title = QLabel("Добро пожаловать. Подготовим JARVIS к работе.")
        title.setObjectName("h1")
        title.setWordWrap(True)
        root.addWidget(title)
        sub = QLabel("Всё работает локально на вашем компьютере. Понадобится около 7 ГБ на диске и интернет "
                     "для скачивания (один раз).")
        sub.setObjectName("dim")
        sub.setWordWrap(True)
        root.addWidget(sub)

        grid = QGridLayout()
        grid.setVerticalSpacing(14)
        self.icons: list[QLabel] = []
        self.texts: list[QLabel] = []
        self.bars: list[QProgressBar] = []
        self.buttons: list[QPushButton] = []
        actions: list[Callable[[], None]] = [self.install_ollama, self.pull_model, self.download_voice]
        labels = ["Установить", "Скачать (5.2 ГБ)", "Скачать (~0.5–1.6 ГБ)"]
        for i, name in enumerate(self.STEPS):
            icon = QLabel("○")
            icon.setFixedWidth(24)
            head = QLabel(f"<b>{name}</b>")
            text = QLabel("проверка…")
            text.setObjectName("dim")
            text.setWordWrap(True)
            bar = QProgressBar()
            bar.setVisible(False)
            btn = QPushButton(labels[i])
            btn.setFixedWidth(190)
            btn.setVisible(False)
            btn.clicked.connect(actions[i])
            col = QVBoxLayout()
            col.addWidget(head)
            col.addWidget(text)
            col.addWidget(bar)
            holder = QWidget()
            holder.setLayout(col)
            grid.addWidget(icon, i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(holder, i, 1)
            grid.addWidget(btn, i, 2, Qt.AlignmentFlag.AlignTop)
            self.icons.append(icon)
            self.texts.append(text)
            self.bars.append(bar)
            self.buttons.append(btn)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)
        root.addStretch(1)

        row = QHBoxLayout()
        self.auto_btn = QPushButton("Установить всё автоматически")
        self.auto_btn.setObjectName("primary")
        self.auto_btn.clicked.connect(self.install_all)
        self.finish_btn = QPushButton("Готово")
        self.finish_btn.setEnabled(False)
        self.finish_btn.clicked.connect(self.accept)
        skip = QPushButton("Закрыть")
        skip.clicked.connect(self.reject)
        row.addWidget(self.auto_btn)
        row.addStretch(1)
        row.addWidget(skip)
        row.addWidget(self.finish_btn)
        root.addLayout(row)
        self._bg(self._initial_check)

    # ------------------------------------------------------------- plumbing
    def _bg(self, fn: Callable[[], None]) -> threading.Thread:
        def run() -> None:
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("Setup step failed")
        t = threading.Thread(target=run, name="setup", daemon=True)
        t.start()
        return t

    def _emit(self, i: int, state: str, text: str) -> None:
        with self._ok_lock:
            if state == "ok":
                self._ok.add(i)
            else:
                self._ok.discard(i)
        self._sig.step.emit(i, state, text)

    def is_ok(self, i: int) -> bool:
        with self._ok_lock:
            return i in self._ok

    def _on_step(self, i: int, state: str, text: str) -> None:
        self.icons[i].setText({"todo": "○", "work": "◌", "ok": "✓", "fail": "✗"}[state])
        self.icons[i].setStyleSheet({"ok": "color:#3ddc97", "fail": "color:#ff4d5e", "work": "color:#ffb347"}.get(state, ""))
        self.texts[i].setText(text)
        self.buttons[i].setVisible(state in ("todo", "fail"))
        self.buttons[i].setEnabled(True)
        self.bars[i].setVisible(state == "work" and self.bars[i].maximum() > 0 and self.bars[i].value() > 0)
        if state == "ok":
            self.done_steps.add(i)
        self.finish_btn.setEnabled({0, 1} <= self.done_steps)
        self.auto_btn.setEnabled(not {0, 1, 2} <= self.done_steps)

    def _on_progress(self, i: int, done: int, total: int) -> None:
        bar = self.bars[i]
        bar.setVisible(True)
        bar.setMaximum(1000)
        bar.setValue(int(done / max(total, 1) * 1000))
        bar.setFormat(f"{done / 2**20:.0f} / {total / 2**20:.0f} МБ")

    # ---------------------------------------------------------------- steps
    def _initial_check(self) -> None:
        env = check_environment(self.settings)
        if env["ollama_running"]:
            self._emit(0, "ok", "установлена и запущена")
        elif env["ollama_installed"]:
            self._emit(0, "fail", "установлена, но не запускается — переустановите или запустите Ollama вручную")
        else:
            self._emit(0, "todo", "не установлена")
        if env["model"]:
            self._emit(1, "ok", f"{self.settings.ai.model} установлена")
        else:
            self._emit(1, "todo", f"нужно скачать {self.settings.ai.model}")
        from jarvis.voice.models import ModelStore

        store = ModelStore(self.paths.data_dir / "models")
        if store.voice_installed(self.settings.voice.tts_voice) and any(store.whisper_dir.glob("*/model.bin")):
            self._emit(2, "ok", "голос и распознавание установлены")
        else:
            self._emit(2, "todo", f"голос {self.settings.voice.tts_voice} и модель распознавания речи")

    def install_ollama(self) -> threading.Thread:
        def work() -> None:
            if ollama_setup.find_ollama() is None:
                self._emit(0, "work", "скачивание официального установщика Ollama…")
                try:
                    installer = ollama_setup.download_ollama_installer(lambda d, t: self._sig.progress.emit(0, d, t))
                except Exception as exc:  # noqa: BLE001
                    self._emit(0, "fail", f"не удалось скачать: {exc}. Скачайте вручную: ollama.com/download")
                    return
                self._emit(0, "work", "откроется установщик Ollama — нажмите «Install» и разрешите установку…")
                try:
                    ollama_setup.run_ollama_installer(installer)
                except OSError as exc:
                    self._emit(0, "fail", str(exc))
                    return
            self._emit(0, "work", "запуск Ollama…")
            if ollama_setup.start_ollama(self.settings.ai.host, timeout=60):
                self._emit(0, "ok", "установлена и запущена")
            else:
                self._emit(0, "fail", "Ollama не запустилась. Запустите её из меню Пуск и нажмите ещё раз.")
        return self._bg(work)

    def pull_model(self) -> threading.Thread:
        model = self.settings.ai.model

        def work() -> None:
            self._emit(1, "work", f"скачивание {model}… можно отойти, окно не закроется")
            try:
                with OllamaClient(self.settings.ai.host) as c:
                    c.pull(model, lambda d: d.get("total") and self._sig.progress.emit(1, d.get("completed", 0), d["total"]))
                self._emit(1, "ok", f"{model} установлена")
            except Exception as exc:  # noqa: BLE001
                self._emit(1, "fail", f"ошибка: {exc}")
        return self._bg(work)

    def download_voice(self) -> threading.Thread:
        def work() -> None:
            from jarvis.voice.models import ModelStore
            from jarvis.voice.stt import resolve_device

            store = ModelStore(self.paths.data_dir / "models")
            v = self.settings.voice
            try:
                self._emit(2, "work", f"голос {v.tts_voice}…")
                store.ensure_voice(v.tts_voice, lambda _l, d, t: self._sig.progress.emit(2, d, t))
                if v.tts_voice_en:
                    try:
                        store.ensure_voice(v.tts_voice_en, lambda _l, d, t: self._sig.progress.emit(2, d, t))
                    except OSError as exc:
                        log.warning("English voice: %s", exc)
                _dev, _ct, model = resolve_device(v.stt_device, v.stt_model)
                self._emit(2, "work", f"модель распознавания речи {model}…")
                store.ensure_whisper(model)
                self._emit(2, "ok", "голос и распознавание установлены")
            except Exception as exc:  # noqa: BLE001
                self._emit(2, "fail", f"ошибка: {exc}")
        return self._bg(work)

    def install_all(self) -> None:
        self.auto_btn.setEnabled(False)

        def work() -> None:
            if not self.is_ok(0):
                self.install_ollama().join()
            if self.is_ok(0) and not self.is_ok(1):
                self.pull_model().join()
            if not self.is_ok(2):
                self.download_voice().join()
        self._bg(work)
