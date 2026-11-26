"""Wake-word detectors. Both work on complete utterances from the VAD, so
"Джарвис, открой Word" said in one breath wakes JARVIS *and* delivers the
command.

* :class:`TranscriptWakeDetector` – transcribes only the first seconds of
  each utterance with Whisper and fuzzy-matches the configured wake words.
  Any word in any language, changeable in settings, no training.
* :class:`OpenWakeWordDetector` – a tiny dedicated neural detector
  (openWakeWord). Lowest CPU use, but the built-in model only knows the
  English "(hey) Jarvis"; custom words need a custom trained .onnx.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from jarvis.voice.text import WakeMatch, find_wake_word, is_noise_transcript

log = logging.getLogger(__name__)


class WakeDetector(Protocol):
    def check(self, audio: Any) -> WakeMatch | None: ...


class Transcriber(Protocol):
    def transcribe(self, audio: Any, max_seconds: float | None = None, language: str | None = None) -> str: ...


class TranscriptWakeDetector:
    def __init__(self, stt: Transcriber, wake_words: list[str], head_seconds: float = 3.0,
                 threshold: float = 0.8) -> None:
        self.stt = stt
        self.wake_words = wake_words
        self.head_seconds = head_seconds
        self.threshold = threshold

    def check(self, audio: Any) -> WakeMatch | None:
        head = self.stt.transcribe(audio, max_seconds=self.head_seconds)
        if is_noise_transcript(head):
            return None
        match = find_wake_word(head, self.wake_words, self.threshold)
        log.debug("Wake check %r -> %s", head, match)
        if match is None:
            return None
        if len(audio) > 16000 * self.head_seconds:
            # Long utterance: the command continues past the checked head; transcribe all of it.
            full = self.stt.transcribe(audio)
            full_match = find_wake_word(full, self.wake_words, self.threshold)
            return full_match or WakeMatch(match.word, full, match.score)
        return match


class OpenWakeWordDetector:
    def __init__(self, model_path: str, melspec_path: str, embedding_path: str, threshold: float = 0.5,
                 stt: Transcriber | None = None) -> None:
        from openwakeword.model import Model

        self._model = Model(wakeword_models=[model_path], inference_framework="onnx",
                            melspec_model_path=melspec_path, embedding_model_path=embedding_path)
        self.threshold = threshold
        self.stt = stt  # used to transcribe a command that follows the wake word in the same breath

    def score(self, audio: Any) -> tuple[float, int]:
        """Max detection score and the sample index where it peaked."""
        import numpy as np

        self._model.reset()
        x = np.concatenate([np.zeros(16000, dtype=np.int16), np.asarray(audio, dtype=np.int16)])
        best, best_at = 0.0, 0
        for i in range(0, len(x) - 1280 + 1, 1280):
            s = float(max(self._model.predict(x[i:i + 1280]).values()))
            if s > best:
                best, best_at = s, max(0, i + 1280 - 16000)
        return best, best_at

    def check(self, audio: Any) -> WakeMatch | None:
        best, at = self.score(audio)
        log.debug("openWakeWord score %.3f", best)
        if best < self.threshold:
            return None
        remainder = ""
        rest = audio[at:]
        if self.stt is not None and len(rest) > 16000 * 0.8:
            text = self.stt.transcribe(audio)
            m = find_wake_word(text, ["jarvis"])
            remainder = m.remainder if m else text
        return WakeMatch("jarvis", remainder, best)
