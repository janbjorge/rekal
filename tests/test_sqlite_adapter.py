"""Tests for SqliteDatabase — all database operations directly."""

from __future__ import annotations

from rekal.adapters.sqlite_adapter import SqliteDatabase


async def test_store_and_get(db: SqliteDatabase) -> None:
    mid = await db.store("Python is great", memory_type="fact", project="test")
    mem = await db.get(mid)
    assert mem is not None
    assert mem.content == "Python is great"
    assert mem.memory_type == "fact"
    assert mem.project == "test"


async def test_store_with_tags(db: SqliteDatabase) -> None:
    mid = await db.store("Use ruff for linting", tags=["python", "tools"])
    mem = await db.get(mid)
    assert mem is not None
    assert mem.tags == ["python", "tools"]


async def test_store_with_conversation(db: SqliteDatabase) -> None:
    conv_id = await db.conversation_start(title="Test conv")
    mid = await db.store("Memory in conv", conversation_id=conv_id)
    mem = await db.get(mid)
    assert mem is not None
    assert mem.conversation_id == conv_id


async def test_get_nonexistent(db: SqliteDatabase) -> None:
    result = await db.get("nonexistent")
    assert result is None


async def test_delete(db: SqliteDatabase) -> None:
    mid = await db.store("To be deleted")
    assert await db.delete(mid)
    assert await db.get(mid) is None


async def test_delete_nonexistent(db: SqliteDatabase) -> None:
    assert not await db.delete("nonexistent")


async def test_delete_with_links(db: SqliteDatabase) -> None:
    mid1 = await db.store("First")
    mid2 = await db.store("Second")
    await db.add_memory_link(mid1, mid2, "related_to")
    assert await db.delete(mid1)
    related = await db.memory_related(mid2)
    assert len(related) == 0


async def test_update_content(db: SqliteDatabase) -> None:
    mid = await db.store("Old content")
    assert await db.update(mid, content="New content")
    mem = await db.get(mid)
    assert mem is not None
    assert mem.content == "New content"


async def test_update_tags(db: SqliteDatabase) -> None:
    mid = await db.store("Tagged memory")
    assert await db.update(mid, tags=["updated"])
    mem = await db.get(mid)
    assert mem is not None
    assert mem.tags == ["updated"]


async def test_update_type(db: SqliteDatabase) -> None:
    mid = await db.store("A memory", memory_type="fact")
    assert await db.update(mid, memory_type="preference")
    mem = await db.get(mid)
    assert mem is not None
    assert mem.memory_type == "preference"


async def test_update_nonexistent(db: SqliteDatabase) -> None:
    assert not await db.update("nonexistent", content="nope")


async def test_update_no_changes(db: SqliteDatabase) -> None:
    mid = await db.store("A memory")
    assert not await db.update(mid)


async def test_set_and_get_config(db: SqliteDatabase) -> None:
    await db.set_config("myproj", "w_fts", "0.6")
    val = await db.get_config("myproj", "w_fts")
    assert val == "0.6"


async def test_set_config_upsert(db: SqliteDatabase) -> None:
    await db.set_config("myproj", "w_fts", "0.6")
    await db.set_config("myproj", "w_fts", "0.8")
    val = await db.get_config("myproj", "w_fts")
    assert val == "0.8"


async def test_get_config_missing(db: SqliteDatabase) -> None:
    val = await db.get_config("noproject", "w_fts")
    assert val is None


async def test_get_project_config(db: SqliteDatabase) -> None:
    await db.set_config("proj", "w_fts", "0.5")
    await db.set_config("proj", "half_life", "7.0")
    config = await db.get_project_config("proj")
    assert config == {"w_fts": "0.5", "half_life": "7.0"}


async def test_get_project_config_empty(db: SqliteDatabase) -> None:
    config = await db.get_project_config("empty")
    assert config == {}


async def test_delete_config(db: SqliteDatabase) -> None:
    await db.set_config("proj", "w_fts", "0.5")
    assert await db.delete_config("proj", "w_fts")
    assert await db.get_config("proj", "w_fts") is None


async def test_delete_config_missing(db: SqliteDatabase) -> None:
    assert not await db.delete_config("proj", "nonexistent")


async def test_resolve_weights_defaults(db: SqliteDatabase) -> None:
    weights = await db.resolve_weights(None)
    assert weights.w_fts == 0.4
    assert weights.w_vec == 0.4
    assert weights.w_recency == 0.2
    assert weights.half_life == 30.0


async def test_resolve_weights_from_project_config(db: SqliteDatabase) -> None:
    await db.set_config("proj", "w_fts", "0.7")
    await db.set_config("proj", "half_life", "14.0")
    weights = await db.resolve_weights("proj")
    assert weights.w_fts == 0.7
    assert weights.w_vec == 0.4  # default, not in config
    assert weights.half_life == 14.0


async def test_resolve_weights_per_call_overrides_config(db: SqliteDatabase) -> None:
    await db.set_config("proj", "w_fts", "0.7")
    weights = await db.resolve_weights("proj", w_fts=0.1)
    assert weights.w_fts == 0.1  # per-call wins
