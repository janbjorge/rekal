"""Core memory tools: store, search, delete, update."""

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from rekal.adapters.mcp_adapter import mcp, resolve_project
from rekal.models import MemoryType
from rekal.scoring import ScoringWeights


@mcp.tool()
async def memory_set_project(
    ctx: Context,
    project: Annotated[
        str, Field(description="Project name to scope all subsequent operations to")
    ],
) -> str:
    """Set the default project for this session. Tools use this unless overridden."""
    ctx.request_context.lifespan_context.default_project = project
    return f"Default project set to '{project}'"


@mcp.tool()
async def memory_store(
    ctx: Context,
    content: Annotated[str, Field(description="The text content to store as a memory")],
    memory_type: Annotated[
        MemoryType, Field(description="Category: fact, preference, procedure, context, episode")
    ] = "fact",
    project: Annotated[str | None, Field(description="Project scope for this memory")] = None,
    conversation_id: Annotated[
        str | None, Field(description="ID of the conversation this memory belongs to")
    ] = None,
    tags: Annotated[list[str] | None, Field(description='Tags for categorization, as a JSON array of strings e.g. ["auth", "jwt"]')] = None,
) -> str:
    """Store a new memory. Returns the memory ID."""
    db = ctx.request_context.lifespan_context.db
    memory_id = await db.store(
        content,
        memory_type=memory_type,
        project=resolve_project(ctx, project),
        conversation_id=conversation_id,
        tags=tags,
    )
    return f"Stored memory {memory_id}"


@mcp.tool()
async def memory_search(
    ctx: Context,
    query: Annotated[str, Field(description="Search query (used for both FTS and vector search)")],
    limit: Annotated[int, Field(description="Maximum number of results")] = 10,
    project: Annotated[str | None, Field(description="Filter results to this project")] = None,
    memory_type: Annotated[
        MemoryType | None, Field(description="Filter results to this memory type")
    ] = None,
    conversation_id: Annotated[
        str | None, Field(description="Filter results to this conversation")
    ] = None,
    w_fts: Annotated[
        float | None,
        Field(
            description="Weight for keyword (BM25) relevance, 0.0-1.0. "
            "Default: project config or 0.4"
        ),
    ] = None,
    w_vec: Annotated[
        float | None,
        Field(
            description="Weight for semantic (vector) similarity, 0.0-1.0. "
            "Default: project config or 0.4"
        ),
    ] = None,
    w_recency: Annotated[
        float | None,
        Field(description="Weight for recency decay, 0.0-1.0. Default: project config or 0.2"),
    ] = None,
    half_life: Annotated[
        float | None,
        Field(description="Recency half-life in days. Default: project config or 30.0"),
    ] = None,
) -> list[dict[str, str | int | float | list[str] | None]]:
    """Search memories using hybrid FTS + vector + recency scoring."""
    db = ctx.request_context.lifespan_context.db
    resolved_project = resolve_project(ctx, project)
    file_config = ctx.request_context.lifespan_context.file_config
    weights = await db.resolve_weights(
        resolved_project,
        w_fts=w_fts,
        w_vec=w_vec,
        w_recency=w_recency,
        half_life=half_life,
        file_config=file_config,
    )
    results = await db.search(
        query,
        limit=limit,
        project=resolved_project,
        memory_type=memory_type,
        conversation_id=conversation_id,
        weights=weights,
    )
    return [r.model_dump() for r in results]


@mcp.tool()
async def memory_delete(
    ctx: Context,
    memory_id: Annotated[str, Field(description="ID of the memory to delete")],
) -> str:
    """Delete a memory by ID."""
    db = ctx.request_context.lifespan_context.db
    deleted = await db.delete(memory_id)
    if deleted:
        return f"Deleted memory {memory_id}"
    return f"Memory {memory_id} not found"


@mcp.tool()
async def memory_update(
    ctx: Context,
    memory_id: Annotated[str, Field(description="ID of the memory to update")],
    content: Annotated[
        str | None, Field(description="New text content (re-embeds the memory)")
    ] = None,
    tags: Annotated[list[str] | None, Field(description='New tags (replaces existing), as a JSON array of strings e.g. ["auth", "jwt"]')] = None,
    memory_type: Annotated[MemoryType | None, Field(description="New memory type")] = None,
) -> str:
    """Update an existing memory's content, tags, or type."""
    db = ctx.request_context.lifespan_context.db
    updated = await db.update(memory_id, content=content, tags=tags, memory_type=memory_type)
    if updated:
        return f"Updated memory {memory_id}"
    return f"Memory {memory_id} not found or no changes"


@mcp.tool()
async def memory_set_config(
    ctx: Context,
    key: Annotated[
        str,
        Field(description="Config key: w_fts, w_vec, w_recency, or half_life"),
    ],
    value: Annotated[str, Field(description="Config value (numeric)")],
    project: Annotated[
        str | None,
        Field(description="Project scope (uses session default if not set)"),
    ] = None,
) -> str:
    """Set a per-project config value. Persists in the database across sessions."""
    if key not in ScoringWeights.model_fields:
        valid = ", ".join(ScoringWeights.model_fields)
        return f"Invalid key '{key}'. Valid keys: {valid}"
    try:
        float(value)
    except ValueError:
        return f"Invalid value '{value}': must be numeric"
    resolved = resolve_project(ctx, project)
    if not resolved:
        return "No project specified and no session default set. Use memory_set_project first."
    db = ctx.request_context.lifespan_context.db
    await db.set_config(resolved, key, value)
    return f"Set {key}={value} for project '{resolved}'"
