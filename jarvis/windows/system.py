"""Media keys, volume and Windows Settings pages."""

from __future__ import annotations

import os
import sys

VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
      "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}

# Spoken settings section -> ms-settings: URI
SETTINGS_PAGES: dict[str, str] = {
    "главная": "ms-settings:", "система": "ms-settings:display", "экран": "ms-settings:display",
    "дисплей": "ms-settings:display", "звук": "ms-settings:sound", "уведомления": "ms-settings:notifications",
    "питание": "ms-settings:powersleep", "батарея": "ms-settings:batterysaver", "хранилище": "ms-settings:storagesense",
    "bluetooth": "ms-settings:bluetooth", "блютуз": "ms-settings:bluetooth", "устройства": "ms-settings:devices",
    "мышь": "ms-settings:mousetouchpad", "сеть": "ms-settings:network", "wifi": "ms-settings:network-wifi",
    "вайфай": "ms-settings:network-wifi", "интернет": "ms-settings:network", "персонализация": "ms-settings:personalization",
    "фон": "ms-settings:personalization-background", "темы": "ms-settings:themes", "цвета": "ms-settings:colors",
    "приложения": "ms-settings:appsfeatures", "автозагрузка": "ms-settings:startupapps",
    "учетные записи": "ms-settings:yourinfo", "время": "ms-settings:dateandtime", "язык": "ms-settings:regionlanguage",
    "игры": "ms-settings:gaming-gamebar", "специальные возможности": "ms-settings:easeofaccess",
    "конфиденциальность": "ms-settings:privacy", "микрофон": "ms-settings:privacy-microphone",
    "камера": "ms-settings:privacy-webcam", "обновления": "ms-settings:windowsupdate",
    "центр обновления": "ms-settings:windowsupdate", "безопасность": "windowsdefender:",
    "защитник": "windowsdefender:", "принтеры": "ms-settings:printers", "дисплей ночной свет": "ms-settings:nightlight",
}


def press_media_key(action: str, times: int = 1) -> None:
    if sys.platform != "win32":
        raise OSError("Управление медиа поддерживается только в Windows")
    import ctypes

    vk = VK[action]
    user32 = ctypes.windll.user32
    for _ in range(max(1, times)):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def settings_uri(section: str) -> str:
    from jarvis.voice.text import normalize

    s = normalize(section)
    if s in SETTINGS_PAGES:
        return SETTINGS_PAGES[s]
    for key, uri in SETTINGS_PAGES.items():
        if key in s or s in key:
            return uri
    return "ms-settings:"


def open_uri(uri: str) -> None:
    if sys.platform != "win32":
        raise OSError("Только для Windows")
    os.startfile(uri)  # type: ignore[attr-defined]
