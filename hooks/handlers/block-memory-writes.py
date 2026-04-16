#!/usr/bin/env python3
"""PreToolUse hook: block Edit/Write targeting MEMORY.md, redirect to rekal."""

from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath, PureWindowsPath

BLOCKED_STEMS = frozenset(("memory",))
BLOCKED_SUFFIX = ".md"

BLOCK_MSG = (
    "BLOCKED: Do not write to MEMORY.md. "
    "Use rekal memory tools instead: memory_store, memory_supersede, memory_search. "
    "rekal is your memory system."
)


def is_memory_file(path: str) -> bool:
    # A posix parser mangles Windows paths (treats "C:\foo\bar" as one segment)
    # and vice versa — check both so we catch either convention.
    return any(
        cls(path).suffix.lower() == BLOCKED_SUFFIX and cls(path).stem.lower() in BLOCKED_STEMS
        for cls in (PurePosixPath, PureWindowsPath)
    )


def main() -> None:
    # Claude Code pipes the tool call as JSON on stdin.
    data = json.load(sys.stdin)
    path = data.get("tool_input", {}).get("file_path", "")
    if path and is_memory_file(path):
        # stderr becomes the agent-visible block reason;
        # exit code 2 tells Claude Code to abort the tool call.
        print(BLOCK_MSG, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
