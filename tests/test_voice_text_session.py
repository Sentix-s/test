import pytest

from jarvis.settings.schema import VoiceSettings
from jarvis.voice.session import VoiceSession, VoiceState
from jarvis.voice.text import (SentenceSplitter, clean_for_speech, detect_language, find_wake_word,
                               is_noise_transcript, is_stop_phrase, normalize)

WAKE = ["джарвис", "jarvis"]


@pytest.mark.parametrize("text,remainder", [
    ("Джарвис!", ""),
    ("Джарвис, открой Word и напиши эссе.", "открой Word и напиши эссе."),
    ("Эй, Джарвис, который час?", "который час?"),
    ("Jarvis, what time is it", "what time is it"),
    ("Жарвис", ""),
    ("Джар вис, привет", "привет"),
])
def test_wake_word_found(text, remainder):
    m = find_wake_word(text, WAKE)
    assert m is not None and m.remainder == remainder


@pytest.mark.parametrize("text", ["Чарвис, привет", "Джервис, привет", "Jarvis привет", "Жарвиз, привет",
                                  "Жарвец, привет", "Дарвин, привет"])
def test_wake_word_sound_alikes(text):
    m = find_wake_word(text, WAKE)
    if text.startswith("Дарвин"):
        assert m is None  # a different name must not wake
    else:
        assert m is not None and m.remainder == "привет"


@pytest.mark.parametrize("text", ["я вчера смотрел про джарвиса фильм", "Привет, как дела", "", "Чарльз",
                                  "Давай поиграем", "Жара невыносимая", "Чарли, иди сюда"])
def test_wake_word_not_found(text):
    assert find_wake_word(text, WAKE) is None


def test_custom_wake_word():
    assert find_wake_word("Пятница, включи музыку", ["пятница"]).remainder == "включи музыку"
    assert find_wake_word("Джарвис, привет", ["пятница"]) is None


@pytest.mark.parametrize("text,expected", [
    ("Всё, спасибо!", True), ("все спасибо", True), ("Всё, спасибо большое", True), ("Отбой.", True),
    ("Всё, спасибо, а теперь открой ворд и напиши письмо", False), ("Спасибо, открой браузер", False),
])
def test_stop_phrases(text, expected):
    assert is_stop_phrase(text, VoiceSettings().stop_phrases) is expected


def test_helpers():
    assert normalize("  Ёлка,  ПРИВЕТ!! ") == "елка привет"
    assert is_noise_transcript("Продолжение следует...") and is_noise_transcript("") and not is_noise_transcript("открой ворд")
    assert detect_language("My hobbies are football and chess.") == "en"
    assert detect_language("Мои хобби — футбол") == "ru"
    assert clean_for_speech("**Итог:** см. [док](http://x.y) и `код`\n- пункт") == "Итог: см. док и код пункт"


def test_sentence_splitter_streaming():
    s = SentenceSplitter()
    out = []
    for piece in ["Конечно, сэр. Число пи равно 3.", "14159. Это ", "иррациональное число!", " Ещё вопросы?"]:
        out += s.feed(piece)
    assert out == ["Конечно, сэр.", "Число пи равно 3.14159.", "Это иррациональное число!"]
    s2 = SentenceSplitter()
    assert s2.feed("Т. е. всё ок. ") == ["Т. е. всё ок."]  # abbreviation not split off
    assert s.flush() == ["Ещё вопросы?"]


# ---------------------------------------------------------------- session

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeSTT:
    """Audio objects in these tests are plain strings: 'transcribing' returns them."""

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, max_seconds=None, language=None):
        self.calls += 1
        return audio


class FakeWake:
    def check(self, audio):
        return find_wake_word(audio, WAKE)


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)

    def wait_done(self):
        pass

    def stop(self):
        pass


def make_session(**voice_overrides):
    settings = VoiceSettings(**voice_overrides)
    clock, speaker, events, asked = Clock(), FakeSpeaker(), [], []

    def responder(text):
        asked.append(text)
        yield "Готово, сэр. "
        yield f"Вы сказали: {text}."

    s = VoiceSession(settings, FakeWake(), FakeSTT(), speaker, responder, clock=clock,
                     listener=lambda n, d: events.append((n, d)))
    s.start()
    return s, clock, speaker, events, asked


def states(events):
    return [d["state"] for n, d in events if n == "state"]


def test_sleep_ignores_speech_without_wake_word():
    s, _c, speaker, _e, asked = make_session()
    s.on_utterance("Какая сегодня погода?")
    assert s.state is VoiceState.SLEEP and asked == [] and speaker.said == []


