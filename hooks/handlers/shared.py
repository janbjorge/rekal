"""Shared helpers for rekal memory hooks.

Imported by sibling handler scripts. Python puts the executed script's own
directory on ``sys.path[0]``, so a plain ``from shared import ...`` resolves
when Claude Code runs ``python3 .../hooks/handlers/<handler>.py``.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath, PureWindowsPath

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
