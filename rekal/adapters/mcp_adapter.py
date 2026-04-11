"""FastMCP server, lifespan, and DB initialization."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.embeddings import FastEmbedder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def default_db_path() -> str:
    return str(Path.home() / ".rekal" / "memory.db")


@dataclass
class AppContext:
    db: SqliteDatabase


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    db_path = os.environ.get("REKAL_DB_PATH", default_db_path())
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    embed = FastEmbedder()
    db = await SqliteDatabase.create(db_path, embed, dimensions=embed.dimensions)
    try:
        yield AppContext(db=db)
    finally:
        await db.close()


mcp = FastMCP("rekal", lifespan=lifespan)

import rekal.adapters.tools  # noqa: E402, F401
