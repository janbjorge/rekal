"""Tests for project scanning MCP tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rekal.adapters.mcp_adapter import AppContext
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.adapters.tools.scanner import project_scan


@dataclass
class FakeRequestContext:
    lifespan_context: AppContext


@dataclass
class FakeContext:
    request_context: FakeRequestContext


def _ctx(db: SqliteDatabase) -> FakeContext:
    return FakeContext(request_context=FakeRequestContext(lifespan_context=AppContext(db=db)))


async def test_project_scan_tool(db: SqliteDatabase, tmp_path: Path) -> None:
    # Create minimal project
    pkg = tmp_path / "mylib"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""My library."""\n')
    (pkg / "core.py").write_text("class Engine:\n    pass\n")
    (tmp_path / "README.md").write_text("# My Lib\n")

    result = await project_scan(_ctx(db), str(tmp_path))

    assert result["project_dir"] == str(tmp_path.resolve())
    assert isinstance(result["total_files"], int)
    assert isinstance(result["suggested_memories"], list)
    assert len(result["suggested_memories"]) > 0
    assert "README.md" in result["doc_files_to_read"]
    assert isinstance(result["instructions"], str)


async def test_project_scan_tool_empty_dir(db: SqliteDatabase, tmp_path: Path) -> None:
    result = await project_scan(_ctx(db), str(tmp_path))
    assert result["module_count"] == 0
    assert isinstance(result["suggested_memories"], list)
