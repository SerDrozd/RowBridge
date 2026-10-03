# RowBridge

[![CI](https://github.com/SerDrozd/rowbridge/actions/workflows/ci.yml/badge.svg)](https://github.com/SerDrozd/rowbridge/actions/workflows/ci.yml)

**Local-first reconciliation for messy CSV and Excel exports.**

RowBridge helps you compare two exports when the same real-world record is represented differently on each side. It combines deterministic matching, bounded candidate generation, explainable evidence, human review, and auditable exports without sending source data to an external service.

![RowBridge results](docs/images/results.png)

## Why it exists

Operational reconciliation often starts with two spreadsheets and no reliable shared key. One file may contain `INV-00123`, while another contains `INV00123`; names vary, dates drift by a day, and numeric values differ by a few cents.

A spreadsheet lookup works until it does not. RowBridge is designed for the cases where you need to answer three questions clearly:

1. Which rows are probably the same record?
2. Why did the matcher pair them?
3. Which decisions were made by a human afterwards?

## What RowBridge does

- imports CSV and XLSX files locally;
- detects common CSV delimiters and UTF-8 / UTF-16 / Windows-1252 encodings;
- previews both sources before matching;
- maps primary, secondary text, amount, and date fields;
- generates a bounded candidate set instead of scoring the full Cartesian product;
- uses deterministic fuzzy text, numeric tolerance, and date-window rules;
- preserves one-to-one matching;
- sends ambiguous candidates to a review queue;
- supports accept, reject, and manual-link decisions;
- persists runs and review history in SQLite;
- paginates large result sets;
- exports the full current state as CSV or an XLSX workbook;
- keeps source processing local to the machine running RowBridge.

## Workflow

### 1. Load two exports

![RowBridge file selection](docs/images/home.png)

### 2. Preview and map equivalent fields

![RowBridge field mapping](docs/images/mapping.png)

### 3. Review uncertain matches

Each proposed match shows the evidence that contributed to its score. Review actions are stored separately from the original algorithmic evidence.

### 4. Export the result

CSV exports include both source payloads and matching evidence. Workbook exports include summary, reconciliation, unmatched rows, and review history sheets.

## Quick start

Requirements: Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run rowbridge
```

Then open `http://127.0.0.1:8000`.

For the included demo:

| Role | Side A | Side B |
| --- | --- | --- |
| File | `examples/orders.csv` | `examples/payments.xlsx` |
| Primary | `invoice_ref` | `reference` |
| Secondary text | `customer` | `payer` |
| Amount | `amount` | `total` |
| Date | `invoice_date` | `paid_at` |

Application state is written to `.rowbridge/` by default. Set `ROWBRIDGE_DATA_DIR` to use another directory.

## Matching model

RowBridge does not treat fuzzy matching as a black box. A match is built from typed comparison rules.

With all four rule types enabled, the default weights are:

| Signal | Weight |
| --- | ---: |
| Primary identity | 55% |
| Secondary text | 20% |
| Amount | 15% |
| Date | 10% |

If optional rules are omitted, the remaining weights are normalized.

Candidate generation uses exact normalized primary blocks first, then supporting blocks. Only rows that still lack candidates can enter a bounded fuzzy fallback. On large inputs, global fuzzy fallback is disabled rather than silently turning into an all-against-all scan.

A selected pair is sent to review when its score is below the automatic-match threshold or when a close competitor exists for either source row.

More detail: [`docs/MATCHING.md`](docs/MATCHING.md).

## Architecture

```text
CSV / XLSX
    |
    v
Ingestion + validation
    |
    v
Candidate generation ----> typed comparison rules
    |                           |
    +------------+--------------+
                 v
          one-to-one resolver
                 |
                 v
          SQLite run state
                 |
        +--------+--------+
        |                 |
        v                 v
 human review          exports
```

The application is intentionally a single local FastAPI process with server-rendered HTML and SQLite. There is no cloud database, frontend build chain, or required external API.

More detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Input handling

CSV input supports comma, semicolon, tab, and pipe delimiters. Encodings currently supported are UTF-8 with or without BOM, UTF-16, and Windows-1252.

XLSX files are opened read-only. RowBridge chooses the first worksheet containing a non-empty header and at least one data row.

Current limits per source file:

- 5 MB upload size;
- 50,000 data rows;
- 200 columns.

Duplicate headers, empty headers, malformed rows, oversized workbook archives, unsupported file types, and obviously invalid amount/date field mappings are rejected with an explicit error.

## Human review and audit trail

A review candidate can be:

- **accepted** — retains the original score and evidence, but becomes human-confirmed;
- **rejected** — returns both source rows to the unmatched pool;
- **replaced with a manual link** — records a human decision without inventing an algorithmic confidence score.

Every human mutation is appended to the review history with affected rows, timestamp, previous state, resulting state, and a short explanation.

## Exports

The CSV export contains the current status, score, source row numbers, source payloads, and matching evidence.

The workbook export contains:

- `Summary`;
- `Reconciliation`;
- `Unmatched A`;
- `Unmatched B`;
- `Review history`.

Values that begin with spreadsheet formula prefixes are escaped before export to reduce formula-injection risk.

## Local-first and security boundaries

RowBridge binds to `127.0.0.1` by default and the matching workflow has no external API dependency. Source files are treated as read-only inputs; temporary staged copies are deleted after successful run creation and stale stages expire automatically.

The web layer uses trusted-host checks, restrictive response headers, `Cache-Control: no-store`, and browser Fetch Metadata to reject cross-site mutation requests when the browser supplies that signal.

This is a local single-user tool, not an authenticated multi-user web service. Do not expose the development server directly to an untrusted network.

See [`SECURITY.md`](SECURITY.md) for the supported security model.

## Performance choices

- exact normalized identifiers take the shortest matching path;
- candidate sets are capped per source row;
- global fuzzy fallback is bounded;
- ambiguity lookup uses per-row competitor indexes;
- result pages are limited to 100 rows;
- large unmatched sets use explicit source-row inputs instead of thousands of `<option>` elements;
- SQLite runs in WAL mode with a busy timeout.

The test suite includes a 2,000-by-2,000 exact-match scenario to guard candidate-generation behavior against accidental quadratic regressions.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run mypy src tests
uv run pytest
uv build
```

CI runs on Windows and Ubuntu with Python 3.12 and 3.13.

Project layout:

```text
src/rowbridge/
  candidates.py      blocking and bounded candidate generation
  exports.py         CSV and XLSX report generation
  ingestion.py       parsing, detection, validation, staging
  matching.py        typed scoring and one-to-one resolution
  matching_utils.py  normalization and value parsing
  service.py         reconciliation use case
  storage.py         SQLite persistence and review mutations
  web.py             FastAPI routes and local web protections
  templates/         server-rendered UI
  static/            local CSS and JavaScript
```

## Limitations

RowBridge currently supports one-to-one reconciliation only. It is not an accounting engine, master-data-management platform, automatic correction system, or probabilistic ML entity-resolution framework.

XLSX import selects one worksheet automatically and reads cached values; RowBridge does not execute spreadsheet formulas.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system boundaries and design choices;
- [`docs/MATCHING.md`](docs/MATCHING.md) — candidate generation and scoring behavior;
- [`SECURITY.md`](SECURITY.md) — local security model and vulnerability reporting;
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development workflow.

## License

MIT
