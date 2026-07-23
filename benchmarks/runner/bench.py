#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["typer"]
# ///
"""rekal token-savings benchmark runner.

Runs a fixed question set through headless Claude Code under three arms
(cold / warm-empty / warm-seed) and records token usage, tool-call
counts, and turns per run. See ../DESIGN.md for the experiment.

Must run where `claude` is authenticated (headless `claude -p` returns
"Not logged in" otherwise, and all token counts come back 0).

Subcommands:
    setup                 clone pinned repos + build warm-arm config dirs
    learn REPO            run seed questions store-on, freeze seed DB
    run REPO              run all questions across arms, N times each
    judge REPO            grade each answer 0-2 vs source (quality parity)
    aggregate REPO        median tables + overhead/benefit decomposition

Everything is written under benchmarks/ (dbs/, config/, results/), all
gitignored.
"""

import json
import re
import statistics
import subprocess
import sys
import threading
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


class Role(StrEnum):
    SEED = "seed"
    HELDOUT = "heldout"


Key = tuple[str, str, str]  # (pair, role, arm)

# Read-only tool allowlist so exploration never hits a permission prompt.
COLD_TOOLS = "Read,Grep,Glob,Bash(rg:*),Bash(fd:*),Bash(cat:*),Bash(head:*),Bash(wc:*)"
# Measured warm runs may only RECALL, never write. A memory_store call during a
# measured run is pure overhead absent from the cold arm — it burns a turn (and,
# against a frozen seed DB, fails), inflating warm token counts and confounding
# the comparison. The rekal MCP server's own system prompt nudges the agent to
# "store as you work", so store must be gated out at the allowlist, not merely
# left unrequested. Only the `learn` pass (which builds the seed DB) gets write.
RECALL_TOOLS = "mcp__rekal__memory_build_context,mcp__rekal__memory_search"
WARM_TOOLS = COLD_TOOLS + "," + RECALL_TOOLS
LEARN_TOOLS = WARM_TOOLS + ",mcp__rekal__memory_store"
# Flags every headless invocation shares, regardless of arm.
HEADLESS = ["--no-session-persistence", "--permission-mode", "dontAsk"]


@cache
def rekal_bin() -> str:
    """Absolute path to the rekal CLI, baked into hook/MCP commands.

    Resolved via PATH (once, cached) rather than assumed, because the warm
    settings.json and --mcp-config embed the literal command and an isolated
    CLAUDE_CONFIG_DIR won't inherit a shell alias.
    """
    if path := which("rekal"):
        return path
    sys.exit("rekal not on PATH; `uv tool install rekal` or activate the venv")


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


