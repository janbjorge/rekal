"""Tests for smart write: supersede, build_context."""

from __future__ import annotations

import pytest

from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.scoring import ScoringWeights


async def test_supersede_inherits_fields(db: SqliteDatabase) -> None:
    # All unspecified fields must be copied from the old memory.
    old_id = await db.store(
        "Old", memory_type="fact", project="py", tags=["a", "b"], conversation_id="conv-1"
    )
    new_id = await db.supersede(old_id, "New")
    new_mem = await db.get(new_id)
    assert new_mem is not None
    assert new_mem.content == "New"
    assert new_mem.memory_type == "fact"
    assert new_mem.project == "py"
    assert new_mem.tags == ["a", "b"]
    assert new_mem.conversation_id == "conv-1"


async def test_supersede_override_all_fields(db: SqliteDatabase) -> None:
    old_id = await db.store(
        "Old", memory_type="fact", project="p1", tags=["old"], conversation_id="c1"
    )
    new_id = await db.supersede(
        old_id, "New", memory_type="preference", project="p2", tags=["new"], conversation_id="c2"
    )
    new_mem = await db.get(new_id)
    assert new_mem is not None
    assert new_mem.memory_type == "preference"
    assert new_mem.project == "p2"
    assert new_mem.tags == ["new"]
    assert new_mem.conversation_id == "c2"


async def test_supersede_explicit_empty_tags_not_inherited(db: SqliteDatabase) -> None:
    # tags=[] must not fall back to old tags — [] is a valid explicit value.
    old_id = await db.store("Old", tags=["keep-me"])
    new_id = await db.supersede(old_id, "New", tags=[])
    new_mem = await db.get(new_id)
    assert new_mem is not None
    assert new_mem.tags == []


async def test_supersede_old_memory_preserved(db: SqliteDatabase) -> None:
    # supersede() must not delete the old memory.
    old_id = await db.store("Old")
    await db.supersede(old_id, "New")
    assert await db.get(old_id) is not None


async def test_supersede_link_direction(db: SqliteDatabase) -> None:
    # Link must go from new → old with relation 'supersedes'.
    old_id = await db.store("Old")
    new_id = await db.supersede(old_id, "New")
    cursor = await db.db.execute(
        """
        SELECT COUNT(*) FROM memories m
        JOIN memory_links l ON l.from_id = m.id
        WHERE m.id = ? AND l.to_id = ? AND l.relation = 'supersedes'
        """,
        (new_id, old_id),
    )
    row = await cursor.fetchone()
    assert row is not None and row[0] == 1


async def test_supersede_nonexistent(db: SqliteDatabase) -> None:
    with pytest.raises(ValueError, match="not found"):
        await db.supersede("nonexistent", "New content")


async def test_add_memory_link(db: SqliteDatabase) -> None:
    mid1 = await db.store("First")
    mid2 = await db.store("Second")
    await db.add_memory_link(mid1, mid2, "related_to")

    related = await db.memory_related(mid1)
    assert len(related) == 1
    assert related[0]["relation"] == "related_to"


async def test_add_memory_link_duplicate(db: SqliteDatabase) -> None:
    mid1 = await db.store("First")
    mid2 = await db.store("Second")
    await db.add_memory_link(mid1, mid2, "related_to")
    await db.add_memory_link(mid1, mid2, "related_to")  # Should not raise

    related = await db.memory_related(mid1)
    assert len(related) == 1


async def test_build_context(db: SqliteDatabase) -> None:
    await db.store("Python uses indentation for blocks")
    await db.store("Python has list comprehensions")
    mid1 = await db.store("Python 2 is deprecated")
    mid2 = await db.store("Python 2 is still used in some places")
    await db.add_memory_link(mid1, mid2, "contradicts")

    ctx = await db.build_context("Python", weights=ScoringWeights())
    assert len(ctx.memories) > 0
    assert ctx.query == "Python"
    assert (
        "memories" in ctx.timeline_summary.lower() or "no memories" in ctx.timeline_summary.lower()
    )


async def test_build_context_empty(db: SqliteDatabase) -> None:
    ctx = await db.build_context("nonexistent xyzzy", weights=ScoringWeights())
    assert ctx.memories == []
    assert ctx.timeline_summary == "No memories found"
