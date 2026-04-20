# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rekal",
#     "typer>=0.15.0",
#     "tabulate>=0.9.0",
#     "aiosqlite>=0.20.0",
#     "sqlite-vec>=0.1.6",
#     "fastembed>=0.4.0",
# ]
# ///
"""LoCoMo-10 retrieval benchmark for rekal.

Measures how well rekal's hybrid search (FTS5 + vector + recency) retrieves
gold-standard evidence from long conversations, compared to a naive flat-file
baseline that dumps everything into context.

Three approaches:
  raw        — every dialogue turn is a separate memory
  compressed — Claude Haiku compresses each session into facts (one memory each)
  flat-file  — entire conversation concatenated into a single memory

Compressed approach auto-detected: runs if `claude` CLI is on PATH, skipped otherwise.

Usage:
  uv run bench/locomo_eval.py
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import sys
import urllib.request
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, fields
from pathlib import Path

import typer
from tabulate import tabulate

from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.embeddings import FastEmbedder
from rekal.scoring import ScoringWeights

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCOMO_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
DATA_PATH = Path("bench/locomo10.json")
CACHE_PATH = Path("bench/locomo_summaries.json")
K = 10

CATEGORY_NAMES: dict[int, str] = {
    1: "single-hop",
    2: "temporal",
    3: "multi-hop",
    4: "open-ended",
    5: "adversarial",
}

COMPRESS_PROMPT = """\
You are a fact extractor. Below is a TRANSCRIPT of a conversation between two people.
Extract every durable fact. One fact per line. Output ONLY facts, nothing else.
If the transcript contains no durable facts (just greetings/small talk), output: NONE

Cover: names, dates, preferences, events, plans, opinions, relationships, locations, jobs.

Compress each fact using these rules (same rules rekal uses for memory storage):
- Drop: articles (a/an/the), filler (just/really/basically/actually/simply), \
pleasantries, hedging (might/could/maybe)
- Replace verbose phrases: "in order to" → "to", "make sure to" → ensure
- Use short synonyms: big not extensive, fix not "implement a solution for"
- Fragments OK. State actions directly — no "you should", "remember to"
- Merge redundant points that say the same thing differently
- Keep exact: technical terms, proper nouns, version numbers, values, causality (X because Y)

One fact = 1-2 sentences max.

BAD:  "User said yeah I think maybe we could try using Python for this project"
GOOD: "Prefer Python for project"

BAD:  "So we eventually decided to use Postgres because the team knows it and data is relational"
GOOD: "DB: Postgres. Team familiar, data relational."

