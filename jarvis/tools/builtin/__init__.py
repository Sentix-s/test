"""Built-in tools. ``register_builtin_tools`` is the single place that wires
all tool modules into a registry; later phases add modules here."""

from __future__ import annotations

from jarvis.tools.base import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> ToolRegistry:
    from jarvis.tools.builtin import memory_tools, pc_tools, system

    registry.register_module(system)
    registry.register_module(memory_tools)
    registry.register_module(pc_tools)
    return registry
