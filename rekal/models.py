"""Pydantic models for rekal tool inputs/outputs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CompactMemory(BaseModel):
    """Token-lean projection returned by the MCP tools: bookkeeping fields
    (timestamps beyond created_at, and friends) are dropped."""

    id: str
    content: str
    project: str | None = None
    tags: list[str] | None = None
    created_at: str | None = None
    score: float | None = None


class MemoryResult(BaseModel):
    id: str
    content: str
    project: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    score: float | None = None

    def compact(self) -> CompactMemory:
        return CompactMemory(
            id=self.id,
            content=self.content,
            project=self.project,
            tags=self.tags or None,
            created_at=self.created_at or None,
            score=round(self.score, 3) if self.score is not None else None,
        )


class HealthReport(BaseModel):
    total_memories: int
    oldest_memory: str | None = None
    newest_memory: str | None = None
    memories_by_project: dict[str, int] = Field(default_factory=dict)


class CompactContext(BaseModel):
    """Token-lean recall payload."""

    query: str
    memories: list[CompactMemory]


class ContextResult(BaseModel):
    query: str
    memories: list[MemoryResult]

    def compact(self) -> CompactContext:
        return CompactContext(
            query=self.query,
            memories=[m.compact() for m in self.memories],
        )
