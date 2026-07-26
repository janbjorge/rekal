"""Tests for .rekal/config.yml discovery and loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from rekal.adapters.mcp_adapter import find_config_file, load_file_config
from rekal.config import resolve_min_relevance
from rekal.scoring import resolve_weights

# ── find_config_file ─────────────────────────────────────────────────


def test_find_config_file_found() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        cfg = root / ".rekal" / "config.yml"
        cfg.parent.mkdir()
        cfg.write_text("scoring:\n  w_fts: 0.6\n")
        assert find_config_file(root) == cfg


def test_find_config_file_not_found() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert find_config_file(Path(tmp)) is None


# ── load_file_config ─────────────────────────────────────────────────


def test_load_file_config_valid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("scoring:\n  w_fts: 0.6\n  w_vec: 0.3\n  w_recency: 0.1\n  half_life: 14\n")
        result = load_file_config(cfg)
        assert result == {"w_fts": 0.6, "w_vec": 0.3, "w_recency": 0.1, "half_life": 14.0}


def test_load_file_config_none_path() -> None:
    assert load_file_config(None) == {}


def test_load_file_config_no_scoring_section() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("other_key: value\n")
        assert load_file_config(cfg) == {}


def test_load_file_config_rejects_non_numeric_values() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("scoring:\n  w_fts: not_a_number\n  w_vec: 0.3\n")
        assert load_file_config(cfg) == {}


# ── resolve_weights with file_config ─────────────────────────────────


def test_resolve_weights_file_config_used() -> None:
    weights = resolve_weights({"w_fts": 0.6, "half_life": 7.0})
    assert weights.w_fts == 0.6
    assert weights.half_life == 7.0
    assert weights.w_vec == 0.4  # default fills the gap
    assert weights.w_recency == 0.2


def test_resolve_weights_defaults() -> None:
    weights = resolve_weights(None)
    assert weights.w_fts == 0.4
    assert weights.w_vec == 0.4
    assert weights.w_recency == 0.2
    assert weights.half_life == 30.0


# ── resolve_min_relevance ────────────────────────────────────────────


def test_resolve_min_relevance_default_off() -> None:
    assert resolve_min_relevance() == 0.0
    assert resolve_min_relevance({}) == 0.0


def test_resolve_min_relevance_from_file_config() -> None:
    assert resolve_min_relevance({"min_relevance": 0.17}) == 0.17


def test_resolve_min_relevance_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REKAL_MIN_RELEVANCE", "0.3")
    assert resolve_min_relevance({"min_relevance": 0.17}) == 0.3


def test_resolve_min_relevance_bad_env_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REKAL_MIN_RELEVANCE", "not-a-float")
    assert resolve_min_relevance({"min_relevance": 0.17}) == 0.17


def test_load_file_config_min_relevance_key() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("scoring:\n  min_relevance: 0.2\n")
        loaded = load_file_config(cfg)
        assert loaded["min_relevance"] == 0.2
        # The scoring-weights model ignores the extra key.
        assert resolve_weights(loaded).w_fts == 0.4