TRANSCRIPT:"""

PopulateFn = Callable[
    [SqliteDatabase, list[list["Turn"]]],
    Coroutine[None, None, int],
]

app = typer.Typer(add_completion=False)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Turn:
    session: int
    dia_id: str
    speaker: str
    text: str


@dataclass(frozen=True, slots=True)
class QAPair:
    question: str
    answer: str
    evidence: tuple[str, ...]
    category: int

    @property
    def gold_sessions(self) -> frozenset[int]:
        out: set[int] = set()
        for e in self.evidence:
            if e.startswith("D") and ":" in e:
                try:
                    out.add(int(e[1:].split(":")[0]))
                except ValueError:
                    pass
        return frozenset(out)


@dataclass(frozen=True, slots=True)
class Conversation:
    sessions: list[list[Turn]]
    qa_pairs: list[QAPair]

    @property
    def turn_count(self) -> int:
        return sum(len(s) for s in self.sessions)


@dataclass(slots=True)
class Metrics:
    recall: float = 0.0
    precision: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0

    @property
    def f1(self) -> float:
        denom = self.precision + self.recall
        return 2 * self.precision * self.recall / denom if denom > 0 else 0.0


@dataclass(slots=True)
class ConvResult:
    tokens: int = 0
    per_query: list[Metrics] = field(default_factory=list)
    per_category: dict[int, list[Metrics]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Summary:
    approach: str
    tokens: float
    recall: float
    precision: float
    f1: float
    ndcg: float
    mrr: float
    efficiency: float


@dataclass(frozen=True, slots=True)
class CategorySummary:
    category: str
    recall: float
    precision: float
    f1: float
    ndcg: float
    mrr: float
    n: int


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def download_locomo(dest: Path) -> None:
    print(f"Downloading LoCoMo-10 → {dest} ...")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(LOCOMO_URL, dest)
    print("Done.")


def load_conversations(path: Path) -> list[Conversation]:
    with open(path) as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = [raw]

    out: list[Conversation] = []
    for conv in raw:
        sessions: list[list[Turn]] = []
        idx = 1
        while f"session_{idx}" in conv["conversation"]:
            sessions.append([
                Turn(idx, t["dia_id"], t["speaker"], t["text"])
                for t in conv["conversation"][f"session_{idx}"]
            ])
            idx += 1

        qa_pairs = [
            QAPair(
                question=qa["question"],
                answer=str(qa.get("answer") or qa.get("adversarial_answer", "")),
                evidence=tuple(qa.get("evidence", ())),
                category=qa.get("category", 0),
            )
            for qa in conv.get("qa", [])
        ]
        out.append(Conversation(sessions=sessions, qa_pairs=qa_pairs))
    return out


# ---------------------------------------------------------------------------
# Populate strategies
# ---------------------------------------------------------------------------


def format_turn(turn: Turn) -> str:
    return f"[{turn.dia_id}] {turn.speaker}: {turn.text}"


async def populate_raw(db: SqliteDatabase, sessions: list[list[Turn]]) -> int:
    chars = 0
    for session in sessions:
        for turn in session:
            line = format_turn(turn)
            await db.store(line)
            chars += len(line)
    return chars // 4


async def populate_flat(db: SqliteDatabase, sessions: list[list[Turn]]) -> int:
    blob = "\n".join(format_turn(t) for s in sessions for t in s)
    await db.store(blob)
    return len(blob) // 4


# -- Compression via Claude CLI -----------------------------------------------

SEM = asyncio.Semaphore(10)


async def compress_one(text: str) -> str:
    async with SEM:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", f"{COMPRESS_PROMPT}\n{text}",
            "--model", "haiku",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            print(f"  compress error: {stderr.decode()[:200]}", file=sys.stderr)
            return ""
        return stdout.decode().strip()


async def compress_sessions(
    sessions: list[list[Turn]],
    conv_idx: int,
    cache: dict[str, str],
) -> dict[int, str]:
    pending: dict[int, asyncio.Task[str]] = {}
    results: dict[int, str] = {}

    for i, session in enumerate(sessions, 1):
        key = f"conv-{conv_idx}:session-{i}"
        if key in cache:
            results[i] = cache[key]
        else:
            text = "\n".join(f"{t.speaker}: {t.text}" for t in session)
            pending[i] = asyncio.create_task(compress_one(text))

    if pending:
        done = await asyncio.gather(*pending.values())
        for sess_num, summary in zip(pending, done):
            cache[f"conv-{conv_idx}:session-{sess_num}"] = summary
            results[sess_num] = summary

    return results


def make_compress_fn(conv_idx: int, cache: dict[str, str]) -> PopulateFn:
    async def populate(db: SqliteDatabase, sessions: list[list[Turn]]) -> int:
        summaries = await compress_sessions(sessions, conv_idx, cache)
        chars = 0
        for num in sorted(summaries):
            if summaries[num]:
                await db.store(summaries[num], tags=[f"session:{num}"])
                chars += len(summaries[num])
        return chars // 4
    return populate


# ---------------------------------------------------------------------------
# Session mapping — which session(s) does a retrieved memory belong to?
# ---------------------------------------------------------------------------


def memory_to_sessions(
    content: str,
    tags: list[str] | None,
    sessions: list[list[Turn]],
) -> frozenset[int]:
    found: set[int] = set()

    for sess_idx, session in enumerate(sessions, 1):
        if any(turn.dia_id in content for turn in session):
            found.add(sess_idx)

    for tag in (tags or []):
        if tag.startswith("session:"):
            try:
                found.add(int(tag.split(":")[1]))
            except ValueError:
                pass

    return frozenset(found)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_query(gold: frozenset[int], retrieved: list[frozenset[int]], k: int) -> Metrics:
    if not gold:
        return Metrics()

    hit_union: set[int] = set()
    for rs in retrieved:
        hit_union |= rs
    recall = len(gold & hit_union) / len(gold)

    relevant = [bool(rs & gold) for rs in retrieved]
    precision = sum(relevant) / len(relevant) if relevant else 0.0

    mrr = 0.0
    for rank, hit in enumerate(relevant, 1):
        if hit:
            mrr = 1.0 / rank
            break

    dcg = sum(float(hit) / math.log2(rank + 1) for rank, hit in enumerate(relevant, 1))
    num_relevant = sum(relevant)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(num_relevant, k)))
    ndcg = dcg / idcg if idcg > 0 else 0.0

    return Metrics(recall=recall, precision=precision, mrr=mrr, ndcg=ndcg)


async def evaluate_conv(
    db: SqliteDatabase,
    qa_pairs: list[QAPair],
    sessions: list[list[Turn]],
) -> ConvResult:
    weights = ScoringWeights()
    result = ConvResult()

    for qa in qa_pairs:
        gold = qa.gold_sessions
        if not gold:
            continue

        hits = await db.search(qa.question, limit=K, weights=weights)
        retrieved = [memory_to_sessions(r.content, r.tags, sessions) for r in hits]
        m = score_query(gold, retrieved, K)

        result.per_query.append(m)
        result.per_category.setdefault(qa.category, []).append(m)

    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def mean_metrics(items: list[Metrics]) -> Metrics:
    n = len(items) or 1
    return Metrics(
        recall=sum(m.recall for m in items) / n,
        precision=sum(m.precision for m in items) / n,
        mrr=sum(m.mrr for m in items) / n,
        ndcg=sum(m.ndcg for m in items) / n,
    )


def build_summary(name: str, results: list[ConvResult]) -> Summary:
    all_queries = [m for r in results for m in r.per_query]
    avg = mean_metrics(all_queries)
    avg_tokens = sum(r.tokens for r in results) / (len(results) or 1)
    eff = avg.recall / (avg_tokens / 1000) if avg_tokens > 0 else 0.0
    return Summary(
        approach=name,
        tokens=avg_tokens,
        recall=avg.recall,
        precision=avg.precision,
        f1=avg.f1,
        ndcg=avg.ndcg,
        mrr=avg.mrr,
        efficiency=eff,
    )


def build_category_summaries(results: list[ConvResult]) -> list[CategorySummary]:
    merged: dict[int, list[Metrics]] = {}
    for r in results:
        for cat, ms in r.per_category.items():
            merged.setdefault(cat, []).extend(ms)

    out: list[CategorySummary] = []
    for cat in sorted(merged):
        avg = mean_metrics(merged[cat])
        out.append(CategorySummary(
            category=CATEGORY_NAMES.get(cat, f"cat-{cat}"),
            recall=avg.recall,
            precision=avg.precision,
            f1=avg.f1,
            ndcg=avg.ndcg,
            mrr=avg.mrr,
            n=len(merged[cat]),
        ))
    return out


# ---------------------------------------------------------------------------
# Table printing via tabulate
# ---------------------------------------------------------------------------


def dataclass_to_table(rows: list[Summary] | list[CategorySummary]) -> str:
    if not rows:
        return ""
    headers = [f.name for f in fields(rows[0])]
    data = [[getattr(r, h) for h in headers] for r in rows]
    return tabulate(data, headers=headers, tablefmt="rounded_outline", floatfmt=".3f", intfmt=",")


def print_section(title: str, rows: list[Summary] | list[CategorySummary]) -> None:
    if not rows:
        return
    print(f"\n  {title}\n")
    print(dataclass_to_table(rows))


def format_progress_table(
    rows: list[tuple[str, int, Metrics, int]],
) -> str:
    """Format per-conversation progress as a compact table."""
    table_rows: list[list[str | int]] = []
    for name, tokens, avg, raw_tokens in rows:
        pct = (tokens / raw_tokens - 1) * 100 if raw_tokens > 0 and tokens != raw_tokens else None
        delta = f"{pct:+.0f}%" if pct is not None else ""
        table_rows.append([name, tokens, f"{avg.recall:.3f}", f"{avg.precision:.3f}", delta])
    return tabulate(table_rows, headers=["approach", "tokens", "R@10", "P@10", "Δ tokens"], tablefmt="simple", intfmt=",")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_one(
    fn: PopulateFn,
    conv: Conversation,
    qa_pairs: list[QAPair],
) -> ConvResult:
    embedder = FastEmbedder()
    embedder.ensure_model()
    db = await SqliteDatabase.create(":memory:", embedder)
    try:
        tokens = await fn(db, conv.sessions)
        result = await evaluate_conv(db, qa_pairs, conv.sessions)
        result.tokens = tokens
        return result
    finally:
        await db.close()


async def _run(data: Path | None) -> None:
    data_path = data or DATA_PATH
    if not data_path.exists():
        download_locomo(data_path)

    convs = load_conversations(data_path)
    print(f"{len(convs)} conversation(s)")
    print("Loading embedding model...")
    FastEmbedder().ensure_model()

    has_compress = shutil.which("claude") is not None
    if has_compress:
        print("claude CLI found — compressed approach enabled")
    else:
        print("claude CLI not found — skipping compressed approach")

    cache: dict[str, str] = {}
    if has_compress and CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text())
        print(f"Loaded {len(cache)} cached summaries")

    approaches: dict[str, PopulateFn | None] = {
        "raw": populate_raw,
        "flat-file": populate_flat,
    }
    if has_compress:
        approaches["compressed"] = None

    collected: dict[str, list[ConvResult]] = {name: [] for name in approaches}

    for ci, conv in enumerate(convs):
        qa_pairs = conv.qa_pairs
        print(f"\nConv {ci}/{len(convs)-1}: {len(conv.sessions)} sessions, {conv.turn_count} turns, {len(qa_pairs)} QA")

        raw_tokens = 0
        progress_rows: list[tuple[str, int, Metrics, int]] = []
        for name in approaches:
            fn: PopulateFn
            if name == "compressed":
                fn = make_compress_fn(ci, cache)
            else:
                fn = approaches[name]  # type: ignore[assignment]
            cr = await run_one(fn, conv, qa_pairs)
            avg = mean_metrics(cr.per_query)

            if name == "raw":
                raw_tokens = cr.tokens

            progress_rows.append((name, cr.tokens, avg, raw_tokens))
            collected[name].append(cr)

        print(format_progress_table(progress_rows))

    if has_compress and cache:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=2))
        print(f"\nCached {len(cache)} summaries → {CACHE_PATH}")

    summaries = [build_summary(name, results) for name, results in collected.items()]
    print_section(f"Aggregated (mean across {len(convs)} conversations, K={K})", summaries)

    for name, results in collected.items():
        cats = build_category_summaries(results)
        print_section(f"By category: {name}", cats)

    print()


@app.command()
def main(
    data: Path | None = typer.Argument(None, help="Path to locomo10.json"),
) -> None:
    """LoCoMo-10 retrieval benchmark for rekal."""
    asyncio.run(_run(data))


if __name__ == "__main__":
    app()
