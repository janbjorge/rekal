"""Test fixtures: in-memory DB with schema, deterministic test embeddings."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import AsyncIterator

import pytest

from rekal.adapters.sqlite_adapter import SqliteDatabase

EMBED_DIMENSIONS = 384


def deterministic_embed(text: str) -> bytes:
    """Generate a deterministic 384-dim embedding from text.

    Not semantically meaningful, but fast, deterministic, and correct shape.
    Uses SHA-512 repeated to fill 384 floats in [-1, 1].
    """
    h = hashlib.sha512(text.encode()).digest()
    # Extend hash to fill 384 floats (need 384 * 4 = 1536 bytes, sha512 = 64 bytes)
    extended = b""
    for i in range(24):  # 24 * 64 = 1536 bytes
        extended += hashlib.sha512(h + i.to_bytes(4, "little")).digest()
    # Convert to floats in [-1, 1]
    raw_ints = struct.unpack("384I", extended[: 384 * 4])
    floats = [(x / (2**32 - 1)) * 2 - 1 for x in raw_ints]
    # Normalize to unit length
    norm = sum(f * f for f in floats) ** 0.5
    floats = [f / norm for f in floats]
    return struct.pack(f"{EMBED_DIMENSIONS}f", *floats)


@pytest.fixture
async def db() -> AsyncIterator[SqliteDatabase]:
    """Provide a fully initialized in-memory SqliteDatabase."""
    instance = await SqliteDatabase.create(":memory:", deterministic_embed)
    try:
        yield instance
    finally:
        await instance.close()
