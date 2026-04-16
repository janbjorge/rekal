#!/usr/bin/env bash

# Inject additionalContext at session start reminding the agent to use rekal.
# This runs automatically — no user action needed.

cat << 'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "rekal is your memory system. Before doing anything else, call memory_build_context with your current task to load relevant prior knowledge. Do NOT use MEMORY.md or CLAUDE.md for storing memories — all persistent knowledge goes through rekal tools (memory_store, memory_supersede, memory_search)."
  }
}
EOF

exit 0
