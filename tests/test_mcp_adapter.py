"""Tests for MCP adapter lifespan."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from rekal.adapters.mcp_adapter import AppContext, lifespan, mcp


async def test_lifespan() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        os.environ["REKAL_DB_PATH"] = db_path
        try:
            async with lifespan(mcp) as ctx:
                assert isinstance(ctx, AppContext)
                assert ctx.db is not None
                # DB should be functional
                mid = await ctx.db.store("Test via lifespan")
                mem = await ctx.db.get(mid)
                assert mem is not None
        finally:
            os.environ.pop("REKAL_DB_PATH", None)
