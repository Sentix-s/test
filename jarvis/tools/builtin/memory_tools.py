"""Long-term memory tools backed by the ``facts`` table."""

from __future__ import annotations

from typing import Annotated, Any

from jarvis.tools.base import RiskLevel, ToolContext, tool


@tool(risk=RiskLevel.SAFE, category="memory")
def remember_fact(
    ctx: ToolContext,
    fact: Annotated[str, "A short self-contained statement to remember, e.g. 'The user's name is Alex'."],
    category: Annotated[str, "Category: personal, preference, work, contact, general"] = "general",
) -> str:
    """Save an important fact about the user or their preferences to long-term memory. Use when the user asks you to remember something or shares a lasting personal preference."""
    fact_id = ctx.service("facts").add(fact, category)
    return f"Saved to memory (id={fact_id})."


@tool(risk=RiskLevel.SAFE, category="memory")
def recall_facts(
    ctx: ToolContext,
    query: Annotated[str, "Keywords to search for. Empty = most recent facts."] = "",
) -> list[dict[str, Any]] | str:
    """Search long-term memory for facts previously saved about the user."""
    facts = ctx.service("facts").search(query)
    if not facts:
        return "No matching facts in memory."
    return [{"id": f.id, "fact": f.content, "category": f.category, "saved": f.created_at[:10]} for f in facts]


@tool(risk=RiskLevel.MODERATE, category="memory")
def forget_fact(ctx: ToolContext, fact_id: Annotated[int, "id of the fact from recall_facts"]) -> str:
    """Delete one fact from long-term memory by its id."""
    return "Deleted." if ctx.service("facts").delete(fact_id) else f"No fact with id={fact_id}."
