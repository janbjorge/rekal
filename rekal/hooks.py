"""Claude Code hook payloads and fixed text, shared by the ``rekal hook`` CLI.

The handlers are thin ``rekal hook <event>`` subcommands in ``rekal.__main__``:
they read the hook JSON from stdin (when relevant) and print a payload to
stdout. This module holds the stdlib-only logic and the fixed text so nothing
here drags in the embedding/sqlite imports — the PreToolUse hooks (which fire
on every Read/Edit/Write) must stay light.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

# Flat-file memory stores rekal should own. Matched by stem + suffix so we catch
# MEMORY.md, memories.txt, etc., without touching real source or CLAUDE.md.
MEMORY_STEMS = frozenset(("memory", "memories"))
MEMORY_SUFFIXES = frozenset((".md", ".txt"))

# A tail directive always follows the recalled memory block so the "memory
# lives only in rekal" guardrail survives even when the DB is empty or recall
# fails. SessionStart runs once (verbose); UserPromptSubmit runs every turn
# (terse — its token cost is paid repeatedly).
SESSION_START_DIRECTIVE = (
    "Persistent memory lives ONLY in rekal, never in files: there is no "
    "MEMORY.md and no memory section of CLAUDE.md. Do not look for, read, or "
    "infer memory from any file; a missing file means nothing. Persist "
    "durable facts, decisions, and preferences as they emerge via memory_store "
    "/ memory_supersede; recall more with memory_search."
)
PROMPT_SUBMIT_DIRECTIVE = (
    "[rekal memory] Memory lives ONLY in rekal, not files. Persist durable "
    "facts/decisions/preferences immediately via memory_store / "
    "memory_supersede; do not batch to end of session."
)

# Reasons handed to the model when a flat-file memory read/write is denied. The
# reason (not a dead end) is what redirects the model to rekal.
BLOCK_WRITE_REASON = (
    "Do not write memories to a file. rekal is your memory system — "
    "use memory_store / memory_supersede instead. "
    "memory_search to check for an existing memory first."
)
REDIRECT_READ_REASON = (
    "This file is not a memory store, and its contents (or absence) tell you "
    "nothing about prior context. rekal holds your memory. "
    "Call memory_build_context to load it instead of reading files."
)


def is_memory_file(path: str) -> bool:
    # A posix parser mangles Windows paths (treats "C:\foo\bar" as one segment)
    # and vice versa — check both so we catch either convention.
    return any(
        cls(path).suffix.lower() in MEMORY_SUFFIXES and cls(path).stem.lower() in MEMORY_STEMS
        for cls in (PurePosixPath, PureWindowsPath)
    )


def context_payload(event: str, directive: str, memory: str | None = None) -> dict[str, object]:
    """Payload injecting recalled ``memory`` (when present) then ``directive``."""
    parts = [memory, directive] if memory else [directive]
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": "\n\n".join(parts),
        }
    }


def deny_payload(reason: str) -> dict[str, object]:
    """PreToolUse deny payload.

    Exit 0 + a structured ``permissionDecision: "deny"`` is the documented way
    to surface ``permissionDecisionReason`` to the model (unlike an exit-2
    stderr block, which is user-facing) — so the model reads the reason and
    calls rekal instead of hitting a dead end.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
