"""SqliteDatabase — holds the aiosqlite connection and ALL query methods."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite
import sqlite_vec

from rekal.models import ContextResult, HealthReport, MemoryResult
from rekal.scoring import RawScores, ScoringWeights, combine_scores

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from rekal.embeddings import EmbeddingFunc

# SQLite parameter types — the union of all types sqlite3 accepts as bind values.
SqlParam = str | int | float | bytes | None

SCHEMA = """\
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    project TEXT,
    tags TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, project)
    VALUES ('delete', old.rowid, old.content, old.tags, old.project);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, project)
    VALUES ('delete', old.rowid, old.content, old.tags, old.project);
    INSERT INTO memories_fts(rowid, content, tags, project)
    VALUES (new.rowid, new.content, new.tags, new.project);
END;
"""

VEC_TABLE_SQL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
    "id TEXT PRIMARY KEY, embedding float[%d])"
)


async def memories_columns(db: aiosqlite.Connection) -> set[str]:
    """Column names of the memories table; empty set when it doesn't exist."""
    cols: set[str] = set()
    async with db.execute("PRAGMA table_info(memories)") as cursor:
        async for row in cursor:
            cols.add(row[1])
    return cols


async def has_memory_links(db: aiosqlite.Connection) -> bool:
    """Whether the legacy memory_links table exists in this DB."""
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_links'"
    ) as cursor:
        return await cursor.fetchone() is not None


