---
name: rekal-hygiene
description: >
  Periodic memory maintenance and cleanup. Finds duplicates, conflicts, stale
  conversations, and quality issues in the memory database. Proposes fixes for
  user approval — never auto-deletes or auto-modifies. Use when user says
  "clean up memories", "memory maintenance", "check memory health", or invokes
  /rekal-hygiene. Run monthly or when conflicts are piling up.
disable-model-invocation: true
allowed-tools: mcp__rekal__memory_health mcp__rekal__memory_conflicts mcp__rekal__memory_search mcp__rekal__memory_topics mcp__rekal__memory_timeline mcp__rekal__memory_supersede mcp__rekal__memory_delete mcp__rekal__memory_update mcp__rekal__memory_link mcp__rekal__memory_set_project mcp__rekal__conversation_stale mcp__rekal__conversation_threads
---

Find and fix problems in the rekal database. Every change requires explicit user approval. Never auto-delete. Never auto-modify.

## Step 1: Health overview

```python
memory_health()
```

Report one line:

> "142 memories across 3 projects, spanning 6 months. 4 conflicts detected."

## Step 2: Resolve conflicts

```python
memory_conflicts()  # global first
```

If > 10 conflicts, also run per-project: `memory_conflicts(project="<name>")`

Per conflict pair, classify and propose:

```
Conflict type?
├── One outdated, one current
│   └── Propose: memory_supersede(old_id="<outdated>", new_content="<current content>")
│
├── Both valid, different scope (e.g. PostgreSQL for OLTP, ClickHouse for analytics)
│   └── Propose: Keep both. Add project scope if missing.
│
├── Genuine contradiction, unclear which is correct
│   └── Ask user: "Which is correct? [A] or [B]?"
│
└── False positive (not actually contradictory)
    └── Propose: Remove contradicts link.
        memory_link(from_id="<id_a>", to_id="<id_b>", relation="related_to")
        — or memory_delete the link if truly unrelated
```

Present as numbered list:

> 1. **"API uses v2"** vs **"API migrated to v3"**
>    Proposal: Supersede v2 with v3. [approve/reject]
> 2. **"Use PostgreSQL"** vs **"Use ClickHouse for analytics"**
>    Proposal: Not a real conflict — different use cases. Remove link. [approve/reject]

## Step 3: Find duplicates

```python
memory_topics()  # or memory_topics(project="<name>")
```

For every topic cluster with count >= 3, search for near-duplicates:

```python
memory_search(query="<topic name>", limit=10)
```

Read results. Group memories covering the same fact/preference/procedure.

Per duplicate group:
1. Identify the most complete and accurate version
2. Propose superseding all others into it

Format:

> **Duplicates (formatting preferences):**
> - `mem_abc`: "User prefers Ruff" (2024-01)
> - `mem_def`: "User prefers Ruff over Black for formatting" (2024-03)
> - `mem_ghi`: "Use Ruff, not Black. Also handles import sorting." (2024-06)
>
> Proposal: Keep `mem_ghi` (most complete). Supersede `mem_abc` and `mem_def` into it.
> ```python
> memory_supersede(old_id="mem_abc", new_content="<content from mem_ghi>")
> memory_supersede(old_id="mem_def", new_content="<content from mem_ghi>")
> ```

## Step 4: Stale conversations

```python
conversation_stale(days=30)
conversation_threads(limit=20)
```

Flag conversations with 0 memories as cleanup candidates. Conversations with memories are fine — memories persist regardless.

Conversations are cheap storage. Only flag truly empty ones older than 30 days.

## Step 5: Quality audit

```python
memory_timeline(limit=20)
```

Skip memories created < 24 hours ago — too fresh to judge.

Per memory, check against these rules:

```
Quality issue?
├── Content < 20 characters
│   └── Propose: reword with more context, or delete if worthless
│       memory_update(memory_id="<id>", content="<expanded content>")
│
├── Project-specific content but project=None
│   └── Propose: add scope
│       memory_update(memory_id="<id>", project="<correct project>") — NOT SUPPORTED
│       Note: memory_update cannot set project. Propose memory_supersede with project= set.
│
├── Wrong memory_type (e.g. a procedure stored as fact)
│   └── Propose: fix type
│       memory_update(memory_id="<id>", memory_type="<correct type>")
│
├── context-type AND older than 60 days
│   └── Propose: still true? → memory_update to fact. Stale? → memory_delete.
│       Ask user which.
│
└── No issues → skip
```

## Step 6: Present action plan

Compile ALL proposals from steps 2-5 into one summary:

> **Hygiene report:**
> - 4 conflicts: 2 supersede, 1 keep-both, 1 remove-link
> - 3 duplicate clusters: 8 memories → 3
> - 2 unscoped memories to re-scope
> - 1 stale context memory to review
> - 0 stale conversations
>
> Approve all? Or review individually?

Wait for explicit approval. Do NOT execute anything until user approves.

## Step 7: Execute approved changes

After user approves (all or specific items):

1. Run each approved operation:
   - `memory_supersede` for duplicates and outdated conflicts
   - `memory_update` for type/tag corrections
   - `memory_delete` only for worthless entries (user explicitly approved)
   - `memory_link` for relationship corrections
2. Report what was done, one line per action
3. Run `memory_health()` again, report improvement:

> "Done. 142 → 134 memories. 4 → 0 conflicts. 3 duplicate clusters resolved."

## Safety rules

These are hard rules. No exceptions.

- **Never auto-delete.** Every deletion requires user approval.
- **Never auto-modify.** All changes proposed first, executed after approval.
- **Skip < 24h old memories.** Too fresh to judge quality.
- **Supersede over delete.** Preserves history via links. Delete only for genuinely worthless entries.
- **No new memories.** This skill cleans. `/rekal-save` stores.
- **No bulk approve without listing.** Always show what will change before asking for approval.

## Large databases (500+ memories)

Do NOT audit everything in one pass. Prioritize in this order:

1. Conflicts — highest impact on search quality
2. Topic clusters with count >= 5 — most duplicate accumulation
3. Recent timeline (last 30 days) — freshest quality issues

After completing priority items, ask:

> "Covered conflicts and top duplicate clusters. Want me to continue with a deeper audit?"
