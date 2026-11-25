"""Interactive text chat.

The agent runs in a worker thread; the main thread renders events and
handles Ctrl+C (= stop current task) and confirmation prompts. The GUI in
Phase 3 uses exactly the same pattern with Qt signals.
"""

from __future__ import annotations

import queue
import sys
import threading
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from jarvis import __version__
from jarvis.core.app import JarvisApp
from jarvis.core.errors import JarvisError
from jarvis.security.permissions import ConfirmRequest

HELP = """[bold]Команды:[/bold]
  /new            новый разговор
  /history        список разговоров
  /open <id>      продолжить разговор
  /tools          доступные инструменты
  /facts          долговременная память
  /audit          журнал действий
  /model          текущая модель
  /help           эта справка
  /exit           выход (модель выгружается из VRAM)
Ctrl+C во время ответа — остановить текущую задачу."""


class ChatSession:
    def __init__(self, app: JarvisApp, console: Console | None = None, conversation_id: int | None = None) -> None:
        self.app = app
        self.console = console or Console(highlight=False)
        self.conversation_id = conversation_id or app.conversations.create()
        self._q: queue.Queue[tuple[str, Any]] = queue.Queue()
        app.permissions.set_confirm_callback(self._confirm_from_worker)

    # Called in the worker thread: hand the question to the main thread and wait.
    def _confirm_from_worker(self, req: ConfirmRequest) -> bool:
        answer: dict[str, Any] = {"ok": False, "done": threading.Event()}
        self._q.put(("confirm", (req, answer)))
        answer["done"].wait()
        return bool(answer["ok"])

    def _ask_confirm(self, req: ConfirmRequest, answer: dict[str, Any]) -> None:
        self.console.print(f"\n[bold yellow]⚠ Требуется подтверждение[/bold yellow] ({req.risk.value}): "
                           f"{escape(req.summary)}")
        try:
            reply = input("Разрешить? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            reply = ""
        answer["ok"] = reply in ("y", "yes", "д", "да")
        answer["done"].set()

    def _worker(self, text: str) -> None:
        try:
            for event in self.app.ask(self.conversation_id, text):
                self._q.put(("event", event))
        except Exception as exc:  # noqa: BLE001
            self._q.put(("crash", exc))
        finally:
            self._q.put(("end", None))

    def send(self, text: str) -> str:
        """Send one message and render the streamed answer. Returns the final text."""
        worker = threading.Thread(target=self._worker, args=(text,), name="agent", daemon=True)
        worker.start()
        final, at_line_start = "", False  # the "JARVIS: " prefix is on the current line
        out = sys.stdout
        self.console.print("[bold cyan]JARVIS:[/bold cyan] ", end="")
        while True:
            try:
                kind, payload = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            except KeyboardInterrupt:
                if self.app.cancel_current_task("Ctrl+C"):
                    self.console.print("\n[red]■ остановка…[/red]")
                continue
            if kind == "end":
                break
            if kind == "confirm":
                self._ask_confirm(*payload)
                continue
            if kind == "crash":
                self.console.print(f"\n[red]Ошибка: {escape(str(payload))}[/red]")
                continue
            ev = payload
            if ev.kind == "text":
                out.write(ev.text)
                out.flush()
                at_line_start = ev.text.endswith("\n")
            elif ev.kind == "tool_start":
                self.console.print(("" if at_line_start else "\n") + f"[dim]  ⚙ {escape(ev.text)}[/dim]")
                at_line_start = True
            elif ev.kind == "tool_end":
                mark = "✓" if ev.data.get("ok") else "✗"
                self.console.print(f"[dim]  {mark} {ev.data.get('tool')} ({ev.data.get('duration_ms')} мс)[/dim]")
            elif ev.kind == "denied":
                self.console.print(f"[yellow]  ⛔ отклонено: {escape(ev.text)} — {ev.data.get('reason')}[/yellow]")
            elif ev.kind == "done":
                final = ev.text
            elif ev.kind == "cancelled":
                self.console.print("\n[red]Задача остановлена.[/red]")
            elif ev.kind == "error":
                self.console.print(f"\n[red]Ошибка: {escape(ev.text)}[/red]")
        worker.join(timeout=5)
        out.write("\n")
        out.flush()
        return final

    # ---------------------------------------------------------------- commands
    def command(self, line: str) -> bool:
        """Handle a /command. Returns False when the user wants to exit."""
        cmd, _, arg = line[1:].partition(" ")
        c = self.console
        if cmd in ("exit", "quit", "q"):
            return False
        if cmd == "help":
            c.print(HELP)
        elif cmd == "new":
            self.conversation_id = self.app.conversations.create()
            c.print(f"[green]Новый разговор #{self.conversation_id}[/green]")
        elif cmd == "history":
            t = Table("id", "обновлён", "сообщений", "тема")
            for info in self.app.conversations.list(20):
                t.add_row(str(info.id), info.updated_at[:16].replace("T", " "), str(info.message_count),
                          escape(info.title or "—"))
            c.print(t)
        elif cmd == "open":
            try:
                cid = int(arg)
            except ValueError:
                c.print("[red]Использование: /open <id>[/red]")
                return True
            if not self.app.conversations.exists(cid):
                c.print(f"[red]Разговора #{cid} нет[/red]")
                return True
            self.conversation_id = cid
            for m in self.app.conversations.messages(cid, limit=10):
                if m.role in ("user", "assistant") and m.content:
                    who = "Вы" if m.role == "user" else "JARVIS"
                    c.print(f"[dim]{who}: {escape(m.content[:300])}[/dim]")
            c.print(f"[green]Продолжаем разговор #{cid}[/green]")
        elif cmd == "tools":
            t = Table("инструмент", "риск", "описание")
            for tool in self.app.registry.all():
                t.add_row(tool.name, tool.risk.value, escape(tool.description.splitlines()[0][:90]))
            c.print(t)
        elif cmd == "facts":
            facts = self.app.facts.search(limit=50)
            if not facts:
                c.print("Память пуста.")
            for f in facts:
                c.print(f"  #{f.id} [{f.category}] {escape(f.content)}")
        elif cmd == "audit":
            t = Table("время", "инструмент", "риск", "решение", "ok")
            for r in reversed(self.app.audit.recent(20)):
                ok = "" if r["ok"] is None else ("✓" if r["ok"] else "✗")
                t.add_row(r["ts"][11:19], r["tool"], r["risk"], r["decision"], ok)
            c.print(t)
        elif cmd == "model":
            c.print(f"Модель: [bold]{self.app.model}[/bold] · Ollama: {self.app.settings.ai.host}")
        else:
            c.print(f"[red]Неизвестная команда /{escape(cmd)}[/red] — /help")
        return True

    def loop(self) -> None:
        c = self.console
        c.print(f"[bold cyan]JARVIS {__version__}[/bold cyan] · модель [bold]{self.app.model}[/bold] · "
                f"разговор #{self.conversation_id} · /help — команды")
        while True:
            try:
                line = input("\nВы: ").strip()
            except (EOFError, KeyboardInterrupt):
                c.print()
                break
            if not line:
                continue
            if line.startswith("/"):
                if not self.command(line):
                    break
                continue
            try:
                self.send(line)
            except JarvisError as exc:
                c.print(f"[red]{escape(str(exc))}[/red]")
