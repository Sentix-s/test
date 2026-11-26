"""Voice-activity segmentation: turns a stream of 30 ms microphone frames
into complete utterances (WebRTC VAD + an energy floor)."""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Any

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480


@dataclass
class SegmenterEvent:
    kind: str           # "speech_start" | "utterance"
    audio: Any = None   # np.int16 array for "utterance"


class Segmenter:
    def __init__(
        self,
        aggressiveness: int = 2,
        end_silence_ms: int = 700,
        max_utterance_s: float = 30.0,
        min_speech_ms: int = 250,
        pre_roll_ms: int = 300,
        energy_floor: float = 150.0,
        vad: Any = None,
    ) -> None:
        import numpy as np

        self._np = np
        if vad is None:
            import webrtcvad

            vad = webrtcvad.Vad(aggressiveness)
        self._vad = vad
        self.end_frames = max(1, end_silence_ms // FRAME_MS)
        self.max_frames = int(max_utterance_s * 1000 / FRAME_MS)
        self.min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self.energy_floor = energy_floor
        self._pre = collections.deque(maxlen=max(1, pre_roll_ms // FRAME_MS))
        self._window: collections.deque[bool] = collections.deque(maxlen=8)
        self.reset()

    def reset(self) -> None:
        self._pre.clear()
        self._window.clear()
        self._frames: list[Any] = []
        self._triggered = False
        self._silence = 0
        self._speech = 0

    @property
    def in_speech(self) -> bool:
        return self._triggered

    def _is_speech(self, frame: Any) -> bool:
        rms = float(self._np.sqrt(self._np.mean(frame.astype(self._np.float32) ** 2)))
        if rms < self.energy_floor:
            return False
        return bool(self._vad.is_speech(frame.tobytes(), SAMPLE_RATE))

    def process(self, frame: Any) -> list[SegmenterEvent]:
        """Feed one 30 ms int16 frame; returns 0..2 events."""
        if len(frame) != FRAME_SAMPLES:
            raise ValueError(f"frame must have {FRAME_SAMPLES} samples")
        speech = self._is_speech(frame)
        events: list[SegmenterEvent] = []
        if not self._triggered:
            self._pre.append(frame)
            self._window.append(speech)
            if sum(self._window) >= 5:  # ~150 ms of speech within 240 ms
                self._triggered = True
                self._frames = list(self._pre)
                self._speech = sum(self._window)
                self._silence = 0
                self._pre.clear()
                events.append(SegmenterEvent("speech_start"))
            return events
        self._frames.append(frame)
        if speech:
            self._speech += 1
            self._silence = 0
        else:
            self._silence += 1
        if self._silence >= self.end_frames or len(self._frames) >= self.max_frames:
            frames, enough = self._frames, self._speech >= self.min_speech_frames
            self.reset()
            if enough:
                events.append(SegmenterEvent("utterance", self._np.concatenate(frames)))
        return events
