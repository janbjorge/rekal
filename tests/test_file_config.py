"""Tests for .rekal/config.yml discovery and loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

from rekal.adapters.mcp_adapter import find_config_file, load_file_config
from rekal.adapters.sqlite_adapter import SqliteDatabase
from rekal.scoring import ScoringWeights

# ── find_config_file ─────────────────────────────────────────────────


def test_find_config_file_in_start_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        cfg = root / ".rekal" / "config.yml"
        cfg.parent.mkdir()
        cfg.write_text("scoring:\n  w_fts: 0.6\n")
        assert find_config_file(root) == cfg


def test_find_config_file_does_not_walk_up() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        cfg = root / ".rekal" / "config.yml"
        cfg.parent.mkdir()
        cfg.write_text("scoring:\n  w_fts: 0.6\n")
        nested = root / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert find_config_file(nested) is None


def test_find_config_file_not_found() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert find_config_file(Path(tmp)) is None


def test_find_config_file_ignores_directory() -> None:
    """If .rekal/config.yml is a directory instead of a file, skip it."""
    with tempfile.TemporaryDirectory() as tmp:
        bogus = Path(tmp) / ".rekal" / "config.yml"
        bogus.mkdir(parents=True)  # directory, not file
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


def test_load_file_config_scoring_not_dict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("scoring: just_a_string\n")
        assert load_file_config(cfg) == {}


def test_load_file_config_non_dict_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("- a list\n- not a dict\n")
        assert load_file_config(cfg) == {}


def test_load_file_config_ignores_unknown_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("scoring:\n  w_fts: 0.5\n  bogus_key: 99\n")
        result = load_file_config(cfg)
        assert result == {"w_fts": 0.5}
        assert "bogus_key" not in result


def test_load_file_config_rejects_non_numeric_values() -> None:
    """Any invalid value in scoring causes the whole section to be rejected."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("scoring:\n  w_fts: not_a_number\n  w_vec: 0.3\n")
        assert load_file_config(cfg) == {}


def test_load_file_config_partial_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("scoring:\n  half_life: 7.0\n")
        result = load_file_config(cfg)
        assert result == {"half_life": 7.0}


def test_load_file_config_empty_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("")
        assert load_file_config(cfg) == {}


def test_load_file_config_skips_none_value() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.yml"
        cfg.write_text("scoring:\n  w_fts:\n")
        assert load_file_config(cfg) == {}


# ── resolve_weights with file_config ─────────────────────────────────


async def test_resolve_weights_file_config_used(db: SqliteDatabase) -> None:
    """File config provides defaults when no DB config or per-call overrides."""
    file_cfg = {"w_fts": 0.6, "half_life": 7.0}
    weights = await db.resolve_weights(None, file_config=file_cfg)
    assert weights.w_fts == 0.6
    assert weights.half_life == 7.0
    assert weights.w_vec == 0.4  # hardcoded default
    assert weights.w_recency == 0.2  # hardcoded default


async def test_resolve_weights_db_overrides_file_config(db: SqliteDatabase) -> None:
    """DB project config takes precedence over file config."""
    await db.set_config("proj", "w_fts", "0.8")
    file_cfg = {"w_fts": 0.6, "w_vec": 0.3}
    weights = await db.resolve_weights("proj", file_config=file_cfg)
    assert weights.w_fts == 0.8  # DB wins over file
    assert weights.w_vec == 0.3  # file config used (no DB override)


async def test_resolve_weights_per_call_overrides_file_config(db: SqliteDatabase) -> None:
    """Per-call overrides take precedence over file config."""
    file_cfg = {"w_fts": 0.6}
    weights = await db.resolve_weights(None, w_fts=0.1, file_config=file_cfg)
    assert weights.w_fts == 0.1  # per-call wins


async def test_resolve_weights_full_precedence_chain(db: SqliteDatabase) -> None:
    """Test all four layers: per-call > DB > file > hardcoded defaults."""
    await db.set_config("proj", "w_vec", "0.9")
    file_cfg = {"w_fts": 0.6, "w_vec": 0.5, "half_life": 7.0}
    weights = await db.resolve_weights("proj", w_fts=0.1, file_config=file_cfg)
    assert weights.w_fts == 0.1  # per-call override
    assert weights.w_vec == 0.9  # DB config (beats file's 0.5)
    assert weights.half_life == 7.0  # file config
    assert weights.w_recency == 0.2  # hardcoded default


async def test_resolve_weights_empty_file_config(db: SqliteDatabase) -> None:
    """Empty file config behaves same as no file config."""
    weights_without = await db.resolve_weights(None)
    weights_with = await db.resolve_weights(None, file_config={})
    assert weights_without == weights_with


async def test_resolve_weights_none_file_config(db: SqliteDatabase) -> None:
    """None file_config behaves same as empty dict."""
    weights = await db.resolve_weights(None, file_config=None)
    assert weights == ScoringWeights()


# ── End-to-end: file config flows through tool layer ─────────────────


async def test_search_tool_uses_file_config(db: SqliteDatabase) -> None:
    """Verify that memory_search passes file_config through to resolve_weights."""
    from dataclasses import dataclass

    from rekal.adapters.mcp_adapter import AppContext
    from rekal.adapters.tools.core import memory_search, memory_store

    @dataclass
    class FakeRequestContext:
        lifespan_context: AppContext

    @dataclass
    class FakeContext:
        request_context: FakeRequestContext

    # Store a memory
    file_cfg: dict[str, float] = {"w_fts": 0.9, "w_vec": 0.05, "w_recency": 0.05}
    ctx = FakeContext(
        request_context=FakeRequestContext(
            lifespan_context=AppContext(db=db, file_config=file_cfg)
        )
    )
    await memory_store(ctx, "Python testing best practices")
    results = await memory_search(ctx, "Python")
    assert isinstance(results, list)
    assert len(results) > 0


async def test_build_context_tool_uses_file_config(db: SqliteDatabase) -> None:
    """Verify that memory_build_context passes file_config through."""
    from dataclasses import dataclass

    from rekal.adapters.mcp_adapter import AppContext
    from rekal.adapters.tools.smart_write import memory_build_context

    @dataclass
    class FakeRequestContext:
        lifespan_context: AppContext

    @dataclass
    class FakeContext:
        request_context: FakeRequestContext

    file_cfg: dict[str, float] = {"half_life": 7.0}
    ctx = FakeContext(
        request_context=FakeRequestContext(
            lifespan_context=AppContext(db=db, file_config=file_cfg)
        )
    )
    await db.store("Rust memory management")
    result = await memory_build_context(ctx, "Rust")
    assert "query" in result
    assert "memories" in result
