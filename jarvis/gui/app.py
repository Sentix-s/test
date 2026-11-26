"""GUI entry point."""

from __future__ import annotations

import logging
import sys

from jarvis import __version__

log = logging.getLogger(__name__)


def run_gui(minimized: bool = False, force_setup: bool = False, home: str | None = None, smoke: bool = False) -> int:
    import os

    if home:
        os.environ["JARVIS_HOME"] = home
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from jarvis.core.errors import ConfigError
    from jarvis.gui.controller import Controller
    from jarvis.gui.hotkey import GlobalHotkey
    from jarvis.gui.icon import app_icon
    from jarvis.gui.main_window import MainWindow
    from jarvis.gui.setup_wizard import SetupWizard, check_environment, needs_setup
    from jarvis.gui.theme import STYLESHEET
    from jarvis.logging_setup import setup_logging
    from jarvis.settings import AppPaths, load_settings

    if sys.platform == "win32":  # own taskbar icon instead of python.exe's
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JARVIS.Assistant")
        except (AttributeError, OSError):
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setApplicationName("JARVIS")
    qapp.setApplicationVersion(__version__)
    qapp.setWindowIcon(app_icon())
    qapp.setStyleSheet(STYLESHEET)
    qapp.setQuitOnLastWindowClosed(False)

    paths = AppPaths.default().ensure()
    try:
        settings = load_settings(paths.settings_file)
    except ConfigError as exc:
        QMessageBox.critical(None, "JARVIS", f"Ошибка в настройках:\n{exc}\n\nФайл: {paths.settings_file}")
        return 2
    setup_logging(settings.logging, paths.log_dir)
    log.info("JARVIS GUI %s starting", __version__)

    if smoke:  # packaging check: build every window and page, render, exit
        from PySide6.QtCore import QTimer

        ctl = Controller(settings, paths)
        win = MainWindow(ctl)
        win.show()
        for i in range(4):
            win.show_page(i)
        wizard = SetupWizard(settings, paths)
        wizard.show()
        QTimer.singleShot(2500, qapp.quit)
        qapp.exec()
        ok = not win.grab().isNull()
        ctl.shutdown()
        _teardown(qapp, win, wizard)
        log.info("Smoke test %s", "passed" if ok else "failed")
        return 0 if ok else 1

    if force_setup or needs_setup(check_environment(settings)):
        wizard = SetupWizard(settings, paths)
        wizard.setWindowIcon(app_icon())
        wizard.exec()

    ctl = Controller(settings, paths)
    win = MainWindow(ctl)
    hotkey = GlobalHotkey()
    hotkey.pressed.connect(win.on_hotkey)
    hotkey.start()
    if minimized and win.tray.isVisible():
        win.hide()
    else:
        win.show()
    ctl.power_on()
    code = qapp.exec()
    hotkey.stop()
    ctl.shutdown()
    _teardown(qapp, win)
    log.info("JARVIS GUI stopped")
    return code


def _teardown(qapp: object, *widgets: object) -> None:
    """Destroy windows while Qt is still fully alive (avoids crashes at interpreter exit)."""
    from jarvis.gui.workers import wait_all

    wait_all(5)
    for w in widgets:
        try:
            w.hide()  # type: ignore[attr-defined]
            w.deleteLater()  # type: ignore[attr-defined]
        except RuntimeError:
            pass
    qapp.processEvents()  # type: ignore[attr-defined]
