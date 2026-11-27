"""Installed applications: find, launch, list running, close.

The catalog comes from the same place as the Start menu: PowerShell
``Get-StartApps`` (desktop programs *and* Microsoft Store apps, with their
AppUserModelIDs), plus Start-menu shortcuts as a fallback. Launching goes
through ``explorer shell:AppsFolder\\<AppID>`` - exactly what clicking the
Start-menu tile does, so no paths need to be guessed.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from jarvis.voice.text import normalize

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Spoken / Russian names -> words that appear in the real app name.
ALIASES: dict[str, list[str]] = {
    "chrome": ["хром", "гугл хром", "google chrome", "хроме", "chrome"],
    "edge": ["edge", "эдж", "майкрософт эдж", "microsoft edge"],
    "firefox": ["firefox", "файрфокс", "мозилла", "фаерфокс"],
    "yandex": ["яндекс браузер", "yandex", "яндекс"],
    "opera": ["опера", "opera"],
    "word": ["ворд", "word", "майкрософт ворд"],
    "excel": ["эксель", "excel", "ексель"],
    "powerpoint": ["пауэрпоинт", "powerpoint", "поверпоинт", "презентации"],
    "outlook": ["аутлук", "outlook"],
    "notepad": ["блокнот", "notepad"],
    "calculator": ["калькулятор", "calculator"],
    "paint": ["паинт", "пейнт", "paint"],
    "file explorer": ["проводник", "explorer", "файлы", "мой компьютер"],
    "task manager": ["диспетчер задач", "task manager"],
    "settings": ["параметры", "настройки", "settings"],
    "telegram": ["телеграм", "телеграмм", "telegram"],
    "discord": ["дискорд", "discord"],
    "steam": ["стим", "steam"],
    "spotify": ["спотифай", "spotify"],
    "visual studio code": ["вс код", "vs code", "vscode", "визуал студио код"],
    "obs studio": ["обс", "obs"],
    "whatsapp": ["ватсап", "вотсап", "whatsapp"],
    "vlc": ["влц", "vlc"],
    "terminal": ["терминал", "terminal", "командная строка"],
    "capcut": ["капкут", "capcut"],
    "davinci resolve": ["давинчи", "davinci"],
    "premiere pro": ["премьер", "premiere"],
}

# Built-in Windows programs that are not always listed by Get-StartApps.
BUILTIN_COMMANDS: dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "paint": ["mspaint.exe"],
    "file explorer": ["explorer.exe"],
    "task manager": ["taskmgr.exe"],
    "settings": ["explorer.exe", "ms-settings:"],
    "terminal": ["wt.exe"],
}

# Never close these: closing them breaks Windows, security software or JARVIS itself.
PROTECTED_PROCESSES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "sihost.exe", "ctfmon.exe", "spoolsv.exe",
    "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe", "securityhealthsystray.exe", "audiodg.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "runtimebroker.exe", "taskhostw.exe", "conhost.exe",
    "ollama.exe", "ollama app.exe", "jarvis.exe", "jarvis-cli.exe", "python.exe", "pythonw.exe",
}

# App name -> process image names (for closing).
PROCESS_NAMES: dict[str, list[str]] = {
    "chrome": ["chrome.exe"], "edge": ["msedge.exe"], "firefox": ["firefox.exe"], "yandex": ["browser.exe"],
    "opera": ["opera.exe"], "word": ["winword.exe"], "excel": ["excel.exe"], "powerpoint": ["powerpnt.exe"],
    "outlook": ["outlook.exe"], "notepad": ["notepad.exe"], "calculator": ["calculatorapp.exe", "calc.exe"],
    "paint": ["mspaint.exe"], "telegram": ["telegram.exe"], "discord": ["discord.exe"], "steam": ["steam.exe"],
    "spotify": ["spotify.exe"], "visual studio code": ["code.exe"], "obs studio": ["obs64.exe"],
    "vlc": ["vlc.exe"], "task manager": ["taskmgr.exe"], "whatsapp": ["whatsapp.exe"], "capcut": ["capcut.exe"],
}


@dataclass(frozen=True)
class AppEntry:
    name: str
    app_id: str = ""        # AppUserModelID from Get-StartApps
    shortcut: str = ""      # .lnk path (fallback catalog)


def canonical(query: str) -> str:
    """Map a spoken name to a canonical key ("хром" -> "chrome"); unknown names pass through."""
    q = normalize(query)
    for key, names in ALIASES.items():
        if q == key or q in names:
            return key
    for key, names in ALIASES.items():
        if any(difflib.SequenceMatcher(None, q, n).ratio() >= 0.85 for n in names):
            return key
    return q


def parse_start_apps(raw: str) -> list[AppEntry]:
    """Parse ``Get-StartApps | ConvertTo-Json`` output."""
    if not raw.strip():
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return [AppEntry(str(d.get("Name", "")), str(d.get("AppID", ""))) for d in data if d.get("Name")]


class AppCatalog:
    def __init__(self, ttl: float = 300.0) -> None:
        self.ttl = ttl
        self._entries: list[AppEntry] = []
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def _load_start_apps(self) -> list[AppEntry]:
        if sys.platform != "win32":
            return []
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=20, creationflags=_NO_WINDOW, check=True).stdout
            return parse_start_apps(out.decode("utf-8", errors="replace"))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            log.warning("Get-StartApps failed: %s", exc)
            return []

    @staticmethod
    def _load_shortcuts() -> list[AppEntry]:
        dirs = []
        if sys.platform == "win32":
            for env in ("ProgramData", "APPDATA"):
                base = os.environ.get(env)
                if base:
                    dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        out = []
        for d in dirs:
            if d.exists():
                for lnk in d.rglob("*.lnk"):
                    out.append(AppEntry(lnk.stem, shortcut=str(lnk)))
        return out

    def entries(self, refresh: bool = False) -> list[AppEntry]:
        with self._lock:
            if refresh or not self._entries or time.monotonic() - self._loaded_at > self.ttl:
                apps = self._load_start_apps()
                known = {normalize(a.name) for a in apps}
                apps += [s for s in self._load_shortcuts() if normalize(s.name) not in known]
                self._entries = apps
                self._loaded_at = time.monotonic()
            return list(self._entries)

    def set_entries(self, entries: list[AppEntry]) -> None:  # tests / non-Windows
        with self._lock:
            self._entries, self._loaded_at = list(entries), time.monotonic()

    def find(self, query: str, limit: int = 5) -> list[tuple[AppEntry, float]]:
        """Best matches for a spoken app name, best first."""
        key = canonical(query)
        words = [normalize(w) for w in ALIASES.get(key, [])] + [key, normalize(query)]
        scored: list[tuple[AppEntry, float]] = []
        raw = query.strip().lower()
        for e in self.entries():
            name = normalize(e.name)
            best = 1.01 if e.name.lower() == raw else 0.0  # exact spelling wins ("Notepad++" vs "Notepad")
            for w in words:
                if not w:
                    continue
                if name == w:
                    s = 1.0
                elif f" {w} " in f" {name} ":  # whole-word containment: "chrome" in "google chrome"
                    s = 0.9 - 0.01 * max(0, len(name.split()) - len(w.split()))
                else:
                    s = difflib.SequenceMatcher(None, name, w).ratio() * 0.85
                best = max(best, s)
            if best >= 0.6:
                scored.append((e, best))
        scored.sort(key=lambda x: (-x[1], len(x[0].name)))
        return scored[:limit]


def launch_entry(entry: AppEntry, args: str = "") -> None:
    if sys.platform != "win32":
        raise OSError("Запуск приложений поддерживается только в Windows")
    if entry.app_id:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{entry.app_id}"], creationflags=_NO_WINDOW)
    elif entry.shortcut:
        os.startfile(entry.shortcut)  # type: ignore[attr-defined]
    else:
        raise OSError(f"Не знаю, как запустить {entry.name}")


def launch_builtin(key: str) -> bool:
    cmd = BUILTIN_COMMANDS.get(key)
    if not cmd or sys.platform != "win32":
        return False
    subprocess.Popen(cmd, creationflags=_NO_WINDOW)
    return True


@dataclass
class RunningApp:
    name: str
    pid: int
    title_count: int
    memory_mb: float


def running_apps() -> list[RunningApp]:
    """User processes, grouped by image name (no system/protected processes)."""
    groups: dict[str, RunningApp] = {}
    user = None
    try:
        user = psutil.Process().username()
    except psutil.Error:
        pass
    for p in psutil.process_iter(["name", "username", "memory_info", "pid"]):
        try:
            name = (p.info["name"] or "").lower()
            if not name or name in PROTECTED_PROCESSES or (user and p.info["username"] != user):
                continue
            mem = (p.info["memory_info"].rss if p.info["memory_info"] else 0) / 2**20
        except (psutil.Error, KeyError):
            continue
        g = groups.get(name)
        if g is None:
            groups[name] = RunningApp(name, p.info["pid"], 1, mem)
        else:
            g.title_count += 1
            g.memory_mb += mem
    return sorted(groups.values(), key=lambda a: -a.memory_mb)


def process_names_for(query: str) -> list[str]:
    key = canonical(query)
    if key in PROCESS_NAMES:
        return PROCESS_NAMES[key]
    q = normalize(query).replace(" ", "")
    if not q:
        return []
    names = {a.name for a in running_apps()}
    return sorted(n for n in names if q in n.replace(".exe", "").replace(" ", ""))


def close_processes(names: list[str]) -> tuple[list[str], list[str]]:
    """Politely close (like clicking ×, so apps can ask to save). Returns (closed, refused)."""
    closed, refused = [], []
    for n in names:
        if n.lower() in PROTECTED_PROCESSES:
            refused.append(n)
            continue
        if sys.platform == "win32":
            r = subprocess.run(["taskkill", "/IM", n], capture_output=True, creationflags=_NO_WINDOW)
            (closed if r.returncode == 0 else refused).append(n)
        else:
            found = False
            for p in psutil.process_iter(["name"]):
                if (p.info["name"] or "").lower() == n.lower():
                    found = True
                    try:
                        p.terminate()
                    except psutil.Error:
                        pass
            (closed if found else refused).append(n)
    return closed, refused
