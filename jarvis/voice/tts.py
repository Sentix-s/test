"""Text-to-speech with Piper (local, CPU, real-time; no VRAM used).

Two voices can be configured: the main one (Russian by default) and an
optional English one that is used automatically for English sentences
(e.g. when JARVIS reads an English essay aloud).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from jarvis.voice.models import ModelStore
from jarvis.voice.text import clean_for_speech, detect_language

log = logging.getLogger(__name__)


@dataclass
class Speech:
    audio: Any        # np.int16
    sample_rate: int


class PiperTTS:
    def __init__(self, store: ModelStore, voice: str, voice_en: str = "", speed: float = 1.0,
                 volume: float = 1.0, auto_download: bool = True) -> None:
        self.store = store
        self.voice_id = voice
        self.voice_en_id = voice_en
        self.speed = speed
        self.volume = volume
        self.auto_download = auto_download
        self._voices: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _load(self, voice_id: str) -> Any:
        with self._lock:
            if voice_id not in self._voices:
                from piper import PiperVoice

                path = self.store.ensure_voice(voice_id) if self.auto_download else self.store.voice_path(voice_id)
                log.info("Loading Piper voice %s", path)
                self._voices[voice_id] = PiperVoice.load(str(path))
            return self._voices[voice_id]

    def preload(self) -> None:
        self._load(self.voice_id)
        if self.voice_en_id:
            try:
                self._load(self.voice_en_id)
            except OSError as exc:
                log.warning("English voice unavailable: %s", exc)
                self.voice_en_id = ""

    def voice_for(self, text: str) -> str:
        if self.voice_en_id and detect_language(text) == "en":
            return self.voice_en_id
        return self.voice_id

    def synthesize(self, text: str, voice_id: str | None = None) -> Speech | None:
        import numpy as np
        from piper import SynthesisConfig

        text = clean_for_speech(text)
        if not text:
            return None
        voice = self._load(voice_id or self.voice_for(text))
        cfg = SynthesisConfig(length_scale=1.0 / max(0.3, self.speed), volume=self.volume)
        chunks = list(voice.synthesize(text, syn_config=cfg))
        if not chunks:
            return None
        audio = np.concatenate([c.audio_int16_array for c in chunks])
        return Speech(audio, chunks[0].sample_rate)

    def unload(self) -> None:
        with self._lock:
            self._voices.clear()
