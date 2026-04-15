"""FastMCP server, lifespan, and DB initialization."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, ValidationError

from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.embeddings import FastEmbedder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def default_db_path() -> str:
    return str(Path.home() / ".rekal" / "memory.db")


def find_config_file(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: CWD) looking for ``.rekal/config.yml``."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / ".rekal" / "config.yml"
        if candidate.is_file():
            return candidate
    return None


class FileScoring(BaseModel):
    w_fts: float | None = None
    w_vec: float | None = None
    w_recency: float | None = None
    half_life: float | None = None


class FileConfig(BaseModel):
    scoring: FileScoring = FileScoring()


def load_file_config(path: Path | None = None) -> dict[str, float]:
    """Load scoring weights from a ``.rekal/config.yml`` file.

    Uses pydantic to validate and coerce values. Returns only keys
    that were explicitly present in the YAML ``scoring:`` section.
    Returns ``{}`` on any parse/validation error or missing file.
    """
    if path is None:
        return {}
    with path.open() as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict) or not isinstance(raw.get("scoring"), dict):
        return {}
    try:
        parsed = FileConfig.model_validate(raw)
    except ValidationError:
        return {}
    # model_dump(exclude_unset=True) gives us only keys that were in the YAML.
    dumped = parsed.scoring.model_dump(exclude_unset=True)
    return {k: v for k, v in dumped.items() if v is not None}


@dataclass
class AppContext:
    db: SqliteDatabase
    default_project: str | None = None
    file_config: dict[str, float] = field(default_factory=dict)


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
    file_config = load_file_config(find_config_file())
    try:
        yield AppContext(db=db, default_project=default_project, file_config=file_config)
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
