#!/usr/bin/env python3
"""PreCompact hook: snapshot recent turns to scratch tier before compaction.

Compaction discards conversation history to fit the model window. Anything
learned this session that hasn't been stored yet is lost. This hook reads the
transcript, distills the last ``TURNS_TO_CAPTURE`` user/assistant exchanges
into a single scratch-tier memory tagged ``pre-compact``, then lets compaction
proceed. After compaction the agent can recover via ``memory_search``.

Silent on every failure path — compaction must always proceed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from shared import clip, detect_project, get_str, read_stdin_json, run_rekal

TURNS_TO_CAPTURE = 20
TURN_MAX_CHARS = 400
SNAPSHOT_MAX_CHARS = 6000
TTL_HOURS = 168.0  # 7 days

VALID_ROLES = frozenset({"user", "assistant"})


class Turn(TypedDict):
    role: str
    text: str


def extract_text(content: object) -> str:
    """Pull plain text out of a transcript ``content`` field.

    Claude Code stores ``content`` as either a string or a list of typed
    blocks (``{"type": "text", "text": "..."}``, tool calls, etc.). We only
    care about text blocks here.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def parse_turn(line: str) -> Turn | None:
    """Parse one JSONL transcript line into a ``Turn``, or ``None`` if not relevant."""
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict):
        return None
    # Claude Code wraps the message under ``message``; older formats store it flat.
    raw_message = entry.get("message", entry)
    if not isinstance(raw_message, dict):
        return None
    role = raw_message.get("role")
    if not isinstance(role, str) or role not in VALID_ROLES:
        return None
    text = extract_text(raw_message.get("content"))
    if not text:
        return None
    return {"role": role, "text": text}


def parse_transcript(path: Path) -> list[Turn]:
    """Return up to the last ``TURNS_TO_CAPTURE`` parseable turns from ``path``."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    turns: list[Turn] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        turn = parse_turn(line)
        if turn is not None:
            turns.append(turn)
    return turns[-TURNS_TO_CAPTURE:]


def format_turn(turn: Turn) -> str:
    # Collapse all whitespace so each turn is exactly one line.
    text = " ".join(turn["text"].split())
    return f"[{turn['role']}] {clip(text, TURN_MAX_CHARS)}"


def render(turns: list[Turn], session_id: str) -> str:
    header = f"Pre-compact snapshot (session {session_id}, {len(turns)} turns):"
    body = "\n".join(format_turn(t) for t in turns)
    return clip(f"{header}\n{body}", SNAPSHOT_MAX_CHARS)


def main() -> None:
    data = read_stdin_json()
    transcript_str = get_str(data, "transcript_path")
    if transcript_str is None:
        return
    transcript = Path(transcript_str)
    if not transcript.is_file():
        return

    turns = parse_transcript(transcript)
    if not turns:
        return

    session_id = get_str(data, "session_id") or "unknown"
    snapshot = render(turns, session_id)

    cwd = get_str(data, "cwd")
    project = detect_project(cwd) if cwd else None
    args = [
        "scratch-capture",
        "--tag",
        "pre-compact",
        "--tag",
        "session-snapshot",
        "--ttl-hours",
        str(TTL_HOURS),
    ]
    if project:
        args += ["--project", project]
    run_rekal(args, stdin=snapshot)


if __name__ == "__main__":
    main()
