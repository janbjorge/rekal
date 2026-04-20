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

## Requirements

1. **Execute ALL steps including Tier 4** (source code scanning). Do NOT stop after reading docs and config.
2. **Run every Grep command** in Tier 4. Each grep with results → at least 1 memory.
3. **Store at least 1 memory per source module** discovered in the codebase.
4. **Read high-signal docs first** (CLAUDE.md, AGENTS.md) — they reveal what else to scan.
5. **Follow breadcrumbs** — if docs reference other files or dirs, read those too.
6. **Run the self-check** in Step 7 before finishing.
7. **On a fresh DB, skip dedup searches** — nothing to dedup against. Store directly.

Common failure: agent reads docs + config, stores ~20 memories, skips source code scanning entirely. **Do not do this.**

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

## Step 2: Scan for knowledge sources

Search for these files in priority order. Read every file that exists. Skip what's missing.

### Tier 1 — High-signal project docs (read fully, read FIRST)

```
CLAUDE.md, AGENTS.md, .claude/CLAUDE.md
README.md, README.rst, README.txt
CONTRIBUTING.md, ARCHITECTURE.md, DESIGN.md, ADR/*.md
docs/architecture.md, docs/design.md, docs/conventions.md
```

Read these BEFORE other tiers. They frequently reference:
- Specific directories or modules to pay attention to
- Conventions not captured in linter configs
- Domain-specific terminology and concepts
- Workflows, procedures, and tool preferences
- Other documentation files worth reading

**Follow the breadcrumbs.** If CLAUDE.md says "read AGENTS.md before doing anything" or references `docs/api-guide.md`, read those files too. If AGENTS.md describes a specific architecture, that's a memory.

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

Extract: stack + versions, key deps with purpose, CI pipeline steps, Docker setup, env vars needed.

### Tier 3 — Structural exploration

```
# Directory tree — top 3 levels via Glob
Glob pattern: "**/*/", max-depth 3

# Entry points — look for main/index/app files in src/ and project root
Glob: "main.*", "app.*", "index.*", "src/main.*", "src/index.*", "cmd/*/main.*"

# Config files that reveal conventions
.editorconfig, .prettierrc, .eslintrc*, ruff.toml, .ruff.toml
mypy.ini, pyrightconfig.json, tox.ini
```

Also explore anything Tier 1 docs pointed to — scripts/ dirs, specific modules mentioned by name, etc.

### Tier 4 — Source code structure (MANDATORY)

**You MUST execute every sub-step below.**

Scan the codebase through an architectural lens. The goal is to map the system's layers — not just list files, but understand what role each piece of code plays.

**4a. Discover all modules/packages:**

Use the directory tree from Tier 3 and the stack from Tier 2 to identify top-level source units (packages, modules, crates, services — whatever the project calls them). Glob for `src/*/` or equivalent. List every module found — store at least 1 memory per module.

**4b. Map the architecture by layer:**

For EACH module from 4a, read its entry/index file and 1-2 key files. Classify what you find and store memories accordingly:

**Domain (entities, value objects, business rules):**
- Find type/model definitions — look in dirs named `models`, `entities`, `domain`, `types`, `schemas`
- Find enums and constants — these define domain vocabulary (statuses, categories, roles)
- Find error/exception types — these define domain boundaries and business rule violations

Store: what entities exist, their relationships, invariants, domain-specific terms.

**Ports (interfaces the domain exposes or depends on):**
- Find abstract interfaces, contracts, traits — look for dirs named `ports`, `interfaces`, `contracts`
- Find types named Repository, Service, Gateway, Port, Store — these are usually boundaries

Store: what abstractions exist, which are inbound (driving) vs outbound (driven).

**Adapters (how ports connect to the outside world):**

*Inbound (drive the application):*
- Find route/handler definitions — HTTP endpoints, GraphQL resolvers, gRPC services, CLI commands
- Find message consumers/subscribers
- Look in dirs named `routes`, `controllers`, `handlers`, `adapters`, `api`

