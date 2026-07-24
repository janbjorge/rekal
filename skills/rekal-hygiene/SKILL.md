---
name: rekal-hygiene
description: >
  Periodic memory maintenance and cleanup. Finds duplicates, contradictions,
  and quality issues in the memory database. Proposes fixes for user approval.
  Never auto-deletes or auto-modifies. Use when user says "clean up memories",
  "memory maintenance", "check memory health", or invokes /rekal-hygiene.
  Run monthly or when recall quality degrades.
disable-model-invocation: true
allowed-tools: mcp__rekal__memory_build_context mcp__rekal__memory_store mcp__rekal__memory_delete Bash(rekal:*)
---

Find and fix problems in the rekal database. Every change requires explicit user approval. Never auto-delete. Never auto-modify.

## Step 1: Health overview

```bash
rekal health
```

Report one line:

> "142 memories across 3 projects, spanning 6 months."

## Step 2: Audit the full store

```bash
rekal export
```

This prints every memory as JSON (id, content, project, tags, timestamps).
Read it and group entries that cover the same fact/preference/procedure.

Per finding, classify and propose:

```
Issue?
├── Duplicates / same topic stored twice
│   └── Identify the most complete version, propose replacing the others:
│       memory_store(content="<best content>", replaces="<older id>")
│
├── Contradiction: one outdated, one current
│   └── Propose: memory_store(content="<current content>", replaces="<outdated id>")
│
├── Contradiction: unclear which is correct
│   └── Ask user: "Which is correct? [A] or [B]?"
│
├── Project-specific content but project missing
│   └── Propose: memory_store(content="<same content>", project="<name>", replaces="<id>")
│
├── Content < 20 characters or meaningless alone
│   └── Propose: reword with more context (store with replaces), or delete if worthless
│
└── No issues → skip
```

Skip memories created < 24 hours ago: too fresh to judge.

## Step 3: Present action plan

Compile ALL proposals into one summary:

> **Hygiene report:**
> - 3 duplicate clusters: 8 memories → 3
> - 2 contradictions: 1 replace, 1 needs your call
> - 2 unscoped memories to re-scope
>
> Approve all? Or review individually?

Wait for explicit approval. Do NOT execute anything until user approves.

## Step 4: Execute approved changes

After user approves (all or specific items):

1. Run each approved operation:
   - `memory_store(..., replaces=<id>)` for duplicates, corrections, re-scoping
   - `memory_delete` only for worthless entries (user explicitly approved)
   - `rekal prune --older-than-days N --yes` for approved bulk cleanup
2. Report what was done, one line per action
3. Run `rekal health` again, report improvement:

> "Done. 142 → 134 memories. 3 duplicate clusters resolved."

## Safety rules

These are hard rules. No exceptions.

- **Never auto-delete.** Every deletion requires user approval.
- **Never auto-modify.** All changes proposed first, executed after approval.
- **Skip < 24h old memories.** Too fresh to judge quality.
- **Replace over delete.** `replaces` keeps the topic covered. Delete only genuinely worthless entries.
- **No new memories.** This skill cleans. `/rekal-save` stores.
- **No bulk approve without listing.** Always show what will change before asking for approval.

## Large databases (500+ memories)

Do NOT audit everything in one pass. Prioritize in this order:

1. Contradictions: highest impact on trust
2. Topics appearing >= 3 times in the export: most duplicate accumulation
3. Newest 30 days of entries: freshest quality issues

After completing priority items, ask:

> "Covered contradictions and top duplicate clusters. Want me to continue with a deeper audit?"
