"""Tests for smart write: supersede, build_context."""

from __future__ import annotations

import pytest

from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.scoring import ScoringWeights


async def test_supersede(db: SqliteDatabase) -> None:
    old_id = await db.store("Python 3.11 is latest", memory_type="fact", project="py")
    new_id = await db.supersede(old_id, "Python 3.14 is latest")

    new_mem = await db.get(new_id)
    assert new_mem is not None
    assert new_mem.content == "Python 3.14 is latest"
    assert new_mem.memory_type == "fact"
    assert new_mem.project == "py"

    # Check link
    related = await db.memory_related(new_id)
    assert any(r["relation"] == "supersedes" and r["id"] == old_id for r in related)


async def test_supersede_with_overrides(db: SqliteDatabase) -> None:
    old_id = await db.store("Old content", memory_type="fact", tags=["old"])
    new_id = await db.supersede(old_id, "New content", memory_type="preference", tags=["new"])

    new_mem = await db.get(new_id)
    assert new_mem is not None
    assert new_mem.memory_type == "preference"
    assert new_mem.tags == ["new"]


async def test_supersede_nonexistent(db: SqliteDatabase) -> None:
    with pytest.raises(ValueError, match="not found"):
        await db.supersede("nonexistent", "New content")


async def test_supersede_atomicity(db: SqliteDatabase) -> None:
    """supersede() is all-or-nothing: link insert failure must roll back the new memory row.

    A SQLite trigger raises an error when a 'supersedes' link is inserted,
    simulating a real mid-transaction failure without any mocking.
    The new memory row must not be committed when the operation fails.
    """
    old_id = await db.store("old content", memory_type="fact")

    # Install a real SQLite trigger that rejects 'supersedes' link inserts.
    await db.db.execute(
        """
        CREATE TRIGGER reject_supersedes
        BEFORE INSERT ON memory_links
        WHEN NEW.relation = 'supersedes'
        BEGIN
            SELECT RAISE(FAIL, 'trigger: supersedes link rejected');
        END
        """
    )
    await db.db.commit()

    with pytest.raises(Exception, match="supersedes link rejected"):
        await db.supersede(old_id, "new content")

    # The interrupted supersede left uncommitted rows in the pending transaction.
    # Rolling back clears them — confirming no partial commit occurred.
    await db.db.rollback()

    # Drop the trigger so remaining assertions use normal DB behaviour.
    await db.db.execute("DROP TRIGGER reject_supersedes")
    await db.db.commit()

    # The new memory must NOT exist — all inserts share one transaction in supersede().
    cursor = await db.db.execute("SELECT COUNT(*) FROM memories WHERE content = 'new content'")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0, "New memory must not be persisted when link insert fails"


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
