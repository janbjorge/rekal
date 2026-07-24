# rekal data model

One SQLite file (`~/.rekal/memory.db`), three tables. Keep this file in
sync with `SCHEMA` in `rekal/adapters/sqlite_adapter.py`.

> **Why this is small.** Earlier versions carried conversations, a memory
> link graph, a scratch tier with TTLs, memory types, access counters, and
> a per-project config table. Benchmarks showed none of it earned its
> cost: the structure existed mostly to be maintained, and the instructions
> explaining it cost tokens every session. The current model is the minimal
> core that hybrid recall actually needs. Structure gets re-added only when
> evidence forces it.

## Tables

| Table | Holds |
|---|---|
| `memories` | the atomic unit: content + scope + tags + timestamps |
| `memories_fts` | FTS5 keyword index, trigger-synced to `memories` |
| `memory_vec` | sqlite-vec 384-dim embedding, 1:1 with `memories` (synced in Python, no trigger) |

### `memories`

```sql
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    project TEXT,
    tags TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | TEXT | none | 16-hex from `uuid4().hex[:16]` |
| `content` | TEXT | none | Distilled fact. Caveman-compressed. 1-2 sentences. |
| `project` | TEXT | NULL | Free-form scope. NULL = global memory. |
| `tags` | TEXT | NULL | JSON-encoded `list[str]`. NULL = no tags. Decoded by `parse_tags`. |
| `created_at` | TEXT | `datetime('now')` | ISO-8601 UTC, `YYYY-MM-DD HH:MM:SS`. Used by recency scoring. |
| `updated_at` | TEXT | `datetime('now')` | Set on insert; kept for provenance. |

### `memories_fts`

External-content FTS5 table over `content`, `tags`, `project`, kept in
sync by three triggers (`memories_ai` / `memories_ad` / `memories_au`).

### `memory_vec`

vec0 virtual table `(id TEXT PRIMARY KEY, embedding float[384])`.
No trigger support on virtual tables, so every write path (`store`,
`replace`, `delete`, `prune`) maintains it by hand.

## Write semantics

- `store(content, project?, tags?)` → insert + embed.
- `replace(old_id, content, ...)` → store new, delete old. No link graph:
  one topic, one memory. Project/tags inherit from the old row unless
  overridden. This backs the `memory_store(replaces=...)` tool.
- `delete(id)` / `prune(project?/before?)` → remove rows + their vec rows.

## Invariants

| Invariant | Enforced by |
|---|---|
| Tags are JSON-encoded `list[str]` | `db.store` JSON-encodes; `parse_tags` decodes. Bad JSON falls back to `[]`. |
| Vector dim matches `memory_vec` declaration | `FastEmbedder.dimensions` passed to `SqliteDatabase.create`. Mismatch → vec0 raises at insert. |
| Timestamps are `'YYYY-MM-DD HH:MM:SS'` UTC strings | `now_utc()`. Compared lexicographically, which works because the format is fixed-width. |
| `memory_vec` stays 1:1 with `memories` | Hand-written cascade in every write path (no triggers on virtual tables). |

## Migration from the old schema

`migrate_to_minimal` (`sqlite_adapter.py`) runs on every open and detects
the pre-minimal shape by column (`memory_type` still present). It rebuilds
`memories` with the minimal columns and then lets `SCHEMA` recreate FTS
(+ a full index rebuild). One-way; idempotent.

Carried over:

- durable, non-superseded rows (id, content, project, tags, timestamps)
- their embeddings in `memory_vec` — no re-embed

Dropped, deliberately:

- superseded rows: their exclusion lived in `memory_links`, which no
  longer exists — carrying them would resurrect stale knowledge in search
- scratch-tier rows: ephemeral by contract
- `conversations`, `conversation_links`, `memory_links`, `project_config`
  tables, and the bookkeeping columns (`memory_type`, `tier`,
  `conversation_id`, `expires_at`, `access_count`, `last_accessed_at`)

Pre-tier DBs (no `tier` column at all) migrate too: every row is treated
as durable.

## Query lifecycle (recall)

1. Embed the query; vec lookup `k = limit × 3`.
2. `quote_fts(query)`; FTS lookup `LIMIT limit × 3` (skipped when the
   query has no usable tokens).
3. Union candidates, fetch rows, filter on `project` (strict equality) in
   Python.
4. Score (`combine_scores`), drop below `min_score`, sort, take `limit`.

Scoring internals: [docs/scoring.md](scoring.md).
