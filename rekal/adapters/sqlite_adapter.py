"""SqliteDatabase — holds the aiosqlite connection and ALL query methods."""

from __future__ import annotations

import json
import uuid
from collections import ChainMap
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite
import sqlite_vec

from rekal.models import (
    ConflictInfo,
    ContextResult,
    ConversationInfo,
    ConversationLink,
    HealthReport,
    MemoryResult,
    StaleConversation,
    TopicSummary,
)
from rekal.scoring import RawScores, ScoringWeights, combine_scores

if TYPE_CHECKING:
    import sqlite3

    from rekal.embeddings import EmbeddingFunc
    from rekal.models import MemoryRelation, MemoryType

# SQLite parameter types — the union of all types sqlite3 accepts as bind values.
SqlParam = str | int | float | bytes | None

SCHEMA = """\
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
    relation TEXT NOT NULL CHECK (relation IN (
        'follows_up_on', 'branches_from', 'contradicts', 'merges'
    )),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (from_id, to_id, relation)
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'fact',
    project TEXT,
    conversation_id TEXT REFERENCES conversations(id),
    tags TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
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

CREATE TABLE IF NOT EXISTS project_config (
    project TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (project, key)
);

CREATE TABLE IF NOT EXISTS memory_links (
    from_id TEXT NOT NULL REFERENCES memories(id),
    to_id TEXT NOT NULL REFERENCES memories(id),
    relation TEXT NOT NULL CHECK (relation IN ('supersedes', 'contradicts', 'related_to')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (from_id, to_id, relation)
);
"""

VEC_TABLE_SQL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
    "id TEXT PRIMARY KEY, embedding float[%d])"
)


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


