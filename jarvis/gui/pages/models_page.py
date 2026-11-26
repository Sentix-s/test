"""AI Models section: Ollama status, installed models, switching and downloading models."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from jarvis.ai.ollama_client import OllamaClient
from jarvis.gui.controller import Controller

log = logging.getLogger(__name__)

RECOMMENDED = [
    ("qwen3:8b", "Основная — лучшая для RTX 3060 (5.2 ГБ)"),
    ("qwen2.5:7b-instruct", "Запасная, очень надёжные инструменты (4.7 ГБ)"),
    ("qwen3:4b", "Лёгкая — для режима ECO (2.6 ГБ)"),
]


class _Sig(QObject):
    progress = Signal(int, int)
    message = Signal(str)
    refreshed = Signal(object)


class ModelsPage(QWidget):
    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        self._sig = _Sig()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel("Модели ИИ")
        title.setObjectName("h1")
        root.addWidget(title)
        self.info = QLabel("…")
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        root.addWidget(self._h2("Установленные модели"))
        self.models = QListWidget()
        self.models.setMaximumHeight(180)
        root.addWidget(self.models)
        row = QHBoxLayout()
        use = QPushButton("Использовать выбранную")
        use.clicked.connect(self._use_selected)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.refresh)
        row.addWidget(use)
        row.addWidget(refresh)
        row.addStretch(1)
        root.addLayout(row)

        root.addWidget(self._h2("Скачать модель"))
        for name, desc in RECOMMENDED:
            b = QPushButton(f"{name} — {desc}")
            b.setStyleSheet("text-align: left;")
            b.clicked.connect(lambda _=False, n=name: self.pull(n))
            root.addWidget(b)
        row2 = QHBoxLayout()
        self.custom = QLineEdit()
        self.custom.setPlaceholderText("другая модель из ollama.com/library, например llama3.1:8b")
        pull = QPushButton("Скачать")
        pull.clicked.connect(lambda: self.custom.text().strip() and self.pull(self.custom.text().strip()))
        row2.addWidget(self.custom, 1)
        row2.addWidget(pull)
        root.addLayout(row2)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        root.addWidget(self.bar)
        self.msg = QLabel("")
        self.msg.setObjectName("dim")
        self.msg.setWordWrap(True)
        root.addWidget(self.msg)
        root.addStretch(1)

        self._sig.progress.connect(self._on_progress)
        self._sig.message.connect(self.msg.setText)
        self._sig.refreshed.connect(self._on_refreshed)
        ctl.power_changed.connect(lambda _p: self.refresh())

    @staticmethod
    def _h2(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("h2")
        return lbl

    def _client(self) -> OllamaClient:
        a = self.ctl.settings.ai
        return OllamaClient(a.host, connect_timeout=a.connect_timeout)

    def refresh(self) -> None:
        def work() -> None:
            data: dict = {}
            try:
                with self._client() as c:
                    data["version"] = c.version()
                    data["models"] = c.list_models()
                    data["loaded"] = c.loaded_models()
            except Exception as exc:  # noqa: BLE001
                data["error"] = str(exc)
            self._sig.refreshed.emit(data)
        threading.Thread(target=work, name="models-refresh", daemon=True).start()

    def _on_refreshed(self, data: dict) -> None:
        a = self.ctl.settings.ai
        if "error" in data:
            self.info.setText(f"Ollama недоступна: {data['error']}")
            return
        loaded = ", ".join(f"{m['name']} ({m.get('size_vram', 0) / 2**30:.1f} ГБ VRAM)" for m in data["loaded"]) or "нет"
        self.info.setText(f"Ollama {data['version']} · выбрана модель: <b>{a.model}</b> · сейчас в памяти: {loaded}<br>"
                          f"Контекст {a.num_ctx} токенов · температура {a.temperature}")
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.models.clear()
        for m in data["models"]:
            item = QListWidgetItem(f"{m['name']}   ·   {m.get('size', 0) / 2**30:.1f} ГБ" + ("   ★" if m["name"] == a.model else ""))
            item.setData(Qt.ItemDataRole.UserRole, m["name"])
            self.models.addItem(item)

    def _use_selected(self) -> None:
        item = self.models.currentItem()
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        self.ctl.settings.ai.model = name
        self.ctl.save_settings()
        self.msg.setText(f"Модель {name} выбрана. Выключите и включите JARVIS, чтобы загрузить её.")
        self.refresh()

    def pull(self, name: str) -> None:
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.msg.setText(f"Скачивание {name}…")

        def work() -> None:
            try:
                with self._client() as c:
                    c.pull(name, lambda d: d.get("total") and self._sig.progress.emit(d.get("completed", 0), d["total"]))
                self._sig.message.emit(f"Готово: {name}. Выберите её в списке и нажмите «Использовать».")
            except Exception as exc:  # noqa: BLE001
                self._sig.message.emit(f"Ошибка скачивания: {exc}")
            self.refresh()
        threading.Thread(target=work, name="model-pull", daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        self.bar.setMaximum(1000)
        self.bar.setValue(int(done / max(total, 1) * 1000))
        self.bar.setFormat(f"{done / 2**30:.2f} / {total / 2**30:.2f} ГБ")
