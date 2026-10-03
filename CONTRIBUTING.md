# Contributing

Thanks for taking a look at RowBridge.

## Development setup

Requirements: Python 3.12+ and `uv`.

```bash
uv sync --extra dev
```

Run the complete local quality gate before opening a pull request:

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest
uv build
```

## Pull requests

Keep changes focused. A pull request should explain:

- the user or engineering problem being solved;
- the behavior before and after the change;
- tests added or updated;
- any compatibility or security implications.

For matching changes, include a concrete example showing why the previous behavior was insufficient.

For persistence changes, preserve existing SQLite data where practical and add a migration/compatibility test when schema behavior changes.

## Design principles

Changes should preserve these project properties unless there is a strong reason not to:

- local-first operation;
- deterministic matching;
- explainable evidence;
- bounded work on large inputs;
- explicit human review for ambiguity;
- no mutation of original source files;
- no required external service for core functionality.
