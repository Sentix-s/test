"""End-to-end: the real CLI process against the fake Ollama server."""

import os
import subprocess
import sys
from pathlib import Path

from fake_ollama import text_turn, tool_turn

ROOT = Path(__file__).resolve().parents[1]


def jarvis(tmp_path, url, *args, stdin=""):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "COLUMNS": "120"}
    return subprocess.run([sys.executable, "-m", "jarvis", "--home", str(tmp_path), "--host", url, *args],
                          input=stdin, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)


def test_ask_one_shot_with_tool(tmp_path, fake_ollama):
    fake_ollama.turns = [tool_turn("calculate", {"expression": "2^10"}), text_turn("Получается 1024.")]
    r = jarvis(tmp_path, fake_ollama.url, "ask", "сколько", "2^10?")
    assert r.returncode == 0, r.stderr
    assert "calculate(expression='2^10')" in r.stdout and "Получается 1024." in r.stdout
    assert (tmp_path / "data" / "jarvis.db").exists() and (tmp_path / "logs" / "jarvis.log").exists()
    assert "qwen3:8b" not in fake_ollama.loaded  # unloaded on exit


def test_interactive_chat_and_commands(tmp_path, fake_ollama):
    fake_ollama.turns = [text_turn("Здравствуйте, сэр.")]
    r = jarvis(tmp_path, fake_ollama.url, "chat", stdin="/tools\nПривет\n/history\n/facts\n/audit\n/bogus\n/exit\n")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "get_system_info" in out and "Здравствуйте, сэр." in out and "Неизвестная команда" in out
    assert "Привет" in out  # conversation title in /history


def test_confirmation_prompt_in_chat(tmp_path, fake_ollama):
    # forget_fact is MODERATE; with confirm_moderate the CLI must ask
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("security:\n  confirm_moderate: true\n", encoding="utf-8")
    fake_ollama.turns = [tool_turn("forget_fact", {"fact_id": 1}), text_turn("Отменено пользователем.")]
    r = jarvis(tmp_path, fake_ollama.url, "chat", stdin="забудь факт 1\nn\n/exit\n")
    assert r.returncode == 0, r.stderr
    assert "Требуется подтверждение" in r.stdout and "отклонено" in r.stdout


def test_doctor_and_missing_ollama(tmp_path, fake_ollama):
    r = jarvis(tmp_path, fake_ollama.url, "doctor")
    assert "Ollama" in r.stdout and "0.12.0" in r.stdout and "qwen3:8b" in r.stdout
    r2 = jarvis(tmp_path, "http://127.0.0.1:9", "chat", stdin="")
    assert r2.returncode == 1 and "Ollama не отвечает" in r2.stdout


def test_setup_pulls_model(tmp_path, fake_ollama):
    r = jarvis(tmp_path, fake_ollama.url, "setup", "--model", "qwen2.5:7b-instruct")
    assert r.returncode == 0, r.stderr
    assert "Готово" in r.stdout
    assert "qwen2.5:7b-instruct" in fake_ollama.models


def test_invalid_config_exit_code(tmp_path, fake_ollama):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("voice:\n  idle_timeout_seconds: 3\n", encoding="utf-8")
    r = jarvis(tmp_path, fake_ollama.url, "config")
    assert r.returncode == 2 and "idle_timeout_seconds" in r.stdout


def test_no_console_streams_do_not_break_downloads():
    """Real bug from the user's PC: model download in JARVIS.exe (no console) crashed in tqdm."""
    code = (
        "import sys; sys.stdout = None; sys.stderr = None\n"
        "from jarvis.__main__ import ensure_std_streams; ensure_std_streams()\n"
        "from tqdm import tqdm; list(tqdm(range(3)))\n"
        "import os; assert os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] == '1'\n"
        "open(sys.argv[1], 'w').write('ok')\n"
    )
    import tempfile
    marker = Path(tempfile.mkdtemp()) / "ok.txt"
    r = subprocess.run([sys.executable, "-c", code, str(marker)], capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT)}, timeout=60)
    assert r.returncode == 0 and marker.read_text() == "ok", r.stderr
