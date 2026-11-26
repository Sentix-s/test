"""The voice state machine (no audio I/O here, so it is fully testable).

    SLEEP ──wake word──▶ ACTIVE ──utterance──▶ PROCESSING ──▶ SPEAKING ──▶ ACTIVE
      ▲                    │  (idle timer runs only in ACTIVE, i.e. after JARVIS
      │                    │   finished speaking; paused while the user talks)
      └─ "Всё, спасибо" / idle timeout ◀┘

The engine feeds it ``on_speech_start`` / ``on_utterance`` from the VAD and
calls ``tick`` regularly. All methods run on the single voice-loop thread.
"""

from __future__ import annotations

import enum
import logging
import time
from typing import Any, Callable, Iterator, Protocol

from jarvis.settings.schema import VoiceSettings
from jarvis.voice.text import SentenceSplitter, find_wake_word, is_noise_transcript, is_stop_phrase
from jarvis.voice.wakeword import Transcriber, WakeDetector

log = logging.getLogger(__name__)


class VoiceState(str, enum.Enum):
    OFF = "off"
    SLEEP = "sleep"
    ACTIVE = "active"          # awake, listening for the next command
    PROCESSING = "processing"  # recognising / thinking
    SPEAKING = "speaking"


class Speaker(Protocol):
    def say(self, text: str) -> None: ...          # queue a sentence
    def wait_done(self) -> None: ...               # block until everything queued is spoken
    def stop(self) -> None: ...                    # interrupt speech


Responder = Callable[[str], Iterator[str]]          # user text -> streamed answer text
Listener = Callable[[str, dict[str, Any]], None]


class VoiceSession:
    def __init__(self, settings: VoiceSettings, wake: WakeDetector, stt: Transcriber, speaker: Speaker,
                 responder: Responder, clock: Callable[[], float] = time.monotonic,
                 listener: Listener | None = None) -> None:
        self.settings = settings
        self.wake = wake
        self.stt = stt
        self.speaker = speaker
        self.responder = responder
        self.clock = clock
        self.listener = listener or (lambda _n, _d: None)
        self.state = VoiceState.OFF
        self.deadline: float | None = None

    # ------------------------------------------------------------ plumbing
    def _emit(self, name: str, **data: Any) -> None:
        try:
            self.listener(name, data)
        except Exception:  # noqa: BLE001
            log.exception("voice listener failed")

    def _set(self, state: VoiceState) -> None:
        if state is not self.state:
            log.info("Voice: %s -> %s", self.state.value, state.value)
            self.state = state
            self._emit("state", state=state.value)

    def _arm_timer(self) -> None:
        self.deadline = self.clock() + self.settings.idle_timeout_seconds

    def _speak_and_wait(self, text: str) -> None:
        if not text:
            return
        self._set(VoiceState.SPEAKING)
        self.speaker.say(text)
        self.speaker.wait_done()

    # ------------------------------------------------------------- control
    def start(self) -> None:
        self.deadline = None
        self._set(VoiceState.SLEEP)

    def stop(self) -> None:
        self.speaker.stop()
        self.deadline = None
        self._set(VoiceState.OFF)

    def activate(self, greet: bool = True) -> None:
        """Wake up (wake word, mic button or hotkey)."""
        self._emit("wake")
        if greet:
            self._speak_and_wait(self.settings.activation_phrase)
        self._set(VoiceState.ACTIVE)
        self._arm_timer()

    def deactivate(self, farewell: bool = True) -> None:
        self.deadline = None
        if farewell and self.settings.farewell_phrase:
            self._speak_and_wait(self.settings.farewell_phrase)
        self._set(VoiceState.SLEEP)
        self._emit("sleep")

    # -------------------------------------------------------------- inputs
    def on_speech_start(self) -> None:
        """The user started talking: an active session must not time out now."""
        if self.state is VoiceState.ACTIVE:
            self.deadline = None

    def on_utterance(self, audio: Any) -> None:
        if self.state is VoiceState.SLEEP:
            match = self.wake.check(audio)
            if match is None:
                return
            self._emit("heard", text=match.remainder, wake=True)
            command = match.remainder.strip()
            if command and not is_noise_transcript(command):
                self.activate(greet=False)
                self._handle_text(command)
            else:
                self.activate(greet=True)
            return
        if self.state is not VoiceState.ACTIVE:
            return  # ignore audio while thinking/speaking (the mic is muted then anyway)
        self._set(VoiceState.PROCESSING)
        text = self.stt.transcribe(audio)
        if is_noise_transcript(text):
            self._set(VoiceState.ACTIVE)
            self._arm_timer()
            return
        # "Джарвис, ..." while already awake: drop the wake word.
        match = find_wake_word(text, self.settings.wake_words)
        if match is not None:
            text = match.remainder
            if not text:
                self._speak_and_wait(self.settings.activation_phrase)
                self._set(VoiceState.ACTIVE)
                self._arm_timer()
                return
        self._emit("heard", text=text, wake=False)
        self._handle_text(text)

    def submit_text(self, text: str) -> None:
        """A typed command in an active voice session (GUI)."""
        if self.state in (VoiceState.SLEEP, VoiceState.ACTIVE):
            self.activate(greet=False)
            self._handle_text(text)

    def _handle_text(self, text: str) -> None:
        if is_stop_phrase(text, self.settings.stop_phrases):
            self.deactivate(farewell=True)
            return
        self._set(VoiceState.PROCESSING)
        splitter = SentenceSplitter()
        spoken = False
        answer: list[str] = []
        for delta in self.responder(text):
            answer.append(delta)
            for sentence in splitter.feed(delta):
                if not spoken:
                    self._set(VoiceState.SPEAKING)
                    spoken = True
                self.speaker.say(sentence)
        for sentence in splitter.flush():
            self._set(VoiceState.SPEAKING)
            self.speaker.say(sentence)
        self.speaker.wait_done()
        self._emit("answered", text="".join(answer))
        if self.state is VoiceState.OFF:  # stopped while answering
            return
        self._set(VoiceState.ACTIVE)
        self._arm_timer()  # idle timer starts after JARVIS finished speaking

    def tick(self, user_speaking: bool = False) -> None:
        if self.state is VoiceState.ACTIVE and self.deadline is not None and not user_speaking:
            if self.clock() >= self.deadline:
                log.info("Idle timeout (%ss) -> sleep", self.settings.idle_timeout_seconds)
                self.deactivate(farewell=self.settings.farewell_on_timeout)
