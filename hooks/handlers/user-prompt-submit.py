#!/usr/bin/env python3
"""UserPromptSubmit hook: inject query-relevant rekal memory each turn.

Reads the submitted prompt from stdin and shells `rekal recall --query
<prompt>` (hybrid FTS + vector + recency search), injecting the top hits.
This is where rekal's ranking earns its keep: recall is scoped to what the
user just asked, deterministically, without a model tool call.

A terse tail directive always follows so the "memory lives only in rekal"
guardrail is re-asserted every turn (recency is the only lever against a
fixed system prompt). Kept short, since this cost is paid on every prompt.
"""

from __future__ import annotations

import json
import sys

from shared import emit_recall_context, run_recall_cli

DIRECTIVE = (
    "[rekal memory] Memory lives ONLY in rekal, not files. Persist durable "
    "facts/decisions/preferences immediately via memory_store / "
    "memory_supersede; do not batch to end of session."
)


def read_prompt() -> str | None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    prompt = data.get("prompt")
    return prompt if isinstance(prompt, str) and prompt else None


def main() -> None:
    prompt = read_prompt()
    memory = run_recall_cli(["--query", prompt, "--limit", "5"]) if prompt else None
    emit_recall_context("UserPromptSubmit", DIRECTIVE, memory)


if __name__ == "__main__":
    main()
