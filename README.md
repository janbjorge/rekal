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

Three steps: add the MCP server, install the plugin, and disable built-in memory.

**1. Add the MCP server** — gives Claude Code the memory tools:

```bash
claude mcp add rekal rekal
```

**2. Install the plugin** — teaches Claude Code when to use those tools, and prevents conflicts with built-in memory:

```bash
claude plugin marketplace add janbjorge/rekal
claude plugin install rekal-skills@rekal
```

**3. Disable built-in auto memory** — add `"autoMemoryEnabled": false` to `~/.claude/settings.json`:

```json
{
  "autoMemoryEnabled": false
}
```

> **Why is this required?** Claude Code's built-in memory writes to `MEMORY.md` and its instructions live in the system prompt — higher priority than MCP server instructions. Without this setting, the agent ignores rekal and writes to a flat file with no search, no deduplication, no ranking. See [full explanation](#why-disable-auto-memory) below.
>
> **What if I forget?** The plugin's `block-memory-writes` hook will catch and block MEMORY.md writes as a safety net, but the agent wastes turns hitting the block. Disabling auto memory is cleaner.
>
> **Can the plugin do this automatically?** No — Claude Code doesn't allow plugins to modify user settings. This manual step is the only way.

### What the plugin provides

**Hooks** (automatic, no user action needed):

| Hook | Event | What it does |
|------|-------|-------------|
| session-start | `SessionStart` | Reminds agent to call `memory_build_context` before doing anything |
| block-memory-writes | `PreToolUse` on Edit/Write | Blocks writes to MEMORY.md, redirects to rekal tools |

**Skills** (user-invocable):

| Skill | Trigger | What it does |
|-------|---------|-------------|
| `rekal-init` | `/rekal-init` | Scans codebase and bootstraps rekal with project knowledge |
| `rekal-save` | `/rekal-save` or auto on session end | Deduplicates and stores durable knowledge from the conversation |
| `rekal-usage` | `/rekal-usage` | Teaches agents how to use rekal effectively |
| `rekal-hygiene` | `/rekal-hygiene` | Finds conflicts, duplicates, stale data — proposes fixes |

## Why disable auto memory?

Claude Code's instruction priority: **system prompt > CLAUDE.md > MCP server instructions**. Built-in memory lives in the system prompt, rekal lives in MCP instructions — so built-in memory always wins. Disabling it removes the competing instructions entirely. The plugin's SessionStart hook replaces the context injection that auto memory normally provides, so you don't lose anything.

> **Note:** We've filed a [feature request](https://github.com/anthropics/claude-code/issues) for a `memoryProvider` setting that would let MCP servers replace built-in memory cleanly. Until that exists, disabling auto memory + using hooks is the most reliable approach.

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

**Why 0.4/0.4/0.2 defaults?** Keywords and semantics contribute equally — neither dominates. Recency is a tiebreaker at 0.2: a one-day-old memory scores ~0.195, a 90-day-old memory still scores ~0.025. Old memories surface when keyword or semantic match is strong enough.

**Configurable weights.** All weights and the half-life are configurable at three levels:

- **Per search** — pass `w_fts`, `w_vec`, `w_recency`, or `half_life` directly to `memory_search` or `memory_build_context` to override for a single query.
- **Per project (database)** — `memory_set_config(key="w_recency", value="0.5", project="my-app")` persists in the database across sessions. Searches scoped to that project automatically use its config.
- **Per project (file)** — drop a `.rekal/config.yml` in your project root with version-controlled defaults:

```yaml
scoring:
  w_fts: 0.6
  w_vec: 0.3
  w_recency: 0.1
  half_life: 14.0
```

rekal looks for this file in the working directory at startup. All keys are optional.

**Precedence.** Each weight is resolved independently through four layers. The first layer that provides a value wins:

