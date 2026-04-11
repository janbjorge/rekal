"""CLI entry point: rekal, rekal health, rekal export."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from rekal.adapters.mcp_adapter import default_db_path
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.embeddings import FastEmbedder


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="rekal", description="rekal memory MCP server")
    parser.add_argument("--db", help="Path to SQLite database file")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Run as MCP server (default)")
    sub.add_parser("health", help="Show database health report")
    sub.add_parser("export", help="Export all memories as JSON")

    args = parser.parse_args()
    command = args.command or "serve"

    if command == "serve":  # pragma: no cover — interactive stdio server
        asyncio.run(run_serve())
    elif command == "health":
        asyncio.run(run_health(get_db_path(args)))
    elif command == "export":
        asyncio.run(run_export(get_db_path(args)))


if __name__ == "__main__":  # pragma: no cover
    main()
