"""Tests for MCP adapter lifespan and server construction."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from rekal.adapters.mcp_adapter import (
    INSTRUCTIONS,
    RECALL_INSTRUCTIONS,
    AppContext,
    create_server,
    lifespan,
)


async def test_lifespan() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        os.environ["REKAL_DB_PATH"] = db_path
        try:
            async with lifespan(create_server()) as ctx:
                assert isinstance(ctx, AppContext)
                assert ctx.db is not None
                # DB should be functional
                mid = await ctx.db.store("Test via lifespan")
                mem = await ctx.db.get(mid)
                assert mem is not None
        finally:
            os.environ.pop("REKAL_DB_PATH", None)


async def test_create_server_full_surface() -> None:
    server = create_server()
    names = {t.name for t in await server.list_tools()}
    assert names == {"memory_build_context", "memory_store", "memory_delete"}
    assert server.instructions == INSTRUCTIONS


async def test_create_server_readonly() -> None:
    os.environ["REKAL_READONLY"] = "1"
    try:
        server = create_server()
    finally:
        os.environ.pop("REKAL_READONLY", None)
    names = {t.name for t in await server.list_tools()}
    assert names == {"memory_build_context"}
    assert server.instructions == RECALL_INSTRUCTIONS
