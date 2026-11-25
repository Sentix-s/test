"""Tool definition, JSON-schema generation and the registry.

A tool is a plain Python function decorated with :func:`tool`::

    @tool(risk=RiskLevel.SAFE)
    def get_datetime(ctx: ToolContext, timezone: Annotated[str, "IANA zone, e.g. Europe/Moscow"] = "") -> str:
        \"\"\"Return the current date and time.\"\"\"

The parameter schema sent to the model is derived from the signature:
type hints give the JSON type, ``Annotated[..., "text"]`` the description,
parameters without defaults are ``required``. A first parameter named
``ctx`` receives a :class:`ToolContext` and is hidden from the model.
"""

from __future__ import annotations

import enum
import inspect
import json
import logging
import time
import types
import typing
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Literal, get_args, get_origin, get_type_hints

from jarvis.core.cancellation import CancelToken
from jarvis.core.errors import TaskCancelled, ToolError

log = logging.getLogger(__name__)


class RiskLevel(str, enum.Enum):
    SAFE = "safe"            # read-only, no side effects -> run automatically
    MODERATE = "moderate"    # reversible changes (create file, open app) -> automatic, logged
    DANGEROUS = "dangerous"  # delete, send, install, run unknown exe, system settings -> confirm


@dataclass
class ToolContext:
    """Runtime services available to a tool call."""

    cancel: CancelToken = field(default_factory=CancelToken)
    services: dict[str, Any] = field(default_factory=dict)

    def service(self, name: str) -> Any:
        try:
            return self.services[name]
        except KeyError as exc:
            raise ToolError(f"Service '{name}' is not available") from exc


@dataclass
class ToolResult:
    ok: bool
    output: str
    duration_ms: int = 0

    def for_model(self) -> str:
        return self.output if self.ok else f"ERROR: {self.output}"


_PRIMITIVES: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _json_type(tp: Any) -> dict[str, Any]:
    origin = get_origin(tp)
    if origin is Annotated:
        return _json_type(get_args(tp)[0])
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return _json_type(args[0])
        return {"anyOf": [_json_type(a) for a in args]}
    if origin is Literal:
        values = list(get_args(tp))
        schema = _json_type(type(values[0]))
        schema["enum"] = values
        return schema
    if origin in (list, tuple, set):
        args = get_args(tp)
        return {"type": "array", "items": _json_type(args[0]) if args else {}}
    if origin is dict or tp is dict:
        return {"type": "object"}
    if tp is list:
        return {"type": "array"}
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return {"type": "string", "enum": [m.value for m in tp]}
    if tp in _PRIMITIVES:
        return {"type": _PRIMITIVES[tp]}
    if tp is Any or tp is inspect.Parameter.empty:
        return {}
    raise TypeError(f"Unsupported tool parameter type: {tp!r}")


def _describe(tp: Any) -> str | None:
    if get_origin(tp) is Annotated:
        for meta in get_args(tp)[1:]:
            if isinstance(meta, str):
                return meta
    return None


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    parameters: dict[str, Any]
    risk: RiskLevel = RiskLevel.SAFE
    category: str = "general"
    wants_context: bool = False

    def schema(self) -> dict[str, Any]:
        """Ollama/OpenAI-compatible function schema."""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    def describe_call(self, arguments: dict[str, Any]) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        return f"{self.name}({args})"

    @classmethod
    def from_function(
        cls,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        risk: RiskLevel = RiskLevel.SAFE,
        category: str = "general",
    ) -> "Tool":
        sig = inspect.signature(func)
        hints = get_type_hints(func, include_extras=True)
        props: dict[str, Any] = {}
        required: list[str] = []
        wants_context = False
        for idx, (pname, param) in enumerate(sig.parameters.items()):
            if idx == 0 and pname == "ctx":
                wants_context = True
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                raise TypeError(f"Tool {func.__name__}: *args/**kwargs are not supported")
            tp = hints.get(pname, Any)
            prop = _json_type(tp)
            desc = _describe(tp)
            if desc:
                prop["description"] = desc
            if param.default is param.empty:
                required.append(pname)
            elif param.default is not None:
                default = param.default.value if isinstance(param.default, enum.Enum) else param.default
                prop["default"] = default
            props[pname] = prop
        params: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            params["required"] = required
        doc = description or inspect.getdoc(func) or ""
        return cls(
            name=name or func.__name__,
            description=doc.strip(),
            func=func,
            parameters=params,
            risk=risk,
            category=category,
            wants_context=wants_context,
        )


