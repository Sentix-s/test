"""CUDA runtime for faster-whisper in the installed app.

CTranslate2 needs cuBLAS 12 and cuDNN 9 DLLs. Ollama ships its own copy, but
not for us. In a developer install they come from pip (``.[gpu]``); the
installed JARVIS.exe downloads NVIDIA's official wheels from PyPI once
(~1 GB) and extracts only the DLLs into ``<data_dir>/cuda``.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger(__name__)

PACKAGES = {  # package -> (min version prefix, DLL that proves it is installed)
    "nvidia-cublas-cu12": ("12.", "cublas64_12.dll"),
    "nvidia-cudnn-cu12": ("9.", "cudnn64_9.dll"),
}


def cuda_dir(data_dir: Path) -> Path:
    return data_dir / "cuda"


def installed(data_dir: Path) -> bool:
    d = cuda_dir(data_dir)
    return all((d / dll).exists() for _v, dll in PACKAGES.values())


def nvidia_gpu_present() -> bool:
    return shutil.which("nvidia-smi") is not None


def _wheel_url(package: str, prefix: str) -> tuple[str, int]:
    data = httpx.get(f"https://pypi.org/pypi/{package}/json", timeout=30).json()
    versions = sorted((v for v in data["releases"] if v.startswith(prefix)),
                      key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")], reverse=True)
    for v in versions:
        for f in data["releases"][v]:
            if f["filename"].endswith("win_amd64.whl"):
                return f["url"], int(f["size"])
    raise OSError(f"No Windows wheel found for {package}")


def extract_dlls(wheel: Path, dest: Path) -> list[str]:
    """Copy ``nvidia/*/bin/*.dll`` out of a wheel (a zip file)."""
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            parts = name.split("/")
            if len(parts) >= 4 and parts[0] == "nvidia" and parts[2] == "bin" and name.lower().endswith(".dll"):
                target = dest / parts[-1]
                with z.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                out.append(parts[-1])
    return out


def download(data_dir: Path, progress: Callable[[str, int, int], None] | None = None) -> Path:
    dest = cuda_dir(data_dir)
    with tempfile.TemporaryDirectory() as td:
        for package, (prefix, _dll) in PACKAGES.items():
            url, size = _wheel_url(package, prefix)
            wheel = Path(td) / url.rsplit("/", 1)[-1]
            done = 0
            with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(120, connect=20)) as r:
                r.raise_for_status()
                with open(wheel, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(package, done, size)
            dlls = extract_dlls(wheel, dest)
            log.info("Extracted %d DLLs from %s", len(dlls), package)
            wheel.unlink()
    if not installed(data_dir):
        raise OSError("CUDA libraries were downloaded but required DLLs are missing")
    return dest


def add_to_search_path(data_dir: Path) -> bool:
    d = cuda_dir(data_dir)
    if sys.platform != "win32" or not installed(data_dir):
        return False
    os.add_dll_directory(str(d))  # type: ignore[attr-defined]
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    return True
