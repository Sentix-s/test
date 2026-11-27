import json
import os
import sys

import pytest

from fake_ollama import text_turn, tool_turn
from jarvis.browser import web
from jarvis.core.errors import ToolError
from jarvis.tools.builtin import pc_tools
from jarvis.windows import apps, system

ENTRIES = [apps.AppEntry(n, f"id.{i}") for i, n in enumerate([
    "Google Chrome", "Microsoft Edge", "Word", "Excel", "PowerPoint", "Telegram Desktop", "Calculator",
    "Notepad", "Notepad++", "Steam", "Spotify", "Visual Studio Code", "Discord", "Paint", "Яндекс Браузер",
])]


@pytest.fixture
def catalog(monkeypatch):
    cat = apps.AppCatalog()
    cat.set_entries(ENTRIES)
    monkeypatch.setattr(pc_tools, "CATALOG", cat)
    return cat


@pytest.mark.parametrize("spoken,expected", [
    ("хром", "Google Chrome"), ("Гугл Хром", "Google Chrome"), ("chrome", "Google Chrome"),
    ("ворд", "Word"), ("Эксель", "Excel"), ("телеграм", "Telegram Desktop"), ("калькулятор", "Calculator"),
    ("блокнот", "Notepad"), ("стим", "Steam"), ("вс код", "Visual Studio Code"), ("дискорд", "Discord"),
    ("эдж", "Microsoft Edge"), ("яндекс браузер", "Яндекс Браузер"), ("notepad++", "Notepad++"),
])
def test_find_apps_by_spoken_name(catalog, spoken, expected):
    assert catalog.find(spoken)[0][0].name == expected


def test_unknown_app(catalog):
    assert catalog.find("фотошоп") == []


def test_parse_start_apps():
    raw = json.dumps([{"Name": "Google Chrome", "AppID": "Chrome"}, {"Name": "Calculator",
                      "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"}])
    assert [e.name for e in apps.parse_start_apps(raw)] == ["Google Chrome", "Calculator"]
    assert apps.parse_start_apps(json.dumps({"Name": "X", "AppID": "y"}))[0].app_id == "y"
    assert apps.parse_start_apps("") == []


def test_open_app_tool(catalog, monkeypatch):
    launched = []
    monkeypatch.setattr(apps, "launch_entry", lambda e, args="": launched.append(e.name))
    assert pc_tools.open_app("хром") == "Запущено: Google Chrome"
    assert launched == ["Google Chrome"]
    monkeypatch.setattr(apps, "launch_builtin", lambda key: False)
    with pytest.raises(ToolError, match="не найдена"):
        pc_tools.open_app("фотошоп")


@pytest.mark.parametrize("url,expected", [
    ("youtube.com", "https://youtube.com"), ("https://vk.com/feed", "https://vk.com/feed"),
    ("http://192.168.1.1", "http://192.168.1.1"), ("localhost:8080", "https://localhost:8080"),
])
def test_normalize_url(url, expected):
    assert web.normalize_url(url) == expected


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///C:/Windows/system32/cmd.exe", "https://", "ftp://x.y"])
def test_reject_non_web_urls(url):
    with pytest.raises(ValueError):
        web.normalize_url(url)


YT_HTML = """<html><script>var ytInitialData = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents":
{"sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [
{"adSlotRenderer": {}},
{"videoRenderer": {"videoId": "jfKfPfyJRdk", "title": {"runs": [{"text": "lofi hip hop radio"}]},
 "ownerText": {"runs": [{"text": "Lofi Girl"}]}, "lengthText": {"simpleText": "LIVE"}}},
{"videoRenderer": {"videoId": "5qap5aO4i9A", "title": {"runs": [{"text": "second"}]}}}
]}}]}}}}};</script></html>"""


