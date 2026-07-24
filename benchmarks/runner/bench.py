#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["typer"]
# ///
"""rekal cost-savings benchmark runner.

Runs a fixed question set through headless Claude Code under three arms
(cold / warm-empty / warm-seed) and records cost, token usage, tool-call
counts, and turns per run. Measured warm arms are INJECTION-ONLY: zero MCP
servers, recall delivered exclusively by the UserPromptSubmit hook. See
../DESIGN.md for the experiment.

Must run where `claude` is authenticated (headless `claude -p` returns
"Not logged in" otherwise, and all token counts come back 0).

Subcommands:
    setup                 clone pinned repos + build warm-arm config dirs
    learn REPO            run seed questions store-on, freeze + verify seed DB
    probe REPO            offline: memories recalled per question ($0)
    run REPO              run all questions across arms, N times each
    judge REPO            grade each answer 0-2 vs source (quality parity)
    aggregate REPO        cost-first medians + overhead/benefit decomposition
    pipeline REPO         run -> judge -> aggregate in one enforced order

Everything is written under benchmarks/ (dbs/, config/, results/), all
gitignored.
"""

import hashlib
import json
import re
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from itertools import product
from os import environ
from pathlib import Path
from shutil import which

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

BENCH = Path(__file__).resolve().parent.parent
REPOS = BENCH / "repos"
QUESTIONS = BENCH / "questions"
DBS = BENCH / "dbs"
CONFIG = BENCH / "config"
RESULTS = BENCH / "results"
WARM_CFG = CONFIG / "warm"

# Pinned targets: exact commit matters for reproducibility, so it is fetched
# by SHA (GitHub allows this) rather than trusting whatever HEAD happens to be.
REPO_SPECS = {
    "tinygrad": (
        "git@github.com:tinygrad/tinygrad.git",
        "9267fca91a0133b620c4068829fff7baf3fe00dd",
    ),
    "pytorch": ("git@github.com:pytorch/pytorch.git", "8e6ba63688df3eb22a39eb39302d98cc80672662"),
    "fastapi": ("git@github.com:fastapi/fastapi.git", "704fbe1439341994100622853f515a8af7ccc2eb"),
    "pydantic": (
        "git@github.com:pydantic/pydantic.git",
        "a2a6577d4c329dd574a45dbb01a8feaa16b1ad3d",
    ),
}


class Arm(StrEnum):
    COLD = "cold"
    WARM_EMPTY = "warm-empty"
    WARM_SEED = "warm-seed"
    # Comparison arm, off by default: today's tool-recall shape (readonly MCP
    # server mounted alongside the hook). One pilot slice quantifies what a
    # model-initiated recall turn costs vs pure injection.
    WARM_TOOL = "warm-tool"


# The arms a normal `run` measures. WARM_TOOL is opt-in via --arms.
DEFAULT_ARMS = (Arm.COLD, Arm.WARM_EMPTY, Arm.WARM_SEED)


class Role(StrEnum):
    SEED = "seed"
    HELDOUT = "heldout"


Key = tuple[str, str, str]  # (pair, role, arm)

# Read-only tool allowlist so exploration never hits a permission prompt.
COLD_TOOLS = "Read,Grep,Glob,Bash(rg:*),Bash(fd:*),Bash(cat:*),Bash(head:*),Bash(wc:*)"
# Measured warm arms mount NO MCP server at all: recall arrives by hook
# injection, so their tool surface is byte-identical to cold and a rekal tool
# call is structurally impossible. Only `learn` (writes) and the opt-in
# warm-tool comparison arm mount the server.
RECALL_TOOLS = "mcp__rekal__memory_build_context"
LEARN_TOOLS = (
    COLD_TOOLS + "," + RECALL_TOOLS + ",mcp__rekal__memory_store,mcp__rekal__memory_delete"
)
# Flags every headless invocation shares, regardless of arm.
HEADLESS = ["--no-session-persistence", "--permission-mode", "dontAsk"]

# No warm-side prompt guidance: every arm gets the bare question. The trust
# framing ("cite anchors, don't re-verify") ships INSIDE the injected memory
# block's header (rekal render_recall), so cold and warm prompts are identical
# and overhead = warm-empty - cold measures pure hook payload.


@cache
def rekal_bin() -> str:
    """Absolute path to the rekal CLI, baked into hook/MCP commands.

    Prefers this repo's .venv so the benchmark measures the checked-out code,
    not a globally installed release. Falls back to PATH. Resolved once
    (cached) because the warm settings.json and --mcp-config embed the
    literal command and an isolated CLAUDE_CONFIG_DIR won't inherit a
    shell alias.
    """
    local = BENCH.parent / ".venv" / "bin" / "rekal"
    if local.is_file():
        return str(local)
    if path := which("rekal"):
        return path
    sys.exit("rekal not on PATH; `uv sync` in the repo or `uv tool install rekal`")


def ensure_repo(repo: str) -> Path:
    """Return the checkout path, cloning the pinned commit if it's missing.

    Called before every run so a deleted (gitignored) repos/ heals itself
    instead of crashing mid-matrix. Shallow-fetches just the pinned SHA.
    """
    if repo not in REPO_SPECS:
        sys.exit(f"unknown repo {repo!r}; known: {', '.join(REPO_SPECS)}")
    dest = REPOS / repo
    if (dest / ".git").exists():
        return dest
    url, sha = REPO_SPECS[repo]
    dest.mkdir(parents=True, exist_ok=True)
    git = ["git", "-C", str(dest)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "remote", "add", "origin", url], check=False)
    print(f"cloning {repo} @ {sha[:8]} (shallow) ...")
    subprocess.run([*git, "fetch", "-q", "--depth", "1", "origin", sha], check=True)
    subprocess.run([*git, "checkout", "-q", sha], check=True)
    return dest


def seed_db(repo: str) -> Path:
    """Per-repo frozen seed DB — knowledge learned from one repo must not leak
    into another repo's warm arm, so each gets its own file."""
    return DBS / f"seed-{repo}.db"


