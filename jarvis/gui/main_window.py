"""Main window, tray icon and the compact tray window."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QCursor, QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from jarvis import __version__
from jarvis.gui.controller import ConfirmTicket, Controller
from jarvis.gui.core_widget import CoreWidget
from jarvis.gui.icon import app_icon
from jarvis.gui.pages.main_page import VOICE_LABELS, MainPage
from jarvis.gui.pages.models_page import ModelsPage
from jarvis.gui.pages.system_page import SystemPage
from jarvis.gui.pages.voice_page import VoicePage

log = logging.getLogger(__name__)


class CompactWindow(QWidget):
    """Small animated window shown when the tray icon is clicked."""

    def __init__(self, ctl: Controller, open_main: callable) -> None:  # type: ignore[valid-type]
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.ctl = ctl
        self.setFixedSize(260, 330)
        self.setObjectName("compact")
        self.setStyleSheet("QWidget#compact { border: 1px solid #1d2a45; border-radius: 12px; }")
        lay = QVBoxLayout(self)
        self.core = CoreWidget()
        lay.addWidget(self.core, 1)
        self.label = QLabel("Выключен")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.label)
        row = QHBoxLayout()
        mic = QPushButton("🎙")
        mic.setToolTip("Слушать")
        mic.clicked.connect(ctl.mic_pressed)
        power = QPushButton("⏻")
        power.setToolTip("Включить / выключить")
        power.clicked.connect(ctl.toggle_power)
        full = QPushButton("Открыть")
        full.clicked.connect(lambda: (self.hide(), open_main()))
        for b in (mic, power, full):
            row.addWidget(b)
        lay.addLayout(row)
        ctl.power_changed.connect(self._on_power)
        ctl.voice_state.connect(self._on_voice)

    def _on_power(self, power: str) -> None:
        self.label.setText({"off": "Выключен", "starting": "Запуск…", "on": "Онлайн", "error": "Ошибка"}[power])
        self.core.set_state({"off": "off", "starting": "starting", "on": "sleep", "error": "error"}[power])

    def _on_voice(self, state: str) -> None:
        if self.ctl.power == "on":
            self.label.setText("Онлайн · " + VOICE_LABELS.get(state, state))
            self.core.set_state(state if state != "off" else "sleep")

    def show_near_cursor(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        pos = QCursor.pos() - QPoint(self.width() // 2, self.height() + 12)
        pos.setX(max(geo.left() + 8, min(pos.x(), geo.right() - self.width() - 8)))
        pos.setY(max(geo.top() + 8, min(pos.y(), geo.bottom() - self.height() - 8)))
        self.move(pos)
        self.show()
        self.raise_()
        self.activateWindow()

    def focusOutEvent(self, event: object) -> None:  # noqa: N802
        self.hide()


class MainWindow(QMainWindow):
    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        self.quitting = False
        self.setWindowTitle(f"JARVIS {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1180, 760)
        self.setMinimumSize(860, 560)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(200)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 18, 10, 12)
        brand = QLabel("J.A.R.V.I.S.")
        brand.setObjectName("h1")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(brand)
        sl.addSpacing(16)
        self.stack = QStackedWidget()
        self.main_page = MainPage(ctl)
        self.voice_page = VoicePage(ctl)
        self.models_page = ModelsPage(ctl)
        self.system_page = SystemPage(ctl)
        self.nav: list[QPushButton] = []
        for i, (name, page) in enumerate([("◉  Главная", self.main_page), ("🎙  Голос", self.voice_page),
                                          ("🧠  Модели ИИ", self.models_page), ("⚙  Система", self.system_page)]):
            self.stack.addWidget(page)
            b = QPushButton(name)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, idx=i: self.show_page(idx))
            sl.addWidget(b)
            self.nav.append(b)
        sl.addStretch(1)
        self.sys_label = QLabel("")
        self.sys_label.setObjectName("dim")
        self.sys_label.setWordWrap(True)
        sl.addWidget(self.sys_label)
        root.addWidget(side)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.show_page(0)

        # tray
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("JARVIS")
        menu = QMenu()
        for text, slot in [("Открыть JARVIS", self.show_main), ("Слушать", ctl.mic_pressed),
                           ("Включить / выключить", ctl.toggle_power), ("Остановить задачу", ctl.stop_task)]:
            act = QAction(text, self)
            act.triggered.connect(slot)
            menu.addAction(act)
        menu.addSeparator()
        quit_act = QAction("Выход", self)
        quit_act.triggered.connect(self.quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.compact = CompactWindow(ctl, self.show_main)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        ctl.confirm_needed.connect(self._confirm)
        self._level_timer = QTimer(self)
        self._level_timer.timeout.connect(self._update_level)
        self._level_timer.start(40)
        self._sys_timer = QTimer(self)
        self._sys_timer.timeout.connect(self._update_sys)
        self._sys_timer.start(2000)
        self._update_sys()

    def show_page(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.nav):
            b.setChecked(i == idx)
        if idx == 2:
            self.models_page.refresh()
        elif idx == 3:
            self.system_page.refresh()

    def show_main(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def on_hotkey(self) -> None:
        self.show_main()
        self.ctl.mic_pressed()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.compact.isVisible():
                self.compact.hide()
            else:
                self.compact.show_near_cursor()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_main()

    def _update_level(self) -> None:
        level = min(1.0, self.ctl.mic_level() / 3000.0)
        self.main_page.core.set_level(level)
        self.compact.core.set_level(level)

    def _update_sys(self) -> None:
        import psutil

        vm = psutil.virtual_memory()
        self.sys_label.setText(f"CPU {psutil.cpu_percent():.0f}% · RAM {vm.percent:.0f}%")

    def _confirm(self, ticket: ConfirmTicket) -> None:
        req = ticket.request
        self.show_main()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("JARVIS — нужно подтверждение")
        box.setText(f"JARVIS хочет выполнить действие с риском «{req.risk.value}»:")
        box.setInformativeText(req.summary)
        allow = box.addButton("Разрешить", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Запретить", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        ticket.answer(box.clickedButton() is allow)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self.quitting and self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage("JARVIS", "JARVIS работает в трее. Выход — правой кнопкой по значку.",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)
            return
        event.accept()

    def quit(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.quitting = True
        self.tray.hide()
        self.close()
        QApplication.quit()
