"""Tests for the rekal Claude Code plugin hooks (hooks/handlers/).

These run the handler scripts as subprocesses — the same way Claude Code
invokes them — and assert on exit code and stdout JSON. The handlers live
outside the ``rekal`` package (they ship with the plugin), so they are not
imported directly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HANDLERS = Path(__file__).resolve().parent.parent / "hooks" / "handlers"


def run_handler(name: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HANDLERS / name)],
        input=stdin,
        capture_output=True,
        text=True,
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
    assert result.returncode == 0
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
