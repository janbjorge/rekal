#!/usr/bin/env python3
"""SessionStart hook: inject REAL rekal memory at the start of every session.

Deterministic recall: instead of asking the model to call
memory_build_context, this shells `rekal recall` (recency-ordered, no query
yet) and injects the actual memories. The memory is in context from turn one
whether or not the model chooses to call a tool.

A tail directive follows the memory block so the "memory lives only in rekal"
guardrail survives even when the DB is empty or the CLI is unavailable.
"""

from __future__ import annotations

from shared import emit_recall_context, run_recall_cli

DIRECTIVE = (
    "Persistent memory lives ONLY in rekal, never in files: there is no "
    "MEMORY.md and no memory section of CLAUDE.md. Do not look for, read, or "
    "infer memory from any file — a missing file means nothing. Persist "
    "durable facts, decisions, and preferences as they emerge via memory_store "
    "/ memory_supersede; recall more with memory_search."
)


def main() -> None:
    emit_recall_context("SessionStart", DIRECTIVE, run_recall_cli(["--limit", "10"]))


if __name__ == "__main__":
    main()
