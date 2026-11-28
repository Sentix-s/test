"""Opening sites in the browser, web/YouTube search.

YouTube search needs no API key: the public results page embeds the result
list as JSON (``ytInitialData``), which is parsed here. If YouTube changes
the page, a plain regex for video ids is the fallback, and the search-results
page itself is the last resort.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BROWSERS: dict[str, dict[str, Any]] = {
    "chrome": {"exe": "chrome.exe", "paths": [r"Google\Chrome\Application\chrome.exe"]},
    "edge": {"exe": "msedge.exe", "paths": [r"Microsoft\Edge\Application\msedge.exe"]},
    "firefox": {"exe": "firefox.exe", "paths": [r"Mozilla Firefox\firefox.exe"]},
    "yandex": {"exe": "browser.exe", "paths": [r"Yandex\YandexBrowser\Application\browser.exe"]},
    "opera": {"exe": "opera.exe", "paths": [r"Programs\Opera\opera.exe"]},
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")


def normalize_url(url: str) -> str:
    """Add https:// to bare domains; allow only web links."""
    url = url.strip()
    scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):(?!\d)", url)  # "x:" but not "host:8080"
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        raise ValueError(f"Это не веб-адрес: {url}")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or " " in parsed.netloc:
        raise ValueError(f"Это не веб-адрес: {url}")
    return url


def find_browser(name: str) -> str | None:
    """Path to a browser executable on Windows (App Paths registry, then usual folders)."""
    info = BROWSERS.get(name)
    if info is None or sys.platform != "win32":
        return None
    import winreg

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{info['exe']}") as k:
                path = winreg.QueryValue(k, None)
                if path and Path(path).exists():
                    return path
        except OSError:
            continue
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            for rel in info["paths"]:
                p = Path(base) / rel
                if p.exists():
                    return str(p)
    return None


def open_url(url: str, browser: str = "default") -> str:
    """Open a link; returns which browser was used."""
    url = normalize_url(url)
    if browser and browser != "default":
        exe = find_browser(browser)
        if exe is None:
            raise FileNotFoundError(f"Браузер {browser} не найден на этом компьютере")
        subprocess.Popen([exe, url], creationflags=_NO_WINDOW)
        return browser
    if not webbrowser.open(url, new=2):
        raise OSError("Не удалось открыть браузер по умолчанию")
    return "default"


def google_search_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}"


def youtube_search_url(query: str) -> str:
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}"


@dataclass
class Video:
    video_id: str
    title: str
    channel: str = ""
    duration: str = ""
    views: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    return "".join(r.get("text", "") for r in node.get("runs", []))


def _walk(node: Any, key: str):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            else:
                yield from _walk(v, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)


_INITIAL_MARKERS = ('var ytInitialData =', 'window["ytInitialData"] =', "window['ytInitialData'] =", "ytInitialData =")


def _js_string(html: str, start: int) -> str:
    """Read a JS string literal starting at html[start] (a quote) and unescape \\xNN / \\uNNNN."""
    quote = html[start]
    i, out = start + 1, []
    while i < len(html):
        ch = html[i]
        if ch == "\\" and i + 1 < len(html):
            nxt = html[i + 1]
            if nxt == "x":
                out.append(chr(int(html[i + 2:i + 4], 16)))
                i += 4
                continue
            if nxt == "u":
                out.append(chr(int(html[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            i += 2
            continue
        if ch == quote:
            break
        out.append(ch)
        i += 1
    return "".join(out)


def extract_initial_data(html: str) -> Any:
    """ytInitialData in any of the forms YouTube serves: an object literal or an escaped string."""
    decoder = json.JSONDecoder()
    for marker in _INITIAL_MARKERS:
        pos = html.find(marker)
        while pos >= 0:
            j = pos + len(marker)
            while j < len(html) and html[j] in " \t\r\n":
                j += 1
            try:
                if j < len(html) and html[j] == "{":
                    return decoder.raw_decode(html, j)[0]
                if j < len(html) and html[j] in "'\"":
                    return json.loads(_js_string(html, j))
            except (ValueError, IndexError):
                pass
            pos = html.find(marker, pos + 1)
    return None


def parse_youtube_results(html: str, limit: int = 5) -> list[Video]:
    videos: list[Video] = []
    data = extract_initial_data(html)
    if data is not None:
        seen: set[str] = set()
        for r in _walk(data, "videoRenderer"):
            vid = r.get("videoId")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            videos.append(Video(vid, _text(r.get("title")), _text(r.get("ownerText")),
                                _text(r.get("lengthText")), _text(r.get("viewCountText"))))
            if len(videos) >= limit:
                break
        # Newer YouTube layout: lockupViewModel cards.
        for r in _walk(data, "lockupViewModel"):
            if len(videos) >= limit:
                break
            vid = r.get("contentId")
            if not vid or vid in seen or "VIDEO" not in str(r.get("contentType", "VIDEO")):
                continue
            seen.add(vid)
            meta = (r.get("metadata") or {}).get("lockupMetadataViewModel") or {}
            title = ((meta.get("title") or {}).get("content")) or ""
            videos.append(Video(vid, str(title)))
    if not videos:  # page format changed: plain ids
        log.warning("YouTube page format not recognised; using bare video ids")
        seen = set()
        for vid in re.findall(r'"videoId":"([\w-]{11})"', html):
            if vid not in seen:
                seen.add(vid)
                videos.append(Video(vid, ""))
                if len(videos) >= limit:
                    break
    return videos


def youtube_search(query: str, limit: int = 5, timeout: float = 15.0) -> list[Video]:
    headers = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}
    cookies = {"CONSENT": "YES+cb", "SOCS": "CAI"}  # skip the EU consent page
    resp = httpx.get(youtube_search_url(query), headers=headers, cookies=cookies, timeout=timeout,
                     follow_redirects=True)
    resp.raise_for_status()
    return parse_youtube_results(resp.text, limit)
