"""TikTok -> видео + текст.

Вставляете ссылку на видео TikTok: программа скачивает ролик (yt-dlp) и
распознаёт всю речь в нём (faster-whisper, локально, без платных API).
Видео, текст (.txt) и субтитры (.srt) сохраняются в одну папку.

Скачивание и распознавание идут параллельно: пока распознаётся одно видео,
следующее из очереди уже скачивается.

Запуск:
    python tiktok_transcriber.py                 окно программы
    python tiktok_transcriber.py <ссылка> ...    в консоли, текст печатается сразу
    python tiktok_transcriber.py video.mp4       распознать файл с компьютера
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

APP_TITLE = "TikTok → текст"
HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "settings.json"
LOG_FILE = HERE / "tiktok_transcriber.log"

# (имя модели faster-whisper, подпись в окне)
MODELS = [
    ("small", "Обычное (рекомендуется, ~500 МБ)"),
    ("base", "Быстрое (~150 МБ)"),
    ("medium", "Точное (~1.5 ГБ, медленнее)"),
    ("large-v3-turbo", "Максимальное (~1.6 ГБ, лучше с видеокартой)"),
]
LANGS = [
    ("auto", "Определить сам"),
    ("ru", "Русский"),
    ("en", "English"),
    ("uk", "Українська"),
    ("kk", "Қазақша"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("fr", "Français"),
    ("it", "Italiano"),
    ("tr", "Türkçe"),
]
MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4a", ".mp3", ".wav", ".ogg", ".opus", ".flac", ".aac"}

# Известные «галлюцинации» Whisper на музыке/тишине (титры из обучающих данных).
HALLUCINATION_RE = re.compile(
    r"DimaTorzok|Синецкая|Егорова|amara\.org|Субтитры (?:сделал|создавал|подогнал|делал)|"
    r"Редактор субтитров|Корректор [А-Я]\.",
    re.IGNORECASE,
)

log = logging.getLogger("tiktok")


def setup_logging() -> None:
    # pythonw.exe (запуск без консоли) даёт sys.stdout/stderr = None,
    # а полосы загрузки моделей пишут в stderr и падали бы.
    if sys.stderr is None or sys.stdout is None:
        try:
            sink = open(LOG_FILE, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        except OSError:
            sink = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s: %(message)s",
        handlers=handlers or [logging.NullHandler()],
    )


# ---------------------------------------------------------------- utilities


def default_out_dir() -> Path:
    home = Path.home()
    for base in (home / "Videos", home / "Downloads"):
        if base.is_dir():
            return base / "TikTok"
    return home / "TikTok"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        log.warning("cannot save settings", exc_info=True)


def find_urls(text: str) -> list[str]:
    """Достаёт ссылки из вставленного текста (в том числе «Поделиться» из приложения TikTok)."""
    urls = re.findall(r"https?://[^\s<>\"'«»]+", text)
    if not urls:  # vm.tiktok.com/XXXX без https://
        urls = ["https://" + u for u in re.findall(r"(?:[\w-]+\.)*tiktok\.com/[^\s<>\"'«»]+", text, re.IGNORECASE)]
    seen: list[str] = []
    for u in urls:
        u = u.rstrip(".,;:!?)]}")
        if u not in seen:
            seen.append(u)
    return seen


def fmt_clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def fmt_srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


Segment = tuple[float, float, str]  # (начало, конец, текст)


def plain_text(segments: list[Segment]) -> str:
    """Сплошной текст; длинная пауза в речи — новый абзац."""
    out: list[str] = []
    prev_end: float | None = None
    for start, end, text in segments:
        if prev_end is not None:
            out.append("\n\n" if start - prev_end > 2.0 else " ")
        out.append(text)
        prev_end = end
    return "".join(out)


def timed_text(segments: list[Segment]) -> str:
    return "\n".join(f"[{fmt_clock(s)}]  {t}" for s, _, t in segments)


def srt_text(segments: list[Segment]) -> str:
    return "\n".join(f"{i}\n{fmt_srt_time(s)} --> {fmt_srt_time(e)}\n{t}\n" for i, (s, e, t) in enumerate(segments, 1))


def open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        log.warning("cannot open %s", path, exc_info=True)


def friendly_error(exc: BaseException) -> str:
    msg = str(exc).strip() or exc.__class__.__name__
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)  # цвета терминала
    msg = re.sub(r"^ERROR:\s*", "", msg)
    msg = re.sub(r"^\[[\w:]+\]\s*[\w-]+:\s*", "", msg)
    low = msg.lower()
    if "unsupported url" in low:
        return "Это не похоже на ссылку на видео. Скопируйте ссылку через «Поделиться» → «Копировать ссылку»."
    if any(k in low for k in ("private", "not available", "unavailable", "status code 10204", "removed")):
        return f"Видео недоступно: удалено, приватное или закрыто для вашей страны.\n({msg})"
    if any(k in low for k in ("getaddrinfo", "timed out", "connection", "network is unreachable", "name resolution")):
        return f"Нет связи с TikTok. Проверьте интернет и попробуйте ещё раз.\n({msg})"
    if "ip address is blocked" in low or "not available in your" in low:
        return f"TikTok закрыл это видео для вашей страны. Попробуйте включить VPN или возьмите другое видео.\n({msg})"
    if any(k in low for k in ("unable to extract", "http error 403", "blocked", "captcha", "json")):
        return (
            "TikTok не отдал видео. Обычно помогает обновление загрузчика: закройте программу "
            f"и запустите start.bat снова (он обновит yt-dlp). Или попробуйте позже.\n({msg})"
        )
    return msg


# ------------------------------------------------------------------ work


@dataclass
class Job:
    url: str = ""
    model: str = "small"
    lang: str = "auto"
    gpu: bool = False
    out_dir: Path = field(default_factory=default_out_dir)
    video: Path | None = None
    title: str = ""
    state: str = "queued"  # queued, downloading, waiting, transcribing, done, error
    progress: float = 0.0  # 0..1 текущего этапа
    note: str = ""
    language: str = ""
    segments: list[Segment] = field(default_factory=list)
    txt_path: Path | None = None
    error: str = ""
    id: int = field(default_factory=itertools.count(1).__next__)

    @property
    def name(self) -> str:
        if self.title:
            return self.title
        if self.video:
            return self.video.name
        return self.url


def video_name(info: dict, video: Path) -> str:
    """«@автор — подпись к видео» для списка в окне."""
    author = info.get("uploader") or info.get("channel") or ""
    caption = " ".join((info.get("description") or "").split())
    if not caption and not str(info.get("title", "")).startswith("TikTok video #"):
        caption = " ".join(str(info.get("title") or "").split())
    parts = [f"@{author}" if author else "", caption or f"видео {info.get('id') or video.stem}"]
    return " — ".join(p for p in parts if p)[:120]


class _YdlLogger:
    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


def download(url: str, out_dir: Path, on_progress=None) -> tuple[Path, dict]:
    """Скачивает одно видео. Возвращает (путь к файлу, сведения о видео)."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {
        "paths": {"home": str(out_dir)},
        "outtmpl": {"default": "%(uploader,uploader_id|tiktok)s - %(id)s.%(ext)s"},
        # H.264 открывается любым плеером; TikTok часто отдаёт ещё и HEVC.
        # Без ffmpeg: только готовые файлы со звуком (у TikTok они все такие).
        "format": "b[vcodec^=h264]/b/b*",
        "playlist_items": "1",  # ссылка на профиль — только последнее видео, а не все
        "windowsfilenames": True,
        "trim_file_name": 150,
        "retries": 5,
        "fragment_retries": 5,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _YdlLogger(),
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("entries"):
            info = next(e for e in info["entries"] if e)
        downloads = info.get("requested_downloads") or []
        path = Path(downloads[0]["filepath"]) if downloads else Path(ydl.prepare_filename(info))
    if not path.exists():
        raise RuntimeError(f"Файл не появился после скачивания: {path}")
    return path, info


