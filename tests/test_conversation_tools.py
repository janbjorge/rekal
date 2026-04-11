"""Tests for conversation MCP tools."""

from __future__ import annotations

from dataclasses import dataclass

from rekal.adapters.mcp_adapter import AppContext
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools.conversations import (
    conversation_stale,
    conversation_start,
    conversation_threads,
    conversation_tree,
)


@dataclass
class FakeRequestContext:
    lifespan_context: AppContext


@dataclass
class FakeContext:
    request_context: FakeRequestContext


def _ctx(db: SqliteDatabase) -> FakeContext:
    return FakeContext(request_context=FakeRequestContext(lifespan_context=AppContext(db=db)))


async def test_conversation_start_tool(db: SqliteDatabase) -> None:
    result = await conversation_start(_ctx(db), title="Test")
    assert "Started conversation" in result


async def test_conversation_tree_tool(db: SqliteDatabase) -> None:
    c1 = await db.conversation_start(title="Root")
    result = await conversation_tree(_ctx(db), c1)
    assert isinstance(result, list)


async def test_conversation_threads_tool(db: SqliteDatabase) -> None:
    await db.conversation_start(title="Thread")
    result = await conversation_threads(_ctx(db))
    assert isinstance(result, list)
    assert len(result) > 0


async def test_conversation_stale_tool(db: SqliteDatabase) -> None:
    result = await conversation_stale(_ctx(db))
    assert isinstance(result, list)
