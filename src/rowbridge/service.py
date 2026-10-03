from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from rowbridge.matching import reconcile
from rowbridge.matching_utils import parse_amount, parse_date
from rowbridge.models import CsvTable, FieldMapping, MatchSettings
from rowbridge.storage import Repository


def _validate_distinct_roles(mapping: FieldMapping) -> None:
    for side, columns in (
        (
            "Side A",
            (mapping.primary_a, mapping.secondary_a, mapping.amount_a, mapping.date_a),
        ),
        (
            "Side B",
            (mapping.primary_b, mapping.secondary_b, mapping.amount_b, mapping.date_b),
        ),
    ):
        selected = [column for column in columns if column]
        if len(selected) != len(set(selected)):
            raise ValueError(
                f"{side} uses the same column for more than one matching role. "
                "Choose a different column or set the optional role to Not used."
            )


def _validate_parseable_column(
    table: CsvTable,
    column: str,
    parser: Callable[[str], object | None],
    role: str,
) -> None:
    nonempty = [row[column] for row in table.rows if row[column].strip()]
    if not nonempty:
        raise ValueError(f"{table.filename}: {role} column '{column}' has no values")
    parsed = sum(parser(value) is not None for value in nonempty)
    if parsed / len(nonempty) < 0.6:
        article = "an" if role.lower() == "amount" else "a"
        raise ValueError(
            f"{table.filename}: column '{column}' does not look like {article} "
            f"{role.lower()} column. Choose a different field or set this optional role "
            "to Not used."
        )


def validate_mapping(table_a: CsvTable, table_b: CsvTable, mapping: FieldMapping) -> None:
    selections = (
        ("primary_a", mapping.primary_a, table_a.headers),
        ("primary_b", mapping.primary_b, table_b.headers),
        ("secondary_a", mapping.secondary_a, table_a.headers),
        ("secondary_b", mapping.secondary_b, table_b.headers),
        ("amount_a", mapping.amount_a, table_a.headers),
        ("amount_b", mapping.amount_b, table_b.headers),
        ("date_a", mapping.date_a, table_a.headers),
        ("date_b", mapping.date_b, table_b.headers),
    )
    for label, column, headers in selections:
        if column is not None and column not in headers:
            raise ValueError(f"Unknown column selected for {label}: {column}")

    if bool(mapping.secondary_a) != bool(mapping.secondary_b):
        raise ValueError("Select secondary text columns on both sides or neither side")
    if bool(mapping.amount_a) != bool(mapping.amount_b):
        raise ValueError("Select amount columns on both sides or neither side")
    if bool(mapping.date_a) != bool(mapping.date_b):
        raise ValueError("Select date columns on both sides or neither side")

    _validate_distinct_roles(mapping)

    if mapping.amount_a and mapping.amount_b:
        _validate_parseable_column(table_a, mapping.amount_a, parse_amount, "Amount")
        _validate_parseable_column(table_b, mapping.amount_b, parse_amount, "Amount")
    if mapping.date_a and mapping.date_b:
        _validate_parseable_column(table_a, mapping.date_a, parse_date, "Date")
        _validate_parseable_column(table_b, mapping.date_b, parse_date, "Date")


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
