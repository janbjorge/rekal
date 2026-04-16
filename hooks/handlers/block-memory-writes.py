#!/usr/bin/env python3
"""Block writes to MEMORY.md files, redirecting to rekal.

This PreToolUse hook intercepts Edit/Write calls targeting MEMORY.md
and blocks them with exit code 2, telling the agent to use rekal instead.
"""

from __future__ import annotations

import json
import sys


MEMORY_FILENAMES = {"MEMORY.md", "memory.md"}


def is_memory_file(path: str) -> bool:
    """Check if the target path is a built-in memory file."""
    # Match any path ending in MEMORY.md or memory.md
    for name in MEMORY_FILENAMES:
        if path.endswith(name):
            return True
    return False


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        sys.exit(0)  # Can't parse — allow through

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    if is_memory_file(file_path):
        print(
            "BLOCKED: Do not write to MEMORY.md. "
            "Use rekal memory tools instead: memory_store, memory_supersede, memory_search. "
            "rekal is your memory system.",
            file=sys.stderr,
        )
        sys.exit(2)  # Exit code 2 = block tool execution

    sys.exit(0)


if __name__ == "__main__":
    main()
