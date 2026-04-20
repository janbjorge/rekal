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

Bootstrap rekal memory from a codebase.

## HARD REQUIREMENTS — read before starting

1. **Target: 40-80 memories** for a substantial project. Fewer than 30 = failure, go back and extract more.
2. **You MUST execute Tier 4** (source code scanning). This is not optional. Most memories come from here.
3. **You MUST run every Grep command** listed in Tier 4c. Each grep that returns results = at least 1 memory.
4. **You MUST store at least 1 memory per source module** found in the codebase.
5. **You MUST run the Step 7 self-check** before reporting. If under 30, loop back.

Common failure: agent reads docs + config (Tiers 1-2), stores ~20 memories, skips Tiers 4-5 entirely. **DO NOT DO THIS.**

Goal: a fresh agent in a new session has enough context to work effectively without the user repeating themselves. Every module, every domain concept, every non-obvious pattern deserves a memory.

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

### Tier 1 — High-signal project docs (read fully) → expect 5-15 memories

```
CLAUDE.md, AGENTS.md, .claude/CLAUDE.md
README.md, README.rst, README.txt
CONTRIBUTING.md, ARCHITECTURE.md, DESIGN.md, ADR/*.md
docs/architecture.md, docs/design.md, docs/conventions.md
```

Each ADR = at least 1 memory. README with architecture section = 2-3 memories. CLAUDE.md conventions = 3-5 memories.

### Tier 2 — Config and dependency manifests (read fully) → expect 5-10 memories

```
pyproject.toml, setup.cfg, setup.py, requirements*.txt
package.json, tsconfig.json, deno.json
Cargo.toml, go.mod, Gemfile, build.gradle, pom.xml
Makefile, Justfile, Taskfile.yml
docker-compose.yml, Dockerfile
.github/workflows/*.yml, .gitlab-ci.yml, Jenkinsfile
.env.example, .envrc
```

Extract: stack + versions, key deps with purpose, CI pipeline steps, Docker base image + multi-stage setup, env vars needed.

### Tier 3 — Structural exploration → expect 3-5 memories

```
# Directory tree — top 3 levels via Glob
Glob pattern: "**/*/", max-depth 3

# Entry points
main.py, app.py, index.ts, main.go, lib.rs, src/main.*
manage.py, wsgi.py, asgi.py

# Config files that reveal conventions
.editorconfig, .prettierrc, .eslintrc*, ruff.toml, .ruff.toml
mypy.ini, pyrightconfig.json, tox.ini
```

Store: project layout overview (which dirs hold what), entry points and how app boots, linter/formatter config summary.

### Tier 4 — Source code structure → MANDATORY, expect 15-30 memories

**THIS IS THE MOST IMPORTANT TIER. You MUST execute every step below. Do NOT skip to Step 3 after reading docs and config. The majority of useful memories come from here.**

Scan source modules to extract architecture that lives in code, not docs.

**Procedure — execute ALL of these in order:**

**4a. Discover all modules.** Run these Glob/Grep commands. Do not skip any.

```
Glob: "src/**/__init__.py", "<pkg>/**/__init__.py"
Glob: "src/*/", "<pkg>/*/"
```

List every module found. This is your checklist — you MUST store at least 1 memory per module.

**4b. For EACH module found in 4a**, read its `__init__.py` and 1-2 key files (the largest .py/.ts/.rs files, or files named models/routes/handlers/schema). Store a memory describing: what it does, key types, how it connects to other modules.

**4c. Grep across entire codebase for domain patterns.** Run ALL of these:

```
Grep: "^class " — list all classes, store memory grouping them by purpose
Grep: "BaseModel|dataclass|TypedDict|Protocol" — find domain models, store summary
Grep: "router|blueprint|app.route|@app.|@router." — find API surface, store summary
Grep: "CREATE TABLE|class.*Table|mapped_column" — find DB schema, store summary
Grep: "class.*Error|class.*Exception" — find error types, store summary
Grep: "class.*Enum|Literal\[" — find enums/constants, store as domain glossary
Grep: "subscribe|publish|emit|Signal|Stream|on_event" — find event patterns, store if found
```

Each grep that returns results → at least 1 memory summarizing findings.

**4d. Read model/schema files.** Find and read:
- Files in directories named `models/`, `schemas/`, `types/`, `entities/`
- Migration files (latest 3-5)
- Proto files, GraphQL schemas, OpenAPI specs

