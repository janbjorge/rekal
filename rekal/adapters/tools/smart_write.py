"""Smart write tools: supersede, build_context."""

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from rekal.adapters.mcp_adapter import mcp, resolve_project
from rekal.models import CompactContext, MemoryRelation, MemoryType


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
    limit: Annotated[
        int,
        Field(description="Max durable-tier memories to include"),
    ] = 10,
    scratch_limit: Annotated[
        int,
        Field(
            description="Max scratch-tier memories to include. "
            "Bounded separately from durable. Set 0 to skip scratch."
        ),
    ] = 5,
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
    min_score: Annotated[
        float,
        Field(description="Drop results scoring below this relevance floor (0.0-1.0)."),
    ] = 0.25,
) -> CompactContext:
    """Build rich context for a query with per-tier budgets.

    Returns ``memories`` (durable tier, top ``limit``) and ``scratch``
    (scratch tier, top ``scratch_limit``) so the caller sees a bounded
    working context per tier instead of a single flat list.
    """
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
    result = await db.build_context(
        query,
        project=resolved_project,
        limit=limit,
        scratch_limit=scratch_limit,
        weights=weights,
        min_score=min_score,
    )
    return result.compact()
