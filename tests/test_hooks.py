"""Tests for the rekal Claude Code plugin hooks (hooks/handlers/).

These run the handler scripts as subprocesses — the same way Claude Code
invokes them — and assert on exit code and stdout JSON. The handlers live
outside the ``rekal`` package (they ship with the plugin), so they are not
imported directly.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

HANDLERS = Path(__file__).resolve().parent.parent / "hooks" / "handlers"

# By default, point the recall CLI at a no-op so tests never invoke the real
# `rekal recall` against the user's live ~/.rekal/memory.db. Individual tests
# override REKAL_RECALL_CMD to a stub to exercise memory injection.
NOOP_RECALL = f"{shlex.quote(sys.executable)} -c pass"


def run_handler(
    name: str, stdin: str = "", env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, "REKAL_RECALL_CMD": NOOP_RECALL}
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(HANDLERS / name)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env=merged,
    )


def stub_recall_cmd(tmp_path: Path, *, stdout: str = "", exit_code: int = 0) -> str:
    """Write a fake recall CLI printing ``stdout`` then exiting ``exit_code``."""
    stub = tmp_path / "recall_stub.py"
    stub.write_text(f"import sys\nsys.stdout.write({stdout!r})\nsys.exit({exit_code})\n")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(stub))}"


def tool_call(file_path: str) -> str:
    return json.dumps({"tool_input": {"file_path": file_path}})


# --- context-injection handlers -------------------------------------------


def injected_context(result: subprocess.CompletedProcess[str], event: str) -> str:
    assert result.returncode == 0
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == event
    return out["additionalContext"]


def test_session_start_directive_when_no_memory() -> None:
    # No-op recall (empty DB / no memories) → directive still injected.
    ctx = injected_context(run_handler("session-start.py"), "SessionStart")
    assert "rekal" in ctx
    assert "only" in ctx.lower()


def test_session_start_injects_memory(tmp_path: Path) -> None:
    cmd = stub_recall_cmd(tmp_path, stdout="## rekal memory\n- [fact] Uses Postgres (id abc)")
    ctx = injected_context(
        run_handler("session-start.py", env={"REKAL_RECALL_CMD": cmd}), "SessionStart"
    )
    assert "Uses Postgres" in ctx
    assert "rekal" in ctx  # tail directive still present


def test_session_start_directive_when_recall_fails(tmp_path: Path) -> None:
    cmd = stub_recall_cmd(tmp_path, stdout="noise", exit_code=1)
    ctx = injected_context(
        run_handler("session-start.py", env={"REKAL_RECALL_CMD": cmd}), "SessionStart"
    )
    assert "noise" not in ctx  # non-zero exit → output discarded
    assert "rekal" in ctx


def test_user_prompt_injects_query_memory(tmp_path: Path) -> None:
    cmd = stub_recall_cmd(tmp_path, stdout="## rekal memory\n- [preference] Ruff > Black (id x)")
    ctx = injected_context(
        run_handler(
            "user-prompt-submit.py",
            stdin=json.dumps({"prompt": "set up linting"}),
            env={"REKAL_RECALL_CMD": cmd},
        ),
        "UserPromptSubmit",
    )
    assert "Ruff > Black" in ctx
    assert "rekal" in ctx


def test_user_prompt_passes_query_as_single_token(tmp_path: Path) -> None:
    # A prompt starting with "-" must reach `rekal recall` as --query=<prompt>,
    # not a bare arg argparse would mistake for a flag. Stub echoes its argv.
    stub = tmp_path / "echo_argv.py"
    stub.write_text("import sys\nsys.stdout.write(' '.join(sys.argv[1:]))\n")
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(stub))}"
    ctx = injected_context(
        run_handler(
            "user-prompt-submit.py",
            stdin=json.dumps({"prompt": "--dangerous flag"}),
            env={"REKAL_RECALL_CMD": cmd},
        ),
        "UserPromptSubmit",
    )
    assert "--query=--dangerous flag" in ctx


@pytest.mark.parametrize("stdin", ["", "{}", '{"prompt": ""}', "[]", "not json"])
def test_user_prompt_directive_when_no_prompt(tmp_path: Path, stdin: str) -> None:
    # No usable prompt → recall skipped, directive-only. Stub would print if
    # called; assert it did not.
    cmd = stub_recall_cmd(tmp_path, stdout="SHOULD-NOT-APPEAR")
    ctx = injected_context(
        run_handler("user-prompt-submit.py", stdin=stdin, env={"REKAL_RECALL_CMD": cmd}),
        "UserPromptSubmit",
    )
    assert "SHOULD-NOT-APPEAR" not in ctx
    assert "rekal" in ctx


# --- PreToolUse memory-file redirect handlers -----------------------------


@pytest.mark.parametrize("handler", ["block-memory-writes.py", "redirect-memory-reads.py"])
@pytest.mark.parametrize(
    "path",
    [
        "/proj/MEMORY.md",
        "/proj/memory.md",
        "/proj/memories.txt",
        "MEMORY.TXT",
        r"C:\proj\MEMORY.md",
    ],
)
def test_memory_paths_are_denied(handler: str, path: str) -> None:
    result = run_handler(handler, tool_call(path))
    assert result.returncode == 0
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "memory" in out["permissionDecisionReason"].lower()


@pytest.mark.parametrize("handler", ["block-memory-writes.py", "redirect-memory-reads.py"])
@pytest.mark.parametrize(
    "path",
    [
        "/proj/src/main.py",
        "/proj/CLAUDE.md",
        "/proj/notes.md",
        "/proj/README.md",
        "",
    ],
)
def test_non_memory_paths_pass_through(handler: str, path: str) -> None:
    result = run_handler(handler, tool_call(path) if path else "{}")
    assert result.returncode == 0
    assert result.stdout.strip() == ""
