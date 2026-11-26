"""Voice-related CLI commands: voice mode, voice selection, say, devices, transcribe."""

from __future__ import annotations

import logging

import threading
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, DownloadColumn, Progress, TransferSpeedColumn
from rich.table import Table

from jarvis.settings import AppPaths, Settings, load_settings, save_settings
from jarvis.voice.models import VOICE_CATALOG, ModelStore

log = logging.getLogger(__name__)

STATE_LABELS = {
    "sleep": "[dim]💤 сон — скажите ключевое слово[/dim]",
    "active": "[green]🎙 слушаю[/green]",
    "processing": "[cyan]⚙ думаю…[/cyan]",
    "speaking": "[cyan]🔊 говорю[/cyan]",
    "off": "[dim]выключено[/dim]",
}


def _download_progress(console: Console) -> tuple[Progress, Any]:
    prog = Progress("[progress.description]{task.description}", BarColumn(), DownloadColumn(),
                    TransferSpeedColumn(), console=console, transient=True)
    tasks: dict[str, Any] = {}

    def cb(label: str, done: int, total: int) -> None:
        if label not in tasks:
            tasks[label] = prog.add_task(label[:40], total=total or None)
        prog.update(tasks[label], completed=done, total=total or None)

    return prog, cb


def _resolve_voice(choice: str, store: ModelStore) -> str:
    """A number from the list, a catalog id or an installed custom voice."""
    all_ids = [v.id for v in VOICE_CATALOG] + [v for v in store.installed_voices()
                                               if v not in {c.id for c in VOICE_CATALOG}]
    if choice.isdigit() and 1 <= int(choice) <= len(all_ids):
        return all_ids[int(choice) - 1]
    if choice in all_ids:
        return choice
    raise SystemExit(f"Неизвестный голос '{choice}'. Список: jarvis voices")


def cmd_voices(args: Any, settings: Settings, paths: AppPaths, console: Console) -> int:
    store = ModelStore(paths.data_dir / "models")
    changed = False
    file_settings = load_settings(paths.settings_file)  # save without CLI overrides
    prog, cb = _download_progress(console)
    with prog:
        if args.set:
            vid = _resolve_voice(args.set, store)
            store.ensure_voice(vid, cb)
            file_settings.voice.tts_voice = settings.voice.tts_voice = vid
            changed = True
        if args.en:
            if args.en.lower() in ("none", "off", "-"):
                file_settings.voice.tts_voice_en = settings.voice.tts_voice_en = ""
            else:
                vid = _resolve_voice(args.en, store)
                store.ensure_voice(vid, cb)
                file_settings.voice.tts_voice_en = settings.voice.tts_voice_en = vid
            changed = True
    if args.speed is not None:
        file_settings.voice.tts_speed = settings.voice.tts_speed = args.speed
        changed = True
    if changed:
        save_settings(file_settings, paths.settings_file)
        console.print(f"[green]Сохранено:[/green] голос {settings.voice.tts_voice}, "
                      f"английский {settings.voice.tts_voice_en or '—'}, скорость {settings.voice.tts_speed}")
        if args.set or args.speed is not None:
            args.test = True

    t = Table("№", "голос", "язык", "", "описание", "установлен", title="Голоса JARVIS (Piper)")
    installed = set(store.installed_voices())
    n = 0
    for v in VOICE_CATALOG:
        n += 1
        mark = "★ основной" if v.id == settings.voice.tts_voice else ("★ англ." if v.id == settings.voice.tts_voice_en else "")
        t.add_row(str(n), v.id, v.language, mark, v.description, "✓" if v.id in installed else "")
    for vid in sorted(installed - {v.id for v in VOICE_CATALOG}):
        n += 1
        t.add_row(str(n), vid, "?", "★ основной" if vid == settings.voice.tts_voice else "", "свой голос", "✓")
    console.print(t)
    console.print("Выбрать: [bold]jarvis voices --set 2[/bold] · английский: [bold]--en 5[/bold] (или none) · "
                  f"скорость: [bold]--speed 1.1[/bold] · свои голоса Piper кладите в {store.voices_dir}")
    if args.test:
        try:
            return cmd_say(Any_(text="Здравствуйте, сэр. Я Джарвис, ваш персональный ассистент. Чем могу помочь?",
                                voice=None, out=None), settings, paths, console)
        except Exception as exc:  # noqa: BLE001 - no speakers must not undo a saved choice
            log.warning("Preview playback failed: %s", exc)
            console.print(f"[yellow]Голос сохранён, но воспроизвести пример не удалось: {escape(str(exc))}[/yellow]")
    return 0


