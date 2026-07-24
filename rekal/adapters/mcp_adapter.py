"""FastMCP server factory, lifespan, and DB initialization."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools import register

# Re-exported: path/config helpers live in rekal.config (MCP-free) so the
# recall CLI can use them without importing this module's FastMCP server.
from rekal.config import default_db_path, find_config_file, load_file_config
from rekal.embeddings import FastEmbedder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass
class AppContext:
    db: SqliteDatabase
    default_project: str | None = None
    file_config: dict[str, float] = field(default_factory=dict)


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    db_path = os.environ.get("REKAL_DB_PATH", default_db_path())
    default_project = os.environ.get("REKAL_PROJECT")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    embed = FastEmbedder()
    async with SqliteDatabase.session(db_path, embed, dimensions=embed.dimensions) as db:
        file_config = load_file_config(find_config_file())
        yield AppContext(db=db, default_project=default_project, file_config=file_config)


INSTRUCTIONS = """\
rekal gives you persistent long-term memory across sessions.

Call memory_build_context with your current task before exploring — prior \
sessions may already hold what you need. Trust recalled memories; read \
source only to fill gaps.

Store durable knowledge as it emerges (memory_store): decisions with \
reasoning, user preferences and corrections, non-obvious facts, surprising \
root causes. Distill first — drop filler, keep technical terms, values, and \
causality (X → Y). One memory = one self-contained fact, 1-2 sentences. To \
update or correct an existing memory, pass replaces=<old_id> instead of \
storing a near-duplicate.

Do not store transient state, trivially re-discoverable facts, or secrets. \
Never write memories to files (CLAUDE.md, MEMORY.md); rekal is the only \
memory system.
"""

RECALL_INSTRUCTIONS = """\
rekal gives you read-only access to persistent memory from prior sessions.

Call memory_build_context with your current task before exploring — trust \
recalled memories and read source only to fill genuine gaps. This session \
cannot store memories, so do not attempt to.
"""


def create_server() -> FastMCP:
    """Build the FastMCP server with the tool surface for this process.

    ``REKAL_READONLY=1`` registers recall only and swaps in instructions with
    no store nudges — e.g. for measured benchmark runs, where a store attempt
    would burn a turn just to be denied.
    """
    readonly = os.environ.get("REKAL_READONLY") == "1"
    server = FastMCP(
        "rekal",
        instructions=RECALL_INSTRUCTIONS if readonly else INSTRUCTIONS,
        lifespan=lifespan,
    )
    register(server, readonly=readonly)
    return server
