"""Chat transcript made of message bubbles; the last JARVIS bubble grows
while the answer streams in."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget


class ChatView(QScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(10)
        self._layout.addStretch(1)
        self.setWidget(self._inner)
        self._streaming: QLabel | None = None
        self._stream_text = ""

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._streaming = None

    def _max_width(self, kind: str) -> int:
        avail = max(300, self.viewport().width() - 40)
        return int(avail * (0.75 if kind == "user" else 0.85))

    def _fit(self, label: QLabel, kind: str) -> None:
        """Word-wrapped QLabels shrink to a narrow column; size them to their text instead."""
        if kind == "tool":
            return
        fm = label.fontMetrics()
        longest = max((fm.horizontalAdvance(line) for line in (label.text() or " ").splitlines()), default=0)
        label.setFixedWidth(min(longest + 32, self._max_width(kind)))

    def _add(self, text: str, kind: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(kind != "tool")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName({"user": "bubbleUser", "bot": "bubbleBot", "tool": "bubbleTool"}[kind])
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if kind == "user":
            row.addStretch(1)
            row.addWidget(label, 0)
        else:
            row.addWidget(label, 0)
            row.addStretch(1)
        self._fit(label, kind)
        holder = QWidget()
        holder.setLayout(row)
        self._layout.insertWidget(self._layout.count() - 1, holder)
        QTimer.singleShot(0, self._scroll_down)
        return label

    def _scroll_down(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def add_user(self, text: str, source: str = "text") -> None:
        self.end_answer()
        self._add(("🎙 " if source == "voice" else "") + text, "user")

    def add_tool(self, text: str) -> None:
        self._add(text, "tool")

    def add_bot(self, text: str) -> None:
        self.end_answer()
        self._add(text, "bot")

    def append_answer(self, delta: str) -> None:
        if self._streaming is None:
            self._stream_text = ""
            self._streaming = self._add("", "bot")
        self._stream_text += delta
        self._streaming.setText(self._stream_text.lstrip())
        self._fit(self._streaming, "bot")
        QTimer.singleShot(0, self._scroll_down)

    def end_answer(self, final: str = "") -> None:
        if self._streaming is not None:
            if final and not self._stream_text.strip():
                self._streaming.setText(final)
                self._fit(self._streaming, "bot")
        elif final:
            self._add(final, "bot")
        self._streaming = None
        self._stream_text = ""

    def bubble_texts(self) -> list[str]:
        out = []
        for i in range(self._layout.count()):
            w = self._layout.itemAt(i).widget()
            if w is not None:
                out.extend(lbl.text() for lbl in w.findChildren(QLabel))
        return out
