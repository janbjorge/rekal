"""Tests for core MCP tools: store, search, delete, update."""

from __future__ import annotations

from dataclasses import dataclass

from rekal.adapters.mcp_adapter import AppContext
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools.core import memory_delete, memory_search, memory_store, memory_update


@dataclass
class FakeRequestContext:
    lifespan_context: AppContext


@dataclass
class FakeContext:
    request_context: FakeRequestContext


def _ctx(db: SqliteDatabase) -> FakeContext:
    return FakeContext(request_context=FakeRequestContext(lifespan_context=AppContext(db=db)))


async def test_memory_store_tool(db: SqliteDatabase) -> None:
    result = await memory_store(_ctx(db), "Test memory", "fact", "proj")
    assert "Stored memory" in result


async def test_memory_search_tool(db: SqliteDatabase) -> None:
    await memory_store(_ctx(db), "Searchable content about Python")
    results = await memory_search(_ctx(db), "Python")
    assert isinstance(results, list)


async def test_memory_delete_tool(db: SqliteDatabase) -> None:
    mid = await db.store("To delete")
    result = await memory_delete(_ctx(db), mid)
    assert "Deleted" in result


async def test_memory_delete_tool_not_found(db: SqliteDatabase) -> None:
    result = await memory_delete(_ctx(db), "nonexistent")
    assert "not found" in result


async def test_memory_update_tool(db: SqliteDatabase) -> None:
    mid = await db.store("Original")
    result = await memory_update(_ctx(db), mid, content="Updated")
    assert "Updated" in result


async def test_memory_update_tool_not_found(db: SqliteDatabase) -> None:
    result = await memory_update(_ctx(db), "nonexistent", content="Nope")
    assert "not found" in result or "no changes" in result
