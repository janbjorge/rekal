"""Tests for conversation operations and DAG traversal."""

from __future__ import annotations

from rekal.adapters.sqlite_adapter import SqliteDatabase


async def test_conversation_start(db: SqliteDatabase) -> None:
    conv_id = await db.conversation_start(title="Test", project="p1")
    assert conv_id is not None
    assert len(conv_id) > 0


async def test_conversation_follows_up(db: SqliteDatabase) -> None:
    c1 = await db.conversation_start(title="First")
    c2 = await db.conversation_start(title="Second", follows_up_on=c1)

    tree = await db.conversation_tree(c2)
    assert len(tree) == 1
    assert tree[0].from_id == c2
    assert tree[0].to_id == c1
    assert tree[0].relation == "follows_up_on"


async def test_conversation_branches(db: SqliteDatabase) -> None:
    c1 = await db.conversation_start(title="Main")
    await db.conversation_start(title="Branch", branches_from=c1)

    tree = await db.conversation_tree(c1)
    assert len(tree) == 1
    assert tree[0].relation == "branches_from"


async def test_conversation_tree_traversal(db: SqliteDatabase) -> None:
    c1 = await db.conversation_start(title="Root")
    c2 = await db.conversation_start(title="Child", follows_up_on=c1)
    c3 = await db.conversation_start(title="Grandchild", follows_up_on=c2)

    tree = await db.conversation_tree(c3)
    assert len(tree) >= 2  # Should find links through the chain


async def test_conversation_tree_empty(db: SqliteDatabase) -> None:
    c1 = await db.conversation_start(title="Isolated")
    tree = await db.conversation_tree(c1)
    assert tree == []


async def test_conversation_threads(db: SqliteDatabase) -> None:
    c1 = await db.conversation_start(title="Conv 1", project="p1")
    await db.conversation_start(title="Conv 2", project="p1")
    await db.store("Memory", conversation_id=c1)

    threads = await db.conversation_threads(project="p1")
    assert len(threads) == 2

    conv_with_mem = next(t for t in threads if t.id == c1)
    assert conv_with_mem.memory_count == 1


async def test_conversation_threads_limit(db: SqliteDatabase) -> None:
    for i in range(5):
        await db.conversation_start(title=f"Conv {i}")

    threads = await db.conversation_threads(limit=3)
    assert len(threads) == 3


async def test_conversation_tree_diamond(db: SqliteDatabase) -> None:
    """Test BFS dedup when multiple paths lead to the same node."""
    c1 = await db.conversation_start(title="Root")
    c2 = await db.conversation_start(title="Left", follows_up_on=c1)
    c3 = await db.conversation_start(title="Right", follows_up_on=c1)
    c4 = await db.conversation_start(title="Merge", follows_up_on=c2)
    # Also link c4 -> c3 to create diamond
    await db.db.execute(
        "INSERT INTO conversation_links (from_id, to_id, relation, created_at) "
        "VALUES (?, ?, 'follows_up_on', datetime('now'))",
        (c4, c3),
    )
    await db.db.commit()

    tree = await db.conversation_tree(c4)
    # Should have 4 links: c4->c2, c4->c3, c2->c1, c3->c1
    assert len(tree) == 4


async def test_conversation_stale(db: SqliteDatabase) -> None:
    # A conversation with no memories is immediately "stale" with days=0
    await db.conversation_start(title="Stale")
    stale = await db.conversation_stale(days=0)
    assert len(stale) >= 1


async def test_conversation_stale_with_old_memory(db: SqliteDatabase) -> None:
    c = await db.conversation_start(title="Old")
    await db.store("Old memory", conversation_id=c)
    # Backdate the memory to make it stale
    await db.db.execute(
        "UPDATE memories SET created_at = '2020-01-01 00:00:00' WHERE conversation_id = ?",
        (c,),
    )
    await db.db.commit()
    stale = await db.conversation_stale(days=1)
    stale_ids = [s.id for s in stale]
    assert c in stale_ids
    # Should have computed days_inactive from last_memory_at
    stale_conv = next(s for s in stale if s.id == c)
    assert stale_conv.days_inactive > 0


async def test_conversation_stale_with_recent_memory(db: SqliteDatabase) -> None:
    c = await db.conversation_start(title="Active")
    await db.store("Recent memory", conversation_id=c)

    stale = await db.conversation_stale(days=30)
    # Should not be stale since memory was just created
    stale_ids = [s.id for s in stale]
    assert c not in stale_ids