Store: entity relationships, key fields, constraints worth knowing.

**If you finish Tier 4 with fewer than 10 memories, you skipped steps. Go back and re-execute 4a-4d.**

### Tier 5 — Test structure and patterns → expect 2-5 memories

```
# Test organization
Glob: "tests/**/*.py", "test/**/*.py", "**/*_test.py", "**/*_test.go"
Read: conftest.py, test helpers, fixtures

# What's being tested reveals what matters
Grep: "def test_", "it(", "describe(" — skim test names for domain concepts
```

Store: test framework + runner config, key fixtures/factories, test DB strategy, parallelization setup.

Do NOT read:
- Lock files (package-lock.json, uv.lock, Cargo.lock, yarn.lock)
- Generated files, build artifacts, node_modules, dist/, .git/
- Binary files, images, fonts
- Individual test implementations (test names suffice)
- Vendored/third-party code

## Step 3: Extract knowledge candidates

For each file read, extract knowledge that passes this filter:

```
Would a fresh agent work faster or make fewer mistakes knowing this?
├── YES → candidate
└── NO  → skip
```

Bias toward storing. The cost of a redundant memory (superseded later) is low.
The cost of a missing memory (user repeats themselves, agent makes wrong assumption) is high.

### What to extract

| Category | Source files | Example memory |
|----------|-------------|----------------|
| **Architecture** | README, ARCHITECTURE, AGENTS.md, source structure | "Three-layer arch: MCP adapter → tool wrappers → SqliteDatabase. All SQL lives in sqlite_adapter.py" |
| **Module map** | __init__.py, imports, directory structure | "services/ has 6 modules: auth (JWT+RBAC), billing (Stripe), notifications (email+push), search (Elasticsearch), inventory (warehouse ops), reporting (async PDF gen)" |
| **Domain model** | Models, schemas, migrations, enums | "Core entities: Order (stateful, FSM), Product (immutable after publish), Warehouse (has zones/bins), User (has roles via RBAC). All IDs are NewType UUIDs." |
| **API surface** | Routes, controllers, gRPC protos | "REST API: /api/v2/ prefix. 47 endpoints across 8 routers. Auth via Bearer JWT. Rate limited 100/min per user." |
| **Data layer** | ORM models, migrations, raw SQL | "Postgres 15. 34 tables. Key: orders→order_lines→products. Soft deletes on orders. Partitioned by created_at on events table." |
| **Conventions** | CONTRIBUTING, CLAUDE.md, linter configs | "No underscore prefixes on attributes. Public by default. No mutable globals." |
| **Dependencies & stack** | pyproject.toml, package.json, Cargo.toml | "Python 3.11+, key deps: mcp[cli], aiosqlite, sqlite-vec, fastembed, pydantic" |
| **Build & test** | Makefile, CI configs, pyproject.toml | "CI: ruff check, ruff format --check, ty check, pytest 100% coverage required" |
| **Test patterns** | conftest.py, test structure, fixtures | "Tests use testcontainers for Postgres+Redis. Factory pattern via conftest fixtures. 8 parallel pytest workers split by module." |
| **Deploy & infra** | Docker, CI/CD, Makefile | "Deploy via git tag vX.Y.Z → CI builds and publishes to PyPI" |
| **Project structure** | Directory tree, entry points | "Entry point: rekal/cli.py. MCP server: rekal/adapters/mcp_adapter.py" |
| **Key decisions** | ADRs, DESIGN.md, README | "Chose SQLite over Postgres for zero-config single-file deployment" |
| **Workflows** | CONTRIBUTING, Makefile, CI | "PR workflow: branch from main, all CI checks must pass, squash merge" |
| **Error handling** | Exception classes, error middleware | "Custom exception hierarchy: AppError → {ValidationError, NotFoundError, AuthError}. All caught by error_middleware → JSON error response." |
| **Event/async patterns** | Message queues, signals, streams | "Redis Streams for async events. 12 event types. Consumers in workers/ dir. Retry with exponential backoff, DLQ after 5 failures." |
| **Cross-cutting concerns** | Middleware, decorators, mixins | "Auth decorator @require_role('admin') on protected endpoints. Audit logging via middleware on all mutations. Request ID propagated via contextvars." |
| **Domain glossary** | README, docs, code comments, enums | "Content code = barcode type for warehouse items. Pick = retrieve item from bin. Putaway = store item in bin. Wave = batch of picks optimized for walk path." |

