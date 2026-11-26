"""System section: autostart, permissions and the action journal."""

from __future__ import annotations


from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from jarvis.core import autostart
from jarvis.gui.controller import Controller


class SystemPage(QWidget):
    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel("Система")
        title.setObjectName("h1")
        root.addWidget(title)

        self.autostart = QCheckBox("Запускать JARVIS вместе с Windows (свёрнутым в трей)")
        self.autostart.setEnabled(autostart.supported())
        self.autostart.setChecked(autostart.is_enabled())
        self.autostart.toggled.connect(self._toggle_autostart)
        root.addWidget(self.autostart)
        self.confirm_moderate = QCheckBox("Спрашивать подтверждение и для умеренно рискованных действий")
        self.confirm_moderate.setChecked(ctl.settings.security.confirm_moderate)
        self.confirm_moderate.toggled.connect(self._toggle_confirm)
        root.addWidget(self.confirm_moderate)
        note = QLabel("Опасные действия (удаление, отправка писем, установка программ, неизвестные .exe) "
                      "всегда требуют вашего подтверждения. Горячая клавиша: Ctrl+Alt+J — показать окно и слушать.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        root.addWidget(note)
        self.msg = QLabel("")
        self.msg.setObjectName("dim")
        root.addWidget(self.msg)

        row = QHBoxLayout()
        h = QLabel("Журнал действий")
        h.setObjectName("h2")
        row.addWidget(h)
        row.addStretch(1)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.refresh)
        logs = QPushButton("Папка логов")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ctl.paths.log_dir))))
        row.addWidget(refresh)
        row.addWidget(logs)
        root.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["время", "действие", "риск", "решение", "результат"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 1)
        self.refresh()

    def _toggle_autostart(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
            self.msg.setText("Автозапуск включён." if on else "Автозапуск выключен.")
        except OSError as exc:
            self.msg.setText(str(exc))

    def _toggle_confirm(self, on: bool) -> None:
        self.ctl.settings.security.confirm_moderate = on
        from jarvis.settings import load_settings, save_settings

        fs = load_settings(self.ctl.paths.settings_file)
        fs.security.confirm_moderate = on
        save_settings(fs, self.ctl.paths.settings_file)

    def refresh(self) -> None:
        rows = self.ctl.app.audit.recent(100)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            ok = "" if r["ok"] is None else ("✓" if r["ok"] else "✗")
            for j, val in enumerate([r["ts"][:19].replace("T", " "), f"{r['tool']} {r['arguments']}", r["risk"],
                                     r["decision"], ok]):
                self.table.setItem(i, j, QTableWidgetItem(str(val)))


