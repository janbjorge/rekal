#!/usr/bin/env python3
"""PreToolUse hook: redirect Edit/Write of a flat-file memory store to rekal."""

from __future__ import annotations

import json
import sys

from shared import emit_pretooluse_deny, is_memory_file

REASON = (
    "Do not write memories to a file. rekal is your memory system — "
    "use memory_store / memory_supersede instead. "
    "memory_search to check for an existing memory first."
)


def main() -> None:
    # Claude Code pipes the tool call as JSON on stdin.
    data = json.load(sys.stdin)
    path = data.get("tool_input", {}).get("file_path", "")
    if path and is_memory_file(path):
        emit_pretooluse_deny(REASON)


if __name__ == "__main__":
    main()