### What NOT to extract

- Verbatim content from CLAUDE.md or AGENTS.md — those are loaded every session. DO synthesize cross-cutting knowledge that spans multiple files into single memories.
- Individual function signatures or line numbers — trivially re-discoverable
- Boilerplate from templates (default CI configs, standard Dockerfile patterns)
- Version numbers that change frequently (pin knowledge to concepts, not versions)
- License text, badges, marketing copy

## Step 4: Deduplicate and store

For each candidate, search before storing to avoid duplicates:

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

**On a fresh DB (no existing memories), skip the search step entirely** — there's nothing to dedup against. Just store directly. This dramatically speeds up init on new projects.

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
2. High-level architecture and structure
3. Module map (what each module/package does)
4. Domain model and key entities
5. API surface and routes
6. Data layer (DB schema, key tables, relationships)
7. Conventions and style
8. Build, test, CI
9. Test patterns and fixtures
10. Deploy and infra
11. Event/async patterns and cross-cutting concerns
12. Domain glossary
13. Key decisions (ADRs)

This ordering helps if the user interrupts — most valuable knowledge lands first. Groups 3-6 are where large codebases need the most coverage.

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

## Step 7: Self-check — MANDATORY, do NOT skip

After Step 6, count memories stored. **You MUST run this checklist before finishing.**

**Check 1: Total count.**
```
memories_stored >= 30 on a project with 5+ source modules?
├── NO  → STOP. You under-extracted. Execute Tier 4 steps 4a-4d again.
│         You likely skipped the grep commands or didn't read module files.
│         Go back NOW. Do not report until you hit 30+.
└── YES → Continue to Check 2.
```

**Check 2: Category coverage.** For EACH category below, verify you stored at least 1 memory. If missing, execute the fix immediately — do not just note it.

```
□ Module map (what each package/dir does)     → missing? Run Tier 4a-4b again
□ Domain model (entities, relationships)       → missing? Grep BaseModel/dataclass, read models/
□ API surface (routes, endpoints, handlers)    → missing? Grep router/app.route, read route files
□ Data layer (tables, schema, migrations)      → missing? Grep CREATE TABLE, read migrations
□ Error handling (exception hierarchy)         → missing? Grep class.*Error
□ Conventions (style, linting, naming)         → missing? Re-read CLAUDE.md, linter configs
□ CI/CD pipeline                               → missing? Re-read workflow files
□ Domain glossary (business terms)             → missing? Grep Enum, read README domain section
```

**A 20-memory init on a large project means Tier 4 was skipped. This is the #1 failure mode. Fix it.**

## Boundaries

- **Read-only on the codebase.** Never modify project files.
- **No secrets.** Skip .env files with real values. Only read .env.example.
- **Scan source structure, not every line.** Read __init__.py, model files, route files, config — not every implementation file. Use Grep to discover patterns across files efficiently. Goal: understand the shape of each module without reading its internals.
- **Dedup is mandatory.** Never skip the search-before-store step.
- **Ask before overwriting.** If existing memories conflict with what the codebase says, present both and ask which is correct.
- **Aim for 40-80 memories** on a substantial codebase (10+ modules). 20 is too few — it means you skipped module-level architecture, domain model, API surface, and cross-cutting patterns. If you finish with <30 memories on a large project, go back and scan source code structure more aggressively.
- **Session-level learning via `/rekal-save`.** Init captures the structural 80%. Runtime discoveries happen via `/rekal-save`.

## Large codebases

For monorepos or projects with 10+ top-level directories:

1. Scan all top-level READMEs and __init__.py files first to build a map
2. Process ALL areas — don't stop after one module. Aim for 3-5 memories per major module.
3. Report progress after every 10 memories stored
4. Only ask user to pick focus areas if the codebase has 50+ top-level directories (true monorepo scale)

For a typical large project (5-20 modules), scan everything without asking. The user ran init to get comprehensive coverage, not partial.

## Re-running

Safe to re-run. Dedup ensures no duplicates. Changed knowledge gets superseded. New files get picked up. Report what changed vs last run:

> "Re-init for `backend`: 2 new memories, 3 superseded, 14 unchanged (skipped)."
