"""Shared helpers for rekal memory hooks.

Imported by sibling handler scripts. Python puts the executed script's own
directory on ``sys.path[0]``, so a plain ``from shared import ...`` resolves
when Claude Code runs ``python3 .../hooks/handlers/<handler>.py``.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import PurePosixPath, PureWindowsPath

# Recall shells out to the `rekal recall` CLI. Tests (and unusual installs)
# override the command via REKAL_RECALL_CMD. A per-turn recall must be quick;
# cap it so a slow/hung CLI never stalls a prompt.
RECALL_TIMEOUT_SECONDS = 10

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


def emit_additional_context(event: str, text: str) -> None:
    """Inject ``text`` into the model's context for ``event``.

    The documented shape: exit 0 + ``hookSpecificOutput.additionalContext``
    is merged into the agent's working context for the named hook event.
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": text,
                }
            }
        )
    )


def emit_recall_context(event: str, directive: str, memory: str | None) -> None:
    """Inject recalled ``memory`` (when present) followed by ``directive``.

    Shared by session-start and user-prompt-submit so the memory-then-directive
    contract lives in one place.
    """
    parts = [memory, directive] if memory else [directive]
    emit_additional_context(event, "\n\n".join(parts))


def run_recall_cli(extra_args: list[str]) -> str | None:
    """Run ``rekal recall`` and return its stdout, or None on any failure.

    Recall must never block a session or turn: a missing binary, non-zero
    exit, timeout, or empty output all collapse to None so the caller can
    fall back to injecting the directive alone.
    """
    override = os.environ.get("REKAL_RECALL_CMD")
    base = shlex.split(override) if override else ["rekal", "recall"]
    try:
        result = subprocess.run(
            [*base, *extra_args],
            capture_output=True,
            text=True,
            check=False,
            timeout=RECALL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


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
