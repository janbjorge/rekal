"""The MCP tool surface: recall (build_context), store, delete."""

from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from rekal.models import CompactContext


def resolve_project(ctx: Context, project: str | None) -> str | None:
    """Return explicit project if given, otherwise fall back to session default."""
    if project is not None:
        return project
    return ctx.request_context.lifespan_context.default_project


async def memory_build_context(
    ctx: Context,
    query: Annotated[str, Field(description="Query to recall memories for")],
    project: Annotated[str | None, Field(description="Filter to this project")] = None,
    limit: Annotated[int, Field(description="Max memories to include")] = 10,
    min_score: Annotated[
        float,
        Field(description="Drop results scoring below this relevance floor (0.0-1.0)."),
    ] = 0.25,
) -> CompactContext:
    """Recall memories relevant to a query via hybrid FTS + vector + recency search."""
    db = ctx.request_context.lifespan_context.db
    if db is None:
        return CompactContext(query=query, memories=[])
    resolved_project = resolve_project(ctx, project)
    result = await db.build_context(
        query,
        project=resolved_project,
        limit=limit,
        weights=ctx.request_context.lifespan_context.weights,
        min_score=min_score,
    )
    return result.compact()


async def memory_store(
    ctx: Context,
    content: Annotated[
        str,
        Field(
            description=(
                "Distilled, self-contained knowledge: a 1-2 sentence fact, or a "
                "350-500 word subsystem brief (headline + keywords, mechanism, "
                "'Deviations:' section). Code claims must embed inline "
                "(relative/path.py:LINE symbol) anchors verified this session."
            )
        ),
    ],
    project: Annotated[str | None, Field(description="Project scope for this memory")] = None,
    tags: Annotated[
        list[str] | None,
        Field(description='Tags for categorization, as a JSON array e.g. ["auth", "jwt"]'),
    ] = None,
    replaces: Annotated[
        str | None,
        Field(description="ID of an existing memory this supersedes (updates in place)"),
    ] = None,
) -> str:
    """Store a durable memory. Pass ``replaces`` to update an existing one."""
    db = ctx.request_context.lifespan_context.db
    if db is None:
        return "Memory database unavailable; nothing stored."
    resolved_project = resolve_project(ctx, project)
    if replaces is not None:
        new_id = await db.replace(replaces, content, project=resolved_project, tags=tags)
        return f"Stored memory {new_id} (replaces {replaces})"
    memory_id = await db.store(content, project=resolved_project, tags=tags)
    return f"Stored memory {memory_id}"


async def memory_delete(
    ctx: Context,
    memory_id: Annotated[str, Field(description="ID of the memory to delete")],
) -> str:
    """Delete a memory by ID."""
    db = ctx.request_context.lifespan_context.db
    if db is None:
        return "Memory database unavailable; nothing deleted."
    deleted = await db.delete(memory_id)
    if deleted:
        return f"Deleted memory {memory_id}"
    return f"Memory {memory_id} not found"


def register(mcp: FastMCP, *, readonly: bool) -> None:
    """Attach the tool surface to a server; readonly exposes recall only."""
    mcp.tool()(memory_build_context)
    if readonly:
        return
    mcp.tool()(memory_store)
    mcp.tool()(memory_delete)
