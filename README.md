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

## How search works

Three signals, blended into one score:

```
score = 0.4 * BM25(keyword match)
      + 0.4 * cosine(semantic similarity)
      + 0.2 * exp(-t / half_life)
```

Embeddings run locally via [fastembed](https://github.com/qdrant/fastembed) (ONNX). No API calls, no network.

## CLI

```bash
rekal serve    # Run as MCP server (default)
rekal health   # Database health report
rekal export   # Export all memories as JSON
```

## License

MIT
