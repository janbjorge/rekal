---
name: rekal-init
description: >
  Bootstrap rekal memory for a project. Scans the codebase for architecture,
  conventions, dependencies, workflows, and config — then stores durable
  knowledge as properly typed, tagged, deduplicated memories. Use when starting
  rekal on a new project, or when user says "init rekal", "bootstrap memory",
  "populate rekal", "scan project". Trigger: /rekal-init.
disable-model-invocation: true
allowed-tools: Read Glob Grep mcp__rekal__memory_search mcp__rekal__memory_store mcp__rekal__memory_supersede mcp__rekal__memory_set_project mcp__rekal__memory_health mcp__rekal__memory_conflicts mcp__rekal__project_scan
---

Bootstrap rekal memory from a codebase.

**Target: 40-80 memories for a substantial project.** This skill uses the `project_scan` tool to programmatically extract code structure, then you read docs to fill in the rest. Do not stop after storing scan results — the docs phase is equally important.

## Step 0: Pre-flight

```python
memory_health()
```

Report current state. If the project already has memories, warn and wait for confirmation. For a fresh database, continue automatically.

## Step 1: Identify the project

Determine the project name from the working directory, git remote, or config files. Prefer short, lowercase names: `rekal`, `backend`, `myapp`.

```python
memory_set_project(project="<name>")
```

## Step 2: Run the automated scanner

Call `project_scan` with the project root directory. This programmatically discovers:
- All Python packages and their classes, routes, models, exceptions, enums
- SQL tables from .sql files
- gRPC services/messages from .proto files
- Dependencies from pyproject.toml / package.json
- Project structure overview

```python
scan = project_scan(directory="<absolute path to project root>")
```

The result contains `suggested_memories` — a list of pre-synthesized memory candidates ready to store. It also contains `doc_files_to_read`, `adr_files_to_read`, and `config_files_to_read` for the next steps.

## Step 3: Store scan results

**On a fresh DB, skip dedup — store directly.** On re-init, search before each store.

For each item in `scan["suggested_memories"]`:

```python
memory_store(
    content=item["content"],
    memory_type=item["memory_type"],
    tags=item["tags"],
)
```

Store ALL of them. Do not filter or skip. The scanner already did the filtering.

## Step 4: Read docs and extract additional memories

The scanner finds code structure but can't read prose. Now read the files listed in the scan results and extract knowledge that the scanner missed.

### 4a. Documentation files (`doc_files_to_read`)

Read each file. For each, extract memories about:
- Architecture decisions and rationale
- Conventions and style rules (→ `preference` type)
- Workflows and procedures (→ `procedure` type)
- Project-specific domain knowledge
- Current state / in-progress work (→ `context` type)

Do NOT store verbatim content from CLAUDE.md or AGENTS.md — those are loaded every session. DO synthesize cross-cutting knowledge that spans multiple files.

### 4b. ADR files (`adr_files_to_read`)

Read each ADR. Store 1 memory per ADR capturing: the decision, the context, and the key reason.

### 4c. Config files (`config_files_to_read`)

Read CI/CD configs, Makefiles, Dockerfiles. Extract:
- CI pipeline steps and requirements (→ `procedure`)
- Build/deploy workflows (→ `procedure`)
- Docker setup details (→ `fact`)
- Environment variables needed (→ `fact`)

### 4d. Test patterns

```
Glob: "tests/conftest.py", "test/conftest.py"
Glob: "tests/**/*.py" — skim file names for patterns
```

Store: test framework, fixtures, DB strategy, parallelization.

## Step 5: Deduplicate and store

For memories from Step 4 (not Step 3 — those are already unique):

```python
memory_search(query="<candidate topic>", limit=5)
```

```
Search results?
├── No match         → memory_store(content, memory_type, tags)
├── Same info exists → SKIP (duplicate)
├── Outdated version → memory_supersede(old_id, new_content)
└── Contradicts      → memory_supersede(old_id, new_content)
```

**On a fresh DB, skip search — store directly.**

### Pick memory_type

| Type | Use for |
|------|---------|
| `fact` | Architecture, stack, structure, dependencies |
| `preference` | Coding conventions, style rules, tool choices |
| `procedure` | Build, test, deploy, PR workflows |
| `context` | Current project state, in-progress migrations |

Do NOT use `episode` — init captures knowledge, not events.

### Content rules

Every memory must be **self-contained**. A fresh agent with zero context reads it.

```
Good: "rekal uses three-layer architecture: MCP adapter (mcp_adapter.py) creates
      FastMCP server → tool modules in adapters/tools/ are thin @mcp.tool()
      wrappers → SqliteDatabase dataclass holds all SQL queries."

Bad:  "Three-layer architecture"          — too vague
Bad:  "See AGENTS.md for architecture"    — not self-contained
```

### Tags must be specific

```
Good: ["architecture", "mcp", "sqlite", "tool-pattern"]
Bad:  ["code", "project", "structure"]
```

## Step 6: Verify and report

```python
memory_health()
memory_conflicts()
```

Summarize what was captured:

> **rekal init complete for `myproject`:**
> - 45 memories stored (30 fact, 5 preference, 7 procedure, 3 context)
> - 2 existing memories superseded
> - 0 conflicts
>
> **From scanner (automated):**
> - Module map: 8 packages discovered
> - API surface: 47 endpoints
> - Domain models: 12 types
> - Exceptions: 5 types
> - DB tables: 15
> - Stack: Python 3.11, FastAPI, asyncpg
>
> **From docs (manual extraction):**
> - Architecture decisions from 6 ADRs
> - Conventions: ruff, mypy, 100-char lines
> - CI: Azure Pipelines, 8 parallel pytest jobs
> - Domain glossary: warehouse terms
>
> Run `/rekal-save` at end of future sessions to maintain.

## Boundaries

- **Read-only on the codebase.** Never modify project files.
- **No secrets.** Skip .env files with real values. Only read .env.example.
- **Store all scan results.** The scanner already filtered — don't second-guess it.
- **Dedup is mandatory for doc-derived memories.** Never skip the search-before-store step (except on fresh DB).
- **Ask before overwriting.** If existing memories conflict with what the codebase says, present both and ask which is correct.
- **Session-level learning via `/rekal-save`.** Init captures the structural 80%. Runtime discoveries happen via `/rekal-save`.

## Large codebases

For monorepos or projects with 10+ top-level directories:

1. Run `project_scan` — it handles large codebases efficiently
2. Store all scan results (this is fast — no manual reading needed)
3. For docs: scan all top-level READMEs first, then process by area
4. Only ask user to pick focus areas if 50+ top-level directories

## Re-running

Safe to re-run. The scanner produces the same results. Dedup ensures no duplicates. Changed knowledge gets superseded. Report what changed vs last run.