def tool(
    _func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    risk: RiskLevel = RiskLevel.SAFE,
    category: str = "general",
) -> Any:
    """Decorator that attaches a :class:`Tool` to a function as ``__jarvis_tool__``."""

    def wrap(func: Callable[..., Any]) -> Callable[..., Any]:
        func.__jarvis_tool__ = Tool.from_function(  # type: ignore[attr-defined]
            func, name=name, description=description, risk=risk, category=category
        )
        return func

    return wrap(_func) if _func is not None else wrap


def _coerce(value: Any, schema: dict[str, Any]) -> Any:
    """Small, forgiving coercion: 7B models sometimes send "5" for an integer."""
    t = schema.get("type")
    try:
        if t == "integer" and isinstance(value, (str, float)) and not isinstance(value, bool):
            return int(float(value))
        if t == "number" and isinstance(value, str):
            return float(value)
        if t == "boolean" and isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "да")
        if t == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if t == "array" and isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
    except (ValueError, json.JSONDecodeError):
        pass
    return value


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, item: Tool | Callable[..., Any]) -> Tool:
        t = item if isinstance(item, Tool) else getattr(item, "__jarvis_tool__", None)
        if t is None:
            t = Tool.from_function(item)  # type: ignore[arg-type]
        if t.name in self._tools:
            raise ValueError(f"Tool '{t.name}' is already registered")
        self._tools[t.name] = t
        return t

    def register_module(self, module: types.ModuleType) -> list[Tool]:
        """Register every ``@tool``-decorated function in a module."""
        found = [obj.__jarvis_tool__ for obj in vars(module).values() if hasattr(obj, "__jarvis_tool__")]
        for t in found:
            self.register(t)
        return found

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def all(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda t: (t.category, t.name))

    def schemas(self, exclude: set[str] | None = None) -> list[dict[str, Any]]:
        return [t.schema() for t in self.all() if not exclude or t.name not in exclude]

    def validate_arguments(self, t: Tool, arguments: dict[str, Any]) -> dict[str, Any]:
        props: dict[str, Any] = t.parameters.get("properties", {})
        clean: dict[str, Any] = {}
        unknown = [k for k in arguments if k not in props]
        if unknown:
            log.warning("Tool %s: ignoring unknown arguments %s", t.name, unknown)
        for key, schema in props.items():
            if key in arguments:
                value = _coerce(arguments[key], schema)
                if "enum" in schema and value not in schema["enum"]:
                    raise ToolError(f"Argument '{key}' must be one of {schema['enum']}, got {value!r}")
                clean[key] = value
        missing = [k for k in t.parameters.get("required", []) if k not in clean]
        if missing:
            raise ToolError(f"Missing required argument(s): {', '.join(missing)}")
        return clean

    def execute(self, name: str, arguments: dict[str, Any], ctx: ToolContext | None = None) -> ToolResult:
        """Run a tool. Never raises for tool failures: the error goes back to the model."""
        start = time.perf_counter()
        t = self._tools.get(name)
        if t is None:
            return ToolResult(False, f"Unknown tool '{name}'. Available: {', '.join(sorted(self._tools))}")
        ctx = ctx or ToolContext()
        try:
            clean = self.validate_arguments(t, arguments)
            ctx.cancel.raise_if_cancelled()
            value = t.func(ctx, **clean) if t.wants_context else t.func(**clean)
            output = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
            ok = True
        except TaskCancelled:
            raise
        except ToolError as exc:
            output, ok = str(exc), False
        except Exception as exc:  # noqa: BLE001 - report any tool failure to the model
            log.exception("Tool %s crashed", name)
            output, ok = f"{type(exc).__name__}: {exc}", False
        return ToolResult(ok, output, int((time.perf_counter() - start) * 1000))
