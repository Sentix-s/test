"""Microphone capture and speaker playback (sounddevice / PortAudio)."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from jarvis.voice.vad import FRAME_SAMPLES, SAMPLE_RATE

log = logging.getLogger(__name__)


def _sd() -> Any:
    import sounddevice as sd  # lazy: needs PortAudio, bundled in the Windows wheel

    return sd


def _resolve_device(name: str | None, kind: str) -> int | None:
    """Accept a device index, an exact name or a substring of the name."""
    if name in (None, "", "default"):
        return None
    if str(name).isdigit():
        return int(name)
    sd = _sd()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for idx, dev in enumerate(sd.query_devices()):
        if dev[key] > 0 and str(name).lower() in dev["name"].lower():
            return idx
    log.warning("Audio device %r not found, using default", name)
    return None


def list_devices() -> list[dict[str, Any]]:
    sd = _sd()
    out = []
    default_in, default_out = sd.default.device
    for idx, dev in enumerate(sd.query_devices()):
        out.append({
            "index": idx, "name": dev["name"], "inputs": dev["max_input_channels"],
            "outputs": dev["max_output_channels"], "default_input": idx == default_in,
            "default_output": idx == default_out,
        })
    return out


class AudioInput:
    """Captures 16 kHz mono int16 audio in 30 ms frames into a queue.
    While ``muted`` (JARVIS is speaking) frames are dropped."""

    def __init__(self, device: str | None = None, max_queue: int = 400) -> None:
        self.device = device
        self.frames: queue.Queue[Any] = queue.Queue(maxsize=max_queue)
        self.muted = False
        self.level = 0.0  # last frame RMS, for the GUI voice visualizer
        self._stream: Any = None

    def _callback(self, indata: Any, _frames: int, _time: Any, status: Any) -> None:
        if status:
            log.debug("Input status: %s", status)
        frame = indata[:, 0].copy()
        import numpy as np

        self.level = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        if self.muted:
            return
        try:
            self.frames.put_nowait(frame)
        except queue.Full:  # consumer is busy: drop the oldest audio
            try:
                self.frames.get_nowait()
                self.frames.put_nowait(frame)
            except queue.Empty:
                pass

    def start(self) -> None:
        sd = _sd()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES,
            device=_resolve_device(self.device, "input"), callback=self._callback,
        )
        self._stream.start()
        log.info("Microphone started (%s)", self.device or "default")

    def flush(self) -> None:
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
                log.info("Microphone stopped")


class AudioOutput:
    """Blocking, interruptible playback of float32/int16 mono audio."""

    def __init__(self, device: str | None = None) -> None:
        self.device = device
        self._stop = threading.Event()

    def play(self, audio: Any, sample_rate: int) -> bool:
        """Play and block until done. Returns False if interrupted."""
        import numpy as np

        sd = _sd()
        self._stop.clear()
        data = audio.astype(np.float32) / 32768.0 if audio.dtype == np.int16 else audio.astype(np.float32)
        block = 1024
        with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32",
                             device=_resolve_device(self.device, "output")) as stream:
            for i in range(0, len(data), block):
                if self._stop.is_set():
                    return False
                stream.write(data[i:i + block].reshape(-1, 1))
        return True

    def stop(self) -> None:
        self._stop.set()
