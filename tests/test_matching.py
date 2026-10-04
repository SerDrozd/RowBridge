from decimal import Decimal
from unittest.mock import patch

from rowbridge.candidates import generate_candidates
from rowbridge.matching import (
    build_rules,
    reconcile,
    reconcile_with_diagnostics,
    score_pair,
)
from rowbridge.matching_utils import normalize_text, parse_amount
from rowbridge.models import (
    CandidateGeneration,
    CandidateScore,
    FieldMapping,
    InputTable,
    MatchSettings,
    MatchStatus,
    RuleKind,
)


def test_normalize_text_removes_case_spacing_and_punctuation() -> None:
    assert normalize_text(" INV-00123 ") == "inv00123"
    assert normalize_text("ACME, Ltd.") == "acmeltd"


def test_normalize_text_preserves_unicode_letters() -> None:
    assert normalize_text(
        "\u0422\u041e\u0412 \u00ab\u041a\u0438\u0457\u0432-2026\u00bb"
    ) == "\u0442\u043e\u0432\u043a\u0438\u0457\u04322026"
    assert normalize_text(
        "Caf\u00e9, S\u00e3o Paulo!"
    ) == "caf\u00e9s\u00e3opaulo"
    assert normalize_text("Cafe\u0301") == "caf\u00e9"


def test_reconcile_matches_cyrillic_primary_values() -> None:
    table_a = InputTable(
        filename="a.csv",
        headers=("name",),
        rows=(
            {
                "name": (
                    "\u0422\u041e\u0412 "
                    "\u00ab\u0420\u043e\u043c\u0430\u0448\u043a\u0430\u00bb"
                )
            },
        ),
    )
    table_b = InputTable(
        filename="b.csv",
        headers=("name",),
        rows=(
            {
                "name": (
                    "\u0442\u043e\u0432 "
                    "\u0440\u043e\u043c\u0430\u0448\u043a\u0430"
                )
            },
        ),
    )

    decisions = reconcile(
        table_a,
        table_b,
        FieldMapping(primary_a="name", primary_b="name"),
        MatchSettings(),
    )

    assert len(decisions) == 1
    assert decisions[0].status == MatchStatus.AUTO_MATCHED
    assert decisions[0].score == 1.0


def test_parse_amount_supports_common_decimal_and_grouping_formats() -> None:
    assert parse_amount("1234.56") == Decimal("1234.56")
    assert parse_amount("1234,56") == Decimal("1234.56")
    assert parse_amount("1,234.56") == Decimal("1234.56")
    assert parse_amount("1.234,56") == Decimal("1234.56")
    assert parse_amount("12,345,678.90") == Decimal("12345678.90")
    assert parse_amount("12.345.678,90") == Decimal("12345678.90")


def test_parse_amount_preserves_accounting_parentheses_as_negative() -> None:
    assert parse_amount("(1,234.56)") == Decimal("-1234.56")
    assert parse_amount("(1.234,56)") == Decimal("-1234.56")
    assert parse_amount("($1,234.56)") == Decimal("-1234.56")
    assert parse_amount("-(1,234.56)") is None


def test_score_pair_matches_amounts_with_different_locale_separators() -> None:
    mapping = FieldMapping(
        primary_a="ref",
        primary_b="reference",
        amount_a="amount",
        amount_b="total",
    )

    score, evidence = score_pair(
        {"ref": "INV-001", "amount": "1.234,56"},
        {"reference": "INV001", "total": "1,234.56"},
        mapping,
        MatchSettings(),
    )

    assert score == 1.0
    amount_evidence = next(item for item in evidence if item.field == "amount")
    assert "difference 0.00" in amount_evidence.detail


def test_score_pair_accepts_excel_datetime_text_for_date_rule() -> None:
    mapping = FieldMapping(
        primary_a="ref",
        primary_b="reference",
        date_a="date",
        date_b="paid_at",
    )

    score, evidence = score_pair(
        {"ref": "INV-001", "date": "2026-10-02"},
        {"reference": "INV001", "paid_at": "2026-10-02 00:00:00"},
        mapping,
        MatchSettings(),
    )

    assert score == 1.0
    date_evidence = next(item for item in evidence if item.field == "date")
    assert "difference 0 day(s)" in date_evidence.detail


def test_build_rules_uses_typed_comparators_and_expected_weights() -> None:
    mapping = FieldMapping(
        primary_a="ref",
        primary_b="reference",
        secondary_a="customer",
        secondary_b="payer",
        amount_a="amount",
        amount_b="total",
        date_a="date",
        date_b="paid",
    )
    rules = build_rules(mapping, MatchSettings())

    assert [rule.kind for rule in rules] == [
        RuleKind.FUZZY_TEXT,
        RuleKind.FUZZY_TEXT,
        RuleKind.NUMERIC_TOLERANCE,
        RuleKind.DATE_WINDOW,
    ]
    assert sum(rule.weight for rule in rules) == 1.0


