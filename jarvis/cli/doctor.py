"""``jarvis doctor`` — checks that the environment can run JARVIS."""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass

import psutil
from rich.console import Console
from rich.table import Table

from jarvis.ai.ollama_client import OllamaClient
from jarvis.core.errors import LLMError
from jarvis.memory.database import Database
from jarvis.settings import AppPaths, Settings
from jarvis.tools.builtin.system import _gpu_info


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str


def run_checks(settings: Settings, paths: AppPaths, client: OllamaClient | None = None) -> list[Check]:
    checks: list[Check] = []
    v = sys.version_info
    checks.append(Check("Python", "ok" if (3, 11) <= v[:2] <= (3, 12) else "warn",
                        f"{platform.python_version()} (рекомендуется 3.12; поддерживается 3.11)"))
    is_win = sys.platform == "win32"
    checks.append(Check("ОС", "ok" if is_win else "warn",
                        f"{platform.system()} {platform.release()}" + ("" if is_win else " — Windows-функции недоступны")))
    ram = psutil.virtual_memory().total / 2**30
    checks.append(Check("RAM", "ok" if ram >= 15 else "warn", f"{ram:.1f} GB"))
    gpus = _gpu_info()
    if gpus:
        g = gpus[0]
        vram = (g.get("vram_total_mb") or 0) / 1024
        checks.append(Check("GPU", "ok" if vram >= 8 else "warn",
                            f"{g['name']}, VRAM {vram:.1f} GB, занято {(g.get('vram_used_mb') or 0) / 1024:.1f} GB"))
    else:
        checks.append(Check("GPU", "warn", "NVIDIA GPU не найден (nvidia-smi). Модель будет работать на CPU — медленно."))
    try:
        free = shutil.disk_usage(paths.data_dir if paths.data_dir.exists() else paths.data_dir.anchor or "/").free / 2**30
        checks.append(Check("Диск", "ok" if free >= 15 else "warn", f"свободно {free:.0f} GB"))
    except OSError as exc:
        checks.append(Check("Диск", "warn", str(exc)))
    try:
        paths.ensure()
        Database(paths.database_file).close()
        checks.append(Check("База данных", "ok", str(paths.database_file)))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("База данных", "fail", str(exc)))

    own = client is None
    client = client or OllamaClient(settings.ai.host, 30, settings.ai.connect_timeout)
    try:
        ver = client.version()
        checks.append(Check("Ollama", "ok", f"{ver} @ {settings.ai.host}"))
        installed = [m["name"] for m in client.list_models()]
        if client.has_model(settings.ai.model):
            checks.append(Check("Модель", "ok", settings.ai.model))
        else:
            fb = [m for m in settings.ai.fallback_models if client.has_model(m)]
            checks.append(Check("Модель", "warn" if fb else "fail",
                                f"{settings.ai.model} не установлена"
                                + (f", будет использована {fb[0]}" if fb else f". Выполните: jarvis setup")
                                + (f". Установлены: {', '.join(installed)}" if installed else "")))
        loaded = client.loaded_models()
        if loaded:
            desc = ", ".join(f"{m['name']} ({m.get('size_vram', 0) / 2**30:.1f} GB VRAM)" for m in loaded)
            checks.append(Check("Загружено", "ok", desc))
    except LLMError as exc:
        checks.append(Check("Ollama", "fail", f"{exc} Установка: winget install Ollama.Ollama"))
    finally:
        if own:
            client.close()
    return checks


def print_checks(checks: list[Check], console: Console | None = None) -> bool:
    console = console or Console()
    t = Table("проверка", "статус", "детали", title="JARVIS doctor")
    style = {"ok": "[green]OK[/green]", "warn": "[yellow]WARN[/yellow]", "fail": "[red]FAIL[/red]"}
    for c in checks:
        t.add_row(c.name, style[c.status], c.detail)
    console.print(t)
    return not any(c.status == "fail" for c in checks)