def mem_count(db: Path) -> int:
    """Number of memories in a DB (0 if it doesn't exist yet).

    Used by `learn` to confirm the agent actually wrote memories rather than
    just answering — the whole warm arm is worthless if the seed DB is empty.
    """
    if not db.exists():
        return 0
    proc = subprocess.run(
        [rekal_bin(), "--db", str(db), "health"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(json.loads(proc.stdout).get("total_memories", 0))
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return 0


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
    arms. Wires SessionStart/UserPromptSubmit to `rekal hook`, which reads
    REKAL_DB_PATH from the env the run sets — so one file works for every repo
    without baking a DB path in. This is what reproduces the rekal plugin's
    injection standalone."""
    rekal = rekal_bin()

    def recall(event: str) -> dict:
        return {"hooks": [{"type": "command", "command": f"{rekal} hook {event}"}]}

    return {
        "hooks": {
            "SessionStart": [recall("session-start")],
            "UserPromptSubmit": [recall("user-prompt-submit")],
        },
    }


def mcp_config() -> str:
    """Inline --mcp-config JSON exposing rekal's tools; the server reads
    REKAL_DB_PATH from the inherited env. Paired with --strict-mcp-config so
    the run sees exactly this server, not the user's global MCP servers."""
    rekal = rekal_bin()
    return json.dumps({"mcpServers": {"rekal": {"command": rekal, "args": ["mcp"]}}})


def write_warm_config() -> Path:
    """(Re)write the shared config dir and return it. Splits base (all arms)
    from hooks (warm only); the DB is chosen per-run via REKAL_DB_PATH."""
    WARM_CFG.mkdir(parents=True, exist_ok=True)
    (WARM_CFG / "settings.json").write_text(json.dumps(base_settings(), indent=2))
    (WARM_CFG / "hooks.json").write_text(json.dumps(hook_settings(), indent=2))
    return WARM_CFG


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

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read + self.cache_creation


def build_cmd(
    arm: str, repo: str, question: str, *, store: bool = False
) -> tuple[list[str], dict[str, str]]:
    """Assemble the headless claude argv + full env for one arm.

    Every arm shares one authenticated config dir (CLAUDE_CONFIG_DIR=WARM_CFG)
    and the same hookless base settings.json, so the ONLY difference is rekal.
    cold adds nothing (--strict-mcp-config with no --mcp-config = zero MCP, no
    hooks); the warm arms layer on the recall hooks (--settings hooks.json) and
    the rekal MCP server, differing from each other only in which DB
    REKAL_DB_PATH points at (empty vs this repo's frozen seed). Measured warm
    runs get a recall-only allowlist; `learn` passes store=True to also expose
    memory_store, since building the seed DB is the one pass that must write.

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
    env = environ | {"CLAUDE_CONFIG_DIR": str(WARM_CFG)}
    if arm == Arm.COLD:
        return [*argv, "--allowedTools", COLD_TOOLS], env
    db = DBS / "empty.db" if arm == Arm.WARM_EMPTY else seed_db(repo)
    argv += [
        "--mcp-config",
        mcp_config(),
        "--settings",
        str(WARM_CFG / "hooks.json"),
        "--allowedTools",
        LEARN_TOOLS if store else WARM_TOOLS,
    ]
    return argv, env | {"REKAL_DB_PATH": str(db)}


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
    activity is easy to spot: '18 tools · 3 memory, 8 read, 5 grep, 2 glob'."""
    buckets: dict[str, int] = {}
    for name, c in calls.items():
        buckets[_tool_bucket(name)] = buckets.get(_tool_bucket(name), 0) + c
    total = sum(buckets.values())
    order = sorted(buckets.items(), key=lambda kv: (kv[0] != "memory", -kv[1]))
    parts = ", ".join(f"{v} {k}" for k, v in order)
    return f"{total} tools · {parts}" if parts else "0 tools"


def _pulse(name: str, detail: str, root: Path) -> str:
    """Format one live tool-call line: repo-relative path, memory calls flagged
    with ⟐ so rekal recall/store stands out from plain exploration."""
    detail = detail.replace(f"{root}/", "").replace("\n", " ")
    if name.startswith("mcp__rekal__"):
        mark, label = "⟐", name.removeprefix("mcp__rekal__")
    else:
        mark, label = "·", _tool_bucket(name)
    return f"    {mark} {label} {detail}".rstrip()[:88]


def run_claude(
    argv: list[str], cwd: Path, env: dict[str, str], rr: RunResult, *, live: bool = True
) -> None:
    """Run headless claude in one streaming pass: fold events into `rr`, echo
    each tool call live so long runs show a pulse instead of silence.

    A successful headless run always emits a terminal `result` event; its
    absence (or a zero-token error) means the run failed — auth, crash — so we
    hard-exit rather than record a poisoned zero-token run. This structural
    check replaces sniffing stderr for a "Not logged in" string.
    """
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
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        match ev.get("type"):
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
        err = "".join(err_chunks)
        tail = (err.strip().splitlines() or ["(no stderr)"])[-1]
        sys.exit(f"claude run failed (exit {code}); is it logged in? — {tail[:200]}")


def one_run(repo: str, arm: str, q: dict, role: str, run: int, pos: str) -> RunResult:
    """Execute one headless question, stream its tool pulse, print a result line."""
    rr = RunResult(repo=repo, arm=arm, pair=q["pair"], role=role, run=run)
    argv, env = build_cmd(arm, repo, q[role])
    print(f"\n{pos} {q['pair']}/{role} · {arm} #{run}", flush=True)
    run_claude(argv, REPOS / repo, env, rr)
    print(
        f"      = {_tokens(rr.total_tokens)} tok · {rr.num_turns} turns · "
        f"{tool_summary(rr.tool_calls)}"
    )
    return rr


def rollup(pair: str, role: str, by_arm: dict[str, list[RunResult]]) -> None:
    """After a (pair, role) group's arms all run, print the running verdict:
    median tokens per arm, each warm arm's delta vs cold, and whether rekal
    saved or cost tokens on this question — the insight the raw log buries."""

    def median_tok(arm: str) -> float | None:
        rrs = by_arm.get(arm)
        return statistics.median([r.total_tokens for r in rrs]) if rrs else None

    cold = median_tok(Arm.COLD)
    print(f"  ┌─ {pair}/{role} ── median over runs ──")
    for arm in Arm:  # fixed cold → warm-empty → warm-seed order
        m = median_tok(arm)
        if m is None:
            continue
        delta = f"  {(m - cold) / cold * 100:+.0f}% vs cold" if cold and arm != Arm.COLD else ""
        print(f"  │  {arm:<11} {_tokens(m):>7} tok{delta}")
    ws = median_tok(Arm.WARM_SEED)
    if cold and ws:
        pct = (cold - ws) / cold * 100
        verdict = "rekal SAVED" if pct > 0 else "rekal COST MORE"
        print(f"  └─ net {pct:+.0f}%  → {verdict}")
    else:
        print("  └─")


def _split_valid(raw: str, enum: type[StrEnum], label: str) -> list[str]:
    """Split a comma list and reject unknown values — a typo would otherwise
    run as a bogus arm and get silently dropped by `aggregate`."""
    valid = set(enum)
    items = [x.strip() for x in raw.split(",") if x.strip()]
    if bad := [x for x in items if x not in valid]:
        sys.exit(f"unknown {label}: {', '.join(bad)}; known: {', '.join(enum)}")
    return items


@app.command()
def run(
    repo: str,
    n: int = typer.Option(3, help="runs per (question, arm)"),
    arms: str = typer.Option(",".join(Arm), help="comma list of arms"),
    roles: str = typer.Option(",".join(Role), help="comma list: seed,heldout"),
) -> None:
    """Run all questions across arms, N times each; append to results/REPO.jsonl."""
    ensure_repo(repo)
    arm_list = _split_valid(arms, Arm, "arm")
    role_list = _split_valid(roles, Role, "role")
    if Arm.WARM_SEED in arm_list and mem_count(seed_db(repo)) == 0:
        sys.exit(
            f"warm-seed requested but {seed_db(repo).name} is empty — run `learn {repo}` first"
        )
    qs = json.loads((QUESTIONS / f"{repo}.json").read_text())
    total = len(qs) * len(role_list) * len(arm_list) * n
    print(
        f"\n{repo}: {len(qs)} pairs x {len(role_list)} roles x "
        f"{len(arm_list)} arms x {n} runs = {total} runs"
    )
    out = RESULTS / f"{repo}.jsonl"
    done = 0
    with out.open("a") as f:
        # Group by (pair, role) so each group's arms can be compared the moment
        # it finishes — the live verdict, not just a wall of per-run lines.
        for q in qs:
            for role in role_list:
                by_arm: dict[str, list[RunResult]] = {}
                for arm, i in product(arm_list, range(n)):
                    done += 1
                    rr = one_run(repo, arm, q, role, i, f"[{done}/{total}]")
                    f.write(json.dumps(rr.__dict__) + "\n")
                    f.flush()
                    by_arm.setdefault(arm, []).append(rr)
                rollup(q["pair"], role, by_arm)
    print(f"\nwrote {done} runs to {out} — run `judge {repo}` then `aggregate {repo}`")


# --------------------------------------------------------------------------- #
# learn — build + freeze the seed DB
# --------------------------------------------------------------------------- #
@app.command()
def learn(repo: str) -> None:
    """Build this repo's seed DB: answer each seed question with store on, then
    freeze the DB read-only.

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
        prompt = (
            q["seed"] + "\n\nAfter answering, call memory_store to save each durable, "
            "non-obvious fact you learned about this subsystem so a future "
            "session can recall it without re-reading the code."
        )
        # warm-seed shape targets this repo's seed DB; store=True exposes the
        # write tool (measured runs don't get it) and the prompt turns it on.
        argv, env = build_cmd(Arm.WARM_SEED, repo, prompt, store=True)
        before = mem_count(seed)
        print(f"\nlearning {q['pair']} ...", flush=True)
        rr = RunResult(repo=repo, arm="learn", pair=q["pair"], role=Role.SEED, run=0)
        run_claude(argv, REPOS / repo, env, rr)
        stored = mem_count(seed) - before
        print(f"learned: {q['pair']:<22} (+{stored} memories)")
        if stored == 0:
            print("  WARNING: nothing stored — agent answered without memory_store")
    total = mem_count(seed)
    seed.chmod(0o444)  # freeze so measured runs can't mutate it
    print(f"seed DB frozen: {seed} ({total} memories)")
    if total == 0:
        print(
            "ERROR: seed DB empty — warm-seed arm would equal warm-empty. "
            "Check that memory_store tool calls are being made."
        )


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


@app.command()
def judge(repo: str) -> None:
    """Grade every recorded answer 0-2 against the real source.

    Guards the headline claim: a warm run that spends fewer tokens is only a
    win if it answered as well. The grader is a fresh `--bare` agent (no
    rekal) so it can't inherit either arm's advantage. Writes REPO.judged.jsonl.
    """
    ensure_repo(repo)
    rows = [
        json.loads(x) for x in (RESULTS / f"{repo}.jsonl").read_text().splitlines() if x.strip()
    ]
    qmap = {q["pair"]: q for q in json.loads((QUESTIONS / f"{repo}.json").read_text())}
    out = RESULTS / f"{repo}.judged.jsonl"
    with out.open("w") as f:
        for i, r in enumerate(rows, start=1):
            question = qmap[r["pair"]][r["role"]]
            prompt = GRADER.format(repo=repo, q=question, a=r["answer"])
            proc = subprocess.run(
                [
                    "claude",
                    "-p",
                    prompt,
                    "--bare",
                    "--output-format",
                    "json",
                    *HEADLESS,
                    "--allowedTools",
                    "Read,Grep,Glob",
                ],
                cwd=REPOS / repo,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            score, reason = parse_verdict(proc.stdout)
            r["score"], r["score_reason"] = score, reason
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(f"[{i}/{len(rows)}] {r['pair']}/{r['role']} {r['arm']}: score {score}")
    print(f"wrote {out}")


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #
@app.command()
def aggregate(repo: str) -> None:
    """Report median tokens per arm and the overhead/benefit/net decomposition.

    Prefers REPO.judged.jsonl so quality sits next to cost; falls back to raw
    results with a warning, since without grades a warm 'saving' can't be
    trusted.
    """
    judged = RESULTS / f"{repo}.judged.jsonl"
    path = judged if judged.exists() else RESULTS / f"{repo}.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if not judged.exists():
        print(
            "WARNING: no judged file — quality unverified, warm 'wins' may be "
            "cheaper AND worse. Run `judge` first."
        )
    # group by (pair, role, arm) -> total tokens per run
    groups: dict[Key, list[int]] = {}
    scores: dict[Key, list[int]] = {}
    for r in rows:
        key: Key = (r["pair"], r["role"], r["arm"])
        tot = r["input_tokens"] + r["output_tokens"] + r["cache_read"] + r["cache_creation"]
        groups.setdefault(key, []).append(tot)
        if "score" in r:
            scores.setdefault(key, []).append(r["score"])

    def med(pair: str, role: str, arm: str) -> float | None:
        xs = groups.get((pair, role, arm))
        return statistics.median(xs) if xs else None

    pairs = sorted({r["pair"] for r in rows})
    print(f"\n=== {repo}: median total tokens ===")
    print(f"{'pair':<18}{'role':<9}{'cold':>10}{'warm-empty':>12}{'warm-seed':>11}{'net%':>8}")
    for pair in pairs:
        for role in Role:
            c = med(pair, role, Arm.COLD)
            we = med(pair, role, Arm.WARM_EMPTY)
            ws = med(pair, role, Arm.WARM_SEED)
            if c is None or ws is None:
                continue
            we_s = "-" if we is None else f"{we:.0f}"
            print(f"{pair:<18}{role:<9}{c:>10.0f}{we_s:>12}{ws:>11.0f}{100 * (c - ws) / c:>7.1f}%")

    def pool(arm: str, role: str) -> list[int]:
        return [g for (_p, rl, a), xs in groups.items() if a == arm and rl == role for g in xs]

    def qual(arm: str, role: str) -> float | None:
        xs = [s for (_p, rl, a), ss in scores.items() if a == arm and rl == role for s in ss]
        return statistics.mean(xs) if xs else None

    for role in Role:
        c, we, ws = (
            statistics.median(pool(Arm.COLD, role) or [0]),
            statistics.median(pool(Arm.WARM_EMPTY, role) or [0]),
            statistics.median(pool(Arm.WARM_SEED, role) or [0]),
        )
        if not c:
            continue
        print(
            f"\n[{role}] cold={c:.0f} warm-empty={we:.0f} warm-seed={ws:.0f} "
            f"| overhead={we - c:.0f} benefit={we - ws:.0f} net={c - ws:.0f} "
            f"({100 * (c - ws) / c:.1f}% saved)"
        )
        qc, qw = qual(Arm.COLD, role), qual(Arm.WARM_SEED, role)
        if qc is not None and qw is not None:
            flag = "  <-- WARM WORSE, savings suspect" if qw < qc - 0.1 else ""
            print(f"       quality: cold={qc:.2f} warm-seed={qw:.2f}{flag}")


if __name__ == "__main__":
    app()