def test_parse_youtube_results():
    v = web.parse_youtube_results(YT_HTML)
    assert [x.video_id for x in v] == ["jfKfPfyJRdk", "5qap5aO4i9A"]
    assert v[0].title == "lofi hip hop radio" and v[0].channel == "Lofi Girl"
    assert v[0].url == "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    # fallback when the JSON block is missing
    assert web.parse_youtube_results('..."videoId":"abcdefghijk"..."videoId":"abcdefghijk"...')[0].video_id == "abcdefghijk"
    assert web.parse_youtube_results("<html></html>") == []


def test_youtube_open(monkeypatch):
    opened = []
    monkeypatch.setattr(web, "open_url", lambda url, browser="default": opened.append((url, browser)) or browser)
    monkeypatch.setattr(web, "youtube_search", lambda q, limit=5: web.parse_youtube_results(YT_HTML, limit))
    msg = pc_tools.youtube_open("лофи музыка", browser="chrome")
    assert opened[-1] == ("https://www.youtube.com/watch?v=jfKfPfyJRdk", "chrome") and "lofi hip hop radio" in msg
    pc_tools.youtube_open()
    assert opened[-1][0] == "https://www.youtube.com"
    pc_tools.youtube_open("котики", results_page=True)
    assert opened[-1][0].startswith("https://www.youtube.com/results?search_query=")

    def boom(q, limit=5):
        raise OSError("offline")
    monkeypatch.setattr(web, "youtube_search", boom)
    assert "открыт поиск" in pc_tools.youtube_open("что-то")  # falls back to the results page


def test_settings_uri():
    assert system.settings_uri("Звук") == "ms-settings:sound"
    assert system.settings_uri("bluetooth") == "ms-settings:bluetooth"
    assert system.settings_uri("что-то непонятное") == "ms-settings:"


def test_protected_processes_never_closed():
    closed, refused = apps.close_processes(["explorer.exe", "svchost.exe", "MsMpEng.exe", "ollama.exe"])
    assert closed == [] and len(refused) == 4


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_windows_only_tools_fail_cleanly():
    with pytest.raises(ToolError):
        pc_tools.media_control("play_pause")


def test_agent_opens_youtube_video(app, fake_ollama, monkeypatch):
    """The model picks youtube_open for 'открой ютуб и включи ...'."""
    opened = []
    monkeypatch.setattr(web, "open_url", lambda url, browser="default": opened.append(url) or browser)
    monkeypatch.setattr(web, "youtube_search", lambda q, limit=5: web.parse_youtube_results(YT_HTML, limit))
    fake_ollama.turns = [tool_turn("youtube_open", {"query": "лофи хип хоп", "browser": "chrome"}),
                         text_turn("Включил «lofi hip hop radio», сэр.")]
    events = list(app.ask(app.conversations.create(), "Джарвис, открой ютуб в хроме и включи лофи хип хоп"))
    assert opened == ["https://www.youtube.com/watch?v=jfKfPfyJRdk"]
    assert events[-1].text == "Включил «lofi hip hop radio», сэр."
    sent_tools = {t["function"]["name"] for t in fake_ollama.chat_requests()[0]["tools"]}
    assert {"open_app", "youtube_open", "open_website", "media_control"} <= sent_tools
    assert "youtube_open" in fake_ollama.chat_requests()[0]["messages"][0]["content"]  # prompt mentions it


# ----------------------------------------------------------------- live checks

@pytest.mark.skipif(sys.platform != "win32", reason="Windows Start menu")
def test_real_start_menu_catalog():
    cat = apps.AppCatalog()
    entries = cat.entries()
    assert len(entries) > 5, entries
    names = [e.name for e in entries]
    print("start menu sample:", names[:15])
    assert cat.find("эдж") and "Edge" in cat.find("эдж")[0][0].name


@pytest.mark.skipif(os.environ.get("JARVIS_TEST_NET") != "1", reason="set JARVIS_TEST_NET=1")
def test_real_youtube_search():
    videos = web.youtube_search("lofi hip hop radio")
    print("youtube:", [(v.video_id, v.title, v.channel) for v in videos])
    assert videos and len(videos[0].video_id) == 11 and videos[0].title
