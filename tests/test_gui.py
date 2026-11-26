"""GUI tests (offscreen Qt): controller, chat in the window, power, confirmations,
voice settings, setup wizard."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from fake_ollama import text_turn, tool_turn  # noqa: E402
from jarvis.ai.ollama_client import OllamaClient  # noqa: E402
from jarvis.core.app import JarvisApp  # noqa: E402
from jarvis.tools.base import RiskLevel, tool  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from jarvis.gui.theme import STYLESHEET

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(STYLESHEET)
    return app


def pump(qapp, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window(qapp, settings, paths):
    from jarvis.gui.controller import Controller
    from jarvis.gui.main_window import MainWindow

    settings.voice.enabled = False  # no audio devices in CI
    ctl = Controller(settings, paths, JarvisApp(settings, paths, llm=OllamaClient(settings.ai.host, 10, 2)))
    win = MainWindow(ctl)
    win.show()
    yield win
    win.quitting = True
    win.tray.hide()
    win.close()
    ctl.shutdown()  # waits for background workers
    win.compact.deleteLater()
    win.deleteLater()
    qapp.processEvents()


def test_power_on_chat_and_power_off(qapp, window, fake_ollama):
    ctl = window.ctl
    ctl.power_on()
    assert pump(qapp, lambda: window.main_page.state_label.text() == "Онлайн")
    assert ctl.power == "on" and "qwen3:8b" in fake_ollama.loaded
    fake_ollama.turns = [tool_turn("calculate", {"expression": "6*7"}), text_turn("Сорок ", "два, сэр.")]
    window.main_page.input.setText("6*7?")
    window.main_page._send()
    assert pump(qapp, lambda: "Сорок два, сэр." in window.main_page.chat.bubble_texts())
    texts = window.main_page.chat.bubble_texts()
    assert texts[0] == "6*7?" and any("calculate" in t for t in texts)
    assert window.main_page.history.count() >= 1 and "6*7?" in window.main_page.history.currentText()
    ctl.power_off()
    assert pump(qapp, lambda: ctl.power == "off")
    assert "qwen3:8b" not in fake_ollama.loaded  # model unloaded from VRAM


def test_send_while_off_shows_hint(qapp, window):
    window.ctl.send_text("привет")
    assert pump(qapp, lambda: any("выключен" in t for t in window.main_page.chat.bubble_texts()))


def test_dangerous_action_dialog(qapp, window, fake_ollama, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    done = []

    @tool(risk=RiskLevel.DANGEROUS)
    def wipe(path: str) -> str:
        """Delete."""
        done.append(path)
        return "ok"

    window.ctl.app.registry.register(wipe)
    answers = iter([False, True])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: self.buttons()[0] if next(answers) else self.buttons()[1])
    window.ctl.power_on()
    assert pump(qapp, lambda: window.ctl.power == "on")
    for expected in ([], ["C:/x"]):
        fake_ollama.turns = [tool_turn("wipe", {"path": "C:/x"}), text_turn("Ок.")]
        window.ctl.send_text("удали")
        assert pump(qapp, lambda: window.ctl.app.state.value == "ready" and len(fake_ollama.turns) == 0)
        pump(qapp, lambda: False, 0.3)
        assert done == expected


def test_new_conversation_and_history(qapp, window):
    old = window.ctl.conversation_id
    window.ctl.new_conversation()
    pump(qapp, lambda: False, 0.2)
    assert window.ctl.conversation_id != old and window.main_page.chat.bubble_texts() == []
    window.ctl.open_conversation(old)
    assert window.ctl.conversation_id == old


def test_voice_page_saves_settings(qapp, window, paths):
    from jarvis.settings import load_settings

    page = window.voice_page
    page.voice.setCurrentIndex(page.voice.findData("ru_RU-denis-medium"))
    page.speed.setValue(120)
    page.timeout.setCurrentIndex(page.timeout.findData(30))
    page.wake_words.setText("пятница, jarvis")
    page.apply()
    saved = load_settings(paths.settings_file).voice
    assert saved.tts_voice == "ru_RU-denis-medium" and saved.tts_speed == 1.2
    assert saved.idle_timeout_seconds == 30 and saved.wake_words == ["пятница", "jarvis"]
    page.wake_words.setText("  ")
    page.apply()
    assert "ключевое слово" in page.msg.text()


def test_pages_render(qapp, window):
    for i in range(4):
        window.show_page(i)
        qapp.processEvents()
        assert not window.grab().isNull()
    window.compact.show()
    qapp.processEvents()
    assert window.compact.label.text() == "Выключен"


def test_setup_wizard_pulls_model(qapp, settings, paths, fake_ollama):
    from jarvis.gui.setup_wizard import SetupWizard, check_environment, needs_setup

    fake_ollama.models = []
    env = check_environment(settings)
    assert env["ollama_running"] and not env["model"] and needs_setup(env)
    wz = SetupWizard(settings, paths)
    assert pump(qapp, lambda: wz.texts[1].text().startswith("нужно скачать"))
    assert not wz.finish_btn.isEnabled()
    wz.pull_model().join(10)
    assert pump(qapp, lambda: 1 in wz.done_steps)
    assert wz.finish_btn.isEnabled() and "qwen3:8b" in fake_ollama.models
    assert not needs_setup(check_environment(settings))
    from jarvis.gui.workers import wait_all

    assert wait_all(10)
    wz.deleteLater()
    qapp.processEvents()
