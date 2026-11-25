# JARVIS — персональный локальный ИИ-ассистент для Windows

Локальный ассистент на базе Qwen (через Ollama) с инструментальной архитектурой:
модель сама выбирает и комбинирует инструменты, опасные действия требуют
подтверждения, всё сохраняется в локальной SQLite-памяти. Без платных API и облака.

> **Статус: Phase 1 из 7** — ядро, ИИ-движок, агентный цикл с инструментами,
> память, безопасность, текстовый чат. Голос, GUI, управление Windows и т.д. —
> в следующих фазах (см. [docs/ROADMAP.md](docs/ROADMAP.md)).

## Требования

| | Минимум | Целевая конфигурация |
|---|---|---|
| ОС | Windows 10/11 x64 | Windows 11 |
| Python | 3.11 | **3.12** |
| GPU | любой / CPU (медленно) | RTX 3060 12 GB |
| RAM | 16 GB | 32 GB |
| Диск | ~10 GB (модель 5.2 GB) | SSD NVMe |
| [Ollama](https://ollama.com/download/windows) | 0.9+ (`think`, стриминг tool calls) | последняя |

## Установка (Windows)

```bat
git clone <repo> jarvis
cd jarvis
jarvis.bat
```

Первый запуск `jarvis.bat` вызывает `scripts\install.ps1`, который:
1. находит Python 3.12/3.11 (предложит установить через `winget`, если нет);
2. создаёт `.venv` и ставит зависимости;
3. проверяет Ollama (предложит `winget install Ollama.Ollama`) и запускает сервер;
4. скачивает модель `qwen3:8b` (~5.2 GB);
5. запускает диагностику `jarvis doctor`.

Ручная установка:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -e .
winget install Ollama.Ollama
.venv\Scripts\python -m jarvis setup      # ollama pull qwen3:8b
.venv\Scripts\python -m jarvis doctor
```

## Запуск

```bat
jarvis.bat                    :: текстовый чат
jarvis.bat chat --continue    :: продолжить последний разговор
jarvis.bat ask "сколько будет 2^32?"
jarvis.bat doctor             :: диагностика: Python, GPU/VRAM, Ollama, модель, БД
jarvis.bat setup --model qwen2.5:7b-instruct
jarvis.bat config             :: пути и действующие настройки
```

В чате: `/new`, `/history`, `/open <id>`, `/tools`, `/facts`, `/audit`, `/model`,
`/help`, `/exit`. **Ctrl+C во время ответа** останавливает текущую задачу
(генерация прерывается сразу, соединение с моделью закрывается).
При выходе модель выгружается из видеопамяти.

## Настройки

Файл `%APPDATA%\JARVIS\settings.yaml` хранит только изменённые значения; всё
остальное — встроенные умолчания, подобранные под RTX 3060 12 GB.
Пример со всеми ключами: [config/settings.example.yaml](config/settings.example.yaml).

| Что | Где |
|---|---|
| Настройки | `%APPDATA%\JARVIS\settings.yaml` |
| База (разговоры, память, журнал) | `%LOCALAPPDATA%\JARVIS\data\jarvis.db` |
| Логи | `%LOCALAPPDATA%\JARVIS\logs\jarvis.log` |
| Портативный режим | `jarvis.bat --home D:\JarvisData` или переменная `JARVIS_HOME` |

Рекомендуемые переменные окружения **сервера Ollama** (снижают VRAM под контекст):
`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` — задаются в
«Переменные среды» Windows, затем перезапуск Ollama.

## Разработка

```bash
pip install -e ".[dev]"
pytest            # 60 тестов; реальный HTTP-эмулятор Ollama, модель не нужна
```

Документация: [архитектура и оценка стека](docs/ARCHITECTURE.md) ·
[план фаз](docs/ROADMAP.md) · [отчёт Phase 1](docs/reports/PHASE1.md).