*Outbound (driven by the application):*
- Find persistence implementations — SQL files, ORM models, repository classes
- Read latest 3-5 migration files and schema definitions
- Find event/message publishers
- Find external service client wrappers (HTTP clients, cloud SDK usage)

Store: API surface, persistence strategy, external integrations, event patterns.

**Infrastructure (wiring, config, middleware, startup):**
- Find DI/wiring configuration
- Find middleware/interceptor chains
- Look in dirs named `config`, `infrastructure`, `wire`, `di`

Store: how layers are wired together, middleware chain.

**4c. Read schema files in detail:**

Find and read files in model/schema/entity directories. Read migration files, proto files, GraphQL schemas, OpenAPI specs if they exist.

Store: entity relationships, key fields, constraints worth knowing.

Each discovery → at least 1 memory summarizing findings. Nothing found → skip.

### Tier 5 — Test structure and patterns

```
# Find test directories and config
Glob: "tests/", "test/", "spec/", "testdata/", "fixtures/"

# Read test setup — fixtures, helpers, factories, shared config
Look for setup/helper/conftest/factory files in test dirs

# Skim test names for domain concepts — just names, not implementations
```

Store: test framework, fixtures/factories, DB/service strategy, parallelization.

### Do NOT read

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

**Architecture & structure:**

| Category | Source | Example memory |
|----------|--------|----------------|
| **Architecture overview** | README, ARCHITECTURE, AGENTS.md | "Hex arch: domain in core/, ports as Protocol classes, adapters in adapters/. DI wires at startup." |
| **Module map** | __init__.py, directory structure | "services/ has 6 modules: auth, billing, notifications, search, inventory, reporting" |
| **Project structure** | Directory tree, entry points | "Entry point: rekal/cli.py. MCP server: rekal/adapters/mcp_adapter.py" |
| **Key decisions** | ADRs, DESIGN.md | "Chose SQLite over Postgres for zero-config single-file deployment" |

**Domain layer:**

| Category | Source | Example memory |
|----------|--------|----------------|
| **Domain model** | Models, schemas, enums | "Core entities: Order (stateful, FSM), Product (immutable), Warehouse (has zones/bins). All IDs are NewType UUIDs." |
| **Domain vocabulary** | Enums, constants, README | "Content code = barcode type. Pick = retrieve from bin. Putaway = store in bin. Wave = batch of picks." |
| **Domain errors** | Exception classes | "DomainError → {ValidationError, NotFoundError, ConflictError}. Raised in domain, caught by adapters." |

**Ports & adapters:**

| Category | Source | Example memory |
|----------|--------|----------------|
| **Inbound ports** | Protocols, ABCs, interfaces | "OrderService protocol: create_order, cancel_order, ship_order. Implemented by OrderServiceImpl." |
| **Inbound adapters** | Routes, controllers, CLI, consumers | "REST API: /api/v2/ prefix. 47 endpoints across 8 routers. Auth via Bearer JWT." |
| **Outbound ports** | Repository protocols, gateway ABCs | "OrderRepository protocol: get, save, list_by_status. WarehouseGateway: reserve_stock, release_stock." |
| **Outbound adapters** | ORM, DB, API clients, publishers | "Postgres 15. 34 tables. Key: orders→order_lines→products. Soft deletes. Redis Streams for events." |
| **External integrations** | HTTP clients, cloud SDKs | "Stripe adapter for billing. Azure Blob for file uploads. SendGrid for email." |

**Infrastructure & operations:**

