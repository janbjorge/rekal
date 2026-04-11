"""Tests for introspection tools: similar, topics, timeline, related, health, conflicts."""

from __future__ import annotations

from rekal.adapters.sqlite_adapter import SqliteDatabase


async def test_memory_similar(db: SqliteDatabase) -> None:
    mid1 = await db.store("Python programming language")
    await db.store("JavaScript programming language")
    await db.store("Cooking recipes for pasta")

    results = await db.memory_similar(mid1, limit=2)
    assert len(results) <= 2
    assert all(r.id != mid1 for r in results)


async def test_memory_similar_nonexistent(db: SqliteDatabase) -> None:
    results = await db.memory_similar("nonexistent")
    assert results == []


async def test_memory_topics(db: SqliteDatabase) -> None:
    await db.store("A fact", memory_type="fact")
    await db.store("Another fact", memory_type="fact")
    await db.store("A preference", memory_type="preference")

    topics = await db.memory_topics()
    assert len(topics) == 2
    fact_topic = next(t for t in topics if t.topic == "fact")
    assert fact_topic.count == 2


async def test_memory_topics_with_project(db: SqliteDatabase) -> None:
    await db.store("P1 fact", memory_type="fact", project="p1")
    await db.store("P2 fact", memory_type="fact", project="p2")

    topics = await db.memory_topics(project="p1")
    total = sum(t.count for t in topics)
    assert total == 1


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
    assert all(r.project == "p1" for r in results)


async def test_memory_timeline_with_date_range(db: SqliteDatabase) -> None:
    await db.store("Memory")
    results = await db.memory_timeline(start="2020-01-01", end="2030-12-31")
    assert len(results) >= 1


async def test_memory_related(db: SqliteDatabase) -> None:
    mid1 = await db.store("First")
    mid2 = await db.store("Second")
    await db.add_memory_link(mid1, mid2, "related_to")

    related = await db.memory_related(mid1)
    assert len(related) == 1
    assert related[0]["id"] == mid2

    # Reverse direction
    related2 = await db.memory_related(mid2)
    assert len(related2) == 1
    assert related2[0]["id"] == mid1


async def test_memory_health(db: SqliteDatabase) -> None:
    await db.store("A fact", memory_type="fact")
    await db.store("A preference", memory_type="preference", project="p1")

    report = await db.memory_health()
    assert report.total_memories == 2
    assert report.total_conversations == 0
    assert report.memories_by_type["fact"] == 1
    assert report.memories_by_type["preference"] == 1
    assert "p1" in report.memories_by_project
    assert report.oldest_memory is not None
    assert report.newest_memory is not None


async def test_memory_health_empty(db: SqliteDatabase) -> None:
    report = await db.memory_health()
    assert report.total_memories == 0


async def test_memory_conflicts(db: SqliteDatabase) -> None:
    mid1 = await db.store("Earth is flat", project="geo")
    mid2 = await db.store("Earth is round", project="geo")
    await db.add_memory_link(mid1, mid2, "contradicts")

    conflicts = await db.memory_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].memory_id == mid1
    assert conflicts[0].related_id == mid2


async def test_memory_conflicts_with_project_filter(db: SqliteDatabase) -> None:
    mid1 = await db.store("A is true", project="p1")
    mid2 = await db.store("A is false", project="p1")
    mid3 = await db.store("B is true", project="p2")
    mid4 = await db.store("B is false", project="p2")
    await db.add_memory_link(mid1, mid2, "contradicts")
    await db.add_memory_link(mid3, mid4, "contradicts")

    conflicts = await db.memory_conflicts(project="p1")
    assert len(conflicts) == 1
    assert conflicts[0].memory_id == mid1


async def test_memory_conflicts_empty(db: SqliteDatabase) -> None:
    conflicts = await db.memory_conflicts()
    assert conflicts == []
