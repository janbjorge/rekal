"""Tests for SqliteDatabase — all database operations directly."""

from __future__ import annotations

import pytest

from rekal.adapters.sqlite_adapter import SqliteDatabase


async def test_store_and_get(db: SqliteDatabase) -> None:
    mid = await db.store("Python is great", project="test")
    mem = await db.get(mid)
    assert mem is not None
    assert mem.content == "Python is great"
    assert mem.project == "test"
    assert mem.created_at
    assert mem.updated_at


async def test_store_with_tags(db: SqliteDatabase) -> None:
    mid = await db.store("Use ruff for linting", tags=["python", "tools"])
    mem = await db.get(mid)
    assert mem is not None
    assert mem.tags == ["python", "tools"]


async def test_get_nonexistent(db: SqliteDatabase) -> None:
    result = await db.get("nonexistent")
    assert result is None


async def test_delete(db: SqliteDatabase) -> None:
    mid = await db.store("To be deleted")
    assert await db.delete(mid)
    assert await db.get(mid) is None


async def test_delete_nonexistent(db: SqliteDatabase) -> None:
    assert not await db.delete("nonexistent")


async def test_delete_removes_vec_row(db: SqliteDatabase) -> None:
    mid = await db.store("Vec cleanup")
    await db.delete(mid)
    async with db.db.execute("SELECT id FROM memory_vec WHERE id = ?", (mid,)) as cursor:
        assert await cursor.fetchone() is None


# ── replace ─────────────────────────────────────────────────────────


async def test_replace_deletes_old_and_stores_new(db: SqliteDatabase) -> None:
    old = await db.store("v1 of the fact", project="p", tags=["t"])
    new = await db.replace(old, "v2 of the fact")
    assert await db.get(old) is None
    mem = await db.get(new)
    assert mem is not None
    assert mem.content == "v2 of the fact"
    # Project and tags inherited from the replaced memory.
    assert mem.project == "p"
    assert mem.tags == ["t"]


async def test_replace_overrides_project_and_tags(db: SqliteDatabase) -> None:
    old = await db.store("v1", project="old-proj", tags=["old"])
    new = await db.replace(old, "v2", project="new-proj", tags=["new"])
    mem = await db.get(new)
    assert mem is not None
    assert mem.project == "new-proj"
    assert mem.tags == ["new"]


async def test_replace_missing_id_raises(db: SqliteDatabase) -> None:
    with pytest.raises(ValueError, match="not found"):
        await db.replace("nonexistent", "content")


# ── prune ───────────────────────────────────────────────────────────


async def test_prune_requires_filter(db: SqliteDatabase) -> None:
    with pytest.raises(ValueError, match="prune requires"):
        await db.prune()


async def test_prune_dry_run_returns_matches(db: SqliteDatabase) -> None:
    keep = await db.store("Keep me", project="other")
    drop1 = await db.store("Drop me", project="trash")
    drop2 = await db.store("Drop me too", project="trash")

    ids = await db.prune(project="trash", dry_run=True)
    assert set(ids) == {drop1, drop2}
    # Nothing actually deleted on dry run
    assert await db.get(drop1) is not None
    assert await db.get(drop2) is not None
    assert await db.get(keep) is not None


async def test_prune_by_project_deletes(db: SqliteDatabase) -> None:
    keep = await db.store("Keep me", project="other")
    drop = await db.store("Drop me", project="trash")

    ids = await db.prune(project="trash", dry_run=False)
    assert ids == [drop]
    assert await db.get(drop) is None
    assert await db.get(keep) is not None


async def test_prune_by_before_timestamp(db: SqliteDatabase) -> None:
    old_id = await db.store("Old")
    # Force created_at backwards
    await db.db.execute(
        "UPDATE memories SET created_at = '2000-01-01 00:00:00' WHERE id = ?",
        (old_id,),
    )
    await db.db.commit()
    new_id = await db.store("New")

    ids = await db.prune(before="2020-01-01 00:00:00", dry_run=False)
    assert ids == [old_id]
    assert await db.get(old_id) is None
    assert await db.get(new_id) is not None


async def test_prune_no_matches_skips_delete(db: SqliteDatabase) -> None:
    await db.store("Stay", project="alive")
    ids = await db.prune(project="ghost", dry_run=False)
    assert ids == []


async def test_prune_removes_vec_rows(db: SqliteDatabase) -> None:
    drop = await db.store("Prune vec", project="trash")
    await db.prune(project="trash", dry_run=False)
    async with db.db.execute("SELECT id FROM memory_vec WHERE id = ?", (drop,)) as cursor:
        assert await cursor.fetchone() is None
