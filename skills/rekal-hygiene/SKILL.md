---
name: rekal-hygiene
description: >
  Periodic memory maintenance and cleanup. Finds duplicates, conflicts, stale
  conversations, and quality issues in the memory database. Proposes fixes for
  user approval — never auto-deletes or auto-modifies. Use when user says
  "clean up memories", "memory maintenance", "check memory health", or invokes
  /rekal-hygiene. Run monthly or when conflicts are piling up.
disable-model-invocation: true
allowed-tools: mcp__rekal__memory_health mcp__rekal__memory_conflicts mcp__rekal__memory_similar mcp__rekal__memory_search mcp__rekal__memory_topics mcp__rekal__memory_timeline mcp__rekal__memory_related mcp__rekal__memory_supersede mcp__rekal__memory_delete mcp__rekal__memory_update mcp__rekal__memory_link mcp__rekal__memory_set_project mcp__rekal__conversation_stale mcp__rekal__conversation_threads
---

# rekal-hygiene — Memory Maintenance

Find problems in rekal database — duplicates, conflicts, stale data, quality issues — and propose fixes. Every change requires explicit user approval.

## Workflow

### Step 1: Health overview

Run `memory_health`. Report brief summary:

> "142 memories across 3 projects, spanning 6 months. 4 conflicts detected."

### Step 2: Conflict resolution

Run `memory_conflicts` (globally, then per-project if many).

Per conflict pair, present side-by-side + propose resolution:

- **One outdated** → propose `memory_supersede` (old with new)
- **Both valid, different scope** → propose keeping both, add project scope
- **Genuine contradiction** → ask user which is correct
- **False positive** → propose removing `contradicts` link

Format as numbered list:

> 1. **"API uses v2"** vs **"API migrated to v3"**
>    Proposal: Supersede v2 with v3. [approve/reject]
> 2. **"Use PostgreSQL"** vs **"Use ClickHouse for analytics"**
>    Proposal: Not real conflict — different use cases. Remove link. [approve/reject]

### Step 3: Duplicate sweep

Run `memory_topics` to find clusters. Per high-count topic, run `memory_search` to surface near-duplicates.

Per duplicate group:
- Find most complete/accurate version
- Propose superseding others into it

> **Duplicates (formatting preferences):**
> - mem_abc: "User prefers Ruff" (2024-01)
> - mem_def: "User prefers Ruff over Black for formatting" (2024-03)
> - mem_ghi: "Use Ruff, not Black. Also handles import sorting." (2024-06)
>
> Proposal: Keep mem_ghi (most complete), supersede others into it.

### Step 4: Stale conversation cleanup

Run `conversation_stale(days=30)` + `conversation_threads`.

- Conversations with 0 memories = safe cleanup candidates
- Conversations with memories = fine, memories persist regardless

Only flag truly empty ones. Conversations are cheap storage.

### Step 5: Quality audit

Sample via `memory_timeline(limit=20)`, check for:

- **Vague** — too short/generic. Propose reword or delete.
- **Unscoped** — project-specific without project tag. Propose adding scope.
- **Mistyped** — wrong `memory_type`. Propose correction via `memory_update`.
- **Stale context** — `context`-type older than 60 days, probably outdated. Propose: upgrade to `fact` if still true, delete if stale.

Skip memories < 24 hours old — too fresh to judge.

### Step 6: Present action plan

Compile all proposals:

> **Hygiene report:**
> - 4 conflicts: 2 supersede, 1 keep-both, 1 remove-link
> - 6 duplicate clusters: 12 memories → 6
> - 3 unscoped memories to re-scope
> - 2 stale context memories to review
>
> Approve all? Or review individually?

Wait for explicit approval before executing.

### Step 7: Execute approved changes

After approval only:
- Run approved supersedes, deletes, updates, link changes
- Report what was done
- Run `memory_health` again to confirm improvement

## Safety rules

- **Never auto-delete.** Every deletion needs user approval.
- **Never auto-modify.** Supersedes/updates proposed, not executed.
- **Skip memories < 24h old.** Fresh memories need time to settle.
- **Supersede over delete.** Preserves history via links; delete only for worthless entries.
- **No new memories.** That's `/rekal-save`.

## Frequency

Run monthly or when conflicts accumulate. `/rekal-save` handles per-session dedup.

## Large databases (500+ memories)

Don't audit everything in one pass. Prioritize:
1. Conflicts (highest impact)
2. Highest-count topic clusters (most duplicates)
3. Recent timeline (freshest quality issues)

Offer follow-up session if lot to review.
