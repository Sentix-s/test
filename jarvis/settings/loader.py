"""Load/save settings as YAML. Only values that differ from the defaults
are written back, so future default improvements reach existing users."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jarvis.core.errors import ConfigError
from jarvis.settings.schema import Settings

log = logging.getLogger(__name__)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _diff(current: dict[str, Any], default: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in current.items():
        dval = default.get(key)
        if isinstance(value, dict) and isinstance(dval, dict):
            sub = _diff(value, dval)
            if sub:
                out[key] = sub
        elif value != dval:
            out[key] = value
    return out


def load_settings(path: Path | None, overrides: dict[str, Any] | None = None) -> Settings:
    """Load settings from ``path`` (missing file = defaults) plus in-memory overrides."""
    data: dict[str, Any] = {}
    if path is not None and path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"Cannot read settings file {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"Settings file {path} must contain a mapping at top level")
        data = raw
    if overrides:
        data = _deep_merge(data, overrides)
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid settings ({path}):\n{exc}") from exc


def save_settings(settings: Settings, path: Path) -> None:
    """Atomically write the non-default part of ``settings`` to ``path``."""
    current = settings.model_dump(mode="json")
    default = Settings().model_dump(mode="json")
    payload = _diff(current, default)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    log.info("Settings saved to %s", path)
