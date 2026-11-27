"""Tools to control the PC: apps, browser, YouTube, media keys, Windows Settings."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from jarvis.browser import web
from jarvis.core.errors import ToolError
from jarvis.tools.base import RiskLevel, tool
from jarvis.windows import apps, system

CATALOG = apps.AppCatalog()

BrowserName = Literal["default", "chrome", "edge", "firefox", "yandex", "opera"]


@tool(risk=RiskLevel.MODERATE, category="apps")
def open_app(name: Annotated[str, "App name as the user said it, e.g. 'хром', 'Word', 'Telegram', 'блокнот', 'калькулятор'"]) -> str:
    """Open (launch) an installed program on this computer: browsers, Office, Telegram, Steam, Notepad, Calculator, etc. For websites use open_website instead."""
    matches = CATALOG.find(name)
    if matches and matches[0][1] >= 0.75:
        entry = matches[0][0]
        try:
            apps.launch_entry(entry)
        except OSError as exc:
            raise ToolError(str(exc)) from exc
        return f"Запущено: {entry.name}"
    key = apps.canonical(name)
    try:
        if apps.launch_builtin(key):
            return f"Запущено: {key}"
    except OSError as exc:
        raise ToolError(str(exc)) from exc
    if matches:
        options = ", ".join(e.name for e, _s in matches)
        raise ToolError(f"Точного совпадения для '{name}' нет. Похожие программы: {options}. Уточни у пользователя.")
    raise ToolError(f"Программа '{name}' не найдена среди установленных. Можно вызвать list_installed_apps.")


@tool(risk=RiskLevel.SAFE, category="apps")
def list_installed_apps(filter: Annotated[str, "Part of the name to look for; empty = all"] = "") -> list[str] | str:  # noqa: A002
    """List programs installed on this computer (from the Start menu)."""
    names = sorted({e.name for e in CATALOG.entries()}, key=str.lower)
    if filter:
        f = filter.lower()
        names = [n for n in names if f in n.lower()] or [e.name for e, _s in CATALOG.find(filter, 10)]
    if not names:
        return "Ничего не найдено."
    return names[:150]


@tool(risk=RiskLevel.SAFE, category="apps")
def list_running_apps() -> list[dict[str, Any]]:
    """List programs currently running for this user (process name, number of processes, memory)."""
    return [{"process": a.name, "processes": a.title_count, "memory_mb": round(a.memory_mb)}
            for a in apps.running_apps()[:40]]


@tool(risk=RiskLevel.MODERATE, category="apps")
def close_app(name: Annotated[str, "App name, e.g. 'хром', 'Word', 'Telegram'"]) -> str:
    """Close a running program politely (like clicking ×, so it may ask to save). System processes are never closed."""
    targets = apps.process_names_for(name)
    if not targets:
        raise ToolError(f"Не нашёл запущенную программу '{name}'. Можно вызвать list_running_apps.")
    closed, refused = apps.close_processes(targets)
    if not closed:
        raise ToolError(f"Не удалось закрыть: {', '.join(refused)} (не запущено или защищённый системный процесс)")
    return "Закрыто: " + ", ".join(closed) + (f". Не закрыто: {', '.join(refused)}" if refused else "")


@tool(risk=RiskLevel.MODERATE, category="browser")
def open_website(url: Annotated[str, "Address, e.g. 'youtube.com', 'https://vk.com', 'mail.google.com'"],
                 browser: Annotated[BrowserName, "Which browser; 'default' = the user's default browser"] = "default") -> str:
    """Open a website in the browser."""
    try:
        used = web.open_url(url, browser)
    except (ValueError, FileNotFoundError, OSError) as exc:
        raise ToolError(str(exc)) from exc
    return f"Открыто {web.normalize_url(url)} в браузере: {used}"


@tool(risk=RiskLevel.MODERATE, category="browser")
def web_search(query: Annotated[str, "What to search for"],
               browser: Annotated[BrowserName, "Which browser"] = "default") -> str:
    """Open a Google search for the query in the browser (shows results to the user)."""
    try:
        used = web.open_url(web.google_search_url(query), browser)
    except (ValueError, FileNotFoundError, OSError) as exc:
        raise ToolError(str(exc)) from exc
    return f"Открыт поиск Google «{query}» ({used})"


@tool(risk=RiskLevel.SAFE, category="browser")
def youtube_search(query: Annotated[str, "What to look for on YouTube"]) -> list[dict[str, str]] | str:
    """Find YouTube videos (does not open anything): returns titles, channels, durations and video ids."""
    try:
        videos = web.youtube_search(query)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"YouTube недоступен: {exc}") from exc
    if not videos:
        return "Ничего не найдено."
    return [{"video_id": v.video_id, "title": v.title, "channel": v.channel, "duration": v.duration} for v in videos]


@tool(risk=RiskLevel.MODERATE, category="browser")
def youtube_open(query: Annotated[str, "What to play, e.g. 'лофи музыка', 'обзор RTX 3060'. Empty = YouTube home page"] = "",
                 video_id: Annotated[str, "Exact video id from youtube_search, if the user chose one"] = "",
                 browser: Annotated[BrowserName, "Which browser"] = "default",
                 results_page: Annotated[bool, "True = show the search results page instead of playing the first video"] = False) -> str:
    """Open YouTube in the browser: play the best matching video for a query (or a given video id), show search results, or the home page."""
    try:
        if video_id:
            web.open_url(f"https://www.youtube.com/watch?v={video_id}", browser)
            return f"Открыто видео {video_id}"
        if not query:
            web.open_url("https://www.youtube.com", browser)
            return "Открыт YouTube"
        if results_page:
            web.open_url(web.youtube_search_url(query), browser)
            return f"Открыты результаты поиска YouTube «{query}»"
        try:
            videos = web.youtube_search(query, limit=1)
        except Exception:  # noqa: BLE001 - fall back to the results page
            videos = []
        if not videos:
            web.open_url(web.youtube_search_url(query), browser)
            return f"Видео выбрать не удалось, открыт поиск YouTube «{query}»"
        v = videos[0]
        web.open_url(v.url, browser)
        return f"Открыто видео «{v.title or v.video_id}»" + (f" ({v.channel}, {v.duration})" if v.channel else "")
    except (ValueError, FileNotFoundError, OSError) as exc:
        raise ToolError(str(exc)) from exc


@tool(risk=RiskLevel.MODERATE, category="media")
def media_control(action: Annotated[Literal["play_pause", "next", "previous", "stop", "volume_up", "volume_down", "mute"],
                                    "play_pause = пауза/продолжить; volume_up/down changes volume by ~2% per step"],
                  times: Annotated[int, "How many presses (for volume: 5 ≈ 10%)"] = 1) -> str:
    """Control playback and system volume (works for YouTube in the browser, Spotify, players): pause/resume, next/previous track, volume, mute."""
    try:
        system.press_media_key(action, min(max(times, 1), 50))
    except OSError as exc:
        raise ToolError(str(exc)) from exc
    return f"Выполнено: {action} ×{times}"


@tool(risk=RiskLevel.MODERATE, category="system")
def open_windows_settings(section: Annotated[str, "Settings section, e.g. 'звук', 'bluetooth', 'экран', 'wifi', 'обновления', 'автозагрузка'"] = "") -> str:
    """Open a Windows Settings page (opens the page only, does not change settings)."""
    uri = system.settings_uri(section)
    try:
        system.open_uri(uri)
    except OSError as exc:
        raise ToolError(str(exc)) from exc
    return f"Открыты параметры Windows: {section or 'главная'} ({uri})"
