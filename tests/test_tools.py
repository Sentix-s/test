from typing import Annotated, Literal

import pytest

from jarvis.core.cancellation import CancelToken
from jarvis.core.errors import TaskCancelled, ToolError
from jarvis.tools.base import RiskLevel, Tool, ToolContext, ToolRegistry, tool
from jarvis.tools.builtin import register_builtin_tools
from jarvis.tools.builtin.system import calculate, get_datetime, get_system_info


def test_schema_from_signature():
    @tool(risk=RiskLevel.MODERATE, category="files")
    def move(ctx: ToolContext, src: Annotated[str, "source path"], count: int = 1,
             mode: Literal["copy", "move"] = "move", tags: list[str] | None = None) -> str:
        """Move a file."""
        return "ok"

    t: Tool = move.__jarvis_tool__
    assert t.wants_context and t.risk is RiskLevel.MODERATE and t.description == "Move a file."
    params = t.schema()["function"]["parameters"]
    assert params["required"] == ["src"]
    assert params["properties"]["src"] == {"type": "string", "description": "source path"}
    assert params["properties"]["count"] == {"type": "integer", "default": 1}
    assert params["properties"]["mode"] == {"type": "string", "enum": ["copy", "move"], "default": "move"}
    assert params["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
    assert "ctx" not in params["properties"]


def test_registry_execute_validation_and_coercion():
    reg = ToolRegistry()

    @tool
    def add(a: int, b: int = 2) -> int:
        """Add."""
        return a + b

    reg.register(add)
    assert reg.execute("add", {"a": "40"}).output == "42"
    missing = reg.execute("add", {})
    assert not missing.ok and "Missing required" in missing.output
    unknown = reg.execute("nope", {})
    assert not unknown.ok and "Unknown tool" in unknown.output
    with pytest.raises(ValueError):
        reg.register(add)


def test_tool_crash_is_reported_not_raised():
    reg = ToolRegistry()

    @tool
    def boom() -> str:
        """Fails."""
        raise RuntimeError("kaput")

    reg.register(boom)
    r = reg.execute("boom", {})
    assert not r.ok and "kaput" in r.output and r.for_model().startswith("ERROR:")


def test_cancelled_context_aborts_tool():
    reg = ToolRegistry()
    reg.register(Tool.from_function(lambda: "x", name="x"))
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(TaskCancelled):
        reg.execute("x", {}, ToolContext(cancel=tok))


@pytest.mark.parametrize("expr,expected", [
    ("2+2*2", "6"), ("(17.5*3)^2", "2756.25"), ("sqrt(16)", "4"), ("10/4", "2.5"),
    ("2,5*2", "5"), ("factorial(5)", "120"), ("-3**2", "-9"), ("round(pi, 2)", "3.14"), ("max(1,2)", "2"),
])
def test_calculate(expr, expected):
    assert calculate(expr) == expected


@pytest.mark.parametrize("expr", ["__import__('os').system('x')", "open('f')", "1/0", "2**99999", "a+1", "().__class__"])
def test_calculate_rejects_unsafe_or_invalid(expr):
    with pytest.raises(ToolError):
        calculate(expr)


def test_datetime_and_system_info():
    d = get_datetime()
    assert d["weekday_ru"] and len(d["date"]) == 10
    assert get_datetime("Europe/Moscow")["utc_offset"] == "+0300"
    with pytest.raises(ToolError):
        get_datetime("Mars/Olympus")
    info = get_system_info()
    assert info["cpu_threads"] >= 1 and info["ram_total_gb"] > 0 and isinstance(info["gpus"], list)


def test_builtin_registration():
    reg = register_builtin_tools(ToolRegistry())
    names = {t.name for t in reg.all()}
    assert {"get_datetime", "get_system_info", "calculate", "remember_fact", "recall_facts", "forget_fact"} <= names
    for schema in reg.schemas():
        assert schema["type"] == "function" and schema["function"]["description"]
