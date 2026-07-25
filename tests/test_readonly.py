"""Readonly open, crash-proof lifespan, and injection-render tests.

The benchmark freezes its seed DB (chmod 0444) and measured runs set
REKAL_READONLY=1; both must open without a single write, old-schema files
must degrade instead of crash, and the injected block must stay within
budget with trust framing and no store-only noise.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import cast

import aiosqlite
import pytest
from mcp.server.fastmcp import Context

from rekal.__main__ import RECALL_BUDGET_CHARS, render_recall, run_recall
from rekal.adapters.mcp_adapter import AppContext, create_server, lifespan
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools.core import memory_build_context, memory_delete, memory_store
from rekal.config import resolve_readonly
from rekal.models import MemoryResult
from rekal.scoring import ScoringWeights

from .conftest import deterministic_embed
from .test_core_tools import FakeContext, FakeRequestContext


def none_ctx() -> Context:
    return cast(
        "Context",
        FakeContext(
            request_context=FakeRequestContext(
                lifespan_context=AppContext(db=None, weights=ScoringWeights())
            )
        ),
    )


async def make_frozen_db(tmp: str, *, legacy: bool = False) -> Path:
    """A frozen (0444) seed-style DB; legacy=True adds the old-schema marker
    column so the open path sees a pre-minimal shape."""
    path = Path(tmp) / "seed.db"
    db = await SqliteDatabase.create(str(path), deterministic_embed)
    await db.store("Ruff over Black for formatting", project="proj")
    await db.store("Postgres for relational data", project="proj")
    if legacy:
        # The migration detector keys on the memory_type column.
        await db.db.execute("ALTER TABLE memories ADD COLUMN memory_type TEXT DEFAULT 'fact'")
        await db.db.execute("ALTER TABLE memories ADD COLUMN tier TEXT DEFAULT 'durable'")
        await db.db.commit()
    await db.close()
    path.chmod(0o444)
    return path


# --- resolve_readonly -------------------------------------------------------


def test_resolve_readonly_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REKAL_READONLY", "1")
    assert resolve_readonly("/nonexistent/anything.db") is True


def test_resolve_readonly_unwritable_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "frozen.db"
        path.write_bytes(b"")
        path.chmod(0o444)
        assert resolve_readonly(str(path)) is True


def test_resolve_readonly_writable_and_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open.db"
        path.write_bytes(b"")
        assert resolve_readonly(str(path)) is False
        assert resolve_readonly(str(Path(tmp) / "missing.db")) is False


# --- readonly SQLite open ---------------------------------------------------


async def test_readonly_open_searches_without_writing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = await make_frozen_db(tmp)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        async with SqliteDatabase.session(str(path), deterministic_embed, readonly=True) as db:
            hits = await db.search("formatting", project="proj", limit=5, weights=ScoringWeights())
            assert any("Ruff" in m.content for m in hits)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before


async def test_readonly_open_legacy_schema_degraded() -> None:
    # Old-schema frozen DBs must still recall (degraded mode) — the exact file
    # shape that used to crash the server lifespan and silently blind recall.
    with tempfile.TemporaryDirectory() as tmp:
        path = await make_frozen_db(tmp, legacy=True)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        async with SqliteDatabase.session(str(path), deterministic_embed, readonly=True) as db:
            hits = await db.search(
                "relational data", project="proj", limit=5, weights=ScoringWeights()
            )
            assert any("Postgres" in m.content for m in hits)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before


async def test_writable_open_migrates_legacy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = await make_frozen_db(tmp, legacy=True)
        path.chmod(0o644)
        async with SqliteDatabase.session(str(path), deterministic_embed) as db:
            async with db.db.execute("PRAGMA table_info(memories)") as cursor:
                cols = {row[1] async for row in cursor}
            assert "memory_type" not in cols


async def test_readonly_memory_rejected() -> None:
    with pytest.raises(ValueError, match="memory"):
        await SqliteDatabase.create(":memory:", deterministic_embed, readonly=True)


async def test_readonly_missing_file_raises() -> None:
    with pytest.raises(aiosqlite.OperationalError):
        await SqliteDatabase.create("/nonexistent/nope.db", deterministic_embed, readonly=True)


async def test_health_works_on_frozen_db(capsys: pytest.CaptureFixture[str]) -> None:
    # `rekal health` (and export) must open frozen seed DBs read-only — the
    # benchmark's seed_status guard depends on it.
    from rekal.__main__ import run_health

    with tempfile.TemporaryDirectory() as tmp:
        path = await make_frozen_db(tmp, legacy=True)
        await run_health(str(path))
        report = json.loads(capsys.readouterr().out)
        assert report["total_memories"] == 2


async def test_readonly_store_fails() -> None:
    # PRAGMA query_only backs up mode=ro: writes are refused at the connection.
    with tempfile.TemporaryDirectory() as tmp:
        path = await make_frozen_db(tmp)
        async with SqliteDatabase.session(str(path), deterministic_embed, readonly=True) as db:
            with pytest.raises(aiosqlite.OperationalError):
                await db.store("should never land")


# --- crash-proof lifespan + tool degradation --------------------------------


async def test_lifespan_degrades_on_garbage_db(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "not.db"
        bad.write_text("this is not a sqlite database")
        monkeypatch.setenv("REKAL_DB_PATH", str(bad))
        async with lifespan(create_server()) as ctx:
            assert ctx.db is None
        assert "cannot open" in capsys.readouterr().err


async def test_lifespan_readonly_skips_mkdir(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = await make_frozen_db(tmp)
        monkeypatch.setenv("REKAL_DB_PATH", str(path))
        monkeypatch.setenv("REKAL_READONLY", "1")
        async with lifespan(create_server()) as ctx:
            assert ctx.db is not None
            hits = await ctx.db.search(
                "formatting", project="proj", limit=5, weights=ScoringWeights()
            )
            assert hits


async def test_tools_degrade_when_db_unavailable() -> None:
    ctx = none_ctx()
    context = await memory_build_context(ctx, "anything")
    assert context.memories == []
    assert "unavailable" in await memory_store(ctx, "fact")
    assert "unavailable" in await memory_delete(ctx, "someid")


# --- injection render -------------------------------------------------------


def mem(
    content: str, *, tags: list[str] | None = None, created_at: str = "2026-07-24"
) -> MemoryResult:
    return MemoryResult(
        id="abcd1234abcd1234",
        content=content,
        tags=tags or [],
        created_at=created_at,
    )


def test_render_readonly_drops_ids_and_dates_label() -> None:
    out = render_recall(
        [mem("Anchored fact (rekal/config.py:16 default_db_path)", tags=["gotcha"])],
        project="proj",
        fmt="text",
        readonly=True,
    )
    assert out.startswith("## rekal memory (project: proj)\n")
    assert "cite anchors" in out  # trust header
    assert "### [gotcha] (as of 2026-07-24)\n" in out
    assert "(id " not in out


def test_render_write_mode_keeps_ids() -> None:
    out = render_recall([mem("A fact")], project=None, fmt="text", readonly=False)
    assert "### [memory] (as of 2026-07-24) (id abcd1234abcd1234)" in out


def test_render_no_created_at_omits_as_of() -> None:
    out = render_recall([mem("A fact", created_at="")], project=None, fmt="text", readonly=True)
    assert "(as of" not in out


def test_render_budget_drops_overflow_but_keeps_top_hit() -> None:
    big = mem("x" * (RECALL_BUDGET_CHARS + 100), tags=["brief"])
    small = mem("small fact", tags=["gotcha"])
    out = render_recall([big, small], project=None, fmt="text", readonly=True)
    # Top hit always ships even over budget; the next memory is dropped whole.
    assert "x" * 50 in out
    assert "small fact" not in out


def test_render_golden_payload() -> None:
    # Golden file for the exact injected shape: a format regression here
    # silently changes what every warm run sees.
    out = render_recall(
        [mem("Dep cache key is (models.py:63 cache_key).", tags=["dependency-injection"])],
        project="fastapi",
        fmt="text",
        readonly=True,
    )
    expected = (
        "## rekal memory (project: fastapi)\n"
        "Facts below were verified against this codebase when learned; file:line "
        "anchors mark where. Build on them directly — cite anchors instead of "
        "re-opening files to re-check what a memory already states. Read code "
        "only for what these do not cover.\n"
        "\n"
        "### [dependency-injection] (as of 2026-07-24)\n"
        "Dep cache key is (models.py:63 cache_key)."
    )
    assert out == expected


async def test_run_recall_readonly_env_suppresses_ids(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = await make_frozen_db(tmp)
        monkeypatch.setenv("REKAL_READONLY", "1")
        await run_recall(str(path), project="proj", query=None, limit=10, fmt="text")
        out = capsys.readouterr().out
        assert "Ruff over Black" in out
        assert "(id " not in out
