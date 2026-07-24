"""Tests for the MCP tool surface: build_context, store, delete."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
from mcp.server.fastmcp import Context

from rekal.adapters.mcp_adapter import AppContext
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools.core import memory_build_context, memory_delete, memory_store
from rekal.models import CompactMemory


@dataclass
class FakeRequestContext:
    lifespan_context: AppContext


@dataclass
class FakeContext:
    request_context: FakeRequestContext


def _ctx(
    db: SqliteDatabase,
    file_config: dict[str, float] | None = None,
    default_project: str | None = None,
) -> Context:
    # Structurally what the tools read from a real Context; cast keeps the
    # tool signatures honest (FastMCP injects Context by annotation).
    return cast(
        "Context",
        FakeContext(
            request_context=FakeRequestContext(
                lifespan_context=AppContext(
                    db=db, default_project=default_project, file_config=file_config or {}
                )
            )
        ),
    )


async def test_memory_store_tool(db: SqliteDatabase) -> None:
    result = await memory_store(_ctx(db), "Test memory", project="proj")
    assert "Stored memory" in result


async def test_memory_store_tool_with_tags(db: SqliteDatabase) -> None:
    result = await memory_store(_ctx(db), "Tagged memory about auth", tags=["auth", "jwt"])
    assert "Stored memory" in result
    context = await memory_build_context(_ctx(db), "auth")
    assert context.memories[0].tags == ["auth", "jwt"]


async def test_memory_store_tool_uses_default_project(db: SqliteDatabase) -> None:
    ctx = _ctx(db, default_project="session-proj")
    await memory_store(ctx, "Memory with session default project")
    context = await memory_build_context(ctx, "session default project")
    assert context.memories[0].project == "session-proj"


async def test_memory_store_tool_replaces(db: SqliteDatabase) -> None:
    old = await db.store("Old fact about the deploy pipeline")
    result = await memory_store(_ctx(db), "New fact about the deploy pipeline", replaces=old)
    assert f"(replaces {old})" in result

    context = await memory_build_context(_ctx(db), "deploy pipeline")
    contents = [m.content for m in context.memories]
    assert "New fact about the deploy pipeline" in contents
    # The replaced memory is gone entirely.
    assert "Old fact about the deploy pipeline" not in contents
    assert await db.get(old) is None


async def test_memory_store_tool_replaces_missing_id(db: SqliteDatabase) -> None:
    with pytest.raises(ValueError, match="not found"):
        await memory_store(_ctx(db), "Replacement", replaces="nonexistent")


async def test_memory_delete_tool(db: SqliteDatabase) -> None:
    mid = await db.store("To delete")
    result = await memory_delete(_ctx(db), mid)
    assert "Deleted" in result


async def test_memory_delete_tool_not_found(db: SqliteDatabase) -> None:
    result = await memory_delete(_ctx(db), "nonexistent")
    assert "not found" in result


async def test_memory_build_context_tool(db: SqliteDatabase) -> None:
    await db.store("Context about Python")
    result = await memory_build_context(_ctx(db), "Python")
    assert result.query == "Python"
    assert len(result.memories) == 1


async def test_memory_build_context_tool_compact_shape(db: SqliteDatabase) -> None:
    await db.store("Compact context about Zig", project="proj", tags=["zig"])
    result = await memory_build_context(_ctx(db), "Zig", project="proj")
    mem = result.memories[0]
    assert mem.project == "proj"
    assert mem.tags == ["zig"]
    assert isinstance(mem.score, float)
    # Bookkeeping fields are dropped from the compact projection entirely.
    for absent in ("access_count", "last_accessed_at", "updated_at", "tier", "expires_at"):
        assert absent not in CompactMemory.model_fields


async def test_memory_build_context_tool_compact_none_when_unset(db: SqliteDatabase) -> None:
    await db.store("Global untagged note about Nim")
    result = await memory_build_context(_ctx(db), "Nim")
    mem = result.memories[0]
    assert mem.project is None
    assert mem.tags is None


async def test_memory_build_context_tool_min_score_filters(db: SqliteDatabase) -> None:
    await db.store("Score floor content about Haskell")
    everything = await memory_build_context(_ctx(db), "Haskell", min_score=0.0)
    nothing = await memory_build_context(_ctx(db), "Haskell", min_score=1.0)
    assert len(everything.memories) > 0
    assert nothing.memories == []


async def test_memory_build_context_tool_uses_file_config(db: SqliteDatabase) -> None:
    await db.store("File config weighted note about linkers")
    default = await memory_build_context(_ctx(db), "linkers")
    weighted = await memory_build_context(
        _ctx(db, file_config={"w_fts": 0.8, "w_vec": 0.1, "w_recency": 0.1}), "linkers"
    )
    assert default.memories[0].score != weighted.memories[0].score
