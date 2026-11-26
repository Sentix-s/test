"""Wires microphone, VAD, wake word, STT, agent and TTS together.

Threads:
  * PortAudio callback  -> 30 ms frames into a queue (dropped while muted)
  * ``voice-loop``      -> VAD segmentation + :class:`VoiceSession`
  * ``voice-tts``       -> sentence synthesis and playback

Echo protection: the microphone is muted from the moment a command is
being processed until JARVIS has finished speaking (+ a short tail), and
the VAD state is reset, so JARVIS never hears itself.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Iterator

from jarvis.core.app import AppState, JarvisApp
from jarvis.core.errors import JarvisError
from jarvis.settings import AppPaths
from jarvis.settings.schema import VoiceSettings
from jarvis.voice.models import ModelStore
from jarvis.voice.session import VoiceSession, VoiceState

log = logging.getLogger(__name__)

ECHO_TAIL_S = 0.25
CONVERSATION_REUSE_S = 600  # keep the conversation context for 10 minutes between activations


class PiperSpeaker:
    """Queue of sentences -> Piper -> speakers, on its own thread."""

    def __init__(self, tts: Any, output: Any) -> None:
        self.tts = tts
        self.output = output
        self._q: queue.Queue[str | None] = queue.Queue()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="voice-tts", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            text = self._q.get()
            try:
                if text is None:
                    return
                if self._cancel.is_set():
                    continue
                speech = self.tts.synthesize(text)
                if speech is not None and not self._cancel.is_set():
                    self.output.play(speech.audio, speech.sample_rate)
            except Exception:  # noqa: BLE001 - a broken sentence must not kill the voice
                log.exception("TTS failed for %r", text)
            finally:
                self._q.task_done()

    def say(self, text: str) -> None:
        self._cancel.clear()
        self._q.put(text)

    def wait_done(self) -> None:
        self._q.join()

    def stop(self) -> None:
        self._cancel.set()
        self.output.stop()

    def close(self) -> None:
        self.stop()
        self._q.put(None)


class VoiceEngine:
    def __init__(self, app: JarvisApp, paths: AppPaths, listener: Callable[[str, dict[str, Any]], None] | None = None,
                 conversation: Callable[[], int] | None = None) -> None:
        self.app = app
        self.settings: VoiceSettings = app.settings.voice
        self.store = ModelStore(paths.data_dir / "models")
        self.external_listener = listener
        self.stt: Any = None
        self.tts: Any = None
        self.input: Any = None
        self.speaker: PiperSpeaker | None = None
        self.session: VoiceSession | None = None
        self.segmenter: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._commands: queue.Queue[Callable[[], None]] = queue.Queue()
        self._conversation_id: int | None = None
        self._last_activity = 0.0
        self._conversation_provider = conversation  # GUI: share the visible conversation

    # ------------------------------------------------------------ building
    def load(self, progress: Callable[[str], None] | None = None) -> None:
        """Load (and download on first use) all voice models."""
        from jarvis.voice.audio import AudioInput, AudioOutput
        from jarvis.voice.stt import WhisperSTT
        from jarvis.voice.tts import PiperTTS
        from jarvis.voice.vad import Segmenter
        from jarvis.voice.wakeword import OpenWakeWordDetector, TranscriptWakeDetector

        say = progress or (lambda _m: None)
        s = self.settings
        say("Загрузка голоса…")
        self.tts = PiperTTS(self.store, s.tts_voice, s.tts_voice_en, s.tts_speed, s.tts_volume)
        self.tts.preload()
        say("Загрузка распознавания речи…")
        # No Whisper hotwords for the wake word: on real Whisper they made it drop the name entirely
        # ("Джарвис, который час" -> "который час"); phonetic matching handles misspellings instead.
        self.stt = WhisperSTT(self.store, s.stt_model, s.stt_device, s.stt_language, progress=say)
        self.stt.load()
        if s.wake_engine == "openwakeword":
            say("Загрузка детектора ключевого слова…")
            wake_path, mel, emb = self.store.ensure_openwakeword(s.openwakeword_model)
            wake: Any = OpenWakeWordDetector(str(wake_path), str(mel), str(emb), s.wake_threshold, stt=self.stt)
        else:
            wake = TranscriptWakeDetector(self.stt, s.wake_words)
        self.segmenter = Segmenter(s.vad_aggressiveness, s.end_silence_ms, s.max_utterance_seconds)
        self.input = AudioInput(s.input_device)
        self.speaker = PiperSpeaker(self.tts, AudioOutput(s.output_device))
        self.session = VoiceSession(s, wake, self.stt, self.speaker, self._respond, listener=self._on_session_event)
        say(f"Голос готов: распознавание {self.stt.model_name} на {self.stt.device.upper()}, голос {s.tts_voice}")

    # ------------------------------------------------------------- agent glue
    def _conversation(self) -> int:
        if self._conversation_provider is not None:
            return self._conversation_provider()
        now = time.monotonic()
        if self._conversation_id is None or now - self._last_activity > CONVERSATION_REUSE_S:
            self._conversation_id = self.app.conversations.create("🎙")
        self._last_activity = now
        return self._conversation_id

    def _respond(self, text: str) -> Iterator[str]:
        try:
            for event in self.app.ask(self._conversation(), text):
                if event.kind == "text":
                    yield event.text
                elif event.kind == "error":
                    yield f" Произошла ошибка: {event.text}"
                elif event.kind == "cancelled":
                    yield " Остановлено."
                self.notify(f"agent.{event.kind}", text=event.text, **event.data)
        except JarvisError as exc:
            yield "Я сейчас занят другой задачей." if self.app.state is AppState.BUSY else f"Ошибка: {exc}"

    def notify(self, name: str, **data: Any) -> None:
        self.app.bus.publish(f"voice.{name}" if not name.startswith("agent.") else name, **data)
        if self.external_listener:
            try:
                self.external_listener(name, data)
            except Exception:  # noqa: BLE001
                log.exception("voice listener failed")

    def _on_session_event(self, name: str, data: dict[str, Any]) -> None:
        if name == "state" and self.input is not None:
            state = data["state"]
            if state in (VoiceState.PROCESSING.value, VoiceState.SPEAKING.value):
                self.input.muted = True
            elif state in (VoiceState.ACTIVE.value, VoiceState.SLEEP.value):
                if self.input.muted:
                    time.sleep(ECHO_TAIL_S)  # let the room echo of JARVIS's voice die out
                    self.input.flush()
                    if self.segmenter is not None:
                        self.segmenter.reset()
                    self.input.muted = False
        self.notify(name, **data)

    # ------------------------------------------------------------- lifecycle
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.session is None:
            self.load()
        assert self.session is not None and self.input is not None
        self._stop.clear()
        self.input.start()
        self.session.start()
        self._thread = threading.Thread(target=self._loop, name="voice-loop", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        assert self.session is not None and self.input is not None and self.segmenter is not None
        while not self._stop.is_set():
            while True:  # commands from other threads run here, so the session has a single owner
                try:
                    cmd = self._commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    cmd()
                except Exception:  # noqa: BLE001
                    log.exception("Voice command failed")
            try:
                frame = self.input.frames.get(timeout=0.1)
            except queue.Empty:
                self.session.tick(self.segmenter.in_speech)
                continue
            try:
                for ev in self.segmenter.process(frame):
                    if ev.kind == "speech_start":
                        self.session.on_speech_start()
                    else:
                        self.session.on_utterance(ev.audio)
                self.session.tick(self.segmenter.in_speech)
            except Exception:  # noqa: BLE001 - keep listening whatever happens
                log.exception("Voice loop error")
                self.notify("error", text="ошибка голосового модуля, см. лог")

    def activate(self) -> None:
        """Wake up without the wake word (mic button / hotkey)."""
        session = self.session
        if session is not None:
            self._commands.put(lambda: session.activate() if session.state is VoiceState.SLEEP else None)

    def submit_text(self, text: str) -> None:
        """Typed command answered by voice."""
        session = self.session
        if session is not None:
            self._commands.put(lambda: session.submit_text(text))

    def deactivate(self) -> None:
        session = self.session
        if session is not None:
            self._commands.put(lambda: session.deactivate(farewell=False)
                               if session.state is VoiceState.ACTIVE else None)

    def stop_speaking(self) -> None:
        if self.speaker is not None:
            self.speaker.stop()
        self.app.cancel_current_task("voice stop")

    def stop(self, unload: bool = True) -> None:
        """Stop listening completely (microphone closed) and free model memory."""
        self._stop.set()
        if self.speaker is not None:
            self.speaker.stop()
        self.app.cancel_current_task("voice off")
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self.input is not None:
            self.input.stop()
        if self.session is not None:
            self.session.stop()
        if unload:
            if self.speaker is not None:
                self.speaker.close()
            if self.stt is not None:
                self.stt.unload()
            if self.tts is not None:
                self.tts.unload()
            self.session = None
