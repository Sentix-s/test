"""Shared helpers for voice tests: real models are downloaded once into
tests/.models (from GitHub, ~130 MB); tests needing them are skipped if the
download is impossible."""

from __future__ import annotations

from pathlib import Path

import pytest

MODELS = Path(__file__).parent / ".models"


def model_store():
    from jarvis.voice.models import ModelStore

    return ModelStore(MODELS)


def require_voice(voice_id: str):
    store = model_store()
    try:
        store.ensure_voice(voice_id)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"voice {voice_id} unavailable: {exc}")
    return store


def synth16k(store, voice_id: str, text: str):
    """Piper speech resampled to 16 kHz int16 (what the microphone delivers)."""
    from jarvis.voice.models import resample
    from jarvis.voice.tts import PiperTTS

    sp = PiperTTS(store, voice_id, auto_download=False).synthesize(text)
    return resample(sp.audio, sp.sample_rate, 16000)
