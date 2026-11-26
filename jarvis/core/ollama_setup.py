"""Finding, starting and installing Ollama (used by the first-run wizard and
by power-on, so JARVIS works right after a reboot without manual steps)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from jarvis.ai.ollama_client import OllamaClient

log = logging.getLogger(__name__)

OLLAMA_SETUP_URL = "https://ollama.com/download/OllamaSetup.exe"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def is_local(host: str) -> bool:
    """Local default Ollama address: the only one we can start ourselves."""
    u = urlparse(host)
    return (u.hostname or "") in ("127.0.0.1", "localhost", "::1") and (u.port or 11434) == 11434


def find_ollama() -> Path | None:
    """The ollama CLI, from PATH or the default per-user install folder."""
    exe = shutil.which("ollama")
    if exe:
        return Path(exe)
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if local.exists():
            return local
    return None


def _tray_app(cli: Path) -> Path | None:
    app = cli.parent / "ollama app.exe"
    return app if app.exists() else None


def start_ollama(host: str, timeout: float = 30.0) -> bool:
    """Start the local Ollama server if it is installed but not running."""
    if not is_local(host):
        return False
    with OllamaClient(host, connect_timeout=2.0) as client:
        if client.is_available():
            return True
        cli = find_ollama()
        if cli is None:
            return False
        tray = _tray_app(cli)
        cmd = [str(tray)] if tray else [str(cli), "serve"]
        log.info("Starting Ollama: %s", cmd)
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             creationflags=_NO_WINDOW | _DETACHED, close_fds=True)
        except OSError as exc:
            log.warning("Could not start Ollama: %s", exc)
            return False
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if client.is_available():
                return True
            time.sleep(0.5)
    return False


def download_ollama_installer(progress: Callable[[int, int], None] | None = None) -> Path:
    dest = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
    with httpx.stream("GET", OLLAMA_SETUP_URL, follow_redirects=True, timeout=httpx.Timeout(60, connect=15)) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1 << 16):
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    return dest


def run_ollama_installer(installer: Path) -> int:
    """Run the official installer with its normal UI (the user sees and approves it).
    Waits only for the installer itself, not for the Ollama app it launches."""
    if sys.platform != "win32":
        raise OSError("Ollama installer is for Windows only")
    proc = subprocess.Popen([str(installer)])
    return proc.wait()
