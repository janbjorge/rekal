#!/usr/bin/env python3
"""UserPromptSubmit hook: re-assert rekal as the memory system on every turn.

SessionStart fires once; its context decays as the conversation grows and the
agent drifts back to the (stronger, fixed) system-prompt memory behavior. This
hook re-injects a terse directive on every user prompt, so the freshest, most
recent instruction in context always points at rekal — recency is the only
lever that competes with a fixed system prompt.

Kept short on purpose: this text is added to context on EVERY turn, so token
cost is paid repeatedly.
"""

from __future__ import annotations

import json

CONTEXT = (
    "[rekal memory] Persistent memory lives ONLY in rekal, never in files. "
    "There is no MEMORY.md or memory section of CLAUDE.md — do not look for, "
    "read, or assume memory from any file; absence of a file means nothing. "
    "To recall prior context, call memory_build_context. As durable facts, "
    "decisions, preferences, or corrections emerge, persist them immediately "
    "with memory_store / memory_supersede — do not batch to end of session."
)


def main() -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": CONTEXT,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