SAMPLE_RATE = 16000


def load_audio(path: Path):
    """Звук из видео: 16 кГц, моно, float32.

    Декодируем сами: faster-whisper 1.2 вызывает av.open(metadata_errors=...),
    а в новых версиях PyAV этого аргумента уже нет.
    """
    import av
    import numpy as np

    bad_data = getattr(av, "FFmpegError", Exception)
    chunks: list = []
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise RuntimeError("В видео нет звуковой дорожки.")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for packet in container.demux(stream):
            try:
                frames = packet.decode()
            except bad_data:  # битый кусок — пропускаем, как плееры
                continue
            for frame in frames:
                chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


class Transcriber:
    """Держит модель Whisper загруженной между видео."""

    def __init__(self) -> None:
        self._model = None
        self._key: tuple[str, bool] | None = None
        self._lock = threading.Lock()

    def _load(self, size: str, gpu: bool, on_status=None):
        if self._key == (size, gpu):
            return self._model
        from faster_whisper import WhisperModel

        if on_status:
            on_status(f"Загружаю модель распознавания «{size}» (только в первый раз может занять несколько минут)…")
        self._model = None
        if gpu:
            self._model = WhisperModel(size, device="cuda", compute_type="float16")
        else:
            threads = min(8, os.cpu_count() or 4)
            self._model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=threads)
        self._key = (size, gpu)
        log.info("model %s loaded (gpu=%s)", size, gpu)
        return self._model

    def preload(self, size: str, gpu: bool, on_status=None) -> None:
        with self._lock:
            try:
                self._load(size, gpu, on_status)
            except Exception:
                if not gpu:
                    raise
                log.warning("GPU model failed, preloading CPU instead", exc_info=True)
                self._load(size, False, on_status)

    def transcribe(self, path: Path, size: str, lang: str, gpu: bool, on_segment=None, on_status=None):
        """Возвращает (segments, язык). on_segment(segment, доля_готовности) вызывается по ходу."""
        with self._lock:
            try:
                return self._run(path, size, lang, gpu, on_segment, on_status)
            except Exception:
                if not gpu:
                    raise
                # Нет CUDA/cuDNN — тихо продолжаем на процессоре.
                log.warning("GPU transcription failed, falling back to CPU", exc_info=True)
                if on_status:
                    on_status("Видеокарта недоступна — распознаю на процессоре…")
                self._key = None
                return self._run(path, size, lang, False, on_segment, on_status)

    def _run(self, path, size, lang, gpu, on_segment, on_status):
        model = self._load(size, gpu, on_status)
        if on_status:
            on_status("Распознаю речь…")
        audio = load_audio(path)
        if audio.size < SAMPLE_RATE // 2:  # меньше полсекунды звука
            return [], ""
        segments_iter, info = model.transcribe(
            audio,
            language=None if lang == "auto" else lang,
            beam_size=5,
            vad_filter=True,  # пропускает музыку и тишину
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,  # меньше зацикливаний на длинных роликах
        )
        duration = info.duration or 0.0
        result: list[Segment] = []
        for seg in segments_iter:
            text = seg.text.strip()
            if not text or HALLUCINATION_RE.search(text):
                continue
            item = (float(seg.start), float(seg.end), text)
            result.append(item)
            if on_segment:
                on_segment(item, min(1.0, seg.end / duration) if duration else 0.0)
        return result, info.language


