# rekal

**Long-term memory for LLMs. One SQLite file, no cloud, no API keys.**

rekal is an [MCP](https://modelcontextprotocol.io) server that gives AI coding agents persistent memory across sessions. Memories are stored locally in SQLite and retrieved with hybrid search (BM25 keywords + vector semantics + recency decay). Nothing leaves your machine.

[How it works](#how-it-works) · [Quickstart](#quickstart-claude-code) · [Install](#install) · [Setup](#setup-for-claude-code) · [Updating](#updating) · [Tools](#tools) · [Under the hood](#under-the-hood) · [Troubleshooting](#troubleshooting-for-claude-code)

Works with any MCP-capable agent: [Claude Code](#setup-for-claude-code), [Codex CLI](#setup-for-codex-cli), [OpenCode](#setup-for-opencode).

```
Session 1:   "I prefer Ruff over Black"  → memory_store(...)
Session 47:  "Set up linting"            → memory_search("formatting preferences")
                                          ← "User prefers Ruff over Black" (0.92)
                                          Sets up Ruff without asking.
```

## How it works

1. **Store.** The agent saves a durable fact with `memory_store`: a preference, a decision, a non-obvious discovery.
2. **Index.** rekal writes it to SQLite and builds two indexes over it: a BM25 keyword index and a 384-dimensional vector embedding, both computed locally with no network calls.
3. **Recall.** In a later session the agent calls `memory_search` (or `memory_build_context`). rekal blends keyword match, semantic similarity, and recency into a single score and returns the top hits.

All state is a single file: `~/.rekal/memory.db`. No daemon, no cloud, no API keys. For the scoring formula, schema, and embedding model, see [Under the hood](#under-the-hood).

## Quickstart (Claude Code)

```bash
uv tool install rekal                            # 1. install rekal (or: pip install rekal)
claude mcp add --scope user rekal -- rekal       # 2. register the MCP server (all projects)
claude plugin marketplace add janbjorge/rekal    # 3. add the plugin marketplace
claude plugin install rekal-skills@rekal         # 4. install the plugin
```

Then add `"autoMemoryEnabled": false` to `~/.claude/settings.json` so Claude Code's built-in memory doesn't compete with rekal.

Restart Claude Code and the agent has persistent memory. For what each step does, the other agents (Codex CLI, OpenCode), and the rationale behind disabling built-in memory, read on.

## Install

```bash
pip install rekal
# or
uv tool install rekal
```

Requires Python 3.11+. On first run, rekal creates `~/.rekal/memory.db`. To upgrade an existing install later, see [Updating](#updating).

## Setup for Claude Code

Three steps: add the MCP server, install the plugin, and disable built-in memory.

**1. Add the MCP server.** This gives Claude Code the memory tools:

```bash
claude mcp add --scope user rekal -- rekal
```

`--scope user` registers rekal for all your projects. Without it, `claude mcp add` defaults to local scope and the server loads only in the project where you ran it ([MCP scopes](https://code.claude.com/docs/en/mcp#mcp-installation-scopes)), and memory should follow you everywhere. The `--` separates Claude Code's own flags from the command that launches the server; stdio is the default transport.

**2. Install the plugin.** This teaches Claude Code when to use those tools and prevents conflicts with built-in memory:

```bash
claude plugin marketplace add janbjorge/rekal
claude plugin install rekal-skills@rekal
```

**3. Disable built-in auto memory.** Add `"autoMemoryEnabled": false` to `~/.claude/settings.json`:

```json
{
  "autoMemoryEnabled": false
}
```

<details>
<summary><b>Why disable built-in memory, and what if I forget?</b></summary>

**Why is this required?** Left enabled, Claude Code's built-in auto memory competes with rekal. It loads its own memory into the agent's context ([context layout](https://code.claude.com/docs/en/context-window)) and the agent favors it, writing to a flat file with no search, no deduplication, no ranking. Disabling it (`autoMemoryEnabled: false`, [settings docs](https://code.claude.com/docs/en/settings)) removes the competitor. The plugin's hooks then re-assert rekal: SessionStart restores the context injection auto memory normally provided, and UserPromptSubmit reinforces it every turn.

**What if I forget?** The plugin's `block-memory-writes` and `redirect-memory-reads` hooks catch flat-file memory access (MEMORY.md/.txt, memories.*) and redirect the agent to rekal as a safety net, but it wastes turns hitting them. Disabling auto memory is cleaner.

**Can the plugin do this automatically?** No. Claude Code only lets a plugin's `settings.json` set the `agent` and `subagentStatusLine` keys ([plugin settings](https://code.claude.com/docs/en/plugins)); it cannot touch `autoMemoryEnabled`. This manual step is the only way.

</details>

<details>
<summary><b>What the plugin provides</b>: hooks and skills</summary>

**Hooks** (automatic, no user action needed):

| Hook | Event | What it does |
|------|-------|-------------|
| session-start | `SessionStart` | Runs `rekal recall` and injects the recalled memories, plus a directive that memory lives only in rekal |
| user-prompt-submit | `UserPromptSubmit` | Runs `rekal recall --query <prompt>` and injects the top matches for the turn, plus the same directive, so recall follows what you just asked as context grows |
| pre-compact | `PreCompact` (auto) | Runs a subagent that saves durable facts to rekal before context is compacted, so nothing is lost to summarization |
| session-end | `SessionEnd` | Runs a subagent that saves durable facts to rekal when the session ends |
| block-memory-writes | `PreToolUse` on Edit/Write | Denies writes to flat-file memory (MEMORY.md/.txt, memories.*) with a reason redirecting to rekal tools |
| redirect-memory-reads | `PreToolUse` on Read | Denies reads of flat-file memory and tells the agent to call `memory_build_context` instead, so a missing file no longer reads as "no memory exists" |

**Skills** (user-invocable):

| Skill | Trigger | What it does |
|-------|---------|-------------|
| `rekal-init` | `/rekal-init` | Scans codebase and bootstraps rekal with project knowledge |
| `rekal-save` | `/rekal-save` or auto on session end | Deduplicates and stores durable knowledge from the conversation |
| `rekal-usage` | `/rekal-usage` | Teaches agents how to use rekal effectively |
| `rekal-hygiene` | `/rekal-hygiene` | Finds conflicts, duplicates, and stale data, then proposes fixes |

</details>

<details>
<summary><b>Recall hooks: PATH and environment scoping</b></summary>

The recall hooks shell out to the `rekal` CLI (`rekal recall`), which adds two requirements beyond installing the MCP server:

- **`rekal` must be on the hook's PATH, and current.** The `recall` subcommand ships alongside these hooks. If `rekal` is not on the PATH Claude Code gives its hook subprocesses, or predates that subcommand, recall injects nothing that turn. You still get the directive and nothing errors, but no memory loads. When updating, move the CLI and the plugin together (see [Updating](#updating)).
- **Project and database scoping belong in your shell or settings env, not the MCP `env` block.** `REKAL_PROJECT` and `REKAL_DB_PATH` set under the MCP server's `env` apply only to the MCP server process. The recall hook is a separate subprocess and does not inherit them, so it would read the default database with no project scope while the MCP tools use your configured scope. Set these in your shell environment or in Claude Code `settings.json` `env` so both the server and the hooks see the same values.

</details>

## Setup for Codex CLI

One step. rekal is a standard MCP stdio server, with no plugin system and no competing memory to disable ([Codex memories are off by default](https://developers.openai.com/codex/memories)).

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

<details>
<summary>If you've enabled Codex memories</summary>

(`memories = true` in `~/.codex/config.toml`): disable them to avoid competing memory instructions.

```toml
[features]
memories = false
```

</details>

## Setup for OpenCode

One step. OpenCode has no built-in memory system, so rekal plugs in cleanly with no conflicts.

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

OpenCode does **not** auto-read `AGENTS.md`; you must list instruction files explicitly ([OpenCode config docs](https://opencode.ai/docs/config/)). Add to your `opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "instructions": ["AGENTS.md"]
}
```

## Updating

### Update rekal (the MCP server)

```bash
pip install -U rekal
# or
uv tool upgrade rekal
```

Restart your agent so it relaunches the server. The SQLite schema **migrates automatically** on the next start: new columns are added in place and existing memories are preserved. No manual migration step, no data loss. To start fresh instead, delete `~/.rekal/memory.db` (rekal recreates it on next run).

### Update the Claude Code plugin

Third-party marketplaces have auto-update **off** by default ([auto-update docs](https://code.claude.com/docs/en/discover-plugins#configure-auto-updates)), so refresh manually, then reload:

```bash
claude plugin marketplace update rekal     # refresh the catalog
claude plugin install rekal-skills@rekal   # reinstall to pull the update
```

If hooks or skills are still missing afterward, Claude Code is serving a stale plugin cache. Clear it, restart Claude Code, then reinstall ([official remedy](https://code.claude.com/docs/en/discover-plugins#common-issues)):

```bash
rm -rf ~/.claude/plugins/cache
```

## Tools

rekal exposes 21 MCP tools across four categories. The three you'll use most:

| Tool | Purpose |
|------|---------|
| `memory_store` | Store a durable memory with type, project, and tags |
| `memory_search` | Hybrid search across memories; filter by `tier` (`durable`/`scratch`) |
| `memory_build_context` | One call returning durable + scratch memories, conflicts, and timeline |

<details>
<summary><b>All 21 tools</b>: core, smart write, introspection, conversations</summary>

**Core** (read and write memories):

| Tool | Purpose |
|------|---------|
| `memory_store` | Store a durable memory with type, project, and tags |
| `memory_store_scratch` | Store a transient note that auto-expires after `ttl_hours` (default 24h) |
| `memory_search` | Hybrid search across memories; filter by `tier` (`durable`/`scratch`) |
| `memory_update` | Edit content, tags, or type of an existing memory |
| `memory_delete` | Remove a memory by ID |
| `memory_prune` | Bulk-delete by scope (project / type / age); dry-run by default |
| `memory_set_project` | Set the default project for the current session |
| `memory_set_config` | Persist per-project scoring weights (`w_fts`, `w_vec`, `w_recency`, `half_life`) |

**Smart write** (manage knowledge over time):

| Tool | Purpose |
|------|---------|
| `memory_supersede` | Replace a memory while linking the old one as history |
| `memory_link` | Connect memories: `supersedes`, `contradicts`, or `related_to` |
| `memory_build_context` | One call returning durable + scratch memories (per-tier budgets), conflicts, and timeline |

**Introspection** (explore what's stored):

| Tool | Purpose |
|------|---------|
| `memory_similar` | Find memories similar to a given one |
| `memory_topics` | Topic summary grouped by type |
| `memory_timeline` | Chronological view with optional date range |
| `memory_related` | All links to and from a memory |
| `memory_health` | Database stats: counts by type, project, date range |
| `memory_conflicts` | Find memories that contradict each other |

**Conversations** (track session threads):

| Tool | Purpose |
|------|---------|
| `conversation_start` | Start a conversation, optionally linked to a previous one |
| `conversation_tree` | Get the full conversation DAG |
| `conversation_threads` | List recent conversations with memory counts |
| `conversation_stale` | Find inactive conversations |

</details>

## Under the hood

### Storage

Everything lives in `~/.rekal/memory.db`. Three subsystems share it:

- **memories table**: content, type, project, tags, timestamps, access counts, plus `tier` (`durable` or `scratch`) and optional `expires_at`
- **FTS5 virtual table**: full-text index over content+tags+project, auto-synced via triggers
- **sqlite-vec virtual table**: 384-dimensional vector index for semantic search

Memory links (`supersedes`, `contradicts`, `related_to`) are stored in a separate table. `memory_supersede` writes the new memory and creates a `supersedes` link in a single operation, so old knowledge stays queryable with explicit lineage.

**Tiers.** Durable memories live forever; scratch memories carry an `expires_at` and are hard-deleted on server start once past their TTL. Search, timeline, and topics hide expired scratch entries automatically. Use scratch for in-flight hypotheses and working notes that should not pollute the durable store.

### Data model

One table does the work; everything else hangs off it.

| Table | Holds |
|---|---|
| `memories` | the atomic unit: content + `memory_type` (semantic) + `tier` (lifecycle) + scope, provenance, tags |
| `memories_fts` | FTS5 keyword index, trigger-synced to `memories` |
| `memory_vec` | sqlite-vec 384-dim embedding, 1:1 with `memories` (synced in Python, no trigger) |
| `memory_links` | memory→memory graph: `supersedes` / `contradicts` / `related_to` |
| `conversations` + `conversation_links` | session threads and their graph |
| `project_config` | per-project scoring-weight overrides |

A memory has three orthogonal axes: **type** (fact / preference / procedure / context / episode), **tier** (durable, or scratch with a TTL), and **links** (the graph). The full schema, covering every column, trigger, foreign-key note, and query lifecycle, lives in [docs/data-model.md](docs/data-model.md).

### Embeddings

rekal uses [fastembed](https://github.com/qdrant/fastembed) with `BAAI/bge-small-en-v1.5` (384 dimensions). Runs locally via ONNX, with no API calls and no network. The model downloads once on first use (~50MB) and is cached.

### Search

Every `memory_search` runs two parallel lookups, merges candidates, then scores:

```
score = w_fts × sigmoid(-BM25)                       ← keyword relevance    (default 0.4)
      + w_vec × (1 - cosine_distance)                 ← semantic similarity  (default 0.4)
      + w_recency × exp(-0.693 × days/half_life)      ← recency              (default 0.2, 30-day half-life)
```

**Why three signals?** Keywords miss synonyms ("deploy" vs "ship to prod"). Vectors miss exact identifiers. Recency alone buries important old knowledge. The blend covers all three failure modes.

<details>
<summary><b>Configurable weights</b>: four resolution layers + <code>.rekal/config.yml</code></summary>

All weights and half-life are configurable at four levels:

| Priority | Source | Set by | Persists? |
|----------|--------|--------|-----------|
| 1 (highest) | Per-search params | `memory_search(..., w_fts=0.8)` | No, single query only |
| 2 | Database project config | `memory_set_config(key, value, project)` | Yes, in SQLite across sessions |
| 3 | `.rekal/config.yml` | Checked into version control | Yes, shared with team |
| 4 (lowest) | Hardcoded defaults | Built into rekal | Always: 0.4 / 0.4 / 0.2, 30-day half-life |

Layers resolve per-key independently. A `.rekal/config.yml` setting `w_fts` and a DB override for `half_life` combine, and each key uses its highest-priority source.

```yaml
# .rekal/config.yml
scoring:
  w_fts: 0.6
  w_vec: 0.3
  w_recency: 0.1
  half_life: 14.0
```

</details>

Full ranking reference, covering normalization, candidate retrieval, weight resolution, and a tuning guide, is in [docs/scoring.md](docs/scoring.md).

### Why SQLite?

- **Single file**: copy, back up, version-control, or delete to start fresh
- **Zero config**: no daemon, no port, no connection string
- **FTS5 built-in**: BM25 ranking without an external search engine
- **sqlite-vec extension**: vector search in the same process, no separate vector DB
- **Sub-millisecond**: local disk I/O, no network round-trips

## Troubleshooting for Claude Code

### Agent still writes to MEMORY.md

1. Check `autoMemoryEnabled` is `false` in `~/.claude/settings.json`
2. Check the plugin is installed: `claude plugin list` should show `rekal-skills`

### Session starts with no memory injected

The `SessionStart` and `UserPromptSubmit` hooks run `rekal recall` and inject the results, so memory should be present without the agent calling a tool. If nothing shows up, the hook cannot reach the CLI: confirm `rekal` is on the PATH Claude Code gives its hooks and is new enough to have the `recall` subcommand (`rekal recall --help`), and that any `REKAL_PROJECT` / `REKAL_DB_PATH` you rely on is set where the hook subprocess sees it (shell or `settings.json` `env`, not the MCP `env` block). See [Recall hooks: PATH and environment scoping](#setup-for-claude-code).

### Memories not being stored

Check the MCP server is running: `claude mcp list` should show `rekal`. If missing:

```bash
claude mcp add --scope user rekal -- rekal
```

### Hooks or skills missing after a plugin update

Claude Code may serve a stale plugin cache. Clear it and reinstall (see [Update the Claude Code plugin](#update-the-claude-code-plugin)).

## CLI

```bash
rekal serve    # Run as MCP server (default)
rekal health   # Database health report
rekal export   # Export all memories as JSON
rekal prune    # Bulk-delete memories by scope (dry-run unless --yes)
```

`rekal prune` requires at least one filter: `--project NAME`, `--memory-type TYPE`, `--older-than-days N`, or `--before "YYYY-MM-DD HH:MM:SS"`. Without `--yes` it only reports the match count.

## Architecture (for contributors)

<details>
<summary>Plugin + MCP server layout, and the single-source instruction flow</summary>

```
Plugin (hooks + skills)
  │
  ├── hooks/
  │   ├── handlers/session-start.py        ← SessionStart: inject recalled memory
  │   ├── handlers/user-prompt-submit.py   ← UserPromptSubmit: inject query-relevant memory
  │   ├── handlers/block-memory-writes.py  ← PreToolUse: redirect MEMORY.md writes to rekal
  │   ├── handlers/redirect-memory-reads.py ← PreToolUse: redirect MEMORY.md reads to rekal
  │   └── handlers/shared.py               ← shared path predicate, recall CLI, inject helpers
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
| "Memory lives in rekal, not files" | MCP server instructions + PreToolUse hooks (read + write) | Instructions guide, hooks enforce both directions |
| "Call memory_build_context first" | SessionStart hook | Automatic, every session |
| "Keep using rekal, don't drift" | UserPromptSubmit hook | Re-asserts every turn as context grows |
| "How to store/search/supersede" | MCP server instructions | Always present next to the tools |
| "Capture session knowledge" | rekal-save skill | Explicit trigger, detailed procedure |
| "Bootstrap project" | rekal-init skill | Explicit trigger |
| "Clean up database" | rekal-hygiene skill | Explicit trigger |

</details>

## License

MIT
