"""Shared helpers for rekal memory hooks.

Imported by sibling handler scripts. Python puts the executed script's own
directory on ``sys.path[0]``, so a plain ``from shared import ...`` resolves
when Claude Code runs ``python3 .../hooks/handlers/<handler>.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

GIT_DETECT_TIMEOUT_SEC = 2
REKAL_DEFAULT_TIMEOUT_SEC = 15

# Flat-file memory stores rekal should own. Matched by stem + suffix so we catch
# MEMORY.md, memories.txt, etc., without touching real source or CLAUDE.md.
MEMORY_STEMS = frozenset(("memory", "memories"))
MEMORY_SUFFIXES = frozenset((".md", ".txt"))


def is_memory_file(path: str) -> bool:
    # A posix parser mangles Windows paths (treats "C:\foo\bar" as one segment)
    # and vice versa — check both so we catch either convention.
    return any(
        cls(path).suffix.lower() in MEMORY_SUFFIXES and cls(path).stem.lower() in MEMORY_STEMS
        for cls in (PurePosixPath, PureWindowsPath)
    )


def emit_pretooluse_deny(reason: str) -> None:
    """Block a tool call and hand the reason to the model so it can redirect.

    Exit 0 + a structured ``permissionDecision: "deny"`` is the documented way
    to surface ``permissionDecisionReason`` to the model (unlike an exit-2
    stderr block, which is user-facing) — so the model reads the reason and
    calls rekal instead of hitting a dead end.
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def detect_project(cwd: str) -> str | None:
    """Pick a project scope. ``REKAL_PROJECT`` env wins; else git root or cwd basename."""
    env = os.environ.get("REKAL_PROJECT")
    if env:
        return env
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=GIT_DETECT_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return Path(cwd).name or None
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).name
    return Path(cwd).name or None


def read_stdin_json() -> dict[str, object]:
    """Parse Claude Code's stdin payload. Returns ``{}`` on any failure."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def run_rekal(
    args: list[str],
    *,
    stdin: str | None = None,
    timeout: int = REKAL_DEFAULT_TIMEOUT_SEC,
) -> str | None:
    """Invoke ``rekal`` with ``args``. Returns stdout, or ``None`` if missing/failed."""
    binary = shutil.which("rekal")
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def clip(text: str, max_chars: int) -> str:
    """Truncate ``text`` to ``max_chars`` with an ellipsis. No-op if already short."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def get_str(data: dict[str, object], key: str) -> str | None:
    """Return ``data[key]`` if it's a non-empty str, else ``None``."""
    value = data.get(key)
    return value if isinstance(value, str) and value else None
