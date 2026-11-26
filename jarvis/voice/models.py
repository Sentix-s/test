"""Model catalog and downloads for the voice engine.

Everything is stored under ``<data_dir>/models``:

    voices/<voice_id>.onnx (+ .onnx.json)   Piper voices (any custom Piper voice dropped here works too)
    wakeword/*.onnx                         openWakeWord models
    whisper/<model>/                        faster-whisper (CTranslate2) models

Downloads try HuggingFace first and then the hf-mirror.com mirror, so voice
works where huggingface.co is slow or blocked.
"""

from __future__ import annotations

import logging
import os
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger(__name__)

HF_HOSTS = ["https://huggingface.co", "https://hf-mirror.com"]
PIPER_REPO = "rhasspy/piper-voices"
PIPER_REV = "v1.0.0"

Progress = Callable[[str, int, int], None]  # (label, done_bytes, total_bytes)


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    language: str        # "ru" | "en"
    gender: str          # "male" | "female"
    description: str
    github_tar: str | None = None  # fallback archive (old Piper release)

    @property
    def hf_path(self) -> str:
        lang_code, name, quality = self.id.split("-", 2)
        family = lang_code.split("_")[0]
        return f"{family}/{lang_code}/{name}/{quality}/{self.id}.onnx"


VOICE_CATALOG: list[VoiceInfo] = [
    VoiceInfo("ru_RU-dmitri-medium", "ru", "male", "Дмитрий — мужской, спокойный"),
    VoiceInfo("ru_RU-denis-medium", "ru", "male", "Денис — мужской"),
    VoiceInfo("ru_RU-ruslan-medium", "ru", "male", "Руслан — мужской, низкий"),
    VoiceInfo("ru_RU-irina-medium", "ru", "female", "Ирина — женский",
              "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-ru-irinia-medium.tar.gz"),
    VoiceInfo("en_GB-alan-medium", "en", "male", "Alan — British male (дворецкий)"),
    VoiceInfo("en_GB-northern_english_male-medium", "en", "male", "Northern English male"),
    VoiceInfo("en_US-ryan-high", "en", "male", "Ryan — American male, high quality"),
    VoiceInfo("en_US-lessac-medium", "en", "female", "Lessac — American female",
              "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-lessac-medium.tar.gz"),
    VoiceInfo("en_US-amy-medium", "en", "female", "Amy — American female"),
]

OWW_BASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
OWW_SHARED = ["melspectrogram.onnx", "embedding_model.onnx"]
OWW_BUILTIN = {"hey_jarvis": "hey_jarvis_v0.1.onnx", "alexa": "alexa_v0.1.onnx", "hey_mycroft": "hey_mycroft_v0.1.onnx"}


