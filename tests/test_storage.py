from __future__ import annotations

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
