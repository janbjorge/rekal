"""CLI entry point: rekal, rekal health, rekal export."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from rekal.adapters.mcp_adapter import (
    default_db_path,
    find_config_file,
    load_file_config,
)
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.embeddings import FastEmbedder

if TYPE_CHECKING:
    from rekal.models import MemoryType


def get_db_path(args: argparse.Namespace) -> str:
    return args.db or os.environ.get("REKAL_DB_PATH", default_db_path())


async def run_serve() -> None:  # pragma: no cover — interactive stdio server
    from rekal.adapters.mcp_adapter import mcp

    await mcp.run_stdio_async()


async def run_health(db_path: str) -> None:
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    embed = FastEmbedder()
    db = await SqliteDatabase.create(db_path, embed)
    try:
        report = await db.memory_health()
        print(json.dumps(report.model_dump(), indent=2))
    finally:
        await db.close()


async def run_recall(
    db_path: str,
    *,
    project: str | None,
    query: str | None,
    limit: int,
    fmt: str,
) -> None:
    # Recall must never block a session: a missing DB is not an error here
    # (unlike health/export, which sys.exit(1)). Emit nothing and return.
    if not Path(db_path).exists():
        if fmt == "json":
            print("[]")
        return

    embed = FastEmbedder()
    db = await SqliteDatabase.create(db_path, embed)
    try:
        if query:
            # Query path embeds the query (loads the ONNX model) and runs the
            # full hybrid search. scratch_limit=0: per-turn injection stays lean.
            weights = await db.resolve_weights(
                project, file_config=load_file_config(find_config_file())
            )
            result = await db.build_context(
                query, project=project, limit=limit, scratch_limit=0, weights=weights
            )
            memories = result.memories
        else:
            # No query (session start): recency-ordered, no embedding load.
            memories = await db.memory_timeline(project=project, limit=limit)
    finally:
        await db.close()

    if fmt == "json":
        print(json.dumps([m.model_dump() for m in memories], indent=2))
        return

    # Text format: a compact block for context injection. Empty result prints
    # nothing so the calling hook injects nothing.
    if not memories:
        return
    scope = f" (project: {project})" if project else ""
    lines = [f"## rekal memory{scope}"]
    lines.extend(f"- [{m.memory_type}] {m.content} (id {m.id})" for m in memories)
    print("\n".join(lines))


async def run_export(db_path: str) -> None:
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    embed = FastEmbedder()
    db = await SqliteDatabase.create(db_path, embed)
    try:
        memories = await db.memory_timeline(limit=100_000)
        data = [m.model_dump() for m in memories]
        print(json.dumps(data, indent=2))
    finally:
        await db.close()


async def run_prune(
    db_path: str,
    *,
    project: str | None,
    memory_type: MemoryType | None,
    older_than_days: int | None,
    before: str | None,
    yes: bool,
) -> None:
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

    embed = FastEmbedder()
    db = await SqliteDatabase.create(db_path, embed)
    try:
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
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="rekal", description="rekal memory MCP server")
    parser.add_argument("--db", help="Path to SQLite database file")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Run as MCP server (default)")
    sub.add_parser("health", help="Show database health report")
    sub.add_parser("export", help="Export all memories as JSON")

    recall = sub.add_parser("recall", help="Print memories for hook context injection")
    recall.add_argument("--project", help="Scope to this project (default: $REKAL_PROJECT)")
    recall.add_argument("--query", help="Hybrid-search query. Omit for recency-ordered recall.")
    recall.add_argument("--limit", type=int, default=10, help="Max memories to return")
    recall.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    prune = sub.add_parser("prune", help="Bulk-delete memories by scope (project/type/age)")
    prune.add_argument("--project", help="Restrict to this project")
    prune.add_argument(
        "--memory-type",
        choices=["fact", "preference", "procedure", "context", "episode"],
        help="Restrict to this memory type",
    )
    prune.add_argument(
        "--older-than-days",
        type=int,
        help="Match memories created more than N days ago",
    )
    prune.add_argument(
        "--before",
        help="Match memories with created_at < this ISO timestamp (YYYY-MM-DD HH:MM:SS)",
    )
    prune.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete. Without this flag the command is a dry run.",
    )

    args = parser.parse_args()
    command = args.command or "serve"

    if command == "serve":  # pragma: no cover — interactive stdio server
        asyncio.run(run_serve())
    elif command == "health":
        asyncio.run(run_health(get_db_path(args)))
    elif command == "export":
        asyncio.run(run_export(get_db_path(args)))
    elif command == "recall":
        asyncio.run(
            run_recall(
                get_db_path(args),
                project=args.project or os.environ.get("REKAL_PROJECT"),
                query=args.query,
                limit=args.limit,
                fmt=args.format,
            )
        )
    elif command == "prune":
        asyncio.run(
            run_prune(
                get_db_path(args),
                project=args.project,
                memory_type=args.memory_type,
                older_than_days=args.older_than_days,
                before=args.before,
                yes=args.yes,
            )
        )


if __name__ == "__main__":  # pragma: no cover
    main()