| Priority | Source | Set by | Persists? |
|----------|--------|--------|-----------|
| 1 (highest) | Per-search params | `memory_search(..., w_fts=0.8)` | No — single query only |
| 2 | Database project config | `memory_set_config(key, value, project)` | Yes — in SQLite, across sessions |
| 3 | `.rekal/config.yml` | Checked into version control | Yes — shared with the team |
| 4 (lowest) | Hardcoded defaults | Built into rekal | Always: 0.4 / 0.4 / 0.2, 30-day half-life |

Layers are per-key, not all-or-nothing. If your `.rekal/config.yml` sets `w_fts` and `half_life`, and a `memory_set_config` call overrides `w_fts` in the database, the final weights for a search with no explicit params would be: `w_fts` from DB (layer 2), `half_life` from file (layer 3), `w_vec` and `w_recency` from hardcoded defaults (layer 4).

**Why over-fetch 3x?** Filtering by project/type/conversation happens after scoring (no dynamic SQL injection). Over-fetching ensures enough candidates survive filtering to fill the requested limit.

### Why SQLite?

- **Single file** — copy it, back it up, version-control it, delete it to start fresh
- **Zero config** — no daemon, no port, no connection string
- **FTS5 built-in** — BM25 ranking with no external search engine
- **sqlite-vec extension** — vector search in the same process, no separate vector DB
- **Sub-millisecond** — everything is local disk I/O, no network round-trips
- **Portable** — works on macOS, Linux, Windows without different backends

## Troubleshooting

### Agent still writes to MEMORY.md

1. Check that `autoMemoryEnabled` is `false` in `~/.claude/settings.json` — this is the most common cause
2. Check that the plugin is installed: `claude plugin list` should show `rekal-skills`

### Agent doesn't call memory_build_context at session start

The `SessionStart` hook injects a reminder, but if the agent ignores it, add this to your project's `CLAUDE.md`:

```markdown
Call memory_build_context before exploring the codebase.
```

### Memories not being stored

Check that the MCP server is running: `claude mcp list` should show `rekal`. If missing, re-add it:

```bash
claude mcp add rekal rekal
```

## Architecture (for contributors)

```
Plugin (hooks + skills)
  │
  ├── hooks/
  │   ├── handlers/session-start.py       ← SessionStart: inject context reminder
  │   └── handlers/block-memory-writes.py ← PreToolUse: block MEMORY.md writes
  │
  └── skills/
      ├── rekal-init/    ← /rekal-init: bootstrap project knowledge
      ├── rekal-save/    ← /rekal-save: end-of-session capture
      ├── rekal-usage/   ← /rekal-usage: operational guide for tools
      └── rekal-hygiene/ ← /rekal-hygiene: maintenance

MCP Server (rekal)
  │ stdio (JSON-RPC)
  │
  mcp_adapter.py          ← FastMCP server, lifespan, instructions
  │
  ├── tools/core.py       ─┐
  ├── tools/introspection.py│─ thin @mcp.tool() wrappers
  ├── tools/smart_write.py  │
  └── tools/conversations.py┘
                            │
                    sqlite_adapter.py ← all SQL lives here
                            │
                            ├── SQLite (memories, conversations, tags, conflicts)
                            ├── FTS5 (full-text index)
                            └── sqlite-vec (vector index)
```

**Instruction flow** (single source per concern):

| What | Where | Why |
|------|-------|-----|
| "Use rekal tools, not MEMORY.md" | MCP server instructions + PreToolUse hook | Instructions guide, hook enforces |
| "Call memory_build_context first" | SessionStart hook | Automatic, every session |
| "How to store/search/supersede" | MCP server instructions | Always present next to the tools |
| "Capture session knowledge" | rekal-save skill | Explicit trigger, detailed procedure |
| "Bootstrap project" | rekal-init skill | Explicit trigger |
| "Clean up database" | rekal-hygiene skill | Explicit trigger |

## CLI

```bash
rekal serve    # Run as MCP server (default)
rekal health   # Database health report
rekal export   # Export all memories as JSON
```

## License

MIT
