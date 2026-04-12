"""Core memory tools: store, search, delete, update."""

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from rekal.adapters.mcp_adapter import mcp, resolve_project
from rekal.models import MemoryType


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
    tags: Annotated[list[str] | None, Field(description="Tags for categorization")] = None,
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
) -> list[dict[str, str | int | float | list[str] | None]]:
    """Search memories using hybrid FTS + vector + recency scoring."""
    db = ctx.request_context.lifespan_context.db
    results = await db.search(
        query,
        limit=limit,
        project=resolve_project(ctx, project),
        memory_type=memory_type,
        conversation_id=conversation_id,
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
    tags: Annotated[list[str] | None, Field(description="New tags (replaces existing)")] = None,
    memory_type: Annotated[MemoryType | None, Field(description="New memory type")] = None,
) -> str:
    """Update an existing memory's content, tags, or type."""
    db = ctx.request_context.lifespan_context.db
    updated = await db.update(memory_id, content=content, tags=tags, memory_type=memory_type)
    if updated:
        return f"Updated memory {memory_id}"
    return f"Memory {memory_id} not found or no changes"
