"""Tests for hybrid search scoring, ranking correctness."""

from __future__ import annotations

from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.scoring import ScoringWeights


async def test_search_basic(db: SqliteDatabase) -> None:
    await db.store("Python is a programming language", project="test")
    await db.store("Rust is a systems language", project="test")
    await db.store("Cooking pasta is easy", project="test")

    results = await db.search("programming language", project="test", weights=ScoringWeights())
    assert len(results) > 0
    # FTS should rank programming language matches higher
    assert any("programming" in r.content.lower() for r in results)


async def test_search_with_project_filter(db: SqliteDatabase) -> None:
    await db.store("Project A memory", project="alpha")
    await db.store("Project B memory", project="beta")

    results = await db.search("memory", project="alpha", weights=ScoringWeights())
    assert all(r.project == "alpha" for r in results)


async def test_search_with_type_filter(db: SqliteDatabase) -> None:
    await db.store("A fact", memory_type="fact")
    await db.store("A preference", memory_type="preference")

    results = await db.search("fact preference", memory_type="fact", weights=ScoringWeights())
    assert all(r.memory_type == "fact" for r in results)


async def test_search_with_conversation_filter(db: SqliteDatabase) -> None:
    conv = await db.conversation_start(title="Test")
    await db.store("In conversation", conversation_id=conv)
    await db.store("No conversation")

    results = await db.search("conversation", conversation_id=conv, weights=ScoringWeights())
    assert all(r.conversation_id == conv for r in results)


async def test_search_limit(db: SqliteDatabase) -> None:
    for i in range(20):
        await db.store(f"Memory number {i} about testing")

    results = await db.search("testing", limit=5, weights=ScoringWeights())
    assert len(results) <= 5


async def test_search_no_results(db: SqliteDatabase) -> None:
    results = await db.search("xyzzy nonexistent query", weights=ScoringWeights())
    assert results == []


async def test_search_updates_access_count(db: SqliteDatabase) -> None:
    mid = await db.store("Accessed memory about databases")
    await db.search("databases", weights=ScoringWeights())

    mem = await db.get(mid)
    assert mem is not None
    assert mem.access_count >= 1
    assert mem.last_accessed_at is not None


async def test_search_scores_present(db: SqliteDatabase) -> None:
    await db.store("Scoring test memory content")
    results = await db.search("scoring test", weights=ScoringWeights())
    for r in results:
        assert r.score is not None
        assert r.score > 0


async def test_search_custom_weights(db: SqliteDatabase) -> None:
    await db.store("Custom weight test memory")
    r1 = await db.search(
        "custom weight test", weights=ScoringWeights(w_fts=0.8, w_vec=0.1, w_recency=0.1)
    )
    r2 = await db.search(
        "custom weight test", weights=ScoringWeights(w_fts=0.1, w_vec=0.8, w_recency=0.1)
    )
    assert len(r1) > 0
    assert len(r2) > 0
    # Different weights should produce different scores
    assert r1[0].score != r2[0].score


async def test_search_custom_half_life(db: SqliteDatabase) -> None:
    await db.store("Half life test memory")
    r_short = await db.search("half life test", weights=ScoringWeights(half_life=1.0))
    r_long = await db.search("half life test", weights=ScoringWeights(half_life=365.0))
    assert len(r_short) > 0
    assert len(r_long) > 0
    # Longer half-life = higher recency component = higher total score
    assert r_long[0].score is not None
    assert r_short[0].score is not None
    assert r_long[0].score >= r_short[0].score


async def test_search_uses_project_config(db: SqliteDatabase) -> None:
    await db.set_config("proj", "w_fts", "0.8")
    await db.set_config("proj", "w_vec", "0.1")
    await db.set_config("proj", "w_recency", "0.1")
    await db.store("Project config weight test", project="proj")

    proj_weights = await db.resolve_weights("proj")
    r_config = await db.search("project config weight", project="proj", weights=proj_weights)
    r_default = await db.search("project config weight", project="proj", weights=ScoringWeights())
    assert len(r_config) > 0
    assert len(r_default) > 0
    # Project config should produce different scores than defaults
    assert r_config[0].score != r_default[0].score


async def test_search_min_score_excludes_low_scores(db: SqliteDatabase) -> None:
    await db.store("Relevance floor memory about compilers")
    # Every component normalizes to [0,1] and fts sigmoid stays below 1, so a
    # floor of 1.0 excludes everything while 0.0 keeps all candidates.
    all_hits = await db.search("compilers", weights=ScoringWeights(), min_score=0.0)
    no_hits = await db.search("compilers", weights=ScoringWeights(), min_score=1.0)
    assert len(all_hits) > 0
    assert no_hits == []


async def test_search_min_score_boundary_inclusive(db: SqliteDatabase) -> None:
    await db.store("Boundary floor memory about linkers")
    hits = await db.search("linkers", weights=ScoringWeights(), min_score=0.0)
    assert len(hits) > 0
    score = hits[0].score
    assert score is not None
    # A result scoring exactly min_score survives (strict < comparison).
    again = await db.search("linkers", weights=ScoringWeights(), min_score=score)
    assert [r.id for r in again] == [r.id for r in hits]


async def test_build_context_min_score(db: SqliteDatabase) -> None:
    await db.store("Context floor memory about parsers")
    kept = await db.build_context("parsers", weights=ScoringWeights(), min_score=0.0)
    dropped = await db.build_context("parsers", weights=ScoringWeights(), min_score=1.0)
    assert len(kept.memories) > 0
    assert dropped.memories == []


async def test_search_per_call_overrides_project_config(db: SqliteDatabase) -> None:
    await db.set_config("proj", "w_fts", "0.8")
    await db.store("Override test memory", project="proj")

    proj_weights = await db.resolve_weights("proj")
    r_config = await db.search("override test", project="proj", weights=proj_weights)
    r_override = await db.search(
        "override test", project="proj", weights=ScoringWeights(w_fts=0.1)
    )
    assert len(r_config) > 0
    assert len(r_override) > 0
    assert r_config[0].score != r_override[0].score
