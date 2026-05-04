Read AGENTS.md before doing anything.

## Pre-commit workflow — required before every commit and push

Run all four CI checks locally and fix anything they flag. Don't push
until they all pass — CI runs the same checks and will reject otherwise.

```bash
uv run ruff check rekal/ tests/
uv run ruff format rekal/ tests/
uv run ty check rekal/ tests/
uv run pytest --cov=rekal --cov-report=term-missing --cov-fail-under=100 tests/
```

Notes:
- Use `ruff format` (not `--check`) so formatting fixes are applied
  in-place before staging.
- If `ty check` reports new errors, fix the types — do not silence with
  `# type: ignore` unless the error is a known tool limitation.
- If coverage drops below 100%, add tests rather than `# pragma: no cover`.
- Run these after every meaningful edit, not just at the end. Catching a
  format/lint error early avoids a fixup commit later.
