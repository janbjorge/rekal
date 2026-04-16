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

Save durable knowledge from this session into rekal. Goal: user never repeats themselves across sessions.

## Step 1: Extract candidates

Review the conversation. Per item, apply this filter:

```
Would a fresh agent in a new session benefit from knowing this?
├── YES → candidate
└── NO  → skip
```

**Candidate types:**

| What | Example |
|------|---------|
| Preference with reasoning | `"User prefers dataclasses over hand-written __init__ for less boilerplate"` |
| Architecture/convention | `"Auth service uses JWT, lives in services/auth, 15-min token expiry"` |
| Decision + why | `"Chose PostgreSQL over MySQL for JSONB support and better partial indexes"` |
| Procedure | `"Deploy: 1) git tag vX.Y.Z 2) git push --tags 3) wait CI 4) merge to main"` |
| Bug with non-obvious cause | `"OOM from unbounded LRU cache in parser — fixed with maxsize=1000"` |
| Behavior correction | `"Never use grep/find. Use rg/fd. Strict, no exceptions."` |

**Skip — do not store:**

- Transient state: "currently editing main.py", "tests passing now"
- Trivially re-discoverable: "function foo is on line 42", "file has 200 lines"
- Too vague: "user likes clean code", "project uses Python"
- Session mechanics: "user asked me to fix a bug", "we discussed testing"
- Secrets, API keys, passwords, tokens — never

If zero candidates survive, stop here. Do not force-store.

## Step 2: Set project scope

```
Single-project session?
├── YES → memory_set_project(project="<name>")
│         All subsequent stores auto-scope to this project.
└── NO  → Skip. Set project= per memory in step 4.
```

## Step 3: Deduplicate each candidate

For EVERY candidate, before storing:

```python
memory_search(query="<candidate topic in natural language>", limit=5)
```

Read results. Apply:

```
Search returned results?
├── NO match at all
│   └── Proceed to step 4 (store new)
│
├── Same topic, same info (duplicate)
│   └── SKIP. Do not store.
│
├── Same topic, new/updated info
│   └── memory_supersede(old_id="<matched memory id>", new_content="<updated content>")
│
└── Same topic, contradictory info
    └── memory_supersede(old_id="<matched memory id>", new_content="<corrected content>")
        Include what changed and why in the content.
```

**Critical rule:** Two memories about the same topic must never coexist. Newer supersedes older. "User's preferred formatter" appears exactly once in the database.

## Step 4: Store surviving candidates

Per candidate that passed dedup with no match:

```python
memory_store(
    content="<self-contained content — what AND why>",
    memory_type="<one of: fact, preference, procedure, context, episode>",
    tags=["<tag1>", "<tag2>"],    # 2-4 specific tags. Not "code", "project", "general".
    project="<name>",             # Omit if memory_set_project was called, or if global.
)
```

### Pick memory_type

| Type | Use when |
|------|----------|
| `fact` | Objective truth about code, system, API |
| `preference` | How user wants things done |
| `procedure` | Step-by-step workflow |
| `context` | Current project state — decays via recency scoring |
| `episode` | Notable event, debugging session, incident |

### Content must be self-contained

A fresh agent with zero conversation context reads this content. It must make complete sense alone.

```
Good: "User prefers Ruff over Black for formatting because it's faster
       and handles import sorting in a single tool"
Bad:  "User prefers Ruff"               — missing the why
Bad:  "As discussed, switch to Ruff"     — references conversation
Bad:  "The formatter preference"         — meaningless alone
```

### Tags must be specific

```
Good: ["ruff", "formatting", "linting"]
Bad:  ["code", "tools", "project"]
```

## Step 5: Conflict check + summary

```python
memory_conflicts(project="<project if scoped>")
```

If new conflicts appear:

> "Noticed conflict: [X] vs [Y]. Want me to resolve it?"

Summarize what was saved:

> "Saved 3 memories: Ruff preference, deploy procedure, auth architecture. Superseded 1 outdated API endpoint memory."

If nothing was saved (all skipped as duplicates), say so:

> "Reviewed session — no new knowledge to capture. Existing memories already cover it."

## Boundaries

- This skill stores memories only. No reorganization, no cleanup — that's `/rekal-hygiene`.
- No conversation creation — captures knowledge FROM conversations, not about them.
- Never stores secrets, API keys, passwords, tokens.
- Ask user before storing sensitive or personal content (health, finance, relationships).