def save_outputs(video: Path, segments: list[Segment]) -> Path:
    txt = video.with_suffix(".txt")
    body = plain_text(segments) if segments else "(речь не найдена — возможно, в видео только музыка)"
    txt.write_text(body + "\n", encoding="utf-8-sig")  # -sig: Блокнот на Windows точно поймёт кириллицу
    if segments:
        video.with_suffix(".srt").write_text(srt_text(segments), encoding="utf-8")
    return txt


class Engine:
    """Два потока: скачивание и распознавание, между ними очередь."""

    def __init__(self, notify) -> None:
        self.notify = notify  # notify(job | None) — что-то изменилось
        self.transcriber = Transcriber()
        self.model_status = ""
        self._download_q: queue.Queue[Job] = queue.Queue()
        self._transcribe_q: queue.Queue[Job | tuple] = queue.Queue()
        threading.Thread(target=self._download_loop, name="download", daemon=True).start()
        threading.Thread(target=self._transcribe_loop, name="transcribe", daemon=True).start()

    def submit(self, job: Job) -> None:
        if job.video:
            job.state = "waiting"
            self._transcribe_q.put(job)
        else:
            self._download_q.put(job)
        self.notify(job)

    def preload(self, model: str, gpu: bool) -> None:
        self._transcribe_q.put(("preload", model, gpu))

    def _set_model_status(self, text: str) -> None:
        self.model_status = text
        self.notify(None)

    def _download_loop(self) -> None:
        while True:
            job = self._download_q.get()
            job.state, job.progress = "downloading", 0.0
            self.notify(job)

            def progress(p: float, job=job) -> None:
                job.progress = p
                self.notify(job)

            try:
                job.video, info = download(job.url, job.out_dir, progress)
                job.title = video_name(info, job.video)
                job.state, job.progress = "waiting", 0.0
                self._transcribe_q.put(job)
            except Exception as exc:
                log.exception("download failed: %s", job.url)
                job.state, job.error = "error", friendly_error(exc)
            self.notify(job)

    def _transcribe_loop(self) -> None:
        while True:
            item = self._transcribe_q.get()
            if isinstance(item, tuple):  # ("preload", model, gpu)
                _, model, gpu = item
                try:
                    self.transcriber.preload(model, gpu, self._set_model_status)
                    self._set_model_status("Распознавание готово к работе")
                except Exception as exc:
                    log.exception("preload failed")
                    self._set_model_status(f"Модель не загрузилась: {friendly_error(exc)}")
                continue
            job = item
            job.state, job.progress, job.segments = "transcribing", 0.0, []
            self.notify(job)

            def on_segment(seg: Segment, p: float, job=job) -> None:
                job.segments.append(seg)
                job.progress = p
                self.notify(job)

            def on_status(text: str, job=job) -> None:
                job.note = text
                self.notify(job)

            try:
                segments, job.language = self.transcriber.transcribe(
                    job.video, job.model, job.lang, job.gpu, on_segment, on_status
                )
                job.segments = segments
                job.txt_path = save_outputs(job.video, segments)
                job.state, job.progress, job.note = "done", 1.0, ""
                self._set_model_status("Распознавание готово к работе")
            except Exception as exc:
                log.exception("transcription failed: %s", job.video)
                job.state, job.error = "error", friendly_error(exc)
            self.notify(job)


