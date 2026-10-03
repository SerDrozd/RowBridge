from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

type RowPayload = dict[str, str]


class MatchStatus(StrEnum):
    AUTO_MATCHED = "auto_matched"
    REVIEW = "review"
    UNMATCHED = "unmatched"


@dataclass(frozen=True, slots=True)
class CsvTable:
    filename: str
    headers: tuple[str, ...]
    rows: tuple[RowPayload, ...]


@dataclass(frozen=True, slots=True)
class FieldMapping:
    primary_a: str
    primary_b: str
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
class MatchDecision:
    a_index: int | None
    b_index: int | None
    score: float
    status: MatchStatus
    evidence: tuple[Evidence, ...]
