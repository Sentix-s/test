"""Voice section: voice choice with preview, speed, volume, wake word, idle timer, devices, STT model."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from jarvis.gui.controller import Controller
from jarvis.settings.schema import IDLE_TIMEOUT_CHOICES
from jarvis.voice.models import VOICE_CATALOG, ModelStore

log = logging.getLogger(__name__)

PREVIEW_RU = "Здравствуйте, сэр. Я Джарвис, ваш персональный ассистент. Чем могу помочь?"
PREVIEW_EN = "Good evening, sir. All systems are online."


class _Sig(QObject):
    message = Signal(str)


class VoicePage(QWidget):
    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        self.store = ModelStore(ctl.paths.data_dir / "models")
        self._sig = _Sig()
        v = ctl.settings.voice
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel("Голос")
        title.setObjectName("h1")
        root.addWidget(title)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(12)

        self.voice = QComboBox()
        self.voice_en = QComboBox()
        self.voice_en.addItem("— как основной голос —", "")
        installed = set(self.store.installed_voices())
        for info in VOICE_CATALOG:
            label = f"{info.description}  [{info.language}]" + ("  ✓" if info.id in installed else "")
            self.voice.addItem(label, info.id)
            if info.language == "en":
                self.voice_en.addItem(label, info.id)
        for custom in sorted(installed - {i.id for i in VOICE_CATALOG}):
            self.voice.addItem(f"{custom} (свой)", custom)
        self._select(self.voice, v.tts_voice)
        self._select(self.voice_en, v.tts_voice_en)
        play = QPushButton("▶ Прослушать")
        play.clicked.connect(lambda: self._preview(self.voice.currentData(), PREVIEW_RU))
        play_en = QPushButton("▶")
        play_en.clicked.connect(lambda: self.voice_en.currentData() and self._preview(self.voice_en.currentData(), PREVIEW_EN))
        form.addRow("Голос JARVIS", self._row(self.voice, play))
        form.addRow("Для английского текста", self._row(self.voice_en, play_en))

        self.speed = self._slider(50, 200, int(v.tts_speed * 100))
        self.speed_label = QLabel()
        self.speed.valueChanged.connect(lambda x: self.speed_label.setText(f"{x / 100:.2f}×"))
        self.speed.valueChanged.emit(self.speed.value())
        form.addRow("Скорость речи", self._row(self.speed, self.speed_label))
        self.volume = self._slider(10, 200, int(v.tts_volume * 100))
        self.volume_label = QLabel()
        self.volume.valueChanged.connect(lambda x: self.volume_label.setText(f"{x}%"))
        self.volume.valueChanged.emit(self.volume.value())
        form.addRow("Громкость", self._row(self.volume, self.volume_label))

        self.wake_engine = QComboBox()
        self.wake_engine.addItem("Любое слово (распознавание речи)", "whisper")
        self.wake_engine.addItem("«Hey Jarvis» (сверхлёгкий детектор)", "openwakeword")
        self._select(self.wake_engine, v.wake_engine)
        form.addRow("Ключевое слово: способ", self.wake_engine)
        self.wake_words = QLineEdit(", ".join(v.wake_words))
        self.wake_words.setToolTip("Через запятую. Например: джарвис, jarvis, пятница")
        form.addRow("Ключевые слова", self.wake_words)
        self.timeout = QComboBox()
        for t in IDLE_TIMEOUT_CHOICES:
            self.timeout.addItem(f"{t} секунд", t)
        self._select(self.timeout, v.idle_timeout_seconds)
        form.addRow("Засыпать после тишины", self.timeout)
        self.activation = QLineEdit(v.activation_phrase)
        form.addRow("Фраза при активации", self.activation)
        self.farewell = QLineEdit(v.farewell_phrase)
        form.addRow("Прощальная фраза", self.farewell)

        self.mic = QComboBox()
        self.out = QComboBox()
        # Never store None as item data: PySide6 mis-counts references to None (crashes on Python 3.11).
        self.mic.addItem("По умолчанию (Windows)", "")
        self.out.addItem("По умолчанию (Windows)", "")
        try:
            from jarvis.voice.audio import list_devices

            for d in list_devices():
                if d["inputs"]:
                    self.mic.addItem(d["name"], d["name"])
                if d["outputs"]:
                    self.out.addItem(d["name"], d["name"])
        except Exception as exc:  # noqa: BLE001 - no audio system (e.g. remote session)
            log.warning("Audio devices unavailable: %s", exc)
        self._select(self.mic, v.input_device or "")
        self._select(self.out, v.output_device or "")
        form.addRow("Микрофон", self.mic)
        form.addRow("Динамики", self.out)

        self.stt_model = QComboBox()
        for label, val in [("Авто (видеокарта: large-v3-turbo, иначе small)", "auto"), ("small — быстро", "small"),
                           ("medium", "medium"), ("large-v3-turbo — лучшее качество", "large-v3-turbo")]:
            self.stt_model.addItem(label, val)
        self._select(self.stt_model, v.stt_model)
        form.addRow("Распознавание речи", self.stt_model)
        root.addLayout(form)

        save = QPushButton("Сохранить и применить")
        save.setObjectName("primary")
        save.clicked.connect(self.apply)
        self.msg = QLabel("")
        self.msg.setObjectName("dim")
        self.msg.setWordWrap(True)
        self._sig.message.connect(self.msg.setText)
        root.addWidget(save, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.msg)
        root.addStretch(1)

    @staticmethod
    def _row(*widgets: QWidget) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        for i, x in enumerate(widgets):
            lay.addWidget(x, 1 if i == 0 else 0)
        return w

    @staticmethod
    def _slider(lo: int, hi: int, val: int) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        return s

    @staticmethod
    def _select(combo: QComboBox, value: object) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _preview(self, voice_id: str, text: str) -> None:
        speed, volume, device = self.speed.value() / 100, self.volume.value() / 100, self.out.currentData() or None

        def work() -> None:
            try:
                from jarvis.voice.audio import AudioOutput
                from jarvis.voice.tts import PiperTTS

                if not self.store.voice_installed(voice_id):
                    self._sig.message.emit(f"Скачивание голоса {voice_id} (~60 МБ)…")
                tts = PiperTTS(self.store, voice_id, speed=speed, volume=volume)
                speech = tts.synthesize(text)
                self._sig.message.emit(f"🔊 {voice_id}")
                if speech is not None:
                    AudioOutput(device).play(speech.audio, speech.sample_rate)
                self._sig.message.emit("")
            except Exception as exc:  # noqa: BLE001
                log.exception("Preview failed")
                self._sig.message.emit(f"Не удалось воспроизвести: {exc}")
        threading.Thread(target=work, name="voice-preview", daemon=True).start()

    def apply(self) -> None:
        v = self.ctl.settings.voice
        words = [w.strip() for w in self.wake_words.text().split(",") if w.strip()]
        if not words:
            self.msg.setText("Укажите хотя бы одно ключевое слово.")
            return
        v.tts_voice = self.voice.currentData()
        v.tts_voice_en = self.voice_en.currentData() or ""
        v.tts_speed = self.speed.value() / 100
        v.tts_volume = self.volume.value() / 100
        v.wake_engine = self.wake_engine.currentData()
        v.wake_words = words
        v.idle_timeout_seconds = self.timeout.currentData()
        v.activation_phrase = self.activation.text().strip()
        v.farewell_phrase = self.farewell.text().strip()
        v.input_device = self.mic.currentData() or None
        v.output_device = self.out.currentData() or None
        v.stt_model = self.stt_model.currentData()
        self.ctl.save_settings()
        self.ctl.restart_voice()
        self.msg.setText("Сохранено. Голосовой модуль перезапускается с новыми настройками.")
