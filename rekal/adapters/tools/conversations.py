"""Conversation tools: start, tree, threads, stale."""

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from rekal.adapters.mcp_adapter import mcp, resolve_project


@mcp.tool()
async def conversation_start(
    ctx: Context,
    title: Annotated[str | None, Field(description="Title for the conversation")] = None,
    project: Annotated[
        str | None, Field(description="Project scope for this conversation")
    ] = None,
    follows_up_on: Annotated[
        str | None, Field(description="ID of conversation this follows up on")
    ] = None,
    branches_from: Annotated[
        str | None, Field(description="ID of conversation this branches from")
    ] = None,
) -> str:
    """Start a new conversation. Optionally link it to an existing one."""
    db = ctx.request_context.lifespan_context.db
    conv_id = await db.conversation_start(
        title=title,
        project=resolve_project(ctx, project),
        follows_up_on=follows_up_on,
        branches_from=branches_from,
    )
    return f"Started conversation {conv_id}"


@mcp.tool()
async def conversation_tree(
    ctx: Context,
    conversation_id: Annotated[
        str, Field(description="ID of the conversation to get the tree for")
    ],
) -> list[dict[str, str]]:
    """Get the full conversation tree (DAG) for a conversation."""
    db = ctx.request_context.lifespan_context.db
    links = await db.conversation_tree(conversation_id)
    return [link.model_dump() for link in links]


@mcp.tool()
async def conversation_threads(
    ctx: Context,
    project: Annotated[str | None, Field(description="Filter to this project")] = None,
    limit: Annotated[int, Field(description="Maximum conversations to return")] = 20,
) -> list[dict[str, str | int | None]]:
    """List recent conversations with memory counts."""
    db = ctx.request_context.lifespan_context.db
    results = await db.conversation_threads(project=resolve_project(ctx, project), limit=limit)
    return [r.model_dump() for r in results]


@mcp.tool()
async def conversation_stale(
    ctx: Context,
    days: Annotated[int, Field(description="Number of days of inactivity to consider stale")] = 30,
) -> list[dict[str, str | int | None]]:
    """Find conversations with no recent memory activity."""
    db = ctx.request_context.lifespan_context.db
    results = await db.conversation_stale(days=days)
    return [r.model_dump() for r in results]
