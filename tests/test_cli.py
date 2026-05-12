"""Tests for CLI entry point."""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from rekal.__main__ import (
    main,
    run_export,
    run_health,
    run_prune,
    run_scratch_capture,
)
from rekal.adapters.sqlite_adapter import SqliteDatabase

from .conftest import deterministic_embed


async def test_run_health(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.store("Test memory")
        await db.close()

        await run_health(db_path)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["total_memories"] == 1


async def test_run_health_no_db() -> None:
    with pytest.raises(SystemExit):
        await run_health("/nonexistent/path/db.sqlite")


async def test_run_export(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.store("Export me")
        await db.close()

        await run_export(db_path)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["content"] == "Export me"


async def test_run_export_no_db() -> None:
    with pytest.raises(SystemExit):
        await run_export("/nonexistent/path/db.sqlite")


def test_main_health() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")

        async def setup() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            await db.close()

        asyncio.run(setup())

        with patch("sys.argv", ["rekal", "--db", db_path, "health"]):
            main()


async def test_run_prune_no_db() -> None:
    with pytest.raises(SystemExit):
        await run_prune(
            "/nonexistent/path/db.sqlite",
            project="x",
            memory_type=None,
            older_than_days=None,
            before=None,
            yes=True,
        )


async def test_run_prune_requires_filter(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.close()

        with pytest.raises(SystemExit):
            await run_prune(
                db_path,
                project=None,
                memory_type=None,
                older_than_days=None,
                before=None,
                yes=False,
            )
        captured = capsys.readouterr()
        assert "Refusing to prune" in captured.out


async def test_run_prune_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        mid = await db.store("Drop", project="trash")
        await db.close()

        await run_prune(
            db_path,
            project="trash",
            memory_type=None,
            older_than_days=None,
            before=None,
            yes=False,
        )
        captured = capsys.readouterr()
        assert "Matched 1" in captured.out
        assert "Dry run only" in captured.out

        # Memory still present
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        try:
            assert await db.get(mid) is not None
        finally:
            await db.close()


async def test_run_prune_executes(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        mid = await db.store("Drop", project="trash")
        await db.close()

        await run_prune(
            db_path,
            project="trash",
            memory_type=None,
            older_than_days=7,
            before=None,
            yes=True,
        )
        captured = capsys.readouterr()
        assert "Matched" in captured.out

        db = await SqliteDatabase.create(db_path, deterministic_embed)
        try:
            # 7-day cutoff means just-stored memory is NOT older than 7 days,
            # so combined filter (project=trash AND before=now-7d) should not match.
            assert await db.get(mid) is not None
        finally:
            await db.close()


async def test_run_prune_executes_with_only_project(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        mid = await db.store("Drop", project="trash")
        await db.close()

        await run_prune(
            db_path,
            project="trash",
            memory_type=None,
            older_than_days=None,
            before=None,
            yes=True,
        )
        captured = capsys.readouterr()
        assert "Deleted 1" in captured.out

        db = await SqliteDatabase.create(db_path, deterministic_embed)
        try:
            assert await db.get(mid) is None
        finally:
            await db.close()


async def test_run_prune_by_memory_type(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.store("A fact", memory_type="fact")
        await db.close()

        await run_prune(
            db_path,
            project=None,
            memory_type="fact",
            older_than_days=None,
            before=None,
            yes=True,
        )
        captured = capsys.readouterr()
        assert "type=fact" in captured.out
        assert "Deleted 1" in captured.out


async def test_run_prune_no_matches(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.close()

        await run_prune(
            db_path,
            project="ghost",
            memory_type=None,
            older_than_days=None,
            before=None,
            yes=True,
        )
        captured = capsys.readouterr()
        assert "Matched 0" in captured.out


def test_main_prune() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")

        async def setup() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            await db.store("Drop", project="trash")
            await db.close()

        asyncio.run(setup())

        with patch("sys.argv", ["rekal", "--db", db_path, "prune", "--project", "trash", "--yes"]):
            main()


async def test_run_scratch_capture(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        await run_scratch_capture(
            db_path,
            content="Pre-compact snapshot body",
            project="alpha",
            tags=["pre-compact", "session-snapshot"],
            ttl_hours=168.0,
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert "id" in payload
        assert "expires_at" in payload

        db = await SqliteDatabase.create(db_path, deterministic_embed)
        try:
            stored = await db.get(payload["id"])
            assert stored is not None
            assert stored.tier == "scratch"
            assert stored.project == "alpha"
            assert "pre-compact" in stored.tags
        finally:
            await db.close()


def test_main_scratch_capture_stdin() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        with (
            patch("sys.argv", ["rekal", "--db", db_path, "scratch-capture", "--tag", "x"]),
            patch("sys.stdin", io.StringIO("body from stdin")),
        ):
            main()

        async def verify() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            try:
                rows = await db.memory_timeline(limit=10)
                assert any(m.content == "body from stdin" for m in rows)
            finally:
                await db.close()

        asyncio.run(verify())


def test_main_scratch_capture_with_content() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        with patch(
            "sys.argv",
            [
                "rekal",
                "--db",
                db_path,
                "scratch-capture",
                "--content",
                "explicit body",
                "--project",
                "alpha",
            ],
        ):
            main()

        async def verify() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            try:
                rows = await db.memory_timeline(limit=10)
                assert any(m.content == "explicit body" for m in rows)
            finally:
                await db.close()

        asyncio.run(verify())


def test_main_export() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")

        async def setup() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            await db.close()

        asyncio.run(setup())

        with patch("sys.argv", ["rekal", "--db", db_path, "export"]):
            main()
