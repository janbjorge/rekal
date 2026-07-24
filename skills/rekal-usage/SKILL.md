---
name: rekal-usage
description: >
  Operational guide for rekal memory tools. Precise rules for when/how to call
  each tool, with exact parameters and decision trees. Use at session start,
  when onboarding to a rekal workspace, or when user asks "how do I use rekal",
  "what rekal tools", "help with memory". Trigger: /rekal-usage.
allowed-tools: mcp__rekal__memory_build_context mcp__rekal__memory_store mcp__rekal__memory_delete
---

You have persistent memory via three rekal MCP tools. Follow these rules exactly.

## The surface

| tool | purpose |
|------|---------|
| `memory_build_context(query, project?, limit?, min_score?)` | recall: memories + conflicts + timeline in one call |
| `memory_store(content, project?, tags?, replaces?)` | persist one distilled fact; `replaces=<old_id>` updates an existing one |
| `memory_delete(memory_id)` | remove a memory that is wrong or obsolete |

Admin operations (health report, export, bulk prune) live in the CLI:
`rekal health`, `rekal export`, `rekal prune`.

## Session start

Call `memory_build_context(query="<task description>")` before exploring the
codebase. Read the result before proceeding. Do this EVERY session.

## Recalling

Queries go through hybrid search: BM25 keywords + vector similarity + recency
decay. Results below `min_score` (default 0.25) are dropped, so an empty
result means "nothing relevant", not "nothing stored".

**Do:** Use natural language with domain terms.
```
"JWT refresh token rotation policy": specific, has domain terms
"how to deploy auth service to staging": natural language, synonyms work
"user's preferred formatter and why": retrieves preferences with reasoning
```

**Do not:** Use vague, generic, or garbage queries.
```
"stuff": matches nothing useful
"code": matches everything
"the thing": no semantic content
```

- `project="backend"`: scope to one project. Use in multi-project workspaces.
- Different angle on the same task = new `memory_build_context` call with a
  reworded query.

## Storing

### Rule: ALWAYS recall before storing

```
1. memory_build_context(query="<what you want to store>")
2. Read results:
   ├── Exact duplicate found     → do nothing
   ├── Same topic, outdated info → memory_store(content="...", replaces="<old_id>")
   └── No match                  → memory_store(content="...", tags=[...])
```

Never skip step 1. Duplicates degrade search quality. Two memories about the
same topic must never coexist: `replaces` supersedes the old memory so it
stops surfacing in recall.

### memory_store: exact parameters

```python
memory_store(
    content="Ruff > Black. Faster + handles import sort.",
    tags=["formatting", "ruff"],   # 2-4 specific tags. Not "code" or "project".
    project="backend",             # Set if project-specific. Omit for global knowledge.
    replaces="mem_abc123",         # ID from recall results when updating a topic.
)
```

### Content rules: distill + compress, always

**Never store raw dialogue, conversation turns, or verbose text.**

Distill to the durable fact, then apply caveman-style compression:

**Drop:** articles (a/an/the), filler (just/really/basically/actually/simply), pleasantries, hedging (might/could/maybe). Replace "in order to" → "to". Remove "you should", "make sure to", "remember to". State actions directly. Merge redundant points.

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

One memory = one distilled, compressed fact. 1-2 sentences max. Must be **self-contained**: fresh agent with zero context must understand it.

```
Bad:  "Use Ruff"                     (no reasoning)
Bad:  "As discussed, switch to Ruff" (references lost conversation)
Bad:  "The formatter thing"          (meaningless)
```

## Do NOT store

- Transient state: "currently editing main.py", "running tests now"
- Trivially re-discoverable: "function foo is on line 42"
- Things in CLAUDE.md or AGENTS.md: those files are already persistent
- Secrets, API keys, passwords, tokens: never
- Vague platitudes: "user likes clean code", "project uses Python"

## Do NOT use rekal for

- Looking up code you can read with file tools
- Targeted edits where you already know the file
- Running tests, commits, mechanical refactors
