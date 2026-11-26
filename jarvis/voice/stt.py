"""Speech-to-text with faster-whisper.

On Windows with an NVIDIA GPU, CTranslate2 needs cuBLAS 12 and cuDNN 9
DLLs. They are installed as pip packages (nvidia-cublas-cu12,
nvidia-cudnn-cu12) and their ``bin`` folders are added to the DLL search
path here, *before* CTranslate2 is imported. If CUDA still cannot be used,
recognition falls back to the CPU instead of crashing.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from jarvis.voice.models import ModelStore

log = logging.getLogger(__name__)

_dll_setup_done = False


def setup_cuda_dlls() -> list[str]:
    """Make pip-installed NVIDIA DLLs visible to CTranslate2 (Windows only)."""
    global _dll_setup_done
    added: list[str] = []
    if sys.platform != "win32" or _dll_setup_done:
        return added
    _dll_setup_done = True
    for entry in sys.path:
        nvidia = Path(entry) / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))  # type: ignore[attr-defined]
            except OSError:
                continue
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            added.append(str(bin_dir))
    if added:
        log.info("CUDA DLL directories: %s", added)
    return added


def cuda_available() -> bool:
    setup_cuda_dlls()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def _cuda_libs_present() -> bool:
    """cuDNN/cuBLAS must be loadable, otherwise CTranslate2 aborts at the first
    transcription. Check the DLLs exist before choosing the GPU."""
    if sys.platform != "win32":
        return True
    found = {"cublas": False, "cudnn": False}
    for entry in sys.path:
        for lib in found:
            if (Path(entry) / "nvidia" / lib / "bin").is_dir():
                found[lib] = True
    return all(found.values())


def resolve_device(device: str, model: str) -> tuple[str, str, str]:
    """Return (device, compute_type, model) for 'auto' settings."""
    use_cuda = device == "cuda" or (device == "auto" and cuda_available() and _cuda_libs_present())
    if use_cuda:
        return "cuda", "int8_float16", ("large-v3-turbo" if model == "auto" else model)
    return "cpu", "int8", ("small" if model == "auto" else model)


class WhisperSTT:
    def __init__(self, store: ModelStore, model: str = "auto", device: str = "auto", language: str = "ru",
                 beam_size: int = 1, cpu_threads: int = 4, progress: Callable[[str], None] | None = None) -> None:
        self.store = store
        self.requested_model = model
        self.requested_device = device
        self.language = None if language == "auto" else language
        self.beam_size = beam_size
        self.cpu_threads = cpu_threads
        self.progress = progress
        self.device = ""
        self.model_name = ""
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            device, compute, model = resolve_device(self.requested_device, self.requested_model)
            path = self.store.ensure_whisper(model, self.progress)
            from faster_whisper import WhisperModel

            try:
                self._model = WhisperModel(path, device=device, compute_type=compute, cpu_threads=self.cpu_threads)
                if device == "cuda":  # surface missing cuDNN now, not mid-conversation
                    import numpy as np

                    list(self._model.transcribe(np.zeros(16000, dtype=np.float32), language="ru")[0])
            except Exception as exc:  # noqa: BLE001
                if device != "cuda":
                    raise
                log.warning("Whisper on CUDA failed (%s); falling back to CPU", exc)
                device, compute = "cpu", "int8"
                if self.requested_model == "auto":
                    model = "small"
                    path = self.store.ensure_whisper(model, self.progress)
                self._model = WhisperModel(path, device=device, compute_type=compute, cpu_threads=self.cpu_threads)
            self.device, self.model_name = device, model
            log.info("Whisper %s loaded on %s (%s)", model, device, compute)

    def transcribe(self, audio: Any, max_seconds: float | None = None, language: str | None = None) -> str:
        """``audio``: 16 kHz mono int16 or float32 numpy array."""
        import numpy as np

        self.load()
        data = np.asarray(audio)
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        if max_seconds:
            data = data[: int(16000 * max_seconds)]
        segments, _info = self._model.transcribe(
            data, language=language or self.language, beam_size=self.beam_size, vad_filter=False,
            condition_on_previous_text=False, without_timestamps=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def unload(self) -> None:
        with self._lock:
            self._model = None