def row_to_memory(row: aiosqlite.Row, score: float | None = None) -> MemoryResult:
    return MemoryResult(
        id=row["id"],
        content=row["content"],
        memory_type=row["memory_type"],
        project=row["project"],
        conversation_id=row["conversation_id"],
        tags=parse_tags(row["tags"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        access_count=row["access_count"],
        last_accessed_at=row["last_accessed_at"],
        score=score,
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
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row

        def load_vec(conn: sqlite3.Connection) -> None:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

        await db.execute("select 1")  # ensure connection is open
        await db._execute(load_vec, db._conn)  # type: ignore[arg-type]
        await db.executescript(SCHEMA)
        await db.execute(VEC_TABLE_SQL % dimensions)
        await db.commit()
        return SqliteDatabase(db=db, embed=embed)

    async def close(self) -> None:
        await self.db.close()

    # ── Config ────────────────────────────────────────────────────────

    async def set_config(self, project: str, key: str, value: str) -> None:
        """Persist a scoring config value for a project.

        Uses INSERT OR UPDATE so calling with an existing (project, key) pair
        overwrites the previous value rather than raising.
        """
        await self.db.execute(
            """
            INSERT INTO project_config (project, key, value)
            VALUES (?, ?, ?)
            ON CONFLICT (project, key) DO UPDATE SET value = excluded.value
            """,
            (project, key, value),
        )
        await self.db.commit()

    async def get_config(self, project: str, key: str) -> str | None:
        """Look up one config entry for a project.

        Returns None when the project has no value for the given key,
        which lets callers fall back to defaults without special-casing.
        """
        async with self.db.execute(
            "SELECT value FROM project_config WHERE project = ? AND key = ?",
            (project, key),
        ) as cursor:
            row = await cursor.fetchone()
            return row["value"] if row else None

    async def get_project_config(self, project: str) -> dict[str, str]:
        """Load the full config dict for a project.

        Used by resolve_weights to seed the ChainMap with project-level
        scoring defaults before per-call overrides are applied.
        """
        result: dict[str, str] = {}
        async with self.db.execute(
            "SELECT key, value FROM project_config WHERE project = ?",
            (project,),
        ) as cursor:
            async for row in cursor:
                result[row["key"]] = row["value"]
        return result

    async def delete_config(self, project: str, key: str) -> bool:
        """Remove a config entry, returning True if it existed.

        After deletion the project falls back to the hardcoded default
        for that key on subsequent searches.
        """
        cursor = await self.db.execute(
            "DELETE FROM project_config WHERE project = ? AND key = ?",
            (project, key),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def resolve_weights(
        self,
        project: str | None,
        *,
        w_fts: float | None = None,
        w_vec: float | None = None,
        w_recency: float | None = None,
        half_life: float | None = None,
        file_config: dict[str, float] | None = None,
    ) -> ScoringWeights:
        """Build a ScoringWeights using a four-level precedence chain.

        Precedence (highest first):
          1. Per-call overrides — explicit values passed to search/build_context.
          2. Project config    — persisted in project_config table via set_config.
          3. File config       — loaded from ``.rekal/config.yml`` in the project.
          4. Hardcoded defaults — ScoringWeights field defaults (0.4/0.4/0.2/30).

        A ChainMap merges layers 1-3; pydantic fills layer 4 for any
        keys still missing. Pydantic also coerces DB strings to floats.
        """
        per_call: dict[str, str | float] = {
            k: v
            for k, v in [
                ("w_fts", w_fts),
                ("w_vec", w_vec),
                ("w_recency", w_recency),
                ("half_life", half_life),
            ]
            if v is not None
        }
        project_config: dict[str, str | float] = (
            dict(await self.get_project_config(project)) if project else {}
        )
        file_defaults: dict[str, str | float] = dict(file_config or {})
        merged = ChainMap(per_call, project_config, file_defaults)
        return ScoringWeights.model_validate(merged)

    # ── Core ─────────────────────────────────────────────────────────

    async def store(
        self,
        content: str,
        *,
        memory_type: MemoryType = "fact",
        project: str | None = None,
        conversation_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        memory_id = new_id()
        ts = now_utc()
        tags_json = json.dumps(tags) if tags else None
        embedding = self.embed(content)

        await self.db.execute(
            """
            INSERT INTO memories
                (id, content, memory_type, project, conversation_id, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, content, memory_type, project, conversation_id, tags_json, ts, ts),
        )
        await self.db.execute(
            "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
            (memory_id, embedding),
        )
        await self.db.commit()
        return memory_id

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project: str | None = None,
        memory_type: MemoryType | None = None,
        conversation_id: str | None = None,
        weights: ScoringWeights,
    ) -> list[MemoryResult]:
        embedding = self.embed(query)

        # Vector search — get candidate IDs + distances
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

        # FTS search — get candidate IDs + BM25 scores
        # Quote each token so FTS5 special chars (., -, :, etc.) don't cause syntax errors
        fts_query = " ".join(f'"{token}"' for token in query.split() if token)
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
            if project is not None and mem.project != project:
                continue
            if memory_type is not None and mem.memory_type != memory_type:
                continue
            if conversation_id is not None and mem.conversation_id != conversation_id:
                continue

            days = parse_days_since(mem.created_at, fallback=0)
            raw = RawScores(
                fts_score=fts_rows.get(cid, 0.0),
                vec_score=vec_rows.get(cid, 1.0),
                recency_days=max(0.0, float(days)),
            )
            score = combine_scores(raw, weights)
            mem.score = score
            scored.append((score, mem))

        # Update access counts
        ts = now_utc()
        for _, mem in scored:
            await self.db.execute(
                """
                UPDATE memories
                SET access_count = access_count + 1, last_accessed_at = ?
                WHERE id = ?
                """,
                (ts, mem.id),
            )
        await self.db.commit()

        scored.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in scored[:limit]]

    async def delete(self, memory_id: str) -> bool:
        await self.db.execute("DELETE FROM memory_vec WHERE id = ?", (memory_id,))
        await self.db.execute(
            "DELETE FROM memory_links WHERE from_id = ? OR to_id = ?",
            (memory_id, memory_id),
        )
        cursor = await self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    async def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        tags: list[str] | None = None,
        memory_type: MemoryType | None = None,
    ) -> bool:
        if content is None and tags is None and memory_type is None:
            return False

        tags_json = json.dumps(tags) if tags is not None else None
        ts = now_utc()

        cursor = await self.db.execute(
            """
            UPDATE memories SET
                content = COALESCE(?, content),
                tags = COALESCE(?, tags),
                memory_type = COALESCE(?, memory_type),
                updated_at = ?
            WHERE id = ?
            """,
            (content, tags_json, memory_type, ts, memory_id),
        )

        if content is not None and cursor.rowcount > 0:
            embedding = self.embed(content)
            await self.db.execute(
                "UPDATE memory_vec SET embedding = ? WHERE id = ?",
                (embedding, memory_id),
            )

        await self.db.commit()
        return cursor.rowcount > 0

    async def get(self, memory_id: str) -> MemoryResult | None:
        async with self.db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return row_to_memory(row)

    # ── Introspection ────────────────────────────────────────────────

    async def memory_similar(self, memory_id: str, *, limit: int = 5) -> list[MemoryResult]:
        async with self.db.execute(
            "SELECT embedding FROM memory_vec WHERE id = ?", (memory_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return []
            embedding = row["embedding"]

        results: list[MemoryResult] = []
        async with self.db.execute(
            """
            SELECT id, distance
            FROM memory_vec
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (embedding, limit + 1),
        ) as cursor:
            async for row in cursor:
                if row["id"] == memory_id:
                    continue
                mem = await self.get(row["id"])
                if mem is not None:
                    mem.score = 1.0 - row["distance"]
                    results.append(mem)
        return results[:limit]

    async def memory_topics(self, *, project: str | None = None) -> list[TopicSummary]:
        results: list[TopicSummary] = []
        async with self.db.execute(
            """
            SELECT memory_type AS topic, COUNT(*) AS cnt, MAX(created_at) AS latest
            FROM memories
            WHERE (? IS NULL OR project = ?)
            GROUP BY memory_type
            ORDER BY cnt DESC
            """,
            (project, project),
        ) as cursor:
            async for row in cursor:
                results.append(
                    TopicSummary(topic=row["topic"], count=row["cnt"], latest=row["latest"])
                )
        return results

    async def memory_timeline(
        self,
        *,
        project: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 20,
    ) -> list[MemoryResult]:
        results: list[MemoryResult] = []
        async with self.db.execute(
            """
            SELECT * FROM memories
            WHERE (? IS NULL OR project = ?)
              AND (? IS NULL OR created_at >= ?)
              AND (? IS NULL OR created_at <= ?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project, project, start, start, end, end, limit),
        ) as cursor:
            async for row in cursor:
                results.append(row_to_memory(row))
        return results

    async def memory_related(self, memory_id: str) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        async with self.db.execute(
            """
            SELECT ml.relation, ml.to_id, m.content
            FROM memory_links ml
            JOIN memories m ON m.id = ml.to_id
            WHERE ml.from_id = ?
            """,
            (memory_id,),
        ) as cursor:
            async for row in cursor:
                results.append(
                    {
                        "relation": row["relation"],
                        "id": row["to_id"],
                        "content": row["content"],
                    }
                )
        async with self.db.execute(
            """
            SELECT ml.relation, ml.from_id, m.content
            FROM memory_links ml
            JOIN memories m ON m.id = ml.from_id
            WHERE ml.to_id = ?
            """,
            (memory_id,),
        ) as cursor:
            async for row in cursor:
                results.append(
                    {
                        "relation": row["relation"],
                        "id": row["from_id"],
                        "content": row["content"],
                    }
                )
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
        total_conversations = await count_rows("SELECT COUNT(*) FROM conversations")
        total_links = await count_rows("SELECT COUNT(*) FROM memory_links")
        total_conflicts = await count_rows(
            "SELECT COUNT(*) FROM memory_links WHERE relation = 'contradicts'"
        )
        oldest = await first_str("SELECT MIN(created_at) FROM memories")
        newest = await first_str("SELECT MAX(created_at) FROM memories")

        by_type: dict[str, int] = {}
        async with self.db.execute(
            "SELECT memory_type, COUNT(*) as cnt FROM memories GROUP BY memory_type"
        ) as cursor:
            async for row in cursor:
                by_type[row["memory_type"]] = row["cnt"]

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
            total_conversations=total_conversations,
            total_links=total_links,
            total_conflicts=total_conflicts,
            oldest_memory=oldest,
            newest_memory=newest,
            memories_by_type=by_type,
            memories_by_project=by_project,
        )

    async def memory_conflicts(self, *, project: str | None = None) -> list[ConflictInfo]:
        results: list[ConflictInfo] = []
        async with self.db.execute(
            """
            SELECT ml.from_id, m1.content AS from_content,
                   ml.to_id, m2.content AS to_content,
                   ml.relation, ml.created_at
            FROM memory_links ml
            JOIN memories m1 ON m1.id = ml.from_id
            JOIN memories m2 ON m2.id = ml.to_id
            WHERE ml.relation = 'contradicts'
              AND (? IS NULL OR m1.project = ? OR m2.project = ?)
            """,
            (project, project, project),
        ) as cursor:
            async for row in cursor:
                results.append(
                    ConflictInfo(
                        memory_id=row["from_id"],
                        content=row["from_content"],
                        related_id=row["to_id"],
                        related_content=row["to_content"],
                        relation=row["relation"],
                        created_at=row["created_at"],
                    )
                )
        return results

    # ── Smart Write ──────────────────────────────────────────────────

    async def supersede(
        self,
        old_id: str,
        new_content: str,
        *,
        memory_type: MemoryType | None = None,
        project: str | None = None,
        conversation_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        old = await self.get(old_id)
        if old is None:
            msg = f"Memory {old_id} not found"
            raise ValueError(msg)

        new_id_val = new_id()
        ts = now_utc()
        effective_tags = tags if tags is not None else old.tags
        tags_json = json.dumps(effective_tags) if effective_tags else None
        embedding = self.embed(new_content)

        await self.db.execute(
            """
            INSERT INTO memories
                (id, content, memory_type, project, conversation_id, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id_val,
                new_content,
                memory_type or old.memory_type,
                project or old.project,
                conversation_id or old.conversation_id,
                tags_json,
                ts,
                ts,
            ),
        )
        await self.db.execute(
            "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
            (new_id_val, embedding),
        )
        await self.db.execute(
            """
            INSERT INTO memory_links (from_id, to_id, relation, created_at)
            VALUES (?, ?, 'supersedes', ?)
            """,
            (new_id_val, old_id, ts),
        )
        await self.db.commit()
        return new_id_val

    async def add_memory_link(
        self,
        from_id: str,
        to_id: str,
        relation: MemoryRelation,
    ) -> None:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO memory_links (from_id, to_id, relation, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (from_id, to_id, relation, now_utc()),
        )
        await self.db.commit()

    async def build_context(
        self,
        query: str,
        *,
        project: str | None = None,
        limit: int = 10,
        weights: ScoringWeights,
    ) -> ContextResult:
        memories = await self.search(query, limit=limit, project=project, weights=weights)
        conflicts = await self.memory_conflicts(project=project)

        if memories:
            oldest = min(m.created_at for m in memories)
            newest = max(m.created_at for m in memories)
            timeline_summary = f"{len(memories)} memories from {oldest} to {newest}"
        else:
            timeline_summary = "No memories found"

        return ContextResult(
            query=query,
            memories=memories,
            conflicts=conflicts,
            timeline_summary=timeline_summary,
        )

    # ── Conversations ────────────────────────────────────────────────

    async def conversation_start(
        self,
        *,
        title: str | None = None,
        project: str | None = None,
        follows_up_on: str | None = None,
        branches_from: str | None = None,
    ) -> str:
        conv_id = new_id()
        ts = now_utc()
        await self.db.execute(
            """
            INSERT INTO conversations (id, title, project, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (conv_id, title, project, ts),
        )
        if follows_up_on:
            await self.db.execute(
                """
                INSERT INTO conversation_links (from_id, to_id, relation, created_at)
                VALUES (?, ?, 'follows_up_on', ?)
                """,
                (conv_id, follows_up_on, ts),
            )
        if branches_from:
            await self.db.execute(
                """
                INSERT INTO conversation_links (from_id, to_id, relation, created_at)
                VALUES (?, ?, 'branches_from', ?)
                """,
                (conv_id, branches_from, ts),
            )
        await self.db.commit()
        return conv_id

    async def conversation_tree(self, conversation_id: str) -> list[ConversationLink]:
        results: list[ConversationLink] = []
        visited: set[str] = set()
        queue = [conversation_id]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            async with self.db.execute(
                "SELECT * FROM conversation_links WHERE from_id = ? OR to_id = ?",
                (current, current),
            ) as cursor:
                async for row in cursor:
                    link = ConversationLink(
                        from_id=row["from_id"],
                        to_id=row["to_id"],
                        relation=row["relation"],
                        created_at=row["created_at"],
                    )
                    results.append(link)
                    other = row["to_id"] if row["from_id"] == current else row["from_id"]
                    if other not in visited:
                        queue.append(other)

        # Deduplicate
        seen: set[tuple[str, str, str]] = set()
        unique: list[ConversationLink] = []
        for link in results:
            key = (link.from_id, link.to_id, link.relation)
            if key not in seen:
                seen.add(key)
                unique.append(link)
        return unique

    async def conversation_threads(
        self, *, project: str | None = None, limit: int = 20
    ) -> list[ConversationInfo]:
        results: list[ConversationInfo] = []
        async with self.db.execute(
            """
            SELECT c.*, COUNT(m.id) AS memory_count
            FROM conversations c
            LEFT JOIN memories m ON m.conversation_id = c.id
            WHERE (? IS NULL OR c.project = ?)
            GROUP BY c.id
            ORDER BY c.started_at DESC
            LIMIT ?
            """,
            (project, project, limit),
        ) as cursor:
            async for row in cursor:
                results.append(
                    ConversationInfo(
                        id=row["id"],
                        title=row["title"],
                        project=row["project"],
                        started_at=row["started_at"],
                        memory_count=row["memory_count"],
                    )
                )
        return results

    async def conversation_stale(self, *, days: int = 30) -> list[StaleConversation]:
        results: list[StaleConversation] = []
        async with self.db.execute(
            """
            SELECT c.id, c.title, c.project, c.started_at,
                   MAX(m.created_at) AS last_memory_at
            FROM conversations c
            LEFT JOIN memories m ON m.conversation_id = c.id
            GROUP BY c.id
            HAVING last_memory_at IS NULL
                OR julianday('now') - julianday(last_memory_at) > ?
            ORDER BY last_memory_at ASC
            """,
            (days,),
        ) as cursor:
            async for row in cursor:
                last: str | None = row["last_memory_at"]
                if last:
                    inactive = parse_days_since(last, fallback=days)
                else:
                    inactive = parse_days_since(row["started_at"], fallback=days)

                results.append(
                    StaleConversation(
                        id=row["id"],
                        title=row["title"],
                        project=row["project"],
                        started_at=row["started_at"],
                        last_memory_at=last,
                        days_inactive=inactive,
                    )
                )
        return results
