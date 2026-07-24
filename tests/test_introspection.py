"""Tests for DB-level introspection: timeline, health (the CLI surface)."""

from __future__ import annotations

from rekal.adapters.sqlite_adapter import SqliteDatabase


async def test_memory_timeline(db: SqliteDatabase) -> None:
    await db.store("First memory")
    await db.store("Second memory")
    await db.store("Third memory")

    results = await db.memory_timeline(limit=2)
    assert len(results) == 2


async def test_memory_timeline_with_project(db: SqliteDatabase) -> None:
    await db.store("P1 memory", project="p1")
    await db.store("P2 memory", project="p2")

    results = await db.memory_timeline(project="p1")
    assert [r.project for r in results] == ["p1"]


async def test_memory_timeline_recency_ordered(db: SqliteDatabase) -> None:
    old = await db.store("Old memory")
    await db.db.execute(
        "UPDATE memories SET created_at = '2000-01-01 00:00:00' WHERE id = ?",
        (old,),
    )
    await db.db.commit()
    new = await db.store("New memory")

    results = await db.memory_timeline()
    assert [r.id for r in results] == [new, old]


async def test_memory_health(db: SqliteDatabase) -> None:
    await db.store("A fact")
    await db.store("A scoped fact", project="p1")

    report = await db.memory_health()
    assert report.total_memories == 2
    assert report.memories_by_project == {"<none>": 1, "p1": 1}
    assert report.oldest_memory is not None
    assert report.newest_memory is not None


async def test_memory_health_empty(db: SqliteDatabase) -> None:
    report = await db.memory_health()
    assert report.total_memories == 0
    assert report.oldest_memory is None
