"""Tests for hybrid search scoring, ranking correctness."""

from __future__ import annotations

from rekal.adapters.sqlite_adapter import SqliteDatabase


async def test_search_basic(db: SqliteDatabase) -> None:
    await db.store("Python is a programming language", project="test")
    await db.store("Rust is a systems language", project="test")
    await db.store("Cooking pasta is easy", project="test")

    results = await db.search("programming language")
    assert len(results) > 0
    # FTS should rank programming language matches higher
    assert any("programming" in r.content.lower() for r in results)


async def test_search_with_project_filter(db: SqliteDatabase) -> None:
    await db.store("Project A memory", project="alpha")
    await db.store("Project B memory", project="beta")

    results = await db.search("memory", project="alpha")
    assert all(r.project == "alpha" for r in results)


async def test_search_with_type_filter(db: SqliteDatabase) -> None:
    await db.store("A fact", memory_type="fact")
    await db.store("A preference", memory_type="preference")

    results = await db.search("fact preference", memory_type="fact")
    assert all(r.memory_type == "fact" for r in results)


async def test_search_with_conversation_filter(db: SqliteDatabase) -> None:
    conv = await db.conversation_start(title="Test")
    await db.store("In conversation", conversation_id=conv)
    await db.store("No conversation")

    results = await db.search("conversation", conversation_id=conv)
    assert all(r.conversation_id == conv for r in results)


async def test_search_limit(db: SqliteDatabase) -> None:
    for i in range(20):
        await db.store(f"Memory number {i} about testing")

    results = await db.search("testing", limit=5)
    assert len(results) <= 5


async def test_search_no_results(db: SqliteDatabase) -> None:
    results = await db.search("xyzzy nonexistent query")
    assert results == []


async def test_search_updates_access_count(db: SqliteDatabase) -> None:
    mid = await db.store("Accessed memory about databases")
    await db.search("databases")

    mem = await db.get(mid)
    assert mem is not None
    assert mem.access_count >= 1
    assert mem.last_accessed_at is not None


async def test_search_scores_present(db: SqliteDatabase) -> None:
    await db.store("Scoring test memory content")
    results = await db.search("scoring test")
    for r in results:
        assert r.score is not None
        assert r.score > 0
