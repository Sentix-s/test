"""Entry point: ``python -m jarvis [chat|ask|doctor|setup|config]``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

from jarvis import __version__

log = logging.getLogger("jarvis")


def _configure_console() -> None:
    # Windows consoles default to a legacy code page; force UTF-8 for Russian text.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis", description="JARVIS — локальный ИИ-ассистент")
    p.add_argument("--version", action="version", version=f"JARVIS {__version__}")
    p.add_argument("--home", help="папка для настроек/данных/логов (портативный режим)")
    p.add_argument("--host", help="адрес Ollama, по умолчанию из настроек")
    p.add_argument("--model", help="модель Ollama, по умолчанию из настроек")
    p.add_argument("--debug", action="store_true", help="подробный лог")
    sub = p.add_subparsers(dest="command")
    chat = sub.add_parser("chat", help="текстовый чат (по умолчанию)")
    chat.add_argument("--continue", dest="resume", action="store_true", help="продолжить последний разговор")
    ask = sub.add_parser("ask", help="один запрос без интерактива")
    ask.add_argument("text", nargs="+")
    ask.add_argument("--yes", action="store_true", help="разрешать опасные действия без вопроса (осторожно)")
    sub.add_parser("doctor", help="проверка окружения")
    setup = sub.add_parser("setup", help="скачать модель в Ollama")
    setup.add_argument("--model", dest="setup_model", help="какую модель скачать")
    sub.add_parser("config", help="показать пути и действующие настройки")
    return p


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = _build_parser().parse_args(argv)
    if args.home:
        os.environ["JARVIS_HOME"] = args.home

    from rich.console import Console

    from jarvis.core.errors import ConfigError, JarvisError
    from jarvis.logging_setup import setup_logging
    from jarvis.settings import AppPaths, load_settings

    console = Console(highlight=False)
    paths = AppPaths.default().ensure()
    overrides: dict[str, Any] = {}
    if args.host:
        overrides.setdefault("ai", {})["host"] = args.host
    if args.model:
        overrides.setdefault("ai", {})["model"] = args.model
    if args.debug:
        overrides["logging"] = {"level": "DEBUG"}
    try:
        settings = load_settings(paths.settings_file, overrides)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    log_file = setup_logging(settings.logging, paths.log_dir, console=args.debug)
    log.info("JARVIS %s starting, command=%s", __version__, args.command or "chat")
    command = args.command or "chat"

    if command == "config":
        import yaml
        console.print(f"Настройки: {paths.settings_file}\nДанные:    {paths.data_dir}\nЛоги:      {log_file}\n")
        console.print(yaml.safe_dump(settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False))
        return 0

    if command == "doctor":
        from jarvis.cli.doctor import print_checks, run_checks
        return 0 if print_checks(run_checks(settings, paths), console) else 1

    if command == "setup":
        from rich.progress import BarColumn, DownloadColumn, Progress, TransferSpeedColumn

        from jarvis.ai.ollama_client import OllamaClient
        model = args.setup_model or settings.ai.model
        with OllamaClient(settings.ai.host) as client:
            try:
                if client.has_model(model):
                    console.print(f"[green]Модель {model} уже установлена.[/green]")
                    return 0
                console.print(f"Скачивание {model}…")
                with Progress("[progress.description]{task.description}", BarColumn(), DownloadColumn(),
                              TransferSpeedColumn(), console=console) as prog:
                    tasks: dict[str, Any] = {}

                    def on_progress(d: dict[str, Any]) -> None:
                        key = d.get("digest") or d.get("status", "")
                        if d.get("total"):
                            if key not in tasks:
                                tasks[key] = prog.add_task(d.get("status", "")[:30], total=d["total"])
                            prog.update(tasks[key], completed=d.get("completed", 0))

                    client.pull(model, on_progress)
                console.print(f"[green]Готово: {model}[/green]")
                return 0
            except JarvisError as exc:
                console.print(f"[red]{exc}[/red]")
                return 1

    from jarvis.core.app import JarvisApp

    app = JarvisApp(settings, paths)
    try:
        with console.status("Запуск JARVIS…") as status:
            app.start(progress=lambda m: status.update(m))
    except JarvisError as exc:
        console.print(f"[red]Не удалось запустить: {exc}[/red]\nДиагностика: python -m jarvis doctor")
        app.close()
        return 1

    from jarvis.cli.chat import ChatSession
    try:
        if command == "ask":
            session = ChatSession(app, console)  # installs the interactive confirm callback
            if args.yes:
                app.permissions.set_confirm_callback(lambda _req: True)
            session.send(" ".join(args.text))
        else:
            cid = app.conversations.latest_id() if args.resume else None
            ChatSession(app, console, cid).loop()
    finally:
        app.close()
        console.print("[dim]JARVIS выключен, модель выгружена из памяти.[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