# -------------------------------------------------------------------- GUI


def tk_safe(text: str) -> str:
    # Tk 8.6 на Windows плохо переносит символы вне BMP (эмодзи в названиях TikTok).
    return "".join(ch if ord(ch) < 0x10000 else "�" for ch in text)


STATE_LABEL = {
    "queued": "… в очереди",
    "downloading": "↓ скачиваю",
    "waiting": "… жду распознавания",
    "transcribing": "✎ распознаю",
    "done": "✔ готово",
    "error": "✖ ошибка",
}


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    if sys.platform == "win32":
        try:  # чёткий текст на экранах с масштабом 125–150 %
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            log.debug("DPI awareness unavailable")

    class App:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.settings = load_settings()
            self.events: queue.Queue = queue.Queue()
            self.jobs: list[Job] = []
            self.shown: tuple | None = None
            self.engine = Engine(self.events.put)
            self._build()
            self.engine.preload(self.model(), self.gpu_var.get())
            root.after(150, self._poll)

        # ---- layout
        def _build(self) -> None:
            r = self.root
            r.title(APP_TITLE)
            r.geometry("1040x680")
            r.minsize(760, 480)
            style = ttk.Style()
            if "vista" in style.theme_names():
                style.theme_use("vista")
            base_font = ("Segoe UI", 10)
            style.configure("Big.TButton", padding=(14, 6))

            main = ttk.Frame(r, padding=12)
            main.pack(fill="both", expand=True)
            main.columnconfigure(1, weight=1)
            main.rowconfigure(4, weight=1)

            ttk.Label(main, text="Ссылка на TikTok:").grid(row=0, column=0, sticky="w")
            self.url_var = tk.StringVar()
            self.url_entry = ttk.Entry(main, textvariable=self.url_var, font=("Segoe UI", 11))
            self.url_entry.grid(row=0, column=1, sticky="ew", padx=8, ipady=3)
            self.url_entry.bind("<Return>", lambda e: self.start())
            self.url_entry.focus_set()
            top = ttk.Frame(main)
            top.grid(row=0, column=2, sticky="e")
            ttk.Button(top, text="Вставить и начать", command=self.paste_and_start).pack(side="left")
            ttk.Button(top, text="Скачать и распознать", style="Big.TButton", command=self.start).pack(
                side="left", padx=(6, 0)
            )

            opts = ttk.Frame(main)
            opts.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
            ttk.Label(opts, text="Качество распознавания:").pack(side="left")
            self.model_cb = ttk.Combobox(opts, state="readonly", width=38, values=[lbl for _, lbl in MODELS])
            model_ids = [m for m, _ in MODELS]
            saved_model = self.settings.get("model", "small")
            self.model_cb.current(model_ids.index(saved_model) if saved_model in model_ids else 0)
            self.model_cb.pack(side="left", padx=(6, 14))
            self.model_cb.bind("<<ComboboxSelected>>", lambda e: self._options_changed(reload=True))
            ttk.Label(opts, text="Язык речи:").pack(side="left")
            self.lang_cb = ttk.Combobox(opts, state="readonly", width=16, values=[lbl for _, lbl in LANGS])
            lang_ids = [c for c, _ in LANGS]
            saved_lang = self.settings.get("lang", "auto")
            self.lang_cb.current(lang_ids.index(saved_lang) if saved_lang in lang_ids else 0)
            self.lang_cb.pack(side="left", padx=(6, 14))
            self.lang_cb.bind("<<ComboboxSelected>>", lambda e: self._options_changed())
            self.gpu_var = tk.BooleanVar(value=bool(self.settings.get("gpu", False)))
            ttk.Checkbutton(
                opts,
                text="Видеокарта NVIDIA",
                variable=self.gpu_var,
                command=lambda: self._options_changed(reload=True),
            ).pack(side="left")

            folder = ttk.Frame(main)
            folder.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
            ttk.Label(folder, text="Папка для видео и текста:").pack(side="left")
            self.dir_var = tk.StringVar(value=self.settings.get("out_dir") or str(default_out_dir()))
            ttk.Entry(folder, textvariable=self.dir_var, state="readonly").pack(
                side="left", fill="x", expand=True, padx=6
            )
            ttk.Button(folder, text="Изменить…", command=self.choose_dir).pack(side="left")
            ttk.Button(folder, text="Открыть папку", command=self.open_dir).pack(side="left", padx=(6, 0))
            ttk.Button(folder, text="Файл с компьютера…", command=self.add_files).pack(side="left", padx=(6, 0))

            self.status_var = tk.StringVar(
                value="Вставьте ссылку на видео TikTok и нажмите «Скачать и распознать» (или Enter)."
            )
            ttk.Label(main, textvariable=self.status_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 6))
            self.progress = ttk.Progressbar(main, mode="determinate", maximum=100, length=240)
            self.progress.grid(row=3, column=2, sticky="e", pady=(12, 6))

            paned = ttk.PanedWindow(main, orient="horizontal")
            paned.grid(row=4, column=0, columnspan=3, sticky="nsew")
            left = ttk.Frame(paned)
            ttk.Label(left, text="Видео").pack(anchor="w", pady=(0, 4))
            self.listbox = tk.Listbox(left, activestyle="none", exportselection=False, font=base_font, width=36)
            self.listbox.pack(fill="both", expand=True)
            self.listbox.bind("<<ListboxSelect>>", lambda e: self.refresh())
            self.listbox.bind("<Double-Button-1>", lambda e: self.open_video())
            paned.add(left, weight=1)

            right = ttk.Frame(paned)
            head = ttk.Frame(right)
            head.pack(fill="x", pady=(0, 4))
            ttk.Label(head, text="Текст из видео").pack(side="left")
            self.ts_var = tk.BooleanVar(value=bool(self.settings.get("timestamps", False)))
            ttk.Checkbutton(
                head, text="С таймкодами", variable=self.ts_var, command=lambda: self._options_changed()
            ).pack(side="right")
            box = ttk.Frame(right)
            box.pack(fill="both", expand=True)
            self.text = tk.Text(box, wrap="word", font=("Segoe UI", 11), padx=10, pady=8, undo=False)
            scroll = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
            self.text.configure(yscrollcommand=scroll.set)
            self.text.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            paned.add(right, weight=3)

            actions = ttk.Frame(main)
            actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))
            ttk.Button(actions, text="Копировать текст", style="Big.TButton", command=self.copy_text).pack(side="left")
            ttk.Button(actions, text="Открыть видео", command=self.open_video).pack(side="left", padx=(6, 0))
            ttk.Button(actions, text="Открыть .txt", command=self.open_txt).pack(side="left", padx=(6, 0))
            self.copied_var = tk.StringVar()
            ttk.Label(actions, textvariable=self.copied_var, foreground="#2a7").pack(side="left", padx=10)
            self.model_status = tk.StringVar()
            ttk.Label(actions, textvariable=self.model_status, foreground="#666").pack(side="right")

            # Ctrl+V / Ctrl+C / Ctrl+A на русской раскладке (Tk их сам не понимает).
            def ctrl_keys(event):
                if event.keysym.lower() in ("v", "c", "x", "a"):
                    return None
                action = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "<<SelectAll>>"}.get(event.keycode)
                if action:
                    event.widget.event_generate(action)
                    return "break"
                return None

            r.bind_all("<Control-KeyPress>", ctrl_keys)
            # Ctrl+A в поле ввода выделяет всё (по умолчанию в Tk — «в начало строки»).
            self.url_entry.bind("<<SelectAll>>", lambda e: (self.url_entry.select_range(0, "end"), "break")[1])

        # ---- helpers
        def model(self) -> str:
            return MODELS[max(0, self.model_cb.current())][0]

        def lang(self) -> str:
            return LANGS[max(0, self.lang_cb.current())][0]

        def _options_changed(self, reload: bool = False) -> None:
            self.settings.update(
                model=self.model(),
                lang=self.lang(),
                gpu=self.gpu_var.get(),
                timestamps=self.ts_var.get(),
                out_dir=self.dir_var.get(),
            )
            save_settings(self.settings)
            if reload:
                self.engine.preload(self.model(), self.gpu_var.get())
            self.shown = None
            self.refresh()

        def _new_job(self, **kw) -> None:
            job = Job(
                model=self.model(), lang=self.lang(), gpu=self.gpu_var.get(), out_dir=Path(self.dir_var.get()), **kw
            )
            self.jobs.append(job)
            self.engine.submit(job)
            self._rebuild_list()
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set("end")
            self.listbox.see("end")
            self.refresh()

        def selected(self) -> Job | None:
            sel = self.listbox.curselection()
            if sel and sel[0] < len(self.jobs):
                return self.jobs[sel[0]]
            return self.jobs[-1] if self.jobs else None

        # ---- actions
        def start(self) -> None:
            urls = find_urls(self.url_var.get())
            if not urls:
                messagebox.showinfo(
                    APP_TITLE,
                    "Вставьте ссылку на видео TikTok.\n\nВ приложении TikTok: «Поделиться» → «Копировать ссылку».",
                )
                return
            for url in urls:
                self._new_job(url=url)
            self.url_var.set("")

        def paste_and_start(self) -> None:
            try:
                self.url_var.set(self.root.clipboard_get())
            except tk.TclError:
                self.url_var.set("")
            self.start()

        def add_files(self) -> None:
            paths = filedialog.askopenfilenames(
                title="Видео или аудио для распознавания",
                filetypes=[("Видео и аудио", " ".join(f"*{e}" for e in sorted(MEDIA_EXT))), ("Все файлы", "*.*")],
            )
            for p in paths:
                self._new_job(video=Path(p))

        def choose_dir(self) -> None:
            d = filedialog.askdirectory(initialdir=self.dir_var.get(), title="Куда сохранять видео и текст")
            if d:
                self.dir_var.set(str(Path(d)))
                self._options_changed()

        def open_dir(self) -> None:
            d = Path(self.dir_var.get())
            d.mkdir(parents=True, exist_ok=True)
            open_path(d)

        def open_video(self) -> None:
            job = self.selected()
            if job and job.video and job.video.exists():
                open_path(job.video)

        def open_txt(self) -> None:
            job = self.selected()
            if job and job.txt_path and job.txt_path.exists():
                open_path(job.txt_path)

        def copy_text(self) -> None:
            text = self.text.get("1.0", "end-1c").strip()
            job = self.selected()
            if not text or not job or not job.segments:
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.copied_var.set("Скопировано ✔")
            self.root.after(2500, lambda: self.copied_var.set(""))

        # ---- refresh
        def _poll(self) -> None:
            changed = False
            try:
                while True:
                    self.events.get_nowait()
                    changed = True
            except queue.Empty:
                pass
            if changed:
                self._rebuild_list()
                self.refresh()
            self.root.after(150, self._poll)

        def _rebuild_list(self) -> None:
            sel = self.listbox.curselection()
            items = []
            for job in self.jobs:
                label = STATE_LABEL.get(job.state, job.state)
                if job.state in ("downloading", "transcribing") and job.progress:
                    label += f" {int(job.progress * 100)}%"
                items.append(tk_safe(f"{label} — {job.name}"))
            if list(self.listbox.get(0, "end")) != items:
                self.listbox.delete(0, "end")
                for it in items:
                    self.listbox.insert("end", it)
                for i in sel:
                    self.listbox.selection_set(i)
            colors = {"done": "#1a7f37", "error": "#c62828"}
            for i, job in enumerate(self.jobs):
                self.listbox.itemconfig(i, foreground=colors.get(job.state, "#000"))

        def refresh(self) -> None:
            self.model_status.set(tk_safe(self.engine.model_status))
            job = self.selected()
            if not job:
                return
            busy = sum(j.state in ("queued", "downloading", "waiting", "transcribing") for j in self.jobs)
            status = {
                "queued": "Видео в очереди…",
                "downloading": "Скачиваю видео…",
                "waiting": "Видео скачано, жду распознавания…",
                "transcribing": job.note or "Распознаю речь…",
                "done": f"Готово ✔  Сохранено в папку: {job.video.parent if job.video else ''}",
                "error": "Не получилось: " + job.error.splitlines()[0] if job.error else "Ошибка",
            }[job.state]
            if job.state == "done" and job.language:
                status += f"   (язык: {job.language})"
            if busy > 1:
                status += f"   ·   в работе ещё видео: {busy - 1}"
            self.status_var.set(tk_safe(status))
            self.progress["value"] = 100 if job.state == "done" else job.progress * 100

            if job.state == "error":
                body = "Не получилось.\n\n" + job.error
            elif job.segments:
                body = timed_text(job.segments) if self.ts_var.get() else plain_text(job.segments)
            elif job.state == "done":
                body = "(Речь не найдена — возможно, в видео только музыка.)"
            else:
                body = ""
            key = (job.id, body)
            if key != self.shown:
                live = job.state == "transcribing"
                self.text.delete("1.0", "end")
                self.text.insert("1.0", tk_safe(body))
                if live:
                    self.text.see("end")
                self.shown = key

    root = tk.Tk()
    run_gui.app = App(root)  # для автотеста
    root.mainloop()


