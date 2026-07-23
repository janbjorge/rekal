# rekal token-savings benchmark

Measures whether [rekal](../), persistent cross-session memory, cuts the
tokens an agent burns answering technical questions about a large codebase,
by recalling what a prior session already learned instead of re-exploring.

See [DESIGN.md](DESIGN.md) for the experiment rationale (theses, arms,
question design, confounds). This file is how to run it.

## TL;DR

```bash
cd benchmarks

# 1. clone pinned repos + build the config dir
uv run runner/bench.py setup

# 2. authenticate the isolated config dir ONCE (see "Auth" below)
CLAUDE_CONFIG_DIR="$PWD/config/warm" claude   # then /login, then quit

# 3. build each repo's frozen seed DB (the one pass that WRITES memory)
uv run runner/bench.py learn tinygrad

# 4. run the 3-arm matrix (cold / warm-empty / warm-seed), N=3 each
uv run runner/bench.py run tinygrad

# 5. grade every answer 0-2 against the real source (quality parity)
uv run runner/bench.py judge tinygrad

# 6. print median tokens + overhead/benefit/net decomposition
uv run runner/bench.py aggregate tinygrad
```

Repeat steps 3-6 per repo: `tinygrad`, `pytorch`, `fastapi`, `pydantic`.

Bare `uv run runner/bench.py` prints help. The script is a standalone
[PEP 723](https://peps.python.org/pep-0723/) `uv` script: `uv` fetches its
deps (typer) on first run, so there's no venv to manage.

## The three arms

Each measured question runs under three configs so the net effect
decomposes into cost vs benefit (tokens):

| arm          | what it is                                   |
|--------------|----------------------------------------------|
| `cold`       | no rekal at all: the baseline                |
| `warm-empty` | rekal loaded, empty DB: pure fixed overhead  |
| `warm-seed`  | rekal loaded, frozen seed DB: the payoff     |

- **fixed overhead** = warm-empty − cold (cost of adopting rekal)
- **content benefit** = warm-empty − warm-seed (what memory buys)
- **net effect** = cold − warm-seed (headline: does it save tokens?)

All three share ONE authenticated config dir and the same hookless
`settings.json`, so the *only* difference between arms is rekal itself.
`cold` adds nothing (`--strict-mcp-config`, no MCP, no hooks); the warm
arms layer on the rekal MCP server + recall hooks. `--bare` is not used
because it disables auth. Details in DESIGN.md.

## Questions

`questions/<repo>.json` holds 10 subsystem-aligned **pairs** per repo:

- `seed`: asked during `learn` (agent explores, answers, stores memory).
- `heldout`: never asked during learn; same subsystem. Warm benefit here
  is *transfer* (learning generalized), not verbatim recall.

`run` executes both roles across all arms; `--roles seed` or
`--roles heldout` narrows it.

## Commands

| command             | does                                                        |
|---------------------|-------------------------------------------------------------|
| `setup`             | clone all pinned repos, make dirs, write config/warm        |
| `learn REPO`        | fresh seed DB → answer seed Qs store-on → freeze read-only  |
| `run REPO`          | run every question × arm × N, append `results/REPO.jsonl`   |
| `judge REPO`        | grade each answer 0-2 vs source → `results/REPO.judged.jsonl` |
| `aggregate REPO`    | median tables + overhead/benefit/net + quality parity       |

`run` options: `--n 3` (runs per question/arm), `--arms cold,warm-empty,warm-seed`,
`--roles seed,heldout`. Unknown arm/role values are rejected up front.

`setup`, `learn`, and `run` self-heal a deleted `repos/` by re-cloning the
pinned SHA, so the gitignored clones can be wiped and rebuilt any time.

## Auth (read this, it's the #1 failure)

Headless `claude -p` needs a logged-in config dir, and the benchmark runs
under an **isolated** one (`config/warm`) so global CLAUDE.md / plugins
can't confound the measurement. Your normal `~/.claude` login does NOT
carry over. Authenticate the isolated dir once:

```bash
CLAUDE_CONFIG_DIR="$PWD/config/warm" claude   # /login, then quit
# verify:
CLAUDE_CONFIG_DIR="$PWD/config/warm" claude -p "say pong"
```

Do **not** run under `--bare` to "get a clean baseline". `--bare`
disables subscription auth and every run comes back "Not logged in". If a
run isn't authenticated the runner hard-exits (`claude run failed … is it
logged in?`) rather than silently record a zero-token run that would
poison the medians.

## What `learn` reports

Per seed question it prints `learned: <pair> (+N memories)`. `N > 0` means
the agent actually called `memory_store`, so the warm arm now has something
to recall. `+0` with a WARNING means it answered without storing (the seed
DB stays empty and `warm-seed` would just equal `warm-empty`). The final
line reports the frozen total; `run` refuses `warm-seed` if the seed DB is
empty.

## Reading `aggregate`

- Per-pair table: median total tokens for cold / warm-empty / warm-seed
  and `net%` = `(cold − warm-seed) / cold`.
- Per-role summary: the overhead/benefit/net decomposition, plus a
  **quality** line (`cold=… warm-seed=…`). If warm scored materially worse
  it's flagged `WARM WORSE, savings suspect`, since a cheap wrong answer is
  not a win. Run `judge` first or `aggregate` warns that quality is
  unverified.

## Notes

- Everything generated (`repos/ dbs/ results/ config/ *.jsonl`) is
  gitignored; only the runner, questions, and docs are committed.
- Runs are long: each question is a full agent exploration (minutes).
  `run` streams a live tool pulse (`- read routing.py`, memory calls
  flagged `*`), a compact per-run line (`= 191k tok | 11 turns | N
  tools`), and (the useful part) a **per-pair verdict** the moment a
  pair's arms finish: median tokens per arm, warm deltas vs cold, and a
  `rekal SAVED` / `rekal COST MORE` net line. Results flush per run, so an
  interrupted matrix keeps what it finished.
