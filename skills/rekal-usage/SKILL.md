---
name: rekal-usage
description: >
  Operational guide for rekal memory tools. Precise rules for when/how to call
  each tool, with exact parameters and decision trees. Use at session start,
  when onboarding to a rekal workspace, or when user asks "how do I use rekal",
  "what rekal tools", "help with memory". Trigger: /rekal-usage.
allowed-tools: mcp__rekal__memory_build_context mcp__rekal__memory_search mcp__rekal__memory_topics mcp__rekal__memory_health mcp__rekal__memory_timeline mcp__rekal__memory_similar mcp__rekal__memory_related mcp__rekal__memory_conflicts mcp__rekal__memory_set_project mcp__rekal__memory_store mcp__rekal__memory_supersede mcp__rekal__memory_update mcp__rekal__memory_delete mcp__rekal__memory_link mcp__rekal__conversation_start mcp__rekal__conversation_tree mcp__rekal__conversation_threads mcp__rekal__conversation_stale
---

You have persistent memory via rekal MCP tools. Follow these rules exactly.

## Session start

1. If working in one project: `memory_set_project(project="<name>")`. All subsequent calls auto-scope.
2. Call `memory_build_context(query="<task description>")`. This returns memories + conflicts + timeline in one call. Read the result before proceeding.

Do this EVERY session. Do not skip. Do not explore the codebase before checking memory.

## Retrieving knowledge

### Decision tree

```
Need prior knowledge about a topic?
├── Broad context (starting task, unfamiliar area)
│   └── memory_build_context(query="...", project="...", limit=10)
│       Returns: relevant memories + conflicts + recent timeline
│
├── Specific lookup (one fact, one preference, one procedure)
│   └── memory_search(query="...", limit=5)
│       Filter with: project=, memory_type=, conversation_id=
│
├── What topics exist?
│   └── memory_topics(project="...")
│
├── What changed recently?
│   └── memory_timeline(project="...", limit=20)
│       Date filter: start="2024-01-01 00:00:00", end="2024-12-31 23:59:59"
│
├── Given a memory ID, find neighbors?
│   └── memory_similar(memory_id="mem_xxx", limit=5)
│
├── Given a memory ID, follow its links?
│   └── memory_related(memory_id="mem_xxx")
│       Returns: supersedes, contradicts, related_to links
│
└── Database health check?
    └── memory_health()
```

### Query rules

Queries go through hybrid search: BM25 keywords + vector similarity + recency decay.

**Do:** Use natural language with domain terms.
```
"JWT refresh token rotation policy"     — specific, has domain terms
"how to deploy auth service to staging" — natural language, synonyms work
"user's preferred formatter and why"    — retrieves preferences with reasoning
```

**Do not:** Use vague, generic, or garbage queries.
```
"stuff"          — matches nothing useful
"code"           — matches everything
"the thing"      — no semantic content
```

### Filtering

- `project="backend"` — scope to one project. Use when multi-project workspace.
- `memory_type="preference"` — only user preferences. Types: `fact`, `preference`, `procedure`, `context`, `episode`.
- `conversation_id="conv_xxx"` — only memories from one conversation.

Combine filters: `memory_search(query="testing", project="api", memory_type="procedure", limit=5)`

## Storing knowledge

### Rule: ALWAYS search before storing

```
1. memory_search(query="<what you want to store>", limit=5)
2. Read results:
   ├── Exact duplicate found     → do nothing
   ├── Same topic, outdated info → memory_supersede(old_id="mem_xxx", new_content="...")
   ├── Same topic, contradicts   → memory_supersede(old_id="mem_xxx", new_content="...") + note the change
   └── No match                  → memory_store(content="...", memory_type="...", tags=[...])
```

Never skip step 1. Duplicates degrade search quality.

### memory_store — exact parameters

```python
memory_store(
    content="User prefers Ruff over Black for formatting because it's faster and handles import sorting",
    memory_type="preference",      # REQUIRED. One of: fact, preference, procedure, context, episode
    tags=["formatting", "ruff"],   # REQUIRED. 2-4 specific tags. Not "code" or "project".
    project="backend",             # Set if project-specific. Omit for global knowledge.
    conversation_id="conv_xxx",    # Set if part of a conversation thread. Usually omit.
)
```

