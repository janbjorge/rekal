---
name: rekal-init
description: >
  Bootstrap rekal memory for a project. Scans the codebase for architecture,
  conventions, dependencies, workflows, and config — then stores durable
  knowledge as properly typed, tagged, deduplicated memories. Use when starting
  rekal on a new project, or when user says "init rekal", "bootstrap memory",
  "populate rekal", "scan project". Trigger: /rekal-init.
disable-model-invocation: true
allowed-tools: Read Glob Grep mcp__rekal__memory_search mcp__rekal__memory_store mcp__rekal__memory_supersede mcp__rekal__memory_set_project mcp__rekal__memory_health mcp__rekal__memory_conflicts
---

Bootstrap rekal memory from a codebase. Goal: a fresh agent in a new session has enough context to work effectively without the user repeating themselves.

## Step 0: Pre-flight

```python
memory_health()
```

Report current state. If the project already has memories, warn:

> "Found 47 memories for project 'backend'. This will add new knowledge and supersede outdated entries. Continue?"

Wait for confirmation before proceeding. For a fresh database, continue automatically.

## Step 1: Identify the project

Determine the project name from the working directory, git remote, or config files. Prefer short, lowercase names: `rekal`, `backend`, `myapp`.

```python
memory_set_project(project="<name>")
```

## Step 2: Scan for knowledge sources

Search for these files in priority order. Read every file that exists. Skip what's missing.

### Tier 1 — High-signal project docs (read fully)

```
CLAUDE.md, AGENTS.md, .claude/CLAUDE.md
README.md, README.rst, README.txt
CONTRIBUTING.md, ARCHITECTURE.md, DESIGN.md, ADR/*.md
docs/architecture.md, docs/design.md, docs/conventions.md
```

### Tier 2 — Config and dependency manifests (read fully)

```
pyproject.toml, setup.cfg, setup.py, requirements*.txt
package.json, tsconfig.json, deno.json
Cargo.toml, go.mod, Gemfile, build.gradle, pom.xml
Makefile, Justfile, Taskfile.yml
docker-compose.yml, Dockerfile
.github/workflows/*.yml, .gitlab-ci.yml, Jenkinsfile
.env.example, .envrc
```

### Tier 3 — Structural exploration (skim, don't read every file)

```
# Directory tree — top 2 levels via Glob
Glob pattern: "**/*/", max-depth 2

# Entry points
main.py, app.py, index.ts, main.go, lib.rs, src/main.*
manage.py, wsgi.py, asgi.py

# Config files that reveal conventions
.editorconfig, .prettierrc, .eslintrc*, ruff.toml, .ruff.toml
mypy.ini, pyrightconfig.json, tox.ini
```

Do NOT read:
- Source code files beyond entry points (agents can read those on demand)
- Lock files (package-lock.json, uv.lock, Cargo.lock, yarn.lock)
- Generated files, build artifacts, node_modules, dist/, .git/
- Binary files, images, fonts

## Step 3: Extract knowledge candidates

For each file read, extract knowledge that passes this filter:

```
Would a fresh agent benefit from knowing this AND
it cannot be trivially discovered by reading one file?
├── YES → candidate
└── NO  → skip
```

### What to extract

| Category | Source files | Example memory |
|----------|-------------|----------------|
| **Architecture** | README, ARCHITECTURE, AGENTS.md | "Three-layer arch: MCP adapter → tool wrappers → SqliteDatabase. All SQL lives in sqlite_adapter.py" |
| **Conventions** | CONTRIBUTING, CLAUDE.md, linter configs | "No underscore prefixes on attributes. Public by default. No mutable globals." |
| **Dependencies & stack** | pyproject.toml, package.json, Cargo.toml | "Python 3.11+, key deps: mcp[cli], aiosqlite, sqlite-vec, fastembed, pydantic" |
| **Build & test** | Makefile, CI configs, pyproject.toml | "CI: ruff check, ruff format --check, ty check, pytest 100% coverage required" |
| **Deploy & infra** | Docker, CI/CD, Makefile | "Deploy via git tag vX.Y.Z → CI builds and publishes to PyPI" |
| **Project structure** | Directory tree, entry points | "Entry point: rekal/cli.py. MCP server: rekal/adapters/mcp_adapter.py" |
| **Key decisions** | ADRs, DESIGN.md, README | "Chose SQLite over Postgres for zero-config single-file deployment" |
| **Workflows** | CONTRIBUTING, Makefile, CI | "PR workflow: branch from main, all CI checks must pass, squash merge" |

