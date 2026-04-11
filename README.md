# rekal

Long-term memory for LLMs, as an [MCP](https://modelcontextprotocol.io) server.

Every conversation starts from scratch — your LLM forgets what you told it yesterday. rekal is a small MCP server that stores memories in a single SQLite file and retrieves them with hybrid search. Install it, point your MCP client at it, and your LLM starts remembering things between sessions.

```bash
pip install rekal
```

## Why I built this

I got tired of repeating myself. "I prefer Ruff." "We deploy with tags." "The auth service lives in `services/auth`." Every new conversation, same explanations.

Existing memory tools either do keyword search (which misses anything phrased differently) or vector search (which misses exact terms). I wanted both, plus a bias toward recent memories so stale stuff sinks naturally. And I wanted it in a single file I could back up by copying.

## How search works

rekal runs three searches and blends the results:

```
score = 0.4 · BM25(keyword match)
      + 0.4 · cosine(semantic similarity)
      + 0.2 · exp(-t/half_life)
```

So a memory about "deploying the auth service to staging" shows up whether you search for "deploy auth" or ask about "shipping the login system to pre-prod". Recent memories rank higher, but old ones still surface if they're relevant.

Embeddings run locally with [fastembed](https://github.com/qdrant/fastembed) — no API keys, no network calls.

## Quick start

Add to your MCP client config (Claude Desktop, Cursor, Claude Code, etc.):

```json
{
  "mcpServers": {
    "rekal": {
      "command": "rekal"
    }
  }
}
```

rekal creates `~/.rekal/memory.db` on first run. That file is your entire memory — portable, backupable, yours.

Requires Python 3.14+.

## What it looks like in practice

Your LLM picks up on things worth remembering and stores them:

```
User: "I prefer Ruff over Black for formatting"
LLM:  → memory_store("User prefers Ruff over Black", type="preference")
```

Weeks later, in a completely different conversation:

```
User: "Set up linting for my new project"
LLM:  → memory_search("formatting linting preferences")
      ← "User prefers Ruff over Black" (score: 0.92)
```

When knowledge changes, it doesn't just pile up — old versions stay linked:

```
LLM: → memory_supersede(old_id="mem_abc", new_content="API moved from v2 to v3")
```

And when things contradict each other, you can ask:

```
LLM: → memory_conflicts(project="backend")
     ← "use PostgreSQL for everything" contradicts "migrate analytics to ClickHouse"
```

## Tools

rekal exposes 16 tools over MCP.

### Core

| Tool | Description |
|------|-------------|
| `memory_store` | Store a memory with type, project, tags, and conversation scope |
| `memory_search` | Hybrid search — BM25 + vector + recency in one query |
| `memory_update` | Update content, tags, or type (re-embeds automatically) |
| `memory_delete` | Delete a memory by ID |

### Smart write

| Tool | Description |
|------|-------------|
| `memory_supersede` | Replace a memory while preserving the old one as history |
| `memory_link` | Link memories: `supersedes`, `contradicts`, `related_to` |
| `memory_build_context` | Relevant memories + conflicts + timeline for a query, in one call |

### Introspection

| Tool | Description |
|------|-------------|
| `memory_similar` | Find memories similar to a given one |
| `memory_topics` | Topic summary grouped by type |
| `memory_timeline` | Chronological view with optional date range filters |
| `memory_related` | All links to and from a memory |
| `memory_health` | Database stats — counts by type, project, date range |
| `memory_conflicts` | Find memories that contradict each other |

### Conversations

| Tool | Description |
|------|-------------|
| `conversation_start` | Start a conversation, optionally linked to a previous one |
| `conversation_tree` | Get the full conversation DAG |
| `conversation_threads` | List recent conversations with memory counts |
| `conversation_stale` | Find inactive conversations |

## Memory types

Memories are tagged with a type so search can be scoped:

| Type | For | Example |
|------|-----|---------|
| `fact` | Things that are true | "The API rate limit is 1000 req/min" |
| `preference` | How the user likes things | "Prefers dataclasses over hand-written \_\_init\_\_" |
| `procedure` | Steps to do something | "Deploy: git tag vX.Y.Z && git push --tags" |
| `context` | What's going on right now | "Currently rewriting the payment service" |
| `episode` | Things that happened | "Debugged the OOM — root cause was unbounded cache" |

## Architecture

Everything lives in one SQLite file:

```
rekal
  │
  SQLite ──┬── FTS5 index ──── keyword relevance (BM25)
           ├── sqlite-vec ──── semantic similarity (384d vectors)
           ├── recency ─────── exponential decay (30-day half-life)
           └── memory links ── supersedes / contradicts / related_to
```

Conversations form a DAG — follow-ups, branches, merges — so you can navigate interaction history the way you'd navigate a Git log.

## CLI

```bash
rekal serve    # Run as MCP server (default)
rekal health   # Database health report
rekal export   # Export all memories as JSON
```

## License

MIT
