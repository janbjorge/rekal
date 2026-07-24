"""Migration from the pre-minimal schema: real old-shape DB, no mocking."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import sqlite_vec

from rekal.adapters.sqlite_adapter import SqliteDatabase, quote_fts
from rekal.scoring import ScoringWeights
from tests.conftest import deterministic_embed

# Frozen copy of the schema as it was before the minimal-schema rework,
# so this test keeps exercising the real migration input even after the
# live SCHEMA moves on.
OLD_SCHEMA = """\
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    project TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS conversation_links (
    from_id TEXT NOT NULL REFERENCES conversations(id),
    to_id TEXT NOT NULL REFERENCES conversations(id),
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (from_id, to_id, relation)
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'fact',
    tier TEXT NOT NULL DEFAULT 'durable',
    project TEXT,
    conversation_id TEXT REFERENCES conversations(id),
    tags TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    tags,
    project,
    content='memories',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags, project)
    VALUES (new.rowid, new.content, new.tags, new.project);
END;

CREATE TABLE IF NOT EXISTS project_config (
    project TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (project, key)
);

CREATE TABLE IF NOT EXISTS memory_links (
    from_id TEXT NOT NULL REFERENCES memories(id),
    to_id TEXT NOT NULL REFERENCES memories(id),
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (from_id, to_id, relation)
);
"""

OLD_VEC_TABLE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
    "id TEXT PRIMARY KEY, embedding float[384])"
)

INSERT_MEMORY = """
INSERT INTO memories
    (id, content, memory_type, tier, project, conversation_id, tags,
     created_at, updated_at, expires_at, access_count)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

TS = "2026-01-02 03:04:05"


async def build_old_db(path: Path) -> None:
    """Create a realistic pre-minimal DB: durable, superseded, and scratch
    rows, embeddings, links, a conversation, and project config."""
    raw = await aiosqlite.connect(str(path))
    try:
        await raw.enable_load_extension(True)
        await raw.load_extension(sqlite_vec.loadable_path())
        await raw.enable_load_extension(False)
        await raw.executescript(OLD_SCHEMA)
        await raw.execute(OLD_VEC_TABLE)

        await raw.execute(
            "INSERT INTO conversations (id, title, started_at) VALUES ('conv1', 'Old chat', ?)",
            (TS,),
        )
        rows = [
            ("keep1", "Durable fact about routing", "fact", "durable", "proj", "conv1"),
            ("keep2", "Durable global preference", "preference", "durable", None, None),
            ("old1", "Outdated fact about routing", "fact", "durable", "proj", None),
            ("scratch1", "Ephemeral working note", "context", "scratch", None, None),
        ]
        for mid, content, mtype, tier, project, conv in rows:
            expires = "2999-12-31 23:59:59" if tier == "scratch" else None
            tags = '["routing"]' if mid == "keep1" else None
            await raw.execute(
                INSERT_MEMORY,
                (mid, content, mtype, tier, project, conv, tags, TS, TS, expires, 7),
            )
            await raw.execute(
                "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
                (mid, deterministic_embed(content)),
            )
        # keep1 supersedes old1 — old1 must not survive migration.
        await raw.execute(
            """
            INSERT INTO memory_links (from_id, to_id, relation, created_at)
            VALUES ('keep1', 'old1', 'supersedes', ?)
            """,
            (TS,),
        )
        await raw.execute(
            "INSERT INTO project_config (project, key, value) VALUES ('proj', 'w_fts', '0.8')"
        )
        await raw.commit()
    finally:
        await raw.close()


async def table_names(db: SqliteDatabase) -> set[str]:
    names: set[str] = set()
    async with db.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
        async for row in cursor:
            names.add(row["name"])
    return names


async def test_migration_carries_durable_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    await build_old_db(path)

    async with SqliteDatabase.session(str(path), deterministic_embed) as db:
        kept = await db.get("keep1")
        assert kept is not None
        assert kept.content == "Durable fact about routing"
        assert kept.project == "proj"
        assert kept.tags == ["routing"]
        assert kept.created_at == TS
        assert kept.updated_at == TS
        assert await db.get("keep2") is not None


