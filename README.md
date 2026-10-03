# RowBridge

RowBridge is a local-first workbench for reconciling two CSV or Excel exports when the same real record is represented differently on each side.

It is aimed at operational data work where a clean shared ID is missing or inconsistent. Instead of returning an opaque match flag, RowBridge records the evidence behind each proposed pair, separates confident matches from rows that need review, and keeps human decisions in an audit trail.

## Current capabilities

- upload CSV and XLSX exports locally;
- detect comma, semicolon, tab, and pipe-delimited CSV files;
- read UTF-8 with or without BOM, UTF-16, and Windows-1252 CSV text;
- inspect detected file details and a data preview before matching;
- read the first XLSX worksheet that contains both a header and data rows;
- map a primary identity field on each side;
- optionally map a secondary text field such as customer, company, email, or description;
- optionally map amount and date fields;
- normalize and fuzzy-match text with deterministic comparison rules;
- apply amount tolerance and date-window checks;
- generate a bounded candidate set instead of scoring every possible row pair on the normal path;
- preserve one-to-one matching across both sides;
- detect close competitors on either side and keep ambiguous pairs in review;
- accept or reject proposed review matches;
- manually link one unmatched Side A row to one unmatched Side B row;
- store human review actions separately from algorithmic matching evidence;
- filter results by review, matched, or unmatched state;
- paginate large result sets instead of rendering every match in one response;
- persist runs, source rows, current match state, and review history in SQLite with WAL mode and a busy timeout;
- reopen a saved run after an application restart;
- export the current reconciliation state as CSV;
- export an XLSX workbook with summary, reconciliation, unmatched, and review-history sheets;
- bound upload reads before parsing, expire stale staged files, and delete staging copies after a run is created;
- reject duplicate field-role selections and obviously invalid amount/date mappings before matching;
- apply local-only browser protections such as trusted-host checks, cross-origin POST rejection, CSP, and no-store responses.

The application does not modify either source file and does not make network calls for matching.

## Quick start

Requirements: Python 3.12+ and `uv`.

```bash
uv sync --extra dev
uv run rowbridge
```

Open `http://127.0.0.1:8000` in a browser.

For the included example, use:

- `examples/orders.csv` as Side A;
- `examples/payments.csv` as Side B for CSV-to-CSV, or `examples/payments.xlsx` for a mixed CSV-to-Excel run;
- `invoice_ref` ↔ `reference` as the primary fields;
- `customer` ↔ `payer` as the secondary text fields;
- `amount` ↔ `total` as the amount fields;
- `invoice_date` ↔ `paid_at` as the date fields.

Application data is stored in `.rowbridge/` by default. Set `ROWBRIDGE_DATA_DIR` to use another local directory.

## Input handling

RowBridge validates files before staging them for a run.

CSV input supports comma, semicolon, tab, and pipe delimiters. Delimiters are detected from the file content instead of being inferred from the file name. UTF-8, UTF-16, and Windows-1252 are supported. Blank lines before or between data rows are ignored, while duplicate headers, empty headers, and rows with more values than the header are rejected with a clear error.

XLSX files are opened read-only. RowBridge uses the first worksheet that contains a non-empty header and at least one data row, and shows the selected sheet name in the preview. Workbook archives are checked before parsing and rejected if their expanded size exceeds the safety limit.

Current input limits are 5 MB, 50,000 data rows, and 200 columns per file. The HTTP upload path reads only up to the configured file limit plus one byte before rejecting an oversized file, so a large request is not first loaded into application memory in full. `.xls`, password-protected workbooks, multi-sheet selection, and arbitrary text encodings are intentionally outside the current scope.

## Matching behavior

RowBridge builds typed comparison rules from the selected field mapping. With all four rule types selected, primary text contributes 55% of the score, secondary text 20%, amount 15%, and date 10%. When the secondary field is not selected, the primary/amount/date weights are 70%/20%/10%. Missing optional rule types are removed and the remaining weights are normalized.

