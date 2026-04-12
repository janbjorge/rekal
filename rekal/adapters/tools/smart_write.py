"""Smart write tools: supersede, build_context."""

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from rekal.adapters.mcp_adapter import mcp, resolve_project
from rekal.models import MemoryRelation, MemoryType


@mcp.tool()
async def memory_supersede(
    ctx: Context,
    old_id: Annotated[str, Field(description="ID of the memory to supersede")],
    new_content: Annotated[str, Field(description="Updated content for the new memory")],
    memory_type: Annotated[
        MemoryType | None,
        Field(description="Override memory type (inherits from old if not set)"),
    ] = None,
    project: Annotated[
        str | None,
        Field(description="Override project (inherits from old if not set)"),
    ] = None,
    conversation_id: Annotated[
        str | None,
        Field(description="Override conversation (inherits from old if not set)"),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(description="Override tags (inherits from old if not set)"),
    ] = None,
) -> str:
    """Create a new memory that supersedes an existing one. Links them explicitly."""
    db = ctx.request_context.lifespan_context.db
    new_id = await db.supersede(
        old_id,
        new_content,
        memory_type=memory_type,
        project=resolve_project(ctx, project),
        conversation_id=conversation_id,
        tags=tags,
    )
    return f"Created memory {new_id} superseding {old_id}"


@mcp.tool()
async def memory_link(
    ctx: Context,
    from_id: Annotated[str, Field(description="Source memory ID")],
    to_id: Annotated[str, Field(description="Target memory ID")],
    relation: Annotated[
        MemoryRelation,
        Field(description="Link type: supersedes, contradicts, or related_to"),
    ],
) -> str:
    """Create a link between two memories (supersedes, contradicts, related_to)."""
    db = ctx.request_context.lifespan_context.db
    await db.add_memory_link(from_id, to_id, relation)
    return f"Linked {from_id} --{relation}--> {to_id}"


@mcp.tool()
async def memory_build_context(
    ctx: Context,
    query: Annotated[str, Field(description="Query to build context for")],
    project: Annotated[str | None, Field(description="Filter to this project")] = None,
    limit: Annotated[int, Field(description="Maximum memories to include")] = 10,
) -> dict[str, str | list[dict[str, str | int | float | list[str] | None]]]:
    """Build rich context for a query: relevant memories + conflicts + timeline."""
    db = ctx.request_context.lifespan_context.db
    result = await db.build_context(query, project=resolve_project(ctx, project), limit=limit)
    return result.model_dump()
