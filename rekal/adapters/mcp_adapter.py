"""FastMCP server factory, lifespan, and DB initialization."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools import register

# Re-exported: path/config helpers live in rekal.config (MCP-free) so the
# recall CLI can use them without importing this module's FastMCP server.
from rekal.config import default_db_path, find_config_file, load_file_config, resolve_readonly
from rekal.embeddings import FastEmbedder
from rekal.scoring import ScoringWeights, resolve_weights

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass
class AppContext:
    db: SqliteDatabase | None
    default_project: str | None = None
    weights: ScoringWeights = field(default_factory=ScoringWeights)


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    """Open the DB for the server's lifetime; never fail the server on a bad DB.

    A lifespan crash is invisible to the model — every tool call just errors
    with no explanation. Degrading to ``db=None`` (tools answer "unavailable")
    keeps the session alive and the failure visible.
    """
    db_path = os.environ.get("REKAL_DB_PATH", default_db_path())
    default_project = os.environ.get("REKAL_PROJECT")
    readonly = resolve_readonly(db_path)
    if not readonly:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    embed = FastEmbedder()
    # Weights are fixed for the server's lifetime: resolve the config
    # file once here instead of on every recall.
    weights = resolve_weights(load_file_config(find_config_file()))
    try:
        db = await SqliteDatabase.create(
            db_path, embed, dimensions=embed.dimensions, readonly=readonly
        )
    except Exception as exc:  # any open failure degrades, never crashes
        print(f"rekal: cannot open {db_path}: {exc}", file=sys.stderr)
        yield AppContext(db=None, default_project=default_project, weights=weights)
        return
    try:
        yield AppContext(db=db, default_project=default_project, weights=weights)
    finally:
        await db.close()


INSTRUCTIONS = """\
rekal gives you persistent long-term memory across sessions. Relevant \
memories are injected automatically into your prompt under '## rekal \
memory'; call memory_build_context only when that block is missing and you \
need prior knowledge.

Store durable knowledge as it emerges (memory_store). Two shapes:

- Fact (default): one distilled, self-contained fact, 1-2 sentences. Drop \
filler; keep technical terms, values, and causality (X → Y).
- Brief: when you have mapped an entire subsystem or mechanism, store ONE \
structured summary (350-500 words): a headline line with 8-12 search \
keywords, the mechanism narrative, and a final "Deviations:" section for \
where this codebase differs from public/upstream behavior. Tag it "brief".

Any claim about code carries an inline anchor — (relative/path.py:LINE \
symbol) — with line numbers you verified this session. Anchored memories \
are trusted by future sessions; bare claims get re-verified, which wastes \
the memory. To update or correct an existing memory, pass replaces=<old_id> \
instead of storing a near-duplicate.

Do not store transient state, trivially re-discoverable facts, or secrets. \
Never write memories to files (CLAUDE.md, MEMORY.md); rekal is the only \
memory system.
"""

RECALL_INSTRUCTIONS = """\
rekal memory is read-only here. Relevant memories are already injected into \
your prompt under '## rekal memory' — trust them and build on them. \
memory_build_context is the ONLY rekal tool in this session (there is no \
memory_search and no memory_store); call it only if the injected block is \
missing and you need prior knowledge, and never attempt to store.
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
