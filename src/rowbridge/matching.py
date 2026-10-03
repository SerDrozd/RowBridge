from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rapidfuzz.fuzz import WRatio

from rowbridge.candidates import generate_candidates
from rowbridge.matching_utils import normalize_text, parse_amount, parse_date
from rowbridge.models import (
    CandidateGeneration,
    CandidateScore,
    ComparisonRule,
    CsvTable,
    Evidence,
    FieldMapping,
    MatchDecision,
    MatchSettings,
    MatchStatus,
    RuleKind,
)


@dataclass(frozen=True, slots=True)
class _RuleResult:
    field: str
    raw_score: float
    weighted_score: float
    weight: float
    detail: str


def build_rules(mapping: FieldMapping, settings: MatchSettings) -> tuple[ComparisonRule, ...]:
    has_secondary = bool(mapping.secondary_a and mapping.secondary_b)
    primary_weight = 0.55 if has_secondary else 0.70
    amount_weight = 0.15 if has_secondary else 0.20

    rules = [
        ComparisonRule(
            field="primary",
            column_a=mapping.primary_a,
            column_b=mapping.primary_b,
            kind=RuleKind.FUZZY_TEXT,
            weight=primary_weight,
        )
    ]
    if mapping.secondary_a and mapping.secondary_b:
        rules.append(
            ComparisonRule(
                field="secondary",
                column_a=mapping.secondary_a,
                column_b=mapping.secondary_b,
                kind=RuleKind.FUZZY_TEXT,
                weight=0.20,
            )
        )
    if mapping.amount_a and mapping.amount_b:
        rules.append(
            ComparisonRule(
                field="amount",
                column_a=mapping.amount_a,
                column_b=mapping.amount_b,
                kind=RuleKind.NUMERIC_TOLERANCE,
                weight=amount_weight,
                tolerance=settings.amount_tolerance,
            )
        )
    if mapping.date_a and mapping.date_b:
        rules.append(
            ComparisonRule(
                field="date",
                column_a=mapping.date_a,
                column_b=mapping.date_b,
                kind=RuleKind.DATE_WINDOW,
                weight=0.10,
                window_days=settings.date_window_days,
            )
        )
    return tuple(rules)


def _evaluate_rule(
    row_a: dict[str, str],
    row_b: dict[str, str],
    rule: ComparisonRule,
) -> _RuleResult:
    value_a = row_a[rule.column_a]
    value_b = row_b[rule.column_b]

    if rule.kind == RuleKind.FUZZY_TEXT:
        normalized_a = normalize_text(value_a)
        normalized_b = normalize_text(value_b)
        raw_score = (
            WRatio(normalized_a, normalized_b) / 100 if normalized_a and normalized_b else 0.0
        )
        detail = f'"{value_a}" vs "{value_b}" similarity {raw_score:.0%}'
    elif rule.kind == RuleKind.NUMERIC_TOLERANCE:
        amount_a = parse_amount(value_a)
        amount_b = parse_amount(value_b)
        tolerance = Decimal(str(rule.tolerance or 0.0))
        raw_score = 0.0
        if amount_a is not None and amount_b is not None:
            difference = abs(amount_a - amount_b)
            raw_score = 1.0 if difference <= tolerance else 0.0
            detail = f"difference {difference}; tolerance {tolerance}"
        else:
            detail = "could not parse both amounts"
    elif rule.kind == RuleKind.DATE_WINDOW:
        date_a = parse_date(value_a)
        date_b = parse_date(value_b)
        window_days = rule.window_days or 0
        raw_score = 0.0
        if date_a is not None and date_b is not None:
            day_difference = abs((date_a - date_b).days)
            raw_score = 1.0 if day_difference <= window_days else 0.0
            detail = f"difference {day_difference} day(s); window {window_days}"
        else:
            detail = "could not parse both dates"
    else:
        raise ValueError(f"Unsupported comparison rule: {rule.kind}")

    return _RuleResult(
        field=rule.field,
        raw_score=raw_score,
        weighted_score=raw_score * rule.weight,
        weight=rule.weight,
        detail=detail,
    )


def _evaluate_pair(
    row_a: dict[str, str],
    row_b: dict[str, str],
    rules: tuple[ComparisonRule, ...],
) -> tuple[float, tuple[Evidence, ...], dict[str, float]]:
    results = tuple(_evaluate_rule(row_a, row_b, rule) for rule in rules)
    available_weight = sum(result.weight for result in results)
    if available_weight <= 0:
        raise ValueError("At least one comparison rule is required")

    score = sum(result.weighted_score for result in results) / available_weight
    evidence = tuple(
        Evidence(
            field=result.field,
            detail=result.detail,
            score=round(result.weighted_score / available_weight, 6),
        )
        for result in results
    )
    raw_scores = {result.field: result.raw_score for result in results}
    return round(min(score, 1.0), 6), evidence, raw_scores


