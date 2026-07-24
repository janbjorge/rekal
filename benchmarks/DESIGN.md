# rekal token-savings benchmark

Prove rekal cuts token usage by recalling cross-session knowledge, so a
"warm" agent skips exploration a "cold" agent must redo.

## Two theses

- **Density thesis** (tinygrad): non-obvious code -> recall beats
  re-derivation. Small file-count, high reasoning depth.
- **Size thesis** (pytorch): more files -> more search -> bigger delta.
  ~11x tinygrad on both file-count and LOC -> plots savings vs repo size.
- **Breadth** (fastapi, pydantic): mid-size, vastly popular libraries as
  additional data points between the two extremes.

## Repos (pinned, shallow clones under repos/)

| repo     | commit     | notes                          |
|----------|------------|--------------------------------|
| tinygrad | 9267fca9   | density thesis (small, dense)  |
| pytorch  | 8e6ba636   | size thesis (~11x tinygrad)    |
| fastapi  | 704fbe14   | mid-size breadth point         |
| pydantic | a2a6577d   | mid-size breadth point         |

## Questions

20 per repo = 10 subsystem-aligned **pairs**. Each pair targets one
subsystem (same files):

- **seed** Q: asked in the learn pass -> agent explores, answers, stores
  memory.
- **held-out** Q: never asked cold in learn pass. Same subsystem. Warm
  agent benefits ONLY via transfer, not verbatim recall.

This yields two measurements from one design:

- **same-Q recall** = re-ask seed Qs warm. Upper bound (real scenario:
  teammate re-asks later).
- **transfer** = ask held-out Qs warm. Conservative floor (proves
  learning generalizes, not memorizes).

## Arms

Three configs decompose net effect into overhead vs content benefit.

| arm        | rekal                        | DB            | purpose                                |
|------------|------------------------------|---------------|----------------------------------------|
| cold       | none                         | none          | true baseline: agent with NO rekal     |
| warm-empty | readonly MCP + recall hook   | empty         | isolates FIXED overhead (tool schema + hook payload) |
| warm-seed  | readonly MCP + recall hook   | frozen seed   | recall benefit                         |
| learn      | full MCP, store on           | empty->seed   | build the frozen seed DB once (not measured) |

Decomposition (tokens):

- **fixed overhead** = warm-empty - cold  (cost of adopting rekal at all)
- **content benefit** = warm-empty - warm-seed  (what memory buys)
- **net effect** = cold - warm-seed  (headline: does rekal save tokens?)

Notes:

- All arms share ONE authenticated config dir (`config/warm`) and the
  same hookless `settings.json` (`autoMemoryEnabled: false`), so the only
  difference between arms is rekal itself, not auth, plugins, or global
  CLAUDE.md (none load: the config dir is isolated, not `~/.claude`).
- cold adds nothing: `--strict-mcp-config` with no `--mcp-config` -> zero
  MCP servers, and no `--settings hooks.json` -> no recall injection.
- `--bare` is NOT used. It disables subscription auth (headless `--bare`
  always returns "Not logged in"), so isolation via a dedicated config
  dir is what gives the clean baseline instead.
- warm arms layer on rekal: `--mcp-config` (`rekal mcp`) + `--settings
  config/warm/hooks.json` wiring UserPromptSubmit -> `rekal hook
  user-prompt-submit` (reads `REKAL_DB_PATH` from the run's env). That is
  the single recall channel: the hook injects query-matched memories at
  zero turn cost. There is deliberately NO SessionStart recall hook: it
  injects the most recent memories with no query, which in a
  multi-subsystem seed DB is mostly the wrong subsystem (measured cost,
  no benefit).
- Store is OFF in measured runs, enforced server-side: `REKAL_READONLY=1`
  makes rekal register `memory_build_context` only and swap in store-free
  instructions. Allowlist gating alone measurably failed: `--allowedTools`
  only pre-approves and does not hide schemas, so the server's own "store
  as you work" instructions drove ~1 denied `memory_store` attempt per
  warm run, each burning a turn. Only `learn` runs the full server, to
  build the seed DB.
- Both warm arms' prompts get a recall nudge: trust the hook-injected
  memory block, call `memory_build_context` only if it is insufficient,
  read code only to fill gaps. Without it the warm agent recalls memory
  but re-reads the code anyway, so recall adds turns instead of replacing
  exploration. Recall-first is how rekal is meant to be used, not a thumb
  on the scale. Given to warm-empty AND warm-seed so
  overhead=warm-empty-cold absorbs the instruction and
  benefit=warm-empty-warm-seed still isolates memory content. cold has no
  memory, so its prompt stays the bare question.
- Seed DB built once by a curated learn pass, then FROZEN (chmod 0o444)
  per run so stray writes can't mutate it.
- Auth: headless `claude -p` needs a logged-in config dir. Authenticate
  the isolated one ONCE: `CLAUDE_CONFIG_DIR=config/warm claude` then
  `/login`. Running elsewhere / unauthenticated returns "Not logged in"
  and the runner hard-exits rather than record a zero-token run.

## Metrics (per question, N=3, report median + MAD)

- input / output / cache-read / cache-creation / total tokens
- cost in USD (the honest axis: cache reads are ~10% of input price, so
  raw token totals overstate the warm arms, whose extra turns are mostly
  cache re-reads)
- tool calls, esp. grep/read/glob count
- turns to answer
- wall-clock (secondary)
- injection overhead: token delta from rekal hook payload (counted, not
  hidden, since it is the cost side)
- **answer quality parity**: graded 0-2 by a rubric/judge. A cheap wrong
  answer is NOT a win. Warm runs that scored below cold are rejected.

## Confounds controlled

- model nondeterminism -> N=3, median+MAD (same gate style as pgqueuer
  benchmark)
- memory quality dominates -> seed DB curated, frozen, inspected
- success parity enforced by judge
- injection overhead counted explicitly; small tasks may go net-negative
  (rekal costs more). Report honestly: it bounds where rekal pays off

## Layout

```
benchmarks/                     # lives in the rekal repo
  README.md                     # workflow / how to run
  DESIGN.md                     # this file: experiment rationale
  .gitignore                    # ignores repos/ dbs/ results/ config/
  repos/<repo>/                 # pinned shallow clones (gitignored)
  questions/<repo>.json         # 10 seed+heldout pairs per repo
  config/warm/                  # single isolated CLAUDE_CONFIG_DIR (auth)
    settings.json               #   hookless base, all arms inherit
    hooks.json                  #   rekal recall hooks, warm arms only
  dbs/                          # empty.db, seed-<repo>.db (frozen)
  runner/bench.py               # headless A/B runner + token parser
  results/                      # per-run jsonl, judged jsonl
```
