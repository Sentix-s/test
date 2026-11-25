import threading

import pytest

from fake_ollama import text_turn, tool_turn
from jarvis.core.app import AppState, JarvisApp
from jarvis.core.errors import JarvisError, ModelNotFoundError
from jarvis.ai.ollama_client import OllamaClient
from jarvis.tools.base import RiskLevel, tool


def run(app, cid, text):
    return list(app.ask(cid, text))


def test_start_stop_loads_and_unloads(app, fake_ollama):
    assert app.state is AppState.READY and app.model == "qwen3:8b"
    assert "qwen3:8b" in fake_ollama.loaded  # warmed up into VRAM
    app.stop()
    assert app.state is AppState.OFF and "qwen3:8b" not in fake_ollama.loaded


def test_fallback_model_and_missing_model(fake_ollama, settings, paths):
    fake_ollama.models = ["qwen2.5:7b-instruct"]
    a = JarvisApp(settings, paths, llm=OllamaClient(fake_ollama.url))
    a.start(warmup=False)
    assert a.model == "qwen2.5:7b-instruct"
    a.close()
    fake_ollama.models = []
    b = JarvisApp(settings, paths, llm=OllamaClient(fake_ollama.url))
    with pytest.raises(ModelNotFoundError):
        b.start()
    assert b.state is AppState.ERROR
    b.close()


def test_plain_answer_is_streamed_and_persisted(app, fake_ollama):
    fake_ollama.turns = [text_turn("Добрый ", "вечер, сэр.")]
    cid = app.conversations.create()
    events = run(app, cid, "Привет")
    assert "".join(e.text for e in events if e.kind == "text") == "Добрый вечер, сэр."
    assert events[-1].kind == "done" and events[-1].text == "Добрый вечер, сэр."
    msgs = app.conversations.messages(cid)
    assert [(m.role, m.content) for m in msgs] == [("user", "Привет"), ("assistant", "Добрый вечер, сэр.")]
    assert app.conversations.list()[0].title == "Привет"
    req = fake_ollama.chat_requests()[-1]
    assert req["messages"][0]["role"] == "system" and "JARVIS" in req["messages"][0]["content"]
    assert {t["function"]["name"] for t in req["tools"]} >= {"calculate", "get_datetime"}
    assert req["options"]["num_ctx"] == 8192 and req["think"] is False
    assert app.state is AppState.READY


def test_multi_step_tool_use(app, fake_ollama):
    fake_ollama.turns = [
        tool_turn("calculate", {"expression": "1234*5678"}),
        tool_turn("remember_fact", {"fact": "Любимое число пользователя — 7006652", "category": "preference"}),
        text_turn("1234 × 5678 = 7 006 652. Запомнил."),
    ]
    cid = app.conversations.create()
    events = run(app, cid, "Посчитай 1234*5678 и запомни результат как моё любимое число")
    kinds = [e.kind for e in events if e.kind != "text"]
    assert kinds == ["tool_start", "tool_end", "tool_start", "tool_end", "done"]
    # tool result went back to the model
    second = fake_ollama.chat_requests()[1]["messages"]
    assert second[-1] == {"role": "tool", "content": "7006652", "tool_name": "calculate"}
    assert second[-2]["tool_calls"][0]["function"]["name"] == "calculate"
    assert app.facts.search("7006652")
    # the remembered fact is now part of the system prompt
    fake_ollama.turns = [text_turn("7006652")]
    run(app, cid, "Какое моё любимое число?")
    assert "7006652" in fake_ollama.chat_requests()[-1]["messages"][0]["content"]
    audit = app.audit.recent()
    assert [r["tool"] for r in reversed(audit)] == ["calculate", "remember_fact"] and all(r["ok"] for r in audit)


def test_text_tool_call_fallback(app, fake_ollama):
    fake_ollama.turns = [
        text_turn('<tool_call>\n{"name": "calculate", "arguments": {"expression": "6*7"}}\n</tool_call>'),
        text_turn("42"),
    ]
    events = run(app, app.conversations.create(), "6*7?")
    assert [e.text for e in events if e.kind == "text"] == ["42"]  # markup never shown
    assert any(e.kind == "tool_end" and e.data["ok"] for e in events)


