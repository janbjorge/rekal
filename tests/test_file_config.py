"""Tests for .rekal/config.yml discovery and loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

from rekal.adapters.mcp_adapter import find_config_file, load_file_config
from rekal.adapters.sqlite_adapter import SqliteDatabase

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


async def test_resolve_weights_file_config_used(db: SqliteDatabase) -> None:
    file_cfg = {"w_fts": 0.6, "half_life": 7.0}
    weights = await db.resolve_weights(None, file_config=file_cfg)
    assert weights.w_fts == 0.6
    assert weights.half_life == 7.0
    assert weights.w_vec == 0.4
    assert weights.w_recency == 0.2


async def test_resolve_weights_full_precedence_chain(db: SqliteDatabase) -> None:
    """All four layers: per-call > DB > file > hardcoded defaults."""
    await db.set_config("proj", "w_vec", "0.9")
    file_cfg = {"w_fts": 0.6, "w_vec": 0.5, "half_life": 7.0}
    weights = await db.resolve_weights("proj", w_fts=0.1, file_config=file_cfg)
    assert weights.w_fts == 0.1  # per-call
    assert weights.w_vec == 0.9  # DB beats file
    assert weights.half_life == 7.0  # file
    assert weights.w_recency == 0.2  # hardcoded default