def _is_viable(raw_scores: dict[str, float], settings: MatchSettings) -> bool:
    primary = raw_scores.get("primary", 0.0)
    if primary >= settings.minimum_primary_similarity:
        return True

    secondary = raw_scores.get("secondary")
    if secondary is None:
        return False

    support_match = raw_scores.get("amount") == 1.0 or raw_scores.get("date") == 1.0
    return (
        primary >= settings.rescue_primary_similarity
        and secondary >= settings.rescue_secondary_similarity
        and support_match
    )


def score_pair(
    row_a: dict[str, str],
    row_b: dict[str, str],
    mapping: FieldMapping,
    settings: MatchSettings,
) -> tuple[float, tuple[Evidence, ...]]:
    score, evidence, _ = _evaluate_pair(row_a, row_b, build_rules(mapping, settings))
    return score, evidence


def _score_candidates(
    table_a: CsvTable,
    table_b: CsvTable,
    mapping: FieldMapping,
    settings: MatchSettings,
    generation: CandidateGeneration,
) -> tuple[CandidateScore, ...]:
    rules = build_rules(mapping, settings)
    scored: list[CandidateScore] = []

    for a_index, b_indices in enumerate(generation.by_a):
        row_a = table_a.rows[a_index]
        for b_index in b_indices:
            score, evidence, raw_scores = _evaluate_pair(row_a, table_b.rows[b_index], rules)
            if not _is_viable(raw_scores, settings) or score < settings.review_threshold:
                continue
            scored.append(
                CandidateScore(
                    a_index=a_index,
                    b_index=b_index,
                    score=score,
                    evidence=evidence,
                )
            )

    return tuple(
        sorted(
            scored,
            key=lambda candidate: (-candidate.score, candidate.a_index, candidate.b_index),
        )
    )


def _is_ambiguous(
    candidate: CandidateScore,
    candidates: tuple[CandidateScore, ...],
    margin: float,
) -> bool:
    for alternative in candidates:
        if alternative is candidate:
            continue
        shares_a = alternative.a_index == candidate.a_index
        shares_b = alternative.b_index == candidate.b_index
        if (shares_a or shares_b) and candidate.score - alternative.score < margin:
            return True
    return False


def reconcile_with_diagnostics(
    table_a: CsvTable,
    table_b: CsvTable,
    mapping: FieldMapping,
    settings: MatchSettings,
) -> tuple[tuple[MatchDecision, ...], CandidateGeneration]:
    generation = generate_candidates(table_a, table_b, mapping, settings)
    candidates = _score_candidates(table_a, table_b, mapping, settings, generation)

    selected: list[MatchDecision] = []
    used_a: set[int] = set()
    used_b: set[int] = set()

    for candidate in candidates:
        if candidate.a_index in used_a or candidate.b_index in used_b:
            continue
        ambiguous = _is_ambiguous(candidate, candidates, settings.ambiguity_margin)
        status = (
            MatchStatus.AUTO_MATCHED
            if candidate.score >= settings.auto_threshold and not ambiguous
            else MatchStatus.REVIEW
        )
        used_a.add(candidate.a_index)
        used_b.add(candidate.b_index)
        selected.append(
            MatchDecision(
                a_index=candidate.a_index,
                b_index=candidate.b_index,
                score=candidate.score,
                status=status,
                evidence=candidate.evidence,
            )
        )

    for a_index in range(len(table_a.rows)):
        if a_index not in used_a:
            selected.append(
                MatchDecision(
                    a_index=a_index,
                    b_index=None,
                    score=0.0,
                    status=MatchStatus.UNMATCHED,
                    evidence=(),
                )
            )

    for b_index in range(len(table_b.rows)):
        if b_index not in used_b:
            selected.append(
                MatchDecision(
                    a_index=None,
                    b_index=b_index,
                    score=0.0,
                    status=MatchStatus.UNMATCHED,
                    evidence=(),
                )
            )

    decisions = tuple(
        sorted(
            selected,
            key=lambda decision: (
                decision.status == MatchStatus.UNMATCHED,
                decision.a_index if decision.a_index is not None else 10**9,
                decision.b_index if decision.b_index is not None else 10**9,
            ),
        )
    )
    return decisions, generation


def reconcile(
    table_a: CsvTable,
    table_b: CsvTable,
    mapping: FieldMapping,
    settings: MatchSettings,
) -> tuple[MatchDecision, ...]:
    decisions, _ = reconcile_with_diagnostics(table_a, table_b, mapping, settings)
    return decisions
