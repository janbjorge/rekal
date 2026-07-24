"""Pydantic models for rekal tool inputs/outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MemoryType = Literal["fact", "preference", "procedure", "context", "episode"]
MemoryTier = Literal["durable", "scratch"]
ConversationRelation = Literal["follows_up_on", "branches_from", "contradicts", "merges"]
MemoryRelation = Literal["supersedes", "contradicts", "related_to"]


class CompactMemory(BaseModel):
    """Token-lean projection returned by the MCP tools: bookkeeping fields
    (tier, timestamps beyond created_at, access counters) are dropped."""

    id: str
    content: str
    memory_type: MemoryType
    project: str | None = None
    tags: list[str] | None = None
    created_at: str | None = None
    score: float | None = None


class MemoryResult(BaseModel):
    id: str
    content: str
    memory_type: MemoryType
    tier: MemoryTier = "durable"
    project: str | None = None
    conversation_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    expires_at: str | None = None
    access_count: int = 0
    last_accessed_at: str | None = None
    score: float | None = None

    def compact(self) -> CompactMemory:
        return CompactMemory(
            id=self.id,
            content=self.content,
            memory_type=self.memory_type,
            project=self.project,
            tags=self.tags or None,
            created_at=self.created_at or None,
            score=round(self.score, 3) if self.score is not None else None,
        )


class TopicSummary(BaseModel):
    topic: str
    count: int
    latest: str


class HealthReport(BaseModel):
    total_memories: int
    total_conversations: int
    total_links: int
    total_conflicts: int
    oldest_memory: str | None = None
    newest_memory: str | None = None
    memories_by_type: dict[str, int] = Field(default_factory=dict)
    memories_by_project: dict[str, int] = Field(default_factory=dict)


class ConflictInfo(BaseModel):
    memory_id: str
    content: str
    related_id: str
    related_content: str
    relation: str
    created_at: str


class ConversationInfo(BaseModel):
    id: str
    title: str | None = None
    project: str | None = None
    started_at: str = ""
    memory_count: int = 0


class ConversationLink(BaseModel):
    from_id: str
    to_id: str
    relation: ConversationRelation
    created_at: str


class CompactContext(BaseModel):
    """Token-lean recall payload; empty tiers/conflicts collapse to None."""

    query: str
    memories: list[CompactMemory]
    timeline_summary: str
    scratch: list[CompactMemory] | None = None
    conflicts: list[ConflictInfo] | None = None


class ContextResult(BaseModel):
    query: str
    memories: list[MemoryResult]
    scratch: list[MemoryResult] = Field(default_factory=list)
    conflicts: list[ConflictInfo]
    timeline_summary: str

    def compact(self) -> CompactContext:
        return CompactContext(
            query=self.query,
            memories=[m.compact() for m in self.memories],
            timeline_summary=self.timeline_summary,
            scratch=[m.compact() for m in self.scratch] or None,
            conflicts=self.conflicts or None,
        )


class StaleConversation(BaseModel):
    id: str
    title: str | None = None
    project: str | None = None
    started_at: str
    last_memory_at: str | None = None
    days_inactive: int
