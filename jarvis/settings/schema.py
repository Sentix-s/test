"""Typed, validated settings. Every value has a sane default for the
target PC (RTX 3060 12 GB, i5-12400F, 32 GB RAM), so JARVIS runs with no
settings file at all. The user file only stores overrides."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AISettings(_Section):
    host: str = "http://127.0.0.1:11434"
    # Qwen3 8B Q4_K_M ~5.2 GB weights; with 8k context ~6.5 GB VRAM total,
    # leaving room for faster-whisper on the 12 GB RTX 3060.
    model: str = "qwen3:8b"
    fallback_models: list[str] = Field(default_factory=lambda: ["qwen2.5:7b-instruct"])
    temperature: float = Field(0.4, ge=0.0, le=2.0)
    num_ctx: int = Field(8192, ge=1024, le=131072)
    # How long Ollama keeps the model in VRAM after the last request.
    keep_alive: str = "30m"
    # Qwen3 hybrid "thinking". Off by default: much faster for a voice assistant.
    think: bool = False
    request_timeout: float = Field(180.0, gt=0)
    connect_timeout: float = Field(5.0, gt=0)
    max_tool_iterations: int = Field(8, ge=1, le=50)
    # Max characters of conversation history sent to the model.
    history_char_budget: int = Field(16000, ge=1000)
    language: Literal["ru", "en"] = "ru"


IDLE_TIMEOUT_CHOICES = (5, 10, 15, 20, 30, 60)


class VoiceSettings(_Section):
    enabled: bool = True
    # ---- wake word
    # "whisper": any word(s) in any language, recognised from speech (default).
    # "openwakeword": tiny neural detector, built-in model knows English "(hey) Jarvis" only.
    wake_engine: Literal["whisper", "openwakeword"] = "whisper"
    wake_words: list[str] = Field(default_factory=lambda: ["джарвис", "jarvis"])
    openwakeword_model: str = "hey_jarvis"   # built-in name or path to a custom .onnx
    wake_threshold: float = Field(0.5, gt=0.0, lt=1.0)
    # ---- session
    idle_timeout_seconds: int = 15
    activation_phrase: str = "Слушаю вас"
    farewell_phrase: str = "Обращайтесь, сэр."
    farewell_on_timeout: bool = True
    stop_phrases: list[str] = Field(default_factory=lambda: ["всё, спасибо", "все спасибо", "спасибо, всё",
                                                            "отбой", "that's all, thanks"])
    # ---- speech recognition (faster-whisper)
    stt_model: str = "auto"      # auto = large-v3-turbo on GPU, small on CPU; or tiny/base/small/medium/...
    stt_device: Literal["auto", "cuda", "cpu"] = "auto"
    stt_language: str = "ru"     # "auto" to detect per phrase
    # ---- speech synthesis (Piper)
    tts_voice: str = "ru_RU-dmitri-medium"
    tts_voice_en: str = "en_GB-alan-medium"   # used for English sentences; "" = always main voice
    tts_speed: float = Field(1.0, ge=0.5, le=2.0)
    tts_volume: float = Field(1.0, ge=0.1, le=2.0)
    # ---- audio
    input_device: str | None = None
    output_device: str | None = None
    vad_aggressiveness: int = Field(2, ge=0, le=3)
    end_silence_ms: int = Field(700, ge=200, le=3000)
    max_utterance_seconds: float = Field(30.0, ge=3, le=120)

    @field_validator("idle_timeout_seconds")
    @classmethod
    def _check_timeout(cls, v: int) -> int:
        if v not in IDLE_TIMEOUT_CHOICES:
            raise ValueError(f"idle_timeout_seconds must be one of {IDLE_TIMEOUT_CHOICES}")
        return v

    @field_validator("wake_words")
    @classmethod
    def _check_wake(cls, v: list[str]) -> list[str]:
        v = [w.strip() for w in v if w.strip()]
        if not v:
            raise ValueError("at least one wake word is required")
        return v


class PerformanceProfile(str, Enum):
    ECO = "eco"
    BALANCED = "balanced"
    PERFORMANCE = "performance"


class PerformanceSettings(_Section):
    profile: PerformanceProfile = PerformanceProfile.BALANCED


class SecuritySettings(_Section):
    # When True, every MODERATE action also asks for confirmation.
    confirm_moderate: bool = False
    # Tools that are always refused, by name.
    blocked_tools: list[str] = Field(default_factory=list)


class MemorySettings(_Section):
    # Number of recent messages loaded into the model context (before char budget).
    max_context_messages: int = Field(40, ge=2, le=500)


class LoggingSettings(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_bytes: int = 5_000_000
    backup_count: int = 5


class Settings(_Section):
    ai: AISettings = Field(default_factory=AISettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    performance: PerformanceSettings = Field(default_factory=PerformanceSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
