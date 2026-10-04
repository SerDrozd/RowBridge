from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rowbridge.models import (
    Evidence,
    FieldMapping,
    MatchDecision,
    MatchSettings,
    MatchStatus,
    ReviewAction,
)
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


def _save_review_run(
    repository: Repository,
    run_id: str,
    *,
    extra_unmatched_b: bool = False,
) -> None:
    rows_b = ({"id": "B-1"}, {"id": "B-2"}) if extra_unmatched_b else ({"id": "B-1"},)
    decisions = [
        MatchDecision(
            0,
            0,
            0.81,
            MatchStatus.REVIEW,
            (Evidence(field="primary", detail="A-1 vs B-1", score=0.81),),
        )
    ]
    if extra_unmatched_b:
        decisions.append(MatchDecision(None, 1, 0.0, MatchStatus.UNMATCHED, ()))

    repository.save_run(
        run_id=run_id,
        created_at="2026-10-04T12:30:00+00:00",
        filename_a="a.csv",
        filename_b="b.csv",
        mapping=FieldMapping(primary_a="id", primary_b="id"),
        settings=MatchSettings(),
        rows_a=({"id": "A-1"},),
        rows_b=rows_b,
        decisions=tuple(decisions),
    )


def test_initialize_migrates_legacy_review_events_with_evidence_column(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                action TEXT NOT NULL,
                source_match_id INTEGER,
                a_row_id INTEGER,
                b_row_id INTEGER,
                previous_status TEXT,
                resulting_status TEXT NOT NULL,
                score REAL,
                detail TEXT NOT NULL
            )
            """
        )

    repository = Repository(database_path)
    repository.initialize()
    repository.initialize()

    with sqlite3.connect(database_path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(review_events)").fetchall()
        }

    assert "evidence_json" in columns


def test_confirmed_match_can_be_reopened_without_losing_evidence(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run_id = "1" * 32
    _save_review_run(repository, run_id)

    review = repository.list_matches(run_id, (MatchStatus.REVIEW,))[0]
    original_evidence = review.evidence
    repository.accept_review(run_id, review.id)
    repository.reopen_confirmed(run_id, review.id)

    reopened = repository.list_matches(run_id, (MatchStatus.REVIEW,))[0]
    summary = repository.get_summary(run_id)
    events = repository.list_review_events(run_id)

    assert reopened.id == review.id
    assert reopened.score == review.score
    assert reopened.evidence == original_evidence
    assert summary.review == 1
    assert summary.human_matched == 0
    assert [event.action for event in events[:2]] == [
        ReviewAction.REOPEN,
        ReviewAction.ACCEPT,
    ]


def test_manual_match_can_be_unlinked_back_to_two_unmatched_rows(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run_id = "2" * 32
    repository.save_run(
        run_id=run_id,
        created_at="2026-10-04T12:31:00+00:00",
        filename_a="a.csv",
        filename_b="b.csv",
        mapping=FieldMapping(primary_a="id", primary_b="id"),
        settings=MatchSettings(),
        rows_a=({"id": "A-1"},),
        rows_b=({"id": "B-1"},),
        decisions=(
            MatchDecision(0, None, 0.0, MatchStatus.UNMATCHED, ()),
            MatchDecision(None, 0, 0.0, MatchStatus.UNMATCHED, ()),
        ),
    )
    a_match = repository.list_unmatched_side(run_id, "a", limit=1)[0]
    b_match = repository.list_unmatched_side(run_id, "b", limit=1)[0]
    repository.create_manual_link(run_id, a_match.id, b_match.id)

    manual = repository.list_matches(run_id, (MatchStatus.MANUAL_MATCHED,))[0]
    repository.unlink_manual(run_id, manual.id)

    summary = repository.get_summary(run_id)
    events = repository.list_review_events(run_id)

    assert repository.count_unmatched_side(run_id, "a") == 1
    assert repository.count_unmatched_side(run_id, "b") == 1
    assert summary.unmatched == 2
    assert summary.human_matched == 0
    assert [event.action for event in events[:2]] == [
        ReviewAction.UNLINK,
        ReviewAction.MANUAL_LINK,
    ]


def test_rejected_proposal_can_be_restored_with_original_score_and_evidence(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    run_id = "3" * 32
    _save_review_run(repository, run_id)

    original = repository.list_matches(run_id, (MatchStatus.REVIEW,))[0]
    repository.reject_review(run_id, original.id)
    rejected = repository.list_review_events(run_id)[0]

    assert rejected.action == ReviewAction.REJECT
    assert rejected.id in repository.restorable_rejected_event_ids(run_id)

    repository.restore_rejected(run_id, rejected.id)

    restored = repository.list_matches(run_id, (MatchStatus.REVIEW,))[0]
    summary = repository.get_summary(run_id)
    events = repository.list_review_events(run_id)

    assert restored.score == original.score
    assert restored.evidence == original.evidence
    assert summary.review == 1
    assert summary.unmatched == 0
    assert repository.restorable_rejected_event_ids(run_id) == frozenset()
    assert [event.action for event in events[:2]] == [
        ReviewAction.RESTORE_REJECTED,
        ReviewAction.REJECT,
    ]


def test_rejected_proposal_cannot_be_restored_after_a_row_is_reused(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    run_id = "4" * 32
    _save_review_run(repository, run_id, extra_unmatched_b=True)

    review = repository.list_matches(run_id, (MatchStatus.REVIEW,))[0]
    repository.reject_review(run_id, review.id)
    rejected = repository.list_review_events(run_id)[0]

    a_match = repository.get_unmatched_match_by_row(run_id, "a", 2)
    other_b_match = repository.get_unmatched_match_by_row(run_id, "b", 3)
    assert a_match is not None
    assert other_b_match is not None
    repository.create_manual_link(run_id, a_match.id, other_b_match.id)

    assert rejected.id not in repository.restorable_rejected_event_ids(run_id)
    with pytest.raises(ValueError, match="both rows are unmatched"):
        repository.restore_rejected(run_id, rejected.id)

    assert repository.count_matches(run_id, (MatchStatus.MANUAL_MATCHED,)) == 1
    assert repository.count_unmatched_side(run_id, "b") == 1


def test_only_latest_rejection_for_same_pair_can_be_restored(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run_id = "5" * 32
    _save_review_run(repository, run_id)

    first_review = repository.list_matches(run_id, (MatchStatus.REVIEW,))[0]
    repository.reject_review(run_id, first_review.id)
    first_rejection = repository.list_review_events(run_id)[0]
    repository.restore_rejected(run_id, first_rejection.id)

    restored_review = repository.list_matches(run_id, (MatchStatus.REVIEW,))[0]
    repository.reject_review(run_id, restored_review.id)
    events = repository.list_review_events(run_id)
    rejection_events = [
        event for event in events if event.action == ReviewAction.REJECT
    ]
    second_rejection = rejection_events[0]

    assert second_rejection.id != first_rejection.id
    assert repository.restorable_rejected_event_ids(run_id) == frozenset(
        {second_rejection.id}
    )
    with pytest.raises(ValueError, match="newer rejection"):
        repository.restore_rejected(run_id, first_rejection.id)
