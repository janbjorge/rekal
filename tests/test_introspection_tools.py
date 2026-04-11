"""Tests for introspection MCP tools."""

from __future__ import annotations

from dataclasses import dataclass

from rekal.adapters.mcp_adapter import AppContext
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools.introspection import (
    memory_conflicts,
    memory_health,
    memory_related,
    memory_similar,
    memory_timeline,
    memory_topics,
)


@dataclass
class FakeRequestContext:
    lifespan_context: AppContext


@dataclass
class FakeContext:
    request_context: FakeRequestContext


def _ctx(db: SqliteDatabase) -> FakeContext:
    return FakeContext(request_context=FakeRequestContext(lifespan_context=AppContext(db=db)))


async def test_memory_similar_tool(db: SqliteDatabase) -> None:
    mid = await db.store("Python programming")
    await db.store("JavaScript programming")
    results = await memory_similar(_ctx(db), mid)
    assert isinstance(results, list)


async def test_memory_topics_tool(db: SqliteDatabase) -> None:
    await db.store("A fact", memory_type="fact")
    results = await memory_topics(_ctx(db))
    assert isinstance(results, list)
    assert len(results) > 0


async def test_memory_timeline_tool(db: SqliteDatabase) -> None:
    await db.store("Timeline entry")
    results = await memory_timeline(_ctx(db))
    assert isinstance(results, list)


async def test_memory_related_tool(db: SqliteDatabase) -> None:
    mid1 = await db.store("First")
    mid2 = await db.store("Second")
    await db.add_memory_link(mid1, mid2, "related_to")
    results = await memory_related(_ctx(db), mid1)
    assert len(results) == 1


async def test_memory_health_tool(db: SqliteDatabase) -> None:
    result = await memory_health(_ctx(db))
    assert "total_memories" in result


async def test_memory_conflicts_tool(db: SqliteDatabase) -> None:
    results = await memory_conflicts(_ctx(db))
    assert isinstance(results, list)
