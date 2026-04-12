"""Introspection tools: similar, topics, timeline, related, health, conflicts."""

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from rekal.adapters.mcp_adapter import mcp, resolve_project


@mcp.tool()
async def memory_similar(
    ctx: Context,
    memory_id: Annotated[str, Field(description="ID of the memory to find similar matches for")],
    limit: Annotated[int, Field(description="Maximum number of similar memories")] = 5,
) -> list[dict[str, str | int | float | list[str] | None]]:
    """Find memories similar to a given memory (by vector similarity)."""
    db = ctx.request_context.lifespan_context.db
    results = await db.memory_similar(memory_id, limit=limit)
    return [r.model_dump() for r in results]


@mcp.tool()
async def memory_topics(
    ctx: Context,
    project: Annotated[str | None, Field(description="Filter to this project")] = None,
) -> list[dict[str, str | int]]:
    """Get a summary of memory topics (grouped by type)."""
    db = ctx.request_context.lifespan_context.db
    results = await db.memory_topics(project=resolve_project(ctx, project))
    return [r.model_dump() for r in results]


@mcp.tool()
async def memory_timeline(
    ctx: Context,
    project: Annotated[str | None, Field(description="Filter to this project")] = None,
    start: Annotated[str | None, Field(description="Start date (YYYY-MM-DD HH:MM:SS)")] = None,
    end: Annotated[str | None, Field(description="End date (YYYY-MM-DD HH:MM:SS)")] = None,
    limit: Annotated[int, Field(description="Maximum number of memories")] = 20,
) -> list[dict[str, str | int | float | list[str] | None]]:
    """Get memories ordered by creation time, optionally filtered by date range."""
    db = ctx.request_context.lifespan_context.db
    results = await db.memory_timeline(
        project=resolve_project(ctx, project), start=start, end=end, limit=limit
    )
    return [r.model_dump() for r in results]


@mcp.tool()
async def memory_related(
    ctx: Context,
    memory_id: Annotated[str, Field(description="ID of the memory to find links for")],
) -> list[dict[str, str]]:
    """Find all memories linked to a given memory (supersedes, contradicts, related_to)."""
    db = ctx.request_context.lifespan_context.db
    return await db.memory_related(memory_id)


@mcp.tool()
async def memory_health(
    ctx: Context,
) -> dict[str, int | str | dict[str, int] | None]:
    """Get a health report of the memory database."""
    db = ctx.request_context.lifespan_context.db
    report = await db.memory_health()
    return report.model_dump()


@mcp.tool()
async def memory_conflicts(
    ctx: Context,
    project: Annotated[str | None, Field(description="Filter conflicts to this project")] = None,
) -> list[dict[str, str]]:
    """Find conflicting memories."""
    db = ctx.request_context.lifespan_context.db
    results = await db.memory_conflicts(project=resolve_project(ctx, project))
    return [r.model_dump() for r in results]
