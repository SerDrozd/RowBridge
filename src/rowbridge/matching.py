from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from rapidfuzz.fuzz import WRatio

from rowbridge.models import (
    CandidateScore,
    CsvTable,
    Evidence,
    FieldMapping,
    MatchDecision,
    MatchSettings,
    MatchStatus,
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y")


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM.sub("", decomposed.casefold())


def parse_amount(value: str) -> Decimal | None:
    cleaned = value.strip().replace(" ", "")
    if not cleaned:
        return None
    if cleaned.count(",") == 1 and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def parse_date(value: str) -> date | None:
    stripped = value.strip()
    if not stripped:
        return None
    for format_string in _DATE_FORMATS:
        try:
            return datetime.strptime(stripped, format_string).date()
        except ValueError:
            continue
    return None


def score_pair(
    row_a: dict[str, str],
    row_b: dict[str, str],
    mapping: FieldMapping,
    settings: MatchSettings,
) -> tuple[float, tuple[Evidence, ...]]:
    components: list[tuple[str, str, float, float]] = []

    primary_a = row_a[mapping.primary_a]
    primary_b = row_b[mapping.primary_b]
    normalized_a = normalize_text(primary_a)
    normalized_b = normalize_text(primary_b)
    similarity = WRatio(normalized_a, normalized_b) / 100 if normalized_a and normalized_b else 0.0
    components.append(
        (
            "primary",
            f'"{primary_a}" vs "{primary_b}" similarity {similarity:.0%}',
            similarity * 0.70,
            0.70,
        )
    )

    if mapping.amount_a and mapping.amount_b:
        amount_a = parse_amount(row_a[mapping.amount_a])
        amount_b = parse_amount(row_b[mapping.amount_b])
        amount_score = 0.0
        if amount_a is not None and amount_b is not None:
            difference = abs(amount_a - amount_b)
            if difference <= Decimal(str(settings.amount_tolerance)):
                amount_score = 0.20
            amount_detail = f"difference {difference}; tolerance {settings.amount_tolerance:g}"
        else:
            amount_detail = "could not parse both amounts"
        components.append(("amount", amount_detail, amount_score, 0.20))

    if mapping.date_a and mapping.date_b:
        date_a = parse_date(row_a[mapping.date_a])
        date_b = parse_date(row_b[mapping.date_b])
        date_score = 0.0
        if date_a is not None and date_b is not None:
            day_difference = abs((date_a - date_b).days)
            if day_difference <= settings.date_window_days:
                date_score = 0.10
            date_detail = (
                f"difference {day_difference} day(s); window {settings.date_window_days}"
            )
        else:
            date_detail = "could not parse both dates"
        components.append(("date", date_detail, date_score, 0.10))

    available_weight = sum(weight for _, _, _, weight in components)
    raw_score = sum(score for _, _, score, _ in components)
    final_score = raw_score / available_weight
    evidence = tuple(
        Evidence(field=field, detail=detail, score=round(score / available_weight, 6))
        for field, detail, score, _ in components
    )
    return round(min(final_score, 1.0), 6), evidence


def reconcile(
    table_a: CsvTable,
    table_b: CsvTable,
    mapping: FieldMapping,
    settings: MatchSettings,
) -> tuple[MatchDecision, ...]:
    candidates_by_a: dict[int, list[CandidateScore]] = {}
    for a_index, row_a in enumerate(table_a.rows):
        candidates: list[CandidateScore] = []
        for b_index, row_b in enumerate(table_b.rows):
            score, evidence = score_pair(row_a, row_b, mapping, settings)
            if score >= settings.review_threshold:
                candidates.append(
                    CandidateScore(
                        a_index=a_index,
                        b_index=b_index,
                        score=score,
                        evidence=evidence,
                    )
                )
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.b_index))
        candidates_by_a[a_index] = candidates

    selected: list[MatchDecision] = []
    used_b: set[int] = set()
    ordered_a = sorted(
        range(len(table_a.rows)),
        key=lambda index: -(candidates_by_a[index][0].score if candidates_by_a[index] else -1.0),
    )

    for a_index in ordered_a:
        available = [
            candidate
            for candidate in candidates_by_a[a_index]
            if candidate.b_index not in used_b
        ]
        if not available:
            selected.append(
                MatchDecision(
                    a_index=a_index,
                    b_index=None,
                    score=0.0,
                    status=MatchStatus.UNMATCHED,
                    evidence=(),
                )
            )
            continue

        best = available[0]
        runner_up_score = available[1].score if len(available) > 1 else 0.0
        ambiguous = best.score - runner_up_score < settings.ambiguity_margin
        status = (
            MatchStatus.AUTO_MATCHED
            if best.score >= settings.auto_threshold and not ambiguous
            else MatchStatus.REVIEW
        )
        used_b.add(best.b_index)
        selected.append(
            MatchDecision(
                a_index=a_index,
                b_index=best.b_index,
                score=best.score,
                status=status,
                evidence=best.evidence,
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

    return tuple(
        sorted(
            selected,
            key=lambda decision: (
                decision.status == MatchStatus.UNMATCHED,
                decision.a_index if decision.a_index is not None else 10**9,
                decision.b_index if decision.b_index is not None else 10**9,
            ),
        )
    )