def seed_status(db: Path) -> tuple[str, int]:
    """Four-state seed DB check: (state, memory_count).

    States: 'missing' (no file), 'ok' (opens, has memories), 'empty' (opens,
    zero memories), 'unopenable' (health crashed or emitted garbage). The last
    state is the one that invalidated a whole benchmark run when it was
    silently reported as 0/empty — an unopenable DB with memories inside must
    be a loud, distinct error, not a shrug.
    """
    if not db.exists():
        return "missing", 0
    proc = subprocess.run(
        [rekal_bin(), "--db", str(db), "health"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        count = int(json.loads(proc.stdout).get("total_memories", 0))
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        detail = tail[-1][:160] if tail else "(no output)"
        print(f"  seed DB unopenable: {db.name} -- health exit {proc.returncode}: {detail}")
        return "unopenable", -1
    return ("ok", count) if count else ("empty", 0)


def mem_count(db: Path) -> int:
    """Memory count for `learn` progress lines; unopenable/missing read as 0."""
    state, count = seed_status(db)
    return count if state == "ok" else 0


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


@cache
def provenance() -> dict[str, str]:
    """Identity of the measuring stack, stamped into every result row.

    The invalid first run mixed an old rekal, an old bench.py, and a stale
    config without any of it being visible in the data. These fields make a
    cross-regime mix detectable (and let --resume refuse it).
    """
    git = subprocess.run(
        ["git", "-C", str(BENCH.parent), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "-C", str(BENCH.parent), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    sha = git.stdout.strip()[:12] or "unknown"
    if dirty.stdout.strip():
        sha += "-dirty"
    claude_version = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return {
        "rekal_sha": sha,
        "bench_sha": file_sha(Path(__file__)),
        "claude_version": claude_version,
    }


def base_settings() -> dict:
    """settings.json for the shared config dir — the base every arm inherits.

    Deliberately HOOKLESS: rekal's recall hooks live in a separate file passed
    via --settings for warm arms only, so the cold arm (same config dir, for
    auth) is never touched by them. autoMemory off so Claude's built-in memory
    can't confound the measurement.
    """
    return {"autoMemoryEnabled": False}


def hook_settings() -> dict:
    """Recall-hook settings, layered on top of base via --settings for warm
    arms. Wires UserPromptSubmit ONLY to `rekal hook`, which reads
    REKAL_DB_PATH from the env the run sets — so one file works for every repo
    without baking a DB path in.

    SessionStart recall is deliberately absent: it injects the N most
    *recent* memories with no query, which in a multi-subsystem seed DB is
    mostly the wrong subsystem — measured cost, no measured benefit. The
    UserPromptSubmit hook injects query-matched memories, which is the one
    recall channel that costs zero extra turns."""
    rekal = rekal_bin()
    return {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": f"{rekal} hook user-prompt-submit"}]}
            ],
        },
    }


def mcp_config() -> str:
    """Inline --mcp-config JSON exposing rekal's tools; the server reads
    REKAL_DB_PATH from the inherited env. Paired with --strict-mcp-config so
    the run sees exactly this server, not the user's global MCP servers."""
    rekal = rekal_bin()
    return json.dumps({"mcpServers": {"rekal": {"command": rekal, "args": ["mcp"]}}})


def write_warm_config() -> str:
    """(Re)write the shared config dir; return a short hash of its contents.

    Called by `setup`, `learn`, AND `run` — the invalid first run happened
    because `run` trusted whatever stale hooks.json was on disk. The hash goes
    into every result row so a config change mid-series is visible in the data.
    """
    WARM_CFG.mkdir(parents=True, exist_ok=True)
    settings = json.dumps(base_settings(), indent=2)
    hooks_json = json.dumps(hook_settings(), indent=2)
    (WARM_CFG / "settings.json").write_text(settings)
    (WARM_CFG / "hooks.json").write_text(hooks_json)
    return hashlib.sha256((settings + hooks_json).encode()).hexdigest()[:12]


@app.command()
def setup() -> None:
    """Clone the pinned target repos and build the warm-arm config dir."""
    for repo in REPO_SPECS:
        ensure_repo(repo)
    for d in (DBS, CONFIG, RESULTS):
        d.mkdir(parents=True, exist_ok=True)
    write_warm_config()
    print(f"setup complete under {BENCH}")
    print(
        "\nNEXT: authenticate the isolated config dir (every arm runs under it):\n"
        f"  CLAUDE_CONFIG_DIR={WARM_CFG} claude   # then /login, then quit\n"
        "Verify:  CLAUDE_CONFIG_DIR=... claude -p 'say pong'"
    )


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
@dataclass
class RunResult:
    repo: str
    arm: str
    pair: str
    role: str  # seed | heldout
    run: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    num_turns: int = 0
    cost_usd: float = 0.0
    tool_calls: dict[str, int] = field(default_factory=dict)
    answer: str = ""
    is_error: bool = False
    model: str = ""
    contaminated: bool = False
    prov: dict[str, str] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read + self.cache_creation

    @property
    def weighted_tokens(self) -> float:
        # Cache reads are priced at ~10% of fresh input; counting them 1:1
        # made warm arms look 2x worse than their bill. Cost is the headline;
        # this is the token-shaped view of the same truth.
        return self.input_tokens + self.output_tokens + self.cache_creation + 0.1 * self.cache_read

    @property
    def reads(self) -> int:
        """Exploration tool calls — the count memory is supposed to displace."""
        return sum(
            c
            for n, c in self.tool_calls.items()
            if n in ("Read", "Grep", "Glob") or n.startswith("Bash")
        )

    @property
    def rekal_calls(self) -> int:
        return sum(c for n, c in self.tool_calls.items() if n.startswith("mcp__rekal__"))


def build_cmd(
    arm: str, repo: str, question: str, *, store: bool = False
) -> tuple[list[str], dict[str, str]]:
    """Assemble the headless claude argv + full env for one arm.

    Every arm shares one authenticated config dir (CLAUDE_CONFIG_DIR=WARM_CFG)
    and the same hookless base settings.json. cold adds nothing
    (--strict-mcp-config with no --mcp-config = zero MCP, no hooks). Measured
    warm arms are INJECTION-ONLY: same zero-MCP shape and the SAME tool
    allowlist as cold, plus the recall hook (--settings hooks.json) and the
    env it reads (REKAL_DB_PATH, REKAL_PROJECT, REKAL_READONLY=1). The only
    delta vs cold is the hook payload — a rekal tool call is structurally
    impossible, so overhead decomposes cleanly.

    REKAL_PROJECT must match what `learn` stored (memories carry
    project={repo}); without it recall searches project=None and the strict
    project filter drops every hit — the bug that silenced every previous
    warm-seed run.

    `learn` (store=True) mounts the full MCP server to write the seed DB.
    The opt-in warm-tool arm mounts the READONLY server alongside the hook to
    price model-initiated recall against injection.

    `--bare` is deliberately NOT used: it disables subscription auth, so a
    headless run under it always comes back "Not logged in". Isolation to a
    dedicated config dir (no global CLAUDE.md, no plugins) gives an equivalent
    clean baseline while staying authenticated. Returns (argv, env) with env
    already merged onto the parent environment.
    """
    argv = [
        "claude",
        "-p",
        question,
        "--output-format",
        "stream-json",
        "--verbose",
        *HEADLESS,
        "--strict-mcp-config",
    ]
    # ENABLE_TOOL_SEARCH=false loads MCP tool schemas eagerly where a server
    # is mounted (learn, warm-tool). Set for every arm so env is uniform.
    env = environ | {"CLAUDE_CONFIG_DIR": str(WARM_CFG), "ENABLE_TOOL_SEARCH": "false"}
    if arm == Arm.COLD:
        return [*argv, "--allowedTools", COLD_TOOLS], env
    db = DBS / "empty.db" if arm == Arm.WARM_EMPTY else seed_db(repo)
    warm_env = env | {"REKAL_DB_PATH": str(db), "REKAL_PROJECT": repo}
    argv += ["--settings", str(WARM_CFG / "hooks.json")]
    if store:
        # learn: full server, write tools allowed.
        argv += ["--mcp-config", mcp_config(), "--allowedTools", LEARN_TOOLS]
        return argv, warm_env
    warm_env |= {"REKAL_READONLY": "1"}
    if arm == Arm.WARM_TOOL:
        # Comparison arm: readonly server mounted, recall tool pre-approved.
        argv += ["--mcp-config", mcp_config(), "--allowedTools", COLD_TOOLS + "," + RECALL_TOOLS]
        return argv, warm_env
    # Measured injection-only arms: zero MCP, cold's exact tool surface.
    argv += ["--allowedTools", COLD_TOOLS]
    return argv, warm_env


def _tool_detail(block: dict) -> str:
    """Short human hint of what a tool_use touched (path/pattern), for live logs."""
    inp = block.get("input", {})
    return next(
        (str(inp[k]) for k in ("file_path", "pattern", "path", "command", "query") if inp.get(k)),
        "",
    )


_K = 1_000
_M = 1_000_000


def _tokens(n: float) -> str:
    """Compact token count: 190531 -> '191k', 1_250_000 -> '1.25M'."""
    if n >= _M:
        return f"{n / _M:.2f}M"
    if n >= _K:
        return f"{n / _K:.0f}k"
    return f"{n:.0f}"


def _tool_bucket(name: str) -> str:
    """Collapse a tool name to a display bucket; rekal calls become 'memory'."""
    if name.startswith("mcp__rekal__"):
        return "memory"
    if name.startswith("Bash"):
        return "bash"
    return name.lower()


def tool_summary(calls: dict[str, int]) -> str:
    """One-line breakdown of a run's tool use, memory calls first so rekal
    activity is easy to spot: '18 tools | 3 memory, 8 read, 5 grep, 2 glob'."""
    buckets: dict[str, int] = {}
    for name, c in calls.items():
        buckets[_tool_bucket(name)] = buckets.get(_tool_bucket(name), 0) + c
    total = sum(buckets.values())
    order = sorted(buckets.items(), key=lambda kv: (kv[0] != "memory", -kv[1]))
    parts = ", ".join(f"{v} {k}" for k, v in order)
    return f"{total} tools | {parts}" if parts else "0 tools"


def _pulse(name: str, detail: str, root: Path) -> str:
    """Format one live tool-call line: repo-relative path, memory calls flagged
    with '*' so rekal recall/store stands out from plain '-' exploration."""
    detail = detail.replace(f"{root}/", "").replace("\n", " ")
    if name.startswith("mcp__rekal__"):
        mark, label = "*", name.removeprefix("mcp__rekal__")
    else:
        mark, label = "-", _tool_bucket(name)
    return f"    {mark} {label} {detail}".rstrip()[:88]


# Attempts per headless run. One transient API error (rate limit, overloaded)
# 30 runs into a 180-run matrix must not abort the whole thing.
RETRIES = 3


def run_claude(
    argv: list[str], cwd: Path, env: dict[str, str], rr: RunResult, *, live: bool = True
) -> None:
    """Run headless claude, retrying transient failures, folding events into `rr`.

    A successful headless run always emits a terminal `result` event; its
    absence (or a zero-token error) means the run failed — auth, crash, usage
    limit. Such runs are retried with backoff; only RETRIES consecutive
    failures abort the matrix, so one flaky API call can't kill a long run.
    """
    fail = ""
    for attempt in range(1, RETRIES + 1):
        if (fail := run_claude_once(argv, cwd, env, rr, live=live)) is None:
            return
        if attempt < RETRIES:
            wait = 30 * attempt
            print(f"      ! claude run failed ({fail}); retrying in {wait}s", flush=True)
            time.sleep(wait)
    sys.exit(f"claude run failed {RETRIES}x -- {fail}")


def run_claude_once(
    argv: list[str], cwd: Path, env: dict[str, str], rr: RunResult, *, live: bool = True
) -> str | None:
    """One streaming claude pass: fold events into `rr`, echo each tool call
    live so long runs show a pulse instead of silence.

    Returns None on success, else a short failure description. claude prints
    errors like "usage limit reached" as PLAIN TEXT on stdout (not stderr, not
    a result event), so non-JSON stdout lines are kept as the primary failure
    detail rather than silently skipped.
    """
    # Reset accumulated state so a retry doesn't double-count the failed attempt.
    rr.input_tokens = rr.output_tokens = rr.cache_read = rr.cache_creation = 0
    rr.num_turns, rr.cost_usd, rr.answer, rr.is_error = 0, 0.0, "", False
    rr.tool_calls = {}
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    # Drain stderr concurrently: streaming stdout line-by-line while stderr fills
    # its pipe buffer would otherwise deadlock the child on a chatty run.
    stderr = proc.stderr
    err_chunks: list[str] = []
    drain = threading.Thread(target=lambda: err_chunks.append(stderr.read()))
    drain.start()
    saw_result = False
    junk: list[str] = []
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                junk.append(line.strip())
            continue
        if not isinstance(ev, dict):
            continue
        match ev.get("type"):
            case "system":
                if ev.get("subtype") == "init":
                    rr.model = ev.get("model", "")
            case "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "tool_use":
                        name = b.get("name", "?")
                        rr.tool_calls[name] = rr.tool_calls.get(name, 0) + 1
                        if live:
                            print(_pulse(name, _tool_detail(b), cwd), flush=True)
            case "result":
                u = ev.get("usage", {})
                rr.input_tokens = u.get("input_tokens", 0)
                rr.output_tokens = u.get("output_tokens", 0)
                rr.cache_read = u.get("cache_read_input_tokens", 0)
                rr.cache_creation = u.get("cache_creation_input_tokens", 0)
                rr.num_turns = ev.get("num_turns", 0)
                rr.cost_usd = ev.get("total_cost_usd", 0.0)
                rr.answer = ev.get("result", "")
                rr.is_error = bool(ev.get("is_error"))
                saw_result = True
    code = proc.wait()
    drain.join()
    if not saw_result or (rr.is_error and rr.total_tokens == 0):
        err = "".join(err_chunks).strip()
        detail = junk[-1] if junk else (err.splitlines()[-1] if err else "(no output)")
        return f"exit {code}: {detail[:200]}"
    return None


def one_run(repo: str, arm: str, q: dict, role: str, run: int, pos: str) -> RunResult:
    """Execute one headless question, stream its tool pulse, print a result line."""
    rr = RunResult(repo=repo, arm=arm, pair=q["pair"], role=role, run=run, prov=provenance())
    # Every arm gets the bare question — the trust framing lives inside the
    # injected memory block, not the prompt, so arms differ only in the hook.
    argv, env = build_cmd(arm, repo, q[role])
    print(f"\n{pos} {q['pair']}/{role} | {arm} #{run}", flush=True)
    run_claude(argv, REPOS / repo, env, rr)
    # In an injection-only arm any rekal tool_use means the run was not wired
    # the way this file believes — the exact silent failure that invalidated
    # run #1. Flag it; aggregate excludes and reports flagged rows.
    if arm != Arm.WARM_TOOL and rr.rekal_calls > 0:
        rr.contaminated = True
    line = (
        f"      = ${rr.cost_usd:.2f} | wtok {_tokens(rr.weighted_tokens)} "
        f"(raw {_tokens(rr.total_tokens)}) | {rr.num_turns} turns | "
        f"{rr.reads} explore | {tool_summary(rr.tool_calls)}"
    )
    print(line)
    if rr.contaminated:
        rekal_used = {n: c for n, c in rr.tool_calls.items() if n.startswith("mcp__rekal__")}
        print(f"      ! CONTAMINATED: rekal tool calls in measured arm: {rekal_used}")
    return rr


def rollup(pair: str, role: str, by_arm: dict[str, list[RunResult]]) -> None:
    """After a (pair, role) group's arms all run, print the running verdict.

    Cost leads (the honest axis — cache reads are ~10% of input price);
    weighted tokens, turns, and explore-call medians follow so displacement
    (memory replacing reads) is visible live, not just in aggregate."""

    def med(arm: str, value: str) -> float | None:
        rrs = by_arm.get(arm)
        if not rrs:
            return None
        return statistics.median([float(getattr(r, value)) for r in rrs])

    cold_cost = med(Arm.COLD, "cost_usd")
    cold_reads = med(Arm.COLD, "reads")
    print(f"  +- {pair}/{role} -- medians over runs --")
    for arm in Arm:  # fixed cold -> warm-empty -> warm-seed (-> warm-tool) order
        cost = med(arm, "cost_usd")
        if cost is None:
            continue
        wtok = med(arm, "weighted_tokens") or 0.0
        turns = med(arm, "num_turns") or 0.0
        reads = med(arm, "reads") or 0.0
        delta = (
            f"  {(cost - cold_cost) / cold_cost * 100:+.0f}% cost vs cold"
            if cold_cost and arm != Arm.COLD
            else ""
        )
        print(
            f"  |  {arm:<11} ${cost:.2f}  wtok {_tokens(wtok):>6}  "
            f"{turns:>3.0f} turns  {reads:>3.0f} explore{delta}"
        )
    ws_cost = med(Arm.WARM_SEED, "cost_usd")
    ws_reads = med(Arm.WARM_SEED, "reads")
    if cold_cost and ws_cost is not None:
        cost_pct = (cold_cost - ws_cost) / cold_cost * 100
        displaced = (cold_reads or 0.0) - (ws_reads or 0.0)
        verdict = "rekal SAVED" if cost_pct > 0 else "rekal COST MORE"
        print(
            f"  +- net ${cold_cost - ws_cost:+.2f} ({cost_pct:+.0f}% cost) | "
            f"reads displaced {displaced:.0f} -> {verdict}"
        )
    else:
        print("  +-")


def _split_valid(raw: str, enum: type[StrEnum], label: str) -> list[str]:
    """Split a comma list and reject unknown values — a typo would otherwise
    run as a bogus arm and get silently dropped by `aggregate`."""
    valid = set(enum)
    items = [x.strip() for x in raw.split(",") if x.strip()]
    if bad := [x for x in items if x not in valid]:
        sys.exit(f"unknown {label}: {', '.join(bad)}; known: {', '.join(enum)}")
    return items


def check_seed(repo: str) -> None:
    """Fail loudly (and distinctly) on an unusable seed DB before spending money."""
    seed = seed_db(repo)
    state, _ = seed_status(seed)
    if state == "ok":
        return
    if state == "unopenable":
        sys.exit(
            f"ERROR: {seed.name} exists but the current rekal build cannot open it\n"
            f"       (old schema and/or frozen file needing migration).\n"
            f"       Re-run `learn {repo}` to rebuild it under the current stack."
        )
    sys.exit(f"warm-seed requested but {seed.name} is {state} -- run `learn {repo}` first")


def preflight_injection(repo: str, question: str) -> None:
    """Prove the recall hook injects memories under the exact measured-run env.

    recall_text swallows every exception by contract (a hook must never block
    a turn), so a broken hook degrades silently to directive-only injection —
    this preflight is the only guard between that and 180 wasted runs.
    """
    _argv, env = build_cmd(Arm.WARM_SEED, repo, question)
    proc = subprocess.run(
        [rekal_bin(), "hook", "user-prompt-submit"],
        input=json.dumps({"prompt": question}),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if "## rekal memory" not in proc.stdout:
        sys.exit(
            "ERROR: injection preflight failed -- the recall hook returned no memory\n"
            f"       block for the first seed question. stdout: {proc.stdout[:200]!r}\n"
            f"       stderr: {proc.stderr.strip()[:200]!r}\n"
            f"       Check seed DB, REKAL_PROJECT, and `probe {repo}`."
        )
    print("injection preflight OK -- hook returns a '## rekal memory' block")


def load_questions(repo: str, pairs: str | None) -> list[dict]:
    qs = json.loads((QUESTIONS / f"{repo}.json").read_text())
    if not pairs:
        return qs
    wanted = {p.strip() for p in pairs.split(",") if p.strip()}
    known = {q["pair"] for q in qs}
    if bad := wanted - known:
        sys.exit(f"unknown pairs: {', '.join(sorted(bad))}; known: {', '.join(sorted(known))}")
    return [q for q in qs if q["pair"] in wanted]


@app.command()
def run(
    repo: str,
    n: int = typer.Option(3, help="runs per (question, arm)"),
    arms: str = typer.Option(",".join(DEFAULT_ARMS), help="comma list of arms"),
    roles: str = typer.Option(",".join(Role), help="comma list: seed,heldout"),
    pairs: str | None = typer.Option(None, help="comma list of pair names (default: all)"),
    resume: bool = typer.Option(
        False, help="skip (pair, role, arm, run) rows already in results/REPO.jsonl"
    ),
) -> None:
    """Run all questions across arms, N times each; append to results/REPO.jsonl."""
    ensure_repo(repo)
    # Regenerate the config EVERY run: a stale hooks.json on disk silently
    # shaped the entire first (invalidated) measurement series.
    config_hash = write_warm_config()
    prov = provenance() | {"config_hash": config_hash}
    arm_list = _split_valid(arms, Arm, "arm")
    role_list = _split_valid(roles, Role, "role")
    qs = load_questions(repo, pairs)
    needs_seed = Arm.WARM_SEED in arm_list or Arm.WARM_TOOL in arm_list
    if needs_seed:
        check_seed(repo)
        prov |= {"seed_sha": file_sha(seed_db(repo)), "seed_mems": str(mem_count(seed_db(repo)))}
        preflight_injection(repo, qs[0]["seed"])
    if Arm.WARM_EMPTY in arm_list:
        # A leftover empty.db can go stale exactly like the seed did; recall
        # treats a missing DB as [] by design, so point at nothing instead.
        (DBS / "empty.db").unlink(missing_ok=True)
    total = len(qs) * len(role_list) * len(arm_list) * n
    print(
        f"\n{repo}: {len(qs)} pairs x {len(role_list)} roles x "
        f"{len(arm_list)} arms x {n} runs = {total} runs"
    )
    out = RESULTS / f"{repo}.jsonl"
    # --resume trusts rows already flushed to the results file: failures are
    # never written (run_claude exits first), so every recorded row is a
    # completed run. Rows from a different stack (rekal/bench/config hash)
    # are refused — mixing regimes is what invalidated run #1.
    prior: dict[tuple[str, str, str, int], RunResult] = {}
    if resume and out.exists():
        stale = 0
        for line in out.read_text().splitlines():
            r = json.loads(line)
            row_prov = r.get("prov", {})
            same_regime = all(row_prov.get(k) == prov[k] for k in ("bench_sha", "config_hash"))
            if not same_regime:
                stale += 1
                continue
            prior[(r["pair"], r["role"], r["arm"], r["run"])] = RunResult(**r)
        print(f"resume: {len(prior)} recorded runs will be skipped ({stale} cross-regime ignored)")
    done = 0
    with out.open("a") as f:
        # Group by (pair, role) so each group's arms can be compared the moment
        # it finishes — the live verdict, not just a wall of per-run lines.
        for q in qs:
            for role in role_list:
                by_arm: dict[str, list[RunResult]] = {}
                for arm, i in product(arm_list, range(n)):
                    done += 1
                    if rr := prior.get((q["pair"], role, arm, i)):
                        by_arm.setdefault(arm, []).append(rr)
                        continue
                    rr = one_run(repo, arm, q, role, i, f"[{done}/{total}]")
                    rr.prov = dict(prov)
                    f.write(json.dumps(rr.__dict__) + "\n")
                    f.flush()
                    by_arm.setdefault(arm, []).append(rr)
                rollup(q["pair"], role, by_arm)
    print(f"\nwrote {done} runs to {out} -- run `judge {repo}` then `aggregate {repo}`")


# --------------------------------------------------------------------------- #
# learn — build + freeze the seed DB
# --------------------------------------------------------------------------- #
def learn_prompt(repo: str, q: dict) -> str:
    """The seed question plus the knowledge-base contract.

    Loose facts failed: recall surfaced fragments, the model saw an incomplete
    picture and re-read everything. The unit that displaces reads is one
    anchored subsystem BRIEF (complete picture = permission to stop reading)
    plus a few gotcha atoms whose leading symbols make hybrid search rank the
    right subsystem for held-out questions.
    """
    pair = q["pair"]
    return (
        q["seed"] + "\n\n---\n"
        "You are also building a knowledge base so a FUTURE session can answer "
        "any question about this subsystem with near-zero file reads. After you "
        f'have explored and answered, store memories with memory_store (project="{repo}").\n\n'
        "STORE EXACTLY ONE SUBSYSTEM BRIEF (one memory_store call), 350-500 words:\n"
        f'- First line: "{pair} brief — <one-line summary>. keywords: <8-12 search '
        'terms: key symbols, filenames, feature words a question might use>".\n'
        "- Then the mechanism narrative: the full lifecycle of this subsystem end "
        "to end (registration/build time AND request time, happy path AND "
        "error/teardown paths, nesting, caching, config knobs) — not just what "
        "this question asked.\n"
        "- EVERY claim carries an inline anchor: (relative/path.py:LINE symbol). "
        "Use line numbers you actually verified in THIS session; never cite from "
        "general knowledge of the library.\n"
        '- End with "Deviations:" — where THIS checkout differs from the public/'
        "upstream version (renamed or non-public machinery, extra parameters, "
        "changed semantics). These are the facts a future agent will get wrong "
        'if it trusts its training data. If none found, write "Deviations: none '
        'found" — but look hard first.\n'
        f'- Tags: ["{pair}", "brief"].\n\n'
        "THEN STORE 2-4 GOTCHAS (one memory_store call each), one sentence apiece:\n"
        "- Each is a single surprising, non-obvious fact a future agent would "
        "otherwise re-derive or get wrong, with its inline anchor.\n"
        "- Put the searchable terms FIRST (symbol names, feature keywords), then "
        "the fact.\n"
        f'- Tags: ["{pair}", "gotcha", "<key-symbol>"].\n\n'
        "Rules: state facts so a fresh agent can act WITHOUT re-verifying — exact "
        "names, exact call order, exact anchors. Do not store your answer as "
        "prose Q&A. Do not store facts obvious from public documentation unless "
        "this checkout deviates from them."
    )


@app.command()
def learn(repo: str) -> None:
    """Build this repo's seed DB: answer each seed question with store on, then
    freeze the DB read-only and VERIFY it still opens frozen.

    The one pass that WRITES memory. Starts from an empty per-repo DB, and
    after every question reports how many memories the agent actually stored —
    a zero delta means the agent answered without storing, which would leave
    the warm arm with nothing to recall.
    """
    ensure_repo(repo)
    seed = seed_db(repo)
    # start fresh: unfreeze + remove any prior seed so re-learning is clean and
    # a leftover read-only file can't silently block writes.
    if seed.exists():
        seed.chmod(0o644)
        seed.unlink()
    write_warm_config()
    qs = json.loads((QUESTIONS / f"{repo}.json").read_text())
    for q in qs:
        # warm-seed shape targets this repo's seed DB; store=True exposes the
        # write tool (measured runs don't get it) and the prompt turns it on.
        argv, env = build_cmd(Arm.WARM_SEED, repo, learn_prompt(repo, q), store=True)
        before = mem_count(seed)
        print(f"\nlearning {q['pair']} ...", flush=True)
        rr = RunResult(repo=repo, arm="learn", pair=q["pair"], role=Role.SEED, run=0)
        run_claude(argv, REPOS / repo, env, rr)
        stored = mem_count(seed) - before
        print(f"learned: {q['pair']:<22} (+{stored} memories)")
        if stored == 0:
            print("  WARNING: nothing stored -- agent answered without memory_store")
    seed.chmod(0o444)  # freeze so measured runs can't mutate it
    # Verify readback under measured conditions: the frozen file itself must
    # open. A frozen-but-unopenable seed silently zeroed out a whole run once.
    state, total = seed_status(seed)
    if state != "ok":
        sys.exit(
            f"ERROR: frozen seed DB failed readback ({state}) — measured warm-seed "
            f"runs would see no memory. Fix rekal readonly open or re-learn."
        )
    print(f"seed DB frozen + verified: {seed} ({total} memories)")


# --------------------------------------------------------------------------- #
# judge — score each answer 0-2 against the real source
# --------------------------------------------------------------------------- #
GRADER = (
    "You are grading an answer about the {repo} codebase (checked out in the "
    "current directory; read it to verify). Question:\n{q}\n\nAnswer under "
    "review:\n{a}\n\nScore the answer's correctness and completeness 0-2: "
    "0 = wrong/empty, 1 = partially correct or missing key detail, 2 = correct "
    "and complete. Verify claims against the actual source. Output ONLY JSON: "
    '{{"score": <0|1|2>, "reason": "<one sentence>"}}'
)


def parse_verdict(stdout: str) -> tuple[int, str]:
    """Pull (score, reason) out of the grader's `claude --output-format json`
    stdout, tolerating a result string that wraps the JSON in prose or ```json
    fences — so a well-graded answer isn't scored 0 just for formatting."""
    try:
        result = json.loads(stdout).get("result", "")
    except (json.JSONDecodeError, AttributeError):
        return 0, "unparseable"
    m = re.search(r"\{.*\}", result, re.DOTALL)
    if not m:
        return 0, "unparseable"
    try:
        v = json.loads(m.group())
        return int(v.get("score", 0)), v.get("reason", "")
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return 0, "unparseable"


def judge_one(repo: str, prompt: str) -> tuple[int, str]:
    """One graded verdict, retrying unparseable output.

    `--bare` is NOT used — it disables subscription auth and headless runs
    under it always return "Not logged in", which parse_verdict would score 0.
    (That combination silently zeroed every earlier judging attempt.) The
    grader runs under the shared authenticated config dir with zero MCP —
    the exact cold shape, so it inherits neither arm's advantage. An
    unparseable verdict is a grader failure, never a real 0: retry, and give
    up loudly after RETRIES attempts.
    """
    argv = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        *HEADLESS,
        "--strict-mcp-config",
        "--allowedTools",
        "Read,Grep,Glob",
    ]
    env = environ | {"CLAUDE_CONFIG_DIR": str(WARM_CFG)}
    fail = "unparseable"
    for attempt in range(1, RETRIES + 1):
        proc = subprocess.run(
            argv,
            cwd=REPOS / repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        score, reason = parse_verdict(proc.stdout)
        if reason != "unparseable":
            return score, reason
        if attempt < RETRIES:
            wait = 30 * attempt
            print(f"      ! grader verdict unparseable; retrying in {wait}s", flush=True)
            time.sleep(wait)
    sys.exit(f"grader failed {RETRIES}x ({fail}) -- check auth: CLAUDE_CONFIG_DIR={WARM_CFG}")


@app.command()
def judge(
    repo: str,
    resume: bool = typer.Option(False, help="skip rows already in REPO.judged.jsonl"),
) -> None:
    """Grade every recorded answer 0-2 against the real source.

    Guards the headline claim: a warm run that spends less is only a win if it
    answered as well. Writes REPO.judged.jsonl.
    """
    ensure_repo(repo)
    rows = [
        json.loads(x) for x in (RESULTS / f"{repo}.jsonl").read_text().splitlines() if x.strip()
    ]
    qmap = {q["pair"]: q for q in json.loads((QUESTIONS / f"{repo}.json").read_text())}
    out = RESULTS / f"{repo}.judged.jsonl"
    done: dict[tuple[str, str, str, int], dict] = {}
    if resume and out.exists():
        for line in out.read_text().splitlines():
            j = json.loads(line)
            done[(j["pair"], j["role"], j["arm"], j["run"])] = j
        print(f"resume: {len(done)} judged rows will be skipped")
    with out.open("w") as f:
        for i, r in enumerate(rows, start=1):
            key = (r["pair"], r["role"], r["arm"], r["run"])
            if prior := done.get(key):
                f.write(json.dumps(prior) + "\n")
                continue
            question = qmap[r["pair"]][r["role"]]
            score, reason = judge_one(repo, GRADER.format(repo=repo, q=question, a=r["answer"]))
            r["score"], r["score_reason"] = score, reason
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(f"[{i}/{len(rows)}] {r['pair']}/{r['role']} {r['arm']}: score {score}")
    print(f"wrote {out}")


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #
def row_metrics(r: dict) -> tuple[float, float, int]:
    """(cost, weighted_tokens, reads) for a raw result row."""
    wtok = r["input_tokens"] + r["output_tokens"] + r["cache_creation"] + 0.1 * r["cache_read"]
    reads = sum(
        c
        for n, c in r.get("tool_calls", {}).items()
        if n in ("Read", "Grep", "Glob") or n.startswith("Bash")
    )
    return r.get("cost_usd", 0.0), wtok, reads


@dataclass
class Pools:
    """Per-(pair, role, arm) metric pools for aggregation."""

    costs: dict[Key, list[float]] = field(default_factory=dict)
    wtoks: dict[Key, list[float]] = field(default_factory=dict)
    reads: dict[Key, list[int]] = field(default_factory=dict)
    scores: dict[Key, list[int]] = field(default_factory=dict)

    def add(self, r: dict) -> None:
        key: Key = (r["pair"], r["role"], r["arm"])
        cost, wtok, reads = row_metrics(r)
        self.costs.setdefault(key, []).append(cost)
        self.wtoks.setdefault(key, []).append(wtok)
        self.reads.setdefault(key, []).append(reads)
        if "score" in r:
            self.scores.setdefault(key, []).append(r["score"])

    def med(self, store: dict[Key, list], pair: str, role: str, arm: str) -> float | None:
        xs = store.get((pair, role, arm))
        return float(statistics.median(xs)) if xs else None

    def pooled(self, store: dict[Key, list], arm: str, role: str) -> list:
        return [g for (_p, rl, a), xs in store.items() if a == arm and rl == role for g in xs]

    def pooled_med(self, store: dict[Key, list], arm: str, role: str) -> float:
        return float(statistics.median(self.pooled(store, arm, role) or [0.0]))


def load_aggregate_rows(repo: str, allow_unjudged: bool) -> list[dict]:
    """Read judged (preferred) rows, dropping contaminated ones loudly."""
    judged = RESULTS / f"{repo}.judged.jsonl"
    if not judged.exists() and not allow_unjudged:
        sys.exit(
            "ERROR: no judged file — quality unverified, so savings can't be "
            "trusted. Run `judge` first, or pass --allow-unjudged."
        )
    path = judged if judged.exists() else RESULTS / f"{repo}.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    contaminated = sum(1 for r in rows if r.get("contaminated"))
    if contaminated:
        print(
            f"WARNING: {contaminated} contaminated rows (rekal tool calls in a "
            "measured arm) EXCLUDED — the run env was not what bench.py intended."
        )
        rows = [r for r in rows if not r.get("contaminated")]
    models = {r.get("model", "") for r in rows if r.get("model")}
    if len(models) > 1:
        print(f"WARNING: mixed models across rows: {sorted(models)}")
    return rows


def print_pair_table(repo: str, pairs: list[str], p: Pools) -> None:
    print(f"\n=== {repo}: medians per (pair, role) — cost first ===")
    print(
        f"{'pair':<20}{'role':<9}{'$cold':>7}{'$wempty':>9}{'$wseed':>8}{'net$%':>8}"
        f"{'wtokC':>8}{'wtokWS':>8}{'explC>WS':>10}{'qualC/WS':>10}"
    )
    for pair in pairs:
        for role in Role:
            cc = p.med(p.costs, pair, role, Arm.COLD)
            wsc = p.med(p.costs, pair, role, Arm.WARM_SEED)
            if cc is None or wsc is None:
                continue
            wec = p.med(p.costs, pair, role, Arm.WARM_EMPTY)
            wtc = p.med(p.wtoks, pair, role, Arm.COLD) or 0.0
            wts = p.med(p.wtoks, pair, role, Arm.WARM_SEED) or 0.0
            rc = p.med(p.reads, pair, role, Arm.COLD) or 0.0
            rs = p.med(p.reads, pair, role, Arm.WARM_SEED) or 0.0
            qc = statistics.mean(p.scores.get((pair, role, Arm.COLD), []) or [0.0])
            qs_ = statistics.mean(p.scores.get((pair, role, Arm.WARM_SEED), []) or [0.0])
            wec_s = "-" if wec is None else f"{wec:.2f}"
            net = f"{100 * (cc - wsc) / cc:+.1f}%" if cc else "-"
            print(
                f"{pair:<20}{role:<9}{cc:>7.2f}{wec_s:>9}{wsc:>8.2f}{net:>8}"
                f"{_tokens(wtc):>8}{_tokens(wts):>8}{f'{rc:.0f}>{rs:.0f}':>10}"
                f"{f'{qc:.1f}/{qs_:.1f}':>10}"
            )


def role_verdict(role: str, p: Pools) -> str | None:
    """Print one role's pooled summary; return its HEADLINE verdict."""
    cc = p.pooled_med(p.costs, Arm.COLD, role)
    if not cc:
        return None
    wec = p.pooled_med(p.costs, Arm.WARM_EMPTY, role)
    wsc = p.pooled_med(p.costs, Arm.WARM_SEED, role)
    wtc = p.pooled_med(p.wtoks, Arm.COLD, role)
    wts = p.pooled_med(p.wtoks, Arm.WARM_SEED, role)
    rc = p.pooled_med(p.reads, Arm.COLD, role)
    rs = p.pooled_med(p.reads, Arm.WARM_SEED, role)
    print(
        f"\n[{role}] COST cold=${cc:.2f} warm-empty=${wec:.2f} warm-seed=${wsc:.2f}\n"
        f"       overhead=${wec - cc:+.2f}  benefit=${wec - wsc:+.2f}  "
        f"net=${cc - wsc:+.2f} ({100 * (cc - wsc) / cc:+.1f}%)\n"
        f"       wtok cold={_tokens(wtc)} warm-seed={_tokens(wts)}   "
        f"explore {rc:.0f} -> {rs:.0f} ({rc - rs:+.0f} displaced)"
    )
    qc_pool = p.pooled(p.scores, Arm.COLD, role)
    qw_pool = p.pooled(p.scores, Arm.WARM_SEED, role)
    if qc_pool and qw_pool:
        qc, qw = statistics.mean(qc_pool), statistics.mean(qw_pool)
        quality_ok = qw >= qc - 0.1
        flag = "" if quality_ok else "  <-- WARM WORSE, savings suspect"
        print(f"       quality: cold={qc:.2f} warm-seed={qw:.2f}{flag}")
    else:
        quality_ok = False
        print("       quality: UNGRADED")
    if cc > wsc and quality_ok:
        return f"{role}: WINS ({100 * (cc - wsc) / cc:+.1f}% cost)"
    if cc > wsc:
        return f"{role}: INCONCLUSIVE (cheaper but quality unverified/worse)"
    return f"{role}: LOSES ({100 * (cc - wsc) / cc:+.1f}% cost)"


@app.command()
def aggregate(
    repo: str,
    allow_unjudged: bool = typer.Option(
        False, help="report without quality grades (savings unverifiable)"
    ),
) -> None:
    """Cost-first medians per arm + overhead/benefit decomposition + HEADLINE.

    Refuses ungraded data by default: a warm 'saving' without a quality score
    can be cheaper AND worse. Exits nonzero on LOSES or quality-suspect wins
    so pipelines can't miss the verdict.
    """
    rows = load_aggregate_rows(repo, allow_unjudged)
    pools = Pools()
    for r in rows:
        pools.add(r)
    print_pair_table(repo, sorted({r["pair"] for r in rows}), pools)
    verdicts = [v for role in Role if (v := role_verdict(role, pools)) is not None]
    print(f"\nHEADLINE {repo}: " + " | ".join(verdicts))
    if any("LOSES" in v or "INCONCLUSIVE" in v for v in verdicts):
        raise typer.Exit(code=1)


@app.command()
def probe(repo: str) -> None:
    """Offline ($0): how many memories the frozen seed DB recalls per question.

    The deterministic 'was there memory to use' signal. A question with zero
    matches is known-blind BEFORE any paid run; if most questions probe blind,
    fix learn/retrieval, not the arms.
    """
    seed = seed_db(repo)
    state, count = seed_status(seed)
    if state != "ok":
        sys.exit(f"seed DB {state} -- run `learn {repo}` first")
    qs = json.loads((QUESTIONS / f"{repo}.json").read_text())
    print(f"\n{repo}: probing {seed.name} ({count} memories)\n")
    blind = 0
    for q in qs:
        for role in Role:
            proc = subprocess.run(
                [
                    rekal_bin(),
                    "--db",
                    str(seed),
                    "recall",
                    "--project",
                    repo,
                    "--query",
                    q[role],
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                memories = json.loads(proc.stdout)
            except json.JSONDecodeError:
                memories = []
            n = len(memories)
            payload = len(proc.stdout)
            blind += n == 0
            marker = "  <-- BLIND" if n == 0 else ""
            print(f"  {q['pair']:<22} {role:<8} {n:>2} memories  {payload:>6}B{marker}")
    total_qs = len(qs) * len(list(Role))
    print(f"\n{total_qs - blind}/{total_qs} questions have memory to use ({blind} blind)")


@app.command()
def pipeline(
    repo: str,
    n: int = typer.Option(3, help="runs per (question, arm)"),
    arms: str = typer.Option(",".join(DEFAULT_ARMS), help="comma list of arms"),
    roles: str = typer.Option(",".join(Role), help="comma list: seed,heldout"),
    pairs: str | None = typer.Option(None, help="comma list of pair names (default: all)"),
    resume: bool = typer.Option(False, help="resume run+judge from recorded rows"),
) -> None:
    """run -> judge -> aggregate, one enforced order. The verdict is the exit code."""
    run(repo, n=n, arms=arms, roles=roles, pairs=pairs, resume=resume)
    judge(repo, resume=resume)
    aggregate(repo)


if __name__ == "__main__":
    app()