| Category | Source | Example memory |
|----------|--------|----------------|
| **Wiring / DI** | Container, startup, middleware | "FastAPI Depends for DI. Middleware chain: auth → audit log → error handler → request ID." |
| **Conventions** | CLAUDE.md, linter configs | "No underscore prefixes. Public by default. No mutable globals." |
| **Dependencies & stack** | pyproject.toml, package.json | "Python 3.11+, key deps: mcp[cli], aiosqlite, sqlite-vec, fastembed, pydantic" |
| **Build & CI** | Makefile, CI configs | "CI: ruff check, ruff format --check, ty check, pytest 100% coverage required" |
| **Test patterns** | conftest.py, fixtures | "Tests use testcontainers for Postgres+Redis. Factory pattern. 8 parallel pytest workers." |
| **Deploy & infra** | Docker, CI/CD | "Deploy via git tag vX.Y.Z → CI builds and publishes to PyPI" |
| **Workflows** | CONTRIBUTING, Makefile | "PR workflow: branch from main, all CI checks pass, squash merge" |

### What NOT to extract

- Verbatim content from CLAUDE.md or AGENTS.md — those are loaded every session. DO synthesize cross-cutting knowledge that spans multiple files into single memories.
- Individual function signatures or line numbers — trivially re-discoverable
- Boilerplate from templates (default CI configs, standard Dockerfile patterns)
- Version numbers that change frequently (pin knowledge to concepts, not versions)
- License text, badges, marketing copy

## Step 4: Deduplicate and store

**On a fresh DB (no existing memories), skip the search step entirely** — there's nothing to dedup against. Store directly. This dramatically speeds up init.

On re-init (memories already exist), search before each store:

```python
memory_search(query="<candidate topic>", limit=5)
```

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

1. Project identity, stack, and architecture overview
2. Module map (what each module/package does)
3. Domain layer: entities, value objects, domain errors, vocabulary
4. Ports: inbound and outbound interfaces
5. Adapters: API surface, persistence, external integrations, event patterns
6. Infrastructure: DI/wiring, middleware, cross-cutting concerns
7. Conventions and style
8. Build, test, CI
9. Deploy and infra
10. Key decisions (ADRs)

This ordering helps if the user interrupts — domain knowledge (most valuable) lands first.

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
> Run `/rekal-save` at end of future sessions to maintain.

## Step 7: Self-check — MANDATORY

After Step 6, verify you actually executed all tiers.

**Did you execute Tier 4?** If you never ran the Grep commands from 4b or never read module entry files, go back and do it now.

**Category coverage.** For each layer below, check if the codebase has it. If it does and you stored nothing about it, go back and scan:

```
□ Module map (what each package/dir does)
□ Domain (entities, value objects, vocabulary, errors)
□ Ports (abstract interfaces and contracts)
□ Inbound adapters (routes, CLI, consumers)
□ Outbound adapters (persistence, API clients, event publishers)
□ Infrastructure (wiring, middleware)
□ Conventions (style, linting, naming)
□ CI/CD pipeline
```

If a category doesn't exist in the codebase, skip it — only store what's actually there.

## Boundaries

- **Read-only on the codebase.** Never modify project files.
- **No secrets.** Skip .env files with real values. Only read .env.example.
- **Scan source structure, not every line.** Read module entry files, model files, route files, config — not every implementation file. Use Grep to discover patterns across files efficiently.
- **Dedup is mandatory on re-init.** On fresh DB, skip dedup.
- **Ask before overwriting.** If existing memories conflict with what the codebase says, present both and ask which is correct.
- **Execute all tiers.** If you only stored memories from docs and config, you skipped Tier 4. Go back.
- **Session-level learning via `/rekal-save`.** Init captures the structural baseline. Runtime discoveries happen via `/rekal-save`.

## Large codebases

For monorepos or projects with 10+ top-level directories:

1. Scan all top-level READMEs and module entry files first to build a map
2. Process ALL areas — don't stop after one module
3. Report progress after every 10 memories stored
4. Only ask user to pick focus areas if 50+ top-level directories (true monorepo scale)

For a typical large project (5-20 modules), scan everything without asking. The user ran init to get comprehensive coverage, not partial.

## Re-running

Safe to re-run. Dedup ensures no duplicates. Changed knowledge gets superseded. New files get picked up. Report what changed vs last run:

> "Re-init for `backend`: 2 new memories, 3 superseded, 14 unchanged (skipped)."
