"""Tests for score normalization and combination."""

from __future__ import annotations

from rekal.scoring import (
    RawScores,
    combine_scores,
    normalize_fts,
    normalize_recency,
    normalize_vec,
)


def test_normalize_fts_negative() -> None:
    score = normalize_fts(-5.0)
    assert 0 < score < 1


def test_normalize_fts_zero() -> None:
    assert normalize_fts(0.0) == 0.0


def test_normalize_fts_positive() -> None:
    assert normalize_fts(1.0) == 0.0


def test_normalize_fts_more_negative_is_better() -> None:
    assert normalize_fts(-10.0) > normalize_fts(-1.0)


def test_normalize_vec_zero_distance() -> None:
    assert normalize_vec(0.0) == 1.0


def test_normalize_vec_full_distance() -> None:
    assert normalize_vec(1.0) == 0.0


def test_normalize_vec_over_distance() -> None:
    assert normalize_vec(1.5) == 0.0


def test_normalize_vec_partial() -> None:
    score = normalize_vec(0.3)
    assert 0.6 < score < 0.8


def test_normalize_recency_zero_days() -> None:
    score = normalize_recency(0.0)
    assert score == 1.0


def test_normalize_recency_half_life() -> None:
    score = normalize_recency(30.0, half_life=30.0)
    assert abs(score - 0.5) < 0.01


def test_normalize_recency_old() -> None:
    score = normalize_recency(365.0)
    assert score < 0.01


def test_combine_scores_all_perfect() -> None:
    raw = RawScores(fts_score=-20.0, vec_score=0.0, recency_days=0.0)
    score = combine_scores(raw)
    assert score > 0.8


def test_combine_scores_all_bad() -> None:
    raw = RawScores(fts_score=0.0, vec_score=1.0, recency_days=365.0)
    score = combine_scores(raw)
    assert score < 0.1


def test_combine_scores_custom_weights() -> None:
    raw = RawScores(fts_score=-5.0, vec_score=0.2, recency_days=10.0)
    score1 = combine_scores(raw, w_fts=0.8, w_vec=0.1, w_recency=0.1)
    score2 = combine_scores(raw, w_fts=0.1, w_vec=0.8, w_recency=0.1)
    # Different weights should give different scores
    assert score1 != score2
