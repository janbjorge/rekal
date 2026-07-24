"""Pydantic models for rekal tool inputs/outputs."""

from __future__ import annotations

from typing import Literal, Required, TypedDict

from pydantic import BaseModel, Field

MemoryType = Literal["fact", "preference", "procedure", "context", "episode"]
MemoryTier = Literal["durable", "scratch"]
ConversationRelation = Literal["follows_up_on", "branches_from", "contradicts", "merges"]
MemoryRelation = Literal["supersedes", "contradicts", "related_to"]

# Token-lean projections returned by the MCP tools: bookkeeping fields
# (tier, timestamps beyond created_at, access counters) are dropped and
# absent values are omitted instead of serialized as null.


class CompactMemory(TypedDict, total=False):
    id: Required[str]
    content: Required[str]
    memory_type: Required[MemoryType]
    project: str
    tags: list[str]
    created_at: str
    score: float


class CompactContext(TypedDict, total=False):
    query: Required[str]
    memories: Required[list[CompactMemory]]
    timeline_summary: Required[str]
    scratch: list[CompactMemory]
    conflicts: list[dict[str, str]]


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
        out: CompactMemory = {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type,
        }
        if self.project:
            out["project"] = self.project
        if self.tags:
            out["tags"] = self.tags
        if self.created_at:
            out["created_at"] = self.created_at
        if self.score is not None:
            out["score"] = round(self.score, 3)
        return out


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


class ContextResult(BaseModel):
    query: str
    memories: list[MemoryResult]
    scratch: list[MemoryResult] = Field(default_factory=list)
    conflicts: list[ConflictInfo]
    timeline_summary: str

    def compact(self) -> CompactContext:
        out: CompactContext = {
            "query": self.query,
            "memories": [m.compact() for m in self.memories],
            "timeline_summary": self.timeline_summary,
        }
        if self.scratch:
            out["scratch"] = [m.compact() for m in self.scratch]
        if self.conflicts:
            out["conflicts"] = [c.model_dump() for c in self.conflicts]
        return out


class StaleConversation(BaseModel):
    id: str
    title: str | None = None
    project: str | None = None
    started_at: str
    last_memory_at: str | None = None
    days_inactive: int