async def migrate_to_minimal(db: aiosqlite.Connection) -> None:
    """Rebuild a pre-minimal DB into the minimal schema, preserving content.

    Detection is by shape: an existing ``memories`` table that still has the
    old ``memory_type`` column. Idempotent — a migrated or fresh DB is a
    no-op. What carries over: durable, non-superseded rows (id, content,
    project, tags, timestamps) and their embeddings in ``memory_vec``
    (no re-embed). What is dropped, deliberately:

    - superseded rows: their exclusion lived in ``memory_links``, which goes
      away — carrying them would resurrect stale knowledge in search
    - scratch-tier rows: ephemeral by contract
    - conversations, links, conflicts, per-project config: structure that
      never earned its token cost
    """
    cols = await memories_columns(db)
    if "memory_type" not in cols:
        return  # fresh DB or already minimal

    await db.execute(
        """
        CREATE TABLE memories_minimal (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            project TEXT,
            tags TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    # Copy first, exclude after: each legacy feature is handled by its own
    # conditional instead of one query that assumes every table exists.
    if "tier" in cols:
        await db.execute(
            """
            INSERT INTO memories_minimal (id, content, project, tags, created_at, updated_at)
            SELECT id, content, project, tags, created_at, updated_at
            FROM memories
            WHERE tier = 'durable'
            """
        )
    else:
        # Pre-tier DBs: every row is durable.
        await db.execute(
            """
            INSERT INTO memories_minimal (id, content, project, tags, created_at, updated_at)
            SELECT id, content, project, tags, created_at, updated_at
            FROM memories
            """
        )
    if await has_memory_links(db):
        # No links table means nothing was ever superseded.
        await db.execute(
            """
            DELETE FROM memories_minimal WHERE id IN (
                SELECT to_id FROM memory_links WHERE relation = 'supersedes'
            )
            """
        )

    # The old external-content FTS table references `memories`; drop both,
    # then swap in the minimal table. Dropping `memories` also drops its
    # triggers, and SCHEMA recreates FTS + triggers right after this runs.
    await db.execute("DROP TABLE IF EXISTS memories_fts")
    await db.execute("DROP TABLE memories")
    await db.execute("ALTER TABLE memories_minimal RENAME TO memories")

    await db.execute("DELETE FROM memory_vec WHERE id NOT IN (SELECT id FROM memories)")
    await db.execute("DROP TABLE IF EXISTS memory_links")
    await db.execute("DROP TABLE IF EXISTS conversation_links")
    await db.execute("DROP TABLE IF EXISTS conversations")
    await db.execute("DROP TABLE IF EXISTS project_config")
    await db.execute("DROP INDEX IF EXISTS idx_memories_expires_tier")


async def init_connection(db: aiosqlite.Connection, dimensions: int) -> None:
    """Load sqlite-vec, migrate old shapes, apply the schema, rebuild FTS."""
    db.row_factory = aiosqlite.Row
    await db.enable_load_extension(True)
    await db.load_extension(sqlite_vec.loadable_path())
    await db.enable_load_extension(False)
    old_shape = "memory_type" in await memories_columns(db)
    await migrate_to_minimal(db)
    await db.executescript(SCHEMA)
    await db.execute(VEC_TABLE_SQL % dimensions)
    if old_shape:
        # SCHEMA recreated an empty FTS index over the rebuilt table.
        await db.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
    await db.commit()


def quote_fts(query: str) -> str:
    """Wrap each token in FTS5 phrase quotes so the query is always treated as literal text."""
    tokens = query.replace('"', " ").replace("\x00", "").split()
    return " ".join(f'"{t}"' for t in tokens)


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def parse_tags(tags: str | None) -> list[str]:
    if not tags:
        return []
    try:
        return json.loads(tags)
    except (json.JSONDecodeError, TypeError):  # pragma: no cover
        return []


def row_to_memory(row: aiosqlite.Row) -> MemoryResult:
    return MemoryResult(
        id=row["id"],
        content=row["content"],
        project=row["project"],
        tags=parse_tags(row["tags"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def parse_days_since(timestamp: str, fallback: int) -> int:
    """Parse a datetime string and return days since then, or fallback on error."""
    try:
        dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        return (datetime.now(UTC) - dt.replace(tzinfo=UTC)).days
    except (ValueError, TypeError):  # pragma: no cover
        return fallback


@dataclass
class SqliteDatabase:
    db: aiosqlite.Connection
    embed: EmbeddingFunc

    @staticmethod
    async def create(
        db_path: str,
        embed: EmbeddingFunc,
        *,
        dimensions: int = 384,
    ) -> SqliteDatabase:
        # connect() starts a non-daemon worker thread, so a failure during
        # init (e.g. the path is not a SQLite file) must close the connection
        # or the orphaned thread blocks interpreter/pytest exit.
        db = await aiosqlite.connect(db_path)
        try:
            await init_connection(db, dimensions)
        except Exception:
            await db.close()
            raise
        return SqliteDatabase(db=db, embed=embed)

    async def close(self) -> None:
        await self.db.close()

    @staticmethod
    @asynccontextmanager
    async def session(
        db_path: str,
        embed: EmbeddingFunc,
        *,
        dimensions: int = 384,
    ) -> AsyncIterator[SqliteDatabase]:
        """Context-managed create()/close(): open a DB, close it on exit.

        Lets callers drop the ``db = await create(...); try: ... finally:
        await db.close()`` boilerplate for ``async with session(...) as db:``.
        """
        db = await SqliteDatabase.create(db_path, embed, dimensions=dimensions)
        try:
            yield db
        finally:
            await db.close()

    # ── Core ─────────────────────────────────────────────────────────

    async def store(
        self,
        content: str,
        *,
        project: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        memory_id = new_id()
        ts = now_utc()
        tags_json = json.dumps(tags) if tags else None
        embedding = self.embed(content)

        await self.db.execute(
            """
            INSERT INTO memories (id, content, project, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (memory_id, content, project, tags_json, ts, ts),
        )
        await self.db.execute(
            "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
            (memory_id, embedding),
        )
        await self.db.commit()
        return memory_id

    async def replace(
        self,
        old_id: str,
        content: str,
        *,
        project: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Store a new memory in place of an existing one.

        The old row is deleted, not linked: one topic, one memory. Project
        and tags fall back to the old memory's values when not given.
        """
        old = await self.get(old_id)
        if old is None:
            msg = f"Memory {old_id} not found"
            raise ValueError(msg)
        new_memory_id = await self.store(
            content,
            project=project if project is not None else old.project,
            tags=tags if tags is not None else (old.tags or None),
        )
        await self.delete(old_id)
        return new_memory_id

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project: str | None = None,
        weights: ScoringWeights,
        min_score: float = 0.0,
    ) -> list[MemoryResult]:
        """Hybrid FTS + vector + recency search.

        ``min_score`` is an inclusive floor: rows scoring exactly at it
        survive; only strictly lower scores are dropped.
        """
        embedding = self.embed(query)

        # Vector search — candidate IDs + distances.
        vec_rows: dict[str, float] = {}
        async with self.db.execute(
            """
            SELECT id, distance
            FROM memory_vec
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (embedding, limit * 3),
        ) as cursor:
            async for row in cursor:
                vec_rows[row["id"]] = row["distance"]

        # FTS search — candidate IDs + BM25 scores.
        fts_query = quote_fts(query)
        fts_rows: dict[str, float] = {}
        if fts_query:
            async with self.db.execute(
                """
                SELECT m.id, memories_fts.rank AS fts_rank
                FROM memories_fts
                JOIN memories m ON m.rowid = memories_fts.rowid
                WHERE memories_fts MATCH ?
                ORDER BY memories_fts.rank
                LIMIT ?
                """,
                (fts_query, limit * 3),
            ) as cursor:
                async for row in cursor:
                    fts_rows[row["id"]] = row["fts_rank"]

        # Merge candidates and fetch full rows — filter in Python, no dynamic SQL
        candidate_ids = set(vec_rows.keys()) | set(fts_rows.keys())
        if not candidate_ids:
            return []

        scored: list[tuple[float, MemoryResult]] = []
        for cid in candidate_ids:
            mem = await self.get(cid)
            if mem is None:
                continue  # pragma: no cover
            if mem.project != project:
                continue

            days = parse_days_since(mem.created_at, fallback=0)
            raw = RawScores(
                fts_score=fts_rows.get(cid, 0.0),
                vec_score=vec_rows.get(cid, 1.0),
                recency_days=max(0.0, float(days)),
            )
            score = combine_scores(raw, weights)
            if score < min_score:
                continue
            mem.score = score
            scored.append((score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in scored[:limit]]

    async def delete(self, memory_id: str) -> bool:
        await self.db.execute("DELETE FROM memory_vec WHERE id = ?", (memory_id,))
        cursor = await self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    async def prune(
        self,
        *,
        project: str | None = None,
        before: str | None = None,
        dry_run: bool = True,
    ) -> list[str]:
        """Bulk-delete memories matching scope filters; returns matched IDs.

        Requires at least one of project / before to avoid an accidental
        full wipe. ``before`` is an ISO timestamp ``YYYY-MM-DD HH:MM:SS``;
        memories with ``created_at`` strictly less than it are pruned.
        ``dry_run=True`` (default) returns the matching IDs without deleting.
        """
        if project is None and before is None:
            msg = "prune requires at least one filter: project or before"
            raise ValueError(msg)

        ids: list[str] = []
        async with self.db.execute(
            """
            SELECT id FROM memories
            WHERE (? IS NULL OR project = ?)
              AND (? IS NULL OR created_at < ?)
            """,
            (project, project, before, before),
        ) as cursor:
            async for row in cursor:
                ids.append(row["id"])

        if dry_run or not ids:
            return ids

        filter_params: tuple[SqlParam, ...] = (project, project, before, before)
        await self.db.execute(
            """
            DELETE FROM memory_vec WHERE id IN (
                SELECT id FROM memories
                WHERE (? IS NULL OR project = ?)
                  AND (? IS NULL OR created_at < ?)
            )
            """,
            filter_params,
        )
        await self.db.execute(
            """
            DELETE FROM memories
            WHERE (? IS NULL OR project = ?)
              AND (? IS NULL OR created_at < ?)
            """,
            filter_params,
        )
        await self.db.commit()
        return ids

    async def get(self, memory_id: str) -> MemoryResult | None:
        async with self.db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return row_to_memory(row)

    # ── Introspection (CLI surface) ──────────────────────────────────

    async def memory_timeline(
        self,
        *,
        project: str | None = None,
        limit: int = 20,
    ) -> list[MemoryResult]:
        results: list[MemoryResult] = []
        async with self.db.execute(
            """
            SELECT * FROM memories
            WHERE (? IS NULL OR project = ?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project, project, limit),
        ) as cursor:
            async for row in cursor:
                results.append(row_to_memory(row))
        return results

    async def memory_health(self) -> HealthReport:
        async def count_rows(sql: str) -> int:
            async with self.db.execute(sql) as c:
                row = await c.fetchone()
                return int(row[0]) if row else 0

        async def first_str(sql: str) -> str | None:
            async with self.db.execute(sql) as c:
                row = await c.fetchone()
                if row and row[0] is not None:
                    return str(row[0])
                return None

        total_memories = await count_rows("SELECT COUNT(*) FROM memories")
        oldest = await first_str("SELECT MIN(created_at) FROM memories")
        newest = await first_str("SELECT MAX(created_at) FROM memories")

        by_project: dict[str, int] = {}
        async with self.db.execute(
            """
            SELECT COALESCE(project, '<none>') as p, COUNT(*) as cnt
            FROM memories
            GROUP BY project
            """
        ) as cursor:
            async for row in cursor:
                by_project[row["p"]] = row["cnt"]

        return HealthReport(
            total_memories=total_memories,
            oldest_memory=oldest,
            newest_memory=newest,
            memories_by_project=by_project,
        )

    # ── Recall ───────────────────────────────────────────────────────

    async def build_context(
        self,
        query: str,
        *,
        project: str | None = None,
        limit: int = 10,
        weights: ScoringWeights,
        min_score: float = 0.0,
    ) -> ContextResult:
        memories = await self.search(
            query,
            limit=limit,
            project=project,
            weights=weights,
            min_score=min_score,
        )
        return ContextResult(query=query, memories=memories)
