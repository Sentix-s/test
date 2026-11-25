"""Read-only system tools: time, machine status, calculator."""

from __future__ import annotations

import ast
import datetime as _dt
import math
import operator
import platform
import re
import shutil
import subprocess
import sys
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psutil

from jarvis.core.errors import ToolError
from jarvis.tools.base import RiskLevel, tool

_WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


@tool(risk=RiskLevel.SAFE, category="system")
def get_datetime(timezone: Annotated[str, "IANA time zone, e.g. 'Europe/Moscow'. Empty = local time."] = "") -> dict[str, Any]:
    """Get the current date, time and weekday. Use it for any question about 'now', 'today', dates or time."""
    if timezone:
        try:
            now = _dt.datetime.now(ZoneInfo(timezone))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ToolError(f"Unknown time zone '{timezone}'") from exc
    else:
        now = _dt.datetime.now().astimezone()
    return {
        "iso": now.isoformat(timespec="seconds"),
        "date": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M:%S"),
        "weekday_ru": _WEEKDAYS_RU[now.weekday()],
        "weekday_en": now.strftime("%A"),
        "utc_offset": now.strftime("%z"),
    }


def _gpu_info() -> list[dict[str, Any]]:
    """Query NVIDIA GPUs via nvidia-smi (installed with the driver). Phase 7 switches to NVML."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=flags, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue

        def num(s: str) -> float | None:
            try:
                return float(s)
            except ValueError:
                return None

        gpus.append({"name": parts[0], "utilization_percent": num(parts[1]), "vram_used_mb": num(parts[2]),
                     "vram_total_mb": num(parts[3]), "temperature_c": num(parts[4])})
    return gpus


@tool(risk=RiskLevel.SAFE, category="system")
def get_system_info() -> dict[str, Any]:
    """Get the current state of the computer: OS, CPU load, RAM, disk space, GPU/VRAM usage and temperature, battery."""
    vm = psutil.virtual_memory()
    disks = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            du = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({"mount": part.mountpoint, "total_gb": round(du.total / 2**30, 1),
                      "free_gb": round(du.free / 2**30, 1), "used_percent": du.percent})
    info: dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_cores_physical": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(logical=True),
        "cpu_load_percent": psutil.cpu_percent(interval=0.3),
        "ram_total_gb": round(vm.total / 2**30, 1),
        "ram_used_percent": vm.percent,
        "ram_available_gb": round(vm.available / 2**30, 1),
        "disks": disks,
        "gpus": _gpu_info(),
        "python": sys.version.split()[0],
        "uptime_hours": round((_dt.datetime.now().timestamp() - psutil.boot_time()) / 3600, 1),
    }
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if battery is not None:
        info["battery"] = {"percent": battery.percent, "plugged_in": battery.power_plugged}
    return info


_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS: dict[str, Any] = {name: getattr(math, name) for name in (
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10", "log2", "exp", "floor", "ceil",
    "factorial", "radians", "degrees", "hypot")}
_FUNCS.update({"abs": abs, "round": round, "min": min, "max": max})
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _eval(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 10_000:
            raise ToolError("Exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0] > 1000:
            raise ToolError("factorial argument too large")
        return _FUNCS[node.func.id](*args)
    raise ToolError(f"Unsupported expression element: {ast.dump(node)[:60]}")


@tool(risk=RiskLevel.SAFE, category="system")
def calculate(expression: Annotated[str, "Arithmetic expression, e.g. '(17.5*3)^2 / sqrt(2)'. Supports + - * / // % ^ ** and math functions."]) -> str:
    """Evaluate a math expression exactly. Always use this instead of computing numbers in your head."""
    expr = expression.replace("^", "**").replace("×", "*").replace("÷", "/").strip()
    if "(" not in expr:  # Russian decimal comma ("2,5*2"); inside calls a comma separates arguments
        expr = re.sub(r"(?<=\d),(?=\d)", ".", expr)
    if len(expr) > 300:
        raise ToolError("Expression too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"Invalid expression: {expression}") from exc
    try:
        value = _eval(tree)
    except ZeroDivisionError as exc:
        raise ToolError("Division by zero") from exc
    except (OverflowError, ValueError) as exc:
        raise ToolError(f"Math error: {exc}") from exc
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return str(value)
