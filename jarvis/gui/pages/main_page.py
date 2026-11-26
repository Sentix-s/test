"""Main section: core, power/mic/stop controls, chat, history, file drop."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from jarvis.gui.chat_view import ChatView
from jarvis.gui.controller import Controller
from jarvis.gui.core_widget import CoreWidget

VOICE_LABELS = {"off": "голос выключен", "sleep": "жду ключевое слово", "active": "слушаю",
                "processing": "думаю…", "speaking": "говорю"}


class MainPage(QWidget):
    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        self.setAcceptDrops(True)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ---- left column: core + controls
        left = QVBoxLayout()
        self.core = CoreWidget()
        self.core.setMinimumSize(260, 260)
        left.addWidget(self.core, 1)
        self.state_label = QLabel("Выключен")
        self.state_label.setObjectName("h2")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.state_label)
        self.status_label = QLabel("")
        self.status_label.setObjectName("dim")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.status_label)

        self.power_btn = QPushButton("⏻  Включить")
        self.power_btn.setObjectName("primary")
        self.power_btn.setMinimumHeight(54)
        self.power_btn.setToolTip("Полное включение / выключение JARVIS (модель и микрофон)")
        self.power_btn.clicked.connect(ctl.toggle_power)
        left.addWidget(self.power_btn)
        row = QHBoxLayout()
        self.mic_btn = QPushButton("🎙  Слушать")
        self.mic_btn.setObjectName("mic")
        self.mic_btn.setToolTip("Начать слушать без ключевого слова")
        self.mic_btn.clicked.connect(ctl.mic_pressed)
        self.stop_btn = QPushButton("■  Стоп")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setToolTip("Остановить текущую задачу и речь")
        self.stop_btn.clicked.connect(ctl.stop_task)
        row.addWidget(self.mic_btn)
        row.addWidget(self.stop_btn)
        left.addLayout(row)
        root.addLayout(left, 2)

        # ---- right column: history + chat + input
        right = QVBoxLayout()
        top = QHBoxLayout()
        self.history = QComboBox()
        self.history.setToolTip("История разговоров")
        self.history.activated.connect(self._history_selected)
        new_btn = QPushButton("＋ Новый разговор")
        new_btn.clicked.connect(ctl.new_conversation)
        top.addWidget(self.history, 1)
        top.addWidget(new_btn)
        right.addLayout(top)
        self.chat = ChatView()
        right.addWidget(self.chat, 1)
        inp = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Напишите JARVIS… (можно перетащить файл в окно)")
        self.input.returnPressed.connect(self._send)
        send = QPushButton("➤")
        send.setObjectName("primary")
        send.setFixedWidth(52)
        send.clicked.connect(self._send)
        inp.addWidget(self.input, 1)
        inp.addWidget(send)
        right.addLayout(inp)
        root.addLayout(right, 3)

        ctl.power_changed.connect(self._on_power)
        ctl.status.connect(self.status_label.setText)
        ctl.voice_state.connect(self._on_voice_state)
        ctl.app_state.connect(self._on_app_state)
        ctl.user_message.connect(self.chat.add_user)
        ctl.answer_delta.connect(self.chat.append_answer)
        ctl.answer_done.connect(self._on_answer_done)
        ctl.tool_event.connect(self._on_tool)
        ctl.error.connect(lambda m: self.chat.add_tool(f"⚠ {m}"))
        ctl.conversation_changed.connect(lambda _cid: self.load_conversation())
        self._voice = "off"
        self.load_conversation()

    # ------------------------------------------------------------- slots
    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.ctl.send_text(text)

    def _on_power(self, power: str) -> None:
        labels = {"off": ("⏻  Включить", "Выключен"), "starting": ("⏻  Выключить", "Запуск…"),
                  "on": ("⏻  Выключить", "Онлайн"), "error": ("⏻  Включить", "Ошибка запуска")}
        btn, state = labels.get(power, ("⏻", power))
        self.power_btn.setText(btn)
        self.state_label.setText(state)
        self.core.set_state({"off": "off", "starting": "starting", "on": "sleep", "error": "error"}[power])

    def _on_voice_state(self, state: str) -> None:
        self._voice = state
        if self.ctl.power == "on":
            self.state_label.setText("Онлайн · " + VOICE_LABELS.get(state, state))
            self.core.set_state(state if state != "off" else "sleep")
        self.mic_btn.setProperty("active", state in ("active", "processing", "speaking"))
        self.mic_btn.style().unpolish(self.mic_btn)
        self.mic_btn.style().polish(self.mic_btn)

    def _on_app_state(self, state: str) -> None:
        if self._voice in ("off", "sleep", "active") and self.ctl.power == "on":
            self.core.set_state("processing" if state == "busy" else (self._voice if self._voice != "off" else "sleep"))

    def _on_answer_done(self, text: str) -> None:
        self.chat.end_answer(text)
        self.refresh_history()

    def _on_tool(self, kind: str, text: str) -> None:
        icon = {"start": "⚙", "ok": "✓", "fail": "✗", "denied": "⛔"}[kind]
        if kind != "start":
            text = f"{text} — {'готово' if kind == 'ok' else 'ошибка' if kind == 'fail' else 'отклонено'}"
        self.chat.add_tool(f"{icon} {text}")

    def _history_selected(self, index: int) -> None:
        cid = self.history.itemData(index)
        if cid is not None and cid != self.ctl.conversation_id:
            self.ctl.open_conversation(int(cid))

    # ------------------------------------------------------------- data
    def refresh_history(self) -> None:
        self.history.blockSignals(True)
        self.history.clear()
        for info in self.ctl.app.conversations.list(30):
            title = info.title or "Новый разговор"
            self.history.addItem(f"{info.updated_at[:16].replace('T', ' ')} · {title[:60]}", info.id)
        idx = self.history.findData(self.ctl.conversation_id)
        if idx < 0:
            self.history.insertItem(0, "Новый разговор", self.ctl.conversation_id)
            idx = 0
        self.history.setCurrentIndex(idx)
        self.history.blockSignals(False)

    def load_conversation(self) -> None:
        self.chat.clear()
        for m in self.ctl.app.conversations.messages(self.ctl.conversation_id, limit=60):
            if m.role == "user":
                self.chat.add_user(m.content)
            elif m.role == "assistant" and m.content:
                self.chat.add_bot(m.content)
        self.refresh_history()

    # ------------------------------------------------------------- file drop
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            current = self.input.text().strip()
            files = " ".join(f'"{p}"' for p in paths)
            self.input.setText(f"{current} {files}".strip() if current else f"Файл: {files} — ")
            self.input.setFocus()
            event.acceptProposedAction()
