"""Logging: rotating UTF-8 file log + optional console output."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from jarvis.settings.schema import LoggingSettings

_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


def setup_logging(settings: LoggingSettings, log_dir: Path, console: bool = False) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "jarvis.log"
    root = logging.getLogger()
    root.setLevel(settings.level)
    for h in list(root.handlers):
        if getattr(h, "_jarvis", False):
            root.removeHandler(h)
            h.close()

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=settings.max_bytes, backupCount=settings.backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    file_handler._jarvis = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setLevel(logging.WARNING)
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        stream._jarvis = True  # type: ignore[attr-defined]
        root.addHandler(stream)

    # httpx logs every request at INFO; keep the log readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return log_file
