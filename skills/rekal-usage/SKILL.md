---
name: rekal-usage
description: >
  Guide for using rekal's MCP tools effectively. Teaches tool selection,
  query patterns, project scoping, and retrieval workflows. Use at session
  start, when an agent is unsure how to query memories, or when onboarding
  a new agent to a rekal-enabled workspace. Trigger: /rekal-usage,
  "how do I use rekal", "what rekal tools are there", "help with memory".
allowed-tools: mcp__rekal__memory_build_context mcp__rekal__memory_search mcp__rekal__memory_topics mcp__rekal__memory_health mcp__rekal__memory_timeline mcp__rekal__memory_similar mcp__rekal__memory_related mcp__rekal__memory_conflicts mcp__rekal__memory_set_project mcp__rekal__memory_store mcp__rekal__memory_supersede mcp__rekal__memory_update mcp__rekal__memory_delete mcp__rekal__memory_link mcp__rekal__conversation_start mcp__rekal__conversation_tree mcp__rekal__conversation_threads mcp__rekal__conversation_stale
---

# rekal-usage — How to Use rekal Memory

rekal gives you persistent long-term memory via MCP tools. This skill teaches you how to use them effectively.

## Quick start: the two tools you need most

### `memory_build_context` — start here

Your default entry point. One call returns relevant memories + conflicts + recent timeline for a query.

```
memory_build_context(query="authentication setup", project="backend")
```

Use this when:
- Starting a task (get relevant prior knowledge)
- The user asks about something that might have prior context
- You need a broad picture before diving in

### `memory_search` — for focused lookups

Narrower than build_context. Use when you know what you're looking for.

```
memory_search(query="deploy procedure", project="backend", limit=5)
memory_search(query="formatting preferences", memory_type="preference")
```

Use this when:
- You need a specific fact, preference, or procedure
- You want to filter by type, project, or conversation
- You're checking if something is already stored (before storing)

## Tool reference

### Retrieval tools

| Tool | When to use |
|------|-------------|
| `memory_build_context` | Starting a task, broad context gathering. Returns memories + conflicts + timeline in one call. |
| `memory_search` | Focused lookup. Supports filters: project, memory_type, conversation_id. |
| `memory_similar` | Given a memory ID, find related ones by vector similarity. |
| `memory_topics` | Overview of what's stored, grouped by type. Good for orientation. |
| `memory_timeline` | Chronological view. Filter by date range. Good for "what changed recently". |
| `memory_related` | Follow links from a memory (supersedes, contradicts, related_to). |
| `memory_health` | Database stats: counts, projects, date range. Quick health check. |
| `memory_conflicts` | Find contradicting memories. Check after storing new facts. |

### Write tools

| Tool | When to use |
|------|-------------|
| `memory_store` | Store new knowledge. Always set type and tags. |
| `memory_supersede` | Replace outdated memory. Keeps old version linked. Prefer over delete + store. |
| `memory_update` | Change content, tags, or type on existing memory. |
| `memory_delete` | Remove a memory entirely. Use sparingly — supersede preserves history. |
| `memory_link` | Connect two memories: `supersedes`, `contradicts`, `related_to`. |

### Session tools

| Tool | When to use |
|------|-------------|
| `memory_set_project` | Scope all subsequent calls to a project. Set once at session start. |
| `conversation_start` | Begin a conversation thread. Optional: link to previous conversation. |
| `conversation_tree` | View the conversation DAG (follow-ups, branches). |
| `conversation_threads` | List recent conversations with memory counts. |
| `conversation_stale` | Find old inactive conversations. |

## Search scoring

rekal blends three signals:

```
score = 0.4 * BM25 (keyword match)
      + 0.4 * cosine (semantic similarity)
      + 0.2 * recency (exponential decay, 30-day half-life)
```

This means:
- **Exact keywords matter** — "Ruff formatter" finds "Ruff" memories
- **Synonyms work** — "shipping login to pre-prod" finds "deploy auth service"
- **Recent memories rank higher** — but old ones still surface when relevant

## Query patterns

### Good queries

```
"user's preferred code formatter"        — specific topic
"how to deploy the auth service"         — natural language works
"PostgreSQL vs MySQL decision"           — decisions and reasoning
"debugging OOM in parser"                — past episodes
```

### Bad queries

```
"stuff"                                  — too vague
"code"                                   — matches everything
"the thing we talked about"              — no semantic content
"a]s#df"                                 — noise
```

### Tips

- **Use natural language.** The vector search handles synonyms and paraphrasing.
- **Be specific.** "React component testing strategy" beats "testing".
- **Include domain terms.** "JWT refresh token rotation" finds auth memories.
- **Filter by project** when working in a multi-project workspace.
- **Filter by type** when you know what you want: `memory_type="procedure"` for how-tos, `memory_type="preference"` for user preferences.

## Workflows

### Starting a new task

```
1. memory_set_project("myproject")           — scope the session
2. memory_build_context("the task topic")    — get prior knowledge
3. Proceed with the task, informed by context
```

### Checking before storing

```
1. memory_search("the thing you want to store", limit=5)
2. If close match exists → memory_supersede (don't create duplicates)
3. If no match → memory_store with type + tags
```

### When facts change

```
1. memory_search("the outdated fact")
2. memory_supersede(old_id="mem_xxx", new_content="updated fact")
   — NOT delete + store (supersede preserves history)
```

### Exploring what's stored

```
1. memory_health()                           — counts and stats
2. memory_topics(project="myproject")        — topic clusters
3. memory_timeline(limit=20)                 — recent activity
```

### Following a memory's history

```
1. memory_related(memory_id="mem_xxx")       — find links
2. Follow supersedes chain to see how a fact evolved
```

## Memory types — when to use each

| Type | Use for | Example |
|------|---------|---------|
| `fact` | Objective truths about code, APIs, systems | "Auth service uses JWT with 15-min expiry" |
| `preference` | How the user likes things done | "Prefers Ruff over Black, rg over grep" |
| `procedure` | Step-by-step workflows | "Deploy: tag, push, wait CI, merge to main" |
| `context` | Current project state (decays faster via recency) | "Rewriting payment service from REST to gRPC" |
| `episode` | Notable events, debugging sessions | "OOM traced to unbounded LRU cache in parser module" |

## Project scoping

- Call `memory_set_project` once at session start for single-project work
- For multi-project sessions, pass `project=` per tool call
- Project scoping filters search results — prevents cross-project noise
- Memories without a project are global (visible from any project scope)

## Common mistakes

1. **Not checking before storing** — creates duplicates. Always search first.
2. **Deleting instead of superseding** — loses history. Use `memory_supersede`.
3. **Vague content** — "use Ruff" is useless without why. Write self-contained content.
4. **Missing type/tags** — makes retrieval harder. Always set both.
5. **Skipping project scope** — cross-project noise in search results.
6. **Over-storing transient info** — "currently editing main.py" isn't worth a memory.

## When NOT to use rekal

- Looking up code you can just read (use file tools instead)
- Storing info already in CLAUDE.md or AGENTS.md (those are persistent by design)
- Storing secrets, API keys, passwords, tokens (never)
- Remembering what line a function is on (trivially re-discoverable)
