"""Tests for core MCP tools: store, search, delete, update."""

from __future__ import annotations

from dataclasses import dataclass

from rekal.adapters.mcp_adapter import AppContext
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools.core import (
    memory_delete,
    memory_prune,
    memory_search,
    memory_set_config,
    memory_set_project,
    memory_store,
    memory_store_scratch,
    memory_update,
)


@dataclass
class FakeRequestContext:
    lifespan_context: AppContext


@dataclass
class FakeContext:
    request_context: FakeRequestContext


def _ctx(db: SqliteDatabase, file_config: dict[str, float] | None = None) -> FakeContext:
    return FakeContext(
        request_context=FakeRequestContext(
            lifespan_context=AppContext(db=db, file_config=file_config or {})
        )
    )


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


async def test_memory_search_tool_compact_shape(db: SqliteDatabase) -> None:
    await memory_store(_ctx(db), "Compact shape content about Elixir", "fact", "proj", tags=["ex"])
    results = await memory_search(_ctx(db), "Elixir", project="proj")
    assert len(results) > 0
    mem = results[0]
    assert mem["content"] == "Compact shape content about Elixir"
    assert mem["memory_type"] == "fact"
    assert mem["project"] == "proj"
    assert mem["tags"] == ["ex"]
    assert isinstance(mem["score"], float)
    # Bookkeeping fields are dropped from tool output.
    for absent in ("access_count", "last_accessed_at", "updated_at", "tier", "expires_at"):
        assert absent not in mem


async def test_memory_search_tool_compact_omits_unset(db: SqliteDatabase) -> None:
    await memory_store(_ctx(db), "Global untagged note about Nim")
    results = await memory_search(_ctx(db), "Nim")
    assert len(results) > 0
    assert "project" not in results[0]
    assert "tags" not in results[0]


async def test_memory_search_tool_min_score_filters(db: SqliteDatabase) -> None:
    await memory_store(_ctx(db), "Score floor content about Haskell")
    everything = await memory_search(_ctx(db), "Haskell", min_score=0.0)
    nothing = await memory_search(_ctx(db), "Haskell", min_score=1.0)
    assert len(everything) > 0
    assert nothing == []


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


async def test_memory_set_config_tool(db: SqliteDatabase) -> None:
    ctx = _ctx(db)
    await memory_set_project(ctx, "test-proj")
    result = await memory_set_config(ctx, "w_fts", "0.6")
    assert "Set w_fts=0.6" in result
    val = await db.get_config("test-proj", "w_fts")
    assert val == "0.6"


async def test_memory_set_config_tool_invalid_key(db: SqliteDatabase) -> None:
    ctx = _ctx(db)
    await memory_set_project(ctx, "test-proj")
    result = await memory_set_config(ctx, "bad_key", "0.5")
    assert "Invalid key" in result


async def test_memory_set_config_tool_invalid_value(db: SqliteDatabase) -> None:
    ctx = _ctx(db)
    await memory_set_project(ctx, "test-proj")
    result = await memory_set_config(ctx, "w_fts", "not_a_number")
    assert "Invalid value" in result


async def test_memory_set_config_tool_no_project(db: SqliteDatabase) -> None:
    ctx = _ctx(db)
    result = await memory_set_config(ctx, "w_fts", "0.5")
    assert "No project" in result


async def test_memory_prune_tool_requires_filter(db: SqliteDatabase) -> None:
    result = await memory_prune(_ctx(db))
    assert "No filter" in result


async def test_memory_prune_tool_dry_run(db: SqliteDatabase) -> None:
    await db.store("Drop", project="trash")
    result = await memory_prune(_ctx(db), project="trash")
    assert "Would delete 1" in result


async def test_memory_prune_tool_executes(db: SqliteDatabase) -> None:
    mid = await db.store("Drop", project="trash")
    result = await memory_prune(_ctx(db), project="trash", dry_run=False)
    assert "Deleted 1" in result
    assert mid[:5] in result or "sample" in result
    assert await db.get(mid) is None


async def test_memory_prune_tool_older_than_days(db: SqliteDatabase) -> None:
    old = await db.store("Old", project="p")
    await db.db.execute(
        "UPDATE memories SET created_at = '2000-01-01 00:00:00' WHERE id = ?", (old,)
    )
    await db.db.commit()
    fresh = await db.store("Fresh", project="p")

    result = await memory_prune(_ctx(db), older_than_days=1, dry_run=False)
    assert "Deleted 1" in result
    assert await db.get(old) is None
    assert await db.get(fresh) is not None


async def test_memory_prune_tool_uses_session_project(db: SqliteDatabase) -> None:
    ctx = _ctx(db)
    await memory_set_project(ctx, "session-proj")
    mid = await db.store("In session", project="session-proj")
    result = await memory_prune(ctx, dry_run=False)
    assert "Deleted 1" in result
    assert await db.get(mid) is None


async def test_memory_prune_tool_no_matches(db: SqliteDatabase) -> None:
    result = await memory_prune(_ctx(db), project="ghost", dry_run=False)
    assert "Deleted 0" in result


async def test_memory_search_filters_by_tier(db: SqliteDatabase) -> None:
    future = "2999-12-31 23:59:59"
    await db.store("durable Java note")
    await db.store("scratch Java note", tier="scratch", expires_at=future)

    durable_only = await memory_search(_ctx(db), "Java", tier="durable")
    scratch_only = await memory_search(_ctx(db), "Java", tier="scratch")

    # Compact output drops the tier field; identify results by content.
    assert {m["content"] for m in durable_only} == {"durable Java note"}
    assert {m["content"] for m in scratch_only} == {"scratch Java note"}


async def test_memory_store_scratch_tool(db: SqliteDatabase) -> None:
    conv = await db.conversation_start(title="scratch test")
    result = await memory_store_scratch(_ctx(db), "wip note", conv)
    assert "Stored scratch memory" in result
    assert "expires" in result

    mid = result.split()[3]
    mem = await db.get(mid)
    assert mem is not None
    assert mem.tier == "scratch"
    assert mem.expires_at is not None
    assert mem.conversation_id == conv
    assert mem.memory_type == "context"


async def test_memory_store_scratch_custom_ttl(db: SqliteDatabase) -> None:
    conv = await db.conversation_start()
    result = await memory_store_scratch(
        _ctx(db), "short-lived", conv, ttl_hours=1.0, memory_type="fact", tags=["wip"]
    )
    mid = result.split()[3]
    mem = await db.get(mid)
    assert mem is not None
    assert mem.tier == "scratch"
    assert mem.memory_type == "fact"
    assert mem.tags == ["wip"]


async def test_memory_store_scratch_uses_session_project(db: SqliteDatabase) -> None:
    ctx = _ctx(db)
    await memory_set_project(ctx, "scratch-proj")
    conv = await db.conversation_start(project="scratch-proj")
    result = await memory_store_scratch(ctx, "note", conv)
    mid = result.split()[3]
    mem = await db.get(mid)
    assert mem is not None
    assert mem.project == "scratch-proj"


async def test_memory_store_scratch_negative_ttl_expires_immediately(
    db: SqliteDatabase,
) -> None:
    conv = await db.conversation_start()
    result = await memory_store_scratch(_ctx(db), "stale", conv, ttl_hours=-1.0)
    mid = result.split()[3]
    # Direct get returns the row even when expired.
    mem = await db.get(mid)
    assert mem is not None
    # But search hides it.
    hits = await memory_search(_ctx(db), "stale")
    assert all(h["id"] != mid for h in hits)
