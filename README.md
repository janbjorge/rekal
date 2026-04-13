# rekal

**Long-term memory for LLMs. One SQLite file, no cloud, no API keys.**

rekal is an [MCP](https://modelcontextprotocol.io) server that gives any LLM persistent memory across sessions. Memories are stored locally in SQLite and retrieved with hybrid search (BM25 keywords + vector semantics + recency decay). Nothing leaves your machine.

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

rekal is a stdio MCP server. Every client needs the same thing: point it at the `rekal` command.

<details>
<summary><strong>Claude Code</strong></summary>

```bash
claude mcp add rekal -- rekal
```

Claude Code also supports a skills plugin for automated memory management (session capture, deduplication, hygiene). After adding the MCP server:

```
/plugin marketplace add janbjorge/rekal
/plugin install rekal-skills@rekal
```

| Skill | Trigger | What it does |
|-------|---------|-------------|
| `rekal-init` | `/rekal-init` | Scans codebase and bootstraps rekal with project knowledge |
| `rekal-save` | Auto on session end | Deduplicates and stores durable knowledge from the conversation |
| `rekal-usage` | `/rekal-usage` | Teaches agents how to use rekal effectively |
| `rekal-hygiene` | `/rekal-hygiene` | Finds conflicts, duplicates, stale data — proposes fixes |

</details>

<details>
<summary><strong>Claude Desktop</strong></summary>

Add to `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "rekal": {
      "command": "rekal"
    }
  }
}
```

</details>

<details>
<summary><strong>Cursor</strong></summary>

Add to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project):

```json
{
  "mcpServers": {
    "rekal": {
      "command": "rekal"
    }
  }
}
```

</details>

<details>
<summary><strong>VS Code / GitHub Copilot</strong></summary>

Add to `.vscode/mcp.json` (workspace) or run **MCP: Open User Configuration** for global:

```json
{
  "servers": {
    "rekal": {
      "type": "stdio",
      "command": "rekal"
    }
  }
}
```

</details>

<details>
<summary><strong>Windsurf</strong></summary>

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "rekal": {
      "command": "rekal"
    }
  }
}
```

</details>

<details>
<summary><strong>Zed</strong></summary>

Add to `~/.config/zed/settings.json`:

```json
{
  "context_servers": {
    "rekal": {
      "command": "rekal"
    }
  }
}
```

</details>

<details>
<summary><strong>Cline</strong></summary>

Open Cline settings, click **MCP Servers → Configure**, and add:

```json
{
  "mcpServers": {
    "rekal": {
      "command": "rekal"
    }
  }
}
```

</details>

<details>
<summary><strong>Gemini CLI</strong></summary>

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "rekal": {
      "command": "rekal"
    }
  }
}
```

</details>

<details>
<summary><strong>Amazon Q CLI</strong></summary>

```bash
q mcp add rekal -- rekal
```

</details>

<details>
<summary><strong>Any other MCP client</strong></summary>

Point your client at the `rekal` command — no args, no env vars needed.

```json
{
  "mcpServers": {
    "rekal": {
      "command": "rekal"
    }
  }
}
```

</details>

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
