"""Filesystem paths and ``.rekal/config.yml`` loading, with no MCP dependencies.

Kept free of the FastMCP server so lightweight entry points (notably the
``rekal recall`` CLI on the per-turn hook hot path) can resolve the DB path
and scoring config without importing or constructing the MCP server.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError


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
