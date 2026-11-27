import threading

import pytest

from fake_ollama import FakeOllama, text_turn, tool_turn
from jarvis.ai.ollama_client import OllamaClient, _model_matches
from jarvis.ai.stream_filter import StreamFilter, parse_text_tool_calls, strip_markup
from jarvis.ai.types import Message
from jarvis.core.cancellation import CancelToken
from jarvis.core.errors import LLMUnavailableError, ModelNotFoundError, TaskCancelled


def test_model_name_matching():
    assert _model_matches("qwen3", "qwen3:latest")
    assert _model_matches("qwen3:8b", "qwen3:8b")
    assert not _model_matches("qwen3:8b", "qwen3:14b")


def test_client_queries_and_lifecycle(fake_ollama):
    with OllamaClient(fake_ollama.url) as c:
        assert c.version() == "0.12.0" and c.is_available()
        assert c.has_model("qwen3:8b") and not c.has_model("llama3")
        c.load("qwen3:8b")
        assert [m["name"] for m in c.loaded_models()] == ["qwen3:8b"]
        c.unload("qwen3:8b")
        assert c.loaded_models() == []
        with pytest.raises(ModelNotFoundError):
            c.load("missing:1b")
        seen = []
        c.pull("tiny:1b", seen.append)
        assert seen[-1]["status"] == "success" and c.has_model("tiny:1b")


def test_unavailable_server():
    c = OllamaClient("http://127.0.0.1:9", connect_timeout=0.5)
    assert not c.is_available()
    with pytest.raises(LLMUnavailableError):
        list(c.chat_stream("m", [Message("user", "hi")]))


def test_chat_stream_text_and_tools(fake_ollama):
    fake_ollama.turns = [text_turn("При", "вет"), tool_turn("calculate", {"expression": "2+2"})]
    with OllamaClient(fake_ollama.url) as c:
        r = c.chat("qwen3:8b", [Message("user", "hi")], options={"temperature": 0.1}, keep_alive="5m", think=False)
        assert r.content == "Привет" and r.stats["eval_count"] == 42
        r2 = c.chat("qwen3:8b", [Message("user", "2+2")], tools=[{"type": "function"}])
        assert r2.tool_calls[0].name == "calculate" and r2.tool_calls[0].arguments == {"expression": "2+2"}
    body = fake_ollama.chat_requests()[0]
    assert body["stream"] is True and body["think"] is False and body["keep_alive"] == "5m"
    assert body["options"] == {"temperature": 0.1} and body["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_cancel_mid_stream():
    with FakeOllama(chunk_delay=0.05) as fake:
        fake.turns = [text_turn(*["слово "] * 200)]
        tok = CancelToken()
        got = []
        with OllamaClient(fake.url) as c:
            with pytest.raises(TaskCancelled):
                for chunk in c.chat_stream("qwen3:8b", [Message("user", "x")], cancel=tok):
                    got.append(chunk.content)
                    if len(got) == 3:
                        threading.Timer(0.01, tok.cancel, args=("stop",)).start()
        assert 3 <= len(got) < 50


@pytest.mark.parametrize("chunks,expected", [
    (["Hello ", "world"], "Hello world"),
    (["<thi", "nk>secret</th", "ink>\nAnswer"], "Answer"),
    (["Answer <", "tool_call>{\"name\":\"x\"}</tool_call>"], "Answer "),
    (["a < b and <", "b> tag"], "a < b and <b> tag"),
])
def test_stream_filter(chunks, expected):
    f = StreamFilter()
    out = "".join(f.feed(c) for c in chunks) + f.flush()
    assert out == expected


def test_parse_text_tool_calls():
    text = 'Сейчас.\n<tool_call>\n{"name": "calculate", "arguments": {"expression": "1+1"}}\n</tool_call>'
    calls = parse_text_tool_calls(text)
    assert calls[0].name == "calculate" and calls[0].arguments == {"expression": "1+1"}
    assert strip_markup(text) == "Сейчас."
    assert parse_text_tool_calls("<tool_call>{broken</tool_call>") == []


def test_connect_timeout_means_unavailable():
    """Windows: a closed localhost port times out instead of refusing."""
    import httpx

    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    c = OllamaClient("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMUnavailableError):
        c.version()
    with pytest.raises(LLMUnavailableError):
        list(c.chat_stream("m", [Message("user", "hi")]))


def test_emoji_split_across_tokens(fake_ollama, tmp_path):
    """Real bug from the user's PC: '🌱' split into two tokens -> 'surrogates not allowed'."""
    from jarvis.ai.types import clean_text
    from jarvis.memory import ConversationStore, Database

    fake_ollama.turns = [text_turn("Сад ", "\ud83c", "\udf31", " — это красиво"), text_turn("ok")]
    with OllamaClient(fake_ollama.url) as c:
        pieces = [ch.content for ch in c.chat_stream("qwen3:8b", [Message("user", "x")])]
        text = "".join(pieces)
        assert text == "Сад 🌱 — это красиво"
        for p in pieces:
            p.encode("utf-8")  # every streamed piece is valid UTF-8
        store = ConversationStore(Database(tmp_path / "db.sqlite"))
        cid = store.create()
        store.add_message(cid, Message("assistant", "lone \ud83c half"))  # must not raise
        assert store.messages(cid)[0].content == "lone � half"
        assert c.chat("qwen3:8b", [Message("user", "x"), Message("assistant", "a\ud83c")]).content == "ok"
    assert clean_text("ok") == "ok" and clean_text("🌱") == "🌱"
