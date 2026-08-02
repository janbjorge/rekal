"""Shared lifespan state for the MCP server and its tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rekal.scoring import ScoringWeights

if TYPE_CHECKING:
    from rekal.adapters.sqlite_adapter import SqliteDatabase


@dataclass
class AppContext:
    db: SqliteDatabase | None
    default_project: str | None = None
    weights: ScoringWeights = field(default_factory=ScoringWeights)
