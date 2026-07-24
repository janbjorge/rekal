# rekal cost-savings benchmark

Prove rekal cuts COST by recalling cross-session knowledge, so a "warm"
agent skips exploration a "cold" agent must redo. The success criterion is
a net cost win at answer-quality parity; tokens are a recorded diagnostic.

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

## Arms — injection-only measurement

Measured warm arms mount ZERO MCP servers. Recall arrives exclusively via
the UserPromptSubmit hook, which injects query-matched memories directly
into the prompt at zero turn cost. The warm tool surface is byte-identical
to cold, so a rekal tool call is structurally impossible in a measured run
(any such call marks the row `contaminated` and aggregate excludes it).

| arm        | rekal                          | DB            | purpose                                |
|------------|--------------------------------|---------------|----------------------------------------|
| cold       | none                           | none          | true baseline: agent with NO rekal     |
| warm-empty | recall hook only, zero MCP     | missing file  | isolates FIXED overhead (hook payload) |
| warm-seed  | recall hook only, zero MCP     | frozen seed   | recall benefit                         |
| warm-tool  | readonly MCP + recall hook     | frozen seed   | OPT-IN comparison: price of tool recall vs injection |
| learn      | full MCP, store on             | empty->seed   | build the frozen seed DB once (not measured) |

Decomposition (dollars — the honest axis):

- **fixed overhead** = $warm-empty - $cold  (hook payload; near zero when
  nothing matches, since an empty readonly payload injects zero bytes)
- **content benefit** = $warm-empty - $warm-seed  (what memory buys)
- **net effect** = $cold - $warm-seed  (headline: does rekal save money?)

Notes:

- All arms share ONE authenticated config dir (`config/warm`) and the
  same hookless `settings.json` (`autoMemoryEnabled: false`), so the only
  difference between arms is rekal itself, not auth, plugins, or global
  CLAUDE.md (none load: the config dir is isolated, not `~/.claude`).
- cold adds nothing: `--strict-mcp-config` with no `--mcp-config` -> zero
  MCP servers, and no `--settings hooks.json` -> no recall injection.
- `--bare` is NOT used anywhere (including the judge). It disables
  subscription auth (headless `--bare` always returns "Not logged in"), so
  isolation via a dedicated config dir is what gives the clean baseline.
- Warm arms add `--settings config/warm/hooks.json` wiring
  UserPromptSubmit -> `rekal hook user-prompt-submit`, which reads
  `REKAL_DB_PATH`, `REKAL_PROJECT`, and `REKAL_READONLY=1` from the run
  env. `REKAL_PROJECT={repo}` is load-bearing: memories are stored with
  project={repo} and recall filters by exact project match — without the
  env var recall silently returns nothing (the bug that blinded every
  early warm-seed run). There is deliberately NO SessionStart recall
  hook: query-less recency recall is mostly the wrong subsystem in a
  multi-subsystem DB (measured cost, no benefit).
- Trust framing lives INSIDE the injected block header (rekal
  `render_recall`): verified-when-learned, cite file:line anchors, don't
  re-open files to re-check. Every arm therefore gets the bare question —
  cold and warm prompts are identical, and overhead measures pure hook
  payload rather than prompt differences.
- Seed DB built once by the learn pass, then FROZEN (chmod 0o444) and
  VERIFIED to still open frozen (rekal opens unwritable DBs with sqlite
  mode=ro and skips migration). `learn` fails loudly if the frozen file
  does not read back.
- warm-empty points `REKAL_DB_PATH` at a nonexistent file (recall treats
  a missing DB as no memories by design) so a stale empty.db can never
  shape the overhead arm.
- Auth: headless `claude -p` needs a logged-in config dir. Authenticate
  the isolated one ONCE: `CLAUDE_CONFIG_DIR=config/warm claude` then
  `/login`. Running elsewhere / unauthenticated returns "Not logged in"
  and the runner hard-exits rather than record a zero-token run.

## Metrics (per question, N=3, report median)

- **cost in USD — the headline.** Cache reads price at ~10% of input, so
  raw token totals overstate warm arms whose extra context is mostly
  cache re-reads.
- **weighted tokens** = input + output + cache_creation + 0.1*cache_read
  (the token-shaped view of the same truth; raw fields stay recorded).
- **explore calls** (Read/Grep/Glob/Bash) and **reads displaced** =
  cold - warm-seed explore medians: the direct signal that memory
  replaced exploration instead of stacking on top of it.
- turns to answer; tool-call breakdown.
- **contamination flag**: any `mcp__rekal__*` tool_use in a measured arm.
- **answer quality parity**: graded 0-2 by a source-verifying judge.
  A cheap wrong answer is NOT a win; `aggregate` refuses ungraded data by
  default and the HEADLINE downgrades cheap-but-worse to INCONCLUSIVE.

## Reproducibility

- `run` regenerates `config/warm` on every invocation (a stale hooks.json
  once silently shaped an entire measurement series) and stamps every
  result row with provenance: rekal git SHA (+dirty), bench.py sha256,
  config hash, seed DB sha256 + memory count, claude version, model.
- `--resume` refuses rows whose bench/config provenance differs from the
  current stack — cross-regime mixing is what invalidated run #1.
- `seed_status` distinguishes missing / ok / empty / UNOPENABLE seed DBs;
  an unopenable DB (e.g. old schema frozen read-only) is a loud, distinct
  error, never silently read as "empty".
- Injection preflight: before any paid run, `run` pipes the first seed
  question through `rekal hook user-prompt-submit` under the exact
  measured env and asserts a `## rekal memory` block comes back —
  `recall_text` swallows all exceptions by contract, so this preflight is
  the only guard against a silently dead hook.
- `probe REPO` ($0, offline) reports memories matched + payload bytes per
  benchmark question against the frozen seed: questions with zero matches
  are known-blind before a dollar is spent.

## Validation ladder

1. **Rung 0** (~$6-10): `setup` -> `learn fastapi` -> inspect DB (briefs
   anchored, project set) -> `probe fastapi` -> frozen-recall regression.
2. **Rung 1** smoke (~$3-5): `pipeline fastapi --pairs
   dependency-injection --roles seed --n 1`. Gate: zero rekal calls in
   measured arms, judge parses, warm answer cites injected anchors.
3. **Rung 2** pilot (~$40-55): 3 pairs x 3 runs, full pipeline
   (+ optional warm-tool slice). GO/NO-GO: cost delta sign consistent
   across pairs, quality parity.
4. **Rung 3** full (~$120-140/repo): `pipeline fastapi`, then the rest.

## Confounds controlled

- model nondeterminism -> N=3, median; model recorded per row, aggregate
  warns on mixed models
- memory quality dominates -> seed DB built by an anchored-brief learn
  prompt, frozen, verified, probed
- success parity enforced by judge; `aggregate` exits nonzero on LOSES or
  INCONCLUSIVE so pipelines can't miss it
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
    hooks.json                  #   rekal recall hook, warm arms only
  dbs/                          # seed-<repo>.db (frozen)
  runner/bench.py               # headless A/B runner + cost parser
  results/                      # per-run jsonl, judged jsonl
```
