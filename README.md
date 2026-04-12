# rekal

Long-term memory for LLMs. An [MCP](https://modelcontextprotocol.io) server backed by a single SQLite file.

```bash
pip install rekal
```

## The problem

LLMs forget everything between sessions. You tell it you prefer Ruff, that you deploy with tags, that the auth service lives in `services/auth`. Next conversation, blank slate.

rekal fixes this. It stores memories in SQLite with hybrid search (keywords + vectors + recency) so your LLM can recall things you've told it before. One file, no cloud, no API keys.

## Quick start

Add rekal to your MCP client config (Claude Desktop, Cursor, Claude Code, etc.):

```json
{
  "mcpServers": {
    "rekal": {
      "command": "rekal"
    }
  }
}
```

On first run, rekal creates `~/.rekal/memory.db`. That single file holds everything. Copy it to back up, drop it to start fresh.

Requires Python 3.14+.

## How it works

Your LLM stores things worth remembering:

```
User: "I prefer Ruff over Black for formatting"
LLM:  → memory_store("User prefers Ruff over Black", type="preference")
```

Weeks later, different conversation:

```
User: "Set up linting for my new project"
LLM:  → memory_search("formatting linting preferences")
      ← "User prefers Ruff over Black" (score: 0.92)
```

When facts change, old versions stay linked:

```
LLM: → memory_supersede(old_id="mem_abc", new_content="API moved from v2 to v3")
```

When things contradict each other:

```
LLM: → memory_conflicts(project="backend")
     ← "use PostgreSQL for everything" contradicts "migrate analytics to ClickHouse"
```

## Search

Three signals, blended into one score:

```
score = 0.4 · BM25(keyword match)
      + 0.4 · cosine(semantic similarity)
      + 0.2 · exp(-t/half_life)
```

"deploy auth" and "shipping the login system to pre-prod" both find the same memory. Recent stuff ranks higher, but old memories still show up when relevant.

Embeddings run locally via [fastembed](https://github.com/qdrant/fastembed). Nothing leaves your machine.

## Claude Code skills

If you use [Claude Code](https://code.claude.com), rekal ships as a plugin with two skills:

| Skill | Trigger | What it does |
|-------|---------|-------------|
| `rekal-save` | Auto on session end, or `/rekal-save` | Reviews the conversation, deduplicates against existing memories, stores what's worth keeping |
| `rekal-hygiene` | `/rekal-hygiene` | Finds conflicts, duplicates, and stale data. Proposes fixes for your approval, never deletes on its own |

Install in Claude Code:

```bash
/plugin marketplace add janbjorge/rekal
/plugin install rekal-skills@rekal
```

Requires rekal to be running as an MCP server (see [Quick start](#quick-start)).

## Tools

16 tools over MCP:

### Core

| Tool | Description |
|------|-------------|
| `memory_store` | Store a memory with type, project, tags, and conversation scope |
| `memory_search` | Hybrid search: BM25 + vector + recency in one query |
| `memory_update` | Update content, tags, or type (re-embeds automatically) |
| `memory_delete` | Delete a memory by ID |

### Smart write

| Tool | Description |
|------|-------------|
| `memory_supersede` | Replace a memory while keeping the old one as history |
| `memory_link` | Link memories: `supersedes`, `contradicts`, `related_to` |
| `memory_build_context` | Relevant memories + conflicts + timeline for a query, in one call |

### Introspection

| Tool | Description |
|------|-------------|
| `memory_similar` | Find memories similar to a given one |
| `memory_topics` | Topic summary grouped by type |
| `memory_timeline` | Chronological view with optional date range filters |
| `memory_related` | All links to and from a memory |
| `memory_health` | Database stats: counts by type, project, date range |
| `memory_conflicts` | Find memories that contradict each other |

### Conversations

| Tool | Description |
|------|-------------|
| `conversation_start` | Start a conversation, optionally linked to a previous one |
| `conversation_tree` | Get the full conversation DAG |
| `conversation_threads` | List recent conversations with memory counts |
| `conversation_stale` | Find inactive conversations |

## Memory types

| Type | For | Example |
|------|-----|---------|
| `fact` | Things that are true | "The API rate limit is 1000 req/min" |
| `preference` | How you like things | "Prefers dataclasses over hand-written \_\_init\_\_" |
| `procedure` | Steps to do something | "Deploy: git tag vX.Y.Z && git push --tags" |
| `context` | Current state | "Currently rewriting the payment service" |
| `episode` | Things that happened | "Debugged the OOM, root cause was unbounded cache" |

## Architecture

One SQLite file, four components:

```
rekal
  │
  SQLite ──┬── FTS5 index ──── keyword relevance (BM25)
           ├── sqlite-vec ──── semantic similarity (384d vectors)
           ├── recency ─────── exponential decay (30-day half-life)
           └── memory links ── supersedes / contradicts / related_to
```

Conversations form a DAG (follow-ups, branches, merges), navigable like a git log.

## CLI

```bash
rekal serve    # Run as MCP server (default)
rekal health   # Database health report
rekal export   # Export all memories as JSON
```

## License

MIT
