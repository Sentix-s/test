from __future__ import annotations

from pathlib import Path

import pytest

from fake_ollama import FakeOllama
from jarvis.ai.ollama_client import OllamaClient
from jarvis.core.app import JarvisApp
from jarvis.settings import AppPaths, Settings


@pytest.fixture
def fake_ollama():
    with FakeOllama() as fake:
        yield fake


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs").ensure()


@pytest.fixture
def settings(fake_ollama: FakeOllama) -> Settings:
    s = Settings()
    s.ai.host = fake_ollama.url
    return s


@pytest.fixture
def app(settings: Settings, paths: AppPaths):
    a = JarvisApp(settings, paths, llm=OllamaClient(settings.ai.host, 10, 2))
    a.start()
    yield a
    a.close()
