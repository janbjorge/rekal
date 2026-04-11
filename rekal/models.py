"""Pydantic models for rekal tool inputs/outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MemoryType = Literal["fact", "preference", "procedure", "context", "episode"]
ConversationRelation = Literal["follows_up_on", "branches_from", "contradicts", "merges"]
MemoryRelation = Literal["supersedes", "contradicts", "related_to"]


class MemoryResult(BaseModel):
    id: str
    content: str
    memory_type: MemoryType
    project: str | None = None
    conversation_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    access_count: int = 0
    last_accessed_at: str | None = None
    score: float | None = None


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
    conflicts: list[ConflictInfo]
    timeline_summary: str


class StaleConversation(BaseModel):
    id: str
    title: str | None = None
    project: str | None = None
    started_at: str
    last_memory_at: str | None = None
    days_inactive: int