### What NOT to extract

- Verbatim content from CLAUDE.md or AGENTS.md — those are loaded every session. DO synthesize cross-cutting knowledge that spans multiple files into single memories.
- Line numbers, function signatures, variable names — trivially re-discoverable
- Boilerplate from templates (default CI configs, standard Dockerfile patterns)
- Version numbers that change frequently (pin knowledge to concepts, not versions)
- License text, badges, marketing copy

## Step 4: Deduplicate and store

For EVERY candidate, before storing:

```python
memory_search(query="<candidate topic>", limit=5)
```

Apply:

```
Search results?
├── No match         → memory_store(content, memory_type, tags)
├── Same info exists → SKIP (duplicate)
├── Outdated version → memory_supersede(old_id, new_content)
└── Contradicts      → memory_supersede(old_id, new_content) — include what changed
```

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
      FastMCP server and manages lifespan → tool modules in adapters/tools/ are
      thin @mcp.tool() wrappers → SqliteDatabase dataclass holds all SQL queries.
      New tool = add method to SqliteDatabase + thin wrapper in tools/*.py."

Bad:  "Three-layer architecture"          — too vague
Bad:  "See AGENTS.md for architecture"    — not self-contained
Bad:  "As described in the README..."     — references source
```

### Tags must be specific

```
Good: ["architecture", "mcp", "sqlite", "tool-pattern"]
Bad:  ["code", "project", "structure"]
```

## Step 5: Batch efficiently

Group related candidates. Store in logical order:

1. Project identity and stack
2. Architecture and structure
3. Conventions and style
4. Build, test, CI
5. Deploy and infra
6. Key decisions

This ordering helps if the user interrupts — most valuable knowledge lands first.

## Step 6: Verify and report

```python
memory_health()
memory_conflicts()
```

Summarize what was captured:

> **rekal init complete for `myproject`:**
> - 12 memories stored (4 fact, 3 preference, 3 procedure, 2 context)
> - 2 existing memories superseded
> - 0 conflicts
>
> **Captured:**
> - Architecture: three-layer MCP → tools → DB
> - Stack: Python 3.11, mcp[cli], aiosqlite, sqlite-vec
> - Conventions: no Any, no underscore prefixes, dataclasses everywhere
> - CI: ruff + ty + pytest 100% coverage
> - Test rules: no mocking, real SQLite, deterministic embeddings
>
> Run `/rekal-hygiene` later to clean up any issues that emerge.

## Boundaries

- **Read-only on the codebase.** Never modify project files.
- **No secrets.** Skip .env files with real values. Only read .env.example.
- **No deep source code analysis.** Extract architecture from docs, config, and entry points — not by reading every .py/.ts/.rs file. Agents read source on demand.
- **Dedup is mandatory.** Never skip the search-before-store step.
- **Ask before overwriting.** If existing memories conflict with what the codebase says, present both and ask which is correct.
- **This skill populates. `/rekal-save` maintains.** Don't try to capture everything — init covers the 80% that prevents users from repeating themselves. Session-level learning happens via `/rekal-save`.

## Large codebases

For monorepos or projects with 10+ top-level directories:

1. Ask user which area to focus on, or scan all top-level READMEs first
2. Process one area at a time
3. After each area, report progress and ask to continue

> "Scanned `services/auth` — 5 memories stored. Continue with `services/payments`?"

## Re-running

Safe to re-run. Dedup ensures no duplicates. Changed knowledge gets superseded. New files get picked up. Report what changed vs last run:

> "Re-init for `backend`: 2 new memories, 3 superseded, 14 unchanged (skipped)."
