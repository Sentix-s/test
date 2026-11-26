"""Real Whisper speech recognition (downloads ~500 MB from HuggingFace).
Runs only when JARVIS_TEST_WHISPER=1 (the Windows CI voice job sets it)."""

import os

import pytest

from voice_helpers import model_store, require_voice, synth16k

pytestmark = pytest.mark.skipif(os.environ.get("JARVIS_TEST_WHISPER") != "1", reason="set JARVIS_TEST_WHISPER=1")

VOICE = os.environ.get("JARVIS_TEST_VOICE", "ru_RU-dmitri-medium")


@pytest.fixture(scope="module")
def stt():
    from jarvis.voice.stt import WhisperSTT

    s = WhisperSTT(model_store(), model=os.environ.get("JARVIS_TEST_STT", "small"), device="cpu", language="ru",
                   hotwords=["джарвис", "jarvis"])
    s.load()
    return s


def test_russian_roundtrip(stt):
    store = require_voice(VOICE)
    text = stt.transcribe(synth16k(store, VOICE, "Открой, пожалуйста, текстовый редактор и создай новый документ."))
    low = text.lower()
    assert "редактор" in low and "документ" in low, text


def test_wake_word_with_command(stt):
    from jarvis.voice.wakeword import TranscriptWakeDetector

    store = require_voice(VOICE)
    det = TranscriptWakeDetector(stt, ["джарвис", "jarvis"])
    audio = synth16k(store, VOICE, "Джарвис, который сейчас час?")
    heard = stt.transcribe(audio)
    print("whisper heard:", repr(heard))
    m = det.check(audio)
    assert m is not None and "час" in m.remainder.lower(), f"whisper heard {heard!r}"
    other = synth16k(store, VOICE, "Сегодня хорошая погода, пойдём гулять.")
    assert det.check(other) is None, stt.transcribe(other)


def test_stop_phrase_recognised(stt):
    from jarvis.settings.schema import VoiceSettings
    from jarvis.voice.text import is_stop_phrase

    store = require_voice(VOICE)
    text = stt.transcribe(synth16k(store, VOICE, "Всё, спасибо."))
    assert is_stop_phrase(text, VoiceSettings().stop_phrases), text