# -------------------------------------------------------------------- CLI


def run_cli(args: argparse.Namespace) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    out_dir = Path(args.out) if args.out else default_out_dir()
    transcriber = Transcriber()
    failed = 0
    for item in args.inputs:
        local = Path(item)
        try:
            if local.is_file():
                video = local
            else:
                urls = find_urls(item)
                if not urls:
                    raise ValueError(f"Не ссылка и не файл: {item}")
                print(f"Скачиваю {urls[0]} …", file=sys.stderr)
                video, _ = download(urls[0], out_dir)
                print(f"Видео: {video}", file=sys.stderr)
            print("Распознаю речь …", file=sys.stderr)
            segments, language = transcriber.transcribe(
                video,
                args.model,
                args.lang,
                args.gpu,
                on_status=lambda s: print(s, file=sys.stderr),
            )
            txt = save_outputs(video, segments)
            print(f"Язык: {language}. Текст: {txt}", file=sys.stderr)
            print(timed_text(segments) if args.timestamps else plain_text(segments) or "(речь не найдена)")
            print()
        except Exception as exc:
            log.exception("cli failed: %s", item)
            print(f"Ошибка ({item}): {friendly_error(exc)}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(description="Скачать видео TikTok и распознать речь в нём.")
    p.add_argument("inputs", nargs="*", help="ссылки на TikTok или файлы видео; без аргументов откроется окно")
    p.add_argument("--model", default="small", choices=[m for m, _ in MODELS])
    p.add_argument("--lang", default="auto", help="ru, en, … или auto")
    p.add_argument("--out", help="папка для видео и текста")
    p.add_argument("--gpu", action="store_true", help="распознавать на видеокарте NVIDIA")
    p.add_argument("--timestamps", action="store_true", help="печатать текст с таймкодами")
    args = p.parse_args(argv)
    if args.inputs:
        return run_cli(args)
    try:
        run_gui()
    except Exception:
        log.exception("GUI crashed")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