def test_optional_rules_keep_base_weights_before_normalization() -> None:
    mapping = FieldMapping(
        primary_a="ref",
        primary_b="reference",
        amount_a="amount",
        amount_b="total",
        date_a="date",
        date_b="paid",
    )

    rules = build_rules(mapping, MatchSettings())

    assert [(rule.field, rule.weight) for rule in rules] == [
        ("primary", 0.55),
        ("amount", 0.15),
        ("date", 0.10),
    ]


def test_score_pair_normalizes_remaining_rule_weights() -> None:
    mapping = FieldMapping(
        primary_a="ref",
        primary_b="reference",
        amount_a="amount",
        amount_b="total",
    )

    score, evidence = score_pair(
        {"ref": "INV-001", "amount": "10.00"},
        {"reference": "INV001", "total": "11.00"},
        mapping,
        MatchSettings(),
    )

    assert score == 0.785714
    assert [item.score for item in evidence] == [0.785714, 0.0]


def test_score_pair_uses_primary_secondary_amount_and_date_evidence() -> None:
    mapping = FieldMapping(
        primary_a="ref",
        primary_b="reference",
        secondary_a="customer",
        secondary_b="payer",
        amount_a="amount",
        amount_b="total",
        date_a="date",
        date_b="paid",
    )
    score, evidence = score_pair(
        {"ref": "INV-123", "customer": "Acme Ltd", "amount": "10.00", "date": "2026-10-01"},
        {"reference": "INV123", "payer": "ACME LTD", "total": "10.02", "paid": "2026-10-02"},
        mapping,
        MatchSettings(),
    )
    assert score == 1.0
    assert [item.field for item in evidence] == ["primary", "secondary", "amount", "date"]


def test_candidate_generation_avoids_cartesian_product_for_exact_ids() -> None:
    row_count = 200
    table_a = InputTable(
        filename="a.csv",
        headers=("id",),
        rows=tuple({"id": f"ITEM-{index:05d}"} for index in range(row_count)),
    )
    table_b = InputTable(
        filename="b.csv",
        headers=("id",),
        rows=tuple({"id": f"ITEM{index:05d}"} for index in range(row_count)),
    )

    generation = generate_candidates(
        table_a,
        table_b,
        FieldMapping(primary_a="id", primary_b="id"),
        MatchSettings(),
    )

    assert generation.possible_pairs == 40_000
    assert generation.generated_pairs == 200
    assert generation.fallback_rows == 0


def test_support_fields_do_not_rescue_unrelated_primary_without_secondary_text() -> None:
    table_a = InputTable(
        filename="a.csv",
        headers=("ref", "amount", "date"),
        rows=({"ref": "INV-00126", "amount": "460.00", "date": "2026-09-30"},),
    )
    table_b = InputTable(
        filename="b.csv",
        headers=("ref", "amount", "date"),
        rows=({"ref": "INV-00999", "amount": "460.00", "date": "2026-09-30"},),
    )
    mapping = FieldMapping(
        primary_a="ref",
        primary_b="ref",
        amount_a="amount",
        amount_b="amount",
        date_a="date",
        date_b="date",
    )

    decisions = reconcile(table_a, table_b, mapping, MatchSettings())

    assert all(decision.status == MatchStatus.UNMATCHED for decision in decisions)


def test_secondary_text_can_rescue_mismatched_reference_for_review() -> None:
    table_a = InputTable(
        filename="a.csv",
        headers=("ref", "customer", "amount", "date"),
        rows=(
            {
                "ref": "INV-00126",
                "customer": "Blue Finch GmbH",
                "amount": "460.00",
                "date": "2026-09-30",
            },
        ),
    )
    table_b = InputTable(
        filename="b.csv",
        headers=("ref", "payer", "amount", "date"),
        rows=(
            {
                "ref": "INV-00999",
                "payer": "Blue Finch GmbH",
                "amount": "460.00",
                "date": "2026-09-30",
            },
        ),
    )
    mapping = FieldMapping(
        primary_a="ref",
        primary_b="ref",
        secondary_a="customer",
        secondary_b="payer",
        amount_a="amount",
        amount_b="amount",
        date_a="date",
        date_b="date",
    )

    decisions, generation = reconcile_with_diagnostics(table_a, table_b, mapping, MatchSettings())

    assert generation.generated_pairs == 1
    assert len(decisions) == 1
    assert decisions[0].status == MatchStatus.REVIEW
    assert 0.75 < decisions[0].score < 0.85


