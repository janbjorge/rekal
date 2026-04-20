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
    """Look for ``.rekal/config.yml`` in *start* (default: CWD)."""
    candidate = (start or Path.cwd()).resolve() / ".rekal" / "config.yml"
    return candidate if candidate.is_file() else None


class FileScoring(BaseModel):
    w_fts: float | None = None
    w_vec: float | None = None
    w_recency: float | None = None
    half_life: float | None = None


class FileConfig(BaseModel):
    scoring: FileScoring = FileScoring()


def load_file_config(path: Path | None = None) -> dict[str, float]:
    """Load scoring weights from ``.rekal/config.yml``. Returns ``{}`` on any error."""
    if path is None:
        return {}
    try:
        raw = yaml.safe_load(path.read_text())
        parsed = FileConfig.model_validate(raw)
    except (ValidationError, yaml.YAMLError, OSError, TypeError):
        return {}
    return parsed.scoring.model_dump(exclude_unset=True, exclude_none=True)


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

## Session start — do this first

Call memory_build_context with your current task before exploring the codebase. \
This loads relevant prior knowledge, conflicts, and timeline in one call.

## Storing memories

Store durable knowledge as you work — do not batch until session end:

- User states a preference or corrects you → store immediately
- Non-obvious architectural fact discovered → store
- Debugging reveals a surprising root cause → store
- User describes a workflow or procedure → store
- Decision made with reasoning → store

### Distill before storing — mandatory

NEVER store raw dialogue, conversation turns, or verbose text. \
Extract the durable fact, then compress:

- Drop: articles (a/an/the), filler (just/really/basically/actually/simply), \
pleasantries, hedging (might/could/maybe)
- Replace verbose phrases: "in order to" → "to", "make sure to" → ensure
- Use short synonyms: big not extensive, fix not "implement a solution for"
- Fragments OK. State actions directly — no "you should", "remember to"
- Merge redundant points that say the same thing differently
- Keep exact: technical terms, proper nouns, version numbers, values, causality (X because Y)

One memory = one distilled fact. 1-2 sentences max.

```
BAD:  "User said yeah I think maybe we could try using Python for this project"
GOOD: "Prefer Python for project"

BAD:  "So we eventually decided to use Postgres because the team knows it and data is relational"
GOOD: "DB: Postgres. Team familiar, data relational."
```

ALWAYS call memory_search before storing. If the same topic exists, \
call memory_supersede instead of creating a duplicate. Two memories about \
the same topic must never coexist.

### memory_store parameters

- content: Self-contained distilled fact. A fresh agent with zero context must understand it.
- memory_type: One of fact, preference, procedure, context, episode.
- tags: 2-4 specific tags. Not "code" or "project".
- project: Set if project-specific. Omit for global knowledge.

### memory_supersede

Call with old_id from search results and new_content. Preserves history via links. \
Use supersede, not delete + store.

## What NOT to store

- Transient state ("currently editing X", "tests passing")
- Trivially re-discoverable facts (line numbers, file lengths)
- Vague platitudes ("user likes clean code")
- Secrets, API keys, passwords, tokens — never

## This is your ONLY memory system

Do NOT write memories to CLAUDE.md, MEMORY.md, or any markdown file. \
All persistent knowledge goes through rekal tools exclusively.
"""

mcp = FastMCP("rekal", instructions=INSTRUCTIONS, lifespan=lifespan)

import rekal.adapters.tools  # noqa: E402, F401
