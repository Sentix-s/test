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

    s = WhisperSTT(model_store(), model=os.environ.get("JARVIS_TEST_STT", "small"), device="cpu", language="ru")
    s.load()
    return s


def test_russian_roundtrip(stt):
    store = require_voice(VOICE)
    text = stt.transcribe(synth16k(store, VOICE, "Открой, пожалуйста, текстовый редактор и создай новый документ."))
    low = text.lower()
    # Word stems: the small CPU model gets endings wrong ("редакты"); the GPU model is more exact.
    assert "редакт" in low and "документ" in low and "созда" in low, text


@pytest.mark.xfail(strict=False, reason=(
    "Known limitation: the small CPU Whisper model often mishears the synthetic 'Джарвис' "
    "('Жарвец', 'Жаркость'). Real use: large-v3-turbo on GPU, or another wake word / 'Hey Jarvis' engine."))
def test_wake_word_with_command(stt):
    """Piper renders each phrase a little differently; require 3 of 4 renditions to wake."""
    from jarvis.voice.wakeword import TranscriptWakeDetector

    store = require_voice(VOICE)
    det = TranscriptWakeDetector(stt, ["джарвис", "jarvis"])
    results = []
    for _ in range(4):
        audio = synth16k(store, VOICE, "Джарвис, который сейчас час?")
        m = det.check(audio)
        results.append((stt.transcribe(audio), m is not None and "час" in m.remainder.lower()))
    print("whisper heard:", results)
    assert sum(ok for _t, ok in results) >= 3, results
    other = synth16k(store, VOICE, "Сегодня хорошая погода, пойдём гулять.")
    assert det.check(other) is None, stt.transcribe(other)


def test_stop_phrase_recognised(stt):
    from jarvis.settings.schema import VoiceSettings
    from jarvis.voice.text import is_stop_phrase

    store = require_voice(VOICE)
    texts = [stt.transcribe(synth16k(store, VOICE, "Всё, спасибо.")) for _ in range(3)]
    print("whisper heard:", texts)
    assert sum(is_stop_phrase(t, VoiceSettings().stop_phrases) for t in texts) >= 2, texts
