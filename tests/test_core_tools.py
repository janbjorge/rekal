"""Tests for core MCP tools: store, search, delete, update."""

from __future__ import annotations

from dataclasses import dataclass

from rekal.adapters.mcp_adapter import AppContext
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools.core import (
    memory_delete,
    memory_search,
    memory_set_project,
    memory_store,
    memory_update,
)


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


async def test_memory_search_tool_custom_weights(db: SqliteDatabase) -> None:
    await memory_store(_ctx(db), "Weight test content about databases")
    results = await memory_search(_ctx(db), "databases", w_fts=0.8, w_vec=0.1, w_recency=0.1)
    assert isinstance(results, list)
    assert len(results) > 0


async def test_memory_set_project_tool(db: SqliteDatabase) -> None:
    ctx = _ctx(db)
    assert ctx.request_context.lifespan_context.default_project is None
    result = await memory_set_project(ctx, "my-project")
    assert "my-project" in result
    assert ctx.request_context.lifespan_context.default_project == "my-project"
    # Subsequent store should use the default project
    await memory_store(ctx, "Test with default project")
    results = await memory_search(ctx, "Test with default project")
    assert any(r["project"] == "my-project" for r in results)
