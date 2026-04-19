# rekal

**Long-term memory for LLMs. One SQLite file, no cloud, no API keys.**

rekal is an [MCP](https://modelcontextprotocol.io) server that gives AI coding agents persistent memory across sessions. Memories are stored locally in SQLite and retrieved with hybrid search (BM25 keywords + vector semantics + recency decay). Nothing leaves your machine.

Works with any MCP-capable agent: [Claude Code](#setup--claude-code), [Codex CLI](#setup--codex-cli), [OpenCode](#setup--opencode).

```
Session 1:   "I prefer Ruff over Black"  → memory_store(...)
Session 47:  "Set up linting"            → memory_search("formatting preferences")
                                          ← "User prefers Ruff over Black" (0.92)
                                          Sets up Ruff without asking.
```

## Install

```bash
pip install rekal
# or
uv tool install rekal
```

Requires Python 3.11+. On first run, rekal creates `~/.rekal/memory.db`.

## Setup — Claude Code

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

> **Why is this required?** Claude Code's instruction priority is **system prompt > CLAUDE.md > MCP server instructions**. Built-in memory lives in the system prompt and always wins — without disabling it, the agent ignores rekal and writes to a flat file with no search, no deduplication, no ranking. The plugin's SessionStart hook replaces the context injection auto memory normally provides, so you don't lose anything.
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

## Setup — Codex CLI

One step. rekal is a standard MCP stdio server — no plugin system, no competing memory to disable ([Codex memories are off by default](https://developers.openai.com/codex/memories)).

Add to `~/.codex/config.toml` ([Codex MCP docs](https://developers.openai.com/codex/mcp)):

```toml
[mcp_servers.rekal]
command = "rekal"

# optional: scope all memories to a project automatically
[mcp_servers.rekal.env]
REKAL_PROJECT = "my-project"
```

Instruct the agent to call `memory_build_context` at session start. Add to your project's `AGENTS.md`:

```markdown
Call memory_build_context with your current task before exploring the codebase.
```

> **If you have enabled Codex memories** (`memories = true` in `~/.codex/config.toml`): disable them to avoid competing memory instructions.
> ```toml
> [features]
> memories = false
> ```

## Setup — OpenCode

One step. OpenCode has no built-in memory system — rekal plugs in cleanly with no conflicts.

Add to `opencode.jsonc` in your project root ([OpenCode MCP docs](https://opencode.ai/docs/mcp-servers/)):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "rekal": {
      "type": "local",
      "command": ["rekal"],
      "enabled": true,
      "environment": {
        "REKAL_PROJECT": "my-project"
      }
    }
  }
}
```

OpenCode does **not** auto-read `AGENTS.md` — you must list instruction files explicitly ([OpenCode config docs](https://opencode.ai/docs/config/)). Add to your `opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "instructions": ["AGENTS.md"]
}
```

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

Everything lives in `~/.rekal/memory.db`. Three subsystems share it:

- **memories table** — content, type, project, tags, timestamps, access counts
- **FTS5 virtual table** — full-text index over content+tags+project, auto-synced via triggers
- **sqlite-vec virtual table** — 384-dimensional vector index for semantic search

Memory links (`supersedes`, `contradicts`, `related_to`) are stored in a separate table. `memory_supersede` writes the new memory and creates a `supersedes` link in a single operation — old knowledge stays queryable with explicit lineage.

### Embeddings

rekal uses [fastembed](https://github.com/qdrant/fastembed) with `BAAI/bge-small-en-v1.5` (384 dimensions). Runs locally via ONNX — no API calls, no network. The model downloads once on first use (~50MB) and is cached.

### Search

Every `memory_search` runs two parallel lookups, merges candidates, then scores:

```
score = w_fts × sigmoid(-BM25)                       ← keyword relevance    (default 0.4)
      + w_vec × (1 - cosine_distance)                 ← semantic similarity  (default 0.4)
      + w_recency × exp(-0.693 × days/half_life)      ← recency              (default 0.2, 30-day half-life)
```

**Why three signals?** Keywords miss synonyms ("deploy" vs "ship to prod"). Vectors miss exact identifiers. Recency alone buries important old knowledge. The blend covers all three failure modes.

**Configurable weights.** All weights and half-life are configurable at three levels:

| Priority | Source | Set by | Persists? |
|----------|--------|--------|-----------|
| 1 (highest) | Per-search params | `memory_search(..., w_fts=0.8)` | No — single query only |
| 2 | Database project config | `memory_set_config(key, value, project)` | Yes — SQLite, across sessions |
| 3 | `.rekal/config.yml` | Checked into version control | Yes — shared with team |
| 4 (lowest) | Hardcoded defaults | Built into rekal | Always: 0.4 / 0.4 / 0.2, 30-day half-life |

Layers resolve per-key independently. A `.rekal/config.yml` setting `w_fts` and a DB override for `half_life` combine — each key uses its highest-priority source.

```yaml
# .rekal/config.yml
scoring:
  w_fts: 0.6
  w_vec: 0.3
  w_recency: 0.1
  half_life: 14.0
```

### Why SQLite?

- **Single file** — copy, back up, version-control, or delete to start fresh
- **Zero config** — no daemon, no port, no connection string
- **FTS5 built-in** — BM25 ranking without an external search engine
- **sqlite-vec extension** — vector search in the same process, no separate vector DB
- **Sub-millisecond** — local disk I/O, no network round-trips

## Troubleshooting — Claude Code

### Agent still writes to MEMORY.md

1. Check `autoMemoryEnabled` is `false` in `~/.claude/settings.json`
2. Check the plugin is installed: `claude plugin list` should show `rekal-skills`

### Agent doesn't call memory_build_context at session start

The `SessionStart` hook injects a reminder. If the agent ignores it, add to your project's `CLAUDE.md`:

```markdown
Call memory_build_context before exploring the codebase.
```

### Memories not being stored

Check the MCP server is running: `claude mcp list` should show `rekal`. If missing:

```bash
claude mcp add rekal rekal
```

### Updating the plugin

Claude Code's plugin system may serve a stale cache after `plugin install`. If hooks or skills are missing after an update, clear the marketplace cache first:

```bash
rm -rf ~/.claude/plugins/marketplaces/rekal
claude plugin marketplace add janbjorge/rekal
claude plugin install rekal-skills@rekal
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