class Any_:  # tiny namespace for internal calls
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def cmd_say(args: Any, settings: Settings, paths: AppPaths, console: Console) -> int:
    from jarvis.voice.models import write_wav
    from jarvis.voice.tts import PiperTTS

    store = ModelStore(paths.data_dir / "models")
    voice = _resolve_voice(args.voice, store) if args.voice else None
    tts = PiperTTS(store, voice or settings.voice.tts_voice, "" if voice else settings.voice.tts_voice_en,
                   settings.voice.tts_speed, settings.voice.tts_volume, auto_download=False)
    prog, cb = _download_progress(console)
    with prog:
        store.ensure_voice(tts.voice_id, cb)
        if tts.voice_en_id:
            try:
                store.ensure_voice(tts.voice_en_id, cb)
            except OSError as exc:
                console.print(f"[yellow]Английский голос недоступен, используется основной: {escape(str(exc).splitlines()[0])}[/yellow]")
                tts.voice_en_id = ""
    speech = tts.synthesize(args.text)
    if speech is None:
        console.print("[red]Пустой текст[/red]")
        return 1
    if args.out:
        write_wav(Path(args.out), speech.audio, speech.sample_rate)
        console.print(f"[green]Сохранено:[/green] {args.out} ({len(speech.audio) / speech.sample_rate:.1f} с, "
                      f"голос {tts.voice_for(args.text)})")
        return 0
    from jarvis.voice.audio import AudioOutput

    console.print(f"🔊 {tts.voice_for(args.text)}: {escape(args.text)}")
    AudioOutput(settings.voice.output_device).play(speech.audio, speech.sample_rate)
    return 0


def cmd_devices(_args: Any, settings: Settings, _paths: AppPaths, console: Console) -> int:
    from jarvis.voice.audio import list_devices

    t = Table("№", "устройство", "вход", "выход", "по умолчанию")
    for d in list_devices():
        default = " ".join(x for x, f in (("микрофон", d["default_input"]), ("динамики", d["default_output"])) if f)
        t.add_row(str(d["index"]), d["name"], str(d["inputs"]), str(d["outputs"]), default)
    console.print(t)
    console.print("В настройках: voice.input_device / voice.output_device — номер или часть названия.")
    return 0


def cmd_transcribe(args: Any, settings: Settings, paths: AppPaths, console: Console) -> int:
    from jarvis.voice.models import read_wav, resample
    from jarvis.voice.stt import WhisperSTT

    audio, sr = read_wav(Path(args.file))
    stt = WhisperSTT(ModelStore(paths.data_dir / "models"), settings.voice.stt_model, settings.voice.stt_device,
                     settings.voice.stt_language, progress=lambda m: console.print(f"[dim]{m}[/dim]"))
    text = stt.transcribe(resample(audio, sr, 16000))
    console.print(f"[dim]{stt.model_name} на {stt.device}[/dim]")
    print(text)
    return 0


def cmd_voice_mode(app: Any, paths: AppPaths, console: Console) -> int:
    """Hands-free mode in the console."""
    from jarvis.voice.engine import VoiceEngine

    printed_answer = threading.Event()

    def listener(name: str, data: dict[str, Any]) -> None:
        if name == "state":
            console.print(STATE_LABELS.get(data["state"], data["state"]))
        elif name == "heard" and data.get("text"):
            console.print(f"[bold]Вы:[/bold] {escape(data['text'])}")
        elif name == "answered":
            console.print(f"[bold cyan]JARVIS:[/bold cyan] {escape(data['text'].strip())}")
            printed_answer.set()
        elif name == "agent.tool_start":
            console.print(f"[dim]  ⚙ {escape(data.get('text', ''))}[/dim]")
        elif name == "error":
            console.print(f"[red]{escape(data.get('text', ''))}[/red]")

    engine = VoiceEngine(app, paths, listener)
    try:
        with console.status("Подготовка голоса…") as status:
            engine.load(progress=lambda m: status.update(m))
    except Exception as exc:  # noqa: BLE001
        log.exception("Voice init failed")
        console.print(f"[red]Голос не запустился: {escape(str(exc))}[/red]")
        return 1
    s = app.settings.voice
    wake = s.wake_words[0].capitalize() if s.wake_engine == "whisper" else "Hey Jarvis"
    console.print(f"[bold cyan]Голосовой режим.[/bold cyan] Скажите «{wake}». "
                  f"Тайм-аут тишины: {s.idle_timeout_seconds} с. Enter — активировать без ключевого слова, "
                  "текст + Enter — команда, /exit — выход.")
    engine.start()
    try:
        while True:
            try:
                line = input().strip()
            except EOFError:
                threading.Event().wait()  # no stdin (e.g. started from a shortcut): run until Ctrl+C
                continue
            if line in ("/exit", "/quit"):
                break
            if line == "/sleep":
                engine.deactivate()
            elif line == "/stop":
                engine.stop_speaking()
            elif line:
                engine.submit_text(line)
            else:
                engine.activate()
    except KeyboardInterrupt:
        pass
    finally:
        console.print("[dim]Выключение голоса…[/dim]")
        engine.stop()
    return 0


def add_voice_parsers(sub: Any) -> None:
    sub.add_parser("voice", help="голосовой режим (ключевое слово → команда → ответ голосом)")
    v = sub.add_parser("voices", help="список голосов и выбор голоса")
    v.add_argument("--set", help="основной голос: номер из списка или id")
    v.add_argument("--en", help="голос для английского текста: номер, id или none")
    v.add_argument("--speed", type=float, help="скорость речи 0.5–2.0")
    v.add_argument("--test", action="store_true", help="прослушать текущий голос")
    say = sub.add_parser("say", help="произнести текст")
    say.add_argument("text")
    say.add_argument("--voice", help="голос: номер или id")
    say.add_argument("--out", help="сохранить в WAV вместо воспроизведения")
    sub.add_parser("devices", help="микрофоны и динамики")
    tr = sub.add_parser("transcribe", help="распознать речь из WAV-файла")
    tr.add_argument("file")


