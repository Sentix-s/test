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
    """Used from Phase 2 on; defined now so the settings file is stable."""

    enabled: bool = True
    wake_word: str = "jarvis"
    idle_timeout_seconds: int = 15
    activation_phrase: str = "Слушаю вас"
    farewell_phrase: str = "До связи."
    stop_phrases: list[str] = Field(default_factory=lambda: ["всё, спасибо", "все, спасибо", "всё спасибо"])
    input_device: str | None = None
    output_device: str | None = None

    @field_validator("idle_timeout_seconds")
    @classmethod
    def _check_timeout(cls, v: int) -> int:
        if v not in IDLE_TIMEOUT_CHOICES:
            raise ValueError(f"idle_timeout_seconds must be one of {IDLE_TIMEOUT_CHOICES}")
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
