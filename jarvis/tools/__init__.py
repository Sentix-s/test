"""Tool architecture: the LLM picks tools from a registry instead of
hard-coded voice commands. Each tool declares its risk level so the
Security layer can decide whether confirmation is required."""

from jarvis.tools.base import RiskLevel, Tool, ToolContext, ToolRegistry, ToolResult, tool

__all__ = ["RiskLevel", "Tool", "ToolContext", "ToolRegistry", "ToolResult", "tool"]
