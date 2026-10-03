from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rowbridge.matching import reconcile
from rowbridge.models import CsvTable, FieldMapping, MatchSettings
from rowbridge.storage import Repository


def validate_mapping(table_a: CsvTable, table_b: CsvTable, mapping: FieldMapping) -> None:
    selections = (
        ("primary_a", mapping.primary_a, table_a.headers),
        ("primary_b", mapping.primary_b, table_b.headers),
        ("amount_a", mapping.amount_a, table_a.headers),
        ("amount_b", mapping.amount_b, table_b.headers),
        ("date_a", mapping.date_a, table_a.headers),
        ("date_b", mapping.date_b, table_b.headers),
    )
    for label, column, headers in selections:
        if column is not None and column not in headers:
            raise ValueError(f"Unknown column selected for {label}: {column}")

    if bool(mapping.amount_a) != bool(mapping.amount_b):
        raise ValueError("Select amount columns on both sides or neither side")
    if bool(mapping.date_a) != bool(mapping.date_b):
        raise ValueError("Select date columns on both sides or neither side")


def create_reconciliation_run(
    repository: Repository,
    table_a: CsvTable,
    table_b: CsvTable,
    mapping: FieldMapping,
    settings: MatchSettings,
) -> str:
    validate_mapping(table_a, table_b, mapping)
    decisions = reconcile(table_a, table_b, mapping, settings)
    run_id = uuid4().hex
    repository.save_run(
        run_id=run_id,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        filename_a=table_a.filename,
        filename_b=table_b.filename,
        mapping=mapping,
        settings=settings,
        rows_a=table_a.rows,
        rows_b=table_b.rows,
        decisions=decisions,
    )
    return run_id
