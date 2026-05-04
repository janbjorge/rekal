"""Tests for SqliteDatabase — all database operations directly."""

from __future__ import annotations

import pytest

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


async def test_prune_requires_filter(db: SqliteDatabase) -> None:
    with pytest.raises(ValueError, match="prune requires"):
        await db.prune()


async def test_prune_dry_run_returns_matches(db: SqliteDatabase) -> None:
    keep = await db.store("Keep me", project="other")
    drop1 = await db.store("Drop me", project="trash")
    drop2 = await db.store("Drop me too", project="trash")

    count, ids = await db.prune(project="trash", dry_run=True)
    assert count == 2
    assert set(ids) == {drop1, drop2}
    # Nothing actually deleted on dry run
    assert await db.get(drop1) is not None
    assert await db.get(drop2) is not None
    assert await db.get(keep) is not None


async def test_prune_by_project_deletes(db: SqliteDatabase) -> None:
    keep = await db.store("Keep me", project="other")
    drop = await db.store("Drop me", project="trash")
    await db.add_memory_link(drop, keep, "related_to")

    count, ids = await db.prune(project="trash", dry_run=False)
    assert count == 1
    assert ids == [drop]
    assert await db.get(drop) is None
    assert await db.get(keep) is not None
    # Link cleaned up
    assert await db.memory_related(keep) == []


async def test_prune_by_memory_type(db: SqliteDatabase) -> None:
    fact_id = await db.store("A fact", memory_type="fact", project="p")
    pref_id = await db.store("A preference", memory_type="preference", project="p")

    count, _ = await db.prune(memory_type="fact", dry_run=False)
    assert count == 1
    assert await db.get(fact_id) is None
    assert await db.get(pref_id) is not None


async def test_prune_by_before_timestamp(db: SqliteDatabase) -> None:
    old_id = await db.store("Old")
    # Force created_at backwards
    await db.db.execute(
        "UPDATE memories SET created_at = '2000-01-01 00:00:00' WHERE id = ?",
        (old_id,),
    )
    await db.db.commit()
    new_id = await db.store("New")

    count, ids = await db.prune(before="2020-01-01 00:00:00", dry_run=False)
    assert count == 1
    assert ids == [old_id]
    assert await db.get(old_id) is None
    assert await db.get(new_id) is not None


async def test_prune_no_matches_skips_delete(db: SqliteDatabase) -> None:
    await db.store("Stay", project="alive")
    count, ids = await db.prune(project="ghost", dry_run=False)
    assert count == 0
    assert ids == []


async def test_prune_combined_filters(db: SqliteDatabase) -> None:
    a = await db.store("trash fact", memory_type="fact", project="trash")
    b = await db.store("trash pref", memory_type="preference", project="trash")
    c = await db.store("other fact", memory_type="fact", project="other")

    count, ids = await db.prune(project="trash", memory_type="fact", dry_run=False)
    assert count == 1
    assert ids == [a]
    assert await db.get(a) is None
    assert await db.get(b) is not None
    assert await db.get(c) is not None


# ── Tier + expiry ───────────────────────────────────────────────────


async def test_store_defaults_to_durable_tier(db: SqliteDatabase) -> None:
    mid = await db.store("durable by default")
    mem = await db.get(mid)
    assert mem is not None
    assert mem.tier == "durable"
    assert mem.expires_at is None


async def test_store_scratch_tier_with_expiry(db: SqliteDatabase) -> None:
    future = "2999-12-31 23:59:59"
    mid = await db.store("scratch note", tier="scratch", expires_at=future)
    mem = await db.get(mid)
    assert mem is not None
    assert mem.tier == "scratch"
    assert mem.expires_at == future


async def test_search_excludes_expired(db: SqliteDatabase) -> None:
    past = "2000-01-01 00:00:00"
    future = "2999-12-31 23:59:59"
    fresh = await db.store("fresh scratch note", tier="scratch", expires_at=future)
    await db.store("stale scratch note", tier="scratch", expires_at=past)

    weights = await db.resolve_weights(None)
    results = await db.search("scratch note", weights=weights)
    ids = [m.id for m in results]
    assert fresh in ids
    assert all(m.expires_at != past for m in results)


async def test_timeline_excludes_expired(db: SqliteDatabase) -> None:
    past = "2000-01-01 00:00:00"
    fresh = await db.store("kept", project="t")
    await db.store("expired", project="t", tier="scratch", expires_at=past)

    rows = await db.memory_timeline(project="t")
    ids = [m.id for m in rows]
    assert fresh in ids
    assert len(ids) == 1


