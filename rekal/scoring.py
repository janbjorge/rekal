"""Hybrid scoring: normalize and combine FTS5/vector/recency scores."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RawScores:
    fts_score: float = 0.0
    vec_score: float = 0.0
    recency_days: float = 0.0


def normalize_fts(score: float) -> float:
    """Normalize FTS5 BM25 score to [0, 1]. FTS5 returns negative scores (lower = better)."""
    if score >= 0:
        return 0.0
    return 1.0 / (1.0 + math.exp(score))


def normalize_vec(distance: float) -> float:
    """Normalize vector cosine distance to similarity in [0, 1]."""
    return max(0.0, 1.0 - distance)


def normalize_recency(days: float, half_life: float = 30.0) -> float:
    """Exponential decay: recent memories score higher. Half-life in days."""
    return math.exp(-0.693 * days / half_life)


def combine_scores(
    raw: RawScores,
    *,
    w_fts: float = 0.4,
    w_vec: float = 0.4,
    w_recency: float = 0.2,
    half_life: float = 30.0,
) -> float:
    """Combine normalized scores into a single relevance score."""
    fts = normalize_fts(raw.fts_score)
    vec = normalize_vec(raw.vec_score)
    recency = normalize_recency(raw.recency_days, half_life)
    return w_fts * fts + w_vec * vec + w_recency * recency
