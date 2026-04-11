"""Tests for embedding generation."""

from __future__ import annotations

import struct

from rekal.embeddings import FastEmbedder, bytes_to_floats, floats_to_bytes

from .conftest import EMBED_DIMENSIONS, deterministic_embed


def test_deterministic_embed_shape() -> None:
    result = deterministic_embed("test")
    assert len(result) == EMBED_DIMENSIONS * 4  # 384 floats * 4 bytes each


def test_deterministic_embed_deterministic() -> None:
    a = deterministic_embed("hello world")
    b = deterministic_embed("hello world")
    assert a == b


def test_deterministic_embed_different_inputs() -> None:
    a = deterministic_embed("hello")
    b = deterministic_embed("world")
    assert a != b


def test_deterministic_embed_normalized() -> None:
    data = deterministic_embed("test normalization")
    floats = struct.unpack(f"{EMBED_DIMENSIONS}f", data)
    norm = sum(f * f for f in floats) ** 0.5
    assert abs(norm - 1.0) < 0.01


def test_floats_to_bytes_roundtrip() -> None:
    original = [0.1, 0.2, 0.3, 0.4]
    encoded = floats_to_bytes(original)
    decoded = bytes_to_floats(encoded)
    assert len(decoded) == 4
    for a, b in zip(original, decoded, strict=True):
        assert abs(a - b) < 1e-6


def test_bytes_to_floats_empty() -> None:
    assert bytes_to_floats(b"") == []


def test_fast_embedder_dimensions() -> None:
    embedder = FastEmbedder()
    assert embedder.dimensions == 384