### memory_supersede — replacing outdated knowledge

```python
memory_supersede(
    old_id="mem_abc123",                                    # ID from search results
    new_content="API rate limit is 5000 req/min after the March infrastructure upgrade (was 1000)",
    # memory_type, project, tags inherited from old memory unless overridden
)
```

Use supersede, NOT delete + store. Supersede preserves history via links.

### memory_types — pick the right one

| Type | When | Example content |
|------|------|-----------------|
| `fact` | Objective truth about code/systems/APIs | `"Auth service uses JWT with 15-min access token, 7-day refresh token"` |
| `preference` | How user wants things done | `"User requires rg over grep, fd over find — strict, no exceptions"` |
| `procedure` | Step-by-step workflow | `"Deploy: 1) git tag vX.Y.Z 2) git push --tags 3) wait CI green 4) merge to main"` |
| `context` | Current project state (decays via recency) | `"Rewriting payment service from REST to gRPC, ~60% done"` |
| `episode` | Notable event, debugging session | `"OOM in parser traced to unbounded LRU cache — fixed by adding maxsize=1000"` |

### Content rules — distill + compress, always

**Never store raw dialogue, conversation turns, or verbose text.**

Distill to the durable fact, then apply caveman-style compression:

**Drop:** articles (a/an/the), filler (just/really/basically/actually/simply), pleasantries, hedging (might/could/maybe). Replace "in order to" → "to". Remove "you should", "make sure to", "remember to" — state actions directly. Merge redundant points.

**Keep exact:** technical terms, proper nouns, version numbers, values, reasons, causality (`X → Y`).

```
BAD:  "User said yeah I think maybe we could try using Python for this"
GOOD: "Prefer Python for project"

BAD:  "So we went back and forth and eventually decided to use Postgres because
       the team already knows it and the data is relational anyway"
GOOD: "DB: Postgres. Team familiar, data relational."

BAD:  "User prefers Ruff over Black for formatting because it's faster and handles import sorting in one tool"
GOOD: "Ruff > Black. Faster + handles import sort."
```

One memory = one distilled, compressed fact. 1-2 sentences max. Must be **self-contained** — fresh agent with zero context must understand it.

```
Bad:  "Use Ruff"                     — no reasoning
Bad:  "As discussed, switch to Ruff" — references lost conversation
Bad:  "The formatter thing"          — meaningless
```

## Updating and linking

### memory_update — modify in place

```python
memory_update(
    memory_id="mem_xxx",
    content="new content",     # Optional. Re-embeds if changed.
    tags=["new", "tags"],      # Optional. Replaces all existing tags.
    memory_type="fact",        # Optional.
)
```

Use for minor corrections (typo, adding a tag). For substantive changes, use `memory_supersede`.

### memory_link — connect memories

```python
memory_link(
    from_id="mem_xxx",
    to_id="mem_yyy",
    relation="related_to",    # One of: supersedes, contradicts, related_to
)
```

- `supersedes` — new version replaces old (created automatically by memory_supersede)
- `contradicts` — two memories conflict (flag for resolution)
- `related_to` — topically connected

### memory_conflicts — find contradictions

```python
memory_conflicts(project="backend")
```

Run after storing new facts that might conflict with existing knowledge. Returns pairs of contradicting memories.

## Conversations

```
conversation_start(title="Debugging auth timeout", parent_id="conv_xxx")  # parent_id optional
conversation_tree(conversation_id="conv_xxx")     # full DAG
conversation_threads(limit=10)                     # recent conversations + memory counts
conversation_stale(days=30)                        # inactive conversations
```

Conversations are optional. Use when a session has a clear thread worth tracking.

## Do NOT store

- Transient state: "currently editing main.py", "running tests now"
- Trivially re-discoverable: "function foo is on line 42"
- Things in CLAUDE.md or AGENTS.md — those files are already persistent
- Secrets, API keys, passwords, tokens — never
- Vague platitudes: "user likes clean code", "project uses Python"

## Do NOT use rekal for

- Looking up code you can read with file tools
- Targeted edits where you already know the file
- Running tests, commits, mechanical refactors