def test_dangerous_tool_requires_confirmation(app, fake_ollama):
    deleted = []

    @tool(risk=RiskLevel.DANGEROUS)
    def delete_everything(path: str) -> str:
        """Delete."""
        deleted.append(path)
        return "deleted"

    app.registry.register(delete_everything)
    # 1) no interactive user -> refused
    fake_ollama.turns = [tool_turn("delete_everything", {"path": "C:/x"}), text_turn("Не могу без подтверждения.")]
    events = run(app, app.conversations.create(), "удали")
    assert any(e.kind == "denied" for e in events) and deleted == []
    assert "Permission denied" in fake_ollama.chat_requests()[-1]["messages"][-1]["content"]
    # 2) user confirms -> executed
    app.permissions.set_confirm_callback(lambda req: req.tool == "delete_everything")
    fake_ollama.turns = [tool_turn("delete_everything", {"path": "C:/x"}), text_turn("Удалено.")]
    run(app, app.conversations.create(), "удали")
    assert deleted == ["C:/x"]
    decisions = [(r["tool"], r["decision"]) for r in reversed(app.audit.recent())]
    assert decisions == [("delete_everything", "deny"), ("delete_everything", "allow")]


def test_unknown_tool_and_step_limit(app, fake_ollama):
    app.settings.ai.max_tool_iterations = 2
    fake_ollama.turns = [tool_turn("hack_pentagon", {}), tool_turn("calculate", {"expression": "1"}),
                         text_turn("Готово частично.")]
    events = run(app, app.conversations.create(), "x")
    assert events[-1].kind == "done" and events[-1].data.get("step_limit")
    last = fake_ollama.chat_requests()[-1]
    assert "tools" not in last  # final summary without tools
    assert "Unknown tool" in fake_ollama.chat_requests()[1]["messages"][-1]["content"]


def test_cancel_running_task(settings, paths):
    from fake_ollama import FakeOllama
    with FakeOllama(chunk_delay=0.05) as fake:
        settings.ai.host = fake.url
        a = JarvisApp(settings, paths, llm=OllamaClient(fake.url))
        a.start()
        fake.turns = [text_turn(*["бла "] * 300)]
        cid = a.conversations.create()
        threading.Timer(0.3, a.cancel_current_task).start()
        events = list(a.ask(cid, "говори долго"))
        assert events[-1].kind == "cancelled" and a.state is AppState.READY
        assert a.conversations.messages(cid)[-1].content == "[задача остановлена пользователем]"
        a.close()


def test_history_context_and_budget(app, fake_ollama):
    cid = app.conversations.create()
    for i in range(3):
        fake_ollama.turns = [text_turn(f"ответ {i}")]
        run(app, cid, f"вопрос {i}")
    msgs = fake_ollama.chat_requests()[-1]["messages"]
    assert [m["content"] for m in msgs[1:]] == ["вопрос 0", "ответ 0", "вопрос 1", "ответ 1", "вопрос 2"]
    app.settings.ai.history_char_budget = 1000
    app.conversations.add_message(cid, __import__("jarvis.ai.types", fromlist=["Message"]).Message("user", "x" * 2000))
    ctx = app.agent.build_context(cid)
    assert len(ctx) == 1 and ctx[0].content == "x" * 2000  # newest always kept


def test_events_published_on_bus(app, fake_ollama):
    seen = []
    app.bus.subscribe("*", lambda e: seen.append(e.name))
    fake_ollama.turns = [text_turn("ok")]
    run(app, app.conversations.create(), "hi")
    assert seen[:3] == ["state.changed", "task.started", "agent.text"] and "task.finished" in seen


def test_ask_when_not_ready(settings, paths):
    a = JarvisApp(settings, paths)
    with pytest.raises(JarvisError):
        list(a.ask(1, "x"))
    a.close()


def test_llm_error_reported_as_event(app, fake_ollama):
    app.model = app.agent.model = "deleted:model"
    events = run(app, app.conversations.create(), "hi")
    assert events[-1].kind == "error" and "not found" in events[-1].text
