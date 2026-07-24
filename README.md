# rekal

**Long-term memory for LLMs. One SQLite file, no cloud, no API keys.**

rekal is an [MCP](https://modelcontextprotocol.io) server that gives AI coding agents persistent memory across sessions. Memories are stored locally in SQLite and retrieved with hybrid search (BM25 keywords + vector semantics + recency decay). Nothing leaves your machine.

[How it works](#how-it-works) · [Quickstart](#quickstart-claude-code) · [Install](#install) · [Setup](#setup-for-claude-code) · [Updating](#updating) · [Tools](#tools) · [Under the hood](#under-the-hood) · [Troubleshooting](#troubleshooting-for-claude-code)

Works with any MCP-capable agent: [Claude Code](#setup-for-claude-code), [Codex CLI](#setup-for-codex-cli), [OpenCode](#setup-for-opencode).

```
Session 1:   "I prefer Ruff over Black"  → memory_store(...)
Session 47:  "Set up linting"            → memory_build_context("formatting preferences")
                                          ← "User prefers Ruff over Black" (0.92)
                                          Sets up Ruff without asking.
```

## How it works

1. **Store.** The agent saves a durable fact with `memory_store`: a preference, a decision, a non-obvious discovery.
2. **Index.** rekal writes it to SQLite and builds two indexes over it: a BM25 keyword index and a 384-dimensional vector embedding, both computed locally with no network calls.
3. **Recall.** In a later session the agent calls `memory_build_context`. rekal blends keyword match, semantic similarity, and recency into a single score and returns the top hits above a relevance floor.

All state is a single file: `~/.rekal/memory.db`. For the scoring formula, schema, and embedding model, see [Under the hood](#under-the-hood).

## Quickstart (Claude Code)

```bash
uv tool install rekal                            # 1. install rekal (or: pip install rekal)
claude mcp add --scope user rekal -- rekal mcp       # 2. register the MCP server (all projects)
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
claude mcp add --scope user rekal -- rekal mcp
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
| session-start | `SessionStart` | `rekal hook session-start` recalls recency-ordered memories in-process and injects them, plus a directive that memory lives only in rekal |
| user-prompt-submit | `UserPromptSubmit` | `rekal hook user-prompt-submit` recalls memories matching the submitted prompt (hybrid search) and injects the top matches, plus the same directive, so recall follows what you just asked as context grows |
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
<summary><b>Recall hooks: environment scoping</b></summary>

The recall hooks run `uv run --project ${CLAUDE_PLUGIN_ROOT} rekal hook <event>`, so they use the plugin's own rekal install (`uv` must be available) and recall runs in-process, without needing a separate `rekal` on the PATH. Recall never blocks a session: a missing DB, load error, or empty result degrades to injecting the directive alone.

- **Project and database scoping belong in your shell or settings env, not the MCP `env` block.** `REKAL_PROJECT` and `REKAL_DB_PATH` set under the MCP server's `env` apply only to the MCP server process. The recall hook is a separate subprocess and does not inherit them, so it would read the default database with no project scope while the MCP tools use your configured scope. Set these in your shell environment or in Claude Code `settings.json` `env` so both the server and the hooks see the same values.

</details>

## Setup for Codex CLI

One step. rekal is a standard MCP stdio server, with no plugin system and no competing memory to disable ([Codex memories are off by default](https://developers.openai.com/codex/memories)).

Add to `~/.codex/config.toml` ([Codex MCP docs](https://developers.openai.com/codex/mcp)):

```toml
[mcp_servers.rekal]
command = "rekal"
args = ["mcp"]

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
      "command": ["rekal", "mcp"],
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

rekal exposes exactly three MCP tools. A small surface keeps the tool schemas
cheap in the agent's context and leaves no ambiguity about which tool to call:

| Tool | Purpose |
|------|---------|
| `memory_build_context` | Recall: hybrid search over stored memories. Results below the relevance floor (`min_score`, default 0.25) are dropped |
| `memory_store` | Store a distilled durable memory with project and tags. Pass `replaces=<old_id>` to update an existing memory instead of creating a near-duplicate |
| `memory_delete` | Remove a memory by ID |

Setting `REKAL_READONLY=1` in the server's environment registers only
`memory_build_context`, for sessions that should recall but never write.

Admin operations live in the CLI, not the tool surface:

| Command | Purpose |
|---------|---------|
| `rekal health` | Database stats: total, counts by project, date range |
| `rekal export` | Dump all memories as JSON |
| `rekal prune` | Bulk-delete by scope (project / age); dry-run by default |
| `rekal recall` | Print memories for a query (what the hooks inject) |

## Under the hood

### Storage

Everything lives in `~/.rekal/memory.db`. Three tables share it:

| Table | Holds |
|---|---|
| `memories` | the atomic unit: content + project scope + tags + timestamps |
| `memories_fts` | FTS5 keyword index over content+tags+project, trigger-synced |
| `memory_vec` | sqlite-vec 384-dim embedding, 1:1 with `memories` (synced in Python, no trigger) |

`memory_store(replaces=<old_id>)` stores the new memory and deletes the old
one in a single operation, so a topic is only ever covered by one memory
and stale versions never show up in search.

The schema is deliberately minimal. Earlier versions carried conversation
graphs, memory links, a scratch tier, memory types, and access counters;
benchmarks showed the structure cost tokens (fatter payloads, fatter
instructions) without earning them back. The full schema and the
auto-migration from older databases live in
[docs/data-model.md](docs/data-model.md). Existing DBs migrate in place
on first open, keeping content and embeddings.

### Embeddings

rekal uses [fastembed](https://github.com/qdrant/fastembed) with `BAAI/bge-small-en-v1.5` (384 dimensions). Runs locally via ONNX, with no API calls and no network. The model downloads once on first use (~50MB) and is cached.

### Search

Every recall runs two parallel lookups, merges candidates, then scores:

```
score = w_fts × sigmoid(-BM25)                       ← keyword relevance    (default 0.4)
      + w_vec × (1 - cosine_distance)                 ← semantic similarity  (default 0.4)
      + w_recency × exp(-0.693 × days/half_life)      ← recency              (default 0.2, 30-day half-life)
```

**Why three signals?** Keywords miss synonyms ("deploy" vs "ship to prod"). Vectors miss exact identifiers. Recency alone buries important old knowledge. The blend covers all three failure modes.

<details>
<summary><b>Configurable weights</b>: <code>.rekal/config.yml</code></summary>

All weights and half-life are configurable:

| Priority | Source | Set by | Persists? |
|----------|--------|--------|-----------|
| 1 (highest) | `.rekal/config.yml` | Checked into version control | Yes, shared with team |
| 2 (lowest) | Hardcoded defaults | Built into rekal | Always: 0.4 / 0.4 / 0.2, 30-day half-life |

Layers resolve per-key independently: a config file setting only `w_fts` keeps the defaults for everything else.

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

- One file you can copy, back up, version-control, or delete to start fresh
- Nothing to configure: no daemon, no port, no connection string
- FTS5 gives BM25 ranking without an external search engine
- sqlite-vec runs vector search in the same process, so there is no separate vector DB
- Queries hit local disk and return in under a millisecond

## Troubleshooting for Claude Code

### Agent still writes to MEMORY.md

1. Check `autoMemoryEnabled` is `false` in `~/.claude/settings.json`
2. Check the plugin is installed: `claude plugin list` should show `rekal-skills`

### Session starts with no memory injected

The `SessionStart` and `UserPromptSubmit` hooks recall memory in-process (`rekal hook <event>`) and inject it, so memory should be present without the agent calling a tool. If nothing shows up, confirm `uv` is available to Claude Code's hook subprocesses, and that any `REKAL_PROJECT` / `REKAL_DB_PATH` you rely on is set where the hook subprocess sees it (shell or `settings.json` `env`, not the MCP `env` block). See [Recall hooks: environment scoping](#setup-for-claude-code).

### Memories not being stored

Check the MCP server is running: `claude mcp list` should show `rekal`. If missing:

```bash
claude mcp add --scope user rekal -- rekal mcp
```

### Hooks or skills missing after a plugin update

Claude Code may serve a stale plugin cache. Clear it and reinstall (see [Update the Claude Code plugin](#update-the-claude-code-plugin)).

## CLI

```bash
rekal mcp      # Run the stdio MCP server (what Claude Code connects to)
rekal recall   # Print memories for hook context injection (--query, --project, --format)
rekal health   # Database health report
rekal export   # Export all memories as JSON
rekal prune    # Bulk-delete memories by scope (dry-run unless --yes)
```

`rekal prune` requires at least one filter: `--project NAME`, `--older-than-days N`, or `--before "YYYY-MM-DD HH:MM:SS"`. Without `--yes` it only reports the match count.

## Architecture (for contributors)

<details>
<summary>Plugin + MCP server layout, and the single-source instruction flow</summary>

```
Plugin (hooks + skills)
  │
  ├── hooks/hooks.json    ← wires each event to `uv run … rekal hook <event>`
  │       SessionStart          → rekal hook session-start        (inject recency recall + directive)
  │       UserPromptSubmit      → rekal hook user-prompt-submit   (inject query recall + directive)
  │       PreCompact / SessionEnd → agent hooks that auto-persist durable facts
  │       PreToolUse Edit|Write → rekal hook block-memory-writes  (redirect MEMORY.md writes)
  │       PreToolUse Read       → rekal hook redirect-memory-reads (redirect MEMORY.md reads)
  │   (handler logic lives in rekal/hooks.py + rekal/__main__.py, not standalone scripts)
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
  └── tools/core.py       ← the 3 tools (build_context, store, delete)
                            │
                    sqlite_adapter.py ← all SQL lives here
                            │
                            ├── SQLite (memories: content, project, tags, timestamps)
                            ├── FTS5 (full-text index)
                            └── sqlite-vec (vector index)
```

**Instruction flow** (single source per concern):

| What | Where | Why |
|------|-------|-----|
| "Memory lives in rekal, not files" | MCP server instructions + PreToolUse hooks (read + write) | Instructions guide, hooks enforce both directions |
| "Call memory_build_context first" | SessionStart hook | Automatic, every session |
| "Keep using rekal, don't drift" | UserPromptSubmit hook | Re-asserts every turn as context grows |
| "How to recall/store/replace" | MCP server instructions | Always present next to the tools |
| "Capture session knowledge" | rekal-save skill | Explicit trigger, detailed procedure |
| "Bootstrap project" | rekal-init skill | Explicit trigger |
| "Clean up database" | rekal-hygiene skill | Explicit trigger |

</details>

## License

MIT
