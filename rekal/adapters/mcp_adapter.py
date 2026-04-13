"""FastMCP server, lifespan, and DB initialization."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context, FastMCP

from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.embeddings import FastEmbedder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def default_db_path() -> str:
    return str(Path.home() / ".rekal" / "memory.db")


@dataclass
class AppContext:
    db: SqliteDatabase
    default_project: str | None = None


def resolve_project(ctx: Context, project: str | None) -> str | None:
    """Return explicit project if given, otherwise fall back to session default."""
    if project is not None:
        return project
    return ctx.request_context.lifespan_context.default_project


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    db_path = os.environ.get("REKAL_DB_PATH", default_db_path())
    default_project = os.environ.get("REKAL_PROJECT")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    embed = FastEmbedder()
    db = await SqliteDatabase.create(db_path, embed, dimensions=embed.dimensions)
    try:
        yield AppContext(db=db, default_project=default_project)
    finally:
        await db.close()


INSTRUCTIONS = """\
rekal gives you persistent long-term memory across sessions.

## Continuous memory capture

Do NOT wait until the end of a session to store memories. As you work, proactively \
store durable knowledge the moment it surfaces:

- User states a preference or corrects you → memory_store immediately
- You discover a non-obvious architectural fact → memory_store
- A debugging session reveals a surprising root cause → memory_store
- User describes a workflow or procedure → memory_store
- A decision is made with reasoning → memory_store

Before every store, call memory_search first to deduplicate. If the same topic exists, \
use memory_supersede instead of creating a duplicate.

## Session start

Call memory_build_context with your current task to load relevant prior knowledge. \
Do this before exploring the codebase.

## What NOT to store

- Transient state ("currently editing X", "tests passing")
- Trivially re-discoverable facts (line numbers, file lengths)
- Vague platitudes ("user likes clean code")
- Secrets, API keys, passwords, tokens — never
"""

mcp = FastMCP("rekal", instructions=INSTRUCTIONS, lifespan=lifespan)

import rekal.adapters.tools  # noqa: E402, F401
