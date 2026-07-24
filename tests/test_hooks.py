"""Tests for the rekal Claude Code hook commands (``rekal hook <event>``).

The plugin's hooks.json invokes these as ``uv run ... rekal hook <event>``, so
they are exercised through the typer CLI the same way Claude Code runs them:
recall runs in-process against the --db, and a payload is printed to stdout.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import Result
from typer.testing import CliRunner

from rekal.__main__ import app
from rekal.adapters.sqlite_adapter import SqliteDatabase

from .conftest import deterministic_embed

runner = CliRunner()

# The query recall path embeds; swap FastEmbedder for the deterministic test
# embedder so it never loads the real ONNX model.
patch_embedder = patch("rekal.embeddings.FastEmbedder", lambda: deterministic_embed)


async def make_db(path: str, *stores: tuple[str, str | None]) -> None:
    db = await SqliteDatabase.create(path, deterministic_embed)
    try:
        for content, project in stores:
            await db.store(content, project=project)
    finally:
        await db.close()


def injected_context(result: Result, event: str) -> str:
    assert result.exit_code == 0
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == event
    return out["additionalContext"]


def tool_call(file_path: str) -> str:
    return json.dumps({"tool_input": {"file_path": file_path}})


# --- context-injection commands -------------------------------------------


def test_session_start_directive_when_no_memory() -> None:
    # Missing DB → recall degrades to empty, but the directive is still injected.
    result = runner.invoke(app, ["--db", "/nonexistent/db.sqlite", "hook", "session-start"])
    ctx = injected_context(result, "SessionStart")
    assert "rekal" in ctx
    assert "only" in ctx.lower()


def test_session_start_injects_directive_only() -> None:
    # SessionStart deliberately injects NO memories: query-less recency recall
    # is mostly the wrong topic in a multi-subsystem DB. Directive only.
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        asyncio.run(make_db(db_path, ("Uses Postgres", None)))

        result = runner.invoke(app, ["--db", db_path, "hook", "session-start"])
        ctx = injected_context(result, "SessionStart")
        assert "Uses Postgres" not in ctx
        assert "rekal" in ctx


def test_session_start_readonly_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REKAL_READONLY", "1")
    result = runner.invoke(app, ["--db", "/nonexistent/db.sqlite", "hook", "session-start"])
    ctx = injected_context(result, "SessionStart")
    assert "read-only" in ctx
    assert "memory_store" not in ctx


def test_user_prompt_readonly_with_match(monkeypatch: pytest.MonkeyPatch) -> None:
    # Readonly + matching memory: block injected with trust header, no ids,
    # no store nudge (readonly per-turn directive is empty).
    monkeypatch.setenv("REKAL_READONLY", "1")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        asyncio.run(make_db(db_path, ("Ruff over Black for formatting", None)))

        with patch_embedder:
            result = runner.invoke(
                app,
                ["--db", db_path, "hook", "user-prompt-submit"],
                input=json.dumps({"prompt": "formatting"}),
            )
        ctx = injected_context(result, "UserPromptSubmit")
        assert "Ruff over Black" in ctx
        assert "(id " not in ctx
        assert "memory_store" not in ctx
        assert "cite anchors" in ctx  # trust header does the framing


def test_user_prompt_readonly_no_match_injects_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Readonly + nothing recalled = empty payload = zero bytes injected;
    # warm-empty must cost the same as cold.
    monkeypatch.setenv("REKAL_READONLY", "1")
    result = runner.invoke(
        app,
        ["--db", "/nonexistent/db.sqlite", "hook", "user-prompt-submit"],
        input=json.dumps({"prompt": "anything"}),
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_session_start_directive_when_recall_fails() -> None:
    # A file that exists but is not a valid SQLite DB → recall raises internally
    # and degrades to directive-only; the hook must not crash.
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "not.db"
        bad.write_text("this is not a sqlite database")
        result = runner.invoke(app, ["--db", str(bad), "hook", "session-start"])
        ctx = injected_context(result, "SessionStart")
        assert "rekal" in ctx


def test_user_prompt_directive_when_recall_fails() -> None:
    # Corrupt DB → recall_text swallows the exception and the turn still gets
    # the write-mode directive alone.
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "not.db"
        bad.write_text("this is not a sqlite database")
        result = runner.invoke(
            app,
            ["--db", str(bad), "hook", "user-prompt-submit"],
            input=json.dumps({"prompt": "anything"}),
        )
        ctx = injected_context(result, "UserPromptSubmit")
        assert "rekal" in ctx


def test_user_prompt_injects_query_memory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        asyncio.run(make_db(db_path, ("Ruff over Black for formatting", None)))

        with patch_embedder:
            result = runner.invoke(
                app,
                ["--db", db_path, "hook", "user-prompt-submit"],
                input=json.dumps({"prompt": "formatting"}),
            )
        ctx = injected_context(result, "UserPromptSubmit")
        assert "Ruff over Black" in ctx
        assert "rekal" in ctx


@pytest.mark.parametrize("stdin", ["", "{}", '{"prompt": ""}', "[]", "not json"])
def test_user_prompt_directive_when_no_prompt(stdin: str) -> None:
    # No usable prompt → recall skipped, directive-only. A stored memory would
    # appear if recall ran; assert it did not.
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        asyncio.run(make_db(db_path, ("SHOULD-NOT-APPEAR", None)))

        result = runner.invoke(app, ["--db", db_path, "hook", "user-prompt-submit"], input=stdin)
        ctx = injected_context(result, "UserPromptSubmit")
        assert "SHOULD-NOT-APPEAR" not in ctx
        assert "rekal" in ctx


# --- PreToolUse memory-file redirect commands -----------------------------


@pytest.mark.parametrize("command", ["block-memory-writes", "redirect-memory-reads"])
@pytest.mark.parametrize(
    "path",
    [
        "/proj/MEMORY.md",
        "/proj/memory.md",
        "/proj/memories.txt",
        "MEMORY.TXT",
        r"C:\proj\MEMORY.md",
    ],
)
def test_memory_paths_are_denied(command: str, path: str) -> None:
    result = runner.invoke(app, ["hook", command], input=tool_call(path))
    assert result.exit_code == 0
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "memory" in out["permissionDecisionReason"].lower()


@pytest.mark.parametrize("command", ["block-memory-writes", "redirect-memory-reads"])
@pytest.mark.parametrize(
    "path",
    [
        "/proj/src/main.py",
        "/proj/CLAUDE.md",
        "/proj/notes.md",
        "/proj/README.md",
        "",
    ],
)
def test_non_memory_paths_pass_through(command: str, path: str) -> None:
    result = runner.invoke(app, ["hook", command], input=tool_call(path) if path else "{}")
    assert result.exit_code == 0
    assert result.stdout.strip() == ""
