#!/usr/bin/env python3
"""PreToolUse hook: redirect a Read of a flat-file memory store to rekal.

The leak this closes: Claude reads MEMORY.md, the file is missing or empty, and
the model concludes "no prior memory exists" and proceeds without rekal. A
missing file means nothing — memory lives in rekal. Deny the misleading read
and feed the model an actionable reason so it calls memory_build_context.
"""

from __future__ import annotations

import json
import sys

from shared import emit_pretooluse_deny, is_memory_file

REASON = (
    "This file is not a memory store, and its contents (or absence) tell you "
    "nothing about prior context. rekal holds your memory. "
    "Call memory_build_context to load it instead of reading files."
)


def main() -> None:
    data = json.load(sys.stdin)
    path = data.get("tool_input", {}).get("file_path", "")
    if path and is_memory_file(path):
        emit_pretooluse_deny(REASON)


if __name__ == "__main__":
    main()
