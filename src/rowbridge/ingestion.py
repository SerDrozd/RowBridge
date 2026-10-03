from __future__ import annotations

import csv
import io
from pathlib import Path

from rowbridge.models import CsvTable

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000


class CsvInputError(ValueError):
    pass


def parse_csv_bytes(content: bytes, filename: str) -> CsvTable:
    if not content:
        raise CsvInputError(f"{filename} is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise CsvInputError(f"{filename} is larger than 5 MB")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvInputError(f"{filename} must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CsvInputError(f"{filename} has no header row")

    headers = tuple(header.strip() for header in reader.fieldnames)
    if any(not header for header in headers):
        raise CsvInputError(f"{filename} contains an empty column name")
    if len(set(headers)) != len(headers):
        raise CsvInputError(f"{filename} contains duplicate column names")

    rows: list[dict[str, str]] = []
    for row_number, raw_row in enumerate(reader, start=2):
        if row_number - 1 > MAX_ROWS:
            raise CsvInputError(f"{filename} exceeds the {MAX_ROWS:,} row limit")
        normalized = {header: (raw_row.get(header) or "").strip() for header in headers}
        rows.append(normalized)

    if not rows:
        raise CsvInputError(f"{filename} has no data rows")
    return CsvTable(filename=filename, headers=headers, rows=tuple(rows))


def write_staged_csv(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def read_staged_csv(path: Path) -> CsvTable:
    if not path.is_file():
        raise CsvInputError("Staged upload was not found. Upload the files again.")
    return parse_csv_bytes(path.read_bytes(), path.name)
