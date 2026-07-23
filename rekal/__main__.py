"""CLI entry point: rekal mcp|recall|health|export|prune, plus rekal hook <event>."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import typer

from rekal import hooks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from rekal.adapters.sqlite_adapter import SqliteDatabase
    from rekal.models import MemoryResult, MemoryType

RecallFormat = Literal["text", "json"]


class MemoryTypeChoice(StrEnum):
    """Prune's --memory-type choices; .value is a valid ``MemoryType`` literal."""

    fact = "fact"
    preference = "preference"
    procedure = "procedure"
    context = "context"
    episode = "episode"


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
    from rekal.embeddings import FastEmbedder

    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)
    async with SqliteDatabase.session(db_path, FastEmbedder()) as db:
        yield db


async def run_serve() -> None:  # pragma: no cover — interactive stdio server
    from rekal.adapters.mcp_adapter import mcp

    await mcp.run_stdio_async()


async def run_health(db_path: str) -> None:
    async with open_db(db_path) as db:
        report = await db.memory_health()
        print(json.dumps(report.model_dump(), indent=2))


async def run_export(db_path: str) -> None:
    async with open_db(db_path) as db:
        memories = await db.memory_timeline(limit=100_000)
        data = [m.model_dump() for m in memories]
        print(json.dumps(data, indent=2))


def render_recall(memories: list[MemoryResult], *, project: str | None, fmt: RecallFormat) -> str:
    """Render memories for hook injection. Empty text renders to "" (inject
    nothing); empty JSON renders to "[]"."""
    if fmt == "json":
        return json.dumps([m.model_dump() for m in memories], indent=2)
    if not memories:
        return ""
    scope = f" (project: {project})" if project else ""
    lines = [f"## rekal memory{scope}"]
    lines.extend(f"- [{m.memory_type}] {m.content} (id {m.id})" for m in memories)
    return "\n".join(lines)


async def recall_memories(
    db_path: str, *, project: str | None, query: str | None, limit: int
) -> list[MemoryResult]:
    """Fetch memories for recall. A missing DB yields ``[]`` — recall never
    treats an absent DB as an error, unlike health/export."""
    if not Path(db_path).exists():
        return []

    from rekal.adapters.sqlite_adapter import SqliteDatabase
    from rekal.config import find_config_file, load_file_config
    from rekal.embeddings import FastEmbedder

    async with SqliteDatabase.session(db_path, FastEmbedder()) as db:
        if query:
            # Query path embeds the query and runs the durable-tier hybrid
            # search directly; build_context would also compute scratch,
            # conflicts, and a timeline summary that injection discards.
            weights = await db.resolve_weights(
                project, file_config=load_file_config(find_config_file())
            )
            return await db.search(
                query, limit=limit, project=project, tier="durable", weights=weights
            )
        # No query (session start): recency-ordered, no embedding load.
        return await db.memory_timeline(project=project, limit=limit)


async def run_recall(
    db_path: str,
    *,
    project: str | None,
    query: str | None,
    limit: int,
    fmt: RecallFormat,
) -> None:
    memories = await recall_memories(db_path, project=project, query=query, limit=limit)
    output = render_recall(memories, project=project, fmt=fmt)
    if output:
        print(output)


async def run_prune(
    db_path: str,
    *,
    project: str | None,
    memory_type: MemoryType | None,
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

    if project is None and memory_type is None and cutoff is None:
        print(
            "Refusing to prune without a filter. "
            "Use --project, --memory-type, --older-than-days, or --before."
        )
        sys.exit(2)

    async with open_db(db_path) as db:
        count, _ = await db.prune(
            project=project,
            memory_type=memory_type,
            before=cutoff,
            dry_run=True,
        )
        scope_parts = []
        if project is not None:
            scope_parts.append(f"project={project}")
        if memory_type is not None:
            scope_parts.append(f"type={memory_type}")
        if cutoff is not None:
            scope_parts.append(f"before={cutoff}")
        scope = ", ".join(scope_parts)
        print(f"Matched {count} memories ({scope}).")

        if not yes:
            if count > 0:
                print("Dry run only. Pass --yes to delete.")
            return
        if count == 0:
            return

        deleted_count, _ = await db.prune(
            project=project,
            memory_type=memory_type,
            before=cutoff,
            dry_run=False,
        )
        print(f"Deleted {deleted_count} memories.")


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


@app.command(help="Bulk-delete memories by scope (project/type/age)")
def prune(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option(help="Restrict to this project")] = None,
    memory_type: Annotated[
        MemoryTypeChoice | None, typer.Option(help="Restrict to this memory type")
    ] = None,
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
            memory_type=memory_type.value if memory_type else None,
            older_than_days=older_than_days,
            before=before,
            yes=yes,
        )
    )


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload))


def recall_text(db_path: str, project: str | None, query: str | None, limit: int) -> str | None:
    """Recall memory for hook injection as rendered text, or None.

    Never raises: recall must not block a session or turn, so any failure
    (corrupt DB, load error) degrades to None and the caller injects the
    directive alone. A missing DB already yields [] in recall_memories.
    """
    try:
        memories = asyncio.run(recall_memories(db_path, project=project, query=query, limit=limit))
    except Exception:  # recall must never block the session/turn
        return None
    return render_recall(memories, project=project, fmt="text") or None


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


@hook_app.command("session-start", help="SessionStart: inject recency recall + directive.")
def hook_session_start(ctx: typer.Context) -> None:
    project = os.environ.get("REKAL_PROJECT")
    memory = recall_text(get_db_path(ctx.obj), project, None, 10)
    emit(hooks.context_payload("SessionStart", hooks.SESSION_START_DIRECTIVE, memory))


@hook_app.command("user-prompt-submit", help="UserPromptSubmit: inject query recall + directive.")
def hook_user_prompt_submit(ctx: typer.Context) -> None:
    prompt = read_prompt()
    project = os.environ.get("REKAL_PROJECT")
    memory = recall_text(get_db_path(ctx.obj), project, prompt, 5) if prompt else None
    emit(hooks.context_payload("UserPromptSubmit", hooks.PROMPT_SUBMIT_DIRECTIVE, memory))


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
