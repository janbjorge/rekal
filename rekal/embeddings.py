"""Embedding generation via fastembed (ONNX-based, lazy-loaded)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastembed import TextEmbedding


class EmbeddingFunc(Protocol):
    def __call__(self, text: str) -> bytes: ...


@dataclass
class FastEmbedder:
    """Lazy-loaded fastembed wrapper. Downloads model on first use."""

    model_name: str = "BAAI/bge-small-en-v1.5"
    dimensions: int = 384
    model: TextEmbedding | None = field(default=None, init=False, repr=False)

    def ensure_model(self) -> None:
        if self.model is None:
            from fastembed import TextEmbedding

            self.model = TextEmbedding(model_name=self.model_name)

    def __call__(self, text: str) -> bytes:
        self.ensure_model()
        assert self.model is not None
        embeddings = list(self.model.embed([text]))
        vec = embeddings[0].tolist()
        return struct.pack(f"{len(vec)}f", *vec)


def floats_to_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def bytes_to_floats(data: bytes) -> list[float]:
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))
