#!/usr/bin/env python3
"""SessionStart hook: inject rekal context at the start of every session.

Claude Code merges additionalContext into the agent's working context,
ensuring memory_build_context is called before any codebase exploration.
"""

from __future__ import annotations

import json
import sys

CONTEXT = (
    "rekal is your memory system. "
    "Before doing anything else, call memory_build_context with your current task "
    "to load relevant prior knowledge. "
    "Persistent memory lives ONLY in rekal, never in files: there is no MEMORY.md "
    "and no memory section of CLAUDE.md. Do not look for, read, or infer memory "
    "from any file — a missing file means nothing, not 'no prior knowledge.' "
    "All persistent knowledge goes through rekal tools "
    "(memory_store, memory_supersede, memory_search)."
)


def main() -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": CONTEXT,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
