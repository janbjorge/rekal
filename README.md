# rekal

**Long-term memory for LLMs. One SQLite file, no cloud, no API keys.**

rekal is an [MCP](https://modelcontextprotocol.io) server that gives Claude Code persistent memory across sessions. Memories are stored locally in SQLite and retrieved with hybrid search (BM25 keywords + vector semantics + recency decay). Nothing leaves your machine.

```
Session 1:   "I prefer Ruff over Black"  → memory_store(...)
Session 47:  "Set up linting"            → memory_search("formatting preferences")
                                          ← "User prefers Ruff over Black" (0.92)
                                          Sets up Ruff without asking.
```

## Install

```bash
pip install rekal
```

or with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install rekal
```

Requires Python 3.11+. On first run, rekal creates `~/.rekal/memory.db` — a single file that holds everything.

## Setup

Two steps: add the MCP server, then install the skills plugin.

**1. Add the MCP server** — gives Claude Code the memory tools:

```bash
claude mcp add rekal rekal
```

**2. Install the skills plugin** — teaches Claude Code when and how to use those tools:

```bash
claude plugin marketplace add janbjorge/rekal
claude plugin install rekal-skills@rekal
```

The MCP server provides the tools. The skills drive the behavior — session capture, deduplication, hygiene. Both are required.

### Skills

| Skill | Trigger | What it does |
|-------|---------|-------------|
| `rekal-init` | `/rekal-init` | Scans codebase and bootstraps rekal with project knowledge |
| `rekal-save` | Auto on session end | Deduplicates and stores durable knowledge from the conversation |
| `rekal-usage` | `/rekal-usage` | Teaches agents how to use rekal effectively |
| `rekal-hygiene` | `/rekal-hygiene` | Finds conflicts, duplicates, stale data — proposes fixes |

## Tools

rekal exposes 16 MCP tools grouped into four categories.

**Core** — read and write memories:

| Tool | Purpose |
|------|---------|
| `memory_store` | Store a memory with type, project, and tags |
| `memory_search` | Hybrid search across all memories |
| `memory_update` | Edit content, tags, or type of an existing memory |
| `memory_delete` | Remove a memory by ID |

**Smart write** — manage knowledge over time:

| Tool | Purpose |
|------|---------|
| `memory_supersede` | Replace a memory while linking the old one as history |
| `memory_link` | Connect memories: `supersedes`, `contradicts`, or `related_to` |
| `memory_build_context` | One call that returns relevant memories + conflicts + timeline |

**Introspection** — explore what's stored:

| Tool | Purpose |
|------|---------|
| `memory_similar` | Find memories similar to a given one |
| `memory_topics` | Topic summary grouped by type |
| `memory_timeline` | Chronological view with optional date range |
| `memory_related` | All links to and from a memory |
| `memory_health` | Database stats: counts by type, project, date range |
| `memory_conflicts` | Find memories that contradict each other |

**Conversations** — track session threads:

| Tool | Purpose |
|------|---------|
| `conversation_start` | Start a conversation, optionally linked to a previous one |
| `conversation_tree` | Get the full conversation DAG |
| `conversation_threads` | List recent conversations with memory counts |
| `conversation_stale` | Find inactive conversations |

## How it works

### Storage

Everything lives in a single SQLite file (`~/.rekal/memory.db`). Three subsystems share it:

- **memories table** — content, type, project, tags, timestamps, access counts
- **FTS5 virtual table** — full-text index over content+tags+project, auto-synced via triggers on insert/update/delete
- **sqlite-vec virtual table** — 384-dimensional vector index for semantic search

When you store a memory, rekal writes the row, updates the FTS5 index (automatically), and inserts a vector embedding. When you update content, it re-embeds automatically.

Memory links (`supersedes`, `contradicts`, `related_to`) are stored in a separate table. `memory_supersede` writes the new memory and creates a `supersedes` link to the old one in a single operation — old knowledge stays queryable but the link makes the lineage explicit.

### Embeddings

rekal uses [fastembed](https://github.com/qdrant/fastembed) with the `BAAI/bge-small-en-v1.5` model (384 dimensions). It runs locally via ONNX — no API calls, no network, no tokens billed. The model is downloaded once on first use (~50MB) and cached.

Vectors are stored as packed floats in sqlite-vec and queried with approximate nearest-neighbor search.

### Search

Every `memory_search` runs two parallel lookups, merges candidates, then scores them:

```
1. Vector search   → top 3×limit candidates by cosine distance
2. FTS5 search     → top 3×limit candidates by BM25 rank
3. Union the candidate sets
4. For each candidate, compute:

   score = w_fts × sigmoid(-BM25)        ← keyword relevance     (default 0.4)
         + w_vec × (1 - cosine_distance)  ← semantic similarity  (default 0.4)
         + w_recency × exp(-0.693 × days/half_life)  ← recency  (default 0.2, 30-day half-life)

5. Sort by score, return top limit
```

**Why three signals?** Keywords alone miss synonyms ("deploy" vs "ship to prod"). Vectors alone miss exact identifiers (`BAAI/bge-small-en-v1.5` needs exact match). Recency alone buries important old knowledge. The blend covers all three failure modes.

**Why 0.4/0.4/0.2 defaults?** Keywords and semantics contribute equally — neither dominates. Recency is a tiebreaker at 0.2: a one-day-old memory scores ~0.195, a 90-day-old memory still scores ~0.025. Old memories surface when keyword or semantic match is strong enough. All weights and the half-life are configurable per search via `w_fts`, `w_vec`, `w_recency`, and `half_life` parameters on `memory_search` and `memory_build_context`.

**Why over-fetch 3x?** Filtering by project/type/conversation happens after scoring (no dynamic SQL injection). Over-fetching ensures enough candidates survive filtering to fill the requested limit.

### Why SQLite?

- **Single file** — copy it, back it up, version-control it, delete it to start fresh
- **Zero config** — no daemon, no port, no connection string
- **FTS5 built-in** — BM25 ranking with no external search engine
- **sqlite-vec extension** — vector search in the same process, no separate vector DB
- **Sub-millisecond** — everything is local disk I/O, no network round-trips
- **Portable** — works on macOS, Linux, Windows without different backends

## CLI

```bash
rekal serve    # Run as MCP server (default)
rekal health   # Database health report
rekal export   # Export all memories as JSON
```

## License

MIT
