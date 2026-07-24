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


async def test_memory_build_context_tool_returns_scratch(db: SqliteDatabase) -> None:
    future = "2999-12-31 23:59:59"
    await db.store("durable Go note")
    scratch_id = await db.store("scratch Go note", tier="scratch", expires_at=future)

    result = await memory_build_context(_ctx(db), "Go")
    assert "scratch" in result
    assert isinstance(result["scratch"], list)
    assert scratch_id in {m["id"] for m in result["scratch"]}
    assert scratch_id not in {m["id"] for m in result["memories"]}


async def test_memory_build_context_tool_scratch_limit_zero(db: SqliteDatabase) -> None:
    future = "2999-12-31 23:59:59"
    await db.store("scratch only", tier="scratch", expires_at=future)

    result = await memory_build_context(_ctx(db), "scratch", scratch_limit=0)
    # Empty tiers are omitted from the compact payload, not serialized as [].
    assert "scratch" not in result


async def test_memory_build_context_tool_compact_shape(db: SqliteDatabase) -> None:
    await db.store("Compact context about Zig", project="proj", tags=["zig"])
    result = await memory_build_context(_ctx(db), "Zig", project="proj")
    assert "timeline_summary" in result
    assert "conflicts" not in result  # empty list omitted
    mem = result["memories"][0]
    assert mem["project"] == "proj"
    assert mem["tags"] == ["zig"]
    assert "access_count" not in mem
    assert "updated_at" not in mem
    assert "tier" not in mem


async def test_memory_build_context_tool_min_score_filters(db: SqliteDatabase) -> None:
    await db.store("Floor test context memory")
    result = await memory_build_context(_ctx(db), "floor test context", min_score=1.0)
    assert result["memories"] == []


async def test_memory_build_context_tool_includes_conflicts(db: SqliteDatabase) -> None:
    mid1 = await db.store("Tabs are better for indentation")
    mid2 = await db.store("Spaces are better for indentation")
    await db.add_memory_link(mid1, mid2, "contradicts")

    result = await memory_build_context(_ctx(db), "indentation")
    assert "conflicts" in result
    assert len(result["conflicts"]) >= 1
