# rekal

**Long-term memory for LLMs. One SQLite file, no cloud, no API keys.**

You tell your LLM you prefer Ruff, that deploys go through tags, that the auth service lives in `services/auth`. Next conversation — blank slate. You repeat yourself. Again. Forever.

rekal fixes this. It's an [MCP](https://modelcontextprotocol.io) server that gives any LLM persistent memory across sessions. Memories are stored locally and retrieved with hybrid search (keywords + semantics + recency). Nothing leaves your machine.

## What it looks like

**Session 1** — you mention a preference:

```
You:  "I prefer Ruff over Black for formatting"
LLM:  → memory_store("User prefers Ruff over Black", type="preference")
```

**Session 47** — different day, different conversation:

```
You:  "Set up linting for my new project"
LLM:  → memory_search("formatting linting preferences")
      ← "User prefers Ruff over Black" (score: 0.92)
      Sets up Ruff without asking.
```

**When facts change**, old versions stay linked — not silently overwritten:

```
LLM:  → memory_supersede(old_id, "API moved from v2 to v3")
```

**When things contradict**:

```
LLM:  → memory_conflicts(project="backend")
      ← "use PostgreSQL for everything" contradicts "migrate analytics to ClickHouse"
```

## Install

```bash
pip install rekal
```

or with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install rekal
```

Requires Python 3.11+.

## Setup — pick your client

On first run, rekal creates `~/.rekal/memory.db` — a single file that holds everything. Copy it to back up, delete it to start fresh.

<details>
<summary><strong>Claude Code</strong></summary>

```bash
claude mcp add rekal -- rekal
```

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

rekal is a standard stdio MCP server. Point your client at the `rekal` command — no args, no env vars needed.

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

### Claude Code plugin (optional)

rekal also ships as a Claude Code plugin with skills for automated memory management. Install the MCP server first (above), then inside Claude Code:

```
/plugin marketplace add janbjorge/rekal
/plugin install rekal-skills@rekal
```

| Skill | Trigger | What it does |
|-------|---------|-------------|
| `rekal-init` | `/rekal-init` | Scans your codebase and bootstraps rekal with project knowledge |
| `rekal-save` | Auto on session end | Reviews the conversation, deduplicates, stores what's worth keeping |
| `rekal-usage` | `/rekal-usage` | Teaches agents how to use rekal effectively |
| `rekal-hygiene` | `/rekal-hygiene` | Finds conflicts, duplicates, stale data — proposes fixes |

## How search works

Three signals, blended into one score:

```
score = 0.4 * BM25(keyword match)
      + 0.4 * cosine(semantic similarity)
      + 0.2 * exp(-t / half_life)
```

- **Keywords** — "deploy auth" matches "deploy auth"
- **Semantics** — "shipping the login system to pre-prod" also matches "deploy auth"
- **Recency** — recent stuff ranks higher, but old memories still surface when relevant

Embeddings run locally via [fastembed](https://github.com/qdrant/fastembed) (ONNX). No API calls, no network, no latency.

## 16 MCP tools

### Core

| Tool | What it does |
|------|-------------|
| `memory_store` | Store a memory with type, project, tags, and conversation scope |
| `memory_search` | Hybrid search: BM25 + vector + recency in one query |
| `memory_update` | Update content, tags, or type (re-embeds automatically) |
| `memory_delete` | Delete a memory by ID |

### Smart write

| Tool | What it does |
|------|-------------|
| `memory_supersede` | Replace a memory while keeping the old one linked as history |
| `memory_link` | Link memories: `supersedes`, `contradicts`, `related_to` |
| `memory_build_context` | Relevant memories + conflicts + timeline in one call |

### Introspection

| Tool | What it does |
|------|-------------|
| `memory_similar` | Find memories similar to a given one |
| `memory_topics` | Topic summary grouped by type |
| `memory_timeline` | Chronological view with optional date range |
| `memory_related` | All links to and from a memory |
| `memory_health` | Database stats: counts by type, project, date range |
| `memory_conflicts` | Find memories that contradict each other |

### Conversations

| Tool | What it does |
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
| `episode` | Things that happened | "Debugged the OOM — root cause was unbounded cache" |

## Architecture

Everything lives in one SQLite file. Four subsystems:

```
rekal
  │
  SQLite ──┬── FTS5 index ──── keyword relevance (BM25)
           ├── sqlite-vec ──── semantic similarity (384d vectors)
           ├── recency ─────── exponential decay (30-day half-life)
           └── memory links ── supersedes / contradicts / related_to
```

No external services. No background processes. No config files. Just `rekal` and a `.db` file.

Conversations form a DAG (follow-ups, branches, merges) — navigable like a git log.

## CLI

```bash
rekal serve    # Run as MCP server (default)
rekal health   # Database health report
rekal export   # Export all memories as JSON
```

## Why rekal?

| | rekal | Cloud memory services |
|---|---|---|
| **Privacy** | Everything local. Nothing leaves your machine | Data sent to third-party servers |
| **Cost** | Free forever | Per-query or subscription pricing |
| **Speed** | Sub-millisecond SQLite queries | Network round-trips |
| **Portability** | One `.db` file. Copy, back up, version control | Vendor lock-in |
| **Dependencies** | `pip install` and go | API keys, accounts, config |

## License

MIT
