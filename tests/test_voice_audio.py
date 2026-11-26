"""Voice tests with real models (Piper voices, WebRTC VAD, openWakeWord).
Speech is synthesized with Piper, so no microphone is needed."""

import queue
import threading
import time

import numpy as np
import pytest

from voice_helpers import model_store, require_voice, synth16k

pytest.importorskip("piper")
pytest.importorskip("webrtcvad")

RU, EN = "ru_RU-irina-medium", "en_US-lessac-medium"


def silence(seconds):
    return np.zeros(int(16000 * seconds), dtype=np.int16)


def frames(audio):
    from jarvis.voice.vad import FRAME_SAMPLES

    n = len(audio) // FRAME_SAMPLES
    return [audio[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES] for i in range(n)]


def test_piper_russian_synthesis():
    from jarvis.voice.tts import PiperTTS

    store = require_voice(RU)
    tts = PiperTTS(store, RU, speed=1.0, auto_download=False)
    sp = tts.synthesize("**Добрый вечер**, сэр. Все системы в норме.")
    assert sp.sample_rate == 22050 and 1.5 < len(sp.audio) / sp.sample_rate < 6
    assert np.abs(sp.audio).max() > 3000  # not silence
    fast = PiperTTS(store, RU, speed=1.5, auto_download=False).synthesize("Добрый вечер, сэр. Все системы в норме.")
    assert len(fast.audio) < len(sp.audio) * 0.85  # speed setting works
    assert tts.synthesize("   ") is None


def test_english_sentences_use_english_voice():
    from jarvis.voice.tts import PiperTTS

    store = require_voice(RU)
    require_voice(EN)
    tts = PiperTTS(store, RU, voice_en=EN, auto_download=False)
    assert tts.voice_for("My hobbies are football and chess.") == EN
    assert tts.voice_for("Мои хобби — футбол.") == RU
    sp = tts.synthesize("My hobbies are football and chess.")
    assert sp is not None and sp.sample_rate in (16000, 22050)  # the GitHub and HF builds of lessac differ


def test_vad_segments_real_speech():
    from jarvis.voice.vad import Segmenter

    store = require_voice(RU)
    speech = synth16k(store, RU, "Открой, пожалуйста, текстовый редактор.")
    audio = np.concatenate([silence(1.0), speech, silence(1.5), synth16k(store, RU, "Спасибо."), silence(1.5)])
    seg = Segmenter(end_silence_ms=700)
    events = [ev for f in frames(audio) for ev in seg.process(f)]
    kinds = [e.kind for e in events]
    assert kinds == ["speech_start", "utterance", "speech_start", "utterance"]
    first = events[1].audio
    assert abs(len(first) - len(speech)) < 16000 * 1.3  # utterance ≈ the spoken phrase (+pre-roll/tail)


def test_vad_ignores_silence_and_low_noise():
    from jarvis.voice.vad import Segmenter

    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 40, 16000 * 3)).astype(np.int16)  # quiet hum
    seg = Segmenter()
    assert [ev for f in frames(noise) for ev in seg.process(f)] == []


def test_vad_max_length_cut():
    from jarvis.voice.vad import Segmenter

    store = require_voice(RU)
    long_speech = np.concatenate([synth16k(store, RU, "Раз два три четыре пять шесть семь.")] * 3)
    seg = Segmenter(max_utterance_s=3.0, end_silence_ms=700)
    utts = [ev for f in frames(long_speech) for ev in seg.process(f) if ev.kind == "utterance"]
    assert utts and all(len(u.audio) <= 16000 * 3.0 + 480 for u in utts)


def test_openwakeword_hey_jarvis():
    pytest.importorskip("openwakeword")
    from jarvis.voice.wakeword import OpenWakeWordDetector

    store = require_voice(EN)
    require_voice(RU)
    try:
        wake, mel, emb = store.ensure_openwakeword("hey_jarvis")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"openWakeWord models unavailable: {exc}")
    det = OpenWakeWordDetector(str(wake), str(mel), str(emb), threshold=0.5)
    # Piper adds random prosody, so check several renditions. The built-in model is trained on
    # "hey jarvis": the full phrase must be reliable; a bare "Jarvis" is borderline and not asserted.
    hits = sum(det.check(synth16k(store, EN, "Hey Jarvis.")) is not None for _ in range(5))
    assert hits == 5
    assert det.check(synth16k(store, EN, "What is the weather like today?")) is None
    assert det.check(synth16k(store, RU, "Привет, как у тебя дела?")) is None


