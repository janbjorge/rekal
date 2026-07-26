"""CLI entry point: rekal mcp|recall|health|export|prune, plus rekal hook <event>."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import typer

from rekal import hooks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from rekal.adapters.sqlite_adapter import SqliteDatabase
    from rekal.models import MemoryResult

RecallFormat = Literal["text", "json"]

app = typer.Typer(add_completion=False, no_args_is_help=True, help="rekal memory MCP server")
hook_app = typer.Typer(help="Claude Code hook handlers (read hook JSON on stdin).")
app.add_typer(hook_app, name="hook", hidden=True)


def get_db_path(db: str | None) -> str:
    from rekal.config import default_db_path

    return db or os.environ.get("REKAL_DB_PATH") or default_db_path()


@asynccontextmanager
async def open_db(db_path: str) -> AsyncIterator[SqliteDatabase]:
    """Open the memory DB for a command, exiting 1 if the file is missing.

    The embedder/sqlite-adapter imports are deferred inside this helper so the
    hook CLI path (which never opens a DB for health/export/prune) stays light.
    """
    from rekal.adapters.sqlite_adapter import SqliteDatabase
    from rekal.config import resolve_readonly
    from rekal.embeddings import FastEmbedder

    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)
    # Frozen DBs (benchmark seeds) must stay inspectable: health/export open
    # read-only when the file is unwritable instead of failing on migration.
    async with SqliteDatabase.session(
        db_path, FastEmbedder(), readonly=resolve_readonly(db_path)
    ) as db:
        yield db


async def run_serve() -> None:  # pragma: no cover — interactive stdio server
    from rekal.adapters.mcp_adapter import create_server

    await create_server().run_stdio_async()


async def run_health(db_path: str) -> None:
    async with open_db(db_path) as db:
        report = await db.memory_health()
        print(json.dumps(report.model_dump(), indent=2))


async def run_export(db_path: str) -> None:
    async with open_db(db_path) as db:
        memories = await db.memory_timeline(limit=100_000)
        data = [m.model_dump() for m in memories]
        print(json.dumps(data, indent=2))


# Trust framing lives with the memories themselves: injected knowledge is only
# cheaper than reading files if the model acts on it instead of re-verifying,
# and provenance anchors + an explicit do-not-re-check instruction are what
# make that happen (bare facts get re-derived).
RECALL_HEADER = (
    "Facts below were verified against this codebase when learned; file:line "
    "anchors mark where. Build on them directly — cite anchors instead of "
    "re-opening files to re-check what a memory already states. Read code "
    "only for what these do not cover."
)

# ~1200 tokens. Injection is paid on every turn via the cached prefix, so the
# block is budgeted; whole memories only (a truncated brief is a corrupted
# brief), highest-scored first, and the top hit always ships even when it
# alone exceeds the budget.
RECALL_BUDGET_CHARS = 4800


def memory_block(memory: MemoryResult, *, readonly: bool) -> str:
    """One rendered memory: '### [tag] (as of date)' header + raw content.

    The id is only useful to pass back as ``replaces=`` on a store, so it is
    omitted in readonly sessions where no store tool exists.
    """
    label = memory.tags[0] if memory.tags else "memory"
    as_of = f" (as of {memory.created_at[:10]})" if memory.created_at else ""
    suffix = "" if readonly else f" (id {memory.id})"
    return f"### [{label}]{as_of}{suffix}\n{memory.content}"


def render_recall(
    memories: list[MemoryResult],
    *,
    project: str | None,
    fmt: RecallFormat,
    readonly: bool = False,
) -> str:
    """Render memories for hook injection. Empty text renders to "" (inject
    nothing); empty JSON renders to "[]"."""
    if fmt == "json":
        return json.dumps([m.model_dump() for m in memories], indent=2)
    if not memories:
        return ""
    scope = f" (project: {project})" if project else ""
    parts = [f"## rekal memory{scope}\n{RECALL_HEADER}"]
    used = len(parts[0])
    for i, memory in enumerate(memories):
        block = memory_block(memory, readonly=readonly)
        if i > 0 and used + len(block) > RECALL_BUDGET_CHARS:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


async def recall_memories(
    db_path: str, *, project: str | None, query: str | None, limit: int, readonly: bool
) -> list[MemoryResult]:
    """Fetch memories for recall. A missing DB yields ``[]`` — recall never
    treats an absent DB as an error, unlike health/export.

    ``readonly`` is resolved ONCE by the caller and used for both the DB open
    here and the render there, so the two can never disagree."""
    if not Path(db_path).exists():
        return []

    from rekal.adapters.sqlite_adapter import SqliteDatabase
    from rekal.embeddings import FastEmbedder

    async with SqliteDatabase.session(db_path, FastEmbedder(), readonly=readonly) as db:
        if query:
            # Query path embeds the query and runs hybrid search directly.
            # The relevance floor keeps injection from carrying low-signal
            # hits into every prompt.
            from rekal.config import find_config_file, load_file_config
            from rekal.scoring import resolve_weights

            weights = resolve_weights(load_file_config(find_config_file()))
            return await db.search(
                query,
                limit=limit,
                project=project,
                weights=weights,
                min_score=0.25,
            )
        # No query (CLI `recall` without --query): recency-ordered, no
        # embedding needed for ranking.
        return await db.memory_timeline(project=project, limit=limit)


async def run_recall(
    db_path: str,
    *,
    project: str | None,
    query: str | None,
    limit: int,
    fmt: RecallFormat,
) -> None:
    from rekal.config import resolve_readonly

    readonly = resolve_readonly(db_path)
    memories = await recall_memories(
        db_path, project=project, query=query, limit=limit, readonly=readonly
    )
    output = render_recall(memories, project=project, fmt=fmt, readonly=readonly)
    if output:
        print(output)


async def run_prune(
    db_path: str,
    *,
    project: str | None,
    older_than_days: int | None,
    before: str | None,
    yes: bool,
) -> None:
    # Existence is checked up front so a missing DB beats the no-filter error
    # below; open_db re-checks it when we actually open.
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    cutoff = before
    if older_than_days is not None:
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    if project is None and cutoff is None:
        print("Refusing to prune without a filter. Use --project, --older-than-days, or --before.")
        sys.exit(2)

    async with open_db(db_path) as db:
        matched = await db.prune(project=project, before=cutoff, dry_run=True)
        scope_parts = []
        if project is not None:
            scope_parts.append(f"project={project}")
        if cutoff is not None:
            scope_parts.append(f"before={cutoff}")
        scope = ", ".join(scope_parts)
        print(f"Matched {len(matched)} memories ({scope}).")

        if not yes:
            if matched:
                print("Dry run only. Pass --yes to delete.")
            return
        if not matched:
            return

        deleted = await db.prune(project=project, before=cutoff, dry_run=False)
        print(f"Deleted {len(deleted)} memories.")


@app.callback()
def root(
    ctx: typer.Context,
    db: Annotated[str | None, typer.Option("--db", help="Path to SQLite database file")] = None,
) -> None:
    ctx.obj = db


@app.command(help="Run the stdio MCP server (what Claude Code connects to)")
def mcp() -> None:  # pragma: no cover — interactive stdio server
    asyncio.run(run_serve())


@app.command(help="Show database health report")
def health(ctx: typer.Context) -> None:
    asyncio.run(run_health(get_db_path(ctx.obj)))


@app.command(help="Export all memories as JSON")
def export(ctx: typer.Context) -> None:
    asyncio.run(run_export(get_db_path(ctx.obj)))


@app.command(help="Print memories for hook context injection")
def recall(
    ctx: typer.Context,
    project: Annotated[
        str | None, typer.Option(help="Scope to this project (default: $REKAL_PROJECT)")
    ] = None,
    query: Annotated[
        str | None, typer.Option(help="Hybrid-search query. Omit for recency-ordered recall.")
    ] = None,
    limit: Annotated[int, typer.Option(help="Max memories to return")] = 10,
    fmt: Annotated[RecallFormat, typer.Option("--format", help="Output format")] = "text",
) -> None:
    asyncio.run(
        run_recall(
            get_db_path(ctx.obj),
            project=project or os.environ.get("REKAL_PROJECT"),
            query=query,
            limit=limit,
            fmt=fmt,
        )
    )


@app.command(help="Bulk-delete memories by scope (project/age)")
def prune(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option(help="Restrict to this project")] = None,
    older_than_days: Annotated[
        int | None, typer.Option(help="Match memories created more than N days ago")
    ] = None,
    before: Annotated[
        str | None,
        typer.Option(help="Match memories with created_at < ISO timestamp (YYYY-MM-DD HH:MM:SS)"),
    ] = None,
    yes: Annotated[
        bool, typer.Option(help="Actually delete. Without this flag the command is a dry run.")
    ] = False,
) -> None:
    asyncio.run(
        run_prune(
            get_db_path(ctx.obj),
            project=project,
            older_than_days=older_than_days,
            before=before,
            yes=yes,
        )
    )


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload))


def recall_text(
    db_path: str, project: str | None, query: str | None, limit: int, *, readonly: bool = False
) -> str | None:
    """Recall memory for hook injection as rendered text, or None.

    Never raises: recall must not block a session or turn, so any failure
    (corrupt DB, load error) degrades to None and the caller injects the
    directive alone. A missing DB already yields [] in recall_memories.
    """
    try:
        memories = asyncio.run(
            recall_memories(db_path, project=project, query=query, limit=limit, readonly=readonly)
        )
    except Exception:  # recall must never block the session/turn
        return None
    return render_recall(memories, project=project, fmt="text", readonly=readonly) or None


def read_prompt() -> str | None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    prompt = data.get("prompt")
    return prompt if isinstance(prompt, str) and prompt else None


def deny_if_memory_file(reason: str) -> None:
    data = json.load(sys.stdin)
    path = data.get("tool_input", {}).get("file_path", "")
    if path and hooks.is_memory_file(path):
        emit(hooks.deny_payload(reason))


@hook_app.command("session-start", help="SessionStart: inject the memory directive.")
def hook_session_start(ctx: typer.Context) -> None:
    # Deliberately no recall here: recency-ordered, query-less injection is
    # mostly the wrong topic in a multi-subsystem DB — measured cost with no
    # measured benefit. Recall is query-matched, at UserPromptSubmit only.
    from rekal.config import resolve_readonly

    readonly = resolve_readonly(get_db_path(ctx.obj))
    emit(hooks.context_payload("SessionStart", hooks.directive("SessionStart", readonly=readonly)))


@hook_app.command("user-prompt-submit", help="UserPromptSubmit: inject query recall + directive.")
def hook_user_prompt_submit(ctx: typer.Context) -> None:
    from rekal.config import resolve_readonly

    prompt = read_prompt()
    project = os.environ.get("REKAL_PROJECT")
    db_path = get_db_path(ctx.obj)
    # Resolved ONCE per hook invocation and threaded through open + render:
    # re-deriving it per layer let the DB open readonly while the render
    # claimed write-mode (or vice versa) if the file's writability raced.
    readonly = resolve_readonly(db_path)
    memory = recall_text(db_path, project, prompt, 10, readonly=readonly) if prompt else None
    payload = hooks.context_payload(
        "UserPromptSubmit", hooks.directive("UserPromptSubmit", readonly=readonly), memory
    )
    if payload:
        emit(payload)


@hook_app.command("block-memory-writes", help="PreToolUse(Edit|Write): deny memory-file writes.")
def hook_block_memory_writes() -> None:
    deny_if_memory_file(hooks.BLOCK_WRITE_REASON)


@hook_app.command("redirect-memory-reads", help="PreToolUse(Read): redirect memory-file reads.")
def hook_redirect_memory_reads() -> None:
    deny_if_memory_file(hooks.REDIRECT_READ_REASON)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
