"""Tests for CLI entry point."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from rekal.__main__ import main, run_export, run_health
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


def test_main_export() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")

        async def setup() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            await db.close()

        asyncio.run(setup())

        with patch("sys.argv", ["rekal", "--db", db_path, "export"]):
            main()
