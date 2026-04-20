"""Project scanning tool for codebase initialization."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from rekal.adapters.mcp_adapter import mcp
from rekal.scanner import scan_project

ScanMemoryDict = dict[str, str | list[str]]
ScanResponseValue = str | int | list[str] | list[ScanMemoryDict]


@mcp.tool()
async def project_scan(
    ctx: Context,
    directory: Annotated[
        str, Field(description="Absolute path to project root directory to scan")
    ],
) -> dict[str, ScanResponseValue]:
    """Scan a project codebase and return memory candidates for /rekal-init.

    Discovers Python packages, extracts classes/routes/models/exceptions/enums,
    finds config and doc files, and synthesizes ready-to-store memory candidates.

    Returns suggested_memories (store each via memory_store) and lists of
    doc/config/ADR files to read for additional memory extraction.
    """
    _ = ctx  # MCP framework requires ctx parameter
    result = scan_project(directory)

    return {
        "project_dir": result.project_dir,
        "total_files": result.total_files,
        "total_source_files": result.total_source_files,
        "top_level_dirs": result.top_level_dirs,
        "module_count": len(result.modules),
        "doc_files_to_read": result.doc_files,
        "adr_files_to_read": result.adr_files,
        "config_files_to_read": result.config_files,
        "suggested_memories": [
            {
                "content": m.content,
                "memory_type": m.memory_type,
                "tags": m.tags,
                "category": m.category,
            }
            for m in result.memories
        ],
        "instructions": (
            f"Found {len(result.memories)} memory candidates from code scanning. "
            f"1) Store each suggested_memory via memory_store (skip dedup on fresh DB). "
            f"2) Read each file in doc_files_to_read and adr_files_to_read — "
            f"extract additional memories from their content. "
            f"3) Read key config files for CI/build/deploy patterns. "
            f"Target: 40-80 total memories."
        ),
    }