async def test_migration_drops_superseded_and_scratch(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    await build_old_db(path)

    async with SqliteDatabase.session(str(path), deterministic_embed) as db:
        assert await db.get("old1") is None
        assert await db.get("scratch1") is None
        # Their embeddings are gone too.
        async with db.db.execute(
            "SELECT id FROM memory_vec WHERE id IN ('old1', 'scratch1')"
        ) as cursor:
            assert await cursor.fetchone() is None


async def test_migration_drops_dead_tables(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    await build_old_db(path)

    async with SqliteDatabase.session(str(path), deterministic_embed) as db:
        names = await table_names(db)
        for dead in ("conversations", "conversation_links", "memory_links", "project_config"):
            assert dead not in names
        assert "memories" in names
        assert "memory_vec" in names


async def test_migration_rebuilds_search(tmp_path: Path) -> None:
    """FTS is rebuilt over the migrated table and embeddings are carried."""
    path = tmp_path / "old.db"
    await build_old_db(path)

    async with SqliteDatabase.session(str(path), deterministic_embed) as db:
        # FTS: keyword lookup must hit the migrated rows.
        assert quote_fts("routing")
        results = await db.search("routing", project="proj", weights=ScoringWeights())
        assert [m.id for m in results] == ["keep1"]
        # Vec: identical text embeds to distance 0, so it must dominate.
        results = await db.search(
            "Durable global preference", weights=ScoringWeights(), min_score=0.5
        )
        assert "keep2" in [m.id for m in results]


async def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    await build_old_db(path)

    async with SqliteDatabase.session(str(path), deterministic_embed) as db:
        assert await db.get("keep1") is not None
    # Second open: already minimal, migration is a no-op.
    async with SqliteDatabase.session(str(path), deterministic_embed) as db:
        assert await db.get("keep1") is not None
        results = await db.search("routing", project="proj", weights=ScoringWeights())
        assert [m.id for m in results] == ["keep1"]


async def test_migration_pre_tier_db(tmp_path: Path) -> None:
    """DBs older than the tier column migrate too: every row is durable."""
    path = tmp_path / "ancient.db"
    raw = await aiosqlite.connect(str(path))
    try:
        await raw.enable_load_extension(True)
        await raw.load_extension(sqlite_vec.loadable_path())
        await raw.enable_load_extension(False)
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
            CREATE TABLE memory_links (
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (from_id, to_id, relation)
            );
            """
        )
        await raw.execute(OLD_VEC_TABLE)
        await raw.execute(
            "INSERT INTO memories (id, content, created_at, updated_at) "
            "VALUES ('ancient1', 'Pre-tier row', ?, ?)",
            (TS, TS),
        )
        await raw.execute(
            "INSERT INTO memory_vec (id, embedding) VALUES ('ancient1', ?)",
            (deterministic_embed("Pre-tier row"),),
        )
        await raw.commit()
    finally:
        await raw.close()

    async with SqliteDatabase.session(str(path), deterministic_embed) as db:
        mem = await db.get("ancient1")
        assert mem is not None
        assert mem.content == "Pre-tier row"


async def test_migration_retries_after_interrupted_run(tmp_path: Path) -> None:
    """A leftover memories_minimal from a crashed migration must not block the retry."""
    path = tmp_path / "crashed.db"
    await build_old_db(path)
    raw = await aiosqlite.connect(str(path))
    try:
        await raw.execute("CREATE TABLE memories_minimal (id TEXT PRIMARY KEY)")
        await raw.commit()
    finally:
        await raw.close()

    async with SqliteDatabase.session(str(path), deterministic_embed) as db:
        assert await db.get("keep1") is not None
        assert await db.get("old1") is None


async def test_migration_old_db_without_memory_links(tmp_path: Path) -> None:
    """Hand-built old-shape DBs may lack memory_links; migration must not crash."""
    path = tmp_path / "nolinks.db"
    raw = await aiosqlite.connect(str(path))
    try:
        await raw.enable_load_extension(True)
        await raw.load_extension(sqlite_vec.loadable_path())
        await raw.enable_load_extension(False)
        await raw.executescript(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL DEFAULT 'fact',
                project TEXT,
                tags TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        await raw.execute(OLD_VEC_TABLE)
        await raw.execute(
            "INSERT INTO memories (id, content, created_at, updated_at) "
            "VALUES ('lonely1', 'No links table here', ?, ?)",
            (TS, TS),
        )
        await raw.execute(
            "INSERT INTO memory_vec (id, embedding) VALUES ('lonely1', ?)",
            (deterministic_embed("No links table here"),),
        )
        await raw.commit()
    finally:
        await raw.close()

    async with SqliteDatabase.session(str(path), deterministic_embed) as db:
        mem = await db.get("lonely1")
        assert mem is not None
        assert mem.content == "No links table here"


async def test_fresh_db_untouched_by_migration(db: SqliteDatabase) -> None:
    """A fresh minimal DB round-trips through init without a rebuild."""
    mid = await db.store("Fresh row", project="p")
    assert await db.get(mid) is not None
    names = await table_names(db)
    assert "memories" in names
    assert "memory_links" not in names
