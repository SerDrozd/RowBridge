# RowBridge

RowBridge is a local-first workbench for reconciling two CSV exports when the same real record is represented differently on each side.

It is aimed at operational data work where a clean shared ID is missing or inconsistent. Instead of returning an opaque match flag, RowBridge records the evidence that contributed to each proposed pair and separates confident matches from rows that need review.

## Current capabilities

- upload two UTF-8 CSV files locally;
- map a primary identity field on each side;
- optionally map amount and date fields;
- normalize and fuzzy-match the primary values;
- apply amount tolerance and date-window checks;
- preserve one-to-one matching on Side B;
- classify results as auto matched, review, or unmatched;
- persist runs and source rows in SQLite;
- reopen a saved run after an application restart;
- export a reconciliation CSV with the evidence summary.

The application does not modify either source file and does not make network calls for matching.

## Quick start

Requirements: Python 3.12+ and `uv`.

```bash
uv sync --extra dev
uv run rowbridge
```

Open `http://127.0.0.1:8000` in a browser.

For a small example, use:

- `examples/orders.csv` as Side A;
- `examples/payments.csv` as Side B;
- `invoice_ref` ↔ `reference` as the primary fields;
- `amount` ↔ `total` as the amount fields;
- `invoice_date` ↔ `paid_at` as the date fields.

Application data is stored in `.rowbridge/` by default. Set `ROWBRIDGE_DATA_DIR` to use another local directory.

## Matching behavior

The current scorer is intentionally small and deterministic. Primary text contributes up to 70% of a match score. An amount within the configured tolerance contributes 20%, and a date inside the configured window contributes 10%.

A candidate above the auto-match threshold is still sent to review when another candidate is too close to it. Side B rows are used at most once in a run.

This is not an accounting engine, master-data-management system, or automatic data-correction tool. Large datasets and richer candidate generation are outside the current scope.

## Development

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest
```

The web integration test exercises the full local flow: upload, field mapping, matching, SQLite persistence, results rendering, application restart, and CSV export.

## Project structure

```text
src/rowbridge/
  ingestion.py   CSV validation and staging
  matching.py    normalization, scoring, and one-to-one decisions
  service.py     reconciliation use case
  storage.py     SQLite persistence
  web.py         FastAPI routes
  templates/     server-rendered UI
  static/        local CSS
```

## Privacy

Files are processed on the machine running RowBridge. The matching path has no external API dependency. Source files are read-only inputs; RowBridge writes its own staged copies, SQLite state, and explicit exports under its application data directory.

## License

MIT
