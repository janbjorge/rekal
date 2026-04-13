"""Tests for smart write MCP tools."""

from __future__ import annotations

from dataclasses import dataclass

from rekal.adapters.mcp_adapter import AppContext
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools.smart_write import memory_build_context, memory_link, memory_supersede


@dataclass
class FakeRequestContext:
    lifespan_context: AppContext


@dataclass
class FakeContext:
    request_context: FakeRequestContext


def _ctx(db: SqliteDatabase) -> FakeContext:
    return FakeContext(request_context=FakeRequestContext(lifespan_context=AppContext(db=db)))


async def test_memory_supersede_tool(db: SqliteDatabase) -> None:
    old_id = await db.store("Old version")
    result = await memory_supersede(_ctx(db), old_id, "New version")
    assert "superseding" in result


async def test_memory_link_tool(db: SqliteDatabase) -> None:
    mid1 = await db.store("First")
    mid2 = await db.store("Second")
    result = await memory_link(_ctx(db), mid1, mid2, "related_to")
    assert "Linked" in result


async def test_memory_build_context_tool(db: SqliteDatabase) -> None:
    await db.store("Context about Python")
    result = await memory_build_context(_ctx(db), "Python")
    assert "query" in result
    assert "memories" in result


async def test_memory_build_context_tool_custom_weights(db: SqliteDatabase) -> None:
    await db.store("Weighted context about Rust")
    result = await memory_build_context(
        _ctx(db), "Rust", w_fts=0.1, w_vec=0.1, w_recency=0.8, half_life=7.0
    )
    assert "query" in result
    assert "memories" in result
