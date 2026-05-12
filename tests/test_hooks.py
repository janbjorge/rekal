"""Subprocess-level tests for the plugin hook handlers (hooks/handlers/).

These run the handler scripts as subprocesses — the same way Claude Code
invokes them — and assert on exit code and stdout JSON. The handlers live
outside the ``rekal/`` package (they ship with the plugin), so they are
excluded from coverage measurement and are not imported directly.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from rekal.adapters.sqlite_adapter import SqliteDatabase

from .conftest import deterministic_embed

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLERS = REPO_ROOT / "hooks" / "handlers"


def run_handler(
    name: str,
    stdin: str | dict[str, object] = "",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke a hook handler script with the given stdin and env overrides.

    ``stdin`` may be raw text (to exercise malformed-input paths) or a dict
    (serialized to JSON, the shape Claude Code sends).
    """
    payload = stdin if isinstance(stdin, str) else json.dumps(stdin)
    return subprocess.run(
        [sys.executable, str(HANDLERS / name)],
        input=payload,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=30,
        check=False,
    )


def tool_call(file_path: str) -> str:
    return json.dumps({"tool_input": {"file_path": file_path}})


# --- context-injection handlers -------------------------------------------


@pytest.mark.parametrize(
    ("handler", "event"),
    [
        ("session-start.py", "SessionStart"),
        ("user-prompt-submit.py", "UserPromptSubmit"),
    ],
)
def test_injection_handler_emits_additional_context(handler: str, event: str) -> None:
    result = run_handler(handler)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == event
    assert "rekal" in out["additionalContext"]


# --- PreToolUse memory-file redirect handlers -----------------------------


@pytest.mark.parametrize("handler", ["block-memory-writes.py", "redirect-memory-reads.py"])
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
def test_memory_paths_are_denied(handler: str, path: str) -> None:
    result = run_handler(handler, tool_call(path))
    assert result.returncode == 0
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "memory" in out["permissionDecisionReason"].lower()


@pytest.mark.parametrize("handler", ["block-memory-writes.py", "redirect-memory-reads.py"])
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
def test_non_memory_paths_pass_through(handler: str, path: str) -> None:
    result = run_handler(handler, tool_call(path) if path else "{}")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# --- PreCompact snapshot handler ------------------------------------------


def test_pre_compact_stores_scratch_memory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "memory.db")
        transcript = Path(tmp) / "transcript.jsonl"
        # Two messages in Claude Code's nested ``message`` envelope plus one
        # in flat shape — handler must extract both.
        transcript.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "message": {
                                "role": "user",
                                "content": "Help me ship the auth refactor",
                            }
                        }
                    ),
                    json.dumps(
                        {
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {"type": "text", "text": "Reading auth/middleware.py"}
                                ],
                            }
                        }
                    ),
                    json.dumps({"role": "user", "content": "Now run tests"}),
                ]
            )
        )

        result = run_handler(
            "pre-compact.py",
            {
                "transcript_path": str(transcript),
                "cwd": tmp,
                "session_id": "sess-123",
            },
            env={"REKAL_DB_PATH": db_path, "REKAL_PROJECT": "alpha"},
        )
        assert result.returncode == 0, result.stderr

        async def verify() -> None:
            db = await SqliteDatabase.create(db_path, deterministic_embed)
            try:
                rows = await db.memory_timeline(limit=10)
                snapshots = [m for m in rows if "pre-compact" in m.tags]
                assert len(snapshots) == 1
                snap = snapshots[0]
                assert snap.tier == "scratch"
                assert snap.project == "alpha"
                assert "sess-123" in snap.content
                assert "auth refactor" in snap.content
                assert "Reading auth/middleware.py" in snap.content
            finally:
                await db.close()

        asyncio.run(verify())


def test_pre_compact_skips_when_transcript_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "memory.db")
        result = run_handler(
            "pre-compact.py",
            {
                "transcript_path": str(Path(tmp) / "nope.jsonl"),
                "cwd": tmp,
                "session_id": "sess-x",
            },
            env={"REKAL_DB_PATH": db_path},
        )
        assert result.returncode == 0
        # DB never created → handler exited before touching anything.
        assert not Path(db_path).exists()


def test_pre_compact_skips_when_transcript_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "memory.db")
        transcript = Path(tmp) / "empty.jsonl"
        transcript.write_text("")
        result = run_handler(
            "pre-compact.py",
            {
                "transcript_path": str(transcript),
                "cwd": tmp,
                "session_id": "sess-x",
            },
            env={"REKAL_DB_PATH": db_path},
        )
        assert result.returncode == 0
        assert not Path(db_path).exists()


def test_pre_compact_handles_invalid_stdin() -> None:
    result = run_handler("pre-compact.py", "not json")
    assert result.returncode == 0
