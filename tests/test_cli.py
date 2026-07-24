"""Tests for CLI entry point."""

from __future__ import annotations

import asyncio
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
    run_recall,
)
from rekal.adapters.sqlite_adapter import SqliteDatabase

from .conftest import deterministic_embed

# recall/open_db build their own FastEmbedder (lazily imported from
# rekal.embeddings); swap it for the deterministic test embedder so the query
# path never loads (or downloads) the real ONNX model.
patch_embedder = patch("rekal.embeddings.FastEmbedder", lambda: deterministic_embed)


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


async def test_run_recall_timeline_json(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.store("First fact")
        await db.store("Second fact")
        await db.close()

        await run_recall(db_path, project=None, query=None, limit=10, fmt="json")
        data = json.loads(capsys.readouterr().out)
        contents = {m["content"] for m in data}
        assert contents == {"First fact", "Second fact"}


async def test_run_recall_timeline_text(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.store("A durable fact")
        await db.close()

        await run_recall(db_path, project=None, query=None, limit=10, fmt="text")
        out = capsys.readouterr().out
        assert out.startswith("## rekal memory\n")
        assert "- A durable fact (id " in out


async def test_run_recall_query(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.store("Ruff over Black for formatting")
        await db.close()

        with patch_embedder:
            await run_recall(db_path, project=None, query="formatting", limit=5, fmt="json")
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)
        assert any(m["content"] == "Ruff over Black for formatting" for m in data)


async def test_run_recall_project_scope(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.store("Scoped fact", project="acme")
        await db.store("Other fact", project="other")
        await db.close()

        await run_recall(db_path, project="acme", query=None, limit=10, fmt="text")
        out = capsys.readouterr().out
        assert out.startswith("## rekal memory (project: acme)\n")
        assert "Scoped fact" in out
        assert "Other fact" not in out


async def test_run_recall_empty_db_text(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.close()

        await run_recall(db_path, project=None, query=None, limit=10, fmt="text")
        assert capsys.readouterr().out == ""


async def test_run_recall_empty_db_json(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.close()

        await run_recall(db_path, project=None, query=None, limit=10, fmt="json")
        assert json.loads(capsys.readouterr().out) == []


async def test_run_recall_missing_db_text(capsys: pytest.CaptureFixture[str]) -> None:
    # Missing DB must NOT raise (unlike health/export) — recall degrades silently.
    await run_recall("/nonexistent/db.sqlite", project=None, query=None, limit=10, fmt="text")
    assert capsys.readouterr().out == ""


async def test_run_recall_missing_db_json(capsys: pytest.CaptureFixture[str]) -> None:
    await run_recall("/nonexistent/db.sqlite", project=None, query=None, limit=10, fmt="json")
    assert json.loads(capsys.readouterr().out) == []


def test_main_recall(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")

        async def setup() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            await db.store("Env-scoped fact", project="fromenv")
            await db.close()

        asyncio.run(setup())

        # No --project flag → falls back to $REKAL_PROJECT.
        monkeypatch.setenv("REKAL_PROJECT", "fromenv")
        with (
            patch_embedder,
            patch("sys.argv", ["rekal", "--db", db_path, "recall", "--query", "fact"]),
            pytest.raises(SystemExit) as e,
        ):
            main()
        assert e.value.code in (0, None)


def test_main_recall_leading_dash_query() -> None:
    # `--query=-x` must parse as a value, not be mistaken for a flag.
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")

        async def setup() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            await db.close()

        asyncio.run(setup())

        with (
            patch_embedder,
            patch("sys.argv", ["rekal", "--db", db_path, "recall", "--query=-dashy"]),
            pytest.raises(SystemExit) as e,
        ):
            main()
        assert e.value.code in (0, None)


def test_main_health() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")

        async def setup() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            await db.close()

        asyncio.run(setup())

        with (
            patch("sys.argv", ["rekal", "--db", db_path, "health"]),
            pytest.raises(SystemExit) as e,
        ):
            main()
        assert e.value.code in (0, None)


async def test_run_prune_no_db() -> None:
    with pytest.raises(SystemExit):
        await run_prune(
            "/nonexistent/path/db.sqlite",
            project="x",
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


async def test_run_prune_no_matches(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = await SqliteDatabase.create(db_path, deterministic_embed)
        await db.close()

        await run_prune(
            db_path,
            project="ghost",
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

        with (
            patch("sys.argv", ["rekal", "--db", db_path, "prune", "--project", "trash", "--yes"]),
            pytest.raises(SystemExit) as e,
        ):
            main()
        assert e.value.code in (0, None)


def test_main_export() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")

        async def setup() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            await db.close()

        asyncio.run(setup())

        with (
            patch("sys.argv", ["rekal", "--db", db_path, "export"]),
            pytest.raises(SystemExit) as e,
        ):
            main()
        assert e.value.code in (0, None)
