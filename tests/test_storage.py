from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from rowbridge.models import FieldMapping, MatchDecision, MatchSettings, MatchStatus
from rowbridge.storage import Repository


def make_repository(tmp_path: Path) -> Repository:
    repository = Repository(tmp_path / "rowbridge.sqlite3")
    repository.initialize()
    return repository


def test_match_pagination_and_status_counts_are_bounded(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    rows_a = tuple({"id": f"A-{index:03d}"} for index in range(125))
    rows_b = tuple({"id": f"B-{index:03d}"} for index in range(125))
    decisions = tuple(
        MatchDecision(
            a_index=index,
            b_index=index,
            score=1.0,
            status=MatchStatus.AUTO_MATCHED,
            evidence=(),
        )
        for index in range(125)
    )
    repository.save_run(
        run_id="a" * 32,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        filename_a="a.csv",
        filename_b="b.csv",
        mapping=FieldMapping(primary_a="id", primary_b="id"),
        settings=MatchSettings(),
        rows_a=rows_a,
        rows_b=rows_b,
        decisions=decisions,
    )

    assert repository.count_matches("a" * 32) == 125
    first_page = repository.list_matches("a" * 32, limit=100, offset=0)
    second_page = repository.list_matches("a" * 32, limit=100, offset=100)

    assert len(first_page) == 100
    assert len(second_page) == 25
    assert first_page[0].a_row_number == 2
    assert second_page[0].a_row_number == 102


def test_unmatched_lookup_by_source_row_supports_large_manual_link_workflows(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    rows_a = tuple({"id": f"A-{index:03d}"} for index in range(3))
    rows_b = tuple({"id": f"B-{index:03d}"} for index in range(3))
    decisions = (
        MatchDecision(0, None, 0.0, MatchStatus.UNMATCHED, ()),
        MatchDecision(1, None, 0.0, MatchStatus.UNMATCHED, ()),
        MatchDecision(None, 0, 0.0, MatchStatus.UNMATCHED, ()),
        MatchDecision(None, 1, 0.0, MatchStatus.UNMATCHED, ()),
    )
    repository.save_run(
        run_id="b" * 32,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        filename_a="a.csv",
        filename_b="b.csv",
        mapping=FieldMapping(primary_a="id", primary_b="id"),
        settings=MatchSettings(),
        rows_a=rows_a,
        rows_b=rows_b,
        decisions=decisions,
    )

    a_match = repository.get_unmatched_match_by_row("b" * 32, "a", 3)
    b_match = repository.get_unmatched_match_by_row("b" * 32, "b", 2)

    assert a_match is not None
    assert b_match is not None
    assert a_match.a_payload == {"id": "A-001"}
    assert b_match.b_payload == {"id": "B-000"}


def test_run_history_is_newest_first_and_uses_current_status_counts(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    repository.save_run(
        run_id="c" * 32,
        created_at="2026-10-04T09:00:00+00:00",
        filename_a="orders.csv",
        filename_b="payments.csv",
        mapping=FieldMapping(primary_a="id", primary_b="id"),
        settings=MatchSettings(),
        rows_a=({"id": "A-1"}, {"id": "A-2"}),
        rows_b=({"id": "B-1"}, {"id": "B-2"}),
        decisions=(
            MatchDecision(0, 0, 1.0, MatchStatus.AUTO_MATCHED, ()),
            MatchDecision(1, None, 0.0, MatchStatus.UNMATCHED, ()),
            MatchDecision(None, 1, 0.0, MatchStatus.UNMATCHED, ()),
        ),
    )
    repository.save_run(
        run_id="d" * 32,
        created_at="2026-10-04T10:00:00+00:00",
        filename_a="ledger.csv",
        filename_b="bank.xlsx",
        mapping=FieldMapping(primary_a="id", primary_b="id"),
        settings=MatchSettings(),
        rows_a=({"id": "A-9"},),
        rows_b=({"id": "B-9"},),
        decisions=(MatchDecision(0, 0, 0.8, MatchStatus.REVIEW, ()),),
    )

    review_match = repository.list_matches(
        "d" * 32,
        statuses=(MatchStatus.REVIEW,),
        limit=1,
    )[0]
    repository.accept_review("d" * 32, review_match.id)

    assert repository.count_runs() == 2

    first_page = repository.list_runs(limit=1)
    second_page = repository.list_runs(limit=1, offset=1)

    assert [item.id for item in first_page] == ["d" * 32]
    assert [item.id for item in second_page] == ["c" * 32]

    newest = first_page[0]
    assert newest.filename_a == "ledger.csv"
    assert newest.filename_b == "bank.xlsx"
    assert newest.total_a == 1
    assert newest.total_b == 1
    assert newest.auto_matched == 0
    assert newest.human_matched == 1
    assert newest.review == 0
    assert newest.unmatched == 0

    older = second_page[0]
    assert older.auto_matched == 1
    assert older.human_matched == 0
    assert older.review == 0
    assert older.unmatched == 2


def test_delete_run_cascades_children_without_touching_other_runs(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    deleted_run_id = "e" * 32
    kept_run_id = "f" * 32

    repository.save_run(
        run_id=deleted_run_id,
        created_at="2026-10-04T11:00:00+00:00",
        filename_a="delete-a.csv",
        filename_b="delete-b.csv",
        mapping=FieldMapping(primary_a="id", primary_b="id"),
        settings=MatchSettings(),
        rows_a=({"id": "A-1"},),
        rows_b=({"id": "B-1"},),
        decisions=(MatchDecision(0, 0, 0.8, MatchStatus.REVIEW, ()),),
    )
    review_match = repository.list_matches(
        deleted_run_id,
        statuses=(MatchStatus.REVIEW,),
        limit=1,
    )[0]
    repository.accept_review(deleted_run_id, review_match.id)

    repository.save_run(
        run_id=kept_run_id,
        created_at="2026-10-04T12:00:00+00:00",
        filename_a="keep-a.csv",
        filename_b="keep-b.csv",
        mapping=FieldMapping(primary_a="id", primary_b="id"),
        settings=MatchSettings(),
        rows_a=({"id": "A-2"},),
        rows_b=({"id": "B-2"},),
        decisions=(MatchDecision(0, 0, 1.0, MatchStatus.AUTO_MATCHED, ()),),
    )

    assert repository.delete_run(deleted_run_id) is True
    assert repository.delete_run(deleted_run_id) is False
    assert repository.get_run(deleted_run_id) is None
    assert repository.get_run(kept_run_id) is not None

    with sqlite3.connect(repository.database_path) as connection:
        for table in ("source_rows", "matches", "review_events"):
            deleted_count = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id = ?",
                (deleted_run_id,),
            ).fetchone()
            assert deleted_count is not None
            assert deleted_count[0] == 0

        kept_source_rows = connection.execute(
            "SELECT COUNT(*) FROM source_rows WHERE run_id = ?",
            (kept_run_id,),
        ).fetchone()
        kept_matches = connection.execute(
            "SELECT COUNT(*) FROM matches WHERE run_id = ?",
            (kept_run_id,),
        ).fetchone()

    assert kept_source_rows is not None
    assert kept_matches is not None
    assert kept_source_rows[0] == 2
    assert kept_matches[0] == 1