def test_full_cycle_with_idle_timeout():
    s, clock, speaker, events, asked = make_session(idle_timeout_seconds=15)
    s.on_utterance("Джарвис")
    assert speaker.said == ["Слушаю вас"] and s.state is VoiceState.ACTIVE
    s.on_utterance("открой блокнот")
    assert asked == ["открой блокнот"]
    assert speaker.said[1:] == ["Готово, сэр.", "Вы сказали: открой блокнот."]
    assert s.state is VoiceState.ACTIVE and s.deadline == clock.t + 15  # timer starts after speaking
    # several commands in a row without the wake word
    clock.t += 10
    s.tick()
    assert s.state is VoiceState.ACTIVE
    s.on_utterance("а теперь калькулятор")
    assert asked[-1] == "а теперь калькулятор" and s.deadline == clock.t + 15  # timer restarted
    # the user starts talking just before the timeout: no sleep while they speak
    clock.t += 14
    s.on_speech_start()
    clock.t += 5
    s.tick(user_speaking=True)
    s.tick()
    assert s.state is VoiceState.ACTIVE
    s.on_utterance("Продолжение следует...")  # noise transcript -> timer re-armed
    assert s.state is VoiceState.ACTIVE and s.deadline == clock.t + 15
    clock.t += 15
    s.tick()
    assert s.state is VoiceState.SLEEP and speaker.said[-1] == "Обращайтесь, сэр."
    assert states(events) == ["sleep", "speaking", "active", "processing", "speaking", "active", "processing",
                              "speaking", "active", "processing", "active", "speaking", "sleep"]


def test_wake_and_command_in_one_phrase():
    s, _c, speaker, _e, asked = make_session()
    s.on_utterance("Джарвис, открой Word и напиши эссе про хобби")
    assert asked == ["открой Word и напиши эссе про хобби"]
    assert "Слушаю вас" not in speaker.said and s.state is VoiceState.ACTIVE


def test_stop_phrase_ends_session_immediately():
    s, _c, speaker, _e, asked = make_session()
    s.on_utterance("Джарвис")
    s.on_utterance("Всё, спасибо!")
    assert s.state is VoiceState.SLEEP and asked == [] and speaker.said[-1] == "Обращайтесь, сэр."
    assert s.deadline is None


def test_wake_word_inside_active_session_is_stripped():
    s, _c, speaker, _e, asked = make_session()
    s.on_utterance("Джарвис")
    s.on_utterance("Джарвис, который час")
    assert asked == ["который час"]
    s.on_utterance("Джарвис")
    assert speaker.said[-1] == "Слушаю вас" and s.state is VoiceState.ACTIVE


@pytest.mark.parametrize("timeout", [5, 10, 15, 20, 30, 60])
def test_configurable_timeouts(timeout):
    s, clock, speaker, _e, _a = make_session(idle_timeout_seconds=timeout, farewell_on_timeout=False)
    s.on_utterance("Джарвис")
    clock.t += timeout - 0.1
    s.tick()
    assert s.state is VoiceState.ACTIVE
    clock.t += 0.2
    s.tick()
    assert s.state is VoiceState.SLEEP and speaker.said == ["Слушаю вас"]  # no farewell when disabled


def test_invalid_timeout_rejected():
    with pytest.raises(ValueError):
        VoiceSettings(idle_timeout_seconds=7)
    with pytest.raises(ValueError):
        VoiceSettings(wake_words=["  "])


def test_manual_activation_and_typed_command():
    s, _c, speaker, _e, asked = make_session()
    s.activate()
    assert speaker.said == ["Слушаю вас"] and s.state is VoiceState.ACTIVE
    s.deactivate(farewell=False)
    s.submit_text("сколько времени")
    assert asked == ["сколько времени"] and s.state is VoiceState.ACTIVE


def test_utterances_ignored_while_off():
    s, _c, speaker, _e, asked = make_session()
    s.stop()
    s.on_utterance("Джарвис, привет")
    assert asked == [] and s.state is VoiceState.OFF


def test_cuda_dll_extraction(tmp_path):
    import zipfile

    from jarvis.voice import cuda_libs

    wheel = tmp_path / "nvidia_cudnn_cu12-9.0-py3-none-win_amd64.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("nvidia/cudnn/bin/cudnn64_9.dll", b"x")
        z.writestr("nvidia/cudnn/bin/cudnn_ops64_9.dll", b"y")
        z.writestr("nvidia/cudnn/include/cudnn.h", b"h")
        z.writestr("nvidia_cudnn_cu12-9.0.dist-info/RECORD", b"")
    assert sorted(cuda_libs.extract_dlls(wheel, tmp_path / "cuda")) == ["cudnn64_9.dll", "cudnn_ops64_9.dll"]
    assert not cuda_libs.installed(tmp_path)  # cuBLAS still missing
    (tmp_path / "cuda" / "cublas64_12.dll").write_bytes(b"z")
    assert cuda_libs.installed(tmp_path)


def test_cuda_wheel_lookup_on_pypi():
    """Real PyPI metadata: Windows wheels for both packages must exist."""
    import httpx

    from jarvis.voice import cuda_libs

    try:
        for pkg, (prefix, _dll) in cuda_libs.PACKAGES.items():
            url, size = cuda_libs._wheel_url(pkg, prefix)
            assert url.endswith("win_amd64.whl") and size > 50_000_000
    except httpx.HTTPError as exc:
        pytest.skip(f"PyPI unreachable: {exc}")