def test_fallback_recovers_candidate_when_primary_blocks_do_not_overlap() -> None:
    table_a = InputTable(filename="a.csv", headers=("id",), rows=({"id": "XNV0012Z"},))
    table_b = InputTable(filename="b.csv", headers=("id",), rows=({"id": "INV00123"},))
    settings = MatchSettings(review_threshold=0.5, minimum_primary_similarity=0.5)

    generation = generate_candidates(
        table_a,
        table_b,
        FieldMapping(primary_a="id", primary_b="id"),
        settings,
    )

    assert generation.by_a == ((0,),)
    assert generation.fallback_rows == 1


def test_reconcile_keeps_side_b_one_to_one_and_marks_competition_for_review() -> None:
    table_a = InputTable(
        filename="a.csv",
        headers=("name",),
        rows=({"name": "Alpha"}, {"name": "Alpha"}),
    )
    table_b = InputTable(filename="b.csv", headers=("name",), rows=({"name": "Alpha"},))
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
    assert paired[0].status == MatchStatus.REVIEW
    assert len(unmatched_a) == 1


def test_score_ordered_resolver_is_greedy_not_global_assignment() -> None:
    table_a = InputTable(
        filename="a.csv",
        headers=("id",),
        rows=({"id": "A0"}, {"id": "A1"}),
    )
    table_b = InputTable(
        filename="b.csv",
        headers=("id",),
        rows=({"id": "B0"}, {"id": "B1"}),
    )
    generation = CandidateGeneration(
        by_a=((0, 1), (0,)),
        possible_pairs=4,
        generated_pairs=3,
        fallback_rows=0,
    )
    scored = (
        CandidateScore(a_index=0, b_index=0, score=0.95, evidence=()),
        CandidateScore(a_index=0, b_index=1, score=0.94, evidence=()),
        CandidateScore(a_index=1, b_index=0, score=0.93, evidence=()),
    )

    with (
        patch("rowbridge.matching.generate_candidates", return_value=generation),
        patch("rowbridge.matching._score_candidates", return_value=scored),
    ):
        decisions, _ = reconcile_with_diagnostics(
            table_a,
            table_b,
            FieldMapping(primary_a="id", primary_b="id"),
            MatchSettings(),
        )

    paired = [
        (decision.a_index, decision.b_index)
        for decision in decisions
        if decision.a_index is not None and decision.b_index is not None
    ]

    assert paired == [(0, 0)]
    assert scored[1].score + scored[2].score > scored[0].score


def test_primary_only_mapping_can_auto_match_exact_values() -> None:
    table_a = InputTable(filename="a.csv", headers=("name",), rows=({"name": "Acme Ltd"},))
    table_b = InputTable(filename="b.csv", headers=("name",), rows=({"name": "ACME LTD"},))
    decisions = reconcile(
        table_a,
        table_b,
        FieldMapping(primary_a="name", primary_b="name"),
        MatchSettings(),
    )
    assert len(decisions) == 1
    assert decisions[0].status == MatchStatus.AUTO_MATCHED
    assert decisions[0].score == 1.0


def test_large_inputs_skip_unbounded_global_fuzzy_fallback() -> None:
    table_a = InputTable(filename="a.csv", headers=("id",), rows=({"id": "NO-MATCH-HERE"},))
    table_b = InputTable(
        filename="b.csv",
        headers=("id",),
        rows=tuple({"id": f"B-{index:05d}"} for index in range(5_001)),
    )
    settings = MatchSettings(fallback_scan_limit=5_000)

    generation = generate_candidates(
        table_a,
        table_b,
        FieldMapping(primary_a="id", primary_b="id"),
        settings,
    )

    assert generation.by_a == ((),)
    assert generation.fallback_rows == 0


def test_reconcile_scales_to_thousands_of_exact_one_to_one_candidates() -> None:
    row_count = 2_000
    table_a = InputTable(
        filename="a.csv",
        headers=("id",),
        rows=tuple({"id": f"ITEM-{index:05d}"} for index in range(row_count)),
    )
    table_b = InputTable(
        filename="b.csv",
        headers=("id",),
        rows=tuple({"id": f"ITEM{index:05d}"} for index in range(row_count)),
    )

    decisions = reconcile(
        table_a,
        table_b,
        FieldMapping(primary_a="id", primary_b="id"),
        MatchSettings(),
    )

    assert len(decisions) == row_count
    assert all(decision.status == MatchStatus.AUTO_MATCHED for decision in decisions)