# ------------------------------------------------------------- integration

class FakeInput:
    """Stands in for the microphone: a frame queue + mute flag like AudioInput."""

    def __init__(self):
        self.frames = queue.Queue()
        self.muted = False
        self.muted_log = []

    def start(self):
        pass

    def stop(self):
        pass

    def flush(self):
        while not self.frames.empty():
            self.frames.get_nowait()


class FakeOutput:
    def __init__(self):
        self.played = []

    def play(self, audio, sr):
        self.played.append((len(audio) / sr, sr))
        time.sleep(0.05)
        return True

    def stop(self):
        pass


class ScriptedSTT:
    """Real audio goes in; text comes from a script (Whisper models can't be downloaded in CI sandboxes)."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.got = []

    def transcribe(self, audio, max_seconds=None, language=None):
        # The wake detector may transcribe the same utterance twice (head, then full): same text.
        key = id(audio)
        if not self.got or self.got[-1][0] != key:
            self.got.append((key, self.texts.pop(0) if self.texts else ""))
        return self.got[-1][1]

    def unload(self):
        self.unloaded = True


def test_engine_end_to_end_with_real_audio(app, fake_ollama, paths):
    """Mic frames -> VAD -> wake -> agent (fake Ollama) -> Piper -> speaker, with mic muting."""
    from fake_ollama import text_turn, tool_turn
    from jarvis.voice.engine import PiperSpeaker, VoiceEngine
    from jarvis.voice.session import VoiceSession
    from jarvis.voice.tts import PiperTTS
    from jarvis.voice.vad import Segmenter
    from jarvis.voice.wakeword import TranscriptWakeDetector

    store = require_voice(RU)
    app.settings.voice.idle_timeout_seconds = 5
    events = []
    engine = VoiceEngine(app, paths, listener=lambda n, d: events.append((n, d)))
    stt = ScriptedSTT(["Джарвис, сколько будет семь умножить на шесть?", "Всё, спасибо"])
    engine.stt = stt
    engine.input = FakeInput()
    out = FakeOutput()
    engine.speaker = PiperSpeaker(PiperTTS(store, RU, auto_download=False), out)
    engine.segmenter = Segmenter()
    engine.session = VoiceSession(app.settings.voice, TranscriptWakeDetector(stt, ["джарвис"]), stt,
                                  engine.speaker, engine._respond, listener=engine._on_session_event)
    fake_ollama.turns = [tool_turn("calculate", {"expression": "7*6"}), text_turn("Сорок два, сэр.")]
    engine.start()
    try:
        phrase = synth16k(store, RU, "Джарвис, сколько будет семь умножить на шесть?")
        for f in frames(np.concatenate([silence(0.5), phrase, silence(1.2)])):
            engine.input.frames.put(f)
        deadline = time.time() + 20
        while time.time() < deadline and not any(n == "answered" for n, _ in events):
            time.sleep(0.05)
        answered = [d["text"] for n, d in events if n == "answered"]
        assert answered == ["Сорок два, сэр."]
        assert out.played, "nothing was spoken"
        # the agent really used the tool, and the voice conversation is stored
        assert any(n == "agent.tool_start" for n, _ in events)
        conv = app.conversations.list()[0]
        assert conv.title == "🎙 сколько будет семь умножить на шесть?" and conv.message_count == 4  # user, assistant(tool), tool, answer
        # stop phrase -> farewell -> sleep
        for f in frames(np.concatenate([synth16k(store, RU, "Всё, спасибо."), silence(1.2)])):
            engine.input.frames.put(f)
        deadline = time.time() + 10
        while time.time() < deadline and not any(n == "sleep" for n, _ in events):
            time.sleep(0.05)
        states = [d["state"] for n, d in events if n == "state"]
        assert states[-1] == "sleep" and "speaking" in states
        assert engine.input.muted is False  # mic re-opened after speaking
    finally:
        engine.stop()
    assert engine.session is None and not engine.running