async def test_topics_excludes_expired(db: SqliteDatabase) -> None:
    past = "2000-01-01 00:00:00"
    await db.store("kept", project="t", memory_type="fact")
    await db.store("expired", project="t", memory_type="fact", tier="scratch", expires_at=past)

    topics = await db.memory_topics(project="t")
    fact_topic = next(t for t in topics if t.topic == "fact")
    assert fact_topic.count == 1


async def test_similar_excludes_expired(db: SqliteDatabase) -> None:
    past = "2000-01-01 00:00:00"
    anchor = await db.store("alpha beta gamma")
    fresh = await db.store("alpha beta delta")
    await db.store("alpha beta omega", tier="scratch", expires_at=past)

    results = await db.memory_similar(anchor, limit=5)
    ids = {m.id for m in results}
    assert fresh in ids
    assert all(m.expires_at != past for m in results)


async def test_get_returns_expired(db: SqliteDatabase) -> None:
    past = "2000-01-01 00:00:00"
    mid = await db.store("expired", tier="scratch", expires_at=past)
    mem = await db.get(mid)
    assert mem is not None
    assert mem.expires_at == past


async def test_sweep_expired_no_op_on_empty(db: SqliteDatabase) -> None:
    deleted = await db.sweep_expired()
    assert deleted == 0


async def test_sweep_expired_keeps_durable(db: SqliteDatabase) -> None:
    durable = await db.store("keep me")
    deleted = await db.sweep_expired()
    assert deleted == 0
    assert await db.get(durable) is not None


async def test_sweep_expired_keeps_future_scratch(db: SqliteDatabase) -> None:
    future = "2999-12-31 23:59:59"
    fresh = await db.store("fresh", tier="scratch", expires_at=future)
    deleted = await db.sweep_expired()
    assert deleted == 0
    assert await db.get(fresh) is not None


async def test_sweep_expired_drops_past_scratch(db: SqliteDatabase) -> None:
    past = "2000-01-01 00:00:00"
    stale = await db.store("stale", tier="scratch", expires_at=past)
    durable = await db.store("kept")

    deleted = await db.sweep_expired()
    assert deleted == 1
    assert await db.get(stale) is None
    assert await db.get(durable) is not None


async def test_sweep_expired_cascades_links(db: SqliteDatabase) -> None:
    past = "2000-01-01 00:00:00"
    stale = await db.store("stale", tier="scratch", expires_at=past)
    other = await db.store("other")
    await db.add_memory_link(stale, other, "related_to")
    await db.add_memory_link(other, stale, "related_to")

    await db.sweep_expired()
    related = await db.memory_related(other)
    assert related == []


async def test_sweep_expired_cascades_vec(db: SqliteDatabase) -> None:
    past = "2000-01-01 00:00:00"
    stale = await db.store("stale", tier="scratch", expires_at=past)

    await db.sweep_expired()
    async with db.db.execute("SELECT id FROM memory_vec WHERE id = ?", (stale,)) as cursor:
        row = await cursor.fetchone()
        assert row is None


async def test_supersede_preserves_tier_and_expiry(db: SqliteDatabase) -> None:
    future = "2999-12-31 23:59:59"
    old = await db.store("v1", tier="scratch", expires_at=future)
    new = await db.supersede(old, "v2")
    mem = await db.get(new)
    assert mem is not None
    assert mem.tier == "scratch"
    assert mem.expires_at == future


async def test_migration_adds_columns_to_legacy_table() -> None:
    """ALTER TABLE path: a pre-tier memories table gets tier+expires_at."""
    import aiosqlite

    from rekal.adapters.sqlite_adapter import migrate_memories_table

    raw = await aiosqlite.connect(":memory:")
    raw.row_factory = aiosqlite.Row
    try:
        await raw.executescript(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL DEFAULT 'fact',
                project TEXT,
                conversation_id TEXT,
                tags TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed_at TEXT
            );
            """
        )
        await raw.execute("INSERT INTO memories (id, content) VALUES ('legacy1', 'legacy row')")
        await raw.commit()

        await migrate_memories_table(raw)
        await raw.commit()

        cols: set[str] = set()
        async with raw.execute("PRAGMA table_info(memories)") as cursor:
            async for row in cursor:
                cols.add(row[1])
        assert "tier" in cols
        assert "expires_at" in cols

        async with raw.execute(
            "SELECT tier, expires_at FROM memories WHERE id = 'legacy1'"
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert row["tier"] == "durable"
            assert row["expires_at"] is None

        # Idempotent: a second run is a no-op.
        await migrate_memories_table(raw)
        await raw.commit()
    finally:
        await raw.close()
