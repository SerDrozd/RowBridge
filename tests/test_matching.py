from rowbridge.matching import normalize_text, reconcile, score_pair
from rowbridge.models import CsvTable, FieldMapping, MatchSettings, MatchStatus


def test_normalize_text_removes_case_spacing_and_punctuation() -> None:
    assert normalize_text(" INV-00123 ") == "inv00123"
    assert normalize_text("ACME, Ltd.") == "acmeltd"


def test_score_pair_uses_primary_amount_and_date_evidence() -> None:
    mapping = FieldMapping(
        primary_a="ref",
        primary_b="reference",
        amount_a="amount",
        amount_b="total",
        date_a="date",
        date_b="paid",
    )
    score, evidence = score_pair(
        {"ref": "INV-123", "amount": "10.00", "date": "2026-10-01"},
        {"reference": "INV123", "total": "10.02", "paid": "2026-10-02"},
        mapping,
        MatchSettings(),
    )
    assert score == 1.0
    assert [item.field for item in evidence] == ["primary", "amount", "date"]


def test_reconcile_keeps_side_b_one_to_one() -> None:
    table_a = CsvTable(
        filename="a.csv",
        headers=("name",),
        rows=({"name": "Alpha"}, {"name": "Alpha"}),
    )
    table_b = CsvTable(filename="b.csv", headers=("name",), rows=({"name": "Alpha"},))
    decisions = reconcile(
        table_a,
        table_b,
        FieldMapping(primary_a="name", primary_b="name"),
        MatchSettings(review_threshold=0.5, auto_threshold=0.6),
    )
    paired = [decision for decision in decisions if decision.b_index is not None]
    unmatched_a = [
        decision
        for decision in decisions
        if decision.a_index is not None and decision.status == MatchStatus.UNMATCHED
    ]
    assert len(paired) == 1
    assert len(unmatched_a) == 1


def test_primary_only_mapping_can_auto_match_exact_values() -> None:
    table_a = CsvTable(filename="a.csv", headers=("name",), rows=({"name": "Acme Ltd"},))
    table_b = CsvTable(filename="b.csv", headers=("name",), rows=({"name": "ACME LTD"},))
    decisions = reconcile(
        table_a,
        table_b,
        FieldMapping(primary_a="name", primary_b="name"),
        MatchSettings(),
    )
    assert len(decisions) == 1
    assert decisions[0].status == MatchStatus.AUTO_MATCHED
    assert decisions[0].score == 1.0
