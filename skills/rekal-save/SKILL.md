---
name: rekal-save
description: >
  End-of-session memory capture with deduplication. Extracts durable knowledge,
  checks for duplicates, stores or supersedes as appropriate. Use whenever a
  session wraps up, a task finishes, or the user says goodbye/thanks/done. Also
  use when significant preferences, decisions, or discoveries emerge mid-session.
  Make sure to trigger this skill proactively — err on the side of capturing.
allowed-tools: mcp__rekal__memory_search mcp__rekal__memory_store mcp__rekal__memory_supersede mcp__rekal__memory_conflicts mcp__rekal__memory_set_project
---

# rekal-save — Session Memory Capture

Save durable knowledge from session into rekal. Goal: user never repeats themselves.

## Workflow

### Step 1: Extract candidates

Review conversation, identify memories worth keeping. Filter: "Would fresh Claude session benefit from this?"

**Store:**
- Preferences/opinions ("prefers dataclasses over hand-written __init__")
- Project conventions/architecture ("auth service uses JWT, lives in services/auth")
- Decisions + reasoning ("chose PostgreSQL over MySQL for JSONB support")
- Procedures ("deploy: git tag, push tags, wait for CI, merge")
- Bugs with non-obvious root causes ("OOM from unbounded LRU cache in parser")
- Behavior corrections ("don't use grep, use rg")

**Skip:**
- Transient state: "currently editing main.py", "running tests now"
- Trivially re-discoverable: "function foo is on line 42"
- Too vague: "user likes clean code", "project uses Python"
- Session mechanics: "user asked me to fix a bug"
- Anything in CLAUDE.md or AGENTS.md — those files ARE persistent memory

### Step 2: Set project scope

Single-project session → call `memory_set_project` first. Scopes all stores automatically.

Multi-project or general session → skip, set `project` per-memory in step 4.

### Step 3: Deduplicate

Per candidate, before storing:

1. `memory_search` with topic query (limit 5)
2. Check for semantic overlap

**Close match found:**
- Same topic, new info → `memory_supersede` old one
- Same topic, same info → skip
- Same topic, contradictory → `memory_supersede` + note change

**No match:** proceed to store.

Two memories about "user's preferred formatter" must never coexist — newer supersedes older. Prevents near-duplicate accumulation that degrades search quality.

### Step 4: Store or supersede

Per surviving candidate:

**Pick type:**
- `fact` — objective truths about code, systems, APIs
- `preference` — how user likes things done
- `procedure` — step-by-step workflows
- `context` — current project state (decays via recency scoring)
- `episode` — notable events, debugging sessions, incidents

**Write self-contained content.** Future session has zero conversation context. Include what AND why.

```
Good: "User prefers Ruff over Black for formatting because it's faster
       and handles import sorting in a single tool"
Bad:  "User prefers Ruff"  (missing the why — too terse)
Bad:  "As discussed, use Ruff"  (references conversation — not self-contained)
```

**Add tags** — 2-4 specific tags per memory, not generic like "code" or "project".

### Step 5: Conflict check and summary

Run `memory_conflicts` (scoped to project if applicable). If new conflicts:

> "Noticed conflict: [X] vs [Y]. Want me to resolve it?"

Summarize saves in 1-2 sentences:

> "Saved 3 memories: Ruff preference, deploy procedure, auth architecture. Superseded 1 outdated API endpoint memory."

## Examples

**Good: preference with reasoning**
```
content: "User requires fd over find and rg over grep for all searching.
          Strict rule, no exceptions. Also prefers rg --files over find
          for file listing."
memory_type: "preference"
tags: ["tooling", "search", "cli"]
```

**Good: superseding outdated info**
```
memory_supersede(
  old_id="mem_abc",
  new_content="API rate limit is 5000 req/min after the 2024-03
               infrastructure upgrade (was 1000 previously)",
)
```

## Boundaries

- Stores memories only. No reorganization or cleanup (that's `/rekal-hygiene`)
- No conversation creation — captures knowledge from conversations
- Never stores secrets, API keys, passwords, tokens
- Asks user before storing sensitive or personal content
