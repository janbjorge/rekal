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
    return any(
        cls(path).suffix.lower() == BLOCKED_SUFFIX and cls(path).stem.lower() in BLOCKED_STEMS
        for cls in (PurePosixPath, PureWindowsPath)
    )


def main() -> None:
    data = json.load(sys.stdin)
    path = data.get("tool_input", {}).get("file_path", "")
    if path and is_memory_file(path):
        print(BLOCK_MSG, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