Candidate generation uses normalized primary blocks first, then optional secondary and amount/date support blocks. Exact normalized primary values take the shortest path. If no block yields a candidate, RowBridge performs a small fuzzy fallback on bounded data sets rather than silently declaring the row unmatched. The global fallback scan is disabled once Side B is large enough that an all-against-all fuzzy scan would become unsafe, and candidate sets are capped per source row.

Support fields do not automatically rescue a clearly unrelated primary value. A lower primary similarity can only be rescued when a strong secondary-text match is present together with amount or date support. This keeps common amounts and dates from filling the review queue with unrelated pairs.

One-to-one selection is global and deterministic. Ambiguity checks use per-row competitor indexes rather than scanning the entire candidate list for every selected pair. A selected pair is sent to review when its score is below the auto threshold or another candidate for either source row is within the ambiguity margin.

## Operational hardening

Result pages are bounded to 100 rows at a time, while exports still contain the complete current reconciliation state. Small unmatched sets keep the convenient dropdown-based manual-link workflow; large unmatched sets switch to exact source-row-number inputs so the browser does not need to render thousands of `<option>` elements. Review history shown in the UI is limited to the latest 100 actions, while workbook exports retain the complete audit trail.

Staged uploads are temporary. A successful run removes its staged source copies immediately, and stale stage directories are removed automatically after 24 hours. SQLite uses foreign keys, WAL mode, a five-second busy timeout, and normal synchronous mode for safer local concurrency.

The web app binds to `127.0.0.1` by default. Requests use trusted-host checks, mutation requests reject foreign browser origins, pages are sent with `Cache-Control: no-store`, and responses include a restrictive Content Security Policy plus frame, referrer, content-type, camera, microphone, and geolocation protections.

## Human review behavior

A proposed review pair can be accepted or rejected. Accepting it keeps the original score and evidence but changes the current state to `confirmed`. Rejecting it removes the proposed pair and returns both source rows to the unmatched pool.

Two unmatched rows can be linked manually. Manual links are marked separately from algorithmic matches and do not pretend to have an algorithmic confidence score.

Every accept, reject, and manual-link action is appended to a review history with the affected rows, timestamp, previous state, resulting state, and a short explanation. The current match table can change as a reviewer works, but the human decision history remains available for audit.

## Exports

The CSV export contains current status, score, source row numbers, both primary values, every remaining source field with `a__` or `b__` prefixes, and matching evidence.

The workbook export contains:

- `Summary` with run metadata and the primary mapping;
- `Reconciliation` with the complete current reconciliation table;
- `Unmatched A` and `Unmatched B` for follow-up work;
- `Review history` with human decisions and state transitions.

Text exported to spreadsheet formats is escaped when it begins with a formula-like prefix, reducing the risk of spreadsheet formula injection from untrusted source values.

## Development

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest
```

The test suite covers typed comparison rules, candidate pruning, bounded fuzzy fallback, large exact-match sets, support-field rescue rules, one-to-one competition, CSV delimiter and encoding handling, XLSX ingestion, upload limits, stage cleanup, semantic mapping validation, review acceptance and rejection, manual links, paginated persistence queries, large unmatched workflows, safety headers, cross-origin protection, CSV/XLSX exports, and the full web flow from upload through SQLite persistence.

## Project structure

```text
src/rowbridge/
  candidates.py      candidate indexes, blocking, and bounded fuzzy fallback
  exports.py         CSV and workbook report generation
  ingestion.py       CSV/XLSX parsing, detection, validation, and staging
  matching.py        typed rule scoring and one-to-one decisions
  matching_utils.py  normalization and value parsing
  service.py         reconciliation use case
  storage.py         SQLite persistence and review mutations
  web.py             FastAPI routes
  templates/         server-rendered UI
  static/            local CSS
```

## Privacy

Files are processed on the machine running RowBridge. The matching path has no external API dependency. Source files are read-only inputs; RowBridge writes temporary staged copies, SQLite state, review history, and explicit exports under its application data directory. Staged copies are removed after a successful run and stale stages are expired automatically.

## Limitations

RowBridge is not an accounting engine, master-data-management system, or automatic data-correction tool. It currently handles one-to-one reconciliation only. XLSX imports use one automatically selected worksheet and do not evaluate spreadsheet formulas themselves; cached workbook values are read when present.

## License

MIT
