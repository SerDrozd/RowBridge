from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

type RowPayload = dict[str, str]


class MatchStatus(StrEnum):
    AUTO_MATCHED = "auto_matched"
    REVIEW = "review"
    CONFIRMED = "confirmed"
    MANUAL_MATCHED = "manual_matched"
    UNMATCHED = "unmatched"


class ReviewAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    MANUAL_LINK = "manual_link"


class RuleKind(StrEnum):
    FUZZY_TEXT = "fuzzy_text"
    NUMERIC_TOLERANCE = "numeric_tolerance"
    DATE_WINDOW = "date_window"


@dataclass(frozen=True, slots=True)
class CsvTable:
    filename: str
    headers: tuple[str, ...]
    rows: tuple[RowPayload, ...]


@dataclass(frozen=True, slots=True)
class FieldMapping:
    primary_a: str
    primary_b: str
    secondary_a: str | None = None
    secondary_b: str | None = None
    amount_a: str | None = None
    amount_b: str | None = None
    date_a: str | None = None
    date_b: str | None = None


@dataclass(frozen=True, slots=True)
class MatchSettings:
    amount_tolerance: float = 0.05
    date_window_days: int = 2
    review_threshold: float = 0.72
    auto_threshold: float = 0.92
    ambiguity_margin: float = 0.08
    minimum_primary_similarity: float = 0.68
    rescue_primary_similarity: float = 0.50
    rescue_secondary_similarity: float = 0.88
    max_candidates_per_row: int = 30
    fallback_candidates: int = 5


@dataclass(frozen=True, slots=True)
class ComparisonRule:
    field: str
    column_a: str
    column_b: str
    kind: RuleKind
    weight: float
    tolerance: float | None = None
    window_days: int | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    field: str
    detail: str
    score: float


@dataclass(frozen=True, slots=True)
class CandidateScore:
    a_index: int
    b_index: int
    score: float
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class CandidateGeneration:
    by_a: tuple[tuple[int, ...], ...]
    possible_pairs: int
    generated_pairs: int
    fallback_rows: int


@dataclass(frozen=True, slots=True)
class MatchDecision:
    a_index: int | None
    b_index: int | None
    score: float
    status: MatchStatus
    evidence: tuple[Evidence, ...]