def _download(url: str, dest: Path, label: str, progress: Progress | None, timeout: float = 60.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".dl-")
    try:
        with os.fdopen(fd, "wb") as fh, httpx.stream("GET", url, follow_redirects=True,
                                                     timeout=httpx.Timeout(timeout, connect=15.0)) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(label, done, total)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ModelStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.voices_dir = root / "voices"
        self.wakeword_dir = root / "wakeword"
        self.whisper_dir = root / "whisper"

    # ------------------------------------------------------------------ voices
    def voice_path(self, voice_id: str) -> Path:
        p = Path(voice_id)
        if p.suffix == ".onnx" and p.is_absolute():
            return p  # a custom voice given by full path
        return self.voices_dir / f"{voice_id}.onnx"

    def voice_installed(self, voice_id: str) -> bool:
        p = self.voice_path(voice_id)
        return p.exists() and Path(str(p) + ".json").exists()

    def installed_voices(self) -> list[str]:
        if not self.voices_dir.exists():
            return []
        return sorted(p.stem for p in self.voices_dir.glob("*.onnx") if Path(str(p) + ".json").exists())

    @staticmethod
    def catalog_entry(voice_id: str) -> VoiceInfo | None:
        return next((v for v in VOICE_CATALOG if v.id == voice_id), None)

    def ensure_voice(self, voice_id: str, progress: Progress | None = None) -> Path:
        if self.voice_installed(voice_id):
            return self.voice_path(voice_id)
        info = self.catalog_entry(voice_id)
        if info is None:
            raise FileNotFoundError(
                f"Голос '{voice_id}' не найден. Положите {voice_id}.onnx и {voice_id}.onnx.json в {self.voices_dir}")
        dest = self.voice_path(voice_id)
        errors = []
        for host in HF_HOSTS:
            base = f"{host}/{PIPER_REPO}/resolve/{PIPER_REV}/{info.hf_path}"
            try:
                _download(base + ".json", Path(str(dest) + ".json"), f"{voice_id} (config)", progress)
                _download(base, dest, voice_id, progress)
                return dest
            except (httpx.HTTPError, OSError) as exc:
                errors.append(f"{host}: {exc}")
                log.warning("Voice download from %s failed: %s", host, exc)
        if info.github_tar:
            try:
                self._install_from_tar(info, progress)
                return dest
            except (httpx.HTTPError, OSError, tarfile.TarError) as exc:
                errors.append(f"github: {exc}")
        raise OSError(f"Не удалось скачать голос {voice_id}:\n" + "\n".join(errors))

    def _install_from_tar(self, info: VoiceInfo, progress: Progress | None) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "voice.tar.gz"
            _download(info.github_tar or "", archive, info.id, progress)
            with tarfile.open(archive) as tar:
                onnx = next(m for m in tar.getmembers() if m.name.endswith(".onnx"))
                cfg = next(m for m in tar.getmembers() if m.name.endswith(".onnx.json"))
                self.voices_dir.mkdir(parents=True, exist_ok=True)
                for member, target in ((onnx, self.voice_path(info.id)), (cfg, Path(str(self.voice_path(info.id)) + ".json"))):
                    src = tar.extractfile(member)
                    if src is None:
                        raise tarfile.TarError(f"bad member {member.name}")
                    target.write_bytes(src.read())

    # --------------------------------------------------------------- wake word
    def ensure_openwakeword(self, model: str, progress: Progress | None = None) -> tuple[Path, Path, Path]:
        """Returns (wake model, melspectrogram, embedding) paths, downloading if needed."""
        files = list(OWW_SHARED)
        custom = Path(model)
        if custom.suffix == ".onnx" and custom.exists():
            wake = custom
        else:
            fname = OWW_BUILTIN.get(model)
            if fname is None:
                raise FileNotFoundError(f"Unknown openWakeWord model '{model}'. Built-in: {', '.join(OWW_BUILTIN)}")
            files.append(fname)
            wake = self.wakeword_dir / fname
        for f in files:
            dest = self.wakeword_dir / f
            if not dest.exists():
                _download(f"{OWW_BASE}/{f}", dest, f, progress)
        return wake, self.wakeword_dir / OWW_SHARED[0], self.wakeword_dir / OWW_SHARED[1]

    # ------------------------------------------------------------------ whisper
    def ensure_whisper(self, model: str, progress: Callable[[str], None] | None = None) -> str:
        """Download a faster-whisper model into the store; returns its local directory."""
        if Path(model).is_dir():
            return model
        local = self.whisper_dir / model
        if (local / "model.bin").exists():
            return str(local)
        from faster_whisper.utils import _MODELS  # size name -> HF repo id
        from huggingface_hub import snapshot_download

        repo = _MODELS.get(model, model)
        allow = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
        errors = []
        for host in HF_HOSTS:
            try:
                if progress:
                    progress(f"Скачивание модели распознавания речи {model} ({host})…")
                snapshot_download(repo, local_dir=str(local), allow_patterns=allow, endpoint=host)
                if (local / "model.bin").exists():
                    return str(local)
            except Exception as exc:  # noqa: BLE001 - huggingface_hub raises many types
                errors.append(f"{host}: {exc}")
                log.warning("Whisper download from %s failed: %s", host, exc)
        raise OSError(f"Не удалось скачать модель {model}:\n" + "\n".join(errors))


def read_wav(path: Path) -> tuple["object", int]:
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("Only 16-bit PCM WAV is supported")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1).astype(np.int16)
        return data, w.getframerate()


def write_wav(path: Path, audio: "object", sample_rate: int) -> None:
    import wave

    import numpy as np

    data = np.asarray(audio)
    if data.dtype != np.int16:
        data = (np.clip(data, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data.tobytes())


def resample(audio: "object", src_rate: int, dst_rate: int = 16000) -> "object":
    import numpy as np

    if src_rate == dst_rate:
        return audio
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(src_rate, dst_rate)
    out = resample_poly(np.asarray(audio, dtype=np.float32), dst_rate // g, src_rate // g)
    return np.clip(out, -32768, 32767).astype(np.int16)


